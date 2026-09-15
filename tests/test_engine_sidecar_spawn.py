"""Sidecar spawn must never own a console window (2026-09-14).

Live report: every engine turn opened a visible CMD window. Root
cause: ``_ensure_sidecar`` was the only spawn site bypassing
``platform.creationflags_no_window()`` (whose docstring claims every
spawn site goes through it). Pinned hermetically by capturing the
``asyncio.create_subprocess_exec`` kwargs — no node process spawns.
"""

from __future__ import annotations

import asyncio

import pytest

import sweave.harness.engine as eng
from sweave.platform import creationflags_no_window


class _FakeStdout:
    async def readline(self) -> bytes:
        return b"SWEAVE_ENGINE_PORT=4567\n"


class _FakeProc:
    stdout = _FakeStdout()
    pid = 4242

    def kill(self) -> None:
        pass


@pytest.mark.asyncio
async def test_sidecar_spawn_passes_no_window_flag(monkeypatch, tmp_path):
    captured: dict = {}

    async def fake_create(*args, **kwargs):
        captured.update(kwargs)
        captured["args"] = args
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(eng.shutil, "which", lambda _name: "C:\\fake\\node.exe")
    # Hermetic tracking: the spawn records its pid, but never into
    # the real home dir.
    monkeypatch.setattr(
        eng, "_sidecar_tracking_path", lambda: tmp_path / "engine-sidecar.json"
    )
    prev, eng._sidecar = eng._sidecar, None
    try:
        sidecar = await eng._ensure_sidecar()
    finally:
        eng._sidecar = prev
    assert sidecar.base_url == "http://127.0.0.1:4567"
    assert captured.get("creationflags") == creationflags_no_window()
    # On Windows the flag must be a real CREATE_NO_WINDOW, not 0.
    import os

    if os.name == "nt":
        assert captured["creationflags"] != 0
    # The pid-gated tracking fired for the real-int pid.
    import json

    tracked = json.loads((tmp_path / "engine-sidecar.json").read_text(encoding="utf-8"))
    assert tracked["pid"] == 4242
    assert tracked["port"] == 4567
