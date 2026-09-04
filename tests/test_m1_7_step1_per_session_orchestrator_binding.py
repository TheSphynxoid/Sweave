"""M1.7 step 1 tests: per-Session orchestrator binding (the §2.1 wrinkle).

Covers:

* ``Session`` gains ``schema_version`` (default 1) and
  ``orchestrator_session_id`` (default None).
* Legacy session files (no ``schema_version`` / no
  ``orchestrator_session_id``) load with the dataclass defaults — the
  migration is a no-op for pre-M1.7 files.
* Two Sessions in the same project get independent
  ``orchestrator_session_id`` values — three Sweave sessions no longer
  share one orchestrator conversation.
* ``Session.to_dict`` / ``Session.from_dict`` roundtrip preserves the
  binding.
* ``SpecialistRuntime`` accepts ``session_id_getter`` /
  ``session_id_setter`` callbacks; the orchestrator path uses them
  (the binding lives on the Session record, not the Specialist
  record). The default (no callbacks) keeps the M1.3 behaviour
  (``specialist.session_id`` is read/written) — backwards compatible.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sweave.projects import ProjectManager, Session
from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist


# ---------------------------------------------------------------------------
# Hermeticity: gate the opencode subprocess seam the same way M1.3 step 3
# did. The runtime path triggers ``ServeRunner.start()``; under
# ``SWEAVE_MOCK_OPENCODE=1`` the runner is a sentinel and
# ``_build_process`` returns a stub ``OpenCodeProcess`` whose ``_client``
# answers ``POST /session`` / ``GET /session/{id}`` with canned responses.
# Module-scoped autouse — same reasoning as the M1.3 + M1.4/5 tests.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    old = os.environ.get("SWEAVE_MOCK_OPENCODE")
    os.environ["SWEAVE_MOCK_OPENCODE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SWEAVE_MOCK_OPENCODE", None)
        else:
            os.environ["SWEAVE_MOCK_OPENCODE"] = old


# ---------------------------------------------------------------------------
# Session dataclass tests
# ---------------------------------------------------------------------------


def test_session_schema_version_default_is_1():
    s = Session(id="s1", project_name="p", name="n")
    assert s.schema_version == 1
    assert s.orchestrator_session_id is None


def test_session_to_dict_roundtrip_preserves_orchestrator_session_id():
    s = Session(
        id="s1",
        project_name="p",
        name="n",
        schema_version=1,
        orchestrator_session_id="orch-sess-abc",
    )
    d = s.to_dict()
    assert d["schema_version"] == 1
    assert d["orchestrator_session_id"] == "orch-sess-abc"
    # roundtrip
    s2 = Session.from_dict(d)
    assert s2.schema_version == 1
    assert s2.orchestrator_session_id == "orch-sess-abc"


def test_session_legacy_file_migrates_with_defaults():
    """Pre-M1.7 session files lack schema_version / orchestrator_session_id.

    The dataclass defaults apply — no KeyError, no 500, the session
    loads with schema_version=1 / orchestrator_session_id=None.
    """
    legacy = {
        "id": "s1",
        "project_name": "p",
        "name": "n",
        "created_at": "2026-09-01T00:00:00",
        "updated_at": "2026-09-01T00:00:00",
        "status": "active",
        "current_agent": None,
        "context": {},
        "messages": [],
        "children": [],
        "memory_bank": "session-s1",
        # NOTE: no schema_version, no orchestrator_session_id
    }
    s = Session.from_dict(legacy)
    assert s.schema_version == 1
    assert s.orchestrator_session_id is None
    # roundtrip writes the new fields back, so a save_session after
    # load migrates the file to v1 on disk.
    d = s.to_dict()
    assert d["schema_version"] == 1
    assert d["orchestrator_session_id"] is None


def test_two_sessions_get_independent_orchestrator_session_ids(tmp_path: Path):
    """The whole point of the M1.7 step-1 fix.

    Two Sessions in the same project must not share an
    ``orchestrator_session_id``. (M1.3 stored it on the Specialist
    record, which is per project — three Sweave sessions shared one
    orchestrator conversation. The fix moves the binding to the
    Session record.)
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    s1 = pm.create_session("demo", session_name="alpha")
    s2 = pm.create_session("demo", session_name="beta")
    # Set the orchestrator binding independently
    s1.orchestrator_session_id = "opencode-sess-AAA"
    s2.orchestrator_session_id = "opencode-sess-BBB"
    pm.save_session(s1)
    pm.save_session(s2)
    # Reload from disk — proves persistence, not just in-memory
    loaded_s1 = pm.get_session(s1.id)
    loaded_s2 = pm.get_session(s2.id)
    assert loaded_s1.orchestrator_session_id == "opencode-sess-AAA"
    assert loaded_s2.orchestrator_session_id == "opencode-sess-BBB"
    assert loaded_s1.orchestrator_session_id != loaded_s2.orchestrator_session_id


# ---------------------------------------------------------------------------
# SpecialistRuntime callback tests
# ---------------------------------------------------------------------------


def _orchestrator_specialist() -> Specialist:
    """The orchestrator singleton shape (M1.6 + M1.7 step 1).

    ``is_orchestrator=True``; the runtime's behaviour change is gated
    on the callbacks, not on the flag, so we don't need a real
    orchestrator to test the seam — any Specialist works.
    """
    return Specialist(
        name="orchestrator",
        scope="project",
        is_orchestrator=True,
        system_prompt="seed",
        harness="opencode",
        current_model=None,
    )


def _make_runtime_with_mock_send() -> tuple[SpecialistRuntime, list[dict[str, Any]]]:
    """Build a runtime whose ``_send_message`` is mocked, so we can
    observe the orchestrator binding seam without a real LLM call.
    Same shape as the M1.3 step 3 helper.
    """
    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    sent_calls: list[dict[str, Any]] = []

    async def fake_send(self, body, trace, on_chunk=None):
        sent_calls.append({"body_keys": list(body.keys())})
        return "ok"

    runtime._send_message = fake_send  # type: ignore[assignment]
    return runtime, sent_calls


@pytest.mark.asyncio
async def test_specialist_runtime_default_uses_specialist_session_id(tmp_path: Path):
    """Backwards-compat invariant: no callbacks = specialist.session_id is
    read/written (M1.3 behaviour). Pinned by the test so M1.7 step 1
    can't silently regress existing specialists.
    """
    from sweave.runtime.delegation_store import Delegation

    runtime, _sent = _make_runtime_with_mock_send()
    spec = _orchestrator_specialist()
    d = Delegation(
        delegation_id="d1",
        agent="orchestrator",
        task="hi",
        project_name="p",
    )
    # Pre-set the binding on the specialist (M1.3 style)
    spec.session_id = "legacy-stored-id"
    out = await runtime.run(
        specialist=spec,
        delegation=d,
        worktree_path=tmp_path,
        message="hi",
        trace=_FakeTrace(),
        # no session_id_getter / session_id_setter
    )
    assert out == "ok"
    # The M1.3 path persisted on the specialist (the default path).
    # Because the stub client under SWEAVE_MOCK_OPENCODE=1 returns a
    # canned 200 for GET /session/{id}, the runtime hits the *reuse*
    # branch and does NOT overwrite the existing id. That matches
    # real production: the binding is on the specialist, not the
    # session. We assert the binding wasn't silently moved.
    assert spec.session_id == "legacy-stored-id"


@pytest.mark.asyncio
async def test_specialist_runtime_uses_callback_when_orchestrator(tmp_path: Path):
    """The M1.7 step-1 seam: when callbacks are passed, the binding
    is read/written through them, NOT through
    ``specialist.session_id``. The orchestrator path uses Session
    storage; the specialist record is untouched.
    """
    from sweave.runtime.delegation_store import Delegation

    runtime, _sent = _make_runtime_with_mock_send()
    spec = _orchestrator_specialist()
    d = Delegation(
        delegation_id="d2",
        agent="orchestrator",
        task="hi",
        project_name="p",
    )
    # Simulate the Session-bound binding: a dict that the Session
    # record would hold. The callbacks close over the dict so the
    # runtime's read/write is observable.
    bound: dict[str, str | None] = {"id": None}
    getter = lambda: bound["id"]  # noqa: E731
    setter = lambda new_id: bound.__setitem__("id", new_id)  # noqa: E731

    await runtime.run(
        specialist=spec,
        delegation=d,
        worktree_path=tmp_path,
        message="hi",
        trace=_FakeTrace(),
        session_id_getter=getter,
        session_id_setter=setter,
    )
    # The callback was written to (the runtime created a session under
    # SWEAVE_MOCK_OPENCODE=1 and persisted the canned id through the
    # setter). The specialist record is untouched.
    assert bound["id"] is not None
    assert spec.session_id is None or spec.session_id == ""
    # The id persisted via callback != specialist.session_id
    assert bound["id"] != (spec.session_id or None)


# ---------------------------------------------------------------------------
# Helper: a TraceLog that swallows appends. The runtime writes
# worktree_set / model_used / etc; the tests don't assert on them.
# ---------------------------------------------------------------------------


class _FakeTrace:
    def append(self, *args: Any, **kwargs: Any) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        # Anything else the runtime touches becomes a no-op.
        return lambda *a, **kw: None
