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

    async def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self.event_bus is not None:
            try:
                await self.event_bus.publish(event, data)
            except Exception as e:  # noqa: BLE001
                logger.warning("ChatLoop: event_bus publish failed for %s: %s", event, e)

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

        Steps:
        1. Acquire the per-session lock (serial queue).
        2. Persist the user message.
        3. Build a chat Delegation (kind=chat, agent=orchestrator,
           depth=0, no worktree) and store it.
        4. Run the orchestrator via SpecialistRuntime with the
           Session-bound session-id callbacks.
        5. Persist the assistant message.
        6. Fire ``message.added`` for the assistant message.
        7. Return the assistant message dict.
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
            # Store the record so the audit trail exists. We bypass
            # JobRunner.submit (which is fire-and-forget); chat is
            # request/response and we await the result.
            store = await self.delegation_stores.for_project(
                project_dir or Path.home() / ".sweave"
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

            # 3) Run the orchestrator with the Session-bound binding.
            from sweave.runtime.trace_log import TraceLog

            trace = TraceLog(delegation_id=delegation.delegation_id)
            # The Session's on-disk file is the durable binding. The
            # callbacks close over the in-memory session record and
            # persist it after every change.
            def _get_orch_id() -> str | None:
                return session.orchestrator_session_id

            def _set_orch_id(new_id: str) -> None:
                session.orchestrator_session_id = new_id
                self.project_manager.save_session(session)

            assistant_text = ""
            error_text: str | None = None
            try:
                from sweave.runtime.specialist_store import ModelRef, parse_model_ref

                model_ref: ModelRef | None = None
                if model_str:
                    model_ref = parse_model_ref(model_str)
                assistant_text = await asyncio.wait_for(
                    self.runtime.run(
                        specialist=specialist,
                        delegation=delegation,
                        worktree_path=project_dir or Path.home() / ".sweave",
                        message=user_content,
                        trace=trace,
                        model_ref=model_ref,
                        # Session-bound binding (M1.7 step 1). The
                        # default (specialist.session_id) would put the
                        # binding back on the Specialist record -- the
                        # per-project scope the wrinkle fixes.
                        session_id_getter=_get_orch_id,
                        session_id_setter=_set_orch_id,
                    ),
                    timeout=self.turn_timeout,
                )
            except asyncio.TimeoutError:
                error_text = (
                    f"[chat error: orchestrator turn exceeded "
                    f"{self.turn_timeout:.0f}s timeout]"
                )
            except Exception as e:  # noqa: BLE001
                error_text = f"[chat error: {type(e).__name__}: {e}]"

            # 4) Mark the delegation terminal. For now: success ->
            # "review" (auto-done lands in step 3). Failure -> "failed".
            final_status = "failed" if error_text else "review"
            await store.update(
                delegation.delegation_id,
                status=final_status,
                completed_at=datetime.now(),
                output=assistant_text,
                error=error_text,
            )
            await self._emit(
                "delegation.status_changed",
                {
                    "delegation_id": delegation.delegation_id,
                    "status": final_status,
                    "kind": "chat",
                    "session_id": session_id,
                },
            )

            # 5) Persist the assistant message. Even on error, the
            # assistant message is the explicit error string -- never
            # silent, never swallowed.
            assistant_content = error_text or assistant_text
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
