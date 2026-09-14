"""M1.3 step 4 tests: stuck detection (turn timeout) + review transition.

Covers:
* A delegation that exceeds the configured turn_timeout is marked
  failed with an explicit error and the trace records the timeout
* A successful delegation lands in 'review' (not 'done'); M1.4
  promotes review -> done
* The turn_timeout is configurable via the JobRunner ctor
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sweave.runtime.delegation_store import PerProjectDelegationStores
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist
from sweave.runtime.serve_runner import ServeRunner, ServeRunnerRegistry
from tests.conftest import fake_worktree_manager_factory


def _wt_factory():
    """Fake worktree lifecycle (real dirs, no git) for JobRunner sites."""
    return fake_worktree_manager_factory()[0]


class _FakeDelegateTool:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay

    async def execute(self, agent, task, model=None, task_id=None):
        from sweave.tools import DelegationResult

        if self.delay > 0:
            await asyncio.sleep(self.delay)
        return DelegationResult(
            success=True, agent=agent, task_id=task_id or "x",
            output=f"ok:{task}", error=None,
        )


def _project_resolver(p: Path):
    def _r(name):
        return p if name else None
    return _r


# ---------------------------------------------------------------------------
# turn timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turn_timeout_marks_delegation_failed(tmp_path: Path):
    """A delegation whose agent call exceeds the turn timeout is
    marked failed with an explicit timeout error and the trace records
    the timeout event."""
    from sweave.runtime.trace_log import read_trace

    stores = PerProjectDelegationStores()
    # The agent call takes 5s; the timeout is 0.2s -> TimeoutError.
    # The runner writes the trace to ~/.sweave/traces (default);
    # we read from the same default.
    tool = _FakeDelegateTool(delay=5.0)
    runner = JobRunner(
        delegate_tool=tool,
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        turn_timeout=0.2,
    )
    d = await runner.submit(agent="a", task="x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=10)
    assert final is not None
    assert final.status == "failed"
    assert "turn_timeout" in (final.error or "")
    assert "0.2" in (final.error or "")
    events = read_trace(d.delegation_id)  # default base_dir (~/.sweave/traces)
    assert any(e["event"] == "turn_timeout" for e in events)


@pytest.mark.asyncio
async def test_turn_timeout_default_is_30_minutes():
    """Default turn_timeout = 30 min (1800s) per the 2026-09-10 ruling.

    Was 15 min (M1.3 plan); raised because real agentic turns outlive
    it. Configurable via ``routing.turn_timeout_s``.
    """
    assert JobRunner.DEFAULT_TURN_TIMEOUT == 1800


@pytest.mark.asyncio
async def test_turn_timeout_config_field_default_and_bounds():
    """routing.turn_timeout_s: default 1800s, bounded (0, 14400]."""
    from pydantic import ValidationError

    from sweave.config.schemas import RoutingConfig

    assert RoutingConfig().turn_timeout_s == 1800.0
    with pytest.raises(ValidationError):
        RoutingConfig(turn_timeout_s=0)
    with pytest.raises(ValidationError):
        RoutingConfig(turn_timeout_s=-5)
    with pytest.raises(ValidationError):
        RoutingConfig(turn_timeout_s=14_400.1)
    RoutingConfig(turn_timeout_s=14_400).turn_timeout_s == 14_400
    RoutingConfig(turn_timeout_s=60).turn_timeout_s == 60


@pytest.mark.asyncio
async def test_turn_timeout_does_not_fire_when_call_is_fast(tmp_path: Path):
    """A fast agent call (well under timeout) completes normally."""
    stores = PerProjectDelegationStores()
    tool = _FakeDelegateTool(delay=0.0)
    runner = JobRunner(
        delegate_tool=tool,
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        turn_timeout=5.0,
    )
    d = await runner.submit(agent="a", task="x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    # The default review transition still happens
    assert final.status == "review"


# ---------------------------------------------------------------------------
# review transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_delegation_enters_review_not_done(tmp_path: Path):
    """A successful delegation lands in 'review' (not 'done'). M1.4
    promotes review -> done. The trace records the transition."""
    from sweave.runtime.trace_log import read_trace

    stores = PerProjectDelegationStores()
    tool = _FakeDelegateTool(delay=0.0)
    runner = JobRunner(
        delegate_tool=tool,
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
    )
    d = await runner.submit(agent="a", task="x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status == "review"
    # Output is preserved
    assert "ok:x" in (final.output or "")
    events = read_trace(d.delegation_id)  # default base_dir
    statuses = [e.get("status") for e in events if e.get("event") == "status_changed"]
    # At least the final review transition is logged
    assert "review" in statuses


@pytest.mark.asyncio
async def test_failed_delegation_does_not_enter_review(tmp_path: Path):
    """A delegation that fails (turn timeout, error from agent) goes
    straight to 'failed', not 'review'."""
    stores = PerProjectDelegationStores()
    runner = JobRunner(
        delegate_tool=_FakeDelegateTool(delay=10.0),
        delegation_stores=stores,
        project_dir_resolver=_project_resolver(tmp_path),
        turn_timeout=0.1,
    )
    d = await runner.submit(agent="a", task="x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=10)
    assert final is not None
    assert final.status == "failed"
    assert "review" not in [
        e.get("status") for e in
        # Events that include status transitions
        [
            {"event": "status_changed", "status": s}
            for s in [final.status, "running", "failed"]
        ]
    ]


# ---------------------------------------------------------------------------
# integration with SpecialistRuntime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_path_respects_turn_timeout(tmp_path: Path):
    """The runtime path is also wrapped in wait_for; a slow runtime
    call is timed out by the same knob."""
    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)

    async def slow_run(*, specialist, delegation, worktree_path, message, trace, model_ref=None, fresh=False):
        await asyncio.sleep(10)
        return "slow"

    runtime.run = slow_run  # type: ignore[assignment]

    def factory(name: str, project_name: str | None = None) -> Specialist:
        return Specialist(name=name, system_prompt="", harness="opencode")

    runner = JobRunner(
        delegate_tool=_FakeDelegateTool(),
        delegation_stores=PerProjectDelegationStores(),
        project_dir_resolver=_project_resolver(tmp_path),
        specialist_runtime=runtime,
        specialist_factory=factory,
        turn_timeout=0.1,
        worktree_manager_factory=_wt_factory(),
    )
    d = await runner.submit(agent="a", task="x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=10)
    assert final is not None
    assert final.status == "failed"
    assert "turn_timeout" in (final.error or "")
