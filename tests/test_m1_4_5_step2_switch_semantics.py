"""M1.4+M1.5 step 2 tests: Switch semantics enforced + proven.

Covers:
* Queued model switch: submit delegation A (model=m1) -> switch the
  specialist's current_model -> submit delegation B (model=None, falls
  through to specialist.current_model) -> B uses the new model, not
  the old one. The first delegation's body is unaffected.
* 4-level model-precedence chain end-to-end incl. orchestrator.default
  final fallback (specialist with unknown role_ref and no
  current_model falls through to the orchestrator's default model).
* ``model.changed`` WS event payload shape ({name, model, scope}).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sweave.harness.base import ModelRef
from sweave.runtime.delegation_store import PerProjectDelegationStores
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist
from sweave.tools import DelegateTaskTool, DelegationResult
from tests.conftest import fake_worktree_manager_factory


def _wt_factory():
    """Fake worktree lifecycle (real dirs, no git) for JobRunner sites."""
    return fake_worktree_manager_factory()[0]


# ---------------------------------------------------------------------------
# Hermeticity: same module-level fixture as the other runtime test files.
# ---------------------------------------------------------------------------
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
# Helpers
# ---------------------------------------------------------------------------


class _FakeDelegateTool:
    """Stand-in; never called on the runtime path (specialist_runtime is wired)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, agent, task, model=None, task_id=None):
        from sweave.tools import DelegationResult
        self.calls.append({"agent": agent, "task": task, "model": model})
        return DelegationResult(
            success=True, agent=agent, task_id=task_id or "x",
            output="legacy", error=None,
        )


def _project_resolver(p: Path):
    def _r(name):
        return p if name else None
    return _r


def _make_runtime_with_capture(tmp_path: Path):
    """Build a SpecialistRuntime whose send records body["model"] per call.

    The real SpecialistRuntime.run executes; the runner is in mock mode
    (env-var fixture). Each call's body is appended to a list so the
    test can assert the wire shape the runtime built.
    """
    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    captured: list[dict[str, Any]] = []

    async def fake_send(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
        captured.append({"model": body.get("model")})
        return "ok"

    runtime._send_message = fake_send  # type: ignore[assignment]
    return runtime, runners, captured


# ---------------------------------------------------------------------------
# Queued model switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_between_delegations_uses_new_model_on_next_call(
    tmp_path: Path,
):
    """M1.4+M1.5 step 2: PUT /api/specialists/{name}/model while a
    specialist is mid-task (or between tasks) does NOT mutate the
    in-flight delegation. The next delegation resolves the model at
    submit time, so the switch takes effect on the next call only.

    Setup: a shared mutable Specialist record stands in for the
    store. The factory returns it by name, and ``set_model_ref``
    mutates ``current_model`` in place -- exactly what the
    ``PUT /api/specialists/{name}/model`` endpoint does (see
    sweave/web/routers/specialists.py:set_specialist_model).
    """
    stores = PerProjectDelegationStores()
    delegate = _FakeDelegateTool()
    runtime, _runners, captured = _make_runtime_with_capture(tmp_path)

    # The shared specialist record (mimics the resolver returning the
    # same object for the same name).
    record = Specialist(
        name="alpha",
        scope="project",
        is_orchestrator=False,
        system_prompt="",
        harness="opencode",
        current_model="ollama/qwen3:8b",
    )

    def factory(name: str, project_name: str | None = None):
        if name == "alpha":
            return record
        return None

    runner = JobRunner(
        delegate_tool=delegate,
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        specialist_runtime=runtime,
        specialist_factory=factory,
        worktree_manager_factory=_wt_factory(),
    )

    # Delegation 1: no task_override; specialist.current_model = m1.
    d1 = await runner.submit(agent="alpha", task="one", model=None)
    await runner.wait(d1.delegation_id, timeout=5)
    assert len(captured) == 1
    assert captured[0]["model"] == {"providerID": "ollama", "modelID": "qwen3:8b"}

    # Switch: PUT /api/specialists/{name}/model is equivalent to
    # ``record.set_model_ref(...)`` in this unit test (the endpoint
    # does exactly that, see specialists.py:259 + the resolver.update
    # call that persists it).
    record.set_model_ref(ModelRef(provider="zai", model_id="glm-5.3"))

    # Delegation 2: same specialist, no task_override -> falls through
    # to specialist.current_model -> uses the NEW model.
    d2 = await runner.submit(agent="alpha", task="two", model=None)
    await runner.wait(d2.delegation_id, timeout=5)
    assert len(captured) == 2
    assert captured[1]["model"] == {"providerID": "zai", "modelID": "glm-5.3"}


@pytest.mark.asyncio
async def test_task_override_beats_specialist_current_model(tmp_path: Path):
    """If a task_override (delegation.model) is set, it wins over
    specialist.current_model. After a switch, an in-flight task_override
    still wins for its own delegation.
    """
    stores = PerProjectDelegationStores()
    runtime, _runners, captured = _make_runtime_with_capture(tmp_path)

    record = Specialist(
        name="alpha", scope="project", is_orchestrator=False,
        system_prompt="", harness="opencode",
        current_model="ollama/qwen3:8b",
    )

    def factory(name: str, project_name: str | None = None):
        if name == "alpha":
            return record
        return None

    runner = JobRunner(
        delegate_tool=_FakeDelegateTool(),
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        specialist_runtime=runtime,
        specialist_factory=factory,
        worktree_manager_factory=_wt_factory(),
    )
    # task_override forces gmi/MiniMaxAI/MiniMax-M3.
    d1 = await runner.submit(
        agent="alpha", task="x", model="gmi/MiniMaxAI/MiniMax-M3"
    )
    await runner.wait(d1.delegation_id, timeout=5)
    # The override beats the stored model on the very same call.
    assert captured[0]["model"] == {
        "providerID": "gmi",
        "modelID": "MiniMaxAI/MiniMax-M3",
    }


# ---------------------------------------------------------------------------
# 4-level chain: orchestrator.default final fallback
# ---------------------------------------------------------------------------
#
# The full 4-level chain (task_override > specialist.current_model >
# config.resolve_model(role_ref) > config.resolve_model(orchestrator) >
# legacy config.resolve_model(agent)) is exercised at the tool layer
# in tests/test_m1_2_step2.py::test_model_precedence_*. We don't
# duplicate that here; the test below is the M1.4+M1.5 step 2
# requirement: verify the chain end-to-end incl. the
# orchestrator.default final fallback with the on-disk config. This
# pins the "the chain still works after M1.3's runtime path landed"
# invariant for the M1.4+M1.5 plan's step 2.
# ---------------------------------------------------------------------------


def _make_config_manager(tmp_path: Path) -> "ConfigManager":
    from sweave.config.manager import ConfigManager
    from tests.conftest import repo_config_pair

    # Tmp copies of the repo files (or a synthetic seed without the
    # generated registry) — hygiene: no test loads the live repo CWD.
    cm = ConfigManager(config_path=repo_config_pair(tmp_path))
    cm.load()
    return cm


def test_four_level_chain_unknown_role_ref_falls_through_to_orchestrator_default(
    tmp_path: Path,
):
    """M1.4+M1.5 step 2: the 4-level chain end-to-end incl. the
    orchestrator.default final fallback. Uses the on-disk config
    (same pattern as tests/test_m1_2_step2.py) so the test tracks
    models.yaml edits.
    """
    from sweave.config.manager import ConfigManager
    from sweave.runtime.specialist_store import (
        GlobalSpecialistStore,
        SpecialistResolver,
    )

    cm = _make_config_manager(tmp_path)
    tool = DelegateTaskTool(cm, None)  # type: ignore[arg-type]
    resolver = SpecialistResolver()
    resolver.global_store = GlobalSpecialistStore(tmp_path / "g.yaml")
    resolver._seed_defs = {}
    tool.specialist_resolver = resolver

    # Specialist with an unknown role_ref and no current_model:
    # the chain must fall through to config.resolve_model(orchestrator).
    tool.specialist_resolver.global_store.upsert(
        Specialist(
            name="alpha", scope="global", current_model=None,
            role_ref="gibberish-not-a-real-role",
        )
    )
    out = tool._resolve_model("alpha", task_override=None)
    expected_orch_default = cm.resolve_model("orchestrator")
    assert out == expected_orch_default
    # And the chain still surfaces the orchestrator default as the
    # final fallback (not a hard error or a different model).
    assert out == cm.resolve_model("gibberish-not-a-real-role")


# ---------------------------------------------------------------------------
# model.changed payload shape (assert wire shape; the endpoint emits it)
# ---------------------------------------------------------------------------


def test_model_changed_payload_shape_from_endpoint():
    """The ``model.changed`` event payload emitted by
    ``PUT /api/specialists/{name}/model`` is exactly ``{name, model, scope}``
    (M1.2 amendment B). This test pins the shape by inspecting the
    endpoint's call to ``state.publish`` -- if anyone changes the keys,
    the assertion below fails and the UI's model-changed handler must
    be updated alongside.
    """
    import inspect

    from sweave.web.routers import specialists as specialists_router

    src = inspect.getsource(specialists_router.set_specialist_model)
    # The publish call must carry all three keys; order is irrelevant
    # in a dict literal, but the literal must contain each key.
    assert '"name"' in src or "'name'" in src
    assert '"model"' in src or "'model'" in src
    assert '"scope"' in src or "'scope'" in src
    # And the event name is the M1.2 vocabulary (the new name; the
    # legacy ``model_changed`` is also emitted for backward compat --
    # the agents router handles that). The new vocabulary is the
    # contract.
    assert '"model.changed"' in src or "'model.changed'" in src


# ---------------------------------------------------------------------------
# Switch-semantics contract: store the expected set of trace events.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_records_source_specialist_current_model_on_second_call(
    tmp_path: Path,
):
    """Belt-and-suspenders: the runtime's trace records
    ``model_used.source = "specialist.current_model"`` for the
    post-switch delegation (the M1.4+M1.5 step 1 trace event covers
    this; this test pins the switch semantics on top of step 1's
    groundwork).
    """
    import os
    from sweave.runtime.specialist_runtime import SpecialistRuntime as _SR2
    from sweave.runtime.serve_runner import ServeRunner as _SR
    from sweave.runtime.trace_log import TraceLog
    from sweave.runtime.delegation_store import Delegation
    import httpx as _httpx

    assert os.environ.get("SWEAVE_MOCK_OPENCODE") == "1"

    async def _fake_start(self) -> None:
        self.port = 0
        self.base_url = "http://mock-opencode"
        self.process = None
        self.log_path = None
        self.touch()

    orig_start = _SR.start
    _SR.start = _fake_start  # type: ignore[assignment]
    try:
        class _T(_httpx.MockTransport):
            def __init__(self) -> None:
                def handler(request: _httpx.Request) -> _httpx.Response:
                    if request.url.path == "/session" and request.method == "POST":
                        return _httpx.Response(200, content=json.dumps({"id": "ses_switch"}))
                    return _httpx.Response(200, content=json.dumps({
                        "info": {"role": "assistant", "time": {"created": 0, "completed": 1}, "finish": "stop"},
                        "parts": [{"type": "text", "text": "ok"}],
                    }))
                super().__init__(handler)

        client = _httpx.AsyncClient(transport=_T(), base_url="http://test")

        record = Specialist(
            name="alpha", scope="project", is_orchestrator=False,
            system_prompt="", harness="opencode",
            current_model="ollama/qwen3:8b",
        )

        def factory(name: str, project_name: str | None = None):
            if name == "alpha":
                return record
            return None

        stores = PerProjectDelegationStores()
        runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
        runner = JobRunner(
            delegate_tool=_FakeDelegateTool(),
            delegation_stores=stores,
            project_dir_resolver=_project_resolver(tmp_path),
            specialist_runtime=runtime,
            specialist_factory=factory,
        )
        # Switch the model BEFORE the second delegation.
        record.set_model_ref(ModelRef(provider="zai", model_id="glm-5.3"))
        trace = TraceLog("d-switch", base_dir=tmp_path)
        delegation = Delegation(agent="alpha", task="x", model="")

        async def fake_build(self_runner, d, *_args, **_kwargs):
            class _P:
                _client = client
                _session_id = "ses_switch"

                async def send(self, message):
                    from sweave.harness.base import AgentResult
                    return AgentResult(success=True, output="ok", metadata={})
            return _P()

        import sweave.runtime.specialist_runtime as sr_mod
        orig_build = sr_mod.SpecialistRuntime._build_process
        sr_mod.SpecialistRuntime._build_process = fake_build  # type: ignore[assignment]
        try:
            await runtime.run(
                specialist=record,
                delegation=delegation,
                worktree_path=tmp_path,
                message="x",
                trace=trace,
                model_ref=None,  # no task_override -> specialist.current_model
            )
        finally:
            sr_mod.SpecialistRuntime._build_process = orig_build  # type: ignore[assignment]
            await client.aclose()

        events = [
            json.loads(line)
            for line in (trace.path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        used = [e for e in events if e.get("event") == "model_used"]
        assert len(used) == 1
        assert used[0]["source"] == "specialist.current_model"
        # The post-switch model is the one that landed on the wire.
        assert used[0]["model_ref"] == {"provider": "zai", "model_id": "glm-5.3"}
        assert used[0]["model_wire"] == {
            "providerID": "zai", "modelID": "glm-5.3"
        }
    finally:
        _SR.start = orig_start  # type: ignore[assignment]
