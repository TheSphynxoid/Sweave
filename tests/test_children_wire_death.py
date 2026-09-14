"""Honest failure states for child delegations (2026-09-10 ruling).

A child whose opencode wire died parks at ``review`` with
``error=null`` because :meth:`SpecialistRuntime._send_message` returns
the failure IN-BAND as a ``[chat error: ...]`` string; the chat loop
knows that prefix, but JobRunner treated it as a successful output
and transitioned to review. The record now carries the error text and
a ``failed`` status (the trace keeps logging as before).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sweave.runtime.delegation_store import PerProjectDelegationStores
from sweave.runtime.job_runner import JobRunner
from sweave.tools import DelegationResult
from sweave.web.events import WSEventBus

SENTINEL_OUTPUT = "[chat error: APIError: 401 upstream rejected]"


class StubDelegateTool:
    """Legacy-path stub returning the in-band wire-death sentinel."""

    async def execute(
        self,
        agent: str,
        task: str,
        model: str | None = None,
        task_id: str | None = None,
    ) -> DelegationResult:
        # success=True with a sentinel -- exactly what the runtime
        # in-band error convention produces for a wire-dead child.
        return DelegationResult(
            success=True,
            agent=agent,
            task_id=task_id or "x",
            output=SENTINEL_OUTPUT,
            error=None,
        )


class StubRuntime:
    """Runtime-path stub: ``run`` mirrors SpecialistRuntime.run's
    in-band error contract (returns the sentinel, never raises)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, *, specialist, delegation, worktree_path, message, trace, model_ref=None, **kwargs):
        self.calls.append({"specialist": specialist.name, "delegation": delegation.delegation_id})
        return SENTINEL_OUTPUT


def _make_runner(project_dir: Path, *, runtime: StubRuntime | None = None):
    stores = PerProjectDelegationStores()
    kwargs: dict[str, Any] = {
        "delegation_stores": stores,
        "event_bus": WSEventBus(),
        "project_dir_resolver": lambda name: project_dir,
    }
    if runtime is not None:
        kwargs.update(
            delegate_tool=None,
            specialist_runtime=runtime,
            specialist_factory=lambda name, project=None: None,
            specialist_saver=lambda specialist, project_name: None,
        )
    else:
        kwargs["delegate_tool"] = StubDelegateTool()
    return JobRunner(**kwargs), stores


@pytest.mark.asyncio
async def test_wire_death_sentinel_fails_the_record_legacy_path(tmp_path: Path):
    runner, _ = _make_runner(tmp_path)
    d = await runner.submit("backend", "x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert final is not None
    assert final.status == "failed"
    assert final.error is not None and "APIError: 401" in final.error
    assert final.output == ""


@pytest.mark.asyncio
async def test_wire_death_sentinel_fails_the_record_runtime_path(tmp_path: Path):
    runtime = StubRuntime()
    runner, _ = _make_runner(tmp_path, runtime=runtime)
    d = await runner.submit("backend", "x", project_name="p1")
    final = await runner.wait(d.delegation_id, timeout=5)
    assert runtime.calls, "specialist runtime must have been invoked"
    assert final is not None
    assert final.status == "failed"
    assert final.error is not None and "APIError: 401" in final.error
    assert final.output == ""
