"""M1.3 step 5 live gate (state machine + lifecycle).

The M1.3 unit + integration suite covers the wire contract
(`tests/test_specialist_runtime.py`, `tests/test_specialist_store.py`,
`tests/test_specialist_model_ref.py`, `tests/test_m1_3_step3_*`,
`tests/test_m1_3_step4_*`). The live gate covers what those tests
don't: a full server boot + a real delegation + the state-machine
end-to-end in a single thread.

This script is deterministic by design: it sets the env var
``SWEAVE_MOCK_OPENCODE=1`` before starting the sweave server, which
gates a test hook inside ``OpenCodeHarness.spawn``. The hook returns
a stub :class:`OpenCodeProcess` whose ``_client`` is a canned httpx
mock that serves the same wire format the real v2 endpoint emits.
This avoids depending on:
  * ollama (the user runs it on their dev box, but the test
    environment is RAM-constrained and ollama isn't always up)
  * gmicloud / gmi / zai (cloud providers that need an API key)
  * a real opencode subprocess (slow Bun startup on Windows)

For a real e2e (real opencode + real LLM), unset
``SWEAVE_MOCK_OPENCODE`` and ensure a real ollama/gmicloud/zai is
reachable. The M1.3 step 0 probe results in
``docs/M1_3_PROBE_RESULTS.md`` are the manual real-e2e proof.

Run:
    python tests/test_m1_3_step5_live_gate.py
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO = Path(r"C:\Users\user\sweave")
SWEAVE_PORT = 8110
SWEAVE_BASE = f"http://127.0.0.1:{SWEAVE_PORT}"
PROJECT_NAME = "m1-3-live-gate"
WORKTREE_NAME = "wt-live-gate"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _post(url: str, body: dict | None = None) -> dict:
    if body is None:
        req = urllib.request.Request(url, data=b"", method="POST",
                                     headers={"content-type": "application/json"})
    else:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def _delete(url: str) -> None:
    req = urllib.request.Request(url, method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    failures: list[str] = []

    # Set up: create a project + worktree. Use a timestamped dir so a
    # previous run's locked/cwd-held directory (e.g. an orphaned
    # opencode serve still running with cwd inside it) doesn't break
    # the new run. Old dirs are the user's to clean up (or the orphan
    # sweep once psutil is a hard dep).
    import time as _time
    proj_dir = REPO / f"tmp-m1-3-live-gate-{int(_time.time())}"
    proj_dir.mkdir(parents=True, exist_ok=True)
    worktree_dir = proj_dir / WORKTREE_NAME
    worktree_dir.mkdir()

    # Start the server with the test hook enabled. The harness gates the
    # mock on this env var; the default (env unset) keeps the real
    # opencode subprocess path active.
    import subprocess
    sweave_log = open(REPO / "web.log", "ab")
    sweave_err = open(REPO / "web_err.log", "ab")
    server_env = {"SWEAVE_MOCK_OPENCODE": "1"}
    server_proc = subprocess.Popen(
        ["python", "-m", "sweave.cli.main", "web",
         "--host", "127.0.0.1", "--port", str(SWEAVE_PORT)],
        stdout=sweave_log, stderr=sweave_err,
        cwd=str(REPO),
        env={**__import__("os").environ, **server_env},
    )
    try:
        # Wait for boot
        for _ in range(60):
            try:
                s = socket.socket()
                s.connect(("127.0.0.1", SWEAVE_PORT))
                s.close()
                break
            except Exception:
                time.sleep(0.5)
        else:
            print("ERROR: sweave server failed to bind within 30s")
            return 1
        for _ in range(30):
            try:
                _get(SWEAVE_BASE + "/api/agents")
                break
            except Exception:
                time.sleep(0.5)
        else:
            print("ERROR: sweave server HTTP layer not ready within 15s")
            return 1

        # Create project + worktree
        try:
            _post(SWEAVE_BASE + "/api/projects", {
                "name": PROJECT_NAME,
                "path": str(proj_dir),
                "description": "M1.3 live gate",
            })
        except urllib.error.HTTPError:
            _delete(SWEAVE_BASE + f"/api/projects/{PROJECT_NAME}")
            _post(SWEAVE_BASE + "/api/projects", {
                "name": PROJECT_NAME,
                "path": str(proj_dir),
                "description": "M1.3 live gate",
            })
        _post(SWEAVE_BASE + f"/api/projects/{PROJECT_NAME}/active")

        # Create the specialist record so the runtime path resolves it
        # (project scope) and the session_id persistence is verifiable
        # via GET after the delegations run.
        _post(
            SWEAVE_BASE + "/api/specialists",
            {
                "name": "live-gate-alpha",
                "scope": "project",
                "description": "M1.3 live-gate test specialist",
                "harness": "opencode",
            },
        )

        # Delegation 1. The mock echoes the task's LAST word, so the
        # prompts end with the token we assert on.
        d1 = _post(
            SWEAVE_BASE + "/api/v2/tasks",
            {
                "agent": "live-gate-alpha",
                "task": "Say ACK",
                "project_name": PROJECT_NAME,
                "model": "opencode/claude-haiku-4-5",
            },
        )
        did1 = d1["delegation_id"]
        deadline = time.time() + 30
        d1_state = None
        d1_output = ""
        while time.time() < deadline:
            d = _get(SWEAVE_BASE + f"/api/delegations/{did1}")
            d1_state = d.get("status")
            d1_output = d.get("output") or ""
            if d1_state in ("review", "failed"):
                break
            time.sleep(0.5)
        print(f"D1: status={d1_state!r} output={d1_output[:60]!r}")
        if d1_state != "review":
            failures.append(f"D1: expected 'review', got {d1_state!r}")
        if "ACK" not in d1_output:
            failures.append(f"D1: expected 'ACK' in output, got {d1_output[:60]!r}")

        # Delegation 2: same specialist (session reuse expected)
        d2 = _post(
            SWEAVE_BASE + "/api/v2/tasks",
            {
                "agent": "live-gate-alpha",
                "task": "Say PONG",
                "project_name": PROJECT_NAME,
                "model": "opencode/claude-haiku-4-5",
            },
        )
        did2 = d2["delegation_id"]
        deadline = time.time() + 30
        d2_state = None
        d2_output = ""
        while time.time() < deadline:
            d = _get(SWEAVE_BASE + f"/api/delegations/{did2}")
            d2_state = d.get("status")
            d2_output = d.get("output") or ""
            if d2_state in ("review", "failed"):
                break
            time.sleep(0.5)
        print(f"D2: status={d2_state!r} output={d2_output[:60]!r}")
        if d2_state != "review":
            failures.append(f"D2: expected 'review', got {d2_state!r}")
        if "PONG" not in d2_output:
            failures.append(f"D2: expected 'PONG' in output, got {d2_output[:60]!r}")

        # Read the actual session id from the mock by checking the
        # per-specialist record (the runtime persists the session_id
        # after the first delegation).
        spec_data = _get(SWEAVE_BASE + "/api/specialists/live-gate-alpha")
        session_id = spec_data.get("session_id") or "<not-set>"
        print(f"PASS: session_id on Specialist after 2 delegations: {session_id!r}")
        if not spec_data.get("session_id"):
            failures.append("Specialist.session_id was never set after 2 delegations")

    finally:
        try:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except Exception:
                server_proc.kill()
        except Exception:
            pass
        try:
            _delete(SWEAVE_BASE + f"/api/projects/{PROJECT_NAME}")
        except Exception:
            pass
        import shutil
        if proj_dir.exists():
            shutil.rmtree(proj_dir, ignore_errors=True)
        try:
            sweave_log.close()
            sweave_err.close()
        except Exception:
            pass

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll live-gate assertions PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
