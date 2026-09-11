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
import time
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


#: Detail value returned by :meth:`JobRunner._bounded_turn` when the
#: human stops the turn at the soft-limit question (slice 3,
#: incident 2026-09-11). Callers map it to a ``turn_stopped_by_user``
#: error instead of the ``turn_timeout_exceeded_*`` text.
USER_STOPPED = "user_stopped"

#: Options on the soft-limit question (existing inline Question card
#: renders them as buttons; free text also maps: keep iff it starts
#: with "keep").
SOFT_LIMIT_OPTIONS = ["Keep waiting", "Stop it"]


def _is_soft_limit_record(rec: dict[str, Any] | None) -> bool:
    """True iff an escalation record is a soft-limit question."""
    if not rec:
        return False
    return bool((rec.get("metadata") or {}).get("soft_limit"))


def _soft_keep_answer(rec: dict[str, Any] | None) -> bool:
    """True iff a resolved soft-limit record says keep waiting."""
    if not rec or rec.get("status") != "answered":
        return False
    return str(rec.get("response", "") or "").strip().lower().startswith("keep")


class JobRunner:
    """Owns the delegation lifecycle for one process.

    Construct once in the FastAPI lifespan. The runner is event-loop-bound;
    the underlying ``DelegateTaskTool`` already exposes ``async`` methods,
    so the runner just calls them in background tasks.
    """

    # 30 min per turn (ruling 2026-09-10: the M1.3 15-min default killed
    # real agentic turns too early). Configurable via
    # ``routing.turn_timeout_s`` (sweave/config/schemas.py); hot-reloaded
    # through the ConfigManager reload callback wired in server.py.
    DEFAULT_TURN_TIMEOUT = 30 * 60

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
        kind: str = "task",
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

        M1.7 step 2:
        * ``kind`` is "task" (implementation delegation, the M1.1-M1.6
          default) or "chat" (orchestrator conversation turn; created
          by the chat loop). Additive; pre-M1.7 records carry no
          ``kind`` and load with "task".
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
            kind=kind,
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

    MAX_TURN_EXTENSIONS = 3
    BEACON_WINDOW_SECONDS = 300.0

    def _beacon_file(self, delegation_id: str) -> "Path | None":
        """The delegation's own trace file (beacon + turn activity)."""
        base = self.traces_dir or (
            Path.home() / ".sweave" / "traces"
        )
        p = Path(base) / f"{delegation_id}.jsonl"
        return p if p.exists() else None

    def _beacon_recent(self, delegation: Delegation, window: float) -> bool:
        """True iff the delegation's trace got a fresh defer beacon.

        opencode gives us NO streaming liveness for a running turn,
        but a `defer` call from THAT turn is an observable mid-turn
        heartbeat (submission writes `child_deferred` to the caller's
        trace — see the /api/v2/tasks beacon). Persistent-file mtime;
        no Delegation schema change."""
        p = self._beacon_file(delegation.delegation_id)
        if p is None:
            return False
        try:
            age = time.time() - p.stat().st_mtime
            return age <= window
        except OSError:
            return False

    async def _bounded_turn(
        self, coro, delegation: Delegation, trace: "TraceLog"
    ) -> "tuple[bool, str | object]":
        """Run one agent turn under ``turn_timeout`` with a shielded
        re-arm: a turn exceeding the cap is NOT silently killed —
        when a fresh defer beacon proves it alive, a full fresh
        budget arms again (bounded by ``MAX_TURN_EXTENSIONS``);
        only an unwitnessed cap fails loud.

        M1.12 fix (2026-09-10, user ruling "full budget re-armed"):
        while THIS delegation has a pending escalation (blocking
        human question, e.g. a ``permission`` ask), the countdown
        suspends — every expiry re-arms a full budget for as long
        as the question is open (unbounded: permission questions
        have no deadline). When a held question has resolved, one
        final full re-arm fires on the next expiry so the
        post-answer work never resumes on a sliver of leftover
        budget. Mirrors the ChatLoop suspension semantics on the
        child path (the M1.12 step-3 build covered chat turns
        only; the reviewer child ``020e3ebb8d1b`` was killed by
        the 900s bound with its question still pending)."""
        import asyncio as _aio

        task = _aio.ensure_future(coro)
        budget = float(self.turn_timeout or self.DEFAULT_TURN_TIMEOUT)
        extensions = 0
        holds = 0
        rearm = False
        # Soft total limit (slice 3): one keep/stop question per turn,
        # one keep-extension. ``soft_open`` marks the question asked;
        # ``soft_extended`` marks the keep consumed.
        soft_open = False
        soft_extended = False
        try:
            while True:
                try:
                    output = await _aio.wait_for(
                        _aio.shield(task), timeout=max(budget, 1.0)
                    )
                    return True, output
                except _aio.TimeoutError:
                    pending = await self._soft_record(delegation)
                    if pending is not None and pending.get("status") == "pending":
                        # A recorded question holds the turn (existing
                        # semantics). Soft-limit questions report their
                        # own reason so the trace shows who is being
                        # waited on.
                        holds += 1
                        rearm = True
                        budget = float(
                            self.turn_timeout or self.DEFAULT_TURN_TIMEOUT
                        )
                        trace.append(
                            "turn_extended",
                            {
                                "n": holds,
                                "budget": budget,
                                "reason": (
                                    "soft_limit_pending"
                                    if _is_soft_limit_record(pending)
                                    else "escalation_pending"
                                ),
                            },
                        )
                        continue
                    if soft_open:
                        # Our soft question resolved (or vanished) while
                        # we waited. Keep (once) or stop now — never ask
                        # twice in one turn.
                        rec = await self._soft_record(delegation)
                        if (
                            _soft_keep_answer(rec)
                            and not soft_extended
                        ):
                            soft_extended = True
                            budget = float(
                                self.turn_timeout or self.DEFAULT_TURN_TIMEOUT
                            )
                            trace.append(
                                "turn_soft_limit_extended",
                                {"budget": budget},
                            )
                            continue
                        stopped = (
                            rec is not None
                            and _is_soft_limit_record(rec)
                            and rec.get("status") in {"answered", "skipped"}
                            and not _soft_keep_answer(rec)
                        )
                        task.cancel()
                        await _aio.gather(task, return_exceptions=True)
                        if stopped:
                            trace.append(
                                "turn_soft_limit_stop", {"timeout": budget}
                            )
                            return False, USER_STOPPED
                        trace.append(
                            "turn_timeout",
                            {"timeout": budget, "extensions": extensions},
                        )
                        return False, None
                    if rearm:
                        # A non-soft question resolved mid-window: re-arm
                        # a FULL budget once so post-answer work is not
                        # capped by leftover time (user ruling).
                        rearm = False
                        budget = float(
                            self.turn_timeout or self.DEFAULT_TURN_TIMEOUT
                        )
                        trace.append(
                            "turn_extended",
                            {
                                "n": holds,
                                "budget": budget,
                                "reason": "escalation_resolved_rearm",
                            },
                        )
                        continue
                    if (
                        extensions < self.MAX_TURN_EXTENSIONS
                        and self._beacon_recent(
                            delegation, self.BEACON_WINDOW_SECONDS
                        )
                    ):
                        extensions += 1
                        budget = float(
                            self.turn_timeout or self.DEFAULT_TURN_TIMEOUT
                        )
                        trace.append(
                            "turn_extended",
                            {
                                "n": extensions,
                                "budget": budget,
                                "reason": "defer_beacon_recent",
                            },
                        )
                        continue
                    if await self._ask_soft_limit(delegation, trace, budget):
                        # First unwitnessed expiry: ask the human
                        # (keep/stop) instead of failing. The answer
                        # window re-arms a full budget; the outcome is
                        # consumed once, above.
                        soft_open = True
                        budget = float(
                            self.turn_timeout or self.DEFAULT_TURN_TIMEOUT
                        )
                        continue
                    task.cancel()
                    await _aio.gather(task, return_exceptions=True)
                    trace.append(
                        "turn_timeout", {"timeout": budget, "extensions": extensions}
                    )
                    return False, None
        except Exception:  # noqa: BLE001
            # Re-raise after cleanup so _run's outer handler sees it.
            if not task.done():
                task.cancel()
            raise

    async def _escalation_pending(self, delegation: Delegation) -> bool:
        """True when this delegation has a PENDING blocking question.

        Reads the SpecialistRuntime's escalation store (the same
        store the permission bridge + stall branch create records
        in). Best-effort: no runtime / no store / store error all
        mean "not held" — the bound then behaves exactly as before
        this fix.

        Kept for its test pin (test_m1_12_turn_hold); ``_bounded_turn``
        now reads via :meth:`_soft_record` (same store, record-level
        so the trace can name soft-limit holds).
        """
        store = getattr(
            getattr(self, "specialist_runtime", None),
            "escalation_store",
            None,
        )
        if store is None:
            return False
        try:
            rec = await store.get(delegation_id=delegation.delegation_id)
        except Exception:  # noqa: BLE001
            return False
        return bool(rec) and rec.get("status") == "pending"

    def _soft_store(self) -> Any | None:
        """The escalation store, if a runtime wires one in."""
        return getattr(
            getattr(self, "specialist_runtime", None),
            "escalation_store",
            None,
        )

    async def _soft_record(self, delegation: Delegation) -> dict[str, Any] | None:
        """This delegation's escalation record at any status (or None).

        Best-effort like :meth:`_escalation_pending`: no runtime /
        no store / store error all mean "no record".
        """
        store = self._soft_store()
        if store is None:
            return None
        try:
            rec = await store.get(delegation_id=delegation.delegation_id)
        except Exception:  # noqa: BLE001
            return None
        return rec if isinstance(rec, dict) else None

    async def _ask_soft_limit(
        self, delegation: Delegation, trace: "TraceLog", budget: float
    ) -> bool:
        """File the one-per-turn keep/stop question. False when there
        is no store to ask through (caller falls back to fail-fast).
        """
        store = self._soft_store()
        if store is None:
            return False
        question = (
            f"Specialist '{delegation.agent}' has been running "
            f"{budget:.0f}s with no new defer activity. "
            f"Keep waiting or stop it?"
        )
        try:
            await store.create(
                delegation_id=delegation.delegation_id,
                question=question,
                options=list(SOFT_LIMIT_OPTIONS),
                kind="question",
                audience="human",
                timeout_seconds=None,
                metadata={"soft_limit": True, "agent": delegation.agent},
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "JobRunner: soft-limit question create failed for %s",
                delegation.delegation_id,
                exc_info=True,
            )
            return False
        trace.append("turn_soft_limit_asked", {"budget": budget})
        return True

    async def _run(self, delegation: Delegation, trace: TraceLog) -> None:
        """Background worker: drive the delegation through the state machine.

        M1.3 step 4: the agent call is wrapped in ``asyncio.wait_for`` with
        ``self.turn_timeout`` (default 30 min, ruling 2026-09-10). On expiry the
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
            # turn timeout; on TimeoutError the beacons decide whether
            # the cap extends (M1.12 amendment 2) or the turn fails.
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
                ok, output = await self._bounded_turn(
                    self.specialist_runtime.run(
                        specialist=specialist,
                        delegation=delegation,
                        worktree_path=worktree_path,
                        message=delegation.task,
                        trace=trace,
                        model_ref=model_ref,
                    ),
                    delegation,
                    trace,
                )
                if not ok:
                    stopped = output == USER_STOPPED
                    await store.update(
                        delegation.delegation_id,
                        output="",
                        error=(
                            "turn_stopped_by_user"
                            if stopped
                            else f"turn_timeout_exceeded_{self.turn_timeout}s"
                        ),
                    )
                    trace.append("turn_timeout", {"timeout": self.turn_timeout})
                    await self._transition(delegation, store, trace, "failed",
                                           completed_at=datetime.now())
                    return
                # Persist the Specialist (the runtime set
                # specialist.session_id during run()). Best-effort:
                # a saver failure is logged, never raised -- the
                # delegation result stands on its own.
                #
                # Seed-scope views are NEVER persisted: writing one
                # would materialise a global/project shadow copy
                # that hides the seed via resolution shadowing
                # (2026-09-09: seed `backend-specialist` vanished
                # behind an auto-saved global of the same name).
                # Seed sessions are intentionally transient.
                if self.specialist_saver is not None and specialist.scope != "seed":
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
                elif specialist.scope == "seed":
                    trace.append(
                        "session_id_transient",
                        {
                            "specialist": specialist.name,
                            "reason": "seed-scope views are never persisted",
                        },
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

                ok, legacy_result = await self._bounded_turn(
                    self.delegate_tool.execute(
                        agent=delegation.agent,
                        task=delegation.task,
                        model=delegation.model or None,
                        task_id=delegation.task_id,
                    ),
                    delegation,
                    trace,
                )
                if not ok:
                    stopped = legacy_result == USER_STOPPED
                    result = type("R", (), {
                        "success": False, "output": "",
                        "error": (
                            "turn_stopped_by_user"
                            if stopped
                            else f"turn_timeout_exceeded_{self.turn_timeout}s"
                        ),
                        "agent": delegation.agent,
                    })()
                else:
                    result = legacy_result

            # Honest failure states (2026-09-10 ruling: failed children
            # must not masquerade as review). The SpecialistRuntime's
            # in-band error contract returns a wire death as a
            # "[chat error: ...]" string with no exception; the chat
            # loop knows that prefix, but a CHILD delegation arriving
            # through this runner was stored as output with success ->
            # review / error=None (the APIError only ever visible in
            # the trace file). Detect the sentinel HERE, at the single
            # convergence point of both agent paths, and convert it to
            # a truthful failed record: error text on the row, status
            # failed, empty output.
            _sentinel_output = (
                (result.output or "") if isinstance(result.output, str) else ""
            )
            if result.success and _sentinel_output.lstrip().startswith(
                "[chat error:"
            ):
                wire_error = _sentinel_output.strip()
                result = DelegationResult(
                    success=False,
                    agent=delegation.agent,
                    task_id=delegation.task_id,
                    output="",
                    error=wire_error,
                )
                trace.append(
                    "wire_death_recorded",
                    {"error": wire_error},
                )

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
