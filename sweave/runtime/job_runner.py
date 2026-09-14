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
    CANCELLED_BY_USER_ERROR,
    Delegation,
    Estimate,
    PerProjectDelegationStores,
    in_join_set,
    is_join_settled,
)
from sweave.runtime.review_bundle import (
    build_review_bundle,
    write_bundle_artifact,
)
from sweave.runtime.trace_log import TraceLog

if TYPE_CHECKING:
    from sweave.tools import DelegateTaskTool
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.specialist_store import Specialist
    from sweave.web.events import WSEventBus

logger = logging.getLogger(__name__)


#: Reviewer-role hint attached to every review-request (M2.1). The
#: orchestrator resolves the request explicitly via
#: ``defer(target=reviewer)``; the constant names the role the
#: reviewer pool is known by (M2.3 may refine this per project).
REVIEWER_HINT = "reviewer"


def build_review_request(delegation: Delegation) -> dict[str, Any]:
    """Build the M2.1 review-request record for a finished delegation.

    Pure function (producer seam, tested directly): reviewer hint is
    the ``reviewer`` role; the diff pointer comes from the
    delegation's own ``worktree_path``/``branch``/``pr_url``; the
    manifest summary + confidence ride along when the finishing
    specialist reported them. No verdict payload (ruling 4 — M2.2
    owns the contract + conformance check).
    """
    manifest = delegation.manifest
    manifest = manifest if isinstance(manifest, dict) else {}
    return {
        "reviewer_hint": REVIEWER_HINT,
        "diff_ref": {
            "worktree_path": delegation.worktree_path,
            "branch": delegation.branch,
            "pr_url": delegation.pr_url,
        },
        "manifest_summary": manifest.get("intent"),
        "confidence": manifest.get("confidence"),
        "requested_at": datetime.now().isoformat(),
    }


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
        specialist_factory: "Callable[[str, str | None], Specialist | None] | None" = None,
        turn_timeout: float | None = None,
        specialist_saver: "Callable[[Specialist, str | None], None] | None" = None,
        delegation_manager: "DelegationManager | None" = None,
        # Step 4: resolves a project name to its human-declared
        # permission roots (for the engine-turn permission map).
        # The AppState supplies a closure over the ProjectManager,
        # mirroring project_dir_resolver. None = engine turns render
        # the map without user roots (fail-safe: ask, never allow).
        permission_roots_resolver: Callable[[str | None], Any | None] | None = None,
        # Two-file config ruling: resolves a project name to its
        # EFFECTIVE config (global + project overlay file). Drives the
        # per-delegation turn budget + project harness tier below.
        # None = global singletons (legacy/tests).
        project_config_resolver: Callable[[str | None], Any | None] | None = None,
        # Worktree isolation (DESIGN principle #2): resolves a project
        # name to its worktree-base override (or None). The effective
        # base is the override when absolute, the override anchored at
        # the project dir when relative, else {project}/.worktrees.
        # The AppState supplies a closure over the ProjectManager +
        # global config. None = every project uses .worktrees.
        worktree_base_resolver: Callable[[str | None], str | None] | None = None,
        # Test seam for worktree lifecycle: (base, git_dir) ->
        # manager with async_create_worktree / async_remove_worktree.
        # None = the real WorktreeManager (git CLI).
        worktree_manager_factory: Callable[[str, Path], Any] | None = None,
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
        # project to global to seed). Signature (agent_name,
        # project_name): the factory MUST resolve against the
        # delegation's own project, never the UI-focused active
        # project (two-file config ruling: task scope, not focus
        # scope). The AppState supplies a closure over the resolver
        # + ProjectManager.
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
        self.permission_roots_resolver = permission_roots_resolver
        self.project_config_resolver = project_config_resolver
        self.worktree_base_resolver = worktree_base_resolver
        self.worktree_manager_factory = worktree_manager_factory
        # Step 4: transient per-task harness overrides
        # (submit(harness=...) -> _run pops). In-memory only: a
        # restart mid-flight loses the override and the recovered
        # turn resolves via the specialist record (documented, never
        # persisted — no Delegation schema change).
        self._harness_overrides: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        # User-cancel ownership (Stop button): ids being cancelled by
        # cancel_subtree. The _run CancelledError branch skips its own
        # transition for these (the subtree pass is the single
        # writer, so the error text + events stay uniform).
        self._user_cancelled: set[str] = set()

    async def _store_for(self, delegation: Delegation) -> Any:
        """Return the :class:`DelegationStore` for *delegation*'s project."""
        return await self.stores.for_project(self._project_dir_for(delegation))

    def _project_dir_for(self, delegation: Delegation) -> Path:
        """Resolve the project dir for *delegation* (same fallback as
        the store: unknown project pins to the global
        ``~/.sweave`` dir so the record — and the review artifact —
        is never lost)."""
        project_dir: Path | None = None
        if self.project_dir_resolver is not None:
            project_dir = self.project_dir_resolver(delegation.project_name)
        if project_dir is None:
            # No project context (or unknown project name): pin to the
            # global store at ~/.sweave/delegations-global.json so the
            # record is never lost.
            project_dir = Path.home() / ".sweave"
        return project_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _effective_config_for(self, delegation: Delegation) -> Any | None:
        """Effective config for delegation's own project (two-file
        ruling: global + project overlay), or None when no resolver
        is wired (legacy/tests keep the global singletons)."""
        if self.project_config_resolver is None:
            return None
        try:
            return self.project_config_resolver(delegation.project_name)
        except Exception:  # noqa: BLE001
            logger.warning(
                "JobRunner: project config resolve failed for %s",
                delegation.project_name,
            )
            return None

    def _turn_budget_raw_for(self, delegation: Delegation) -> Any:
        """Raw budget value: the project overlay routing timeout when
        present and positive, else the runner turn_timeout VERBATIM
        (type preserved: legacy pins like 900s and trace payloads must
        never change shape for the non-overlay path)."""
        effective = self._effective_config_for(delegation)
        try:
            if effective is not None:
                value = effective.routing.turn_timeout_s
                if value and float(value) > 0:
                    return value
        except Exception:  # noqa: BLE001
            pass
        return self.turn_timeout

    def _turn_budget_for(self, delegation: Delegation) -> float:
        """Per-delegation turn budget in seconds for the wait math."""
        try:
            return float(
                self._turn_budget_raw_for(delegation)
                or self.DEFAULT_TURN_TIMEOUT
            )
        except Exception:  # noqa: BLE001
            return float(self.DEFAULT_TURN_TIMEOUT)

    def _turn_budget_label_for(self, delegation: Delegation) -> str:
        """Display form for turn_timeout_exceeded_* (900, never 900.0)."""
        raw = self._turn_budget_raw_for(delegation)
        return f"{raw:g}" if isinstance(raw, float) else str(raw)

    def _project_harness_for(self, delegation: Delegation) -> str | None:
        """Project overlay ``harness.default`` for the harness tier."""
        effective = self._effective_config_for(delegation)
        try:
            if effective is not None:
                value = (effective.harness.default or "").strip()
                return value or None
        except Exception:  # noqa: BLE001
            pass
        return None

    def _worktree_manager_for(
        self, project_dir: Path, project_name: str | None
    ) -> Any:
        """WorktreeManager scoped to one project (base + git dir).

        Effective base: the project override when absolute, the
        override anchored at the project dir when relative, else
        ``{project}/.worktrees`` (the permission map's built-in
        assumption). The git dir is always the project dir, so
        creation/removal run in the right repo whatever the process
        CWD is. Uses the injected factory in tests, else the real
        WorktreeManager.
        """
        base_raw: str | None = None
        if self.worktree_base_resolver is not None:
            try:
                base_raw = self.worktree_base_resolver(project_name)
            except Exception:  # noqa: BLE001
                base_raw = None
        if base_raw:
            base = Path(base_raw)
            if not base.is_absolute():
                base = project_dir / base
        else:
            base = project_dir / ".worktrees"
        base = base.resolve()
        base.mkdir(parents=True, exist_ok=True)
        if self.worktree_manager_factory is not None:
            return self.worktree_manager_factory(str(base), project_dir)
        from sweave.workspace.manager import WorktreeManager

        return WorktreeManager(str(base), git_dir=str(project_dir))

    async def _remove_task_worktree(
        self, delegation: Delegation, trace: "TraceLog"
    ) -> None:
        """Best-effort removal of the task worktree at settle.

        Runs on ``done``/``failed`` (centralized in :meth:`_transition`,
        so normal, timeout and cancel paths all converge here). The
        branch is KEPT (review forensics + future PR flow); ``review``
        keeps its tree (humans may still inspect). Chat turns never
        own trees. Only the creator retires a tree: an ``inherit``
        child or treeless run records ``worktree_owned=False`` and is
        skipped here — removing a shared tree would pull the worktree
        out from under its owner. Never raises.
        """
        try:
            if delegation.kind == "chat":
                return
            if getattr(delegation, "worktree_owned", True) is False:
                try:
                    trace.append(
                        "worktree_not_owned",
                        {
                            "worktree": str(
                                getattr(delegation, "worktree_path", None)
                            ),
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
                return
            tree = getattr(delegation, "worktree_path", None)
            if not tree:
                return
            project_dir = self._project_dir_for(delegation)
            manager = self._worktree_manager_for(
                project_dir, delegation.project_name
            )
            removed = await manager.async_remove_worktree(
                delegation.task_id, delegation.agent
            )
            try:
                trace.append(
                    "worktree_removed",
                    {
                        "worktree": str(tree),
                        "branch": getattr(delegation, "branch", None),
                        "branch_kept": True,
                        "removed": bool(removed),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "JobRunner: worktree removal failed for %s: %s",
                delegation.delegation_id, exc,
            )

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
        estimate: Estimate | None = None,
        blocking: bool = False,
        # Step 4: per-task harness override (transient — see
        # _harness_overrides; validated by the caller when it comes
        # from HTTP).
        harness: str | None = None,
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

        M2.0:
        * ``estimate`` is the caller-supplied ``{tokens, seconds}``
          (or None). Record only — nothing reads it for decisions in
          M2.0; the estimate-vs-actual projection joins it against the
          trace. Chat turns never carry one (non-goal).

        M2.1:
        * ``blocking`` is the wait-set opt-in (default False —
          fire-and-forget). True puts the child in the synthesis join
          set (ChatLoop + parent gate wait on it). Task delegations
          only; chat turns never carry one.
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
            estimate=estimate,
            blocking=blocking,
            status="queued",
        )
        store = await self._store_for(delegation)
        await store.add(delegation)
        if harness:
            self._harness_overrides[delegation.delegation_id] = harness

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

    # Canonical user-cancel error (Stop button). Distinct from the
    # crash-recovery "interrupted by ..." text so forensics can tell
    # a deliberate stop from a server death; the status stays
    # "failed" (the closed VALID_STATUSES set is untouched — no
    # schema, join, or recovery change required).
    USER_CANCEL_ERROR = CANCELLED_BY_USER_ERROR

    # Live statuses a user-cancel may claim. ``review`` is excluded
    # on purpose: it is user-facing promotion state (a human owns
    # the next move), not in-flight work.
    CANCELABLE_STATUSES = frozenset({"queued", "running"})

    async def cancel_subtree(
        self, root_delegation_id: str, reason: str = USER_CANCEL_ERROR
    ) -> list[str]:
        """Cancel a delegation and its live descendants (user Stop).

        Collects the root + every ``queued``/``running`` descendant
        (by ``parent_task_id`` walk across known stores), best-effort
        aborts their engine turns, cancels their asyncio tasks, and
        transitions each to ``failed`` with the cancel error (single
        writer: the tasks' own CancelledError branches stand down
        via ``_user_cancelled``). Pending escalations are NOT
        resolved here — the caller (ChatLoop, which owns the
        EscalationStore) skips them so attention flags clear.

        Returns the cancelled delegation ids (root first). Unknown
        or already-terminal roots return [].
        """
        # 1) Collect the live subtree across known stores.
        hits: list[tuple[Any, Any]] = []  # (store, record)
        seen: set[str] = set()
        queue = [root_delegation_id]
        while queue:
            did = queue.pop(0)
            if did in seen:
                continue
            seen.add(did)
            for store in self.stores.known_projects_stores():
                rec = store.get(did)
                if rec is None:
                    continue
                if rec.status in self.CANCELABLE_STATUSES:
                    hits.append((store, rec))
                # Descend regardless of the parent's own status (a
                # terminal parent may still own live children).
                for child in store.list():
                    if child.parent_task_id == did and child.delegation_id not in seen:
                        queue.append(child.delegation_id)
                break
        if not hits:
            return []
        ids = [rec.delegation_id for _, rec in hits]
        self._user_cancelled.update(ids)
        try:
            # 2) Best-effort engine abort per live engine session.
            try:
                from sweave.harness.engine import abort_engine_session
            except Exception:  # noqa: BLE001
                abort_engine_session = None  # type: ignore[assignment]
            if abort_engine_session is not None:
                for _, rec in hits:
                    try:
                        await abort_engine_session(
                            getattr(rec, "engine_session_id", None)
                        )
                    except Exception:  # noqa: BLE001
                        continue
            # 3) Cancel the driving tasks; their CancelledError
            # branches stand down (single writer: step 4 below).
            live_tasks = [
                t for did in ids
                if (t := self._tasks.get(did)) is not None and not t.done()
            ]
            for t in live_tasks:
                t.cancel()
            if live_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*live_tasks, return_exceptions=True),
                        timeout=10.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "JobRunner: %d task(s) did not settle after cancel of %s",
                        len(live_tasks), root_delegation_id,
                    )
            # 4) Transition whatever is still live (a settled task's
            # branch already stood down; a taskless record — e.g. a
            # queued delegation whose worker never started — lands
            # here directly).
            cancelled: list[str] = []
            for store, rec in hits:
                try:
                    fresh = store.get(rec.delegation_id)
                except Exception:  # noqa: BLE001
                    fresh = None
                if fresh is not None and fresh.status not in self.CANCELABLE_STATUSES:
                    # Task branch already terminal (or raced us):
                    # normalise the error text to the canonical one.
                    if fresh.status == "failed" and fresh.error == "cancelled":
                        try:
                            await store.update(rec.delegation_id, error=reason)
                        except Exception:  # noqa: BLE001
                            pass
                    cancelled.append(rec.delegation_id)
                    continue
                trace = TraceLog(rec.delegation_id, base_dir=self.traces_dir)
                try:
                    trace.append(
                        "turn_cancelled",
                        {"by": "user", "agent": rec.agent},
                    )
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await self._transition(
                        rec,
                        store,
                        trace,
                        "failed",
                        completed_at=datetime.now(),
                        error=reason,
                    )
                except Exception as transition_err:  # noqa: BLE001
                    logger.warning(
                        "JobRunner: cancel transition failed for %s: %s",
                        rec.delegation_id, transition_err,
                    )
                    continue
                cancelled.append(rec.delegation_id)
            # Root first for the caller's convenience.
            cancelled.sort(key=lambda did: (did != root_delegation_id, did))
            return cancelled
        finally:
            self._user_cancelled.difference_update(ids)

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
        self,
        coro,
        delegation: Delegation,
        trace: "TraceLog",
        budget_override: float | None = None,
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
        # Two-file config ruling: the caller passes the delegation's
        # own project budget; without it the runner singleton applies.
        base_budget = float(
            budget_override
            if budget_override
            else (self.turn_timeout or self.DEFAULT_TURN_TIMEOUT)
        )
        budget = base_budget
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
                        budget = base_budget
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
                            budget = base_budget
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
                        budget = base_budget
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
                        budget = base_budget
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
                        budget = base_budget
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
                project_dir = self.project_dir_resolver(delegation.project_name)
                if project_dir is None:
                    project_dir = Path.home() / ".sweave"
                worktree_path = project_dir
                # Task scope, not focus scope: resolve against the
                # delegation's own project (two-file config ruling).
                # Resolved BEFORE the tree so the specialist's
                # worktree policy (a per-specialist user toggle, never
                # an LLM parameter) steers isolation per task.
                specialist = self.specialist_factory(
                    delegation.agent, delegation.project_name
                )
                if specialist is None:
                    from sweave.runtime.specialist_store import Specialist as _Spec

                    specialist = _Spec(
                        name=delegation.agent,
                        scope="project" if delegation.project_name else "global",
                        is_orchestrator=False,
                        system_prompt="",
                        harness="sweave-engine",
                        current_model=delegation.model or None,
                    )
                # Worktree isolation (DESIGN principle #2) with a
                # per-specialist policy: ``isolated`` runs in its own
                # git worktree + branch (sweave/{task}/{agent}), never
                # in the live project tree; ``inherit`` runs in the
                # parent delegation's tree (reviewers — project root
                # when the parent has no tree); ``none`` runs in the
                # project root with no tree at all. Chat turns
                # (orchestrator) always stay in the project dir.
                # Only the creator owns (and retires) a tree: shared
                # or treeless runs record worktree_owned=False so
                # settle can never remove another delegation's tree.
                worktree_owned = True
                if delegation.kind != "chat":
                    from sweave.runtime.specialist_store import (
                        WORKTREE_POLICIES,
                    )

                    policy = (
                        getattr(specialist, "worktree_policy", None) or "isolated"
                    )
                    if policy not in WORKTREE_POLICIES:
                        try:
                            trace.append(
                                "worktree_policy_unknown",
                                {
                                    "policy": str(policy),
                                    "agent": delegation.agent,
                                },
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        policy = "isolated"
                    if policy == "none":
                        worktree_owned = False
                        try:
                            trace.append(
                                "worktree_skipped", {"policy": "none"}
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    elif policy == "inherit":
                        parent_tree = None
                        parent_branch = None
                        if delegation.parent_task_id:
                            try:
                                parent = store.get(delegation.parent_task_id)
                            except Exception:  # noqa: BLE001
                                parent = None
                            if parent is not None:
                                candidate = (
                                    getattr(parent, "worktree_path", None)
                                )
                                if candidate and Path(candidate).exists():
                                    parent_tree = candidate
                                    parent_branch = getattr(
                                        parent, "branch", None
                                    )
                        if parent_tree:
                            worktree_owned = False
                            worktree_path = Path(parent_tree)
                            delegation.worktree_path = str(worktree_path)
                            delegation.branch = parent_branch
                            await store.update(
                                delegation.delegation_id,
                                worktree_path=str(worktree_path),
                                branch=parent_branch,
                            )
                            try:
                                trace.append(
                                    "worktree_inherited",
                                    {
                                        "worktree": str(worktree_path),
                                        "branch": parent_branch,
                                        "from": delegation.parent_task_id,
                                    },
                                )
                            except Exception:  # noqa: BLE001
                                pass
                        else:
                            # Parentless, treeless parent (chat turns
                            # own no tree), or retired tree: project
                            # root, no tree. An empty isolated tree
                            # would be useless for review; the live
                            # tree at least has the bundle + repo.
                            worktree_owned = False
                            try:
                                trace.append(
                                    "worktree_inherited_fallback",
                                    {
                                        "reason": (
                                            "no_parent_tree"
                                            if delegation.parent_task_id
                                            else "parentless"
                                        ),
                                    },
                                )
                            except Exception:  # noqa: BLE001
                                pass
                    else:
                        try:
                            wt_manager = self._worktree_manager_for(
                                project_dir, delegation.project_name
                            )
                            worktree_info = await wt_manager.async_create_worktree(
                                delegation.task_id, delegation.agent
                            )
                        except Exception as exc:  # noqa: BLE001
                            err = (
                                "[worktree error: cannot isolate task "
                                f"({type(exc).__name__}: {exc}); refusing to "
                                "run in the live tree — is the project a "
                                "git repository?]"
                            )
                            logger.warning(
                                "JobRunner: worktree creation failed for %s: %s",
                                delegation.delegation_id, exc,
                            )
                            try:
                                trace.append("worktree_failed", {"error": err})
                            except Exception:  # noqa: BLE001
                                pass
                            await store.update(
                                delegation.delegation_id,
                                output="",
                                error=err,
                            )
                            await self._transition(
                                delegation, store, trace, "failed",
                                completed_at=datetime.now(), error=err,
                            )
                            return
                        worktree_path = Path(worktree_info.path)
                        delegation.worktree_path = str(worktree_path)
                        delegation.branch = worktree_info.branch
                        await store.update(
                            delegation.delegation_id,
                            worktree_path=str(worktree_path),
                            branch=worktree_info.branch,
                        )
                        trace.append(
                            "worktree_created",
                            {
                                "worktree": str(worktree_path),
                                "branch": worktree_info.branch,
                            },
                        )
                    delegation.worktree_owned = worktree_owned
                    try:
                        await store.update(
                            delegation.delegation_id,
                            worktree_owned=worktree_owned,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                from sweave.runtime.specialist_store import ModelRef, parse_model_ref

                model_ref: ModelRef | None = None
                if delegation.model:
                    model_ref = parse_model_ref(delegation.model)
                # Step 4: transient per-task harness override (popped —
                # each delegation consumes its own) + engine-turn
                # context for the permission map.
                harness_override = self._harness_overrides.pop(
                    delegation.delegation_id, None
                )
                permission_roots = None
                if self.permission_roots_resolver is not None:
                    try:
                        permission_roots = self.permission_roots_resolver(
                            delegation.project_name
                        )
                    except Exception:  # noqa: BLE001 — fail-safe map
                        permission_roots = None
                # The task's own tree is always an allowed root (its
                # designated work area, whatever the base). This also
                # covers override bases outside the project, which the
                # built-in worktrees glob would otherwise miss.
                if delegation.kind != "chat" and delegation.worktree_path:
                    permission_roots = [
                        str(delegation.worktree_path),
                        *(permission_roots or []),
                    ]
                ok, output = await self._bounded_turn(
                    self.specialist_runtime.run(
                        specialist=specialist,
                        delegation=delegation,
                        worktree_path=worktree_path,
                        message=delegation.task,
                        trace=trace,
                        model_ref=model_ref,
                        harness=harness_override,
                        project_dir=project_dir,
                        permission_roots=permission_roots,
                        project_harness_default=self._project_harness_for(
                            delegation
                        ),
                    ),
                    delegation,
                    trace,
                    budget_override=self._turn_budget_for(delegation),
                )
                if not ok:
                    stopped = output == USER_STOPPED
                    turn_timeout_raw = self._turn_budget_raw_for(delegation)
                    await store.update(
                        delegation.delegation_id,
                        output="",
                        error=(
                            "turn_stopped_by_user"
                            if stopped
                            else f"turn_timeout_exceeded_{self._turn_budget_label_for(delegation)}s"
                        ),
                    )
                    trace.append("turn_timeout", {"timeout": turn_timeout_raw})
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

            # Persist result + transition. The engine session id the
            # runtime resolved in run() rides the same write (M2.1
            # follow-up: per-delegation display + forensics).
            await store.update(
                delegation.delegation_id,
                output=result.output or "",
                error=result.error,
                engine_session_id=getattr(delegation, "engine_session_id", None),
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
            # M2.1 step 4: on the success branch (landing in
            # ``review``), attach the review-request record + a
            # ``review_requested`` trace event. The failure branch
            # attaches nothing (failed work has nothing to review).
            # M2.1 follow-up §A step 1: entering ``review`` also sets
            # ``needs_attention`` (a review awaits human promotion, so
            # it joins the attention surfaces) + a sourced
            # ``attention_flag`` trace event.
            if final_status == "review":
                review_request = build_review_request(delegation)
                await store.update(
                    delegation.delegation_id,
                    review_request=review_request,
                    needs_attention=True,
                )
                trace.append(
                    "review_requested",
                    {
                        "delegation_id": delegation.delegation_id,
                        "reviewer_hint": review_request["reviewer_hint"],
                    },
                )
                trace.append(
                    "attention_flag",
                    {
                        "delegation_id": delegation.delegation_id,
                        "value": True,
                        "source": "review_entry",
                    },
                )
                # REVIEW Phase 1 step 1: capture the transition-time
                # review bundle synchronously (the worktree may move
                # on; later is never). Never fails the transition:
                # capture errors degrade to a pointer-less record +
                # a traced reason.
                await self._capture_review_bundle(
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
            if delegation.delegation_id in self._user_cancelled:
                # Owned by cancel_subtree (single writer): the subtree
                # pass already transitioned + traced; just propagate.
                raise
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
        """M1.6 step 3: parent gating (M2.1 step 5: wait-set-scoped).

        Wait until every JOIN-SET child (``blocking == True``) whose
        ``parent_task_id`` equals this delegation's id reaches a
        join-settled state — the shared ``JOIN_SETTLED_STATUSES``
        rule (``done``/``failed``/``review``), same as the ChatLoop
        synthesis join. ``blocking=false`` children are
        fire-and-forget: they never gate (named once in a
        ``wait_set_scoped`` trace event so the skip is auditable).
        An empty join set (leaf, or all fire-and-forget) returns
        immediately. ``review`` counts as settled: promotion is
        explicit and may lag, so a never-promoted child must not
        wedge its parent (the :898 fix — pre-M2.1 only
        ``done``/``failed`` settled, wedging the parent until
        ``turn_timeout``).

        The wait is bounded by ``self.turn_timeout`` (the same cap
        as the agent turn) so a stuck join-set child can't wedge the
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
        scoped_logged = False
        while True:
            all_children = [
                r for r in store.list()
                if r.parent_task_id == delegation.delegation_id
            ]
            join = [r for r in all_children if in_join_set(r)]
            skipped = [r for r in all_children if not in_join_set(r)]
            if join:
                children_found = True
            if skipped and not scoped_logged:
                # Audit the scoping once per wait (the skip is always
                # visible; never silently absorbed).
                trace.append(
                    "wait_set_scoped",
                    {
                        "parent": delegation.delegation_id,
                        "joined": [r.delegation_id for r in join],
                        "skipped": [r.delegation_id for r in skipped],
                    },
                )
                scoped_logged = True
            if not join and not children_found:
                # No join-set children ever existed (e.g. a leaf
                # delegation, or all fire-and-forget). No gate needed.
                return
            if children_found and all(is_join_settled(r.status) for r in join):
                # All join-set children settled -- parent can advance.
                trace.append(
                    "children_settled",
                    {
                        "parent": delegation.delegation_id,
                        "count": len(join),
                        "done": sum(1 for r in join if r.status == "done"),
                        "failed": sum(1 for r in join if r.status == "failed"),
                        "review": sum(1 for r in join if r.status == "review"),
                    },
                )
                return
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                trace.append(
                    "children_settle_timeout",
                    {
                        "parent": delegation.delegation_id,
                        "count": len(join),
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

    async def _capture_review_bundle(
        self, delegation: Delegation, store: Any, trace: TraceLog
    ) -> None:
        """Capture the transition-time review bundle (Phase 1 step 1).

        Builds the diff material, persists
        ``{project}/.sweave/reviews/{id}.diff``, and stores the
        ``review_bundle`` pointer on the record. Degraded captures
        (worktree gone, not a repo, no base) store a pointer WITHOUT
        a file (``path`` None, ``scope`` ``missing:<reason>``) so the
        detail surface can say why. Never raises — a capture error
        degrades the same way with ``scope`` ``missing:capture_error``.
        """
        try:
            manifest = delegation.manifest
            manifest = manifest if isinstance(manifest, dict) else {}
            touched = manifest.get("files_touched")
            project_dir = self._project_dir_for(delegation)
            bundle = build_review_bundle(
                delegation_id=delegation.delegation_id,
                worktree_path=delegation.worktree_path,
                manifest_files=(
                    list(touched)
                    if isinstance(touched, list)
                    else None
                ),
                project_dir=project_dir,
            )
            if bundle["scope"].startswith("missing"):
                await store.update(
                    delegation.delegation_id,
                    review_bundle={
                        "path": None,
                        "bytes": 0,
                        "truncated": False,
                        "scope": bundle["scope"],
                    },
                )
                trace.append(
                    "review_bundle_degraded",
                    {
                        "delegation_id": delegation.delegation_id,
                        "scope": bundle["scope"],
                    },
                )
                return
            art_path, nbytes = write_bundle_artifact(
                project_dir=project_dir,
                delegation_id=delegation.delegation_id,
                agent=delegation.agent,
                bundle=bundle,
            )
            try:
                rel = art_path.relative_to(project_dir).as_posix()
            except ValueError:
                rel = str(art_path)
            await store.update(
                delegation.delegation_id,
                review_bundle={
                    "path": rel,
                    "bytes": nbytes,
                    "truncated": bool(bundle["truncated"]),
                    "scope": bundle["scope"],
                },
            )
            trace.append(
                "review_bundled",
                {
                    "delegation_id": delegation.delegation_id,
                    "scope": bundle["scope"],
                    "bytes": nbytes,
                    "truncated": bool(bundle["truncated"]),
                    "files": len(bundle.get("files", [])),
                    "redactions": bundle.get("redactions", 0),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "JobRunner: review bundle capture failed for %s: %s",
                delegation.delegation_id, e,
            )
            try:
                await store.update(
                    delegation.delegation_id,
                    review_bundle={
                        "path": None,
                        "bytes": 0,
                        "truncated": False,
                        "scope": "missing:capture_error",
                    },
                )
                trace.append(
                    "review_bundle_degraded",
                    {
                        "delegation_id": delegation.delegation_id,
                        "scope": "missing:capture_error",
                        "error": f"{type(e).__name__}: {e}",
                    },
                )
            except Exception:  # noqa: BLE001
                pass

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
        # on next defer in the same process). The in-memory status is
        # set first so downstream readers (manager, worktree removal)
        # see the settled state even without a manager wired.
        if new_status in {"done", "failed"}:
            try:
                delegation.status = new_status
            except Exception:  # noqa: BLE001
                pass
        if new_status in {"done", "failed"} and self.delegation_manager is not None:
            try:
                self.delegation_manager.record_terminal(delegation)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "JobRunner: delegation_manager.record_terminal failed for %s",
                    delegation.delegation_id,
                    exc_info=True,
                )
        # Worktree isolation lifecycle: the task tree is removed at
        # settle (done/failed) — review keeps its tree for human
        # inspection; the branch is always kept. Centralized here so
        # normal, timeout and cancel paths converge (best-effort,
        # never fails the settled state).
        if new_status in {"done", "failed"}:
            await self._remove_task_worktree(delegation, trace)

    async def _publish(self, event: str, data: dict[str, Any]) -> None:
        if self.event_bus is not None:
            await self.event_bus.publish(event, data)
