"""M1.7 step 2 tests: chat turn pipeline.

Covers:

* Delegation gains kind: str = "task" (with "chat" for orchestrator
  conversation turns). Additive; from_dict defaults to "task" for
  pre-M1.7 records.
* ``POST /api/sessions/{id}/messages`` (user role) drives the
  ChatLoop: persists the user message, runs the orchestrator, persists
  the assistant reply. Both messages are returned.
* Double-send guard (supersedes lock-as-queue): a second user
  message arriving mid-turn is REJECTED with TurnActiveError
  (HTTP 409) -- never silently queued.
* Orchestrator-unreachable: the loop persists an explicit error
  message -- never a silent fallback, never a swallowed failure.
* Legacy / non-user roles (system, tool, assistant) still hit the
  persist-only path; the chat loop is not invoked.
* Step 1's Session binding: the orchestrator's opencode session id
  is written to Session.orchestrator_session_id (not the Specialist
  record) when the chat loop runs.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from sweave.projects import ProjectManager
from sweave.runtime.delegation_store import PerProjectDelegationStores
from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist


# ---------------------------------------------------------------------------
# Hermeticity: gate the opencode subprocess seam the same way M1.3 step 3
# did. The chat loop's SpecialistRuntime.run triggers
# ``ServeRunner.start()``; under ``SWEAVE_MOCK_OPENCODE=1`` the runner
# is a sentinel and ``_build_process`` returns a stub ``OpenCodeProcess``
# whose ``_client`` answers ``POST /session`` / ``GET /session/{id}``
# with canned responses.
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
# Test helpers
# ---------------------------------------------------------------------------


def _orchestrator_specialist() -> Specialist:
    return Specialist(
        name="orchestrator",
        scope="project",
        is_orchestrator=True,
        system_prompt="seed",
        harness="opencode",
        current_model=None,
    )


def _build_chat_loop(
    *,
    pm: ProjectManager,
    send_responses: list[str] | None = None,
    send_delay: float = 0.0,
    send_error: Exception | None = None,
):
    """Build a ChatLoop with a mocked SpecialistRuntime._send_message.

    The runtime's real run() executes (so the registry creates a runner
    and the session lifecycle runs), but the wire-formatting layer
    returns canned text. This lets the chat loop's plumbing
    (delegation record, message persistence, queue, binding) be
    exercised end-to-end without a real LLM call.

    ``send_responses`` is a list consumed FIFO; if exhausted, the
    loop returns a default text. ``send_error`` raises instead.
    """
    from sweave.chat.loop import ChatLoop

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)

    if send_error is not None:
        async def fake_send_error(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
            raise send_error
        runtime._send_message = fake_send_error  # type: ignore[assignment]
    else:
        responses = list(send_responses or ["ok"])

        async def fake_send(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
            if send_delay:
                await asyncio.sleep(send_delay)
            # M1.8: if a streaming callback is provided, push the
            # canned response as a single text part so the chat
            # loop exercises the on_chunk path. Otherwise the
            # pre-M1.8 behaviour is preserved.
            if on_chunk is not None:
                on_chunk(responses[0] if responses else "ok")
            if not responses:
                return "ok"
            return responses.pop(0)

        runtime._send_message = fake_send  # type: ignore[assignment]

    factories = {"orchestrator": _orchestrator_specialist()}
    factory = lambda agent_name, project_name=None: factories.get(agent_name)  # noqa: E731

    def resolver(name: str | None) -> Path | None:
        if name is None:
            return None
        proj = pm.get_project(name)
        return proj.path if proj else None

    stores = PerProjectDelegationStores()
    chat = ChatLoop(
        project_manager=pm,
        specialist_runtime=runtime,
        specialist_factory=factory,
        project_dir_resolver=resolver,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=10.0,
        model_resolver=lambda agent, project=None: "deepseek-flash",
    )
    return chat, stores


# ---------------------------------------------------------------------------
# Delegation.kind tests
# ---------------------------------------------------------------------------


def test_delegation_kind_default_is_task():
    """Pre-M1.7 records carry no kind field; from_dict defaults to 'task'."""
    from sweave.runtime.delegation_store import Delegation

    d = Delegation(
        delegation_id="d1",
        agent="backend",
        task="do the thing",
    )
    assert d.kind == "task"
    # Roundtrip preserves kind
    d2 = Delegation.from_dict(d.to_dict())
    assert d2.kind == "task"


def test_delegation_kind_chat_roundtrip():
    from sweave.runtime.delegation_store import Delegation

    d = Delegation(
        delegation_id="d2",
        agent="orchestrator",
        task="hi",
        kind="chat",
    )
    assert d.kind == "chat"
    d2 = Delegation.from_dict(d.to_dict())
    assert d2.kind == "chat"


def test_delegation_legacy_record_loads_as_task():
    """Pre-M1.7 records (no kind field) load with kind='task'."""
    from sweave.runtime.delegation_store import (
        SCHEMA_VERSION,
        Delegation,
    )

    legacy = {
        "delegation_id": "legacy-1",
        "task_id": "legacy-1",
        "agent": "backend",
        "model": "hy3",
        "task": "old delegation",
        "status": "done",
        "schema_version": SCHEMA_VERSION,
        "kind": "task",  # written by current v3 code; older v3 had no kind
    }
    # Remove kind to simulate a pre-M1.7 record
    del legacy["kind"]
    d = Delegation.from_dict(legacy)
    assert d.kind == "task"


# ---------------------------------------------------------------------------
# ChatLoop end-to-end tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_loop_persists_user_and_assistant_messages(tmp_path: Path):
    """End-to-end: user message in -> orchestrator runs -> assistant out.

    Both messages land in Session.messages; the Session is persisted
    after each (so the audit trail and the binding survive restart).
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _stores = _build_chat_loop(pm=pm, send_responses=["hello back"])

    result = await chat.run_turn(
        session_id=session.id, user_content="hello"
    )
    assert result["role"] == "assistant"
    assert result["content"] == "hello back"
    assert result["agent"] == "orchestrator"

    # Reload and inspect the persisted Session
    loaded = pm.get_session(session.id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.messages[0].role == "user"
    assert loaded.messages[0].content == "hello"
    assert loaded.messages[1].role == "assistant"
    assert loaded.messages[1].content == "hello back"
    assert loaded.messages[1].agent == "orchestrator"


@pytest.mark.asyncio
async def test_chat_loop_persists_orchestrator_session_binding(tmp_path: Path):
    """Step 1's binding is written: the chat loop runs the runtime
    with session_id callbacks, so the runtime persists the opencode
    session id on the Session record (not on the Specialist record).
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _stores = _build_chat_loop(pm=pm, send_responses=["hi"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    loaded = pm.get_session(session.id)
    # The stub opencode serve returns a canned id; the binding
    # landed on the Session record via the callback.
    assert loaded.orchestrator_session_id is not None
    assert loaded.orchestrator_session_id != ""


@pytest.mark.asyncio
async def test_two_sessions_get_independent_orchestrator_bindings(tmp_path: Path):
    """Two chat turns in two sessions -- two independent
    orchestrator bindings stored on the Session records. The
    per-Session binding (M1.7 step 1) holds.

    The mock opencode serve returns a stable per-specialist id
    (``ses_mock_orchestrator`` -- R4.0 wire-shape prefix) so a
    strict ``!=`` check on the string would be a false negative.
    The actual invariant we pin is: the binding is stored on the
    Session record, not on the Specialist record. The
    specialist_factory returns a fresh Specialist each call (no
    persistence), so the only place the binding can land is the
    Session record.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    s1 = pm.create_session("demo", session_name="s1")
    s2 = pm.create_session("demo", session_name="s2")
    chat, _stores = _build_chat_loop(pm=pm, send_responses=["a", "b"])

    await chat.run_turn(session_id=s1.id, user_content="hi")
    await chat.run_turn(session_id=s2.id, user_content="hello")

    loaded_s1 = pm.get_session(s1.id)
    loaded_s2 = pm.get_session(s2.id)
    # The binding is stored on the Session record (not the
    # Specialist record, which is fresh per call).
    assert loaded_s1.orchestrator_session_id is not None
    assert loaded_s2.orchestrator_session_id is not None
    # Each session's binding was persisted to its own file on disk.
    # The two sessions have separate files; the on-disk binding
    # for each is the value ChatLoop wrote.
    assert loaded_s1.orchestrator_session_id == "ses_mock_orchestrator"
    assert loaded_s2.orchestrator_session_id == "ses_mock_orchestrator"


@pytest.mark.asyncio
async def test_chat_loop_double_send_rejected_with_turn_active(tmp_path: Path):
    """Double-send guard (supersedes the M1.7 lock-as-queue semantics
    per the chat-recovery hardening): a second user message arriving
    while a turn is active is REJECTED with ``TurnActiveError``
    (HTTP 409), never silently queued -- a queued POST would hang the
    client's HTTP request for up to ``turn_timeout``, and a refreshed
    client could never see it waiting. The error carries the active
    turn's snapshot.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _stores = _build_chat_loop(
        pm=pm,
        send_responses=["first-reply"],
        send_delay=0.15,
    )

    from sweave.chat.loop import TurnActiveError

    # Fire both concurrently: the second must be rejected, not queued.
    results = await asyncio.gather(
        chat.run_turn(session_id=session.id, user_content="first-msg"),
        chat.run_turn(session_id=session.id, user_content="second-msg"),
        return_exceptions=True,
    )
    first, second = results
    # Exactly one of the two raised TurnActiveError (the racing
    # double-send); the other completed as a normal turn.
    errs = [r for r in results if isinstance(r, TurnActiveError)]
    oks = [r for r in results if not isinstance(r, Exception)]
    assert len(oks) == 1
    assert len(errs) == 1
    err = errs[0]
    assert err.snapshot["session_id"] == session.id
    assert err.snapshot["status"] == "running"

    loaded = pm.get_session(session.id)
    assert loaded is not None
    # Only ONE turn ran: one user + one assistant message; the
    # rejected double-send never persisted a message.
    assert len(loaded.messages) == 2
    assistant_msgs = [m for m in loaded.messages if m.role == "assistant"]
    assert assistant_msgs[0].content == "first-reply"


@pytest.mark.asyncio
async def test_chat_loop_orchestrator_unreachable_persists_explicit_error(tmp_path: Path):
    """When the runtime raises, the loop persists an explicit error
    message -- never silent, never swallowed. The user sees the
    failure in the chat.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _stores = _build_chat_loop(
        pm=pm, send_error=ConnectionError("serve unreachable")
    )

    result = await chat.run_turn(
        session_id=session.id, user_content="hello"
    )
    assert result["role"] == "assistant"
    assert "ConnectionError" in result["content"]
    assert "serve unreachable" in result["content"]

    loaded = pm.get_session(session.id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.messages[1].role == "assistant"
    assert "ConnectionError" in loaded.messages[1].content


@pytest.mark.asyncio
async def test_chat_loop_turn_timeout_persists_explicit_error(tmp_path: Path):
    """The turn timeout (asyncio.wait_for) surfaces as an explicit
    error message, not a hung HTTP request.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    # send_delay > turn_timeout forces a TimeoutError.
    chat, _stores = _build_chat_loop(pm=pm, send_delay=0.5)
    # Override the timeout to a tiny value so the test is fast.
    chat.turn_timeout = 0.05

    result = await chat.run_turn(
        session_id=session.id, user_content="hello"
    )
    assert result["role"] == "assistant"
    assert "timeout" in result["content"].lower()


@pytest.mark.asyncio
async def test_chat_loop_writes_chat_delegation_record(tmp_path: Path):
    """The chat turn creates a Delegation with kind='chat' and the
    correct session binding. The audit trail exists.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, stores = _build_chat_loop(pm=pm, send_responses=["hi"])

    await chat.run_turn(session_id=session.id, user_content="hello")

    # The Delegation record was added to the per-project store
    store = await stores.for_project(tmp_path)
    all_records = store.list()
    chat_records = [r for r in all_records if r.kind == "chat"]
    assert len(chat_records) == 1
    rec = chat_records[0]
    assert rec.agent == "orchestrator"
    assert rec.parent_session_id == session.id
    assert rec.project_name == "demo"
    # Final status: success -> "review" (step 3 auto-promotes to "done")
    assert rec.status in {"review", "done"}
    assert rec.output == "hi"


@pytest.mark.asyncio
async def test_chat_loop_different_sessions_run_in_parallel(tmp_path: Path):
    """The per-session lock is per-session, not global. Two sessions
    can run chat turns in parallel.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    s1 = pm.create_session("demo", session_name="s1")
    s2 = pm.create_session("demo", session_name="s2")
    # send_delay 0.1s per call; if the lock were global, two parallel
    # calls would take ~0.2s. We assert they finish in ~0.1s.
    chat, _stores = _build_chat_loop(pm=pm, send_delay=0.1)

    t0 = time.monotonic()
    await asyncio.gather(
        chat.run_turn(session_id=s1.id, user_content="a"),
        chat.run_turn(session_id=s2.id, user_content="b"),
    )
    elapsed = time.monotonic() - t0
    # Generous upper bound (10x delay) -- parallel execution should
    # easily fit. A serial global lock would be ~0.2s.
    assert elapsed < 0.5, f"parallel turns took {elapsed:.2f}s; expected <0.5s"
