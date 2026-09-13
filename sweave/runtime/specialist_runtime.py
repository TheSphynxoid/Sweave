
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

import httpx

from sweave.harness.opencode import OpenCodeProcess
from sweave.engine.protocol import (
    ENGINE_HARNESS_NAME,
    OPENCODE_HARNESS_NAME,
    ROLE_ORCHESTRATOR,
    ROLE_SPECIALIST,
)
from sweave.harness.base import (
    AgentSpec as EngineAgentSpec,
    Message as EngineMessage,
    harness_registry,
    resolve_harness_name,
)
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
    render_external_directory,
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


# Stall watchdog default (seconds of wire silence before a turn is
# declared stalled). Bounds silence, not the turn: streaming turns
# keep the full turn_timeout; silent ones fail here first with a
# truthful message. 300s reproduces the old httpx trip-point, but
# now the failure names the symptom instead of "ReadTimeout".
STALL_TIMEOUT_SECONDS = 300.0

# Kill-on-silence gate — see _attempt_engine_stop. OFF by default
# after the 2026-09-13 regression (healthy slow turns were being
# aborted mid-work); the abort mechanism stays implemented, tested,
# and one flag away when the liveness probe lands.
KILL_ON_SILENCE = False

# Pre-model bound (incident 2026-09-13, session
# Sweave-20260912-214940-2f4ca6): the serve does LEGITIMATE pre-model
# work before the first byte — tool-loop warmup steps, compaction,
# provider admission. Header-phase silence is therefore NOT the same
# signal as body-phase silence; it gets a generous bound under the
# httpx 1000s cap. The 300s body-phase clock arms at the first byte,
# where silence really does mean wedged.
PRE_MODEL_TIMEOUT_SECONDS = 950.0


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


async def _attempt_engine_stop(
    process: Any, session_id: str, trace: Any | None
) -> str:
    """Best-effort ``POST /session/{id}/abort``. Returns a message
    suffix naming the outcome. NEVER raises.

    INCIDENT OFF (2026-09-13, user-identified regression): kill-on-
    silence converted previously-survivable slow turns into kills —
    the 05:20 frontend trip aborted an alive, 9-patches-deep turn
    mid-work. Byte-silence cannot classify patient-vs-wedged. Until
    a liveness probe exists, Sweave trips the bound, records the
    failure loudly, rotates for retry — and lets the specialist
    continue server-side (the pre-watchdog semantics that "used to
    work": late-failed but completed). When the probe ships, set
    KILL_ON_SILENCE=True (the mechanism below stays live and tested
    via the flag); an acknowledged stop then resolves the paradox,
    anything else stays LOUD (UNCONFIRMED) instead of silent.
    """
    if not KILL_ON_SILENCE:
        if trace is not None:
            try:
                trace.append("abort_skipped", {"reason": "kill_on_silence_off"})
            except Exception:  # noqa: BLE001
                pass
        return "; stop not attempted (kill-on-silence off; work may continue server-side)"
    if not session_id:

        def _trace(event: str, payload: dict[str, Any]) -> None:
            if trace is not None:
                try:
                    trace.append(event, payload)
                except Exception:  # noqa: BLE001
                    pass

        _trace("abort_skipped", {"reason": "no engine session"})
        return "; stop not attempted (no engine session)"
    client = getattr(process, "_client", None)
    post = getattr(client, "post", None)
    if not callable(post):

        def _trace(event: str, payload: dict[str, Any]) -> None:
            if trace is not None:
                try:
                    trace.append(event, payload)
                except Exception:  # noqa: BLE001
                    pass

        _trace("abort_skipped", {"reason": "no abort channel"})
        return "; stop UNCONFIRMED — orphaned run possible (no abort channel)"
    try:
        headers_fn = getattr(process, "_default_headers", None)
        headers = headers_fn() if callable(headers_fn) else {}
        resp = await post(
            f"/session/{session_id}/abort", headers=headers, timeout=10.0
        )
        status = getattr(resp, "status_code", None)
        ok = isinstance(status, int) and 200 <= status < 300
        if trace is not None:
            try:
                trace.append(
                    "abort_sent",
                    {"status_code": status, "acknowledged": ok},
                )
            except Exception:  # noqa: BLE001
                pass
        if ok:
            return "; serve acknowledged stop"
        return "; stop UNCONFIRMED — orphaned run possible (abort rejected)"
    except Exception as e:  # noqa: BLE001

        def _trace(event: str, payload: dict[str, Any]) -> None:
            if trace is not None:
                try:
                    trace.append(event, payload)
                except Exception:  # noqa: BLE001
                    pass

        _trace("abort_failed", {"error": f"{type(e).__name__}: {e}"})
        return "; stop UNCONFIRMED — orphaned run possible"


class _ToolActivityProbe:
    """Trace wrapper counting engine ``tool.started`` events.

    Step-4 fallback guard: the opencode fallback re-runs the whole
    turn from scratch, so it must engage ONLY when the engine did no
    work yet (no tool executed, no text produced). Re-running after
    side effects (file edits, defers, escalations) would execute
    them twice. Duck-types :class:`TraceLog` (``append`` +
    attribute passthrough) so doubles work in tests.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.tool_started = 0

    def append(self, event: str, payload: dict[str, Any] | None = None) -> Any:
        if event == "tool.started":
            self.tool_started += 1
        return self._inner.append(event, payload)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


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
        escalation_store: Any = None,
        # Step 4: operator global harness default (wired from
        # config ``harness.default`` in the lifespan). Used only
        # when neither the per-task override nor the specialist
        # record names a registered harness.
        harness_default: str | None = None,
    ) -> None:
        self.runners = runners
        self.event_bus = event_bus
        # M1.12: EscalationStore for permission questions. When a
        # stalled turn has a pending opencode permission (bus
        # signal), the runtime converts it into a blocking human
        # question (kind="permission", no timeout) and posts the
        # answer back to the serve. Wire-only (no sqlite).
        self.escalation_store = escalation_store
        self.harness_default = harness_default

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
        session_id_getter: Callable[[], str | None] | None = None,
        session_id_setter: Callable[[str], None] | None = None,
        on_chunk: Callable[[str], Any] | None = None,
        on_reasoning: Callable[[str], Any] | None = None,
        # Step 4: per-task harness override (beats the specialist
        # record — and the test mock. Explicit is explicit).
        harness: str | None = None,
        # Step 4: engine-turn context for the orchestrator-rendered
        # permission map (blindly enforced by the engine). Opencode
        # turns ignore both (the map lives in opencode.json there).
        project_dir: Path | None = None,
        permission_roots: Any = None,
    ) -> str:
        """Run one delegation on the selected harness.

        Resolution (``harness_selected`` trace event): per-task
        override > test mock > specialist record > operator default
        > opencode. The native engine is the default for records
        that never chose (step-4 flip); opencode is the automatic
        per-delegation fallback when the engine fails before doing
        any work (``fallback_used`` trace event).
        """
        selected, source = resolve_harness_name(
            harness, specialist.harness, self.harness_default
        )
        trace.append(
            "harness_selected",
            {
                "requested": harness,
                "specialist_harness": specialist.harness,
                "selected": selected,
                "source": source,
            },
        )
        if selected == ENGINE_HARNESS_NAME:
            output, fallback_reason = await self._run_engine_attempt(
                specialist=specialist,
                delegation=delegation,
                worktree_path=worktree_path,
                message=message,
                trace=trace,
                model_ref=model_ref,
                fresh=fresh,
                session_id_getter=session_id_getter,
                session_id_setter=session_id_setter,
                on_chunk=on_chunk,
                project_dir=project_dir,
                permission_roots=permission_roots,
            )
            if fallback_reason is None:
                return output  # type: ignore[return-value]
            logger.warning(
                "SpecialistRuntime: engine turn failed before any "
                "work (%s); falling back to opencode for %s",
                fallback_reason,
                delegation.delegation_id,
            )
            trace.append(
                "fallback_used",
                {
                    "from": ENGINE_HARNESS_NAME,
                    "to": OPENCODE_HARNESS_NAME,
                    "reason": fallback_reason,
                    "delegation_id": delegation.delegation_id,
                },
            )
        return await self._run_opencode(
            specialist=specialist,
            delegation=delegation,
            worktree_path=worktree_path,
            message=message,
            trace=trace,
            model_ref=model_ref,
            fresh=fresh,
            session_id_getter=session_id_getter,
            session_id_setter=session_id_setter,
            on_chunk=on_chunk,
            on_reasoning=on_reasoning,
        )

    async def _run_engine_attempt(
        self,
        *,
        specialist: Specialist,
        delegation: Delegation,
        worktree_path: Path,
        message: str,
        trace: TraceLog,
        model_ref: ModelRef | None = None,
        fresh: bool = False,
        session_id_getter: Callable[[], str | None] | None = None,
        session_id_setter: Callable[[str], None] | None = None,
        on_chunk: Callable[[str], Any] | None = None,
        project_dir: Path | None = None,
        permission_roots: Any = None,
    ) -> tuple[str | None, str | None]:
        """Attempt one turn on the native engine.

        Returns ``(output, None)`` when the turn settled (success or
        honest failure); ``(None, reason)`` when the caller must run
        the opencode fallback — i.e. the engine raised, was never
        reached (not registered, version drift), or failed with no
        tool executed and no text produced. Anything the engine
        actually did (tools, partial text) is returned as-is, never
        re-run. Cancellation propagates (never swallowed into a
        fallback — the outer bound owns that decision).
        """
        probe = _ToolActivityProbe(trace)
        try:
            harness_obj = harness_registry.get(ENGINE_HARNESS_NAME)
            if harness_obj is None:
                return None, "engine harness not registered"

            used_ref = model_ref or specialist.model_ref
            if used_ref is not None:
                _provider = used_ref.get("provider")
                _model_id = used_ref.get("model_id")
                _variant = used_ref.get("variant")
                model_str = (
                    f"{_provider}/{_model_id}"
                    if _provider and _model_id
                    else (_model_id or "")
                )
                if _variant and _provider and _model_id:
                    model_str = f"{model_str}+{_variant}"
            else:
                model_str = ""

            # Session binding: same rule as _ensure_session —
            # external getter wins, else the specialist record. The
            # engine owns durable sessions; attach resumes, spawn
            # starts. A turn that mints the binding is a new session.
            if session_id_getter is not None:
                stored = session_id_getter() or ""
            else:
                stored = specialist.session_id or ""
            new_session = fresh or not stored

            prompt_text = message
            if new_session:
                # Role charter, new sessions only (reused sessions
                # remember it — same session-memory rule as the
                # opencode path, minus the per-message agent pin the
                # protocol has no field for). Specialists render
                # {{var}} templates fresh like the opencode path.
                charter = specialist.system_prompt or ""
                if (
                    charter
                    and not specialist.is_orchestrator
                    and has_template_vars(charter)
                ):
                    context = build_template_context(
                        specialist=specialist,
                        delegation=delegation,
                        worktree_path=worktree_path,
                        model=model_str,
                    )
                    charter = render_prompt_template(charter, context)
                    trace.append(
                        "prompt_template_rendered",
                        {
                            "specialist": specialist.name,
                            "vars": sorted(
                                set(template_var_names(charter)) & set(context)
                            ),
                        },
                    )
                if charter.strip():
                    prompt_text = charter.strip() + "\n\n" + message

            scope_dir = project_dir or worktree_path
            permission_map = {
                "external_directory": render_external_directory(
                    scope_dir, permission_roots
                )
            }
            spec = EngineAgentSpec(
                name=specialist.name,
                role=(
                    ROLE_ORCHESTRATOR
                    if specialist.is_orchestrator
                    else ROLE_SPECIALIST
                ),
                model=model_str,
                system_prompt="",
                worktree_path=Path(worktree_path),
                memory_bank="",
                tools=(
                    []
                    if specialist.is_orchestrator
                    else list(harness_obj.get_default_tools())
                ),
                env={},
                harness=ENGINE_HARNESS_NAME,
            )
            if stored and not fresh:
                process = await harness_obj.attach(stored, spec)
            else:
                process = await harness_obj.spawn(spec)
            msg = EngineMessage(
                type="user",
                content=prompt_text,
                metadata={
                    "permission_map": permission_map,
                    "delegation_id": delegation.delegation_id,
                    "role": (
                        ROLE_ORCHESTRATOR
                        if specialist.is_orchestrator
                        else ROLE_SPECIALIST
                    ),
                },
                model=used_ref,
            )
            result = await process.send(msg, on_chunk=on_chunk, trace=probe)

            # Persist the engine session binding (best-effort, like
            # the opencode path) + record it on the delegation.
            try:
                engine_sid = (
                    getattr(process, "_session_id", "")
                    or getattr(process, "session_id", "")
                    or ""
                )
                if engine_sid:
                    if session_id_setter is not None:
                        session_id_setter(engine_sid)
                    else:
                        specialist.session_id = engine_sid
                    delegation.engine_session_id = engine_sid
                    trace.append(
                        "session_created" if new_session else "session_resumed",
                        {"session_id": engine_sid},
                    )
            except Exception:  # noqa: BLE001
                pass
            trace.append(
                "model_used",
                {
                    "model_ref": dict(used_ref) if used_ref is not None else None,
                    "model_wire": model_str or None,
                    "source": (
                        "task_override"
                        if model_ref
                        else "specialist.current_model"
                        if specialist.model_ref
                        else "none"
                    ),
                },
            )
            if result.success:
                return result.output, None
            if probe.tool_started == 0 and not (result.output or "").strip():
                return (
                    None,
                    f"engine error before any work: "
                    f"{(result.error or 'unknown')[:200]}",
                )
            return (
                result.error
                or result.output
                or "[chat error: engine_empty_error]"
            ), None
        except Exception as e:  # noqa: BLE001 — fall back, never fail cryptic
            if probe.tool_started > 0:
                # The engine died AFTER tools ran (kill mid-turn with
                # partial work). Falling back would re-run those side
                # effects — surface the failure loudly instead.
                return (
                    f"[chat error: engine_failed_after_work: "
                    f"{type(e).__name__}: {str(e)[:200]} "
                    f"({probe.tool_started} tool(s) already ran; not "
                    f"falling back to avoid double-execution)]"
                ), None
            return None, f"{type(e).__name__}: {str(e)[:200]}"

    async def _run_opencode(
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
        # Thinking capture: optional callback invoked with each
        # ``reasoning`` part as it leaves the opencode stream.
        # The chat loop forwards these as ``chat.thinking`` WS
        # events so the UI can render a live Thinking block.
        # Reasoning never pollutes the returned text output.
        on_reasoning: "Callable[[str], Any] | None" = None,
    ) -> str:
        """Run one delegation on the opencode harness. Returns the
        agent's text output.

        Step 4: this is the fallback path. :meth:`run` dispatches
        here when opencode is selected — or when the native engine
        failed before doing any work (``fallback_used`` trace).
        
        The single-active-task queue per (specialist, worktree) is
        enforced by a per-key asyncio.Lock: concurrent calls for the
        same key serialise; different keys run in parallel.

        M1.7 step 1: ``session_id_getter`` / ``session_id_setter``
        let the orchestrator bind the durable opencode session id to
        the **Session** record (one per project × session) instead of
        the **Specialist** record. When None, the runtime reads/writes
        ``specialist.session_id`` (the M1.3 default).
        """
        # Turn-start clock for truthful stall errors (follow-up
        # hardening, incident Sweave-20260911-213619-096e65): passed
        # as ``t0`` so "stalled after Ns" also names the total age.
        t_start = asyncio.get_running_loop().time()
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

            # M2.1-follow-up: record which engine session runs this
            # delegation (display + forensics without trace-digging).
            # The id is resolved here; JobRunner persists it with the
            # result write. Best-effort: never fail a turn on it.
            try:
                _engine_sid = getattr(process, "_session_id", "") or ""
                if _engine_sid:
                    delegation.engine_session_id = _engine_sid
            except Exception:  # noqa: BLE001
                pass

            # M1.12 amendment 1: register (session → serve, worktree,
            # delegation) with the in-band permission bridge so the
            # hijack endpoint can resolve the serving serve + owning
            # chat delegation when opencode raises a permission ask.
            try:
                from sweave.runtime import permission_bridge

                permission_bridge.register_session(
                    str(getattr(process, "_session_id", "") or ""),
                    runner.base_url,
                    worktree_path,
                    delegation.delegation_id,
                )
            except Exception as reg_err:  # noqa: BLE001
                logger.warning(
                    "SpecialistRuntime: bridge session registration "
                    "failed: %s", reg_err,
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
                # Follow-up hardening (incident
                # Sweave-20260911-213619-096e65): the legacy bare
                # ``process.send`` hung 16m40s unwatched (httpx 1000s
                # only, result ignored) while the watchdog covered
                # just the main send. Bound + checked like every
                # other send; a failed identity prompt fails loudly
                # instead of running the turn anonymous.
                sys_err = await self._bounded_system_send(
                    process, rendered, trace, t0=t_start
                )
                if sys_err is not None:
                    return sys_err
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
            # M1.12: the delegation id rides along so the permission
            # ask-flow can create the blocking question record under
            # the same key the chat loop polls.
            result = await self._send_message(
                process,
                body,
                trace,
                on_chunk=on_chunk,
                on_reasoning=on_reasoning,
                delegation_id=delegation.delegation_id,
                t0=t_start,
            )
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

    async def _bounded_system_send(
        self,
        process: OpenCodeProcess,
        message_text: str,
        trace: TraceLog,
        t0: float | None = None,
    ) -> str | None:
        """Send a system message under the stall bound.

        The harness ``process.send`` path carries only the httpx
        timeout (1000s) and swallows failures into ``AgentResult``
        (incident Sweave-20260911-213619-096e65: 16m40s of silence
        on the templated-prompt send, then the turn proceeded
        anonymous). Returns None on delivery, or a ``[chat error:``
        string the caller must return (fail loudly, never anonymous).

        ``t0`` names the total turn age beside the silence window,
        like :meth:`_send_message`.

        Two-phase bound (incident 2026-09-13: a system send may run a
        FULL agent turn legitimately — tool calls, thinking — so a
        flat total cap kills honest work; and the hang that started
        this was zero-bytes-from-the-start):
        * first-byte bound: PRE_MODEL_TIMEOUT_SECONDS. Byte-silence
          from the very start is always wedged (httpx's own 1000s
          would kill it seconds later, invisibly).
        * after the first streamed byte the send runs on — the
          delegation's outer ``turn_timeout`` governs the rest, and
          harness-level bytes (tools, reasoning) keep it alive even
          when no text part has landed yet.
        """
        first_activity: asyncio.Event = asyncio.Event()

        def _touch(_part: str) -> None:
            first_activity.set()

        send_task = asyncio.create_task(
            process.send(_system_message(message_text), on_chunk=_touch)
        )

        try:
            await asyncio.wait_for(
                first_activity.wait(), timeout=PRE_MODEL_TIMEOUT_SECONDS
            )
            result = await send_task
        except asyncio.TimeoutError:
            send_task.cancel()
            try:
                trace.append(
                    "stalled",
                    {
                        "phase": "system_prompt",
                        "stall_seconds": PRE_MODEL_TIMEOUT_SECONDS,
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            age_suffix = ""
            if t0 is not None:
                try:
                    age_suffix = (
                        f"; turn age {asyncio.get_running_loop().time() - t0:.0f}s"
                    )
                except RuntimeError:
                    pass
            stop_suffix = await _attempt_engine_stop(
                process, getattr(process, "_session_id", "") or "", trace
            )
            return (
                f"[chat error: stalled after {PRE_MODEL_TIMEOUT_SECONDS:.0f}s "
                f"without data (system-prompt send hung with zero bytes; "
                f"the turn may still be running server-side; retry starts "
                f"a fresh session{age_suffix}{stop_suffix})]"
            )
        if not getattr(result, "success", True):
            err = getattr(result, "error", None) or "unknown error"
            try:
                trace.append("system_prompt_failed", {"error": str(err)})
            except Exception:  # noqa: BLE001
                pass
            return f"[chat error: system-prompt send failed: {err}]"
        return None

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
        on_reasoning: "Callable[[str], Any] | None" = None,
        stall_seconds: float | None = None,
        delegation_id: str | None = None,
        t0: float | None = None,
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

        Thinking capture: ``on_reasoning`` mirrors ``on_chunk`` for
        ``reasoning`` parts (extended-thinking models). Reasoning is
        traced (``reasoning`` events, like the harness's
        ``trace_reasoning`` path but always on here) and forwarded;
        it is never mixed into the returned text output.

        Stall watchdog: ``stall_seconds`` (default
        ``STALL_TIMEOUT_SECONDS``) bounds *silence*, not the turn: any
        received bytes reset the clock, so a slow-but-streaming turn
        keeps its full budget while a wedged one (hung tool approval,
        dead serve) fails fast with a truthful message instead of
        riding out ``turn_timeout`` -- or losing the race to httpx
        with a bare ``ReadTimeout``.

        ``t0`` (optional ``loop.time()`` captured by the caller at
        turn start) lets the stall errors name the total turn age
        beside the silence window -- "stalled after 300s" for a
        21-minute hang must read as such (incident
        Sweave-20260911-213619-096e65). Absent ``t0`` the legacy
        exact strings are preserved.
        """
        if stall_seconds is None:
            stall_seconds = STALL_TIMEOUT_SECONDS
        from sweave.harness.base import Message

        message = Message(type="user", content=str(body.get("parts", [{}])[0].get("text", "")))
        # We bypass OpenCodeProcess.send's body construction by using
        # _client.stream directly: send() rebuilds the body from spec.model,
        # which doesn't carry our structured ModelRef. The runtime owns
        # the model field (M1.3 K-revised); the harness just delivers.
        text_parts: list[str] = []
        # Thinking capture: reasoning parts accumulate here for
        # the trace; the live forwarding goes to on_reasoning.
        reasoning_parts: list[str] = []
        # M1.9 terminal + error tracking (mirrors
        # OpenCodeProcess.send): the v2 wire reports failures via
        # info.error on an otherwise-200 stream. Without this, an
        # upstream rejection (e.g. 401 CreditsError) arrives with
        # zero text parts and we returned "" as a SUCCESS -- the
        # chat loop persisted an empty assistant message and the UI
        # showed a turn that "did nothing" (2026-09-10 rerun
        # incident: two empty done turns, error visible only in
        # opencode's own sqlite db).
        saw_terminal = False
        info_error: str | None = None
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
            # Incident 2026-09-11 (17-min silent turn died on httpx
            # ReadTimeout; the stall watchdog never fired): the
            # watchdog below only wraps body chunks — a hang in
            # response-header wait sat outside it. Bound the open
            # too, and trace both phases so the next silent death is
            # classifiable from the trace alone.
            loop = asyncio.get_running_loop()
            raw_cm = process._client.stream(
                "POST",
                f"/session/{wire_session_id}/message",
                json=body,
                headers=headers,
            )
            t_open = loop.time()
            # Pre-model phase: legit serve warmup (tool-loop steps,
            # compaction, provider admission) can take minutes of
            # header silence. Bound it generously (PRE_MODEL), not at
            # the body-silence 300s — the 02:05 retry died exactly
            # this way (never reached the serve-side model).
            header_bound = PRE_MODEL_TIMEOUT_SECONDS

            class _AlreadyOpen:
                """Re-wrap a manually-entered stream CM for ``async with``.

                Lets the header wait carry its own stall bound while
                the body below keeps its exact shape (no re-indent,
                no behaviour change past the open).
                """

                def __init__(self, cm: Any, resp: Any) -> None:
                    self._cm = cm
                    self._resp = resp

                async def __aenter__(self) -> Any:
                    return self._resp

                async def __aexit__(self, *exc: Any) -> Any:
                    return await self._cm.__aexit__(*exc)

            try:
                _resp = await asyncio.wait_for(
                    raw_cm.__aenter__(), timeout=header_bound
                )
            except asyncio.TimeoutError:
                if trace is not None:
                    try:
                        trace.append(
                            "stalled",
                            {
                                "phase": "headers",
                                "stall_seconds": header_bound,
                                "wait_s": round(loop.time() - t_open, 1),
                            },
                        )
                    except Exception:
                        pass
                try:
                    await raw_cm.__aexit__(asyncio.TimeoutError, asyncio.TimeoutError(), None)
                except Exception:
                    pass
                age_suffix = (
                    f"; turn age {loop.time() - t0:.0f}s"
                    if t0 is not None
                    else ""
                )
                stop_suffix = await _attempt_engine_stop(
                    process, wire_session_id, trace
                )
                return (
                    f"[chat error: stalled after {header_bound:.0f}s without "
                    f"data (response headers never arrived; the turn may "
                    f"still be running server-side; retry starts a fresh session"
                    f"{age_suffix}{stop_suffix})]"
                )
            if trace is not None:
                try:
                    trace.append(
                        "stream_opened",
                        {"wait_s": round(loop.time() - t_open, 2)},
                    )
                except Exception:
                    pass
            async with _AlreadyOpen(raw_cm, _resp) as resp:
                resp.raise_for_status()
                # Stall watchdog: ANY bytes reset the clock, so a slow
                # but streaming turn keeps its full budget while a
                # silent one fails fast with a truthful message (no
                # more riding out turn_timeout in the dark, and no
                # more cryptic ReadTimeout when httpx wins the race).
                stream_iter = resp.aiter_text().__aiter__()
                carry = ""
                stalled = False
                first_byte_at: float | None = None
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            stream_iter.__anext__(), timeout=stall_seconds
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        stalled = True
                        break
                    if not chunk:
                        continue
                    # Incident 2026-09-11: first body bytes vs open
                    # distinguishes "headers hung" (no stream_opened /
                    # first_byte events) from "body dribbled then died".
                    if first_byte_at is None:
                        first_byte_at = loop.time()
                        if trace is not None:
                            try:
                                trace.append(
                                    "first_byte",
                                    {
                                        "latency_s": round(first_byte_at - t_open, 2),
                                    },
                                )
                            except Exception:
                                pass
                    pieces, carry = _split_json_stream(chunk, carry)
                    for piece in pieces:
                        try:
                            obj = __import__("json").loads(piece)
                        except __import__("json").JSONDecodeError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        info = obj.get("info")
                        info = info if isinstance(info, dict) else {}
                        # info.error is the canonical v2 error
                        # surface -- read it whenever present, not
                        # only on terminal turns, so a rejected turn
                        # can never pass as empty success.
                        err_obj = info.get("error")
                        if err_obj and info_error is None:
                            from sweave.harness.opencode import (
                                _format_info_error as _fmt_info_error,
                            )

                            info_error = _fmt_info_error(err_obj)
                        time_obj = info.get("time") or {}
                        if (
                            time_obj.get("completed") is not None
                            and info.get("finish")
                        ):
                            saw_terminal = True
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
                            elif isinstance(part, dict) and part.get("type") == "reasoning":
                                # Thinking capture: trace every
                                # reasoning part (the turn's audit
                                # trail, mirroring the harness's
                                # trace_reasoning events) and forward
                                # the incremental text to
                                # on_reasoning. Reasoning never lands
                                # in text_parts / the returned output.
                                rtext = part.get("text", "")
                                reasoning_parts.append(rtext)
                                try:
                                    trace.append("reasoning", {"text": rtext})
                                except Exception as trace_err:  # noqa: BLE001
                                    logger.warning(
                                        "SpecialistRuntime: reasoning "
                                        "trace failed: %s", trace_err
                                    )
                                if on_reasoning is not None:
                                    try:
                                        result = on_reasoning(rtext)
                                        if hasattr(result, "__await__"):
                                            await result
                                    except Exception as cb_err:  # noqa: BLE001
                                        logger.warning(
                                            "SpecialistRuntime: on_reasoning "
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
        if stalled:
            # Wire silence for stall_seconds (hung tool approval,
            # dead serve, wedged upstream): not an answer, even with
            # partial text -- persisting a fragment as success would
            # be worse than failing. The "stalled after" marker tells
            # the chat loop to rotate the engine session (a stalled
            # turn may still be running server-side and would poison
            # a retry on the same binding).
            got = sum(len(t) for t in text_parts)
            try:
                trace.append(
                    "stalled",
                    {"stall_seconds": stall_seconds, "partial_chars": got},
                )
            except Exception as trace_err:  # noqa: BLE001
                logger.warning(
                    "SpecialistRuntime: stalled "
                    "trace failed: %s", trace_err
                )
            # M1.12: a stalled turn is often a permission ask the
            # headless serve cannot answer. When the permission
            # machinery is wired, convert the pending ask into a
            # blocking human question and resume the turn (rulings:
            # route to the human for BOTH roles; no timeout):
            if self.escalation_store is not None and delegation_id:
                base_url = getattr(process, "base_url", "") or ""
                if base_url:
                    resolved = await self._resolve_pending_permission(
                        base_url=base_url,
                        session_id=wire_session_id,
                        delegation_id=delegation_id,
                        trace=trace,
                    )
                    if resolved is not None:
                        return resolved
            age_suffix = (
                f"; turn age {loop.time() - t0:.0f}s"
                if t0 is not None
                else ""
            )
            stop_suffix = await _attempt_engine_stop(
                process, wire_session_id, trace
            )
            return (
                f"[chat error: stalled after {stall_seconds:.0f}s without "
                f"data (the turn may still be running server-side; retry "
                f"starts a fresh session{age_suffix}{stop_suffix})]"
            )
        if info_error:
            # Upstream rejection carried on a 200 stream (401
            # CreditsError, ProviderAuthError, ...). Surface it with
            # the same "[chat error:" prefix the chat loop checks,
            # so the turn fails visibly instead of persisting "".
            try:
                trace.append("info_error", {"error": info_error})
            except Exception as trace_err:  # noqa: BLE001
                logger.warning(
                    "SpecialistRuntime: info_error "
                    "trace failed: %s", trace_err
                )
            return f"[chat error: {info_error}]"
        if not text_parts and not saw_terminal:
            # Stream ended with no text and no terminal flag --
            # mid-stream cut or empty response, never an answer.
            try:
                trace.append("incomplete_turn", {"chunks": 0, "length": 0})
            except Exception as trace_err:  # noqa: BLE001
                logger.warning(
                    "SpecialistRuntime: incomplete_turn "
                    "trace failed: %s", trace_err
                )
            return (
                "[chat error: opencode serve: incomplete turn "
                "(stream ended without info.time.completed + "
                "info.finish; mid-stream or empty response?)]"
            )
        trace.append("output_text", {"chunks": len(text_parts), "length": sum(len(t) for t in text_parts), "reasoning_chunks": len(reasoning_parts), "reasoning_length": sum(len(t) for t in reasoning_parts)})
        return "".join(text_parts)


    async def _recorded_hold(self, delegation_id: str) -> dict[str, Any] | None:
        """The recorded blocking question for a delegation, if still pending.

        Best-effort: no store / store error / no record / resolved
        record all mean "no hold" — callers fall back to their
        pre-hold behaviour.
        """
        store = getattr(self, "escalation_store", None)
        if store is None:
            return None
        try:
            rec = await store.get(delegation_id=delegation_id)
        except Exception:  # noqa: BLE001
            return None
        if not rec or rec.get("status") != "pending":
            return None
        return rec

    async def _resolve_pending_permission(
        self,
        *,
        base_url: str,
        session_id: str,
        delegation_id: str,
        trace: TraceLog,
    ) -> str | None:
        """Convert a pending opencode permission into a blocking human
        question and resume the turn (M1.12 step 2).

        Called from the ``stalled`` branch of ``_send_message`` when
        EITHER the watcher reports a pending ask for this engine
        session OR the store holds a recorded (bridge-owned) question
        for this delegation. Coherence rule (incident 2026-09-11):
        exactly one finder owns the reply POST — a second finder
        waits on the recorded hold (``permission_reused`` /
        ``permission_hold_wait``) instead of overwriting it, and the
        stall timer never fails a turn that the total budget is
        holding for a recorded question.
        Returns None when nothing answerable was found (caller falls
        back to the plain stall error); otherwise the recovered turn
        text — or a ``[chat error: ...]`` string when the human
        denied / the post-reply turn produced no content (loud
        failure, never silent).

        Wire facts (step-0 pinned, 1.18.29):
        * pending signal = bus ``permission.asked`` (no GET route
          exists — candidate routes return the SPA HTML catch-all);
        * reply = POST /session/{sid}/permissions/{rid}
          ``{"response": "once"|"always"|"reject"}`` -> 200 ``true``;
        * post-reply completion = bus ``session.idle``; the final
          text is fetched with GET /session/{sid}/message (the
          original message stream does not re-deliver terminal).
        """
        from sweave.runtime.permission_watch import (
            fetch_messages,
            final_assistant_text,
            get_permission_watcher,
            reply_permission_request,
        )

        watcher = get_permission_watcher(base_url)
        pending = watcher.pending_for(session_id)
        owned_reply = True
        recover_session_id = session_id
        if not pending:
            # No bus signal. A recorded hold owned by the other finder
            # (in-band bridge) still suspends us: wait for it, never
            # fail the turn for silence mid-wait. The total budget
            # already holds for recorded questions (_bounded_turn);
            # the stall timer must not contradict it (incident
            # 2026-09-11). The bridge owns the reply POST — we only
            # wait and recover.
            held = await self._recorded_hold(delegation_id)
            if held is None:
                # Late-answer check: the hold resolved just before we
                # looked (human answer racing the stall). One fetch —
                # no wait — recovers text that would otherwise die as
                # a stall despite the answer existing.
                try:
                    late = await self.escalation_store.get(
                        delegation_id=delegation_id
                    )
                except Exception:  # noqa: BLE001
                    late = None
                late_meta = ((late or {}).get("metadata") or {})
                if (
                    late is not None
                    and late.get("status") == "answered"
                    and str(late_meta.get("sessionID") or "") == session_id
                    and str(late_meta.get("requestID") or "")
                ):
                    try:
                        async with httpx.AsyncClient(timeout=15.0) as client:
                            late_messages = await fetch_messages(
                                client, base_url, session_id
                            )
                    except Exception:  # noqa: BLE001
                        late_messages = []
                    late_text = final_assistant_text(late_messages)
                    if late_text:
                        try:
                            trace.append(
                                "permission_recovered_late",
                                {
                                    "request_id": str(
                                        late_meta.get("requestID") or ""
                                    ),
                                    "chars": len(late_text),
                                },
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        return late_text
                return None
            meta = held.get("metadata") or {}
            request_id = str(meta.get("requestID") or "")
            permission = str(meta.get("permission") or "permission")
            patterns = list(meta.get("patterns") or [])
            command = str(meta.get("command", ""))
            recover_session_id = str(meta.get("sessionID") or session_id)
            owned_reply = False
        else:
            rec = pending[0]
            request_id = rec["id"]
            permission = rec["permission"]
            patterns = rec.get("patterns") or []
            command = str((rec.get("metadata") or {}).get("command", ""))

        summary = (
            f"opencode asks {permission} for {patterns}"
            + (f" (command: {command})" if command else "")
        )
        if owned_reply:
            # Blocking human question — no timeout (M1.11 ruling extends
            # to permission prompts; specialist prompts route to the
            # human too). Answered -> grant ("always" wording -> always),
            # skipped -> deny. The claim is atomic: a concurrent finder
            # (in-band bridge) racing us on the same ask reuses instead
            # of overwriting (incident 2026-09-11).
            try:
                _, created = await self.escalation_store.create_or_reuse(
                    delegation_id=delegation_id,
                    question=(
                        f"Permission required: {summary}. Answer 'allow once'"
                        f" / 'always allow' / 'deny' (or skip = deny)."
                    ),
                    options=["allow once", "always allow", "deny"],
                    kind="permission",
                    audience="human",
                    timeout_seconds=None,
                    metadata={
                        "requestID": request_id,
                        "sessionID": session_id,
                        "permission": permission,
                        "patterns": patterns,
                        "command": command,
                    },
                    reuse_request_id=request_id,
                )
            except Exception as esc_err:  # noqa: BLE001
                logger.warning(
                    "SpecialistRuntime: permission escalation create "
                    "failed: %s", esc_err,
                )
                return None
            owned_reply = bool(created)
            if not created:
                # Lost the race: the other finder recorded first and
                # owns the reply POST — wait on its record.
                try:
                    trace.append(
                        "permission_reused",
                        {"request_id": request_id},
                    )
                except Exception:  # noqa: BLE001
                    pass
        else:
            # Case D (recorded hold, no bus signal): the bridge owns
            # the reply — trace the wait, then fall into the shared
            # wait + resume-recovery below.
            try:
                trace.append(
                    "permission_hold_wait",
                    {"request_id": request_id},
                )
            except Exception:  # noqa: BLE001
                pass
        # Wait for the human resolution — unbounded by user ruling.
        esc: dict[str, Any] = {}
        while True:
            await asyncio.sleep(0.5)
            try:
                esc = (
                    await self.escalation_store.get(
                        delegation_id=delegation_id
                    )
                ) or {}
            except Exception:  # noqa: BLE001
                esc = {}
            if esc.get("status") != "pending":
                break
        status = str(esc.get("status", ""))
        response_text = str(esc.get("response", "") or "").strip()
        response_value = "reject"
        if status == "answered":
            low = response_text.lower()
            if "always" in low:
                response_value = "always"
            elif "deny" in low or "reject" in low or low == "no":
                response_value = "reject"
            else:
                response_value = "once"
        else:  # skipped / timeout => deny
            response_value = "reject"
        try:
            trace.append(
                "permission_answered",
                {
                    "request_id": request_id,
                    "status": status,
                    "reply": response_value,
                },
            )
        except Exception:  # noqa: BLE001
            pass
        if owned_reply:
            idle_baseline = watcher.idle_snapshot(session_id)
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    code = await reply_permission_request(
                        client, base_url, session_id, request_id, response_value
                    )
            except Exception as reply_err:  # noqa: BLE001
                logger.warning(
                    "SpecialistRuntime: permission reply POST failed: %s",
                    reply_err,
                )
                return (
                    f"[chat error: permission reply failed "
                    f"({response_value}): {type(reply_err).__name__}]"
                )
            if code != 200:
                return f"[chat error: permission reply rejected (HTTP {code})]"
            if response_value == "reject":
                return f"[chat error: permission denied: {summary}]"
        else:
            # The other finder owns the reply POST (double-POSTing the
            # same request id corrupts the serve's permission state):
            # on deny it posts reject itself — mirror the loud failure
            # without a second POST. On allow, fall through to the
            # shared resume-recovery below.
            if response_value == "reject":
                return f"[chat error: permission denied: {summary}]"
            idle_baseline = watcher.idle_snapshot(recover_session_id)
        # Allowed: wait for the resumed turn to finish, then recover
        # the final text from the message list (the original stream
        # does not re-deliver terminal).
        completed = await watcher.wait_idle(recover_session_id, idle_baseline)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                messages = await fetch_messages(client, base_url, recover_session_id)
        except Exception as fetch_err:  # noqa: BLE001
            logger.warning(
                "SpecialistRuntime: post-permission message fetch "
                "failed: %s", fetch_err,
            )
            return (
                f"[chat error: stalled after permission allow "
                f"(fetch failed: {type(fetch_err).__name__})]"
            )
        recovered = final_assistant_text(messages)
        if not completed:
            try:
                trace.append(
                    "permission_resume_timeout",
                    {"request_id": request_id},
                )
            except Exception:  # noqa: BLE001
                pass
            return (
                "[chat error: opencode serve: incomplete turn "
                "(permission allowed but the resumed turn never "
                "signalled completion; mid-stream or empty response?)]"
            )
        if not recovered:
            return (
                "[chat error: opencode serve: incomplete turn "
                "(permission allowed, turn completed with no text)]"
            )
        try:
            trace.append(
                "permission_recovered",
                {"request_id": request_id, "chars": len(recovered)},
            )
        except Exception:  # noqa: BLE001
            pass
        return recovered


def _system_message(content: str) -> "Message":
    """Build a system-role :class:`Message`."""
    from sweave.harness.base import Message

    return Message(type="system", content=content)


def _split_json_stream(
    chunk: str, carry: str = ""
) -> tuple[list[str], str]:
    """Same helper as in sweave/harness/opencode.py — split a chunk on
    the closing brace of the outermost JSON object. Returns
    ``(complete_pieces, leftover)``; feed ``leftover`` back as
    ``carry`` so objects split across chunks are glued, not dropped."""
    pieces: list[str] = []
    text = carry + chunk
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
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
                pieces.append(text[start : i + 1])
                start = -1
    leftover = text[start:] if (start >= 0 and depth > 0) else ""
    return pieces, leftover
