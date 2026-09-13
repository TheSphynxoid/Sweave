"""Sweave-native engine harness adapter (step 1: chat path, no tools).

Speaks the versioned engine protocol (``sweave/engine/protocol.py``)
to the ``sweave-engine/`` sidecar over localhost HTTP/SSE. Mirrors the
: meth:`OpenCodeProcess.send` contract (``message.model`` wins,
optional ``on_chunk`` per token, ``tokens_used`` trace anchor) so the
runtime can drive either harness identically.

Version discipline (wire-drift ruling): the protocol version header
is checked on EVERY turn; a mismatch raises :class:`ProtocolMismatch`
at connect, never fails a turn cryptically.

Sidecar lifecycle: one shared sidecar per process, started lazily
(``node <root>/sweave-engine/src/serve.js --port 0`` with
``SWEAVE_ENGINE_PORT=N`` discovery on stdout). ``SWEAVE_ENGINE_URL``
points at an existing sidecar (tests + operators);
``SWEAVE_ENGINE_BIN`` overrides the serve script path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from sweave.engine.protocol import (
    ENGINE_HARNESS_NAME,
    PROTOCOL_VERSION_HEADER,
    TOOL_BASELINE,
    ProtocolMismatch,
    check_protocol_version,
    validate_run_request,
)
from sweave.harness.base import (
    AgentProcess,
    AgentResult,
    AgentSpec,
    Harness,
    Message,
    harness_registry,
)

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _serve_script() -> Path:
    override = os.environ.get("SWEAVE_ENGINE_BIN")
    if override:
        return Path(override)
    return _repo_root() / "sweave-engine" / "src" / "serve.js"


@dataclass
class _Sidecar:
    base_url: str
    process: "asyncio.subprocess.Process | None" = None


_sidecar: _Sidecar | None = None
_sidecar_lock = asyncio.Lock()


async def _ensure_sidecar() -> _Sidecar:
    """Return the shared sidecar, starting it on first use."""
    global _sidecar
    existing_url = os.environ.get("SWEAVE_ENGINE_URL")
    if existing_url:
        return _Sidecar(base_url=existing_url.rstrip("/"))
    async with _sidecar_lock:
        if _sidecar is not None:
            return _sidecar
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("sweave-engine: node not found on PATH")
        script = _serve_script()
        if not script.is_file():
            raise RuntimeError(f"sweave-engine: serve script missing: {script}")
        proc = await asyncio.create_subprocess_exec(
            node,
            str(script),
            "--port",
            "0",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ},
        )
        assert proc.stdout is not None
        base_url = None
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=15.0)
            text = line.decode("utf-8", errors="replace").strip()
            if text.startswith("SWEAVE_ENGINE_PORT="):
                base_url = f"http://127.0.0.1:{text.split('=', 1)[1]}"
        except (asyncio.TimeoutError, ValueError):
            base_url = None
        if base_url is None:
            try:
                proc.kill()
            except ProcessLookupError:  # noqa: BLE001
                pass
            raise RuntimeError(
                "sweave-engine: sidecar did not report a port within 15s"
            )
        _sidecar = _Sidecar(base_url=base_url, process=proc)
        return _sidecar


def _check_version(headers: Any) -> None:
    remote = headers.get(PROTOCOL_VERSION_HEADER)
    check_protocol_version(remote)


class SweaveEngineProcess:
    """Handle to one engine session (chat path)."""

    def __init__(
        self,
        spec: AgentSpec,
        base_url: str,
        session_id: str,
    ):
        self.spec = spec
        self.base_url = base_url
        self._session_id = session_id
        self.pid = -1  # no per-session subprocess; the sidecar is shared
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60.0)
        self.trace_reasoning = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self._session_id = value

    async def _ensure_session(self) -> str:
        if not self._session_id:
            self._session_id = f"eng_{uuid.uuid4().hex[:12]}"
        return self._session_id

    async def send(
        self,
        message: Message,
        on_chunk: "Callable[[str], Any] | None" = None,
        trace: Any = None,
        trace_reasoning: bool = False,
    ) -> AgentResult:
        """POST /run and stream native tokens (same shape as opencode send).

        ``message.model`` (a ModelRef) wins over ``spec.model`` for
        this one turn; ``spec.model`` accepts the same
        ``provider/model`` string form the rest of Sweave uses and is
        split into the structured pair here. ``on_chunk`` fires per
        token (sync or async). ``trace`` receives the ``tokens_used``
        terminal anchor verbatim (tool events arrive with step 2).
        """
        try:
            session_id = await self._ensure_session()
            ref = message.model or _parse_spec_model(self.spec.model)
            turn_timeout = _turn_timeout(message)
            body = {
                "session_id": session_id,
                "composed_prompt": message.content,
                "tools": list(self.spec.tools) if self.spec.tools else [],
                "permission_map": dict(
                    message.metadata.get("permission_map", {})
                ),
                "model": dict(ref) if ref else {},
                "turn_timeout": turn_timeout,
                "cwd": str(self.spec.worktree_path or ""),
            }
            # Step-2 additive passthrough (runtime-owned; absent keeps
            # step-1 behavior): delegation_id links sweave-tool calls,
            # role gates which sweave tools the loop offers.
            delegation_id = message.metadata.get("delegation_id")
            if isinstance(delegation_id, str) and delegation_id:
                body["delegation_id"] = delegation_id
            role = message.metadata.get("role")
            if role in ("orchestrator", "specialist"):
                body["role"] = role
            try:
                validate_run_request(body)
            except ValueError as e:
                return AgentResult(
                    success=False, output="", error=f"engine bad_request: {e}"
                )
            output_parts: list[str] = []
            try:
                async with self._client.stream(
                    "POST", "/run", json=body, timeout=turn_timeout + 30.0
                ) as resp:
                    _check_version(resp.headers)
                    if resp.status_code == 409:
                        detail = (await resp.aread()).decode(
                            "utf-8", errors="replace"
                        )
                        return AgentResult(
                            success=False,
                            output="",
                            error=f"engine turn_active: {detail[:200]}",
                        )
                    if resp.status_code == 400:
                        detail = (await resp.aread()).decode(
                            "utf-8", errors="replace"
                        )
                        return AgentResult(
                            success=False,
                            output="",
                            error=f"engine bad_request: {detail[:300]}",
                        )
                    resp.raise_for_status()
                    # Drain to EOF: the sidecar sends tokens_used AFTER
                    # done, so breaking on done would drop the anchor.
                    # The stream always ends right after (httpx timeout
                    # is the backstop against a misbehaving server).
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except (ValueError, json.JSONDecodeError):
                            continue
                        kind = event.get("event")
                        if kind == "token":
                            text = event.get("text", "")
                            output_parts.append(text)
                            if on_chunk is not None:
                                try:
                                    result = on_chunk(text)
                                    if hasattr(result, "__await__"):
                                        await result
                                except Exception as cb_err:  # noqa: BLE001
                                    logger.warning(
                                        "SweaveEngineProcess.send: on_chunk "
                                        "callback raised: %s",
                                        cb_err,
                                    )
                        elif kind in (
                            "tool.started",
                            "tool.updated",
                            "tool.completed",
                            "tool.failed",
                            "step.boundary",
                            "permission.asked",
                            "tokens_used",
                        ):
                            # Frozen-vocabulary trace parity with the
                            # opencode adapter: identical event names so
                            # `sweave log` and DetailView work unchanged.
                            if trace is not None:
                                try:
                                    trace.append(
                                        kind,
                                        {
                                            k: v
                                            for k, v in event.items()
                                            if k != "event"
                                        },
                                    )
                                except Exception as trace_err:  # noqa: BLE001
                                    logger.warning(
                                        "SweaveEngineProcess.send: %s "
                                        "trace failed: %s",
                                        kind,
                                        trace_err,
                                    )
                        elif kind == "error":
                            code = event.get("code", "error")
                            msg = event.get("message", "")
                            return AgentResult(
                                success=False,
                                output="".join(output_parts).strip(),
                                error=f"[chat error: {code}: {msg}]",
                            )
                        elif kind == "done":
                            # Turn complete — keep draining: tokens_used
                            # follows done on the wire, then EOF ends us.
                            pass
            except ProtocolMismatch:
                raise
            except httpx.HTTPStatusError as e:
                upstream = (
                    e.response.text.strip()
                    if e.response is not None
                    else ""
                )
                return AgentResult(
                    success=False,
                    output="",
                    error=(
                        f"sweave-engine "
                        f"{e.response.status_code if e.response else '?'}: "
                        f"{upstream or str(e)}"
                    ),
                )
            output = "".join(output_parts).strip()
            if not output:
                return AgentResult(
                    success=False,
                    output="",
                    error="[chat error: incomplete_turn: engine returned no text]",
                )
            return AgentResult(success=True, output=output)
        except ProtocolMismatch:
            raise
        except Exception as e:  # noqa: BLE001
            return AgentResult(
                success=False, output="", error=f"[chat error: {type(e).__name__}: {e}]"
            )

    async def terminate(self) -> None:
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass

    async def wait(self) -> AgentResult:
        return AgentResult(success=True, output="")


def _parse_spec_model(spec_model: str | None) -> dict[str, str] | None:
    """Split a ``provider/model[+variant]`` string into a ModelRef."""
    if not spec_model or "/" not in spec_model:
        return None
    provider, rest = spec_model.split("/", 1)
    ref: dict[str, str] = {"provider": provider, "model_id": rest}
    if "+" in rest:
        model_id, variant = rest.rsplit("+", 1)
        ref = {"provider": provider, "model_id": model_id, "variant": variant}
    return ref


def _turn_timeout(message: Message) -> float:
    try:
        value = float(message.metadata.get("turn_timeout", 1800.0))
    except (TypeError, ValueError):
        return 1800.0
    return value if value > 0 else 1800.0


class SweaveEngineHarness(Harness):
    """The native engine as a second Harness (best-offer, step-4 flip)."""

    name = ENGINE_HARNESS_NAME

    async def spawn(self, spec: AgentSpec) -> AgentProcess:
        sidecar = await _ensure_sidecar()
        return SweaveEngineProcess(
            spec=spec,
            base_url=sidecar.base_url,
            session_id=f"eng_{uuid.uuid4().hex[:12]}",
        )

    async def attach(self, session_id: str, spec: AgentSpec) -> AgentProcess:
        sidecar = await _ensure_sidecar()
        return SweaveEngineProcess(
            spec=spec, base_url=sidecar.base_url, session_id=session_id
        )

    def get_default_tools(self) -> list[str]:
        # Execution-tool baseline only. Sweave-native tools
        # (defer/list/ask/escalate) are granted structurally per
        # role in step 2, never via this list.
        return list(TOOL_BASELINE)

    async def health_check(self) -> bool:
        try:
            sidecar = await _ensure_sidecar()
            async with httpx.AsyncClient(
                base_url=sidecar.base_url, timeout=5.0
            ) as client:
                resp = await client.get("/health")
                _check_version(resp.headers)
                return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False


harness_registry.register(SweaveEngineHarness())
