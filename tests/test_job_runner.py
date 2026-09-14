"""JobRunner tests with a stubbed DelegateTaskTool.

M1.1 step 2: JobRunner holds a PerProjectDelegationStores (not a single
DelegationStore). Each test gets a fresh per-project store rooted at
a tmp path; the stub delegate_tool returns a fixed result so the
runner reaches a terminal status quickly.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from sweave.runtime.delegation_store import (
    DelegationStore,
    PerProjectDelegationStores,
)
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.trace_log import read_trace
from sweave.tools import DelegationResult
from sweave.web.events import WSEventBus


class StubDelegateTool:
    """Stand-in for DelegateTaskTool that records calls and returns a fixed result."""

    def __init__(self, result: DelegationResult | None = None, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result
        self._fail = fail
        # Mimic the exception path
        self.exception: Exception | None = None

    async def execute(
        self,
        agent: str,
        task: str,
        model: str | None = None,
        task_id: str | None = None,
    ) -> DelegationResult:
        self.calls.append(
            {"agent": agent, "task": task, "model": model, "task_id": task_id}
        )
        if self.exception is not None:
            raise self.exception
        if self._result is not None:
            return self._result
        if self._fail:
            return DelegationResult(
                success=False, agent=agent, task_id=task_id or "x",
                output="", error="forced failure",
            )
        return DelegationResult(
            success=True, agent=agent, task_id=task_id or "x", output="ok"
        )


def _make_runner(tool: StubDelegateTool, project_dir: Path) -> tuple[JobRunner, PerProjectDelegationStores]:
    """Build a JobRunner with a fresh per-project store rooted at *project_dir*."""
    stores = PerProjectDelegationStores()
    runner = JobRunner(
        delegate_tool=tool,
        delegation_stores=stores,
        event_bus=None,
        project_dir_resolver=lambda name: project_dir,
    )
    return runner, stores


@pytest.mark.asyncio
async def test_submit_returns_queued_delegation(tmp_path: Path):
    bus = WSEventBus()
    tool = StubDelegateTool()
    runner, _ = _make_runner(tool, tmp_path)

    d = await runner.submit("backend", "hello", model="m1", project_name="p1")
    assert d.status == "queued"
    assert d.agent == "backend"
    assert d.model == "m1"
    assert d.task == "hello"
    assert d.project_name == "p1"


@pytest.mark.asyncio
async def test_submit_writes_trace_file(tmp_path: Path):
    bus = WSEventBus()
    tool = StubDelegateTool()
    runner, _ = _make_runner(tool, tmp_path)

    d = await runner.submit("backend", "x", project_name="p1")
    await runner.wait(d.delegation_id, timeout=5)
    events = read_trace(d.delegation_id)
    assert len(events) >= 3
    assert [e["event"] for e in events[:3]] == [
        "status_changed", "status_changed", "prompt_sent",
    ]


@pytest.mark.asyncio
async def test_successful_run_ends_in_review(tmp_path: Path):
    """M1.3 step 4: on stream success the delegation enters 'review'
    (not 'done') -- human / cross-review promotes to 'done' in M1.4.
    The test was renamed to reflect the new contract.
    """
    bus = WSEventBus()
    tool = StubDelegateTool(DelegationResult(
        success=True, agent="backend", task_id="t", output="OK",
    ))
    runner, _ = _make_runner(tool, tmp_path)

    d = await runner.submit("backend", "x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status == "review"
    assert final.output == "OK"
    assert tool.calls and tool.calls[0]["task"] == "x"


@pytest.mark.asyncio
async def test_failed_run_ends_in_failed(tmp_path: Path):
    bus = WSEventBus()
    tool = StubDelegateTool(fail=True)
    runner, _ = _make_runner(tool, tmp_path)

    d = await runner.submit("backend", "x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status == "failed"
    assert final.error == "forced failure"


@pytest.mark.asyncio
async def test_exception_in_tool_ends_in_failed(tmp_path: Path):
    bus = WSEventBus()
    tool = StubDelegateTool()
    tool.exception = RuntimeError("kaboom")
    runner, _ = _make_runner(tool, tmp_path)

    d = await runner.submit("backend", "x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status == "failed"
    assert "kaboom" in (final.error or "")


@pytest.mark.asyncio
async def test_submit_without_bus_works(tmp_path: Path):
    """Event bus is optional; the runner works without one."""
    tool = StubDelegateTool()
    runner, _ = _make_runner(tool, tmp_path)

    d = await runner.submit("backend", "x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status == "review"


@pytest.mark.asyncio
async def test_delegation_persists_per_project_to_disk(tmp_path: Path):
    """M1.1 step 2: the per-project DelegationStore writes the record to
    ``{project_dir}/.sweave/delegations.json`` on every update, and a fresh
    runner reading the same project_dir sees the record."""
    project_dir = tmp_path / "p1"
    project_dir.mkdir()
    tool = StubDelegateTool(DelegationResult(
        success=True, agent="backend", task_id="t", output="OK",
    ))
    runner, stores = _make_runner(tool, project_dir)
    d = await runner.submit("backend", "x", project_name="p1")
    await runner.wait(d.delegation_id, timeout=5)

    file_path = project_dir / ".sweave" / "delegations.json"
    assert file_path.exists(), "delegations.json not written"
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    assert "delegations" in payload
    assert len(payload["delegations"]) == 1
    assert payload["delegations"][0]["delegation_id"] == d.delegation_id
    assert payload["delegations"][0]["status"] == "review"

    # A fresh runner reading the same project_dir sees the same record
    # once it has materialised the per-project store from disk (which
    # happens on first access; the resolver points at the same path).
    fresh_runner, fresh_stores = _make_runner(StubDelegateTool(), project_dir)
    await fresh_stores.for_project(project_dir)  # materialise from disk
    found = await fresh_runner.wait(d.delegation_id, timeout=1)
    assert found is not None
    assert found.delegation_id == d.delegation_id


@pytest.mark.asyncio
async def test_different_projects_get_different_stores(tmp_path: Path):
    """Two projects never see each other's delegations."""
    p1 = tmp_path / "p1"
    p2 = tmp_path / "p2"
    p1.mkdir()
    p2.mkdir()
    tool = StubDelegateTool()
    runner, stores = _make_runner(tool, p1)
    await runner.submit("backend", "x", project_name="p1")
    # P2 needs its own runner with a different resolver
    runner2, _ = _make_runner(tool, p2)
    await runner2.submit("backend", "y", project_name="p2")
    await asyncio.sleep(0.05)  # let the background tasks settle

    p1_store = await stores.for_project(p1)
    p2_store = await stores.for_project(p2)
    assert len(p1_store.list()) == 1
    assert len(p2_store.list()) == 1
    assert p1_store.list()[0].task == "x"
    assert p2_store.list()[0].task == "y"


@pytest.mark.asyncio
async def test_corrupted_disk_file_does_not_500(tmp_path: Path):
    """A corrupt ``delegations.json`` is logged + skipped; new writes
    overwrite the bad file rather than crashing the API."""
    project_dir = tmp_path / "p1"
    project_dir.mkdir()
    target = project_dir / ".sweave"
    target.mkdir(parents=True, exist_ok=True)
    (target / "delegations.json").write_text("not valid json {", encoding="utf-8")
    tool = StubDelegateTool()
    runner, _ = _make_runner(tool, project_dir)
    # Should not raise; the bad file is treated as empty.
    d = await runner.submit("backend", "x", project_name="p1")
    assert d.delegation_id is not None
    await runner.wait(d.delegation_id, timeout=5)
    # Subsequent write replaces the bad file with valid JSON.
    payload = json.loads((target / "delegations.json").read_text(encoding="utf-8"))
    assert "delegations" in payload


class _StubSpecialistRuntime:
    """Mimics SpecialistRuntime.run: records the call, stamps a
    session id on the specialist (the M1.3 contract the saver
    persists), and returns canned output. No subprocess."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        *,
        specialist,
        delegation,
        worktree_path,
        message,
        trace,
        model_ref=None,
        # Step-4 additions (selection + engine context). Accepted
        # and ignored — this double pins the opencode-path contract.
        # (Same rule as GOTCHAS: doubles must track run's kwargs.)
        harness=None,
        project_dir=None,
        permission_roots=None,
        max_retries=None,
    ) -> str:
        self.calls.append({"specialist": specialist.name, "message": message})
        specialist.session_id = "ses_stub_1"
        return "stub output"


def _make_runtime_runner(
    tmp_path: Path, factory, saver_calls: list
) -> JobRunner:
    """JobRunner on the SpecialistRuntime path with an isolated
    traces dir (never the real ~/.sweave/traces)."""
    return JobRunner(
        delegate_tool=StubDelegateTool(),
        delegation_stores=PerProjectDelegationStores(),
        event_bus=None,
        traces_dir=tmp_path / "traces",
        project_dir_resolver=lambda name: tmp_path,
        specialist_runtime=_StubSpecialistRuntime(),
        specialist_factory=factory,
        specialist_saver=lambda spec, project: saver_calls.append(
            (spec.name, spec.scope, spec.session_id, project)
        ),
    )


def _seed_backend() -> Any:
    from sweave.runtime.specialist_store import Specialist

    return Specialist(
        name="backend-specialist",
        scope="seed",
        is_orchestrator=False,
        system_prompt="seed prompt",
        harness="opencode",
    )


@pytest.mark.asyncio
async def test_seed_specialist_session_never_persisted(tmp_path: Path):
    """Seed-scope views must not materialise store copies.

    2026-09-09: seed `backend-specialist` vanished behind an
    auto-saved global of the same name (resolution shadowing).
    The delegation still succeeds; only the saver call is skipped,
    with a `session_id_transient` trace note."""
    saver_calls: list = []
    runner = _make_runtime_runner(tmp_path, lambda name: _seed_backend(), saver_calls)

    d = await runner.submit("backend-specialist", "do work", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=10)
    assert final is not None and final.status == "review"
    assert saver_calls == []
    events = read_trace(d.delegation_id, base_dir=tmp_path / "traces")
    kinds = [e.get("event") for e in events]
    assert "session_id_transient" in kinds
    assert "session_id_persisted" not in kinds


@pytest.mark.asyncio
async def test_project_specialist_session_persisted(tmp_path: Path):
    """Control: project/global records still persist their session."""
    from sweave.runtime.specialist_store import Specialist

    saver_calls: list = []
    rec = Specialist(
        name="alpha",
        scope="project",
        is_orchestrator=False,
        system_prompt="p",
        harness="opencode",
    )
    runner = _make_runtime_runner(tmp_path, lambda name: rec, saver_calls)

    d = await runner.submit("alpha", "do work", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=10)
    assert final is not None and final.status == "review"
    assert saver_calls == [("alpha", "project", "ses_stub_1", "p1")]
    events = read_trace(d.delegation_id, base_dir=tmp_path / "traces")
    assert "session_id_persisted" in [e.get("event") for e in events]
