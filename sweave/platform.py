"""Windows-friendly subprocess defaults (no console-window flashes).

On Windows, every console-subsystem child (``git.exe``,
``python.exe``, ``opencode.exe``, ``gh``, ``docker``, ...) briefly
opens a visible CMD window unless spawned with
``CREATE_NO_WINDOW``. The chat turn alone used to flash 2-3 windows
(the transcript snapshotter's ``git`` calls); the opencode-spawned
MCP server (``python.exe``) flashed its own, which our flags can't
cover -- that one is fixed by spawning ``pythonw.exe`` instead
(see :func:`pythonw_executable`).

This module is the single place that knows the flag; every spawn
site (sync ``subprocess.run`` / ``check_output`` and async
``asyncio.create_subprocess_exec``) goes through it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


def creationflags_no_window() -> int:
    """``CREATE_NO_WINDOW`` on Windows, ``0`` elsewhere.

    Off-Windows the flag doesn't exist; ``0`` keeps every call
    site branch-free.
    """
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def run_no_window(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """Like :func:`subprocess.run` but never flashes a console window."""
    kwargs.setdefault("creationflags", creationflags_no_window())
    return subprocess.run(*args, **kwargs)


def check_output_no_window(*args: Any, **kwargs: Any) -> bytes:
    """Like :func:`subprocess.check_output` but never flashes a console."""
    kwargs.setdefault("creationflags", creationflags_no_window())
    return subprocess.check_output(*args, **kwargs)


def pythonw_executable() -> str:
    """Interpreter path that never owns a console (Windows only).

    ``sys.executable`` is ``python.exe`` (console subsystem): any
    parent that spawns it without ``CREATE_NO_WINDOW`` -- notably
    the opencode serve spawning our MCP server -- flashes a window.
    ``pythonw.exe`` (windows subsystem, same directory) runs the
    identical interpreter with no console; stdio pipes work the
    same, so MCP stdio is unaffected. Falls back to
    ``sys.executable`` off-Windows or when ``pythonw.exe`` is absent
    (non-standard installs).
    """
    exe = sys.executable or "python"
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        cand = exe[: -len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe
