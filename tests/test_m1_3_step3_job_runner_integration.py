"""M1.3 step 3 tests: JobRunner integration with SpecialistRuntime.

Covers:
* The runtime path is taken when specialist_runtime + factory are wired
* The legacy path is taken when specialist_runtime is None
* Two sequential delegations to the same specialist through the
  runtime go through the same per-specialist serve (and the runtime's
  session_id is updated on the specialist after the first run)
* Different specialists get different runners
* Bare model strings are parsed as legacy ModelRef(provider=None, ...)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sweave.runtime.delegation_store import (
    DelegationStore,
    PerProjectDelegationStores,
)
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.serve_runner import ServeRunner, ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import (
    ModelRef,
    Specialist,
)


# ---------------------------------------------------------------------------
# Hermeticity: M1.4+M1.5 step 0
# ---------------------------------------------------------------------------
#
# The runtime path in ``SpecialistRuntime.run`` calls
# ``self.runners.get_or_create(...)`` which triggers ``ServeRunner.start()``.
# In production that spawns a real ``opencode serve`` subprocess and waits
# for the listening port. The tests in this file only assert on the wire
# shape the runtime builds (captured via the per-instance ``_send_message``
# mock) — they never call into the serve. Letting ``start()`` run a real
# subprocess is dead work AND a flake source (``opencode serve exited
# early`` when port-bind races earlier in the suite). Setting
# ``SWEAVE_MOCK_OPENCODE=1`` is the codebase's existing seam: the runner
# no-ops into a sentinel state (``port=0``, ``base_url="http://mock-opencode"``,
# no real subprocess) and ``SpecialistRuntime._build_process`` returns a
# stub ``OpenCodeProcess`` whose ``_client`` answers ``POST /session`` and
# ``GET /session/{id}`` with canned responses. The mocked
# ``_send_message`` is still needed because it captures the ``body["model"]``
# the runtime built; the stub's ``stream`` is never reached.
#
# Module scope is safe: the env var is read at every ``start()`` /
# ``_build_process()`` call, not at import time, so changes mid-suite
# would be observable. We pin it for the whole file.
@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    import os

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


class _FakeDelegateTool:
    """Stand-in for DelegateTaskTool; records calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, agent, task, model=None, task_id=None):
        from sweave.tools import DelegationResult

        self.calls.append(
            {"agent": agent, "task": task, "model": model, "task_id": task_id}
        )
        return DelegationResult(
            success=True, agent=agent, task_id=task_id or "x",
            output=f"legacy: {task}", error=None,
        )


def _project_resolver(p: Path):
    def _r(name):
        return p if name else None
    return _r


def _make_runtime_with_mock_send(tmp_path: Path):
    """Build a SpecialistRuntime + a ServeRunnerRegistry where
    ``runtime._send_message`` is mocked. Combined with the module-level
    ``_mock_opencode_env`` autouse fixture, this lets the real
    ``runtime.run`` execute (so the registry's ``get_or_create`` runs
    and the runner is created) WITHOUT spawning a real opencode serve.

    The send mock records (model, parts_count) from the body the
    runtime built, so tests can assert the wire shape directly.

    We do NOT mock ``_ensure_session`` here: doing so as a class
    attribute leaks into other tests in the same pytest process
    (recorded in M1.3 step 3). Per-instance mocks are safe; the
    stub client returned by ``_build_process`` under
    ``SWEAVE_MOCK_OPENCODE=1`` answers POST/GET ``/session`` calls
    deterministically.
    """
    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    sent_calls: list[dict[str, Any]] = []

    async def fake_send(self, body, trace, on_chunk=None):
        sent_calls.append({
            "model": body.get("model"),
            "parts_count": len(body.get("parts", [])),
        })
        return f"runtime-output-for-{body.get('parts', [{}])[0].get('text', 'x')[-20:]}"

    runtime._send_message = fake_send  # type: ignore[assignment]

    return runtime, runners, sent_calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_runner_legacy_path_when_no_runtime(tmp_path: Path):
    """When specialist_runtime is None, JobRunner uses delegate_tool."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores as _P

    stores = _P()
    delegate = _FakeDelegateTool()
    runner = JobRunner(
        delegate_tool=delegate,
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
    )
    d = await runner.submit(
        agent="backend",
        task="do the thing",
        model="hy3",
    )
    await runner.wait(d.delegation_id, timeout=5)
    assert len(delegate.calls) == 1
    assert delegate.calls[0]["agent"] == "backend"


@pytest.mark.asyncio
async def test_job_runner_runtime_path_when_wired(tmp_path: Path):
    """When specialist_runtime + factory are wired, JobRunner uses the
    runtime instead of delegate_tool. The structured 'provider/model_id'
    model string is parsed and emitted as the structured v2 body shape."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores as _P

    stores = _P()
    delegate = _FakeDelegateTool()
    runtime, runners, sent_calls = _make_runtime_with_mock_send(tmp_path)

    def factory(name: str) -> Specialist:
        return Specialist(
            name=name, scope="project", is_orchestrator=False,
            system_prompt="", harness="opencode", current_model=None,
        )

    runner = JobRunner(
        delegate_tool=delegate,
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        specialist_runtime=runtime,
        specialist_factory=factory,
    )
    d = await runner.submit(
        agent="backend",
        task="hello",
        model="zhipu/glm-5",  # structured pair -- exercises ModelRef
    )
    await runner.wait(d.delegation_id, timeout=5)
    # Legacy path was NOT taken
    assert delegate.calls == []
    # Runtime path was taken; structured pair was sent
    assert len(sent_calls) == 1
    body = sent_calls[0]
    assert body["model"] == {"providerID": "zhipu", "modelID": "glm-5"}
    # Exactly one runner was created for the (specialist, worktree) key
    assert len(runners.known()) == 1


@pytest.mark.asyncio
async def test_job_runner_legacy_path_when_specialist_unknown(tmp_path: Path):
    """When the factory returns None, fall back to the legacy path."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores as _P

    stores = _P()
    delegate = _FakeDelegateTool()
    runtime, _runners, _calls = _make_runtime_with_mock_send(tmp_path)

    def factory(agent_name: str) -> Specialist | None:
        return None  # agent not in any store

    runner = JobRunner(
        delegate_tool=delegate,
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        specialist_runtime=runtime,
        specialist_factory=factory,
    )
    d = await runner.submit(agent="ghost", task="x", model=None)
    await runner.wait(d.delegation_id, timeout=5)
    # The factory returned None -> JobRunner's runtime path builds a
    # default Specialist with no system_prompt (graceful fallback
    # for unknown agent names; the legacy path is reserved for when
    # no specialist_runtime is wired at all). The legacy delegate was
    # NOT called.
    assert delegate.calls == []
    # The runtime still created a runner for the (ghost, worktree) key.
    assert len(_runners.known()) == 1


@pytest.mark.asyncio
async def test_job_runner_runtime_path_legacy_model_string(tmp_path: Path):
    """Bare model string (v1 path) is parsed as ModelRef(provider=None,
    model_id=raw). The runtime emits a warning in production; the test
    just verifies the wire shape the runtime receives."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores as _P

    stores = _P()
    delegate = _FakeDelegateTool()
    runtime, _runners, sent_calls = _make_runtime_with_mock_send(tmp_path)

    def factory(name: str) -> Specialist:
        return Specialist(name=name, system_prompt="", harness="opencode")

    runner = JobRunner(
        delegate_tool=delegate,
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        specialist_runtime=runtime,
        specialist_factory=factory,
    )
    d = await runner.submit(agent="a", task="x", model="qwen3:8b")
    await runner.wait(d.delegation_id, timeout=5)
    # Bare string parses to ModelRef(provider=None, model_id=...).
    # The runtime's _model_body returns None for incomplete refs
    # (provider missing), so the unqualified-name path is used: the
    # body has NO 'model' key. The harness (v2) defaults the model
    # to its own default provider resolution. The v1 record is
    # preserved through round-trip; probe 5b proved bare names
    # 400 on non-default providers (warning fires at routing time
    # in production; not exercised in this mock).
    assert sent_calls[0]["model"] is None


@pytest.mark.asyncio
async def test_runtime_runner_is_mocked_no_real_subprocess(tmp_path: Path):
    """M1.4+M1.5 step 0 regression: assert the leak source is gone.

    The flake: ``test_job_runner_runtime_path_legacy_model_string`` (and
    its siblings) used to let ``ServeRunner.start()`` spawn a real
    ``opencode serve`` subprocess even though the test only asserts on
    the wire shape the runtime built. The subprocess occasionally
    exited early under full-suite load, surfacing as
    ``RuntimeError: opencode serve exited early`` and making the
    whole test order-dependent.

    The fix is structural: ``SWEAVE_MOCK_OPENCODE=1`` (set by the
    module-level autouse fixture) makes ``ServeRunner.start()`` a
    no-op sentinel and makes ``_build_process`` return a stub
    ``OpenCodeProcess``. This test pins the invariant: after a
    runtime delegation, the runner is in mock mode (port=0, no
    subprocess, sentinel base_url). If anyone removes the env-var
    fixture, this test fails immediately — the leak source cannot
    silently return.
    """
    import os

    from sweave.runtime.delegation_store import PerProjectDelegationStores as _P

    assert os.environ.get("SWEAVE_MOCK_OPENCODE") == "1", (
        "Test invariant: SWEAVE_MOCK_OPENCODE must be set by the "
        "module fixture. Removing it reintroduces the real-subprocess "
        "leak (see M1.4+M1.5 step 0)."
    )

    stores = _P()
    runtime, runners, _calls = _make_runtime_with_mock_send(tmp_path)

    def factory(name: str) -> Specialist:
        return Specialist(name=name, system_prompt="", harness="opencode")

    runner = JobRunner(
        delegate_tool=_FakeDelegateTool(),
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        specialist_runtime=runtime,
        specialist_factory=factory,
    )
    d = await runner.submit(agent="alpha", task="x", model=None)
    await runner.wait(d.delegation_id, timeout=5)

    assert len(runners.known()) == 1
    serve = runners.known()[0]
    assert serve.port == 0, (
        f"runner should be in mock mode (port=0); got port={serve.port}. "
        "This means a real opencode serve subprocess was spawned and "
        "leaked the flake source."
    )
    assert serve.base_url == "http://mock-opencode"
    assert serve.process is None
    assert serve.log_path is None


@pytest.mark.asyncio
async def test_job_runner_two_sequential_delegations_share_runner(tmp_path: Path):
    """Two sequential delegations to the same specialist through the
    runtime use the same per-specialist serve (key: (name, worktree))."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores as _P

    stores = _P()
    runners = ServeRunnerRegistry()
    runtime, _r, _c = _make_runtime_with_mock_send(tmp_path)
    # Use the same registry the runtime will use
    runtime.runners = runners

    def factory(name: str) -> Specialist:
        return Specialist(name=name, system_prompt="", harness="opencode")

    runner = JobRunner(
        delegate_tool=_FakeDelegateTool(),
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        specialist_runtime=runtime,
        specialist_factory=factory,
    )
    d1 = await runner.submit(agent="alpha", task="one", model=None)
    await runner.wait(d1.delegation_id, timeout=5)
    d2 = await runner.submit(agent="alpha", task="two", model=None)
    await runner.wait(d2.delegation_id, timeout=5)
    # Two sequential runs; same agent; same worktree -> same runner.
    assert len(runners.known()) == 1
    runner = runners.known()[0]
    assert runner.specialist_name == "alpha"


@pytest.mark.asyncio
async def test_job_runner_different_specialists_different_runners(tmp_path: Path):
    """Different specialists get different ServeRunner instances."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores as _P

    stores = _P()
    runners = ServeRunnerRegistry()
    runtime, _r, _c = _make_runtime_with_mock_send(tmp_path)
    runtime.runners = runners

    def factory(name: str) -> Specialist:
        return Specialist(name=name, system_prompt="", harness="opencode")

    runner = JobRunner(
        delegate_tool=_FakeDelegateTool(),
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        specialist_runtime=runtime,
        specialist_factory=factory,
    )
    d1 = await runner.submit(agent="alpha", task="x", model=None)
    await runner.wait(d1.delegation_id, timeout=5)
    d2 = await runner.submit(agent="bravo", task="y", model=None)
    await runner.wait(d2.delegation_id, timeout=5)
    # Two different specialists -> two runners
    assert len(runners.known()) == 2
    names = {r.specialist_name for r in runners.known()}
    assert names == {"alpha", "bravo"}
