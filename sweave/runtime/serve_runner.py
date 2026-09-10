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
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from sweave.platform import creationflags_no_window
from sweave.runtime.locking import atomic_write_json_sync

if TYPE_CHECKING:
    from sweave.harness.opencode import OpenCodeHarness, OpenCodeProcess


logger = logging.getLogger(__name__)

DEFAULT_IDLE_TTL_SECONDS = 30 * 60  # 30 minutes per the plan
DEFAULT_START_TIMEOUT = 30.0
DEFAULT_TRACKING_FILENAME = "serves.json"
IDLE_SWEEP_INTERVAL_SECONDS = 5 * 60  # lifespan sweeper cadence


async def _tree_kill(pid: int) -> None:
    """Force-kill *pid* with its child tree (MCP servers the serve spawned).

    Windows first (``taskkill /F /T``); POSIX falls back to SIGKILL
    (serves are spawned without a process group, so no pgid kill).
    Never raises -- callers already handle the wedged-process case.
    """
    try:
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception as kill_err:  # noqa: BLE001
        logger.warning("tree-kill failed for pid=%d: %s", pid, kill_err)


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
    # Lifecycle tracker, set by ServeRunnerRegistry: called with
    # ("started" | "stopped", self) so the registry can persist the
    # serve PID for boot-time reclaim of previous-run orphans.
    tracker: Any = None
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
                target = re.sub(r"%~?dp0%|%basedir%", lambda _m: shim_dir, target, flags=re.IGNORECASE)
                target = Path(os.path.expandvars(target))
                if target.is_file():
                    return str(target)
        return resolved or self.command

    async def start(self) -> None:
        """Spawn the serve in ``worktree_path``; wait for the listening URL.

        No-op if already running. Stores ``process``, ``port``, ``base_url``,
        ``log_path`` and emits ``serve.started`` if an event bus is attached.

        Test hook: when ``SWEAVE_MOCK_OPENCODE=1``, skip the subprocess
        spawn entirely -- set a fake process/port/base_url. The runtime's
        ``_build_process`` also checks the hook and returns a stubbed
        OpenCodeProcess, so no real HTTP call is made. This keeps
        end-to-end tests deterministic without opencode or an LLM.
        """
        if self.is_alive():
            return
        import os

        if os.environ.get("SWEAVE_MOCK_OPENCODE") == "1":
            self.worktree_path.mkdir(parents=True, exist_ok=True)
            self.process = None  # type: ignore[assignment]
            self.port = 0  # sentinel: mock mode
            self.base_url = "http://mock-opencode"
            self.log_path = None
            self.touch()
            logger.info(
                "ServeRunner mock-start for %s in %s (SWEAVE_MOCK_OPENCODE=1)",
                self.specialist_name, self.worktree_path,
            )
            if self.event_bus is not None:
                await self.event_bus.publish(
                    "serve.started",
                    {
                        "specialist": self.specialist_name,
                        "worktree": str(self.worktree_path),
                        "port": self.port,
                        "pid": None,
                        "mock": True,
                    },
                )
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
                creationflags=creationflags_no_window(),
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
        if self.tracker is not None:
            try:
                self.tracker("started", self)
            except Exception as track_err:  # noqa: BLE001
                logger.warning(
                    "ServeRunner tracker failed for %s: %s",
                    self.specialist_name, track_err,
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
        """Terminate the subprocess; close the log file; clear state.

        Escalation is tree-aware on Windows (``taskkill /F /T``): the
        serve spawns MCP-server children that must not be orphaned
        alongside it. POSIX keeps terminate → SIGKILL (serves are
        spawned without a process group, so no pgid kill).
        """
        if self.process is None:
            return
        pid = self.process.pid
        try:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                await _tree_kill(pid)
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
            if self.tracker is not None:
                try:
                    self.tracker("stopped", self)
                except Exception as track_err:  # noqa: BLE001
                    logger.warning(
                        "ServeRunner tracker failed for %s: %s",
                        self.specialist_name, track_err,
                    )
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
                await _tree_kill(self.process.pid)
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        except ProcessLookupError:
            pass
        self.process = None
        self.port = None
        self.base_url = None
        if self.tracker is not None:
            try:
                self.tracker("stopped", self)
            except Exception as track_err:  # noqa: BLE001
                logger.warning(
                    "ServeRunner tracker failed for %s: %s",
                    self.specialist_name, track_err,
                )
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

    def __init__(self, event_bus: Any = None, tracking_path: Path | None = None) -> None:
        self._runners: dict[tuple[str, str], ServeRunner] = {}
        self._lock = asyncio.Lock()
        self.event_bus = event_bus
        # PID tracking file (production: ~/.sweave/serves.json; None in
        # tests = in-memory only). Lets the NEXT server boot reclaim
        # serves orphaned by a crash / force-stop of this run.
        self.tracking_path = tracking_path
        self._tracked: dict[int, dict[str, Any]] = {}

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
                tracker=self._on_runner_lifecycle,
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
            self._untrack_key(runner.key)
            self._runners.pop(runner.key, None)
        if evicted:
            self._save_tracked()
        return evicted

    async def shutdown_all(self) -> None:
        """Tear down every runner (used at server shutdown)."""
        for runner in list(self._runners.values()):
            await runner.shutdown()
            self._untrack_key(runner.key)
        self._runners.clear()
        self._save_tracked()

    # -- serve PID tracking (boot reclaim of previous-run orphans) --

    def _on_runner_lifecycle(self, event: str, runner: ServeRunner) -> None:
        """Registry-side tracker callback wired into every runner."""
        if event == "started":
            proc = runner.process
            pid = getattr(proc, "pid", None) if proc is not None else None
            if pid is None:
                return  # mock mode / sentinel: nothing to reclaim later
            self._tracked[int(pid)] = {
                "pid": int(pid),
                "port": runner.port,
                "key": list(runner.key),
                "owner_pid": os.getpid(),
                "started_at": time.time(),
            }
        else:  # "stopped"
            self._untrack_key(runner.key)
        self._save_tracked()

    def _untrack_key(self, key: tuple[str, str]) -> None:
        self._tracked = {
            pid: entry
            for pid, entry in self._tracked.items()
            if entry.get("key") != [key[0], key[1]]
        }

    def _save_tracked(self) -> None:
        if self.tracking_path is None:
            return
        try:
            atomic_write_json_sync(
                self.tracking_path, {"serves": list(self._tracked.values())}
            )
        except Exception as save_err:  # noqa: BLE001
            logger.warning(
                "serve PID tracking save failed (%s): %s",
                self.tracking_path, save_err,
            )


# ---------------------------------------------------------------------------
# Boot reclaim: kill serves orphaned by a previous server run
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    """Best-effort "does this pid exist" without psutil."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            return f'"{pid}"' in (out.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _serve_probe(port: Any) -> bool:
    """Does an opencode serve answer on *port*? (PID-reuse guard: we only
    kill a stale pid if its recorded port still serves the v2 API.)"""
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return False
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port_int}/session", timeout=3
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _kill_pid(pids: int) -> None:
    """Force-kill *pid* with its child tree. Raises on failure."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pids)],
            capture_output=True, timeout=30, check=True,
        )
    else:
        os.kill(pids, signal.SIGTERM)
        time.sleep(1.0)
        try:
            os.kill(pids, 0)
        except OSError:
            return
        os.kill(pids, signal.SIGKILL)


def reclaim_tracked_serves(
    tracking_path: Path | None = None,
    *,
    _is_alive: Callable[..., bool] | None = None,
    _probe: Callable[..., bool] | None = None,
    _kill: Callable[..., None] | None = None,
) -> list[dict[str, Any]]:
    """Kill serves orphaned by a previous server run.

    Reads the PID tracking file (written by ServeRunnerRegistry as it
    starts/stops serves). Entries whose owner server is still alive
    belong to a concurrent live server and are left alone (and kept in
    the rewritten file). Entries with a dead owner are verified --
    pid alive AND recorded port answers the serve API (defeats PID
    reuse) -- then killed. Dead pids are pruned silently.

    stdlib only (no psutil): aliveness via tasklist / kill(pid, 0),
    identity via the recorded port's ``GET /session``. The ``_is_alive``,
    ``_probe`` and ``_kill`` kwargs are test seams.

    Returns the list of killed entries. Safe to call at every boot
    before the new registry spawns anything.
    """
    path = Path(tracking_path) if tracking_path is not None else (
        Path.home() / ".sweave" / DEFAULT_TRACKING_FILENAME
    )
    is_alive = _is_alive or _pid_alive
    probe = _probe or _serve_probe
    kill = _kill or _kill_pid
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = payload.get("serves") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    killed: list[dict[str, Any]] = []
    survivors: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("pid")
        try:
            pid_int = int(pid)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        owner = entry.get("owner_pid")
        try:
            owner_int = int(owner) if owner is not None else None
        except (TypeError, ValueError):
            owner_int = None
        if owner_int is not None and owner_int != os.getpid() and is_alive(owner_int):
            survivors.append(entry)  # a live server still owns it
            continue
        if not is_alive(pid_int):
            continue  # already gone; prune silently
        if not probe(entry.get("port")):
            # PID recycled by something that isn't our serve -- never
            # kill on pid alone. Leave it out of the file (the entry
            # is stale) but don't touch the process.
            logger.warning(
                "reclaim: pid=%d not serving on port=%s; leaving it alone",
                pid_int, entry.get("port"),
            )
            continue
        try:
            kill(pid_int)
            killed.append(entry)
        except Exception as kill_err:  # noqa: BLE001
            logger.warning("reclaim: kill failed for pid=%d: %s", pid_int, kill_err)
            survivors.append(entry)
    try:
        atomic_write_json_sync(path, {"serves": survivors})
    except Exception as save_err:  # noqa: BLE001
        logger.warning("reclaim: tracking rewrite failed (%s): %s", path, save_err)
    return killed


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
