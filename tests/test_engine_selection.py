"""Engine step 4 tests: per-specialist selection (fallback removed).

Hermetic: fake Harness objects stand in for both engines in the
registry (no node, no sidecar, no LLM).

Covers the step-4 done-gate shape: resolver tier order
(override > mock > specialist > config > selection-default), the
step-4 default flip, engine dispatch with identical Message
metadata, the 2026-09-14 fallback REMOVAL (engine death fails loud,
the opencode path is never entered — fail loud across harnesses),
the after-work loud-failure guard, and the JobRunner transient
override channel.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from sweave.engine.protocol import ENGINE_HARNESS_NAME
from sweave.harness.base import AgentResult, harness_registry, resolve_harness_name
from sweave.runtime.specialist_store import Specialist


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
# Resolver matrix (pure — no I/O)
# ---------------------------------------------------------------------------


def test_override_wins_even_under_mock():
    name, source = resolve_harness_name("sweave-engine", "opencode", "opencode")
    assert (name, source) == ("sweave-engine", "override")


def test_mock_pins_opencode():
    name, source = resolve_harness_name(None, "sweave-engine", "sweave-engine")
    assert (name, source) == ("opencode", "mock")


def test_specialist_tier(monkeypatch):
    monkeypatch.delenv("SWEAVE_MOCK_OPENCODE", raising=False)
    monkeypatch.setitem(
        harness_registry._harnesses, "mine", object()
    )
    name, source = resolve_harness_name(None, "mine", "opencode")
    assert (name, source) == ("mine", "specialist")


def test_config_tier(monkeypatch):
    monkeypatch.delenv("SWEAVE_MOCK_OPENCODE", raising=False)
    name, source = resolve_harness_name(None, "nope", "opencode")
    assert (name, source) == ("opencode", "config")


def test_fallback_on_unknowns(monkeypatch):
    monkeypatch.delenv("SWEAVE_MOCK_OPENCODE", raising=False)
    assert resolve_harness_name(None, "nope", "alsono") == ("opencode", "fallback")
    assert resolve_harness_name(None, None, None) == ("opencode", "fallback")
    assert resolve_harness_name("", "", "") == ("opencode", "fallback")


def test_new_records_default_to_engine():
    assert Specialist(name="fresh").harness == ENGINE_HARNESS_NAME


# ---------------------------------------------------------------------------
# Fake-harness helpers
# ---------------------------------------------------------------------------


class _FakeEngineProcess:
    def __init__(self, spec, script, seen):
        self.spec = spec
        self._seen = seen
        self._script = script
        self._session_id = "eng_fake_1"
        self.pid = -1

    async def send(self, message, on_chunk=None, trace=None, on_reasoning=None):
        self._seen.append(message)
        self._seen_reasoning_cb = on_reasoning
        return await self._script(message, trace)

    async def terminate(self):
        pass

    async def wait(self):
        return AgentResult(success=True, output="")


class _FakeEngineHarness:
    name = ENGINE_HARNESS_NAME

    def __init__(self, script, seen, fail_spawn=None, specs=None):
        self._script = script
        self._seen = seen
        self._fail_spawn = fail_spawn
        self._specs = specs

    async def spawn(self, spec):
        if self._fail_spawn is not None:
            raise self._fail_spawn
        if self._specs is not None:
            self._specs.append(spec)
        return _FakeEngineProcess(spec, self._script, self._seen)

    async def attach(self, session_id, spec):
        proc = await self.spawn(spec)
        proc._session_id = session_id
        return proc

    def get_default_tools(self):
        return ["read", "edit", "write", "bash", "glob", "grep", "todo"]

    async def health_check(self):
        return True


def _register_fake(monkeypatch, script, seen, fail_spawn=None, specs=None):
    monkeypatch.setitem(
        harness_registry._harnesses,
        ENGINE_HARNESS_NAME,
        _FakeEngineHarness(script, seen, fail_spawn, specs),
    )


class _Trace:
    def __init__(self):
        self.events: list[tuple[str, Any]] = []

    def append(self, event, payload=None):
        self.events.append((event, payload or {}))

    def kinds(self, name):
        return [p for e, p in self.events if e == name]


def _specialist(**kw):
    base = dict(
        name="worker",
        scope="project",
        is_orchestrator=False,
        system_prompt="",
        harness="sweave-engine",
    )
    base.update(kw)
    return Specialist(**base)


def _delegation(**kw):
    from sweave.runtime.delegation_store import Delegation

    base = dict(
        delegation_id="d-eng-1",
        agent="worker",
        task="do the thing",
        model="",
        project_name="proj",
    )
    base.update(kw)
    return Delegation(**base)


def _runtime():
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    return SpecialistRuntime(runners=ServeRunnerRegistry())


# ---------------------------------------------------------------------------
# _run_engine_attempt: dispatch shape + after-work loud-failure guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_attempt_success_shape(monkeypatch, tmp_path: Path):
    from sweave.harness.base import Message  # noqa: F401 (contract shape)

    seen: list = []

    async def script(message, trace):
        if trace is not None:
            trace.append("tool.started", {"tool": "read"})
            trace.append("tool.completed", {"tool": "read"})
        return AgentResult(success=True, output="engine did it")

    _register_fake(monkeypatch, script, seen)
    runtime = _runtime()
    trace = _Trace()
    delegation = _delegation()
    out, fallback = await runtime._run_engine_attempt(
        specialist=_specialist(),
        delegation=delegation,
        worktree_path=tmp_path,
        message="do the thing",
        trace=trace,  # type: ignore[arg-type]
        project_dir=tmp_path,
        permission_roots=[],
    )
    assert fallback is None
    assert out == "engine did it"
    # Message contract the adapter enforces.
    assert len(seen) == 1
    msg = seen[0]
    assert msg.metadata["delegation_id"] == "d-eng-1"
    assert msg.metadata["role"] == "specialist"
    assert msg.metadata["permission_map"]["external_directory"]["*"] == "ask"
    # Specialists get the execution-tool baseline; the model + session
    # bindings land like the opencode path records them.
    assert delegation.engine_session_id == "eng_fake_1"
    assert trace.kinds("model_used"), "model_used parity event missing"


@pytest.mark.asyncio
async def test_engine_attempt_orchestrator_readonly_tools_and_charter(monkeypatch, tmp_path: Path):
    seen: list = []
    specs: list = []

    async def script(message, trace):
        return AgentResult(success=True, output="ok")

    _register_fake(monkeypatch, script, seen, specs=specs)
    runtime = _runtime()
    trace = _Trace()
    out, fallback = await runtime._run_engine_attempt(
        specialist=_specialist(
            name="orchestrator",
            is_orchestrator=True,
            system_prompt="ORCHESTRATOR CHARTER",
        ),
        delegation=_delegation(agent="orchestrator"),
        worktree_path=tmp_path,
        message="hello",
        trace=trace,  # type: ignore[arg-type]
        project_dir=tmp_path,
    )
    assert (out, fallback) == ("ok", None)
    assert seen[0].metadata["role"] == "orchestrator"
    # New session: the charter rides along (no per-message agent pin
    # on this protocol); reused sessions remember it.
    assert "ORCHESTRATOR CHARTER" in seen[0].content
    # 2026-09-14 ruling: the orchestrator gets read-only exec tools
    # (factual Q&A without a delegation); never mutate/run tools.
    from sweave.runtime.specialist_runtime import ORCHESTRATOR_READONLY_TOOLS

    assert specs and list(specs[0].tools) == list(ORCHESTRATOR_READONLY_TOOLS)
    assert "edit" not in specs[0].tools and "bash" not in specs[0].tools


@pytest.mark.asyncio
async def test_engine_attempt_spawn_failure_falls_back(monkeypatch, tmp_path: Path):
    _register_fake(
        monkeypatch, None, [], fail_spawn=RuntimeError("node gone")
    )
    runtime = _runtime()
    out, reason = await runtime._run_engine_attempt(
        specialist=_specialist(),
        delegation=_delegation(),
        worktree_path=tmp_path,
        message="x",
        trace=_Trace(),  # type: ignore[arg-type]
    )
    assert out is None
    assert reason is not None and "RuntimeError" in reason


@pytest.mark.asyncio
async def test_engine_attempt_kill_before_work_falls_back(monkeypatch, tmp_path: Path):
    import httpx

    async def script(message, trace):
        raise httpx.ConnectError("sidecar killed")

    _register_fake(monkeypatch, script, [])
    runtime = _runtime()
    out, reason = await runtime._run_engine_attempt(
        specialist=_specialist(),
        delegation=_delegation(),
        worktree_path=tmp_path,
        message="x",
        trace=_Trace(),  # type: ignore[arg-type]
    )
    assert out is None
    assert reason is not None and "ConnectError" in reason


@pytest.mark.asyncio
async def test_engine_attempt_kill_after_work_never_falls_back(
    monkeypatch, tmp_path: Path
):
    async def script(message, trace):
        trace.append("tool.started", {"tool": "edit"})
        raise RuntimeError("killed mid-turn")

    _register_fake(monkeypatch, script, [])
    runtime = _runtime()
    out, fallback = await runtime._run_engine_attempt(
        specialist=_specialist(),
        delegation=_delegation(),
        worktree_path=tmp_path,
        message="x",
        trace=_Trace(),  # type: ignore[arg-type]
    )
    assert fallback is None
    assert out is not None and out.startswith(
        "[chat error: engine_failed_after_work:"
    )


@pytest.mark.asyncio
async def test_engine_attempt_error_without_work_falls_back(
    monkeypatch, tmp_path: Path
):
    async def script(message, trace):
        return AgentResult(
            success=False, output="", error="[chat error: auth_missing: no key]"
        )

    _register_fake(monkeypatch, script, [])
    runtime = _runtime()
    out, reason = await runtime._run_engine_attempt(
        specialist=_specialist(),
        delegation=_delegation(),
        worktree_path=tmp_path,
        message="x",
        trace=_Trace(),  # type: ignore[arg-type]
    )
    assert out is None
    assert reason is not None and "auth_missing" in reason


@pytest.mark.asyncio
async def test_engine_attempt_error_with_work_returned_as_is(
    monkeypatch, tmp_path: Path
):
    async def script(message, trace):
        trace.append("tool.started", {"tool": "bash"})
        trace.append("tool.completed", {"tool": "bash"})
        return AgentResult(
            success=False,
            output="",
            error="[chat error: provider_error: 429 slow down]",
        )

    _register_fake(monkeypatch, script, [])
    runtime = _runtime()
    out, fallback = await runtime._run_engine_attempt(
        specialist=_specialist(),
        delegation=_delegation(),
        worktree_path=tmp_path,
        message="x",
        trace=_Trace(),  # type: ignore[arg-type]
    )
    assert fallback is None
    assert out == "[chat error: provider_error: 429 slow down]"


# ---------------------------------------------------------------------------
# run(): selection event + fallback wiring through the real dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_selects_engine_via_override_and_skips_fallback(
    monkeypatch, tmp_path: Path
):
    seen: list = []

    async def script(message, trace):
        return AgentResult(success=True, output="native reply")

    _register_fake(monkeypatch, script, seen)
    runtime = _runtime()
    trace = _Trace()
    out = await runtime.run(
        specialist=_specialist(harness="opencode"),
        delegation=_delegation(),
        worktree_path=tmp_path,
        message="hi",
        trace=trace,  # type: ignore[arg-type]
        harness="sweave-engine",
        project_dir=tmp_path,
    )
    assert out == "native reply"
    selected = trace.kinds("harness_selected")
    assert len(selected) == 1
    assert selected[0]["selected"] == "sweave-engine"
    assert selected[0]["source"] == "override"
    assert trace.kinds("fallback_used") == []


@pytest.mark.asyncio
async def test_run_fails_loud_on_engine_death_without_fallback(
    monkeypatch, tmp_path: Path
):
    """No automatic cross-harness fallback (user ruling 2026-09-14,
    removal executed same day): an engine-selected turn whose engine
    dies before any work fails LOUD with the engine error. The
    opencode path is never entered (no ``fallback_used`` trace, no
    second session, no double bill)."""
    _register_fake(
        monkeypatch, None, [], fail_spawn=RuntimeError("node gone")
    )
    runtime = _runtime()

    opencode_entered = False

    async def fake_send(
        self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs
    ):
        nonlocal opencode_entered
        opencode_entered = True
        return "opencode-fallback-output"

    runtime._send_message = fake_send  # type: ignore[assignment]
    trace = _Trace()
    out = await runtime.run(
        specialist=_specialist(harness="opencode"),
        delegation=_delegation(),
        worktree_path=tmp_path,
        message="hi",
        trace=trace,  # type: ignore[arg-type]
        harness="sweave-engine",
        project_dir=tmp_path,
    )
    assert "node gone" in out
    assert out.startswith("[chat error:")
    assert opencode_entered is False
    assert trace.kinds("fallback_used") == []


@pytest.mark.asyncio
async def test_run_mock_stays_opencode_without_override(tmp_path: Path):
    runtime = _runtime()

    async def fake_send(
        self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs
    ):
        return "mock-opencode-output"

    runtime._send_message = fake_send  # type: ignore[assignment]
    trace = _Trace()
    out = await runtime.run(
        specialist=_specialist(),  # record says sweave-engine…
        delegation=_delegation(),
        worktree_path=tmp_path,
        message="hi",
        trace=trace,  # type: ignore[arg-type]
        project_dir=tmp_path,
    )
    # …but the mock seam pins opencode so the suite never spawns node.
    assert out == "mock-opencode-output"
    assert trace.kinds("harness_selected")[0]["source"] == "mock"


@pytest.mark.asyncio
async def test_run_engine_attempt_forwards_on_reasoning(
    monkeypatch, tmp_path: Path
):
    """The runtime wires the chat loop's on_reasoning through to the
    engine send (protocol v2 thinking text reaches chat.thinking)."""
    captured: dict = {}

    class _Proc:
        _session_id = "eng_probe"

        async def send(
            self, message, on_chunk=None, trace=None, on_reasoning=None, **kw
        ):
            captured["cb"] = on_reasoning
            if on_reasoning is not None:
                # Same sync-or-async contract as the real send.
                res = on_reasoning("hmm")
                if hasattr(res, "__await__"):
                    await res
            return AgentResult(success=True, output="ok")

    class _Harness:
        name = ENGINE_HARNESS_NAME

        async def spawn(self, spec):
            return _Proc()

        async def attach(self, sid, spec):
            return _Proc()

        def get_default_tools(self):
            return []

        async def health_check(self):
            return True

    monkeypatch.setitem(
        harness_registry._harnesses, ENGINE_HARNESS_NAME, _Harness()
    )
    runtime = _runtime()
    trace = _Trace()
    got: list = []
    out, reason = await runtime._run_engine_attempt(
        specialist=_specialist(),
        delegation=_delegation(),
        worktree_path=tmp_path,
        message="hi",
        trace=trace,  # type: ignore[arg-type]
        project_dir=tmp_path,
        on_reasoning=got.append,
    )
    assert (out, reason) == ("ok", None)
    assert captured.get("cb") is not None
    assert got == ["hmm"]


# ---------------------------------------------------------------------------
# v2/tasks override: validation helper + JobRunner transient channel
# ---------------------------------------------------------------------------


def test_harness_override_validation():
    from fastapi import HTTPException

    from sweave.web.routers.delegations import _validate_harness_override

    assert _validate_harness_override(None) is None
    assert _validate_harness_override("") is None
    assert _validate_harness_override("opencode") == "opencode"
    assert _validate_harness_override("sweave-engine") == "sweave-engine"
    try:
        _validate_harness_override("bogus-harness")
    except HTTPException as e:
        assert e.status_code == 400
    else:  # pragma: no cover
        raise AssertionError("expected HTTP 400 for unknown harness")


@pytest.mark.asyncio
async def test_submit_threads_harness_override_to_run(tmp_path: Path):
    """submit(harness=...) reaches SpecialistRuntime.run transiently."""
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.job_runner import JobRunner
    from sweave.tools import DelegateTaskTool

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    stores = PerProjectDelegationStores()

    seen: list[dict] = []

    class _StubRuntime:
        async def run(self, **kwargs):
            seen.append(kwargs)
            return "stub-output"

    def _factory(name: str, project_name: str | None = None):
        return _specialist(name=name)

    runner = JobRunner(
        delegate_tool=DelegateTaskTool.__new__(DelegateTaskTool),
        delegation_stores=stores,
        traces_dir=tmp_path,
        project_dir_resolver=lambda _name: project_dir,
        specialist_runtime=_StubRuntime(),  # type: ignore[arg-type]
        specialist_factory=_factory,
    )
    d = await runner.submit(
        agent="worker", task="t", project_name="p", harness="sweave-engine"
    )
    terminal = await runner.wait(d.delegation_id, timeout=30)
    assert terminal is not None and terminal.status in ("review", "done")
    assert seen and seen[0].get("harness") == "sweave-engine"
    # The override is consumed, never persisted on the record.
    assert "harness" not in d.to_dict()
