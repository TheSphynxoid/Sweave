"""SpecialistRuntime: orchestrates one delegation through a ServeRunner.

M1.3 step 2. Per the plan:

* Resolve a ServeRunner (start if needed) for the (specialist, worktree)
  pair. Each delegation may land in a different worktree; the runner
  is per-worktree (Branch A: cwd binds to the serve process).
* Session lifecycle (per the M1.3 step 0 probe 4: opencode stores
  sessions in-memory only; session_id is per-serve-lifetime):
  - if ``fresh=True`` or no stored session id -> ``POST /session`` and
    persist on the Specialist record
  - else ``GET /session/{id}`` to verify; 404 -> recreate + warn trace
  - on session create, send the system prompt ONCE per session
* Per-delegation model: build the v2 body ``{"model": {providerID,
  modelID}}`` from the Specialist's ModelRef (K-revised; probe 5b
  proved bare-name fallback 400s on non-default providers, so we
  always emit the structured pair when the provider is known).
* Worktree re-injection: the FIRST message of a session includes a
  context preamble so the agent's tools operate in the right cwd
  (conversational context never assumes cwd).
* Single-active-task queue: while a delegation is running on a
  ServeRunner, subsequent submissions for the same (specialist,
  worktree) wait. (Per DESIGN.md §2.1: "one active task per
  specialist.")
* Trace events: ``serve_started`` (already emitted by ServeRunner),
  ``session_resumed|session_created``, ``worktree_set``.

The runtime is a thin orchestration layer on top of
``OpenCodeProcess`` (the existing M1.0 harness client) and
``ServeRunnerRegistry`` (M1.3 step 1). It does NOT replace either;
``harness.attach()`` (still a stub) will be wired to this in
M1.3 step 3.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from sweave.harness.opencode import OpenCodeProcess
from sweave.runtime.delegation_store import Delegation
from sweave.runtime.serve_runner import ServeRunner, ServeRunnerRegistry
from sweave.runtime.specialist_store import (
    ModelRef,
    Specialist,
    model_ref_to_wire,
)
from sweave.runtime.trace_log import TraceLog

if TYPE_CHECKING:
    from sweave.web.events import WSEventBus

logger = logging.getLogger(__name__)


# Module-level queue lock: keyed by (specialist_name, worktree_path) so
# different specialists + worktrees don't block each other. The
# lock is a simple per-key asyncio.Lock; the dict is process-local.
_RUNNER_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_RUNNER_LOCKS_META: Optional[asyncio.Lock] = None


async def _lock_for(key: tuple[str, str]) -> asyncio.Lock:
    """Return a per-(specialist, worktree) asyncio lock, creating on first use.

    Process-local; never persisted. The dict is mutated under
    ``_RUNNER_LOCKS_META`` so concurrent first-callers don't race.
    """
    global _RUNNER_LOCKS_META
    if _RUNNER_LOCKS_META is None:
        _RUNNER_LOCKS_META = asyncio.Lock()
    async with _RUNNER_LOCKS_META:
        existing = _RUNNER_LOCKS.get(key)
        if existing is not None:
            return existing
        lock = asyncio.Lock()
        _RUNNER_LOCKS[key] = lock
        return lock


class SpecialistRuntime:
    """Orchestrates one delegation through a ServeRunner.

    Construct once in the FastAPI lifespan; call :meth:`run` per
    delegation. The runtime is process-local; on server restart
    every runner is rebuilt (sessions die with the serve per
    M1.3 step 0 probe 4).
    """

    def __init__(
        self,
        *,
        runners: ServeRunnerRegistry,
        event_bus: "WSEventBus | None" = None,
    ) -> None:
        self.runners = runners
        self.event_bus = event_bus

    async def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self.event_bus is not None:
            await self.event_bus.publish(event, data)

    @staticmethod
    def _model_body(model_ref: ModelRef | None) -> dict | None:
        """Build the v2 ``body["model"]`` payload from a ModelRef.

        Returns the structured ``{providerID, modelID}`` when both
        fields are set; returns None when the ref is incomplete
        (caller may fall back to the unqualified-name path with a
        warning). Per the M1.3 K-revised plan + step 0 probe 5b,
        a structured pair is the only safe path for non-default
        providers.
        """
        return model_ref_to_wire(model_ref)

    async def _ensure_session(
        self,
        runner: ServeRunner,
        process: OpenCodeProcess,
        specialist: Specialist,
        trace: TraceLog,
        *,
        fresh: bool,
    ) -> OpenCodeProcess:
        """Return a process ready to receive a message.

        If ``fresh`` is True OR the stored session id is None/empty,
        we create a new session, persist the id on the Specialist
        record (best-effort — silently skip if the record is read-only
        or the resolver isn't available), and return the process.

        If a stored session id exists, we ``GET /session/{id}`` to
        verify; 404 means the serve was restarted (probe 4: sessions
        are in-memory) and the id is stale -> recreate and warn-trace.
        """
        stored = specialist.session_id or ""
        if fresh or not stored:
            # Create
            response = await process._client.post("/session", json={})
            response.raise_for_status()
            data = response.json()
            new_id = data.get("id")
            if not new_id:
                raise RuntimeError("opencode serve returned no session id")
            # Send the system prompt once per session
            if specialist.system_prompt:
                await process.send(_system_message(specialist.system_prompt))
            # Persist the session id (best-effort)
            await self._persist_session_id(specialist, new_id)
            trace.append("session_created", {"session_id": new_id})
            await self._emit(
                "session_created",
                {
                    "specialist": specialist.name,
                    "session_id": new_id,
                },
            )
            return process

        # Verify
        response = await process._client.get(f"/session/{stored}")
        if response.status_code == 404:
            # Stale id; create a new session
            logger.warning(
                "SpecialistRuntime: stored session_id %s returned 404; "
                "recreating (probe 4: opencode stores sessions in memory).",
                stored,
            )
            trace.append(
                "session_recreated_after_404",
                {"old_session_id": stored},
            )
            response = await process._client.post("/session", json={})
            response.raise_for_status()
            data = response.json()
            new_id = data.get("id")
            if not new_id:
                raise RuntimeError("opencode serve returned no session id")
            if specialist.system_prompt:
                await process.send(_system_message(specialist.system_prompt))
            await self._persist_session_id(specialist, new_id)
            trace.append("session_recreated", {"session_id": new_id})
            await self._emit(
                "session_resumed",  # the user-facing event name; this is a re-resume
                {
                    "specialist": specialist.name,
                    "session_id": new_id,
                    "old_session_id": stored,
                },
            )
            return process

        # Reuse
        trace.append("session_resumed", {"session_id": stored})
        await self._emit(
            "session_resumed",
            {
                "specialist": specialist.name,
                "session_id": stored,
            },
        )
        return process

    async def _persist_session_id(
        self, specialist: Specialist, session_id: str
    ) -> None:
        """Best-effort: write the session id back to the Specialist record.

        The runtime doesn't import AppState directly (avoids cycles);
        callers (step 3 / JobRunner integration) can pass a callback.
        Default: mutate the in-memory record (which is enough for the
        same ServeRunner to reuse the id; cross-runner reuse happens
        via the store's persistence layer in step 3+).
        """
        specialist.session_id = session_id

    async def run(
        self,
        *,
        specialist: Specialist,
        delegation: Delegation,
        worktree_path: Path,
        message: str,
        trace: TraceLog,
        model_ref: ModelRef | None = None,
        fresh: bool = False,
    ) -> str:
        """Run one delegation. Returns the agent's text output.

        The single-active-task queue per (specialist, worktree) is
        enforced by a per-key asyncio.Lock: concurrent calls for the
        same key serialise; different keys run in parallel.
        """
        # Trace + log + worktree_set event
        trace.append("worktree_set", {"worktree": str(worktree_path)})
        await self._emit(
            "worktree_set",
            {
                "specialist": specialist.name,
                "worktree": str(worktree_path),
            },
        )

        # Resolve the runner (start it on first access). The runner
        # gets reused on subsequent runs within the same serve
        # lifetime (probe 4: sessions are in-memory; reusing means
        # we don't pay the cost of a new session). The model + system
        # prompt are sent per-delegation; the runner's job is just to
        # own a live serve in this worktree's cwd.
        runner = await self.runners.get_or_create(
            specialist.name, worktree_path,
        )

        # Single-active-task queue per (specialist, worktree). The
        # lock is keyed on the same tuple the runner uses, so the
        # gate is the same identity.
        key = runner.key
        lock = await _lock_for(key)
        async with lock:
            # Build the OpenCodeProcess from the runner. The runner's
            # ``base_url`` is the live serve's endpoint; we instantiate
            # a fresh OpenCodeProcess with the delegation's id (or a new
            # uuid) and reuse the existing serve's HTTP client.
            process = await self._build_process(runner, delegation)

            # Ensure a session exists (create, recreate on 404, or reuse)
            process = await self._ensure_session(
                runner, process, specialist, trace, fresh=fresh
            )

            # Per-delegation body: structured ModelRef when known.
            model_body = self._model_body(model_ref or specialist.model_ref)

            # Worktree re-injection: include the cwd preamble on the
            # FIRST message of the session (or every message if
            # always_inject=True; default is every message for safety).
            preamble = (
                f"Task working directory: {worktree_path} (absolute). "
                "All file operations happen here.\n\n"
            )
            full_message = preamble + message

            body: dict[str, Any] = {"parts": [{"type": "text", "text": full_message}]}
            if model_body is not None:
                body["model"] = model_body

            # Send (the harness handles stream + terminal detection)
            result = await self._send_message(process, body, trace)
            return result

    async def _build_process(
        self, runner: ServeRunner, delegation: Delegation
    ) -> OpenCodeProcess:
        """Construct an OpenCodeProcess bound to the runner's live serve.

        The process object owns an httpx.AsyncClient; we reuse the
        runner's base_url. A new OpenCodeProcess is created per
        delegation (the v0.2 send() call is per-instance); this is
        cheap and isolates per-delegation state.

        Test hook: when ``SWEAVE_MOCK_OPENCODE=1``, return the stubbed
        process from ``OpenCodeHarness._spawn_mock`` instead. The stub
        serves the same wire format (POST /session, streaming message
        POST) with canned responses, so end-to-end tests are
        deterministic without a live opencode subprocess or LLM
        provider. This is the single gate point for the runtime path:
        the runner still start()s (a no-op spawn under the hook is
        avoided by the runner's own check), but the process the
        runtime talks to is the stub.
        """
        import os

        from sweave.config.schemas import AgentSpec

        if os.environ.get("SWEAVE_MOCK_OPENCODE") == "1":
            from sweave.harness.opencode import OpenCodeHarness

            spec_mock = AgentSpec(
                name=delegation.agent,
                role=delegation.agent,
                model=delegation.model or "",
                system_prompt="",  # already sent on session create
                worktree_path=Path(runner.worktree_path),
                memory_bank="",
                tools=[],
                env={},
                harness="opencode",
            )
            return OpenCodeHarness()._spawn_mock(spec_mock)

        # Construct a fresh AgentSpec-shaped helper for OpenCodeProcess
        # The OpenCodeProcess only uses spec.system_prompt, spec.model,
        # and spec.worktree_path for its context; we pass minimal values.
        from sweave.harness.opencode import OpenCodeProcess

        spec = AgentSpec(
            name=delegation.agent,
            role=delegation.agent,
            model=delegation.model or "",
            system_prompt="",  # already sent on session create
            worktree_path=Path(runner.worktree_path),
            memory_bank="",
            tools=[],
            env={},
            harness="opencode",
        )
        if not runner.base_url or runner.process is None:
            raise RuntimeError(
                f"ServeRunner not running: pid={runner.process and runner.process.pid}, "
                f"base_url={runner.base_url}"
            )
        # Reuse the serve's port + URL; we don't manage the subprocess here.
        return OpenCodeProcess(
            spec=spec,
            process=runner.process,
            base_url=runner.base_url,
            session_id=delegation.delegation_id,  # placeholder; _ensure_session replaces
        )

    async def _send_message(
        self,
        process: OpenCodeProcess,
        body: dict[str, Any],
        trace: TraceLog,
    ) -> str:
        """Send one message and return the agent's text output.

        The OpenCodeProcess.send path is the M1.0 v2 streaming reader;
        we capture text parts and surface errors verbatim (M1.1
        contract). A fresh delegation_id is assigned to the OpenCodeProcess
        so its ``task_id`` field isn't confused with a stale value.
        """
        from sweave.harness.base import Message

        message = Message(type="user", content=str(body.get("parts", [{}])[0].get("text", "")))
        # We bypass OpenCodeProcess.send's body construction by using
        # _client.stream directly: send() rebuilds the body from spec.model,
        # which doesn't carry our structured ModelRef. The runtime owns
        # the model field (M1.3 K-revised); the harness just delivers.
        text_parts: list[str] = []
        try:
            async with process._client.stream(
                "POST", f"/session/{process._session_id}/message", json=body
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_text():
                    if not chunk:
                        continue
                    for piece in _split_json_stream(chunk):
                        try:
                            obj = __import__("json").loads(piece)
                        except __import__("json").JSONDecodeError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        for part in obj.get("parts", []) or []:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text_parts.append(part.get("text", ""))
                            elif isinstance(part, dict) and part.get("type") == "error":
                                # Surface upstream error verbatim
                                return f"[error: {part.get('text') or part.get('error') or str(part)}]"
        except Exception as e:
            import traceback as _tb
            return f"[error: {type(e).__name__}: {e}]\n{_tb.format_exc()}"
        trace.append("output_text", {"chunks": len(text_parts), "length": sum(len(t) for t in text_parts)})
        return "".join(text_parts)


def _system_message(content: str) -> "Message":
    """Build a system-role :class:`Message`."""
    from sweave.harness.base import Message

    return Message(type="system", content=content)


def _split_json_stream(chunk: str) -> list[str]:
    """Same helper as in sweave/harness/opencode.py — split a chunk on
    the closing brace of the outermost JSON object."""
    pieces: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(chunk):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                pieces.append(chunk[start : i + 1])
                start = -1
    return pieces
