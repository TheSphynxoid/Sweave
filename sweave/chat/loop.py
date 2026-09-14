"""Chat loop: the orchestrator's per-turn conversation driver.

M1.7 step 2: POST /api/sessions/{id}/messages (user role) -> persist
message -> create a chat-turn Delegation (kind=chat, agent=orchestrator,
depth=0, no worktree) -> run the orchestrator specialist through
SpecialistRuntime -> persist the assistant reply.

Per-session serial queue: an asyncio.Lock keyed by session_id. A second
user message arriving while a turn is already active is REJECTED as
``TurnActiveError`` (HTTP 409; the double-send guard) -- the old
lock-as-queue semantics wait-hidden multi-minute HTTP hangs, and a
refreshed client could never see the queued message. Concurrent calls
on different sessions are independent.

Turn execution is DETACHED from the HTTP handler: ``run_turn`` spawns
the turn into a task held by the loop and shields it, so a client
refresh/disconnect cancels the POST but not the turn -- the reply is
still persisted + emitted when it finishes. The loop's turn-activity
registry (``_active_turns``) is the client-refresh recovery surface:
``active_turn_snapshot`` (served by GET /api/sessions/{id}/turn)
carries the delegation id, phase and the already-streamed text, and
the streaming coalescer accumulates + periodically persists the
partial reply onto the delegation record so a hard server death
leaves it on disk (boot recovery marks the stale record failed and
keeps the partial output).

Orchestrator binding: the durable opencode session id lives on the
Session record (Session.orchestrator_session_id, M1.7 step 1), not on
the Specialist record. The runtime gets session_id_getter /
session_id_setter callbacks that close over the in-memory Session
record (the ChatLoop also persists the Session so the binding survives
a server restart; the same field is in the Session's on-disk JSON).

Auto-done (M1.7 step 3 will revisit): for now a chat turn that
finishes successfully transitions the delegation to "review" via the
runtime's existing code path; a follow-up auto-promote to "done" lands
in step 3. Step 5's live gate will exercise the auto-done path.

Error handling: if the runtime errors, TimeoutErrors, or the orchestrator
serve is unreachable, the chat loop persists an assistant message
that explains the failure explicitly. There is no silent fallback to
the rule-router for chat (the orchestrator is the only consumer of
chat messages; the rule-router is the fallback for direct task
submissions only).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from sweave.projects import ProjectManager, Session
from sweave.runtime.delegation_store import (
    CANCELLED_BY_USER_ERROR,
    Delegation,
    PerProjectDelegationStores,
    in_join_set,
    is_join_settled,
)
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist

logger = logging.getLogger(__name__)


class TurnActiveError(RuntimeError):
    """A turn is already active (or in flight) for the session.

    Raised by :meth:`ChatLoop.run_turn` / :meth:`ChatLoop.rerun_turn`
    as the double-send guard: a second POST while a turn is running is
    REJECTED (HTTP 409), not silently queued -- a queued POST would hang
    the client's HTTP request for up to ``turn_timeout`` with no UI
    feedback, and a refreshed client cannot see it waiting.

    ``snapshot`` carries the active turn's state in exactly the shape
    ``GET /api/sessions/{id}/turn`` returns, so the router can hand the
    client the same recovery payload in the 409 response.
    """

    def __init__(self, message: str, snapshot: dict[str, Any]) -> None:
        super().__init__(message)
        self.snapshot = snapshot


@dataclass
class _ActiveTurn:
    """The running turn's live state for one session (in-memory registry).

    This is the server's half of the client-refresh recovery contract:
    a freshly loaded page calls ``GET /api/sessions/{id}/turn`` and gets
    this snapshot instead of seeing a dead ``idle`` thread.

    * ``stream_text`` / ``thinking_text`` accumulate the partial reply /
      reasoning emitted so far (the coalescer's emit closure appends),
      so a re-subscriber gets the current text, not just "a turn exists".
      Both reset when the turn advances to a new round (first turn ->
      synthesis): the prior round's text is already persisted as its
      own assistant message, so the snapshot always describes the
      CURRENT bubble, never a mix of rounds.
    * ``round`` (0 = first turn, 1 = synthesis) scopes the snapshot to
      the live round's bubble (multi-message turns, 2026-09-11).
    * ``pending_question`` is True while a blocking human question
      (ask_human / permission) holds the turn open (M1.11).
    * ``tools`` accumulates the current round's compact tool rows
      (chat transparency: one row per callID, latest status wins).
      Reset on round change like the text accumulators.
    * ``durable_until`` tracks the last periodic persist of the partial
      text onto the delegation record (see ChatLoop.stream_persist_interval).
    """

    session_id: str
    delegation_id: str | None = None
    registered_at: datetime = field(default_factory=datetime.now)
    stream_text: str = ""
    thinking_text: str = ""
    round: int = 0
    pending_question: bool = False
    task: asyncio.Task | None = None
    tools: list = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        if self.pending_question:
            phase = "question"
        elif self.stream_text or self.thinking_text or self.tools:
            phase = "streaming"
        else:
            phase = "waiting"
        return {
            "session_id": self.session_id,
            "delegation_id": self.delegation_id,
            "status": "running",
            "phase": phase,
            "started_at": self.registered_at.isoformat(),
            "stream_text": self.stream_text,
            "thinking_text": self.thinking_text,
            "round": self.round,
            "pending_question": self.pending_question,
            "tools": list(self.tools),
        }


# Silence-class turn failures: the declared-stalled turn is KILLED
# (KILL_ON_SILENCE, plus an explicit kill-verify on the timeout path,
# whose task.cancel carries no abort) and the session is ALWAYS kept.
# No-rotation invariant (user ruling): a stop kills the work, never
# the conversation — retry continues the same session. Content errors
# (auth/model rejections, validation) need no kill: the session is
# healthy and the failure is the reply.
STALE_SESSION_ERROR_MARKERS: tuple[str, ...] = (
    "stalled after",  # stall watchdog (_send_message)
    "turn exceeded",  # turn_timeout in _run_orchestrator_turn
    "ReadTimeout",  # httpx gave up first (shouldn't win anymore)
    "incomplete turn",  # stream ended without a terminal flag
)


def _is_stale_session_error(error_text: str) -> bool:
    """True iff a ``[chat error: ...]`` text is silence-class."""
    return any(m in error_text for m in STALE_SESSION_ERROR_MARKERS)


class ChatLoop:
    """The orchestrator chat loop, one per server.

    Constructed in the FastAPI lifespan; :meth:`run_turn` is called per
    user message. The class owns the per-session asyncio locks that
    enforce serial conversation semantics.
    """

    def __init__(
        self,
        *,
        project_manager: ProjectManager,
        specialist_runtime: SpecialistRuntime,
        specialist_factory: Callable[[str, str | None], Specialist | None],
        project_dir_resolver: Callable[[str | None], Path | None],
        delegation_stores: PerProjectDelegationStores,
        event_bus: Any = None,
        turn_timeout: float = 1800.0,
        model_resolver: Callable[[str, str | None], str | None] | None = None,
        synthesis_token_cap: int = 8_000,
        # M1.7 step 4: transcript system hooks. The ChatLoop is the
        # composer driver; the backend hooks are passed in so the
        # loop stays decoupled from MemoryTool / GitSnapshotter.
        memory_recall: Any = None,
        memory_bank_id_resolver: Callable[[str], str | None] | None = None,
        memory_whats_new_recall: Any = None,
        git_snapshotter: Any = None,
        # M1.8: streaming knobs. The runtime builds a
        # ``ChatDeltaCoalescer`` per turn and emits ``chat.delta``
        # events on the WSEventBus while the orchestrator replies.
        # The persisted ``message.added`` event (Step 2 final) is
        # authoritative; the chat.delta events are partial
        # snapshots the UI uses for incremental rendering.
        stream_coalesce_ms: int = 100,
        stream_char_threshold: int = 64,
        # M1.11: escalation store for blocking questions. When the
        # orchestrator calls ask_human during its turn, the MCP call
        # returns immediately but the turn stays open (no assistant
        # persisted) until answered | skipped. None = legacy path.
        escalation_store: Any = None,
        # Stop button (2026-09-14): the JobRunner owns the child
        # delegation tasks, so subtree cancel routes through it.
        # None = legacy path (parent turn cancels, live children
        # keep running into the Children lane).
        job_runner: Any = None,
        # Retry budget (turn_retries setting, default 3): retries AFTER
        # the first provider attempt on engine turns (opencode retries
        # inside its own stack). Hot-reloaded like turn_timeout; None
        # keeps the sidecar default. A per-turn project overlay value
        # wins over this singleton when project_config_resolver is set.
        turn_retries: int | None = 3,
        # Two-file config ruling: (project_name) -> effective
        # SweaveConfig (global + project overlay) or None.
        project_config_resolver: Callable[[str | None], Any | None] | None = None,
    ) -> None:
        self.project_manager = project_manager
        self.runtime = specialist_runtime
        self.specialist_factory = specialist_factory
        self.project_dir_resolver = project_dir_resolver
        self.delegation_stores = delegation_stores
        self.event_bus = event_bus
        self.turn_timeout = turn_timeout
        # model_resolver(agent, project_name) -> model string (the same
        # chain the /api/v2/tasks endpoint uses, project-aware). For
        # chat we resolve against "orchestrator" in the turn's own
        # project (task scope, not focus scope); an explicit model on
        # the chat delegation would override (none today).
        self.model_resolver = model_resolver
        # project_config_resolver(project_name) -> effective SweaveConfig
        # (global + project overlay) or None. Drives the per-turn
        # routing (timeout bound, retry budget) and the project harness
        # tier. None = global singletons (legacy/tests).
        self.project_config_resolver = project_config_resolver
        # M1.7 step 3: synthesis prompt token cap. The synthesis
        # prompt is the per-section budget for the orchestrator's
        # second turn (per-child truncation is oldest-first when
        # the total overflows). Step 4 added per-section budgets;
        # this cap remains the synthesis-specific dial.
        self.synthesis_token_cap = synthesis_token_cap
        # M1.7 step 4 transcript hooks
        self.memory_recall = memory_recall
        self.memory_bank_id_resolver = memory_bank_id_resolver
        # memory_whats_new_recall(query, bank_id, since_ts, limit) ->
        # list of entries with ts > since_ts. Distinct from
        # memory_recall because "what's new" is a temporal query
        # (filtered by ts), not a relevance query.
        self.memory_whats_new_recall = memory_whats_new_recall
        self.git_snapshotter = git_snapshotter
        # M1.8 streaming knobs
        self.stream_coalesce_ms = stream_coalesce_ms
        self.stream_char_threshold = stream_char_threshold
        # M1.11 blocking questions
        self.escalation_store = escalation_store
        # Stop button (2026-09-14)
        self.job_runner = job_runner
        # Retry budget (turn_retries setting)
        self.turn_retries = turn_retries
        # Two-file config ruling: project overlay resolver (see the
        # model_resolver note above for the contract).
        self.project_config_resolver = project_config_resolver
        # Per-session serial locks. Created on first use; never
        # persisted. The dict is mutated under _locks_meta so
        # concurrent first-callers don't race.
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_meta: Optional[asyncio.Lock] = None
        # Turn-activity registry (client-refresh recovery contract).
        # ``_active_turns`` maps session_id -> the turn's live state
        # while a turn runs on this process; ``active_turn_snapshot``
        # is what GET /api/sessions/{id}/turn serves. Entries are
        # removed in the turn's finally (never persisted: a turn in
        # an old process has no live counterpart, and boot recovery
        # marks those stale delegation records failed instead).
        self._active_turns: dict[str, _ActiveTurn] = {}
        # Synchronously-maintained companion of _active_turns: added
        # BEFORE the turn task is spawned so two simultaneous callers
        # can never both pass the guard (the registry entry itself
        # only appears after the task acquires the lock).
        self._turn_inflight: set[str] = set()
        # Live turn tasks by session (Stop button): registered alongside
        # _turn_inflight BEFORE the task is spawned, discarded in the
        # turn's finally. Lets cancel_turn drive task.cancel() on the
        # actual driver instead of only marking records.
        self._turn_tasks: dict[str, asyncio.Task] = {}
        # Delegation id per live session (pre-registration window: the
        # registry entry only appears after lock acquisition, but the
        # delegation id is minted up front in run_turn/rerun_turn).
        self._turn_delegations: dict[str, str] = {}
        # User-cancel ownership (Stop button): delegation ids whose
        # CancelledError must finalise as a deliberate stop (bubble
        # persisted, binding rotated) rather than a crash. Consumed
        # by the turn task itself; cancel_turn only sets.
        self._cancel_requested: set[str] = set()
        # Cancel-finalised bubbles by delegation id: _cancel_finalise
        # stashes here, cancel_turn pops (bounded: one entry per
        # cancel, popped on read).
        self._cancel_results: dict[str, dict[str, Any]] = {}
        # How often (seconds) the accumulated partial reply is
        # persisted onto the chat delegation record (``output``) so a
        # hard server death leaves the already-streamed text on disk.
        # 0 disables. The final ``_finalise_turn`` write is always
        # authoritative, this is only the crash tail.
        self.stream_persist_interval: float = 2.0

    # ---- turn-activity registry (refresh recovery) ----------------------

    def active_turn_snapshot(self, session_id: str) -> dict[str, Any] | None:
        """The session's active turn snapshot, or None when idle.

        Served by GET /api/sessions/{id}/turn right after a page load /
        WS reconnect so a freshly mounted thread can restore the
        waiting/streaming indicator + the already-streamed bubble.
        """
        entry = self._active_turns.get(session_id)
        if entry is None:
            return None
        snap = entry.snapshot()
        snap["updated_at"] = None
        return snap

    def _register_active_turn(
        self, session_id: str, delegation_id: str
    ) -> _ActiveTurn:
        entry = _ActiveTurn(session_id=session_id, delegation_id=delegation_id)
        self._active_turns[session_id] = entry
        return entry

    def _unregister_active_turn(self, session_id: str) -> None:
        self._active_turns.pop(session_id, None)
        self._turn_inflight.discard(session_id)
        self._turn_tasks.pop(session_id, None)
        self._turn_delegations.pop(session_id, None)

    def _set_question_flag(self, session_id: str, pending: bool) -> None:
        entry = self._active_turns.get(session_id)
        if entry is not None:
            entry.pending_question = pending

    async def _lock_for(self, session_id: str) -> asyncio.Lock:
        """Return a per-session asyncio lock, creating on first use."""
        if self._locks_meta is None:
            self._locks_meta = asyncio.Lock()
        async with self._locks_meta:
            existing = self._locks.get(session_id)
            if existing is not None:
                return existing
            lock = asyncio.Lock()
            self._locks[session_id] = lock
            return lock

    async def _compose_prompt(
        self,
        *,
        session: Any,
        user_content: str,
        project_dir: Path | None,
        children: list | None,
    ) -> Any:
        """Build the runtime's composed prompt for one orchestrator turn.

        M1.7 step 4: the composer is the runtime's view. The LLM
        sees what the runtime built -- curated memory, multi-source
        "what's new", synthesis (when children), a one-paragraph
        transcript reference, and the user message. Per-section
        token budgets keep the prompt bounded regardless of
        conversation length.
        """
        from sweave.chat.transcript import compose_turn_prompt

        bank_id: str | None = None
        if self.memory_bank_id_resolver is not None:
            try:
                bank_id = self.memory_bank_id_resolver(
                    session.project_name or ""
                )
            except Exception:  # noqa: BLE001
                bank_id = None
        return await compose_turn_prompt(
            session=session,
            user_message=user_content,
            project_dir=project_dir,
            memory_bank_id=bank_id,
            memory_backend=self.memory_recall,
            git_snapshotter=self.git_snapshotter,
            children=children,
            transcript_messages=list(session.messages),
            synthesis_budget=self.synthesis_token_cap,
        )

    async def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self.event_bus is not None:
            try:
                await self.event_bus.publish(event, data)
            except Exception as e:  # noqa: BLE001
                logger.warning("ChatLoop: event_bus publish failed for %s: %s", event, e)

    async def _wait_for_children(
        self,
        store: Any,
        parent_delegation_id: str,
        trace: Any = None,
        timeout: float | None = None,
    ) -> list:
        """Wait until every JOIN-SET child (``blocking == True``) with
        parent_task_id == *parent_delegation_id* reaches a join-settled
        state, or the turn timeout elapses.

        M2.1 wait-set: ``blocking=false`` children are fire-and-forget
        into the Children lane — excluded from the gate (still listed
        in Children, still in the trace) and named in a
        ``wait_set_scoped`` trace event so the scoping is auditable.
        An empty join set returns immediately (no deadline burn on
        all-fire-and-forget turns). ``review`` counts as settled
        (promotion is explicit and may lag) — the shared
        ``JOIN_SETTLED_STATUSES`` rule, same as the JobRunner parent
        gate.

        Bounded by the turn timeout (the same cap the runtime
        uses; ``timeout`` overrides the singleton for project-scoped
        turns) so a wedged join-set child can't stall the chat
        forever. Late-arriving children are silently absorbed --
        whatever is terminal when the deadline hits is what we
        synthesise on.

        Returns the list of JOIN-SET child records (in completion
        order) — the synthesis input.
        """
        cap = float(timeout) if timeout else float(self.turn_timeout)
        deadline = asyncio.get_running_loop().time() + cap
        poll_interval = 0.25
        children_found = False
        scoped_logged = False

        def _split() -> tuple[list, list]:
            all_children = [
                r for r in store.list()
                if r.parent_task_id == parent_delegation_id
            ]
            join = [r for r in all_children if in_join_set(r)]
            skipped = [r for r in all_children if not in_join_set(r)]
            return join, skipped

        while True:
            join, skipped = _split()
            if join:
                children_found = True
            if skipped and trace is not None and not scoped_logged:
                # Audit the scoping once per wait (the skip is always
                # visible; never silently absorbed).
                try:
                    trace.append(
                        "wait_set_scoped",
                        {
                            "parent": parent_delegation_id,
                            "joined": [r.delegation_id for r in join],
                            "skipped": [r.delegation_id for r in skipped],
                        },
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "ChatLoop: wait_set_scoped trace append failed for %s",
                        parent_delegation_id,
                    )
                scoped_logged = True
            if not join and not children_found:
                # No join-set children ever existed (leaf, or all
                # fire-and-forget): no gate needed. When skipped
                # children exist the scoping event above is the
                # audit record.
                return []
            if children_found and all(
                is_join_settled(r.status) for r in join
            ):
                return sorted(
                    join,
                    key=lambda c: c.completed_at or c.updated_at,
                )
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                logger.warning(
                    "ChatLoop: child wait timed out for %s after %.0fs; "
                    "proceeding with %d children (some may not be terminal)",
                    parent_delegation_id,
                    cap,
                    len(join),
                )
                return sorted(
                    join,
                    key=lambda c: c.completed_at or c.updated_at,
                )
            await asyncio.sleep(poll_interval)

    async def _wait_for_escalation(self, delegation_id: str) -> dict | None:
        """Wait until the delegation's escalation resolves, no deadline.

        M1.11 blocking questions (user ruling: no timeout). Returns
        the resolved record (status answered | skipped | timeout) or
        None when no escalation store is wired or no escalation was
        ever created for this delegation. Polls the store directly
        (the WS events drive the UI; the turn drives off the record).

        The LLM streaming turns stay bounded by ``turn_timeout``;
        this wait is separate and unbounded by design — the turn
        holds with no assistant persisted until the human answers
        or explicitly skips (system-confirmed in the UI).
        """
        store = self.escalation_store
        if store is None:
            return None
        try:
            rec = await store.get(delegation_id=delegation_id)
        except Exception:  # noqa: BLE001
            return None
        if rec is None:
            return None
        if rec.get("status") != "pending":
            return rec
        poll_interval = 0.5
        while True:
            await asyncio.sleep(poll_interval)
            try:
                rec = await store.get(delegation_id=delegation_id)
            except Exception:  # noqa: BLE001
                continue
            if rec is None:
                return None
            if rec.get("status") != "pending":
                return rec

    async def _peek_escalation(self, delegation_id: str) -> dict | None:
        """Non-blocking peek: a PENDING escalation record for this
        delegation, or None. Drives the turn registry's question flag
        (a peek that finds nothing does not touch the flag — the
        ask never happened / already resolved)."""
        store = self.escalation_store
        if store is None:
            return None
        try:
            rec = await store.get(delegation_id=delegation_id)
        except Exception:  # noqa: BLE001
            return None
        if rec is None or rec.get("status") != "pending":
            return None
        return rec

    @staticmethod
    def _escalation_note(rec: dict) -> str:
        """One-block synthesis note for a resolved escalation."""
        q = str(rec.get("question", "")).strip()
        status = str(rec.get("status", ""))
        resp = str(rec.get("response", "") or "").strip()
        kind = str(rec.get("kind", "question"))
        if status == "answered":
            return f"Human answer ({kind}): Q: {q} A: {resp or '(empty)'}"
        if status == "skipped":
            return (
                f"Human skipped the question ({kind}): Q: {q} — "
                "proceed with best judgment."
            )
        return f"Human Q&A ({kind}) resolved as {status}: Q: {q} A: {resp}"

    def _turn_scope_for(self, project_name: str | None) -> dict[str, Any]:
        """Per-turn scope: timeout, retries, project harness tier.

        Two-file config ruling: the turn's own project overlay wins;
        without a resolver (legacy/tests) the singletons apply. Never
        raises — every field falls back independently.
        """
        scope: dict[str, Any] = {
            "timeout": float(self.turn_timeout),
            "retries": self.turn_retries,
            "project_harness": None,
        }
        if self.project_config_resolver is None:
            return scope
        try:
            effective = self.project_config_resolver(project_name)
        except Exception:  # noqa: BLE001
            return scope
        if effective is None:
            return scope
        try:
            timeout = float(effective.routing.turn_timeout_s)
            if timeout > 0:
                scope["timeout"] = timeout
        except Exception:  # noqa: BLE001
            pass
        try:
            retries = effective.routing.turn_retries
            if retries is not None and int(retries) >= 0:
                scope["retries"] = int(retries)
        except Exception:  # noqa: BLE001
            pass
        try:
            harness = (effective.harness.default or "").strip()
            scope["project_harness"] = harness or None
        except Exception:  # noqa: BLE001
            pass
        return scope

    async def _resolve_model(self, project_name: str | None = None) -> str | None:
        """Resolve the orchestrator's model via the precedence chain.

        Uses the same ``model_resolver`` the legacy /api/tasks path
        uses (orchestrator -> backend -> ...), in the turn's own
        project (task scope, not focus scope). Returns None when no
        resolver is wired; the runtime's bare-name fallback then
        applies.
        """
        if self.model_resolver is None:
            return None
        try:
            return self.model_resolver("orchestrator", project_name)
        except Exception:  # noqa: BLE001
            return None

    async def _resolve_orchestrator_specialist(
        self, project_dir: Path | None, project_name: str | None = None
    ) -> Specialist:
        """Resolve the orchestrator specialist via the factory.

        Falls back to a transient Specialist if the factory returns
        None (test fixtures, edge cases). The orchestrator singleton
        is auto-seeded via SpecialistResolver.resolve_orchestrator in
        production; the fallback here matches JobRunner's pattern.
        Resolution is task-scoped: the turn's own project, never the
        UI-focused active project.
        """
        spec = None
        if self.specialist_factory is not None:
            try:
                spec = self.specialist_factory("orchestrator", project_name)
            except Exception:  # noqa: BLE001
                spec = None
        if spec is not None:
            return spec
        return Specialist(
            name="orchestrator",
            scope="project" if project_dir else "global",
            is_orchestrator=True,
            system_prompt="",
            harness="sweave-engine",
            current_model=None,
        )

    async def run_turn(
        self,
        *,
        session_id: str,
        user_content: str,
    ) -> dict[str, Any]:
        """Run one user turn end-to-end. Returns the assistant message dict.

        The turn body runs inside a DETACHED task held by the loop
        (not the HTTP handler): a client refresh/disconnect cancels
        the request coroutine but NOT the turn -- the assistant reply
        is still persisted + emitted when it finishes, and a refreshed
        client re-subscribes / refetches the turn state. The guard
        raises ``TurnActiveError`` (-> HTTP 409) when a turn is already
        running for the session: the double-send is rejected, never
        silently queued.

        Body steps (in ``_turn_owner_runner``; M1.7 step 2 + step 3):
        1. Acquire the per-session lock (serial queue).
        2. Persist the user message.
        3. Build a chat Delegation (kind=chat, agent=orchestrator,
           depth=0, no worktree) and store it.
        4. Run the orchestrator's first turn. The prompt includes
           the chat delegation's id so the orchestrator can pass it
           to the MCP ``defer`` tool as ``caller_delegation_id``.
        5. Scan for child delegations (``parent_task_id ==
           chat_d.delegation_id``). If none: fast path, the first
           turn's reply is the final answer.
        6. If children exist: wait for them to terminate (bounded by
           turn_timeout), build a server-composed synthesis prompt,
           and run a second orchestrator turn. The second turn's
           reply is the final answer.
        7. Persist assistant messages — one per orchestrator round
            (multi-message turns, 2026-09-11): the first-turn reply
            persists immediately as round 0 (``turn_final: False``)
            before the child wait, so a failed synthesis can never
            erase the turn's narration; the synthesis result persists
            as round 1 (final). Mark the chat delegation as ``done``
            (auto-done; implementation delegations still stop at
            ``review`` per the M1.4+M1.5 ruling). Childless turns keep
            the single-message fast path (round 0, final).
        8. Fire ``message.added`` per persisted message (round 0, then
            the final); the streaming bubbles are round-scoped
            (``chat.delta`` carries ``round``).

        Error handling: if either orchestrator call errors, the
        explicit error string is the assistant message. Children
        failing during the wait are included in the synthesis prompt
        with their status=``failed`` and error text -- the
        orchestrator's synthesis acknowledges them. The chat is
        never hung waiting forever; the child wait is bounded.
        """
        fallback = {
            "session_id": session_id,
            "delegation_id": None,
            "status": "running",
            "phase": "waiting",
            "started_at": None,
            "stream_text": "",
            "thinking_text": "",
            "pending_question": False,
            "tools": [],
        }
        snapshot = self.active_turn_snapshot(session_id)
        if snapshot is None and session_id in self._turn_inflight:
            snapshot = fallback
        if snapshot is None:
            lock = await self._lock_for(session_id)
            if lock.locked():
                snapshot = fallback
        if snapshot is not None:
            raise TurnActiveError(
                f"Session '{session_id}' already has an active chat turn; "
                "reconnect via GET /api/sessions/{id}/turn.",
                snapshot=snapshot,
            )

        delegation_id = f"chat-{uuid.uuid4().hex[:12]}"
        self._turn_inflight.add(session_id)
        try:
            task = asyncio.create_task(
                self._turn_owner_runner(
                    session_id=session_id,
                    user_content=user_content,
                    delegation_id=delegation_id,
                )
            )
            self._turn_tasks[session_id] = task
            self._turn_delegations[session_id] = delegation_id
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The HTTP handler is being cancelled (client refresh /
            # disconnect). The turn task keeps running detached and
            # still persists + emits its reply on completion.
            logger.info(
                "ChatLoop: client abandoned the POST; turn %s for session "
                "%s continues detached",
                delegation_id,
                session_id,
            )
            raise

    async def rerun_turn(
        self,
        *,
        session_id: str,
        from_message_id: str,
        content: str | None = None,
    ) -> dict[str, Any]:
        """Re-run the turn starting at a past user message.

        The target message must be role=user (`TypeError` otherwise;
        unknown session/message is `ValueError`). Supersede is record,
        not deletion: child delegations of superseded turns stay exactly
        as they were. A retry (no *content*, or identical text) keeps
        the target live, flags every message after it
        ``metadata["superseded"] = True``, and reuses the existing user
        message (no duplicate persist). An edit (*content* differs)
        APPENDS a new user message (``metadata.fork_from`` links the
        target, ``metadata.revision`` marks the revision) and flags the
        target plus its tail superseded - the original prompt text
        survives on record so the pager has something to flip between
        (revision-preserving rerun, 2026-09-14). An edit rewrites
        history in place (engine ``/revert``, opencode native revert)
        while the session binding is always kept. The turn then runs
        through the same body as a fresh turn. The double-send guard
        applies here too: a rerun while a turn is already running for
        the session raises ``TurnActiveError``.

        Returns the new assistant message dict.
        """
        snapshot = self.active_turn_snapshot(session_id)
        if snapshot is None and session_id in self._turn_inflight:
            snapshot = {
                "session_id": session_id,
                "delegation_id": None,
                "status": "running",
                "phase": "waiting",
                "started_at": None,
                "stream_text": "",
                "thinking_text": "",
                "pending_question": False,
                "tools": [],
            }
        if snapshot is None:
            lock = await self._lock_for(session_id)
            if lock.locked():
                snapshot = {
                    "session_id": session_id,
                    "delegation_id": None,
                    "status": "running",
                    "phase": "waiting",
                    "started_at": None,
                    "stream_text": "",
                    "thinking_text": "",
                    "pending_question": False,
                    "tools": [],
                }
        if snapshot is not None:
            raise TurnActiveError(
                f"Session '{session_id}' already has an active chat turn; "
                "reconnect via GET /api/sessions/{id}/turn.",
                snapshot=snapshot,
            )

        # The edit + supersede bookkeeping must complete synchronously
        # (before the detached turn starts): the caller's POST response
        # only covers this part -- the turn itself streams via WS.
        session = self.project_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session '{session_id}' not found")
        idx = next(
            (i for i, m in enumerate(session.messages) if m.id == from_message_id),
            None,
        )
        if idx is None:
            raise ValueError(
                f"Message '{from_message_id}' not found in session '{session_id}'"
            )
        target = session.messages[idx]
        if target.role != "user":
            raise TypeError(
                f"Can only rerun from a user message (got role '{target.role}')"
            )
        edited = content is not None and content != target.content
        # Revision-preserving rerun (2026-09-14): a retry reuses the
        # live target row; an edit APPENDS a new user message with
        # fork linkage and supersedes the target plus its tail, so
        # the original prompt text survives for the pager.
        rerun_user_msg = target
        new_message_id: str | None = None
        if edited:
            assert content is not None
            target.metadata["superseded"] = True
            superseded = 1
            for later in session.messages[idx + 1:]:
                later.metadata["superseded"] = True
                superseded += 1
            rerun_user_msg = session.add_message(
                role="user",
                content=content,
                metadata={
                    "fork_from": from_message_id,
                    "revision": True,
                },
            )
            new_message_id = rerun_user_msg.id
            self.project_manager.save_session(session)
            await self._emit(
                "message.added",
                {
                    "session_id": session_id,
                    "message": rerun_user_msg.to_dict(),
                },
            )
        else:
            superseded = 0
            for later in session.messages[idx + 1:]:
                later.metadata["superseded"] = True
                superseded += 1
        # No-rotation invariant: an edit rewrites history in place
        # (drops the superseded prompts from the provider session),
        # never discards the session. The rewrite runs synchronously
        # here — the session is idle (the guard above rejected an
        # active turn), so the revert busy-guard cannot trip.
        history_rewrite = "not_edited"
        if edited:
            history_rewrite = await self._rewrite_superseded_history(
                session=session, from_index=idx
            )
        self.project_manager.save_session(session)

        delegation_id = f"chat-{uuid.uuid4().hex[:12]}"
        self._turn_inflight.add(session_id)
        try:
            task = asyncio.create_task(
                self._turn_owner_runner(
                    session_id=session_id,
                    user_content=rerun_user_msg.content,
                    delegation_id=delegation_id,
                    existing_user_msg=rerun_user_msg,
                    rerun_info={
                        "from_message_id": from_message_id,
                        "edited": edited,
                        "revision": edited,
                        "fork_from": from_message_id if edited else None,
                        "new_message_id": new_message_id,
                        "history_rewrite": history_rewrite,
                        "superseded_count": superseded,
                    },
                )
            )
            self._turn_tasks[session_id] = task
            self._turn_delegations[session_id] = delegation_id
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            logger.info(
                "ChatLoop: client abandoned the rerun POST; turn %s for "
                "session %s continues detached",
                delegation_id,
                session_id,
            )
            raise

    async def _turn_owner_runner(
        self,
        *,
        session_id: str,
        user_content: str,
        delegation_id: str,
        existing_user_msg: Any | None = None,
        rerun_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The turn task: lock + register + body + crash cleanup.

        Registered in the loop's turn-activity registry (the
        refresh-recovery surface) and unregistered in the finally on
        every exit path. A mid-body crash -- before ``_finalise_turn``
        could ever mark the delegation -- is caught here and the chat
        delegation is marked ``failed`` explicitly (no phantom
        ``running`` record).
        """
        lock = await self._lock_for(session_id)
        async with lock:
            self._register_active_turn(session_id, delegation_id)
            entry = self._active_turns.get(session_id)
            if entry is not None:
                entry.task = asyncio.current_task()
            # M1.8: streaming coalescers are created once per turn
            # and closed on every exit path via try/finally. The
            # coalescers' close_and_flush is idempotent.
            coalescer_box: list = [None]
            thinking_box: list = [None]
            try:
                return await self._run_turn_body(
                    session_id=session_id,
                    user_content=user_content,
                    delegation_id=delegation_id,
                    coalescer_box=coalescer_box,
                    thinking_box=thinking_box,
                    existing_user_msg=existing_user_msg,
                    rerun_info=rerun_info,
                )
            except asyncio.CancelledError:
                if delegation_id in self._cancel_requested:
                    # User Stop (single writer: cancel_turn owns the
                    # subtree + engine abort; this branch only
                    # persists the stopped bubble).
                    self._cancel_requested.discard(delegation_id)
                    try:
                        result = await self._cancel_finalise(
                            session_id=session_id,
                            delegation_id=delegation_id,
                        )
                    except Exception as finalise_err:  # noqa: BLE001
                        logger.warning(
                            "ChatLoop: cancel finalise failed for %s: %s",
                            delegation_id, finalise_err,
                        )
                        raise
                    self._cancel_results[delegation_id] = result
                    raise
                # Server shutdown / task cancel: mark the delegation
                # cleanly failed (no phantom running record), then
                # propagate.
                await self._crash_finalise(
                    session_id, delegation_id, "cancelled"
                )
                raise
            except Exception as e:  # noqa: BLE001
                await self._crash_finalise(session_id, delegation_id, e)
                raise
            finally:
                if coalescer_box[0] is not None:
                    await coalescer_box[0].close_and_flush()
                if thinking_box[0] is not None:
                    await thinking_box[0].close_and_flush()
                self._unregister_active_turn(session_id)

    async def _crash_finalise(
        self,
        session_id: str,
        delegation_id: str,
        exc: Any,
    ) -> None:
        """Best-effort: mark the chat delegation ``failed`` when the
        turn body died before ``_finalise_turn`` ran (crash, cancel,
        compose error). Any storage failure is swallowed (this is the
        cleanup path; the trace log + delegation record stay as-is).
        """
        entry = self._active_turns.get(session_id)
        text = (
            f"[chat error: {exc}]" if isinstance(exc, str)
            else f"[chat error: {type(exc).__name__}: {exc}]"
        )
        partial = entry.stream_text if entry is not None else ""
        try:
            session = self.project_manager.get_session(session_id)
            project_dir = (
                self.project_dir_resolver(session.project_name)
                if session is not None
                else None
            )
            store = await self.delegation_stores.for_project(
                project_dir or Path.home() / ".sweave"
            )
            await store.update(
                delegation_id,
                status="failed",
                completed_at=datetime.now(),
                output=partial,
                error=text,
            )
        except Exception as cleanup_err:  # noqa: BLE001
            logger.warning(
                "ChatLoop: crash cleanup for %s failed: %s",
                delegation_id, cleanup_err,
            )
        await self._emit(
            "delegation.status_changed",
            {
                "delegation_id": delegation_id,
                "status": "failed",
                "kind": "chat",
                "session_id": session_id,
                "error": text,
            },
        )

    async def _kill_parent_turn(
        self, session_id: str, delegation_id: str
    ) -> str:
        """Kill one turn's work, keep its session (never rotate).

        Resolves the turn's engine session id from its delegation
        record and kills via the runtime (sidecar abort, or serve
        abort with a serve-restart fallback on the opencode path).
        The outcome is traced as ``turn_killed``. Never raises.
        """
        from sweave.runtime.trace_log import TraceLog

        try:
            session = self.project_manager.get_session(session_id)
            if session is None:
                return "no_session"
            project_dir = (
                self.project_dir_resolver(session.project_name)
                if session.project_name
                else None
            )
            worktree = project_dir or Path.home() / ".sweave"
            store = await self.delegation_stores.for_project(worktree)
            rec = store.get(delegation_id)
            engine_sid = (
                getattr(rec, "engine_session_id", None)
                if rec is not None
                else None
            )
            agent = (
                getattr(rec, "agent", None) or "orchestrator"
                if rec is not None
                else "orchestrator"
            )
            helper = getattr(self.runtime, "abort_live_turn", None)
            if helper is not None:
                outcome = await helper(
                    specialist_name=agent,
                    worktree_path=worktree,
                    engine_session_id=engine_sid,
                )
            else:
                # Legacy doubles (tests): engine-only abort, never spawn.
                try:
                    from sweave.harness.engine import abort_engine_session

                    ok = await abort_engine_session(engine_sid)
                    outcome = "acknowledged" if ok else "no_live_turn"
                except Exception as exc:  # noqa: BLE001
                    outcome = f"abort_failed:{type(exc).__name__}"
            try:
                TraceLog(delegation_id).append(
                    "turn_killed", {"outcome": outcome}
                )
            except Exception:  # noqa: BLE001
                pass
            return outcome
        except Exception as exc:  # noqa: BLE001
            return f"abort_failed:{type(exc).__name__}"

    async def _rewrite_superseded_history(
        self, *, session: Any, from_index: int
    ) -> str:
        """Rewrite provider history for an edit-rerun (never rotate).

        Collects the traced prompt ids of the superseded turns (chat
        delegations after *from_index*, oldest first) and drops them
        from the provider session via the runtime, so the re-run's
        replacement prompt does not sit beside the superseded
        original. The binding is kept on every outcome; without an id
        mapping the caller falls back to a rewrite preamble (named in
        the returned outcome). Never raises.
        """
        from sweave.runtime.trace_log import read_trace

        dep_ids: list[str] = []
        for later in session.messages[from_index + 1:]:
            meta = getattr(later, "metadata", None) or {}
            did = meta.get("delegation_id")
            if did and did not in dep_ids:
                dep_ids.append(did)
        before_ids: list[str] = []
        for did in dep_ids:
            try:
                events = read_trace(did)
            except Exception:  # noqa: BLE001
                continue
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                if ev.get("event") == "engine_user_message" and ev.get("id"):
                    before_ids.append(str(ev["id"]))
                elif ev.get("event") == "opencode_user_messages":
                    before_ids.extend(
                        str(i) for i in (ev.get("ids") or [])
                    )
        if not before_ids:
            return "preamble_fallback:no_mapping"
        try:
            project_dir = self.project_dir_resolver(session.project_name)
        except Exception:  # noqa: BLE001
            project_dir = None
        worktree = project_dir or Path.home() / ".sweave"
        helper = getattr(self.runtime, "rewrite_history_before", None)
        if helper is None:
            return "preamble_fallback:no_runtime"
        try:
            return await helper(
                specialist_name="orchestrator",
                worktree_path=worktree,
                session_id=session.orchestrator_session_id,
                before_ids=before_ids,
                trace=None,
            )
        except Exception as exc:  # noqa: BLE001
            return f"preamble_fallback:{type(exc).__name__}"

    async def cancel_turn(self, session_id: str) -> dict[str, Any]:
        """Stop the live turn for *session_id* (Stop button).

        Cancels the whole subtree: live child delegations first
        (via the JobRunner — their tasks are cancelled and their
        records transition to failed/cancelled), then the parent
        turn task itself. The stopped turn persists a ``cancelled``
        assistant bubble carrying the already-streamed partial text,
        so unlike a server kill nothing is lost from the thread.
        Pending escalations of the stopped subtree resolve as
        skipped (the human stopped instead of answering).

        Raises ``ValueError`` when no turn is running for the
        session (the router maps it to 404).
        """
        entry = self._active_turns.get(session_id)
        task = self._turn_tasks.get(session_id)
        if task is None and entry is None and session_id not in self._turn_inflight:
            raise ValueError(f"No active turn for session '{session_id}'")
        if task is None:
            # Pre-registration window (guard passed, lock not yet
            # acquired): nothing cancellable exists yet.
            raise ValueError(
                f"Turn for session '{session_id}' is not yet running"
            )
        delegation_id = (
            entry.delegation_id
            if entry is not None and entry.delegation_id
            else self._turn_delegations.get(session_id)
        )
        if delegation_id is None:
            raise ValueError(f"No active turn for session '{session_id}'")

        cancelled_ids: list[str] = []
        if self.job_runner is not None:
            try:
                cancelled_ids = await self.job_runner.cancel_subtree(delegation_id)
            except Exception as subtree_err:  # noqa: BLE001
                logger.warning(
                    "ChatLoop: subtree cancel failed for %s: %s",
                    delegation_id, subtree_err,
                )
        # Pending questions on the stopped subtree resolve as
        # skipped (the human stopped instead of answering); without
        # this the attention flag would strand on dead delegations.
        if self.escalation_store is not None:
            for did in [delegation_id, *cancelled_ids]:
                try:
                    await self.escalation_store.skip(delegation_id=did)
                except Exception:  # noqa: BLE001
                    continue
        # Kill the work, keep the session (no-rotation invariant).
        # The asyncio cancel below is the waiting guarantee; this
        # owns stopping the provider-side work: engine turns die via
        # the sidecar abort, opencode turns via serve abort with a
        # serve-restart fallback. Best-effort per id, never raising.
        try:
            await self._kill_parent_turn(session_id, delegation_id)
        except Exception as kill_err:  # noqa: BLE001
            logger.warning(
                "ChatLoop: parent kill failed for %s: %s",
                delegation_id, kill_err,
            )
        if cancelled_ids:
            try:
                from sweave.harness.engine import abort_engine_session

                session = self.project_manager.get_session(session_id)
                project_dir = (
                    self.project_dir_resolver(session.project_name)
                    if session is not None and session.project_name
                    else None
                )
                store = await self.delegation_stores.for_project(
                    project_dir or Path.home() / ".sweave"
                )
                for did in cancelled_ids:
                    try:
                        rec = store.get(did)
                        sid = (
                            getattr(rec, "engine_session_id", None)
                            if rec is not None
                            else None
                        )
                        if sid and sid.startswith("eng_"):
                            await abort_engine_session(sid)
                    except Exception:  # noqa: BLE001
                        continue
            except Exception as child_kill_err:  # noqa: BLE001
                logger.warning(
                    "ChatLoop: child kill failed for %s: %s",
                    delegation_id, child_kill_err,
                )

        self._cancel_requested.add(delegation_id)
        task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(task, return_exceptions=True),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "ChatLoop: turn task did not settle after cancel of %s",
                delegation_id,
            )
        result = self._cancel_results.pop(delegation_id, None)
        if result is None:
            raise RuntimeError(
                f"Turn for session '{session_id}' stopped but the "
                "stopped bubble was not persisted"
            )
        return result

    async def _cancel_finalise(
        self,
        *,
        session_id: str,
        delegation_id: str,
    ) -> dict[str, Any]:
        """Persist a user-stopped turn (single writer).

        Runs inside the dying turn task (flag consumed by the
        caller): marks the chat delegation failed/cancelled, verifies
        the kill (the cancel path already attempted it; this is the
        backstop), and persists the already-streamed partial text as a
        ``cancelled`` assistant bubble so the thread shows the stop
        instead of a hole. The orchestrator binding is KEPT
        (no-rotation invariant) — the next turn continues the same
        session. Mirrors :meth:`_finalise_turn`'s event contract.
        """
        from sweave.runtime.trace_log import TraceLog

        entry = self._active_turns.get(session_id)
        partial = entry.stream_text if entry is not None else ""
        thinking = (
            entry.thinking_text if entry is not None and entry.thinking_text else None
        )
        round_no = entry.round if entry is not None else 0
        cancelled_tools = (
            list(entry.tools) if entry is not None and entry.tools else None
        )
        session = self.project_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session '{session_id}' not found")
        store = await self.delegation_stores.for_project(
            session.project_name
            and self.project_dir_resolver(session.project_name)
            or Path.home() / ".sweave"
        )
        await store.update(
            delegation_id,
            status="failed",
            completed_at=datetime.now(),
            output=partial,
            error=CANCELLED_BY_USER_ERROR,
        )
        await self._emit(
            "delegation.status_changed",
            {
                "delegation_id": delegation_id,
                "status": "failed",
                "kind": "chat",
                "session_id": session_id,
            },
        )
        try:
            TraceLog(delegation_id).append("turn_cancelled", {"by": "user"})
        except Exception:  # noqa: BLE001
            pass
        # No rotation: the kill was attempted on the cancel path;
        # verify it here (the dying task's backstop) and keep the
        # binding unconditionally.
        try:
            await self._kill_parent_turn(session_id, delegation_id)
        except Exception:  # noqa: BLE001
            pass
        stripped = partial.strip()
        content = (
            stripped + "\n\n[turn stopped by user — partial reply kept]"
            if stripped
            else "[turn stopped by user before any output]"
        )
        metadata: dict[str, Any] = {
            "delegation_id": delegation_id,
            "turn_round": round_no,
            "turn_final": True,
            "cancelled": True,
        }
        if thinking:
            metadata["thinking"] = thinking
        if cancelled_tools:
            metadata["tools"] = cancelled_tools
        assistant_msg = session.add_message(
            role="assistant",
            content=content,
            agent="orchestrator",
            metadata=metadata,
        )
        self.project_manager.save_session(session)
        await self._emit(
            "message.added",
            {
                "session_id": session_id,
                "message": assistant_msg.to_dict(),
            },
        )
        return assistant_msg.to_dict()

    async def _run_turn_body(
        self,
        *,
        session_id: str,
        user_content: str,
        delegation_id: str,
        coalescer_box: list,
        thinking_box: list,
        # Rerun path: reuse an already-persisted user message instead
        # of persisting a duplicate (the rerun endpoint owns the
        # edit + supersede bookkeeping before calling us).
        existing_user_msg: Any | None = None,
        rerun_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
            # 1) Persist the user message (fresh turns only; reruns
            # reuse the existing row and must NOT re-emit it — the UI
            # reconciles by id and a second add would duplicate).
            session = self.project_manager.get_session(session_id)
            if session is None:
                raise ValueError(f"Session '{session_id}' not found")
            if existing_user_msg is None:
                user_msg = session.add_message(role="user", content=user_content)
                self.project_manager.save_session(session)
                await self._emit(
                    "message.added",
                    {
                        "session_id": session_id,
                        "message": user_msg.to_dict(),
                    },
                )
            else:
                user_msg = existing_user_msg

            project_dir = self.project_dir_resolver(session.project_name)
            specialist = await self._resolve_orchestrator_specialist(
                project_dir, session.project_name
            )
            model_str = await self._resolve_model(session.project_name)
            turn_scope = self._turn_scope_for(session.project_name)
            store = await self.delegation_stores.for_project(
                project_dir or Path.home() / ".sweave"
            )

            # 2) Build + persist the chat Delegation record. The
            # delegation id is generated by run_turn/rerun_turn up
            # front so the turn-activity registry carries it from the
            # first moment (a refresh inside the first second still
            # gets a rescuable snapshot).
            delegation = Delegation(
                delegation_id=delegation_id,
                task_id=f"chat-{uuid.uuid4().hex[:12]}",
                agent="orchestrator",
                model=model_str or "",
                task=user_content,
                parent_session_id=session_id,
                project_name=session.project_name,
                parent_task_id=None,  # chat is top-level
                depth=0,
                chain_root_id=None,
                coordination_tokens=0,
                kind="chat",
                status="running",
                started_at=datetime.now(),
            )
            await store.add(delegation)
            await self._emit(
                "delegation.status_changed",
                {
                    "delegation_id": delegation.delegation_id,
                    "status": "running",
                    "kind": "chat",
                    "session_id": session_id,
                },
            )

            # 3) Build the runtime's composed prompt for the first
            # turn (M1.7 step 4). The composer is the runtime's view;
            # the LLM sees what the runtime built, not the bare user
            # message. On the first turn of a new session,
            # last_memory_recall_ts is None -> "what's new" is empty
            # (per the plan's inter-session rules). The Session's
            # last_memory_recall_ts is set after the turn.
            from sweave.runtime.trace_log import TraceLog

            trace = TraceLog(delegation_id=delegation.delegation_id)
            if rerun_info is not None:
                trace.append("rerun", dict(rerun_info))

            def _get_orch_id() -> str | None:
                return session.orchestrator_session_id

            def _set_orch_id(new_id: str) -> None:
                session.orchestrator_session_id = new_id
                self.project_manager.save_session(session)

            composed = await self._compose_prompt(
                session=session,
                user_content=user_content,
                project_dir=project_dir,
                children=None,
            )
            # The caller_delegation_id wrapper is prepended so the
            # LLM can pass it back to MCP defer. The composed body
            # is the runtime's view; the LLM never sees the bare
            # user content alone.
            first_turn_body = (
                f"[sweave: caller_delegation_id={delegation.delegation_id}]\n\n"
                + composed.to_body()
            )
            # Preamble fallback (edit-rerun without an id mapping):
            # the binding was kept but the superseded prompts could
            # not be reverted — name the rewrite explicitly so the
            # model does not treat stale history as live intent.
            if rerun_info is not None and str(
                rerun_info.get("history_rewrite", "")
            ).startswith("preamble_fallback"):
                first_turn_body = (
                    "[sweave: history rewrite — the user edited an "
                    "earlier message. Treat the user content below as "
                    "replacing the superseded turns; ignore any "
                    "contradicted instructions in the conversation "
                    "history.]\n\n" + first_turn_body
                )

            # M1.8: streaming coalescer. The chat loop wraps the
            # harness's on_chunk callback in a coalescer that
            # emits chat.delta events on the bus at most every
            # ``stream_coalesce_ms`` (or sooner if the buffer
            # crosses ``stream_char_threshold``). The chat.delta
            # payload carries the live turn's delegation_id so
            # the UI can scope updates to the right bubble.
            #
            # Refresh-recovery hook: each emit also appends to the
            # turn-activity registry's stream_text (so GET .../turn
            # serves the accumulated snapshot) and -- rate-limited to
            # ``stream_persist_interval`` -- persists the partial
            # reply onto the delegation record, so a hard server
            # death leaves the text on disk (boot recovery surfaces
            # it on the failed record).
            #
            # Multi-message turns (2026-09-11): every event carries
            # the live ``round`` (0 = first turn, 1 = synthesis) so
            # the UI scopes streaming bubbles per round instead of
            # merging both rounds into one bubble that the final
            # message then replaces. The registry resets its text on
            # a round change (the prior round is already persisted).
            from sweave.chat.streaming import ChatDeltaCoalescer

            last_persist = {"t": asyncio.get_running_loop().time()}
            round_box = {"round": 0}

            async def _emit_delta(text: str) -> None:
                entry = self._active_turns.get(session_id)
                if entry is not None:
                    if entry.round != round_box["round"]:
                        entry.round = round_box["round"]
                        entry.stream_text = ""
                    entry.stream_text += text
                    await self._emit(
                        "chat.delta",
                        {
                            "session_id": session_id,
                            "delegation_id": delegation_id,
                            "round": round_box["round"],
                            "text": text,
                        },
                    )
                    loop_t = asyncio.get_running_loop().time()
                    if (
                        self.stream_persist_interval > 0
                        and entry.stream_text
                        and loop_t - last_persist["t"]
                        >= self.stream_persist_interval
                    ):
                        last_persist["t"] = loop_t
                        try:
                            await store.update(
                                delegation_id,
                                output=entry.stream_text,
                            )
                        except Exception as persist_err:  # noqa: BLE001
                            logger.warning(
                                "ChatLoop: stream snapshot persist "
                                "failed for %s: %s",
                                delegation_id, persist_err,
                            )
                    return
                # No registry entry (pre-registration emit or a
                # non-registry turn): emit only.
                await self._emit(
                    "chat.delta",
                    {
                        "session_id": session_id,
                        "delegation_id": delegation_id,
                        "round": round_box["round"],
                        "text": text,
                    },
                )

            def _make_coalescer() -> ChatDeltaCoalescer:
                return ChatDeltaCoalescer(
                    emit=_emit_delta,
                    flush_interval_ms=self.stream_coalesce_ms,
                    char_threshold=self.stream_char_threshold,
                )

            coalescer = _make_coalescer()
            coalescer.start()
            # M1.8: stash the coalescer in the box so the
            # run_turn try/finally can close it on every exit
            # path. The box avoids the early-binding problem of
            # the coalescer not existing when run_turn sets up
            # the try/finally.
            coalescer_box[0] = coalescer

            def _on_chunk(text: str) -> None:
                segments.append(("text", text))
                coalescer.push(text)

            # Thinking capture: a second coalescer over the
            # runtime's on_reasoning callback emits chat.thinking
            # events (same shape as chat.delta) so the UI can
            # render a live Thinking block. An ordered segment log
            # records text/reasoning chunks in ARRIVAL order so the
            # persisted message keeps interleave fidelity (think, act,
            # think, answer renders in that order — never coalesced
            # into one Thinking blob + one answer). Only providers
            # that emit reasoning parts produce reasoning segments.
            segments: list[tuple[str, str]] = []

            async def _emit_thinking(text: str) -> None:
                entry = self._active_turns.get(session_id)
                if entry is not None:
                    if entry.round != round_box["round"]:
                        entry.round = round_box["round"]
                        entry.thinking_text = ""
                    entry.thinking_text += text
                await self._emit(
                    "chat.thinking",
                    {
                        "session_id": session_id,
                        "delegation_id": delegation_id,
                        "round": round_box["round"],
                        "text": text,
                    },
                )

            def _make_thinking_coalescer() -> ChatDeltaCoalescer:
                return ChatDeltaCoalescer(
                    emit=_emit_thinking,
                    flush_interval_ms=self.stream_coalesce_ms,
                    char_threshold=self.stream_char_threshold,
                )

            thinking_coalescer = _make_thinking_coalescer()
            thinking_coalescer.start()
            thinking_box[0] = thinking_coalescer

            def _on_reasoning(text: str) -> None:
                segments.append(("reasoning", text))
                thinking_coalescer.push(text)

            # Tool transparency: per-callID compact rows for the live
            # round. The harness reports every transition via on_tool;
            # latest-status-wins per callID (pending -> running ->
            # completed | error). Each transition emits a chat.tool WS
            # event (no coalescing: tool counts are tiny vs text) and
            # refreshes the registry snapshot so reconnects see them.
            # Delegation-generic: keyed by (delegation, round) so
            # future specialist chats reuse the pipeline unchanged.
            from sweave.chat.tools import (
                MAX_TOOLS_PER_MESSAGE,
                compact_tool_record,
            )

            tools_by_call: dict[str, dict[str, Any]] = {}
            tools_order: list[str] = []

            async def _on_tool(event: dict[str, Any]) -> None:
                try:
                    row = compact_tool_record(
                        event, round=round_box["round"]
                    )
                except Exception:  # noqa: BLE001 — never fail a turn
                    return
                call_id = str(row.get("callID") or "")
                if not call_id:
                    return
                if call_id not in tools_by_call:
                    if len(tools_order) >= MAX_TOOLS_PER_MESSAGE:
                        return
                    tools_order.append(call_id)
                    # First sighting takes a position in the arrival
                    # log so the persisted segments render tools
                    # interleaved with thinking/text (opencode-style
                    # timeline). Status updates only refresh the row.
                    segments.append(("tool", call_id))
                tools_by_call[call_id] = row
                current = [tools_by_call[c] for c in tools_order]
                entry = self._active_turns.get(session_id)
                if entry is not None:
                    if entry.round != round_box["round"]:
                        entry.round = round_box["round"]
                        entry.stream_text = ""
                        entry.thinking_text = ""
                        entry.tools = []
                    entry.tools = list(current)
                await self._emit(
                    "chat.tool",
                    {
                        "session_id": session_id,
                        "delegation_id": delegation_id,
                        "round": round_box["round"],
                        "tool": dict(row),
                    },
                )

            def _round_tools() -> list[dict[str, Any]]:
                return [dict(tools_by_call[c]) for c in tools_order]

            async def _finish(**kwargs: Any) -> dict[str, Any]:
                """Persist the final message, closing the stream first.

                The coalescer's final flush MUST precede the
                authoritative ``message.added``: deltas are partial
                snapshots and the persisted message replaces them.
                Flushing after finalise (the old run_turn-finally-only
                order) left a trailing ``chat.delta`` that the UI
                rendered as a second, never-finalized streaming
                bubble with the turn stuck in "running".
                """
                if coalescer_box[0] is not None:
                    await coalescer_box[0].close_and_flush()
                if thinking_box[0] is not None:
                    await thinking_box[0].close_and_flush()
                thinking_text = "".join(
                    t for kind, t in segments if kind == "reasoning"
                )
                # Segments persist when the turn has reasoning OR tool
                # markers: a text-only tool-less turn keeps the legacy
                # single-block shape (no redundant single-text segment
                # in payloads). Tool markers ride as {"kind": "tool",
                # "callID"} — the row lookup stays metadata.tools.
                has_tool_markers = any(kind == "tool" for kind, _ in segments)
                segments_payload = (
                    [
                        {"kind": "tool", "callID": t}
                        if kind == "tool"
                        else {"kind": kind, "text": t}
                        for kind, t in segments
                    ]
                    if thinking_text or has_tool_markers
                    else None
                )
                return await self._finalise_turn(
                    session=session,
                    session_id=session_id,
                    user_msg=user_msg,
                    thinking_text=thinking_text or None,
                    segments=segments_payload or None,
                    tools=_round_tools() or None,
                    **kwargs,
                )

            first_turn_text = await self._run_orchestrator_turn(
                specialist=specialist,
                delegation=delegation,
                worktree_path=project_dir or Path.home() / ".sweave",
                message=first_turn_body,
                trace=trace,
                model_str=model_str,
                session_id_getter=_get_orch_id,
                session_id_setter=_set_orch_id,
                on_chunk=_on_chunk,
                on_reasoning=_on_reasoning,
                on_tool=_on_tool,
                timeout=turn_scope["timeout"],
                max_retries=turn_scope["retries"],
                project_harness=turn_scope["project_harness"],
            )

            # Update the Session's "what's new" anchors for the
            # next turn. ``now`` is the post-turn timestamp; this
            # becomes the lower bound for memory entries on the
            # NEXT turn (the user explicitly chose this behaviour:
            # "what's new" excludes entries seen on THIS turn).
            session.last_memory_recall_ts = datetime.now()
            if self.git_snapshotter is not None and project_dir is not None:
                session.last_git_snapshot = self.git_snapshotter.snapshot(
                    project_dir
                )
            self.project_manager.save_session(session)
            # Trace what was injected and what was dropped (M1.7
            # step 4 audit trail). Step 3 adds the session-stable
            # sections + the context.built audit event (same shape
            # the native engine emits — one source for both).
            trace.append(
                "composed_prompt",
                {
                    "memory_chars": len(composed.memory_section),
                    "whats_new_chars": len(composed.whats_new_section),
                    "synthesis_chars": len(composed.synthesis_section),
                    "transcript_ref_chars": len(composed.transcript_ref),
                    "instructions_chars": len(composed.instructions_section),
                    "skills_chars": len(composed.skills_section),
                    "user_chars": len(composed.user_message),
                    "dropped_memory": len(composed.dropped_memory),
                    "dropped_whats_new": len(composed.dropped_whats_new),
                    "dropped_synthesis": len(composed.dropped_synthesis),
                    "dropped_instructions": len(
                        composed.dropped_instructions
                    ),
                    "dropped_skills": len(composed.dropped_skills),
                },
            )
            if composed.context_audit is not None:
                trace.append("context.built", dict(composed.context_audit))
            if first_turn_text.startswith("[chat error:"):
                # First turn hard-failed (timeout, exception, etc.).
                # No synthesis; the error is the assistant reply.
                if _is_stale_session_error(first_turn_text):
                    # Silence-class failure: the stall kill already ran
                    # inside the runtime — verify it here for the
                    # timeout path (whose task.cancel carries no abort)
                    # and KEEP the binding. No-rotation invariant: retry
                    # continues the same session.
                    try:
                        kill_outcome = await self._kill_parent_turn(
                            session_id, delegation.delegation_id
                        )
                    except Exception:  # noqa: BLE001
                        kill_outcome = "abort_failed:unverified"
                    trace.append(
                        "stall_killed",
                        {
                            "delegation_id": delegation.delegation_id,
                            "kill": kill_outcome,
                        },
                    )
                return await _finish(
                    delegation_id=delegation.delegation_id,
                    error_text=first_turn_text,
                )

            # 5) Blocking-question gate (M1.11). ask_human returns
            # immediately at the MCP layer, but the chat turn stays
            # open (no assistant persisted) until the human answers
            # or explicitly skips. No deadline by user ruling.
            escalation_note: str | None = None
            # Question flag on the turn registry BEFORE waiting: a
            # refresh during a blocking question must show that the
            # turn is asking the human, not "waiting for the model".
            if await self._peek_escalation(delegation.delegation_id):
                self._set_question_flag(session_id, True)
            esc_rec = await self._wait_for_escalation(delegation.delegation_id)
            if self._active_turns.get(session_id) is not None:
                self._set_question_flag(session_id, False)
            if esc_rec is not None and esc_rec.get("status") in {
                "answered",
                "skipped",
                "timeout",
            }:
                trace.append(
                    "escalation_resolved",
                    {
                        "delegation_id": delegation.delegation_id,
                        "status": esc_rec.get("status"),
                        "kind": esc_rec.get("kind", "question"),
                    },
                )
                escalation_note = self._escalation_note(esc_rec)

            # 6) Scan for children the orchestrator spawned via defer
            children = [
                r for r in store.list()
                if r.parent_task_id == delegation.delegation_id
            ]
            if children:
                # Transparency (2026-09-11): one persisted assistant
                # message per orchestrator round. The first-turn reply
                # lands NOW, before the (possibly long) child wait +
                # synthesis — a failed synthesis can no longer erase
                # it, and sequential deferral rounds keep their
                # narration instead of collapsing to the final step.
                # Flush both coalescers first so the buffered round-0
                # deltas attribute to round 0, not to whatever round
                # is live at the next timer tick.
                await coalescer.flush()
                await thinking_coalescer.flush()
                round_thinking = "".join(
                    t for kind, t in segments if kind == "reasoning"
                )
                round_has_tools = any(kind == "tool" for kind, _ in segments)
                await self._persist_round_message(
                    session=session,
                    session_id=session_id,
                    delegation_id=delegation.delegation_id,
                    round=0,
                    text=first_turn_text,
                    thinking_text=round_thinking or None,
                    segments=[
                        {"kind": "tool", "callID": t}
                        if kind == "tool"
                        else {"kind": kind, "text": t}
                        for kind, t in segments
                    ] if round_thinking or round_has_tools else None,
                    tools=_round_tools() or None,
                )
                del segments[:]
                tools_by_call.clear()
                tools_order.clear()
                round_box["round"] = 1
                entry = self._active_turns.get(session_id)
                if entry is not None:
                    entry.round = 1
                    entry.stream_text = ""
                    entry.thinking_text = ""
                    entry.tools = []
            if not children and escalation_note is None:
                # Fast path: no deferrals and no blocking question --
                # the first turn's reply is the final answer.
                return await _finish(
                    delegation_id=delegation.delegation_id,
                    assistant_text=first_turn_text,
                )

            # 7) Children and/or blocking question: wait for children,
            # then run a synthesis turn carrying both. Child waits
            # stay bounded by turn_timeout; the question wait above
            # is unbounded (M1.11). M2.1: the wait joins only
            # blocking children; the trace carries the
            # wait_set_scoped audit event naming the skipped set.
            children = await self._wait_for_children(
                store,
                delegation.delegation_id,
                trace,
                timeout=turn_scope["timeout"],
            )
            # M2.1 follow-up §A step 3 (backend half): empty join set
            # with still-running fire-and-forget children. The
            # synthesis turn would otherwise see an empty result set
            # that reads as a stall, so a server-built handoff note
            # names the running children + the settle-time delivery
            # contract (the wait_set_scoped event above is the audit
            # record; this is the orchestrator-facing half).
            handoff_note: str | None = None
            if not children:
                from sweave.chat.synthesis import fire_and_forget_handoff

                skipped = [
                    r for r in store.list()
                    if r.parent_task_id == delegation.delegation_id
                    and not in_join_set(r)
                ]
                handoff_note = fire_and_forget_handoff(skipped)
                if handoff_note is not None:
                    try:
                        trace.append(
                            "handoff_note",
                            {
                                "parent": delegation.delegation_id,
                                "running": len([
                                    r for r in skipped
                                    if r.status in {"queued", "running"}
                                ]),
                            },
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "ChatLoop: handoff_note trace append failed for %s",
                            delegation.delegation_id,
                        )
            # Re-compose the prompt for the synthesis turn; the
            # synthesis section is now populated from the children's
            # results, the "what's new" anchors haven't moved yet
            # (we set them after the synthesis turn).
            composed_synth = await self._compose_prompt(
                session=session,
                user_content=user_content,
                project_dir=project_dir,
                children=children,
            )
            # M1.11: append resolved Q&A + child escalation notices
            # to the synthesis body (server-built, not LLM-composed).
            extra_sections: list[str] = []
            if escalation_note is not None:
                extra_sections.append(escalation_note)
            if handoff_note is not None:
                extra_sections.append(handoff_note)
            if self.escalation_store is not None and children:
                for child in children:
                    try:
                        child_esc = await self.escalation_store.get(
                            delegation_id=child.delegation_id
                        )
                    except Exception:  # noqa: BLE001
                        child_esc = None
                    if child_esc is not None and child_esc.get("status") in {
                        "pending",
                        "answered",
                        "skipped",
                        "timeout",
                    }:
                        extra_sections.append(
                            f"Child {child.agent} escalation "
                            f"({child_esc.get('kind', 'escalation')}/"
                            f"{child_esc.get('status')}): "
                            f"Q: {str(child_esc.get('question', '')).strip()} "
                            f"A: {str(child_esc.get('response', '') or '').strip() or '(pending)'}"
                        )
            synthesis_body = composed_synth.to_body()
            if extra_sections:
                synthesis_body += "\n\nHuman Q&A / escalations:\n" + "\n".join(
                    f"- {s}" for s in extra_sections
                )
            synthesis_turn_text = await self._run_orchestrator_turn(
                specialist=specialist,
                delegation=delegation,
                worktree_path=project_dir or Path.home() / ".sweave",
                message=(
                    f"[sweave: caller_delegation_id={delegation.delegation_id}]\n\n"
                    + synthesis_body
                ),
                trace=trace,
                model_str=model_str,
                session_id_getter=_get_orch_id,
                session_id_setter=_set_orch_id,
                on_chunk=_on_chunk,
                on_reasoning=_on_reasoning,
                on_tool=_on_tool,
                timeout=turn_scope["timeout"],
                max_retries=turn_scope["retries"],
                project_harness=turn_scope["project_harness"],
            )
            if synthesis_turn_text.startswith("[chat error:"):
                # Synthesis turn hard-failed. Return the explicit
                # error; the children are still visible via the
                # Children tab, so the user can pick up the
                # conversation. The round-0 message is already
                # persisted above, so unlike before, the failure no
                # longer erases the turn's narration.
                return await _finish(
                    delegation_id=delegation.delegation_id,
                    error_text=synthesis_turn_text,
                    round=1,
                )

            return await _finish(
                delegation_id=delegation.delegation_id,
                assistant_text=synthesis_turn_text,
                round=1,
            )

    async def _run_orchestrator_turn(
        self,
        *,
        specialist: Specialist,
        delegation: Delegation,
        worktree_path: Path,
        message: str,
        trace: Any,
        model_str: str | None,
        session_id_getter: Any,
        session_id_setter: Any,
        # M1.8: optional streaming callback. The chat loop wraps
        # this in a ChatDeltaCoalescer so the WS publishes
        # coalesced chat.delta events. None = no streaming (the
        # pre-M1.8 path: full text arrives on message.added).
        on_chunk: "Callable[[str], Any] | None" = None,
        # Thinking capture: mirrors on_chunk for reasoning parts.
        # The chat loop wraps this in a second coalescer emitting
        # chat.thinking events; the full text is persisted on the
        # assistant message metadata (see _finalise_turn).
        on_reasoning: "Callable[[str], Any] | None" = None,
        # Tool transparency: one normalized event per tool transition;
        # the loop forwards these as chat.tool WS events + persists
        # them on the assistant message metadata (see _finalise_turn).
        on_tool: "Callable[[dict[str, Any]], Any] | None" = None,
        # Per-turn scope (two-file config ruling): timeout bound,
        # retry budget, and project harness tier for THIS turn's
        # project. None = the loop singletons (legacy/tests).
        timeout: float | None = None,
        max_retries: int | None = None,
        project_harness: str | None = None,
    ) -> str:
        """Run one orchestrator turn via SpecialistRuntime.

        Returns the agent's text output, or an error string of the
        form ``[chat error: ...]`` on timeout/exception. The caller
        decides whether the result is a real reply or a failure
        based on this prefix.
        """
        from sweave.runtime.specialist_store import ModelRef, parse_model_ref

        model_ref: ModelRef | None = None
        if model_str:
            model_ref = parse_model_ref(model_str)
        # Step 4: engine-turn context for the permission map. The
        # chat turn runs in the project dir; user roots come from
        # the project record (fail-safe None when unresolvable).
        permission_roots = None
        try:
            proj = (
                self.project_manager.get_project(delegation.project_name)
                if delegation.project_name
                else None
            )
            if proj is not None:
                permission_roots = list(proj.permission_roots or [])
        except Exception:  # noqa: BLE001
            permission_roots = None
        turn_timeout = float(timeout) if timeout else float(self.turn_timeout)
        turn_retries = self.turn_retries if max_retries is None else max_retries
        inner = self.runtime.run(
            specialist=specialist,
            delegation=delegation,
            worktree_path=worktree_path,
            message=message,
            trace=trace,
            model_ref=model_ref,
            session_id_getter=session_id_getter,
            session_id_setter=session_id_setter,
            on_chunk=on_chunk,
            on_reasoning=on_reasoning,
            on_tool=on_tool,
            project_dir=worktree_path,
            permission_roots=permission_roots,
            max_retries=turn_retries,
            project_harness_default=project_harness,
        )
        task = asyncio.ensure_future(inner)
        remaining = turn_timeout
        try:
            # M1.12: the turn timer SUSPENDS while a human question
            # for this turn is unresolved (user ruling 2026-09-10:
            # no-timeout questions; timers suspended). The shielded
            # task keeps running across re-arms; on a real timeout
            # (no pending question) we cancel with the old contract.
            while True:
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(task), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    pending_q = await self._pending_human_question(
                        delegation.delegation_id
                    )
                    if pending_q is None:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        return (
                            f"[chat error: orchestrator turn exceeded "
                            f"{turn_timeout:.0f}s timeout]"
                        )
                    # Permission question outstanding: re-arm with the
                    # FULL budget (the countdown suspends, not shrinks)
                    # and hold until answered; then the post-answer
                    # stretch gets a fresh full budget.
                    trace.append(
                        "turn_timer_suspended",
                        {
                            "delegation_id": delegation.delegation_id,
                            "kind": pending_q.get("kind", "permission"),
                        },
                    )
                    remaining = turn_timeout
        except asyncio.CancelledError:
            # The turn task is being cancelled (user Stop, server
            # shutdown): the shield above protected the inner runtime
            # task from the outer cancel, so cancel it explicitly —
            # otherwise the provider stream leaks orphaned. The
            # caller's CancelledError branch (crash vs user-cancel
            # finalise) still owns the record + bubble.
            if not task.done():
                task.cancel()
            raise
        except Exception as e:  # noqa: BLE001
            return f"[chat error: {type(e).__name__}: {e}]"

    async def _pending_human_question(
        self, delegation_id: str
    ) -> dict | None:
        """The delegation's pending escalation (kind question or
        permission), or None. Used by the turn-timer suspension."""
        store = self.escalation_store
        if store is None:
            return None
        try:
            rec = await store.get(delegation_id=delegation_id)
        except Exception:  # noqa: BLE001
            return None
        if rec is None or rec.get("status") != "pending":
            return None
        kind = str(rec.get("kind", "question"))
        if kind in ("permission", "question"):
            return rec
        return None

    async def _persist_round_message(
        self,
        *,
        session: Any,
        session_id: str,
        delegation_id: str,
        round: int,
        text: str,
        thinking_text: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Persist one intermediate round's assistant message.

        Unlike :meth:`_finalise_turn` this touches nothing else: no
        delegation status change, no delegation output rewrite, no
        turn close. The round carries ``turn_round`` + ``turn_final:
        False`` metadata so the UI renders it collapsible and the
        streaming runtime scopes bubbles per round. ``segments`` is
        the arrival-ordered text/reasoning log for interleave
        fidelity; ``thinking`` stays as the joined back-compat copy.
        ``tools`` is the round's compact tool rows (chat
        transparency); omitted when empty.
        """
        metadata: dict[str, Any] = {
            "delegation_id": delegation_id,
            "turn_round": round,
            "turn_final": False,
        }
        if thinking_text:
            metadata["thinking"] = thinking_text
        if segments:
            metadata["segments"] = segments
        if tools:
            metadata["tools"] = tools
        msg = session.add_message(
            role="assistant",
            content=text,
            agent="orchestrator",
            metadata=metadata,
        )
        self.project_manager.save_session(session)
        await self._emit(
            "message.added",
            {"session_id": session_id, "message": msg.to_dict()},
        )
        return msg.to_dict()

    async def _finalise_turn(
        self,
        *,
        session: Any,
        session_id: str,
        user_msg: Any,
        delegation_id: str,
        assistant_text: str | None = None,
        error_text: str | None = None,
        thinking_text: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        round: int = 0,
        turn_final: bool = True,
    ) -> dict[str, Any]:
        """Persist the final assistant message and mark the chat
        delegation ``done`` (auto-done per the M1.7 ruling).

        ``round`` / ``turn_final`` (multi-message turns, 2026-09-11):
        the synthesis result lands as round 1 / final; every other
        call site keeps the fast-path shape (round 0, final).
        ``tools`` is the round's compact tool rows (chat
        transparency); omitted when empty. UI-only: the composer
        never reads it, so it cannot leak into the model context."""
        store = await self.delegation_stores.for_project(
            session.project_name
            and self.project_dir_resolver(session.project_name)
            or Path.home() / ".sweave"
        )
        final_status = "failed" if error_text else "done"
        # M1.7 step 3: chat turns auto-``done`` on success. The
        # ``review`` state is reserved for implementation
        # delegations (M1.4+M1.5 ruling). Implementation children
        # of a chat turn still stop at ``review`` independently.
        await store.update(
            delegation_id,
            status=final_status,
            completed_at=datetime.now(),
            output=assistant_text or "",
            error=error_text,
        )
        await self._emit(
            "delegation.status_changed",
            {
                "delegation_id": delegation_id,
                "status": final_status,
                "kind": "chat",
                "session_id": session_id,
            },
        )
        assistant_content = error_text or (assistant_text or "")
        # Thinking capture: the accumulated reasoning text rides
        # on the assistant message metadata (omitted when empty)
        # so reloads + the detail view keep it. The live
        # chat.thinking deltas already painted the Thinking block;
        # this is the durable copy with the same content.
        # ``segments`` is the arrival-ordered text/reasoning log
        # (interleave fidelity: think, act, think, answer renders in
        # that order); ``thinking`` stays as the joined back-compat
        # copy for older readers.
        metadata: dict[str, Any] = {
            "delegation_id": delegation_id,
            "turn_round": round,
            "turn_final": turn_final,
        }
        if thinking_text:
            metadata["thinking"] = thinking_text
        if segments:
            metadata["segments"] = segments
        if tools:
            metadata["tools"] = tools
        assistant_msg = session.add_message(
            role="assistant",
            content=assistant_content,
            agent="orchestrator",
            # M1.8: tag the assistant message with the chat
            # delegation's id so the UI can match it against
            # the streaming bubble (keyed by the same id). The
            # message.added event replaces the partial; the
            # delegation_id is the join key.
            metadata=metadata,
        )
        self.project_manager.save_session(session)
        await self._emit(
            "message.added",
            {
                "session_id": session_id,
                "message": assistant_msg.to_dict(),
            },
        )
        return assistant_msg.to_dict()
