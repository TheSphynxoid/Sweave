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
  - on session create, specialists get their one-off system prompt;
    the orchestrator's charter lives on the managed
    ``sweave-orchestrator`` agent instead (pinned per message, so no
    one-off send)
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
from sweave.runtime.prompt_template import (
    build_template_context,
    has_template_vars,
    render_prompt_template,
    template_var_names,
)
from sweave.runtime.mcp_config import (
    ORCHESTRATOR_AGENT_NAME,
    SPECIALIST_AGENT_NAME,
)
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
        session_id_getter: "Callable[[], str | None] | None" = None,
        session_id_setter: "Callable[[str], None] | None" = None,
    ) -> OpenCodeProcess:
        """Return a process ready to receive a message.

        If ``fresh`` is True OR the stored session id is None/empty,
        we create a new session, persist the id via the provided
        setter (or ``specialist.session_id`` by default), and return
        the process.

        If a stored session id exists, we ``GET /session/{id}`` to
        verify; 404 means the serve was restarted (probe 4: sessions
        are in-memory) and the id is stale -> recreate and warn-trace.

        M1.7 step 1: ``session_id_getter`` / ``session_id_setter`` are
        the seam that lets the orchestrator persist the opencode
        session id on the **Session** record (per project × session)
        instead of on the **Specialist** record (per project). When
        both are None, the runtime falls back to reading/writing
        ``specialist.session_id`` (the M1.3 behaviour, still used for
        non-orchestrator specialists).
        """
        # M1.7 step 1: support an external binding. When the caller
        # passes the getters/setters, the binding lives outside the
        # Specialist record (today: Session.orchestrator_session_id).
        def _get() -> str:
            if session_id_getter is not None:
                return session_id_getter() or ""
            return specialist.session_id or ""

        def _set(new_id: str) -> None:
            if session_id_setter is not None:
                session_id_setter(new_id)
                return
            specialist.session_id = new_id

        stored = _get()
        if fresh or not stored:
            # Create
            response = await process._client.post("/session", json={})
            response.raise_for_status()
            data = response.json()
            new_id = data.get("id")
            if not new_id:
                raise RuntimeError("opencode serve returned no session id")
            # One-off system prompt, specialists only, STATIC prompts
            # only. Templated prompts (``{{var}}``) are rendered fresh
            # per delegation in run() — sending them here would bake
            # the first turn's values into the reused session. The
            # orchestrator's charter lives on the managed
            # ``sweave-orchestrator`` agent (pinned per message), so
            # sending it here too would duplicate it every session;
            # specialists keep the one-off role prompt on top of the
            # generic ``sweave-specialist`` charter.
            if (
                specialist.system_prompt
                and not specialist.is_orchestrator
                and not has_template_vars(specialist.system_prompt)
            ):
                await process.send(_system_message(specialist.system_prompt))
            # Persist the session id (best-effort)
            _set(new_id)
            # R4.0: the wire MUST see the resolved id. The binding
            # (``_set``) and the process are two sources of truth;
            # they diverge unless we propagate the id into the
            # process here. Without this, the runtime's _send_message
            # POSTs to the placeholder id _build_process seeded
            # (``chat-{hex}`` for chat turns) and the opencode serve
            # returns 500 because the id is unknown.
            process._session_id = new_id  # type: ignore[attr-defined]
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
            # One-off system prompt: static prompts only (see the
            # create path — templated prompts render per delegation
            # in run()).
            if (
                specialist.system_prompt
                and not specialist.is_orchestrator
                and not has_template_vars(specialist.system_prompt)
            ):
                await process.send(_system_message(specialist.system_prompt))
            _set(new_id)
            # R4.0: same propagation as the create path -- a recreate
            # must also rebind the process's session_id so the wire
            # gets the freshly-issued id, not the stale ``stored`` id.
            process._session_id = new_id  # type: ignore[attr-defined]
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
        # R4.0: propagate the stored id into the process. The
        # previous code updated the binding only; the process kept
        # the placeholder id and the wire hit ``/session/{chat-hex}/
        # message`` -> 500. The binding and the process are
        # intentionally the same id -- that single invariant is the
        # whole point of the R4.0 fix.
        process._session_id = stored  # type: ignore[attr-defined]
        trace.append("session_resumed", {"session_id": stored})
        await self._emit(
            "session_resumed",
            {
                "specialist": specialist.name,
                "session_id": stored,
            },
        )
        return process

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
        session_id_getter: "Callable[[], str | None] | None" = None,
        session_id_setter: "Callable[[str], None] | None" = None,
        # M1.8: optional streaming callback. Default None
        # preserves the M1.7 behaviour (full text on return). The
        # chat loop's coalescer wraps this so the WS publishes
        # coalesced chat.delta events while the orchestrator
        # replies.
        on_chunk: "Callable[[str], Any] | None" = None,
    ) -> str:
        """Run one delegation. Returns the agent's text output.
        
        The single-active-task queue per (specialist, worktree) is
        enforced by a per-key asyncio.Lock: concurrent calls for the
        same key serialise; different keys run in parallel.

        M1.7 step 1: ``session_id_getter`` / ``session_id_setter``
        let the orchestrator bind the durable opencode session id to
        the **Session** record (one per project × session) instead of
        the **Specialist** record. When None, the runtime reads/writes
        ``specialist.session_id`` (the M1.3 default).
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
                runner,
                process,
                specialist,
                trace,
                fresh=fresh,
                session_id_getter=session_id_getter,
                session_id_setter=session_id_setter,
            )

            # Per-delegation body: structured ModelRef when known.
            model_body = self._model_body(model_ref or specialist.model_ref)

            # Templated system prompt (``{{var}}``): render fresh with
            # this delegation's values and send as a system message on
            # EVERY turn. Static prompts keep the legacy one-off send
            # in _ensure_session (zero wire change); the orchestrator
            # is excluded (its charter is the pinned native agent).
            if (
                specialist.system_prompt
                and not specialist.is_orchestrator
                and has_template_vars(specialist.system_prompt)
            ):
                used_ref = model_ref or specialist.model_ref
                if used_ref is not None:
                    _provider = used_ref.get("provider")
                    _model_id = used_ref.get("model_id")
                    model_str = (
                        f"{_provider}/{_model_id}"
                        if _provider and _model_id
                        else (_model_id or "")
                    )
                else:
                    model_str = ""
                context = build_template_context(
                    specialist=specialist,
                    delegation=delegation,
                    worktree_path=worktree_path,
                    model=model_str,
                )
                rendered = render_prompt_template(specialist.system_prompt, context)
                await process.send(_system_message(rendered))
                trace.append(
                    "prompt_template_rendered",
                    {
                        "specialist": specialist.name,
                        "vars": sorted(
                            set(template_var_names(specialist.system_prompt))
                            & set(context)
                        ),
                    },
                )

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
            # Opencode-native agent pin (per-message ``agent``): the
            # turn runs as the managed agent rendered into the
            # project's opencode.json (custom prompt + per-role tool
            # gating enforced by opencode itself). The orchestrator
            # turns keep the sweave MCP tools; specialist turns
            # cannot see them (``sweave_*: deny`` on the agent).
            body["agent"] = (
                ORCHESTRATOR_AGENT_NAME
                if specialist.is_orchestrator
                else SPECIALIST_AGENT_NAME
            )

            # Send (the harness handles stream + terminal detection)
            result = await self._send_message(process, body, trace, on_chunk=on_chunk)
            # Record which model was actually used for this delegation
            # (M1.4+M1.5 step 1: surface the resolved ModelRef on the
            # trace so observers can audit what ran; useful for
            # debugging switch semantics and for R6 dispatch eval).
            used = model_ref or specialist.model_ref
            trace.append(
                "model_used",
                {
                    "model_ref": dict(used) if used is not None else None,
                    "model_wire": model_body,
                    "source": (
                        "task_override" if model_ref
                        else "specialist.current_model" if (specialist.model_ref)
                        else "none"
                    ),
                },
            )
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
        # R4.0: pass session_id=None -- the contract is that the id is
        # resolved by _ensure_session (or the harness's own _ensure_session
        # for system-prompt sends), never invented. The previous
        # ``session_id=delegation.delegation_id`` placeholder leaked the
        # internal chat-{hex} id onto the wire when _ensure_session updated
        # only the external binding (see docs/R4_PLAN.md R4.0).
        return OpenCodeProcess(
            spec=spec,
            process=runner.process,
            base_url=runner.base_url,
            session_id="",  # resolved by _ensure_session (create / recreate / reuse)
        )

    async def _send_message(
        self,
        process: OpenCodeProcess,
        body: dict[str, Any],
        trace: TraceLog,
        on_chunk: "Callable[[str], Any] | None" = None,
    ) -> str:
        """Send one message and return the agent's text output.

        The OpenCodeProcess.send path is the M1.0 v2 streaming reader;
        we capture text parts and surface errors verbatim (M1.1
        contract). A fresh delegation_id is assigned to the OpenCodeProcess
        so its ``task_id`` field isn't confused with a stale value.

        M1.8: ``on_chunk`` is an optional async-or-sync callback invoked
        with each text part as it leaves the opencode stream. Default
        ``None`` preserves the M1.0+ accumulate-only behavior; all
        existing tests pass unchanged. The callback receives the
        **incremental** text (a part of the assistant reply), not the
        full accumulated output -- the runtime owns the coalescing +
        WS publish, the harness only delivers the parts.
        """
        from sweave.harness.base import Message

        message = Message(type="user", content=str(body.get("parts", [{}])[0].get("text", "")))
        # We bypass OpenCodeProcess.send's body construction by using
        # _client.stream directly: send() rebuilds the body from spec.model,
        # which doesn't carry our structured ModelRef. The runtime owns
        # the model field (M1.3 K-revised); the harness just delivers.
        text_parts: list[str] = []
        # R4.0: defensive assertion -- _ensure_session always sets
        # process._session_id to a serve-issued ``ses_*`` id before we
        # get here. If we ever reach the wire with a placeholder
        # (``chat-{hex}`` / delegation id / empty), it is a bug; the
        # opencode serve will return 500 (unknown session) and the
        # error surfaces to the user as a generic 500. Catching it
        # here turns a silent regression into a stack trace at the
        # source.
        wire_session_id = getattr(process, "_session_id", "") or ""
        if not wire_session_id.startswith("ses_"):
            raise RuntimeError(
                f"SpecialistRuntime: refusing to send -- process._session_id "
                f"is {wire_session_id!r}; expected a serve-issued id "
                f"starting with 'ses_'. _ensure_session must resolve "
                f"the id before _send_message is called."
            )
        try:
            headers_fn = getattr(process, "_default_headers", None)
            headers = headers_fn() if callable(headers_fn) else {}
            async with process._client.stream(
                "POST",
                f"/session/{wire_session_id}/message",
                json=body,
                headers=headers,
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
                                text = part.get("text", "")
                                text_parts.append(text)
                                # M1.8: forward the incremental text
                                # to the caller-supplied callback. The
                                # callback is the runtime's view; the
                                # harness only delivers parts. The
                                # caller is responsible for coalescing
                                # + WS publish (Step 2).
                                if on_chunk is not None:
                                    try:
                                        result = on_chunk(text)
                                        if hasattr(result, "__await__"):
                                            await result
                                    except Exception as cb_err:  # noqa: BLE001
                                        # A misbehaving callback must
                                        # not poison the stream -- log
                                        # + continue.
                                        logger.warning(
                                            "SpecialistRuntime: on_chunk "
                                            "callback raised: %s", cb_err
                                        )
                            # M1.9: the dead ``type: "error"`` part
                            # branch was removed. The v2 wire surfaces
                            # errors via info.error (which the harness
                            # reads and surfaces verbatim); there is no
                            # ``type: "error"`` part type. The harness's
                            # terminal detection also sees info.error
                            # and routes through the explicit error
                            # path.
        except Exception as e:
            # R4.0 / R4.2 hotfix (2026-09-07): the chat loop checks for the
            # ``[chat error:`` prefix (see ``sweave/chat/loop.py`` lines
            # 492 + 549) to short-circuit a hard-failed first/synthesis
            # turn -- the second orchestrator call would otherwise
            # also error and the user waits ~30-60s on a doomed run.
            # The previous prefix (``[error:``) didn't match the
            # chat-loop check, so the prefix was effectively dead and
            # every error triggered a wasted synthesis turn. The full
            # traceback is logged at WARNING (dev visibility) but is
            # NOT surfaced to the user -- a stack trace in the chat
            # is noise, the dev sees it in the backend log.
            import traceback as _tb
            logger.warning(
                "SpecialistRuntime._send_message failed: %s\n%s",
                type(e).__name__,
                e,
            )
            logger.debug(
                "SpecialistRuntime._send_message traceback:\n%s",
                _tb.format_exc(),
            )
            return f"[chat error: {type(e).__name__}: {e}]"
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
