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
from sweave.runtime.delegation_store import Delegation, PerProjectDelegationStores
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
    * ``pending_question`` is True while a blocking human question
      (ask_human / permission) holds the turn open (M1.11).
    * ``durable_until`` tracks the last periodic persist of the partial
      text onto the delegation record (see ChatLoop.stream_persist_interval).
    """

    session_id: str
    delegation_id: str | None = None
    registered_at: datetime = field(default_factory=datetime.now)
    stream_text: str = ""
    thinking_text: str = ""
    pending_question: bool = False
    task: asyncio.Task | None = None

    def snapshot(self) -> dict[str, Any]:
        if self.pending_question:
            phase = "question"
        elif self.stream_text or self.thinking_text:
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
            "pending_question": self.pending_question,
        }


# Silence-class turn failures: the engine session may still be busy
# server-side (a stalled turn keeps running after we stop waiting),
# so retrying on the same binding replays the wedge (2026-09-10
# stream-probe: two hung-tool timeouts, then a third turn that got
# literally nothing on the reused session). A first-turn error
# containing one of these markers rotates the orchestrator session
# binding (fresh engine session, like edits already do). Content
# errors (auth/model rejections, validation) are NOT here: the
# session is healthy, and rotating would just burn context.
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
        specialist_factory: Callable[[str], Specialist | None],
        project_dir_resolver: Callable[[str | None], Path | None],
        delegation_stores: PerProjectDelegationStores,
        event_bus: Any = None,
        turn_timeout: float = 900.0,
        model_resolver: Callable[[str], str | None] | None = None,
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
    ) -> None:
        self.project_manager = project_manager
        self.runtime = specialist_runtime
        self.specialist_factory = specialist_factory
        self.project_dir_resolver = project_dir_resolver
        self.delegation_stores = delegation_stores
        self.event_bus = event_bus
        self.turn_timeout = turn_timeout
        # model_resolver(agent) -> model string (the same chain the
        # /api/v2/tasks endpoint uses). For chat we resolve against
        # "orchestrator" by default; an explicit model on the chat
        # delegation would override (none today).
        self.model_resolver = model_resolver
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
        self, store: Any, parent_delegation_id: str
    ) -> list:
        """Wait until every delegation with parent_task_id ==
        *parent_delegation_id* reaches a terminal state, or the
        turn timeout elapses.

        Bounded by ``self.turn_timeout`` (the same cap the runtime
        uses) so a wedged child can't stall the chat forever. Late-
        arriving children are silently absorbed -- whatever is
        terminal when the deadline hits is what we synthesise on.

        Returns the list of child records (in completion order).
        """
        deadline = asyncio.get_running_loop().time() + self.turn_timeout
        poll_interval = 0.25
        children_found = False
        while True:
            all_records = store.list()
            children = [
                r for r in all_records
                if r.parent_task_id == parent_delegation_id
            ]
            if children:
                children_found = True
            if children_found and all(
                r.status in {"done", "failed", "review"} for r in children
            ):
                return sorted(
                    children,
                    key=lambda c: c.completed_at or c.updated_at,
                )
            now = asyncio.get_running_loop().time()
            if now >= deadline:
                logger.warning(
                    "ChatLoop: child wait timed out for %s after %.0fs; "
                    "proceeding with %d children (some may not be terminal)",
                    parent_delegation_id,
                    self.turn_timeout,
                    len(children),
                )
                return sorted(
                    children,
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

    async def _resolve_model(self) -> str | None:
        """Resolve the orchestrator's model via the precedence chain.

        Uses the same ``model_resolver`` the legacy /api/tasks path
        uses (orchestrator -> backend -> ...). Returns None when no
        resolver is wired; the runtime's bare-name fallback then
        applies.
        """
        if self.model_resolver is None:
            return None
        try:
            return self.model_resolver("orchestrator")
        except Exception:  # noqa: BLE001
            return None

    async def _resolve_orchestrator_specialist(
        self, project_dir: Path | None
    ) -> Specialist:
        """Resolve the orchestrator specialist via the factory.

        Falls back to a transient Specialist if the factory returns
        None (test fixtures, edge cases). The orchestrator singleton
        is auto-seeded via SpecialistResolver.resolve_orchestrator in
        production; the fallback here matches JobRunner's pattern.
        """
        spec = None
        if self.specialist_factory is not None:
            try:
                spec = self.specialist_factory("orchestrator")
            except Exception:  # noqa: BLE001
                spec = None
        if spec is not None:
            return spec
        return Specialist(
            name="orchestrator",
            scope="project" if project_dir else "global",
            is_orchestrator=True,
            system_prompt="",
            harness="opencode",
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
        7. Persist the final assistant message. Mark the chat
           delegation as ``done`` (auto-done; implementation
           delegations still stop at ``review`` per the M1.4+M1.5
           ruling). The intermediate first-turn reply (when there
           are children) is NOT persisted as the final assistant
           message -- only the synthesis result is.
        8. Fire ``message.added`` for the user + final assistant
           messages.

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
        unknown session/message is `ValueError`). Every message after
        it is flagged ``metadata["superseded"] = True`` — record, not
        deletion: child delegations of superseded turns stay exactly
        as they were. When *content* differs it replaces the message
        (edit); an edit also rotates the orchestrator session binding
        (`orchestrator_session_id = None`) so the engine never sees
        contradictory history, while a pure retry keeps the binding.
        The turn then runs through the same body as a fresh turn, but
        the existing user message is reused (no duplicate persist).
        The double-send guard applies here too: a rerun while a turn
        is already running for the session raises ``TurnActiveError``.

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
        if edited:
            target.content = content
        superseded = 0
        for later in session.messages[idx + 1:]:
            later.metadata["superseded"] = True
            superseded += 1
        rotated = False
        if edited:
            session.orchestrator_session_id = None
            rotated = True
        self.project_manager.save_session(session)

        delegation_id = f"chat-{uuid.uuid4().hex[:12]}"
        self._turn_inflight.add(session_id)
        try:
            task = asyncio.create_task(
                self._turn_owner_runner(
                    session_id=session_id,
                    user_content=target.content,
                    delegation_id=delegation_id,
                    existing_user_msg=target,
                    rerun_info={
                        "from_message_id": from_message_id,
                        "edited": edited,
                        "session_rotated": rotated,
                        "superseded_count": superseded,
                    },
                )
            )
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
            specialist = await self._resolve_orchestrator_specialist(project_dir)
            model_str = await self._resolve_model()
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
            from sweave.chat.streaming import ChatDeltaCoalescer

            last_persist = {"t": asyncio.get_running_loop().time()}

            async def _emit_delta(text: str) -> None:
                entry = self._active_turns.get(session_id)
                if entry is not None:
                    entry.stream_text += text
                    await self._emit(
                        "chat.delta",
                        {
                            "session_id": session_id,
                            "delegation_id": delegation_id,
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
                coalescer.push(text)

            # Thinking capture: a second coalescer over the
            # runtime's on_reasoning callback emits chat.thinking
            # events (same shape as chat.delta) so the UI can
            # render a live Thinking block. A plain accumulator
            # keeps the full text for the persisted message
            # metadata; only providers that emit reasoning parts
            # produce any events here.
            thinking_parts: list[str] = []

            async def _emit_thinking(text: str) -> None:
                entry = self._active_turns.get(session_id)
                if entry is not None:
                    entry.thinking_text += text
                await self._emit(
                    "chat.thinking",
                    {
                        "session_id": session_id,
                        "delegation_id": delegation_id,
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
                thinking_parts.append(text)
                thinking_coalescer.push(text)

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
                thinking_text = "".join(thinking_parts)
                return await self._finalise_turn(
                    session=session,
                    session_id=session_id,
                    user_msg=user_msg,
                    thinking_text=thinking_text or None,
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
            # step 4 audit trail).
            trace.append(
                "composed_prompt",
                {
                    "memory_chars": len(composed.memory_section),
                    "whats_new_chars": len(composed.whats_new_section),
                    "synthesis_chars": len(composed.synthesis_section),
                    "transcript_ref_chars": len(composed.transcript_ref),
                    "user_chars": len(composed.user_message),
                    "dropped_memory": len(composed.dropped_memory),
                    "dropped_whats_new": len(composed.dropped_whats_new),
                    "dropped_synthesis": len(composed.dropped_synthesis),
                },
            )
            if first_turn_text.startswith("[chat error:"):
                # First turn hard-failed (timeout, exception, etc.).
                # No synthesis; the error is the assistant reply.
                if _is_stale_session_error(first_turn_text):
                    # The engine session may still be busy with the
                    # dead turn server-side: rotate the binding so the
                    # NEXT turn (and any user retry) starts fresh
                    # instead of queueing behind the wedge. Same
                    # mechanism as edit-rotation, same audit shape as
                    # rerun (trace event, no schema change).
                    session.orchestrator_session_id = None
                    self.project_manager.save_session(session)
                    trace.append(
                        "session_rotated_after_stall",
                        {"delegation_id": delegation.delegation_id},
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
            # is unbounded (M1.11).
            children = await self._wait_for_children(
                store, delegation.delegation_id
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
            )
            if synthesis_turn_text.startswith("[chat error:"):
                # Synthesis turn hard-failed. Return the explicit
                # error; the children are still visible via the
                # Children tab, so the user can pick up the
                # conversation.
                return await _finish(
                    delegation_id=delegation.delegation_id,
                    error_text=synthesis_turn_text,
                )

            return await _finish(
                delegation_id=delegation.delegation_id,
                assistant_text=synthesis_turn_text,
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
        )
        task = asyncio.ensure_future(inner)
        remaining = self.turn_timeout
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
                            f"{self.turn_timeout:.0f}s timeout]"
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
                    remaining = self.turn_timeout
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
    ) -> dict[str, Any]:
        """Persist the final assistant message and mark the chat
        delegation ``done`` (auto-done per the M1.7 ruling)."""
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
        metadata: dict[str, Any] = {"delegation_id": delegation_id}
        if thinking_text:
            metadata["thinking"] = thinking_text
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
