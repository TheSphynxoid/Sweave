"""In-process asyncio job runner for delegations.

The :class:`JobRunner` is the queue. There is no broker: each call to
:meth:`submit` creates a :class:`~sweave.runtime.delegation_store.Delegation`
record (the durable artefact), then schedules the actual delegation on the
event loop. Multiple delegations can be in flight at once; the runner
doesn't enforce a specialist pool (that's M1.3's job).

What the runner does:

* holds the delegation record lifecycle (queued → running → review → done/failed)
* writes every transition to the per-delegation :class:`TraceLog`
* publishes a ``delegation.status_changed`` event on the :class:`WSEventBus`
* returns the ``delegation_id`` synchronously so HTTP handlers can answer
  ``POST /api/v2/tasks`` immediately

What the runner does **not** do (deferred to later milestones):

* lifecycle completion detection from the harness (M1.4)
* per-specialist active-task tracking (M1.3)
* deferral depth / loop detection (M1.6)
* streaming output (M1.8)
* cost budgets (M1.5/6)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from sweave.runtime.delegation_store import Delegation, DelegationStore
from sweave.runtime.trace_log import TraceLog

if TYPE_CHECKING:
    from sweave.tools import DelegateTaskTool
    from sweave.web.events import WSEventBus

logger = logging.getLogger(__name__)


class JobRunner:
    """Owns the delegation lifecycle for one process.

    Construct once in the FastAPI lifespan. The runner is event-loop-bound;
    the underlying ``DelegateTaskTool`` already exposes ``async`` methods,
    so the runner just calls them in background tasks.
    """

    def __init__(
        self,
        delegate_tool: "DelegateTaskTool",
        delegation_store: DelegationStore,
        event_bus: "WSEventBus | None" = None,
        traces_dir: Any = None,
    ) -> None:
        self.delegate_tool = delegate_tool
        self.store = delegation_store
        self.event_bus = event_bus
        self.traces_dir = traces_dir  # Path or None (defaults to ~/.sweave/traces)
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(
        self,
        agent: str,
        task: str,
        *,
        model: str | None = None,
        parent_session_id: str | None = None,
        project_name: str | None = None,
    ) -> Delegation:
        """Submit *task* to *agent*. Returns the freshly-created delegation.

        The delegation is in status ``queued``; the actual work happens on
        a background asyncio task. Use :meth:`wait` or poll the store /
        WebSocket for completion.
        """
        delegation = Delegation(
            agent=agent,
            model=model or "",
            task=task,
            parent_session_id=parent_session_id,
            project_name=project_name,
            status="queued",
        )
        await self.store.add(delegation)

        trace = TraceLog(delegation.delegation_id, base_dir=self.traces_dir)
        trace.append("status_changed", {"status": "queued", "agent": agent})

        await self._publish(
            "delegation.status_changed",
            {
                "delegation_id": delegation.delegation_id,
                "status": "queued",
                "agent": agent,
                "task_id": delegation.task_id,
            },
        )

        # Schedule the work
        task_obj = asyncio.create_task(
            self._run(delegation, trace),
            name=f"delegation-{delegation.delegation_id}",
        )
        self._tasks[delegation.delegation_id] = task_obj
        task_obj.add_done_callback(lambda t, did=delegation.delegation_id: self._tasks.pop(did, None))
        return delegation

    async def wait(self, delegation_id: str, timeout: float | None = None) -> Delegation | None:
        """Block until *delegation_id* reaches a terminal status, or timeout.

        Returns the final delegation record, or None if not found.
        """
        task = self._tasks.get(delegation_id)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except asyncio.TimeoutError:
                return self.store.get(delegation_id)
        return self.store.get(delegation_id)

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _run(self, delegation: Delegation, trace: TraceLog) -> None:
        """Background worker: drive the delegation through the state machine."""
        try:
            await self._transition(delegation, trace, "running", started_at=__import__("datetime").datetime.now())
            trace.append("prompt_sent", {"prompt": delegation.task, "agent": delegation.agent})

            # Call DelegateTaskTool. It is async, returns a DelegationResult
            # (a different ``DelegationResult`` from the runtime ``Delegation``).
            from sweave.tools import DelegationResult  # local import to avoid cycle

            result: DelegationResult = await self.delegate_tool.execute(
                agent=delegation.agent,
                task=delegation.task,
                model=delegation.model or None,
                task_id=delegation.task_id,
            )

            # Persist result + transition
            await self.store.update(
                delegation.delegation_id,
                output=result.output or "",
                error=result.error,
            )
            final_status = "done" if result.success else "failed"
            trace.append("output_chunk" if result.success else "error", {
                "output_len": len(result.output or ""),
                "agent": result.agent,
            })
            await self._transition(
                delegation,
                trace,
                final_status,
                completed_at=__import__("datetime").datetime.now(),
            )
        except asyncio.CancelledError:
            await self._transition(
                delegation, trace, "failed", error="cancelled",
                completed_at=__import__("datetime").datetime.now(),
            )
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("Delegation %s failed", delegation.delegation_id)
            await self.store.update(
                delegation.delegation_id, error=f"{type(e).__name__}: {e}"
            )
            await self._transition(
                delegation,
                trace,
                "failed",
                error=str(e),
                completed_at=__import__("datetime").datetime.now(),
            )
        finally:
            trace.close()

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    async def _transition(
        self,
        delegation: Delegation,
        trace: TraceLog,
        new_status: str,
        *,
        started_at: Any = None,
        completed_at: Any = None,
        error: str | None = None,
    ) -> None:
        updates: dict[str, Any] = {"status": new_status}
        if started_at is not None:
            updates["started_at"] = started_at
        if completed_at is not None:
            updates["completed_at"] = completed_at
        if error is not None:
            updates["error"] = error
        await self.store.update(delegation.delegation_id, **updates)
        trace.append("status_changed", {"status": new_status, "agent": delegation.agent})
        await self._publish(
            "delegation.status_changed",
            {
                "delegation_id": delegation.delegation_id,
                "status": new_status,
                "agent": delegation.agent,
                "task_id": delegation.task_id,
            },
        )

    async def _publish(self, event: str, data: dict[str, Any]) -> None:
        if self.event_bus is not None:
            await self.event_bus.publish(event, data)
