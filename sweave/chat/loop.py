"""Chat loop: the orchestrator's per-turn conversation driver.

M1.7 step 2: POST /api/sessions/{id}/messages (user role) -> persist
message -> create a chat-turn Delegation (kind=chat, agent=orchestrator,
depth=0, no worktree) -> run the orchestrator specialist through
SpecialistRuntime -> persist the assistant reply.

Per-session serial queue: an asyncio.Lock keyed by session_id. A second
user message arriving mid-turn waits; each becomes its own turn after
the previous completes. Concurrent calls on different sessions are
independent.

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
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from sweave.projects import ProjectManager, Session
from sweave.runtime.delegation_store import Delegation, PerProjectDelegationStores
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist

logger = logging.getLogger(__name__)


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
        # Per-session serial locks. Created on first use; never
        # persisted. The dict is mutated under _locks_meta so
        # concurrent first-callers don't race.
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_meta: Optional[asyncio.Lock] = None

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

        Steps (M1.7 step 2 + step 3):
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
        lock = await self._lock_for(session_id)
        async with lock:
            session = self.project_manager.get_session(session_id)
            if session is None:
                raise ValueError(f"Session '{session_id}' not found")

            # 1) Persist the user message
            user_msg = session.add_message(role="user", content=user_content)
            self.project_manager.save_session(session)
            await self._emit(
                "message.added",
                {
                    "session_id": session_id,
                    "message": user_msg.to_dict(),
                },
            )

            project_dir = self.project_dir_resolver(session.project_name)
            specialist = await self._resolve_orchestrator_specialist(project_dir)
            model_str = await self._resolve_model()
            store = await self.delegation_stores.for_project(
                project_dir or Path.home() / ".sweave"
            )

            # 2) Build + persist the chat Delegation record
            delegation = Delegation(
                delegation_id=f"chat-{uuid.uuid4().hex[:12]}",
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

            first_turn_text = await self._run_orchestrator_turn(
                specialist=specialist,
                delegation=delegation,
                worktree_path=project_dir or Path.home() / ".sweave",
                message=first_turn_body,
                trace=trace,
                model_str=model_str,
                session_id_getter=_get_orch_id,
                session_id_setter=_set_orch_id,
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
                return await self._finalise_turn(
                    session=session,
                    session_id=session_id,
                    user_msg=user_msg,
                    delegation_id=delegation.delegation_id,
                    error_text=first_turn_text,
                )

            # 5) Scan for children the orchestrator spawned via defer
            children = [
                r for r in store.list()
                if r.parent_task_id == delegation.delegation_id
            ]
            if not children:
                # Fast path: no deferrals -- the first turn's reply
                # is the final answer.
                return await self._finalise_turn(
                    session=session,
                    session_id=session_id,
                    user_msg=user_msg,
                    delegation_id=delegation.delegation_id,
                    assistant_text=first_turn_text,
                )

            # 6) Children exist: wait for them, then run a synthesis
            # turn. ``_wait_for_children`` is bounded by turn_timeout
            # so a stuck child can't wedge the chat.
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
            synthesis_turn_text = await self._run_orchestrator_turn(
                specialist=specialist,
                delegation=delegation,
                worktree_path=project_dir or Path.home() / ".sweave",
                message=(
                    f"[sweave: caller_delegation_id={delegation.delegation_id}]\n\n"
                    + composed_synth.to_body()
                ),
                trace=trace,
                model_str=model_str,
                session_id_getter=_get_orch_id,
                session_id_setter=_set_orch_id,
            )
            if synthesis_turn_text.startswith("[chat error:"):
                # Synthesis turn hard-failed. Return the explicit
                # error; the children are still visible via the
                # Children tab, so the user can pick up the
                # conversation.
                return await self._finalise_turn(
                    session=session,
                    session_id=session_id,
                    user_msg=user_msg,
                    delegation_id=delegation.delegation_id,
                    error_text=synthesis_turn_text,
                )

            return await self._finalise_turn(
                session=session,
                session_id=session_id,
                user_msg=user_msg,
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
        try:
            return await asyncio.wait_for(
                self.runtime.run(
                    specialist=specialist,
                    delegation=delegation,
                    worktree_path=worktree_path,
                    message=message,
                    trace=trace,
                    model_ref=model_ref,
                    session_id_getter=session_id_getter,
                    session_id_setter=session_id_setter,
                ),
                timeout=self.turn_timeout,
            )
        except asyncio.TimeoutError:
            return (
                f"[chat error: orchestrator turn exceeded "
                f"{self.turn_timeout:.0f}s timeout]"
            )
        except Exception as e:  # noqa: BLE001
            return f"[chat error: {type(e).__name__}: {e}]"

    async def _finalise_turn(
        self,
        *,
        session: Any,
        session_id: str,
        user_msg: Any,
        delegation_id: str,
        assistant_text: str | None = None,
        error_text: str | None = None,
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
        assistant_msg = session.add_message(
            role="assistant",
            content=assistant_content,
            agent="orchestrator",
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
