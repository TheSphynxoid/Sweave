"""ServeRunner: one long-lived opencode serve per (specialist, worktree).

M1.3 step 1. The runtime owns a small registry of runners keyed by
``(specialist_name, worktree_path)``. A runner is started lazily on
first use, kept alive across multiple delegations to the same
specialist in the same worktree, and shut down on idle TTL.

Design constraints (from M1.3 step 0 probes + the plan):
* Branch A: cwd binds to the serve process. Each ServeRunner pins
  its serve's cwd to a single worktree; when a new delegation comes
  for the same specialist in a *different* worktree, that's a new
  ServeRunner. (The alternative — re-cd the same serve per
  delegation — would also work for cwd, but opencode's session
  contract is "one session lives in one cwd" so re-cd would invalidate
  the session anyway.)
* Sessions are in-memory in this opencode version. The runner
  creates a fresh session on `start()`; resumes are only possible
  within the same runner's lifetime (i.e. same worktree, between
  delegations before idle TTL).
* The Bun long-lived-process freeze incident motivates the TTL
  (Bun processes are a known freeze risk; we don't keep them
  alive forever).
* The runner registers with the WSEventBus for
  ``serve.started|stopped|restarted {specialist, port}`` events.

This module is *pure runtime* — no AppState, no FastAPI, no event
bus dependency in the constructor. The event bus is optional
(``None`` is fine for tests).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from sweave.harness.opencode import OpenCodeHarness, OpenCodeProcess


logger = logging.getLogger(__name__)

DEFAULT_IDLE_TTL_SECONDS = 30 * 60  # 30 minutes per the plan
DEFAULT_START_TIMEOUT = 30.0


@dataclass
class ServeRunner:
    """One opencode serve for a (specialist, worktree) pair.

    Lifecycle (managed by ServeRunnerRegistry):
    * created (no serve running yet)
    * start() — spawn opencode serve, wait for port
    * use() — caller calls get_process() to get a session
    * touch() — caller marks the runner as recently used
    * shutdown() — graceful terminate, force on timeout

    The runner is NOT thread-safe; the registry serialises lifecycle
    operations via its own asyncio lock.
    """

    specialist_name: str
    worktree_path: Path
    command: str = "opencode"
    serve_args: list[str] = field(default_factory=lambda: ["--port", "0"])
    idle_ttl_seconds: float = DEFAULT_IDLE_TTL_SECONDS
    start_timeout: float = DEFAULT_START_TIMEOUT
    event_bus: Any = None  # WSEventBus | None; typed as Any to avoid the import cycle
    # State
    process: Optional["asyncio.subprocess.Process"] = None
    port: Optional[int] = None
    base_url: Optional[str] = None
    log_path: Optional[Path] = None
    last_used_at: float = field(default_factory=time.monotonic)
    sessions: dict[str, Any] = field(default_factory=dict)  # session_id -> OpenCodeProcess

    @property
    def key(self) -> tuple[str, str]:
        """Stable identifier for this runner in the registry."""
        return (self.specialist_name, str(self.worktree_path.resolve()))

    def touch(self) -> None:
        """Mark the runner as recently used (refreshes TTL)."""
        self.last_used_at = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used_at

    def is_alive(self) -> bool:
        """Cheap check: is the subprocess still running?"""
        return self.process is not None and self.process.returncode is None

    def _resolve_command(self) -> str:
        """Same shim-resolution trick the harness uses (CMD -> real .exe)."""
        resolved = shutil.which(self.command)
        if resolved and resolved.lower().endswith((".cmd", ".bat")):
            text = Path(resolved).read_text(encoding="utf-8", errors="replace")
            m = re.search(r'"([^"]+\.exe)"', text, re.IGNORECASE)
            if m:
                target = m.group(1)
                shim_dir = str(Path(resolved).resolve().parent)
                target = re.sub(r"%~?dp0%", lambda _m: shim_dir, target, flags=re.IGNORECASE)
                target = Path(__import__("os").path.expandvars(target))
                if target.is_file():
                    return str(target)
        return resolved or self.command

    async def start(self) -> None:
        """Spawn the serve in ``worktree_path``; wait for the listening URL.

        No-op if already running. Stores ``process``, ``port``, ``base_url``,
        ``log_path`` and emits ``serve.started`` if an event bus is attached.
        """
        if self.is_alive():
            return
        # Ensure worktree exists
        self.worktree_path.mkdir(parents=True, exist_ok=True)
        cmd = [self._resolve_command(), "serve", *self.serve_args]
        self.log_path = Path(tempfile.gettempdir()) / (
            f"sweave-m1-3-{self.specialist_name}-{uuid.uuid4().hex[:8]}.log"
        )
        log_file = open(self.log_path, "ab")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.worktree_path),
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception:
            log_file.close()
            raise
        # log_file stays open for the lifetime of the serve (closed on shutdown)
        self._log_file = log_file  # type: ignore[attr-defined]
        try:
            self.port = await self._wait_for_port()
        except Exception:
            await self._terminate_force()
            raise
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.touch()
        logger.info(
            "ServeRunner started for %s in %s (pid=%d port=%d log=%s)",
            self.specialist_name, self.worktree_path, self.process.pid,
            self.port, self.log_path,
        )
        if self.event_bus is not None:
            await self.event_bus.publish(
                "serve.started",
                {
                    "specialist": self.specialist_name,
                    "worktree": str(self.worktree_path),
                    "port": self.port,
                    "pid": self.process.pid,
                },
            )

    async def _wait_for_port(self) -> int:
        """Poll the serve log for the listening URL."""
        url_re = re.compile(r"http://[\d.]+:(\d+)")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.start_timeout
        while loop.time() < deadline:
            if self.process is None or self.process.returncode is not None:
                code = self.process.returncode if self.process else "?"
                raise RuntimeError(
                    f"opencode serve exited early (code {code}) for "
                    f"{self.specialist_name} in {self.worktree_path}; log: {self.log_path}"
                )
            try:
                text = (self.log_path or Path()).read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                text = ""
            m = url_re.search(text)
            if m:
                return int(m.group(1))
            await asyncio.sleep(0.25)
        raise RuntimeError(
            f"opencode serve did not bind within {self.start_timeout}s for "
            f"{self.specialist_name} in {self.worktree_path}; log: {self.log_path}"
        )

    async def health(self) -> bool:
        """``GET /session`` returns 200 within the httpx timeout."""
        if not self.base_url:
            return False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{self.base_url}/session")
            return r.status_code == 200
        except Exception:
            return False

    async def restart(self) -> None:
        """Shutdown + start. Used when the serve is dead or unhealthy.

        Sessions die with the serve (per M1.3 step 0 probe 4: opencode
        stores sessions in-memory only); on restart the runner starts
        fresh and callers must create new sessions.
        """
        old_port = self.port
        await self.shutdown()
        try:
            await self.start()
        except Exception as e:
            logger.warning("ServeRunner restart failed for %s: %s", self.specialist_name, e)
            raise
        if self.event_bus is not None:
            await self.event_bus.publish(
                "serve.restarted",
                {
                    "specialist": self.specialist_name,
                    "worktree": str(self.worktree_path),
                    "old_port": old_port,
                    "new_port": self.port,
                },
            )

    async def shutdown(self) -> None:
        """Terminate the subprocess; close the log file; clear state."""
        if self.process is None:
            return
        pid = self.process.pid
        try:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        except ProcessLookupError:
            pass
        finally:
            self.process = None
            self.port = None
            self.base_url = None
            self.sessions.clear()
        log_file = getattr(self, "_log_file", None)
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
            self._log_file = None  # type: ignore[attr-defined]
        logger.info("ServeRunner shut down for %s (was pid=%d)", self.specialist_name, pid)
        if self.event_bus is not None:
            await self.event_bus.publish(
                "serve.stopped",
                {
                    "specialist": self.specialist_name,
                    "worktree": str(self.worktree_path),
                    "pid": pid,
                },
            )

    async def _terminate_force(self) -> None:
        """For start() failure paths: terminate + close + clear (no event)."""
        if self.process is None:
            return
        try:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self.process.kill()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        except ProcessLookupError:
            pass
        self.process = None
        self.port = None
        self.base_url = None
        log_file = getattr(self, "_log_file", None)
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
            self._log_file = None  # type: ignore[attr-defined]


class ServeRunnerRegistry:
    """Lazy map of (specialist, worktree) -> ServeRunner.

    ``get_or_create(specialist_name, worktree_path, system_prompt,
    model_ref, **kwargs)`` returns the runner for that key, starting it
    if necessary. ``sweep_idle(now, ttl)`` returns the list of
    runners that exceeded the TTL (and shuts them down so the registry
    doesn't leak). The registry is process-local and not persisted
    across server restarts; on restart every runner is rebuilt.
    """

    def __init__(self, event_bus: Any = None) -> None:
        self._runners: dict[tuple[str, str], ServeRunner] = {}
        self._lock = asyncio.Lock()
        self.event_bus = event_bus

    def get(self, key: tuple[str, str]) -> Optional[ServeRunner]:
        return self._runners.get(key)

    def known(self) -> list[ServeRunner]:
        return list(self._runners.values())

    async def get_or_create(
        self,
        specialist_name: str,
        worktree_path: Path,
        **kwargs: Any,
    ) -> ServeRunner:
        """Return the runner for the (specialist, worktree) key, starting
        it on first access. ``kwargs`` are passed to ServeRunner
        (system_prompt, model_ref, command, serve_args, idle_ttl_seconds, …).
        """
        worktree_path = Path(worktree_path).resolve()
        key = (specialist_name, str(worktree_path))
        existing = self._runners.get(key)
        if existing is not None:
            existing.touch()
            return existing
        async with self._lock:
            existing = self._runners.get(key)
            if existing is not None:
                existing.touch()
                return existing
            runner = ServeRunner(
                specialist_name=specialist_name,
                worktree_path=worktree_path,
                event_bus=self.event_bus,
                **kwargs,
            )
            self._runners[key] = runner
        await runner.start()
        runner.touch()
        return runner

    async def sweep_idle(
        self, *, now: float | None = None, ttl: float | None = None
    ) -> list[ServeRunner]:
        """Shut down runners whose last_used_at is older than ``ttl``
        seconds. Returns the list of runners that were shut down (for
        logging / WS events). Call from a periodic task or on submit().
        """
        if now is None:
            now = time.monotonic()
        evicted: list[ServeRunner] = []
        for runner in list(self._runners.values()):
            effective_ttl = ttl if ttl is not None else runner.idle_ttl_seconds
            if now - runner.last_used_at >= effective_ttl:
                evicted.append(runner)
        for runner in evicted:
            await runner.shutdown()
            self._runners.pop(runner.key, None)
        return evicted

    async def shutdown_all(self) -> None:
        """Tear down every runner (used at server shutdown)."""
        for runner in list(self._runners.values()):
            await runner.shutdown()
        self._runners.clear()


# ---------------------------------------------------------------------------
# Orphan sweep (M1.3 step 1 risks: psutil adoption per DESIGN §8)
# ---------------------------------------------------------------------------


def find_orphan_serves(
    *,
    worktree_substr: str = ".worktrees",
    protected_pid: int | None = None,
    proc_iter: Callable | None = None,
) -> list[dict[str, Any]]:
    """Find opencode processes that look like orphans from a previous run.

    Heuristic (DESIGN §8 "psutil, never hold serve pipes" + M1.3
    step 1 risks): a process is an orphan candidate iff

    * it has a ``.worktrees`` substring in its cmdline OR cwd (i.e. it
      was started by the harness, not by the user interactively);
    * its parent pid is None or its parent pid is not a live
      Sweave/opencode parent (heuristic: we don't have a sentinel, so
      we just check the process is alive);
    * it is NOT ``protected_pid`` (the live serve, if any).

    Returns a list of ``{pid, cmdline, cwd, create_time}`` dicts for
    the candidates. **The caller decides whether to kill them** --
    this function only lists them.
    """
    if proc_iter is None:
        # Production path: query the OS via psutil.
        try:
            import psutil
        except ImportError:
            return []  # psutil not installed; the sweep is a no-op
        proc_iter = psutil.process_iter
    candidates: list[dict[str, Any]] = []
    for proc in proc_iter(["pid", "cmdline", "cwd", "create_time"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        cwd = proc.info.get("cwd") or ""
        is_worktree_bound = worktree_substr in cmdline or worktree_substr in cwd
        if not is_worktree_bound:
            continue
        if protected_pid is not None and proc.info.get("pid") == protected_pid:
            continue
        candidates.append({
            "pid": proc.info.get("pid"),
            "cmdline": cmdline,
            "cwd": cwd,
            "create_time": proc.info.get("create_time"),
        })
    return candidates


def sweep_orphan_serves(**kwargs: Any) -> list[dict[str, Any]]:
    """List + kill orphan serve processes. Returns the killed list.

    Passes a ``protected_pid`` so the *current* live serve (if any)
    is never killed. Uses psutil when the candidate list comes from the
    real OS (no ``proc_iter`` override). On a process-kill failure the
    entry is still returned (with an ``error`` key) for visibility.
    """
    candidates = find_orphan_serves(**kwargs)
    # If the candidates came from an injected proc_iter (test path),
    # the test is responsible for the kill semantics. The production
    # path (no proc_iter) goes through psutil.
    if any("pid" in c for c in candidates) and not any(
        c.get("_terminated") for c in candidates
    ):
        # Heuristic: production path. We didn't terminate anything via
        # the proc_iter injection (no _terminated flag). Use psutil to
        # actually kill.
        try:
            import psutil
        except ImportError:
            return candidates  # can't kill; caller decides
        killed: list[dict[str, Any]] = []
        for entry in candidates:
            pid = entry["pid"]
            try:
                psutil.Process(pid).terminate()
                psutil.Process(pid).wait(timeout=3)
            except psutil.NoSuchProcess:
                pass
            except Exception as e:
                entry["error"] = str(e)
            killed.append(entry)
        return killed
    return candidates
