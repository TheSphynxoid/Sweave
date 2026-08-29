"""In-process asyncio job runner for delegations.

The :class:`JobRunner` is the queue. There is no broker: each call to
:meth:`submit` creates a :class:`~sweave.runtime.delegation_store.Delegation`
record (the durable artefact), then schedules the actual delegation on the
event loop. Multiple delegations can be in flight at once; the runner
doesn't enforce a specialist pool (that's M1.3's job).

M1.1 step 2: the runner no longer holds a single ``DelegationStore``; it
holds a :class:`PerProjectDelegationStores` registry. Each delegation
is written to the store for its project (resolved via the injected
``project_dir_resolver`` callable). The runner doesn't import the
``ProjectManager`` directly — that's the AppState's job — so the
dependency stays one-way (runner → state).

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
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
)
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
        delegation_stores: PerProjectDelegationStores,
        event_bus: "WSEventBus | None" = None,
        traces_dir: Any = None,
        project_dir_resolver: Callable[[str | None], Path | None] | None = None,
        child_session_adder: Callable[[Any], None] | None = None,
    ) -> None:
        self.delegate_tool = delegate_tool
        self.stores = delegation_stores
        self.event_bus = event_bus
        self.traces_dir = traces_dir  # Path or None (defaults to ~/.sweave/traces)
        # Resolves a project name (or None) to a filesystem path. The
        # AppState supplies a closure over the ProjectManager. When the
        # resolver returns None (e.g. unknown project), the delegation
        # is recorded in the "global" project — a per-process store
        # rooted at ~/.sweave — so it is never lost, just un-scoped.
        self.project_dir_resolver = project_dir_resolver
        # M1.1 step 4: UI v1 compat bridge. On every delegation submit
        # we add a ChildSession entry to the parent session so the
        # Children tab keeps rendering. The AppState supplies a closure
        # that knows about Session + project_manager; the runner
        # itself stays domain-agnostic.
        self.child_session_adder = child_session_adder
        self._tasks: dict[str, asyncio.Task] = {}

    async def _store_for(self, delegation: Delegation) -> Any:
        """Return the :class:`DelegationStore` for *delegation*'s project."""
        if self.project_dir_resolver is not None:
            project_dir = self.project_dir_resolver(delegation.project_name)
        else:
            project_dir = None
        if project_dir is None:
            # No project context (or unknown project name): pin to the
            # global store at ~/.sweave/delegations-global.json so the
            # record is never lost.
            project_dir = Path.home() / ".sweave"
        return await self.stores.for_project(project_dir)

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
        manifest: Any = None,
        parent_task_id: str | None = None,
    ) -> Delegation:
        """Submit *task* to *agent*. Returns the freshly-created delegation.

        The delegation is in status ``queued``; the actual work happens on
        a background asyncio task. Use :meth:`wait` or poll the store /
        WebSocket for completion.

        M1.1 step 4:
        * ``parent_task_id`` plumbs the deferral chain (M1.6 will use
          this to detect loops). None means orchestrator-initiated.
        * ``manifest`` is a self-report the specialist fills in via
          prompt convention. Stored as-is; M1.1 does not generate.
        * A ``ChildSession`` entry is written to the parent session
          (bridge) so the UI v1 Children tab keeps rendering.
        """
        delegation = Delegation(
            agent=agent,
            model=model or "",
            task=task,
            parent_session_id=parent_session_id,
            project_name=project_name,
            parent_task_id=parent_task_id,
            manifest=manifest,
            status="queued",
        )
        store = await self._store_for(delegation)
        await store.add(delegation)

        # UI v1 compat bridge: write a ChildSession entry into the
        # parent session so the Children tab keeps rendering without
        # any UI change. The bridge write is best-effort: if the parent
        # session can't be resolved (test fixtures, edge cases) the
        # delegation still stands on its own.
        if self.child_session_adder is not None and parent_session_id:
            try:
                from sweave.projects import ChildSession

                self.child_session_adder(
                    ChildSession(
                        id=delegation.task_id,  # match v1 child id shape
                        parent_session_id=parent_session_id,
                        agent_name=agent,
                        task=task,
                        worktree_path=None,  # filled by the runtime when set
                        status="running",
                        delegation_id=delegation.delegation_id,
                    )
                )
            except Exception as e:  # noqa: BLE001
                # Never let a bridge failure kill a delegation.
                logger.warning(
                    "JobRunner: child-session bridge write failed for %s: %s",
                    delegation.delegation_id, e,
                )

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

        First waits on the in-process task (if any), then falls back to a
        cross-store lookup. The cross-store scan walks this runner's
        known stores AND, as a last resort, the project_dir_resolver for
        any project name we know about via the active project. The
        resolver covers the case where a *different* runner (or a
        freshly-restarted process) is asking about a delegation filed by
        a previous process — its per-project store will be re-loaded
        from disk on first access.
        """
        task = self._tasks.get(delegation_id)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except asyncio.TimeoutError:
                return await self._find(delegation_id)
        return await self._find(delegation_id)

    async def _find(self, delegation_id: str) -> Delegation | None:
        """Look up a delegation across all known per-project stores.

        Walks this runner's known stores, then falls through to
        per-project stores created on demand from the
        ``project_dir_resolver`` (e.g. a process that just restarted
        and needs to recover state from disk).
        """
        for store in self.stores.known_projects_stores():
            rec = store.get(delegation_id)
            if rec is not None:
                return rec
        # Last-resort: try resolving common project names against the
        # resolver. The first runner pinned the record under a specific
        # project; if this runner is fresh, the resolver can still
        # materialise the right store from disk. We don't enumerate all
        # project names here (the AppState would have to expose them);
        # the fast path above covers the same-process case.
        return None

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _run(self, delegation: Delegation, trace: TraceLog) -> None:
        """Background worker: drive the delegation through the state machine."""
        store = await self._store_for(delegation)
        try:
            await self._transition(delegation, store, trace, "running", started_at=datetime.now())
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
            await store.update(
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
                store,
                trace,
                final_status,
                completed_at=datetime.now(),
            )
        except asyncio.CancelledError:
            await self._transition(
                delegation, store, trace, "failed", error="cancelled",
                completed_at=datetime.now(),
            )
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("Delegation %s failed", delegation.delegation_id)
            await store.update(
                delegation.delegation_id, error=f"{type(e).__name__}: {e}"
            )
            await self._transition(
                delegation,
                store,
                trace,
                "failed",
                error=str(e),
                completed_at=datetime.now(),
            )
        finally:
            trace.close()

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    async def _transition(
        self,
        delegation: Delegation,
        store: Any,
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
        await store.update(delegation.delegation_id, **updates)
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
