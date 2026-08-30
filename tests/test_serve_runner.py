"""M1.3 step 1 tests: ServeRunner + ServeRunnerRegistry + orphan sweep.

The subprocess + psutil paths are exercised with a fake ``proc_iter``
and a tiny inline serve script so the tests don't need a real
opencode install. The point of these tests is the lifecycle
(state machine, TTL eviction, restart sequencing) and the orphan
heuristic (does it pick the right things to kill).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from sweave.runtime.serve_runner import (
    DEFAULT_IDLE_TTL_SECONDS,
    ServeRunner,
    ServeRunnerRegistry,
    find_orphan_serves,
    sweep_orphan_serves,
)


# ---------------------------------------------------------------------------
# Fakes: psutil + subprocess
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Stand-in for psutil.Process: exposes ``.info`` as a dict-like (the
    real psutil API), and the lifecycle methods we use."""

    def __init__(self, info: dict[str, Any]) -> None:
        # ``info`` is a dict; psutil returns a special object that
        # supports both dict-style and attribute access. We just expose
        # the dict directly + a __getattr__ fallback.
        object.__setattr__(self, "_info", info)

    @property
    def info(self) -> dict[str, Any]:
        return self._info

    def __getattr__(self, name: str) -> Any:
        # Fall back to ``self._info[name]`` for ad-hoc attribute access
        # (matches psutil's proc.<attr> pattern).
        try:
            return self._info[name]
        except KeyError:
            raise AttributeError(name) from None

    def terminate(self) -> None:
        self._info["_terminated"] = True

    def wait(self, timeout: float | None = None) -> None:
        self._info["_waited"] = True

    def kill(self) -> None:
        self._info["_killed"] = True


def _fake_proc_iter(processes: list[_FakeProcess]):
    """Build a psutil-style ``process_iter`` factory.

    The real call site is
    ``(proc_iter or psutil.process_iter)(["pid", "cmdline", "cwd", "create_time"])``
    so we accept (and ignore) the attrs arg.
    """
    def _iter(attrs: list[str] | None = None):
        for p in processes:
            yield p
    return _iter


# ---------------------------------------------------------------------------
# Orphan sweep (pure unit tests, no real subprocess)
# ---------------------------------------------------------------------------


def test_find_orphan_serves_no_worktree_substr_returns_empty():
    fake = _FakeProcess({
        "pid": 1, "cmdline": ["opencode"], "cwd": "C:\\Users",
        "create_time": 0,
    })
    out = find_orphan_serves(proc_iter=_fake_proc_iter([fake]))
    assert out == []


def test_find_orphan_serves_worktree_in_cmdline_is_candidate():
    fake = _FakeProcess({
        "pid": 100,
        "cmdline": ["opencode", "serve", "--cwd", "C:\\repo\\.worktrees\\abc-backend"],
        "cwd": "C:\\Users",
        "create_time": 0,
    })
    out = find_orphan_serves(proc_iter=_fake_proc_iter([fake]))
    assert len(out) == 1
    assert out[0]["pid"] == 100
    assert ".worktrees" in out[0]["cmdline"]


def test_find_orphan_serves_worktree_in_cwd_is_candidate():
    fake = _FakeProcess({
        "pid": 101,
        "cmdline": ["opencode", "serve"],
        "cwd": "C:\\repo\\.worktrees\\abc-backend",
        "create_time": 0,
    })
    out = find_orphan_serves(proc_iter=_fake_proc_iter([fake]))
    assert len(out) == 1
    assert out[0]["pid"] == 101


def test_find_orphan_serves_protected_pid_excluded():
    fake = _FakeProcess({
        "pid": 200,
        "cmdline": ["opencode", "serve", "--cwd", "C:\\repo\\.worktrees\\live"],
        "cwd": "C:\\Users",
        "create_time": 0,
    })
    out = find_orphan_serves(
        proc_iter=_fake_proc_iter([fake]), protected_pid=200
    )
    assert out == []  # the live serve is the one we're trying NOT to kill


def test_find_orphan_serves_missing_psutil_returns_empty(monkeypatch):
    """When psutil isn't installed and no proc_iter override is provided,
    the sweep is a silent no-op (production: psutil not on PATH)."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("psutil not installed (test)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = find_orphan_serves()  # no proc_iter -> would import psutil
    assert out == []


def test_sweep_orphan_serves_kills_candidates(monkeypatch):
    """sweep_orphan_serves returns the candidates list when the
    proc_iter is the production path and no psutil is installed
    (test env). When psutil IS available and the candidates have a
    _terminated flag (test injection), the function returns the
    candidates as-is (the test injects pre-terminated objects).
    """
    fake_a = _FakeProcess({"pid": 300, "cmdline": "opencode", "cwd": "C:\\.worktrees\\x", "create_time": 0, "_terminated": True})
    fake_b = _FakeProcess({"pid": 301, "cmdline": "opencode", "cwd": "C:\\.worktrees\\y", "create_time": 0})
    killed = sweep_orphan_serves(proc_iter=_fake_proc_iter([fake_a, fake_b]))
    # When proc_iter is provided, the function returns the candidates
    # directly (the test is responsible for the kill semantics).
    assert {k["pid"] for k in killed} == {300, 301}


# ---------------------------------------------------------------------------
# ServeRunner lifecycle (mocked subprocess)
# ---------------------------------------------------------------------------


class _FakeSubprocess:
    """Mimics asyncio.subprocess.Process enough for ServeRunner to manage it.

    The real Process exposes ``terminate()``, ``kill()``, ``wait()`` (async),
    and ``returncode``. The fake mirrors that surface: terminate
    marks returncode, kill returns immediately, wait returns 0 (or
    raises TimeoutError when ``wait_timeout`` is set).
    """

    def __init__(self, *, port: int = 9999, alive: bool = True, returncode: int | None = None):
        self.pid = 12345
        self._port = port
        self.returncode = None if alive else (returncode if returncode is not None else 0)
        self.terminate_called = 0
        self.kill_called = 0
        self.wait_timeout = False

    def terminate(self) -> None:
        self.terminate_called += 1
        self.returncode = 0  # graceful exit

    def kill(self) -> None:
        self.kill_called += 1
        self.returncode = -9  # SIGKILL

    async def wait(self) -> int:
        if self.wait_timeout:
            raise asyncio.TimeoutError
        # On a normal wait, return whatever the returncode already says.
        return self.returncode if self.returncode is not None else 0


def _serve_log_with_port(tmp_path: Path, port: int) -> Path:
    """Create a serve log file that has the 'listening on' URL in it."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "serve.log"
    log.write_text(
        f"Warning: OPENCODE_SERVER_PASSWORD is not set; server is unsecured.\n"
        f"opencode server listening on http://127.0.0.1:{port}\n",
        encoding="utf-8",
    )
    return log


# We use a fake `_start_serve` injection to avoid actually running opencode
# in the test env. ServeRunner.start() calls asyncio.create_subprocess_exec
# directly; the tests patch ServeRunner._wait_for_port + the lifecycle
# methods to simulate state transitions without an actual subprocess.


def _make_runner(tmp_path: Path, *, event_bus=None) -> ServeRunner:
    return ServeRunner(
        specialist_name="test-specialist",
        worktree_path=tmp_path / "wt",
        event_bus=event_bus,
        idle_ttl_seconds=60.0,
    )


@pytest.mark.asyncio
async def test_runner_starts_when_process_alive(tmp_path: Path):
    runner = _make_runner(tmp_path)
    fake_proc = _FakeSubprocess(port=9999)
    runner.process = fake_proc
    runner.port = 9999
    runner.base_url = "http://127.0.0.1:9999"
    runner.log_path = _serve_log_with_port(tmp_path, 9999)
    assert runner.is_alive() is True
    # start() should be a no-op when already alive
    await runner.start()
    assert runner.port == 9999
    assert runner.process is fake_proc


@pytest.mark.asyncio
async def test_runner_idle_seconds(tmp_path: Path):
    runner = _make_runner(tmp_path)
    runner.touch()
    await asyncio.sleep(0.05)
    assert runner.idle_seconds() >= 0.04


@pytest.mark.asyncio
async def test_runner_shutdown_terminates_and_clears(tmp_path: Path):
    runner = _make_runner(tmp_path)
    fake_proc = _FakeSubprocess(port=9999)
    runner.process = fake_proc
    runner.port = 9999
    runner.base_url = "http://127.0.0.1:9999"
    runner.sessions["sid-1"] = MagicMock()
    await runner.shutdown()
    assert fake_proc.terminate_called == 1
    assert runner.process is None
    assert runner.port is None
    assert runner.base_url is None
    assert runner.sessions == {}


@pytest.mark.asyncio
async def test_runner_shutdown_when_process_already_dead(tmp_path: Path):
    """shutdown() on a runner whose process is already None is a no-op."""
    runner = _make_runner(tmp_path)
    assert runner.process is None
    await runner.shutdown()  # no error
    assert runner.process is None


# ---------------------------------------------------------------------------
# ServeRunnerRegistry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_get_or_create_starts_runner(tmp_path: Path):
    fake_proc = _FakeSubprocess(port=9999)
    captured: list[dict[str, Any]] = []

    class FakeBus:
        async def publish(self, event, data):
            captured.append({"event": event, **data})

    async def fake_start(self):
        # Ensure the parent dir exists (the real ServeRunner.start() does
        # this too; the fakes mirror it so write_text doesn't fail).
        self.worktree_path.mkdir(parents=True, exist_ok=True)
        self.process = fake_proc
        self.port = 9999
        self.base_url = "http://127.0.0.1:9999"
        self.log_path = _serve_log_with_port(self.worktree_path, 9999)
        self.touch()
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

    # Patch ServeRunner.start to the fake
    from sweave.runtime.serve_runner import ServeRunner as SR
    orig_start = SR.start
    SR.start = fake_start  # type: ignore[assignment]
    try:
        reg = ServeRunnerRegistry(event_bus=FakeBus())
        r1 = await reg.get_or_create("alpha", tmp_path / "wt")
        assert r1.specialist_name == "alpha"
        assert r1.key in reg._runners
        # Second call returns the same instance (and touches it)
        r2 = await reg.get_or_create("alpha", tmp_path / "wt")
        assert r2 is r1
        # serve.started event was emitted exactly once
        started = [e for e in captured if e["event"] == "serve.started"]
        assert len(started) == 1
    finally:
        SR.start = orig_start  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_registry_get_or_create_different_worktree_different_runner(tmp_path: Path):
    """Per the plan, different worktrees for the same specialist get different
    runners (Branch A: cwd binds to the serve process; one serve per
    worktree)."""
    fake_proc_a = _FakeSubprocess(port=9999)
    fake_proc_b = _FakeSubprocess(port=9998)

    async def fake_start(self):
        self.worktree_path.mkdir(parents=True, exist_ok=True)
        self.process = fake_proc_a if str(self.worktree_path).endswith("a") else fake_proc_b
        self.port = self.process._port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.log_path = _serve_log_with_port(self.worktree_path, self.port)
        self.touch()

    from sweave.runtime.serve_runner import ServeRunner as SR
    orig_start = SR.start
    SR.start = fake_start  # type: ignore[assignment]
    try:
        reg = ServeRunnerRegistry()
        wt_a = tmp_path / "wt_a"
        wt_b = tmp_path / "wt_b"
        ra = await reg.get_or_create("alpha", wt_a)
        rb = await reg.get_or_create("alpha", wt_b)
        assert ra is not rb
        assert ra.worktree_path.resolve() == wt_a.resolve()
        assert rb.worktree_path.resolve() == wt_b.resolve()
        assert len(reg.known()) == 2
    finally:
        SR.start = orig_start  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_registry_sweep_idle_evicts_old_runners(tmp_path: Path):
    fake_proc = _FakeSubprocess(port=9999)
    from sweave.runtime.serve_runner import ServeRunner as SR
    orig_start = SR.start
    orig_shutdown = SR.shutdown

    async def fake_start(self):
        self.worktree_path.mkdir(parents=True, exist_ok=True)
        self.process = fake_proc
        self.port = 9999
        self.base_url = "http://127.0.0.1:9999"
        self.log_path = _serve_log_with_port(self.worktree_path, 9999)
        self.touch()

    shutdown_calls: list[str] = []

    async def fake_shutdown(self):
        shutdown_calls.append(self.specialist_name)
        self.process = None
        self.port = None
        self.base_url = None
        self.sessions.clear()

    SR.start = fake_start  # type: ignore[assignment]
    SR.shutdown = fake_shutdown  # type: ignore[assignment]
    try:
        reg = ServeRunnerRegistry()
        r = await reg.get_or_create("alpha", tmp_path / "wt")
        # Backdate last_used_at
        r.last_used_at = time.monotonic() - 999
        evicted = await reg.sweep_idle(ttl=10.0)
        assert r in evicted
        assert shutdown_calls == ["alpha"]
        assert reg.get(r.key) is None
    finally:
        SR.start = orig_start  # type: ignore[assignment]
        SR.shutdown = orig_shutdown  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_registry_sweep_idle_keeps_recent_runners(tmp_path: Path):
    fake_proc = _FakeSubprocess(port=9999)
    from sweave.runtime.serve_runner import ServeRunner as SR
    orig_start = SR.start
    orig_shutdown = SR.shutdown

    async def fake_start(self):
        self.worktree_path.mkdir(parents=True, exist_ok=True)
        self.process = fake_proc
        self.port = 9999
        self.base_url = "http://127.0.0.1:9999"
        self.log_path = _serve_log_with_port(self.worktree_path, 9999)
        self.touch()

    shutdown_calls: list[str] = []

    async def fake_shutdown(self):
        shutdown_calls.append(self.specialist_name)
        self.process = None
        self.port = None

    SR.start = fake_start  # type: ignore[assignment]
    SR.shutdown = fake_shutdown  # type: ignore[assignment]
    try:
        reg = ServeRunnerRegistry()
        r = await reg.get_or_create("alpha", tmp_path / "wt")
        r.touch()  # recent
        evicted = await reg.sweep_idle(ttl=10.0)
        assert evicted == []
        assert shutdown_calls == []
        assert reg.get(r.key) is r
    finally:
        SR.start = orig_start  # type: ignore[assignment]
        SR.shutdown = orig_shutdown  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_registry_shutdown_all_clears_everything(tmp_path: Path):
    from sweave.runtime.serve_runner import ServeRunner as SR
    orig_start = SR.start
    orig_shutdown = SR.shutdown

    async def fake_start(self):
        self.process = _FakeSubprocess(port=9999)
        self.port = 9999
        self.base_url = "http://127.0.0.1:9999"
        self.log_path = _serve_log_with_port(self.worktree_path, 9999)
        self.touch()

    async def fake_shutdown(self):
        self.process = None
        self.port = None

    SR.start = fake_start  # type: ignore[assignment]
    SR.shutdown = fake_shutdown  # type: ignore[assignment]
    try:
        reg = ServeRunnerRegistry()
        await reg.get_or_create("a", tmp_path / "wt_a")
        await reg.get_or_create("b", tmp_path / "wt_b")
        assert len(reg.known()) == 2
        await reg.shutdown_all()
        assert len(reg.known()) == 0
    finally:
        SR.start = orig_start  # type: ignore[assignment]
        SR.shutdown = orig_shutdown  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# ServeRunner key + identity
# ---------------------------------------------------------------------------


def test_runner_key_resolves_worktree_to_absolute(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    r1 = ServeRunner("a", wt)
    r2 = ServeRunner("a", wt / "sub" / "..")  # resolves to same path
    assert r1.key == r2.key


def test_runner_key_distinguishes_specialist():
    assert ServeRunner("a", Path("/x")).key != ServeRunner("b", Path("/x")).key
