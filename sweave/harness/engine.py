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
    PROTOCOL_VERSION,
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
from sweave.platform import creationflags_no_window

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
            # Windows: a flagless node.exe owns a visible console
            # window (the "CMD opens on every test try" report,
            # 2026-09-14). Every other spawn site already goes
            # through creationflags_no_window (platform.py).
            creationflags=creationflags_no_window(),
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
        _write_sidecar_tracking(proc.pid, base_url)
        return _sidecar


def _check_version(headers: Any) -> None:
    remote = headers.get(PROTOCOL_VERSION_HEADER)
    check_protocol_version(remote)


# ---------------------------------------------------------------------------
# Sidecar lifetime: tracking file + boot sweep + shutdown reap.
#
# The sidecar is one shared node process per server run, spawned lazily
# on the first engine turn. Two leaks stacked node processes in task
# managers (2026-09-14: a pile of "Node.js" entries surviving server
# stops): (1) graceful stops (Ctrl+C lifespan shutdown, which reaps
# opencode serves but never touched the sidecar) and (2) hard kills
# (console close / crash — CREATE_NO_WINDOW children aren't console
# attached, so they outlive the server and every restart spawned a
# fresh one). The tracking file + sweep mirror the opencode
# ``serves.json`` reclaim (same owner_pid + probe guards, same
# never-kill-foreign-process rule).
# ---------------------------------------------------------------------------

#: PID tracking file for the shared sidecar (``~/.sweave/``).
ENGINE_SIDECAR_TRACKING_FILENAME = "engine-sidecar.json"


def _sidecar_tracking_path() -> Path:
    return Path.home() / ".sweave" / ENGINE_SIDECAR_TRACKING_FILENAME


def _write_sidecar_tracking(pid: int, base_url: str) -> None:
    """Record our sidecar (best-effort; a missing file just skips the sweep)."""
    try:
        from sweave.runtime.locking import atomic_write_text_sync

        port = int(base_url.rsplit(":", 1)[1].rstrip("/"))
        atomic_write_text_sync(
            _sidecar_tracking_path(),
            json.dumps({"pid": pid, "port": port, "owner_pid": os.getpid()}),
        )
    except Exception as track_err:  # noqa: BLE001
        logger.warning("sidecar tracking write failed: %s", track_err)


def _read_sidecar_tracking(tracking_path: Path | None = None) -> dict[str, Any] | None:
    """Parse the tracking file, or None when absent/unusable."""
    path = Path(tracking_path) if tracking_path is not None else _sidecar_tracking_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _prune_sidecar_tracking(
    tracking_path: Path | None = None, *, expected_pid: int | None = None
) -> None:
    """Delete the tracking file (only when it still names our pid, when given)."""
    path = Path(tracking_path) if tracking_path is not None else _sidecar_tracking_path()
    try:
        if expected_pid is not None:
            current = _read_sidecar_tracking(path)
            if not isinstance(current, dict) or int(current.get("pid", -1)) != expected_pid:
                return  # another run already rewrote it; leave it alone
        path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def _pid_alive(pid: int) -> bool:
    """Best-effort "does this pid exist" without psutil."""
    try:
        if os.name == "nt":
            import subprocess

            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return f'"{pid}"' in (out.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _sidecar_cmdline(pid: int) -> str | None:
    """Command line of *pid*, or None when unreadable.

    Identity guard before killing: a stale pid may have been recycled
    by an unrelated process (possibly another Node app). Unknown
    means "don't touch" — the sweep leaves it alone.
    """
    try:
        if os.name == "nt":
            import subprocess

            # CIM first (wmic is an optional feature on recent Win11).
            out = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | "
                    "Select-Object -ExpandProperty CommandLine)",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            text = (out.stdout or "").strip()
            if text:
                return text
            out = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}",
                 "get", "CommandLine", "/value"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return (out.stdout or "").strip() or None
        data = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
        return data.strip() or None
    except Exception:
        return None


def _sidecar_health(port: Any) -> tuple[bool, str | None]:
    """Does an engine sidecar answer ``GET /health`` on *port*?

    Returns ``(healthy, protocol_version)``. Version comes from the
    version header the sidecar sends on every response (adopting a
    foreign-version sidecar would fail every turn loudly at connect
    instead — better to start our own).
    """
    try:
        port_int = int(port)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False, None
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port_int}/health", timeout=3
        ) as resp:
            if resp.status != 200:
                return False, None
            return True, resp.headers.get(PROTOCOL_VERSION_HEADER)
    except Exception:
        return False, None


def _kill_sidecar_pid(pid: int) -> None:
    """Force-kill *pid* with its child tree. Raises on failure."""
    if os.name == "nt":
        import subprocess

        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=30,
            check=True,
        )
    else:
        import signal
        import time

        os.kill(pid, signal.SIGTERM)
        time.sleep(1.0)
        try:
            os.kill(pid, 0)
        except OSError:
            return
        os.kill(pid, signal.SIGKILL)


def sweep_stale_sidecar(
    tracking_path: Path | None = None,
    *,
    _is_alive: Any | None = None,
    _health: Any | None = None,
    _cmdline: Any | None = None,
    _kill: Any | None = None,
) -> str | None:
    """Adopt or reap the previous run's sidecar. Returns an adopted base_url or None.

    Read the PID tracking file left by the last server run that
    spawned a sidecar:

    * no/invalid file → None (nothing to do; the next engine turn
      spawns fresh).
    * recorded port serves ``/health`` at our protocol version → adopt
      it (return the base URL; the caller caches it as the shared
      sidecar). Covers crash survivors AND a concurrent live server's
      sidecar — one sidecar per home dir, never two, and never a
      kill of a process another run still uses.
    * owner server alive (and not us) but the port doesn't serve us →
      None (their process, their problem — spawn our own, touch nothing).
    * otherwise, pid alive AND its command line is our serve script →
      kill the stale predecessor, prune the file, return None.
    * pid dead, recycled (cmdline is something else), or unreadable →
      prune-or-leave without killing: never kill on pid alone.

    stdlib only. The ``_*`` kwargs are test seams. Safe to call at
    every boot before the first engine turn.
    """
    path = Path(tracking_path) if tracking_path is not None else _sidecar_tracking_path()
    is_alive = _is_alive or _pid_alive
    health = _health or _sidecar_health
    cmdline = _cmdline or _sidecar_cmdline
    kill = _kill or _kill_sidecar_pid
    entry = _read_sidecar_tracking(path)
    if entry is None:
        # Absent, corrupt, or non-mapping: nothing adoptable. Prune a
        # corrupt file so every boot doesn't re-read it (atomic
        # writes mean a torn file can't be a concurrent writer).
        _prune_sidecar_tracking(path)
        return None
    try:
        pid = int(entry.get("pid"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        _prune_sidecar_tracking(path)
        return None
    port = entry.get("port")
    base_url = (
        f"http://127.0.0.1:{int(port)}"
        if isinstance(port, int) or (isinstance(port, str) and str(port).isdigit())
        else None
    )
    if base_url is not None:
        healthy, version = health(port)
        if healthy and version == PROTOCOL_VERSION:
            logger.info(
                "sidecar sweep: adopting live sidecar pid=%d on %s",
                pid, base_url,
            )
            _adopt_sidecar(base_url)
            return base_url
    try:
        owner = entry.get("owner_pid")
        owner_int = int(owner) if owner is not None else None
    except (TypeError, ValueError):
        owner_int = None
    if (
        owner_int is not None
        and owner_int != os.getpid()
        and is_alive(owner_int)
    ):
        return None  # a live server still owns it; touch nothing
    if is_alive(pid):
        cmd = cmdline(pid)
        ours = bool(cmd) and _serve_script().name in cmd
        if ours:
            try:
                kill(pid)
                logger.info("sidecar sweep: reaped stale sidecar pid=%d", pid)
            except Exception as kill_err:  # noqa: BLE001
                logger.warning(
                    "sidecar sweep: kill failed for pid=%d: %s", pid, kill_err
                )
                return None
        else:
            logger.warning(
                "sidecar sweep: pid=%d alive but not our sidecar; leaving it alone",
                pid,
            )
    _prune_sidecar_tracking(path)
    return None


def _adopt_sidecar(base_url: str) -> None:
    """Cache an externally-owned live sidecar (no process handle to reap)."""
    global _sidecar
    _sidecar = _Sidecar(base_url=base_url, process=None)


async def shutdown_sidecar() -> str:
    """Terminate the shared sidecar this process spawned (server shutdown).

    Graceful terminate, force-kill on timeout; prunes our tracking
    file when it still names our pid. Adopted (externally-owned),
    URL-configured, or absent sidecars are left alone — ``"not_ours"``.
    Never raises; returns ``"stopped"`` | ``"not_ours"``.
    """
    global _sidecar
    current = _sidecar
    if current is None or current.process is None:
        return "not_ours"
    proc = current.process
    try:
        pid: int | None = proc.pid
    except Exception:  # noqa: BLE001
        pid = None
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (asyncio.TimeoutError, TimeoutError):
            try:
                proc.kill()
            except ProcessLookupError:  # noqa: BLE001
                pass
            await asyncio.gather(proc.wait(), return_exceptions=True)
    except (ProcessLookupError, AttributeError):  # noqa: BLE001
        pass
    except Exception as stop_err:  # noqa: BLE001
        logger.warning("sidecar shutdown failed: %s", stop_err)
    _sidecar = None
    _prune_sidecar_tracking(expected_pid=pid)
    return "stopped"


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
        on_reasoning: "Callable[[str], Any] | None" = None,
        on_tool: "Callable[[dict[str, Any]], Any] | None" = None,
    ) -> AgentResult:
        """POST /run and stream native tokens (same shape as opencode send).

        ``message.model`` (a ModelRef) wins over ``spec.model`` for
        this one turn; ``spec.model`` accepts the same
        ``provider/model`` string form the rest of Sweave uses and is
        split into the structured pair here. ``on_chunk`` fires per
        token (sync or async). ``on_reasoning`` mirrors it for
        ``reasoning`` events (engine thinking text, protocol v2):
        traced like the opencode path, never mixed into the output.
        ``on_tool`` mirrors both for ``tool.started | updated |
        completed | failed`` SSE (chat transparency): one normalized
        event per transition (see ``sweave.chat.tools.tool_event``);
        sync or async; a raising callback is logged, never fatal.
        ``trace`` receives the ``tokens_used`` terminal anchor
        verbatim (tool events arrive with step 2).
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
            # Retry budget (turn_retries setting): retries AFTER the
            # first provider attempt; absent keeps the sidecar default
            # (3). Only plain ints ride the wire (validated again
            # sidecar-side by validateRun).
            _retries = message.metadata.get("max_retries")
            if isinstance(_retries, bool):
                pass
            elif isinstance(_retries, int) and _retries >= 0:
                body["max_retries"] = _retries
            # Step-2 additive passthrough (runtime-owned; absent keeps
            # step-1 behavior): delegation_id links sweave-tool calls,
            # role gates which sweave tools the loop offers.
            delegation_id = message.metadata.get("delegation_id")
            if isinstance(delegation_id, str) and delegation_id:
                body["delegation_id"] = delegation_id
            # Project root for bounding always-grants to the project
            # subtree (2026-09-15 ruling). Absent keeps the old
            # exact-path behavior — fail closed. Tolerated-and-ignored
            # by older sidecars (forward compatibility).
            project_dir = message.metadata.get("project_dir")
            if isinstance(project_dir, str) and project_dir:
                body["project_dir"] = project_dir
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
            # No-rotation invariant: the turn's prompt id names the unit
            # a later /revert rewrites (protocol v3). Captured from the
            # `done` event into the result metadata — the runtime traces
            # it per delegation so edit-rerun can rewrite history.
            user_message_id: str | None = None
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
                        elif kind == "reasoning":
                            # Thinking capture (protocol v2, same
                            # contract as opencode reasoning parts):
                            # traced + forwarded, never output.
                            rtext = event.get("text", "")
                            if trace is not None:
                                try:
                                    trace.append("reasoning", {"text": rtext})
                                except Exception as trace_err:  # noqa: BLE001
                                    logger.warning(
                                        "SweaveEngineProcess.send: reasoning "
                                        "trace failed: %s",
                                        trace_err,
                                    )
                            if on_reasoning is not None:
                                try:
                                    result = on_reasoning(rtext)
                                    if hasattr(result, "__await__"):
                                        await result
                                except Exception as cb_err:  # noqa: BLE001
                                    logger.warning(
                                        "SweaveEngineProcess.send: on_reasoning "
                                        "callback raised: %s",
                                        cb_err,
                                    )
                        elif kind in (
                            "tool.started",
                            "tool.updated",
                            "tool.completed",
                            "tool.failed",
                        ):
                            # Frozen-vocabulary trace parity with the
                            # opencode adapter: identical event names so
                            # `sweave log` and DetailView work unchanged.
                            # The same transitions feed on_tool (chat
                            # transparency) -- never gated on tracing.
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
                            if on_tool is not None:
                                from sweave.chat.tools import tool_event

                                try:
                                    _ev = tool_event(
                                        event.get("callID"),
                                        event.get("tool"),
                                        event.get("state"),
                                    )
                                    _result = on_tool(_ev)
                                    if hasattr(_result, "__await__"):
                                        await _result
                                except Exception as cb_err:  # noqa: BLE001
                                    logger.warning(
                                        "SweaveEngineProcess.send: on_tool "
                                        "callback raised: %s",
                                        cb_err,
                                    )
                        elif kind in (
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
                            err_result = AgentResult(
                                success=False,
                                output="".join(output_parts).strip(),
                                error=f"[chat error: {code}: {msg}]",
                            )
                            if user_message_id:
                                err_result.metadata["user_message_id"] = user_message_id
                            return err_result
                        elif kind == "done":
                            # Turn complete — keep draining: tokens_used
                            # follows done on the wire, then EOF ends us.
                            if isinstance(event.get("user_message_id"), str):
                                user_message_id = event["user_message_id"]
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
            result = AgentResult(success=True, output=output)
            if user_message_id:
                result.metadata["user_message_id"] = user_message_id
            return result
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

    async def abort_turn(self, session_id: str) -> bool:
        """POST /abort to the sidecar for *session_id* (best-effort).

        Returns True when the sidecar stopped (``acknowledged``) or
        attempted the stop (``UNCONFIRMED`` — the asyncio cancel the
        caller also issues is the real guarantee); False when there
        is no live turn (409), no sidecar, or the call fails. Never
        starts a sidecar just to abort: no sidecar means no live
        turn by definition.
        """
        import os

        existing_url = os.environ.get("SWEAVE_ENGINE_URL")
        if existing_url:
            base_url = existing_url.rstrip("/")
        else:
            if _sidecar is None:
                return False
            base_url = _sidecar.base_url
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
                resp = await client.post("/abort", json={"session_id": session_id})
            if resp.status_code == 200:
                return True
            return False
        except Exception as abort_err:  # noqa: BLE001
            logger.warning(
                "SweaveEngineHarness.abort_turn failed for %s: %s",
                session_id, abort_err,
            )
            return False


async def abort_engine_session(engine_session_id: str | None) -> bool:
    """Best-effort abort of one live engine turn (user-stop assist).

    Returns True when the sidecar was asked to stop; False when
    there is nothing to stop (no id, non-engine id, no harness) or
    the abort failed. Never raises — the asyncio task cancel the
    caller also issues is the real guarantee; the kill path (signal
    into tools, bash child kill) settles the sidecar turn promptly.
    No-rotation invariant: the caller keeps the session binding
    regardless — a stop kills the work, never the conversation.
    """
    if not engine_session_id or not engine_session_id.startswith("eng_"):
        return False
    try:
        harness = harness_registry.get(ENGINE_HARNESS_NAME)
        if harness is None:
            return False
        return await harness.abort_turn(engine_session_id)
    except Exception as abort_err:  # noqa: BLE001
        logger.warning(
            "abort_engine_session failed for %s: %s",
            engine_session_id, abort_err,
        )
        return False


async def revert_engine_session(
    engine_session_id: str | None, before_message_id: str
) -> tuple[bool, str]:
    """Rewrite engine history: drop *before_message_id* and everything
    after it (edit = history rewrite, never session rotation).

    Returns ``(True, "reverted")`` or ``(False, reason)``. Spawns the
    shared sidecar when needed (a rewrite targets an idle session, so
    starting the sidecar is safe — unlike abort, which never spawns).
    Never raises.
    """
    if not engine_session_id or not engine_session_id.startswith("eng_"):
        return False, "not_engine_id"
    if not before_message_id:
        return False, "no_message_id"
    try:
        sidecar = await _ensure_sidecar()
    except Exception as exc:  # noqa: BLE001
        return False, f"sidecar_unavailable: {type(exc).__name__}"
    try:
        async with httpx.AsyncClient(
            base_url=sidecar.base_url, timeout=15.0
        ) as client:
            resp = await client.post(
                "/revert",
                json={
                    "session_id": engine_session_id,
                    "before_message": before_message_id,
                },
            )
        if resp.status_code == 200:
            return True, "reverted"
        return False, f"revert_rejected_{resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "revert_engine_session failed for %s: %s",
            engine_session_id, exc,
        )
        return False, f"revert_failed: {type(exc).__name__}"


harness_registry.register(SweaveEngineHarness())
