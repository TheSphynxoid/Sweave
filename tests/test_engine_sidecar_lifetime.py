"""Engine sidecar lifetime tests (2026-09-14 node-pile incident).

Hermetic: no node, no sidecar, no sockets. The sweep's process
probes (aliveness, /health, cmdline, kill) are injected seams; the
shutdown path drives a fake process. Module-global ``_sidecar`` is
saved/restored per test so the suite never touches live state.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import sweave.harness.engine as eng
from sweave.engine.protocol import PROTOCOL_VERSION


@pytest.fixture
def tracking(tmp_path: Path) -> Path:
    return tmp_path / "engine-sidecar.json"


@pytest.fixture
def clean_sidecar(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(eng, "_sidecar", None)
    return None


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# sweep_stale_sidecar
# ---------------------------------------------------------------------------


def test_sweep_no_file_is_noop(tracking: Path):
    calls: list[int] = []
    out = eng.sweep_stale_sidecar(
        tracking, _kill=calls.append,
    )
    assert out is None
    assert calls == []
    assert not tracking.exists()


def test_sweep_malformed_file_prunes(tracking: Path):
    tracking.write_text("[[[not json", encoding="utf-8")
    out = eng.sweep_stale_sidecar(tracking, _kill=lambda pid: None)
    assert out is None
    assert not tracking.exists()


def test_sweep_adopts_healthy_matching_sidecar(
    tracking: Path, clean_sidecar: None
):
    _write(tracking, {"pid": 1111, "port": 57259, "owner_pid": 9999})
    kills: list[int] = []
    out = eng.sweep_stale_sidecar(
        tracking,
        _is_alive=lambda pid: True,
        _health=lambda port: (True, PROTOCOL_VERSION),
        _kill=kills.append,
    )
    assert out == "http://127.0.0.1:57259"
    assert kills == []
    assert eng._sidecar is not None and eng._sidecar.process is None
    assert tracking.exists()  # adopted entry stays for the next boot


def test_sweep_leaves_live_owners_sidecar_alone(tracking: Path):
    _write(tracking, {"pid": 1111, "port": 57259, "owner_pid": 2222})
    kills: list[int] = []
    out = eng.sweep_stale_sidecar(
        tracking,
        _is_alive=lambda pid: True,  # owner 2222 alive, not us
        _health=lambda port: (False, None),
        _cmdline=lambda pid: "node serve.js --port 0",
        _kill=kills.append,
    )
    assert out is None
    assert kills == []
    assert tracking.exists()  # still theirs; don't prune


def test_sweep_kills_stale_ours_and_prunes(tracking: Path):
    _write(tracking, {"pid": 1111, "port": 57259, "owner_pid": 9999})
    kills: list[int] = []
    out = eng.sweep_stale_sidecar(
        tracking,
        _is_alive=lambda pid: pid == 1111,  # owner dead, sidecar alive
        _health=lambda port: (False, None),
        _cmdline=lambda pid: "node C:\\x\\sweave-engine\\src\\serve.js --port 0",
        _kill=kills.append,
    )
    assert out is None
    assert kills == [1111]
    assert not tracking.exists()


def test_sweep_never_kills_on_pid_alone(tracking: Path):
    """Pid alive but the command line is foreign (recycled pid,
    another Node app): leave it alone."""
    _write(tracking, {"pid": 1111, "port": 57259, "owner_pid": 9999})
    kills: list[int] = []
    out = eng.sweep_stale_sidecar(
        tracking,
        _is_alive=lambda pid: True,
        _health=lambda port: (False, None),
        _cmdline=lambda pid: "node C:\\other\\app.js",
        _kill=kills.append,
    )
    assert out is None
    assert kills == []


def test_sweep_unknown_cmdline_leaves_process_alone(tracking: Path):
    _write(tracking, {"pid": 1111, "port": 57259, "owner_pid": 9999})
    kills: list[int] = []
    out = eng.sweep_stale_sidecar(
        tracking,
        _is_alive=lambda pid: True,
        _health=lambda port: (False, None),
        _cmdline=lambda pid: None,  # unreadable: safe direction
        _kill=kills.append,
    )
    assert out is None
    assert kills == []


def test_sweep_dead_pid_prunes_silently(tracking: Path):
    _write(tracking, {"pid": 1111, "port": 57259, "owner_pid": 9999})
    kills: list[int] = []
    out = eng.sweep_stale_sidecar(
        tracking,
        _is_alive=lambda pid: False,
        _kill=kills.append,
    )
    assert out is None
    assert kills == []
    assert not tracking.exists()


def test_sweep_version_mismatch_kills_stale_ours(tracking: Path):
    """Healthy port but a foreign protocol version from a dead owner:
    don't adopt (every turn would fail at connect) — reap and spawn fresh."""
    _write(tracking, {"pid": 1111, "port": 57259, "owner_pid": 9999})
    kills: list[int] = []
    out = eng.sweep_stale_sidecar(
        tracking,
        _is_alive=lambda pid: pid == 1111,
        _health=lambda port: (True, "0"),
        _cmdline=lambda pid: "node serve.js --port 0",
        _kill=kills.append,
    )
    assert out is None
    assert kills == [1111]
    assert not tracking.exists()


# ---------------------------------------------------------------------------
# shutdown_sidecar
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, pid: int = 4242, hang: bool = False) -> None:
        self.pid = pid
        self.calls: list[str] = []
        self._hang = hang

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")

    async def wait(self) -> int:
        self.calls.append("wait")
        if self._hang:
            await asyncio.sleep(30)
        return 0


@pytest.mark.asyncio
async def test_shutdown_stops_owned_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from sweave.harness import engine as eng_mod

    proc = _FakeProc(pid=4242)
    monkeypatch.setattr(eng_mod, "_sidecar", eng_mod._Sidecar(
        base_url="http://127.0.0.1:9", process=proc,  # type: ignore[arg-type]
    ))
    tracking = tmp_path / "engine-sidecar.json"
    tracking.write_text(
        json.dumps({"pid": 4242, "port": 9, "owner_pid": 1}), encoding="utf-8"
    )
    monkeypatch.setattr(
        eng_mod, "_sidecar_tracking_path", lambda: tracking
    )
    assert await eng_mod.shutdown_sidecar() == "stopped"
    assert proc.calls[0] == "terminate"
    assert "wait" in proc.calls
    assert "kill" not in proc.calls
    assert eng_mod._sidecar is None
    assert not tracking.exists()


@pytest.mark.asyncio
async def test_shutdown_force_kills_hung_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from sweave.harness import engine as eng_mod

    proc = _FakeProc(pid=4242, hang=True)
    monkeypatch.setattr(eng_mod, "_sidecar", eng_mod._Sidecar(
        base_url="http://127.0.0.1:9", process=proc,  # type: ignore[arg-type]
    ))
    tracking = tmp_path / "engine-sidecar.json"
    tracking.write_text(
        json.dumps({"pid": 4242, "port": 9, "owner_pid": 1}), encoding="utf-8"
    )
    monkeypatch.setattr(
        eng_mod, "_sidecar_tracking_path", lambda: tracking
    )
    real_wait_for = asyncio.wait_for

    async def _fast_timeout(awaitable, timeout):
        await asyncio.sleep(0)
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()  # mimic wait_for cancelling the inner coroutine
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", _fast_timeout)
    try:
        assert await eng_mod.shutdown_sidecar() == "stopped"
    finally:
        monkeypatch.setattr(asyncio, "wait_for", real_wait_for)
    assert "terminate" in proc.calls
    assert "kill" in proc.calls


@pytest.mark.asyncio
async def test_shutdown_leaves_foreign_state_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from sweave.harness import engine as eng_mod

    # Nothing spawned: no-op, never raises.
    monkeypatch.setattr(eng_mod, "_sidecar", None)
    assert await eng_mod.shutdown_sidecar() == "not_ours"
    # Adopted (externally owned): no handle to reap.
    monkeypatch.setattr(
        eng_mod, "_sidecar",
        eng_mod._Sidecar(base_url="http://127.0.0.1:9", process=None),
    )
    assert await eng_mod.shutdown_sidecar() == "not_ours"


@pytest.mark.asyncio
async def test_shutdown_keeps_another_runs_tracking_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Our pid died but the file was already rewritten for a new
    sidecar: prune must not delete the fresh entry."""
    from sweave.harness import engine as eng_mod

    proc = _FakeProc(pid=4242)
    monkeypatch.setattr(eng_mod, "_sidecar", eng_mod._Sidecar(
        base_url="http://127.0.0.1:9", process=proc,  # type: ignore[arg-type]
    ))
    tracking = tmp_path / "engine-sidecar.json"
    tracking.write_text(
        json.dumps({"pid": 7777, "port": 10, "owner_pid": 1}), encoding="utf-8"
    )
    monkeypatch.setattr(
        eng_mod, "_sidecar_tracking_path", lambda: tracking
    )
    assert await eng_mod.shutdown_sidecar() == "stopped"
    assert tracking.exists()
