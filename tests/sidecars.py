"""Bounded engine-sidecar spawn for tests (2026-09-14 hang fix).

Five engine test modules spawn the REAL sidecar (``node
sweave-engine/src/serve.js``) and used to block forever on
``proc.stdout.readline()`` when the sidecar never printed (crash /
port clash — stderr is DEVNULL, so the failure was silent), while
``proc.kill()`` without ``wait()`` orphaned node processes (three
strays found live the same day). Every wait in this module is
bounded; a tripped bound fails loud with the sidecar's returncode
instead of wedging the suite with zero output.
"""

from __future__ import annotations

import subprocess
import threading

import pytest

from sweave.platform import creationflags_no_window

#: How long the sidecar gets to print its ``SWEAVE_ENGINE_PORT=``
#: line. Healthy spawns answer in <2s; production
#: (``sweave/harness/engine.py``) allows 15s — tests allow double
#: for loaded machines sharing the box with the dev server.
PORT_LINE_TIMEOUT_SECONDS = 30.0

#: How long a killed sidecar gets to exit before teardown stops
#: waiting (the orphan stays the OS's problem, never the suite's).
STOP_TIMEOUT_SECONDS = 10.0


def spawn_sidecar(argv: list[str], env: dict[str, str]) -> subprocess.Popen:
    """Spawn the engine sidecar without flashing a console window."""
    return subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        creationflags=creationflags_no_window(),
    )


def read_port_line(
    proc: subprocess.Popen,
    *,
    timeout: float = PORT_LINE_TIMEOUT_SECONDS,
) -> str:
    """Read the sidecar's port line, bounded. Fails loud on a trip.

    A daemon thread does the blocking read; the join is the bound.
    On a trip the sidecar is stopped and the test fails with its
    returncode — never a silent infinite hang.
    """
    box: list[str] = []

    def _read() -> None:
        try:
            if proc.stdout is not None:
                box.append(proc.stdout.readline())
        except Exception:  # noqa: BLE001 — reader thread; the join below owns the outcome
            pass

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(timeout=timeout)
    if box and box[0].strip():
        return box[0].strip()
    rc = stop_sidecar(proc)
    pytest.fail(f"engine sidecar printed no port line within {timeout:.0f}s (returncode={rc})")
    raise AssertionError("unreachable")


def wait_for_health(url: str, *, attempts: int = 50, per_try_timeout: float = 2.0) -> bool:
    """Poll ``GET <url>/health`` until 200. Bounded by construction.

    Returns False when the sidecar never became healthy — the
    caller's turn then fails loudly on its own (the pre-fix shape;
    this helper only makes the bound explicit).
    """
    import time

    import httpx

    for _ in range(attempts):
        try:
            if httpx.get(f"{url}/health", timeout=per_try_timeout).status_code == 200:
                return True
        except httpx.ConnectError:
            time.sleep(0.1)
    return False


def stop_sidecar(proc: subprocess.Popen, *, timeout: float = STOP_TIMEOUT_SECONDS):
    """Kill + bounded wait. Never raises; returns the returncode."""
    try:
        proc.kill()
    except Exception:  # noqa: BLE001 — already dead is fine
        pass
    try:
        return proc.wait(timeout=timeout)
    except Exception:  # noqa: BLE001 — still alive after kill; don't wedge teardown
        return proc.returncode
