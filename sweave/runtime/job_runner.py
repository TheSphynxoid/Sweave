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
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.specialist_store import Specialist
    from sweave.web.events import WSEventBus

logger = logging.getLogger(__name__)


class JobRunner:
    """Owns the delegation lifecycle for one process.

    Construct once in the FastAPI lifespan. The runner is event-loop-bound;
    the underlying ``DelegateTaskTool`` already exposes ``async`` methods,
    so the runner just calls them in background tasks.
    """

    DEFAULT_TURN_TIMEOUT = 15 * 60  # 15 min per the M1.3 plan

    def __init__(
        self,
        delegate_tool: "DelegateTaskTool",
        delegation_stores: PerProjectDelegationStores,
        event_bus: "WSEventBus | None" = None,
        traces_dir: Any = None,
        project_dir_resolver: Callable[[str | None], Path | None] | None = None,
        child_session_adder: Callable[[Any], None] | None = None,
        specialist_runtime: "SpecialistRuntime | None" = None,
        specialist_factory: Callable[[str], "Specialist | None"] | None = None,
        turn_timeout: float | None = None,
        specialist_saver: "Callable[[Specialist, str | None], None] | None" = None,
        delegation_manager: "DelegationManager | None" = None,
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
        # Children tab keeps rendering. The AppState itself supplies a
        # closure that knows about Session + project_manager; the
        # runner stays domain-agnostic.
        self.child_session_adder = child_session_adder
        # M1.3 step 3: when a SpecialistRuntime is wired in, the runner
        # delegates to it for the actual work (per-specialist
        # ServeRunner, session resume, ModelRef routing, worktree
        # re-injection). When absent, the runner falls back to the
        # legacy ``delegate_tool.execute`` path so existing tests +
        # non-runtime callers keep working.
        self.specialist_runtime = specialist_runtime
        # Resolves an agent name to a Specialist record (M1.2 store,
        # project→global→seed). The AppState supplies a closure that
        # calls ``ensure_specialist_resolver().resolve(name, project_dir)``.
        # When None, the runtime path is bypassed and the legacy
        # delegate_tool path runs.
        self.specialist_factory = specialist_factory
        # M1.3 step 4: per-turn timeout. The agent's streaming response
        # is wrapped in ``asyncio.wait_for(self.turn_timeout, ...)``; on
        # expiry the delegation is marked failed with an explicit
        # error and the serve is recycled on next use. M1.3 step 4.
        self.turn_timeout = turn_timeout if turn_timeout is not None else self.DEFAULT_TURN_TIMEOUT
        # M1.3 step 5 (live-gate fix): persists the Specialist record
        # (including the session_id the runtime set during run()) back
        # to its store. Signature: (specialist, project_name) -> None.
        # The AppState supplies a closure over the SpecialistResolver;
        # best-effort (failures logged, never raised). Without this,
        # the session_id only lives in the in-memory Specialist object
        # and is lost on restart -- contradicting the opencode.db
        # session-persistence contract (M1.3 post-step-0 amendment).
        self.specialist_saver = specialist_saver
        # M1.6 step 2: per-process DelegationManager. On terminal
        # status, we call ``record_terminal`` to free the per-chain
        # cache (so a new defer on the same chain can pick a new
        # target) and (for the root) drop the cache entirely (chain
        # is over). Best-effort: failures are logged, never raised --
        # the delegation result stands on its own.
        self.delegation_manager = delegation_manager
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
        depth: int = 0,
        chain_root_id: str | None = None,
        coordination_tokens: int = 0,
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

        M1.6 step 2:
        * ``depth`` is the chain depth (0 for the orchestrator's own
          delegation, +1 per defer; max ``routing.max_depth``).
        * ``chain_root_id`` is the root of the deferral chain (or None
          for top-level user tasks; these don't participate in
          chain caches).
        * ``coordination_tokens`` is the tiktoken estimate of this
          delegation's coordination traffic (orchestrator turn + defer
          payload + result summaries; specialist internal work is
          *not* counted by design).
        """
        delegation = Delegation(
            agent=agent,
            model=model or "",
            task=task,
            parent_session_id=parent_session_id,
            project_name=project_name,
            parent_task_id=parent_task_id,
            manifest=manifest,
            depth=depth,
            chain_root_id=chain_root_id,
            coordination_tokens=coordination_tokens,
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
        """Background worker: drive the delegation through the state machine.

        M1.3 step 4: the agent call is wrapped in ``asyncio.wait_for`` with
        ``self.turn_timeout`` (default 15 min, M1.3 plan). On expiry the
        delegation is marked failed with an explicit error; the
        ServeRunner is recycled on next use (the runner's
        ``runners.get_or_create`` checks ``is_alive`` before reusing).
        Heartbeat / output-staleness detection is deferred to R6.
        """
        store = await self._store_for(delegation)
        try:
            await self._transition(delegation, store, trace, "running", started_at=datetime.now())
            trace.append("prompt_sent", {"prompt": delegation.task, "agent": delegation.agent})

            # The actual agent call. Either path is wrapped in the
            # turn timeout; on TimeoutError we mark the delegation
            # failed and the runner (next use) will recycle the serve.
            try:
                if (
                    self.specialist_runtime is not None
                    and self.specialist_factory is not None
                    and self.project_dir_resolver is not None
                ):
                    worktree_path = self.project_dir_resolver(delegation.project_name)
                    if worktree_path is None:
                        worktree_path = Path.home() / ".sweave"
                    specialist = self.specialist_factory(delegation.agent)
                    if specialist is None:
                        from sweave.runtime.specialist_store import Specialist as _Spec

                        specialist = _Spec(
                            name=delegation.agent,
                            scope="project" if delegation.project_name else "global",
                            is_orchestrator=False,
                            system_prompt="",
                            harness="opencode",
                            current_model=delegation.model or None,
                        )
                    from sweave.runtime.specialist_store import ModelRef, parse_model_ref

                    model_ref: ModelRef | None = None
                    if delegation.model:
                        model_ref = parse_model_ref(delegation.model)
                    output = await asyncio.wait_for(
                        self.specialist_runtime.run(
                            specialist=specialist,
                            delegation=delegation,
                            worktree_path=worktree_path,
                            message=delegation.task,
                            trace=trace,
                            model_ref=model_ref,
                        ),
                        timeout=self.turn_timeout,
                    )
                    # Persist the Specialist (the runtime set
                    # specialist.session_id during run()). Best-effort:
                    # a saver failure is logged, never raised -- the
                    # delegation result stands on its own.
                    if self.specialist_saver is not None:
                        try:
                            self.specialist_saver(specialist, delegation.project_name)
                            trace.append(
                                "session_id_persisted",
                                {
                                    "specialist": specialist.name,
                                    "session_id": specialist.session_id,
                                },
                            )
                        except Exception as saver_err:  # noqa: BLE001
                            logger.warning(
                                "JobRunner: specialist_saver failed for %s: %s",
                                specialist.name, saver_err,
                            )
                    from sweave.tools import DelegationResult

                    result = DelegationResult(
                        success=True,
                        agent=delegation.agent,
                        task_id=delegation.task_id,
                        output=output,
                        error=None,
                    )
                else:
                    # Legacy path: wrap the call in wait_for directly.
                    # delegate_tool.execute is async (returns a
                    # coroutine), so wait_for times the call. The
                    # returned DelegationResult becomes the value of
                    # the await expression.
                    from sweave.tools import DelegationResult

                    result: DelegationResult = await asyncio.wait_for(
                        self.delegate_tool.execute(
                            agent=delegation.agent,
                            task=delegation.task,
                            model=delegation.model or None,
                            task_id=delegation.task_id,
                        ),
                        timeout=self.turn_timeout,
                    )
            except asyncio.TimeoutError:
                trace.append("turn_timeout", {"timeout": self.turn_timeout})
                result = type("R", (), {"success": False, "output": "",
                                          "error": f"turn_timeout_exceeded_{self.turn_timeout}s",
                                          "agent": delegation.agent})()

            # Persist result + transition.
            await store.update(
                delegation.delegation_id,
                output=result.output or "",
                error=result.error,
            )
            # M1.3 step 4: on stream success the delegation enters
            # 'review' (not 'done') -- human / cross-review promotes to
            # 'done' in M1.4. Failure modes (turn timeout, error from
            # the agent) still go straight to 'failed'.
            #
            # M1.6 step 3: parent gating. A delegation with children
            # (any other delegation whose parent_task_id points at
            # it) cannot leave the running-equivalent state until
            # ALL its children reach a terminal state. The gate
            # blocks the transition; once the children settle, the
            # parent advances to the same outcome it would have hit
            # without children (review on success, failed on
            # failure). This is a non-blocking poll: we sleep a
            # short interval and re-check; the per-turn timeout
            # bounds the wait so a misbehaving child can't wedge
            # the parent forever.
            if result.success:
                final_status = "review"
            else:
                final_status = "failed"

            # Parent gating: if this delegation is a parent (i.e. some
            # other delegation's parent_task_id == this delegation's id),
            # wait for all children to reach a terminal state. The
            # wait is bounded by self.turn_timeout so a wedged child
            # can't stall the parent forever.
            if final_status != "failed":
                await self._wait_for_children(
                    delegation, store, trace
                )
            trace.append(
                "output_chunk" if result.success else "error",
                {
                    "output_len": len(result.output or ""),
                    "agent": result.agent,
                },
            )
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

    async def _wait_for_children(
        self, delegation: Delegation, store: Any, trace: TraceLog
    ) -> None:
        """M1.6 step 3: parent gating.

        Wait until every delegation whose ``parent_task_id`` equals
        this delegation's id has reached a terminal state (done or
        failed). The wait is bounded by ``self.turn_timeout`` (the
        same cap as the agent turn) so a stuck child can't wedge the
        parent forever -- if the timeout hits we proceed and the
        parent transitions normally; the late-arriving child is
        silently absorbed (the parent's record is the audit
        source-of-truth for the chain).

        ``store`` is the per-project store that owns the parent's
        record. We use the same store's ``list()`` to enumerate
        children, then re-read the latest status. The polling
        interval is 250ms (responsive enough for the UI without
        hammering the disk).
        """
        deadline = asyncio.get_running_loop().time() + self.turn_timeout
        poll_interval = 0.25
        children_found = False
        while True:
            all_records = store.list()
            children = [r for r in all_records if r.parent_task_id == delegation.delegation_id]
            if children:
                children_found = True
            if not children_found:
                # No children ever existed (e.g. a leaf delegation).
                # No gate needed.
                return
            if all(r.status in {"done", "failed"} for r in children):
                # All children terminal -- parent can advance.
                trace.append(
                    "children_settled",
                    {
                        "parent": delegation.delegation_id,
                        "count": len(children),
                        "done": sum(1 for r in children if r.status == "done"),
                        "failed": sum(1 for r in children if r.status == "failed"),
                    },
                )
                return
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                trace.append(
                    "children_settle_timeout",
                    {
                        "parent": delegation.delegation_id,
                        "count": len(children),
                        "timeout": self.turn_timeout,
                    },
                )
                logger.warning(
                    "JobRunner: parent %s children-settle timeout after %ss",
                    delegation.delegation_id,
                    self.turn_timeout,
                )
                return
            await asyncio.sleep(poll_interval)

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
        # M1.6 step 2: notify the DelegationManager of a terminal
        # transition so the per-chain caches free up. Best-effort;
        # the cache is per-process, so a missed call only affects the
        # current process's view of the chain (the persisted record
        # is the source of truth and ``rebuild_chain_state`` recovers
        # on next defer in the same process).
        if new_status in {"done", "failed"} and self.delegation_manager is not None:
            try:
                # Use the post-update status so the manager's view
                # matches the persisted record.
                delegation.status = new_status
                self.delegation_manager.record_terminal(delegation)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "JobRunner: delegation_manager.record_terminal failed for %s",
                    delegation.delegation_id,
                    exc_info=True,
                )

    async def _publish(self, event: str, data: dict[str, Any]) -> None:
        if self.event_bus is not None:
            await self.event_bus.publish(event, data)
