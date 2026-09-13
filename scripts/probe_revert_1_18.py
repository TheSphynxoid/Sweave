"""Live probe: opencode 1.18.30 revert/unrevert routes (structural, no model).

Drives a scratch `opencode serve` in a temp cwd with isolated data dir:
create session, send two user messages with a deliberately bogus provider
(user messages persist with IDs even when generation fails — provider
errors surface downstream), then revert to the FIRST message, verify the
shape, unrevert. Also probes the file-state angle structurally: files can
only change via real generation, so this probe verifies ROUTES + HISTORY
TRUNCATION + POINTER SHAPE only; the file-restore probe runs separately
with a free model.

Run:  python scripts/probe_revert_1_18.py
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\user\sweave")

import httpx

from sweave.harness.opencode import isolated_opencode_env
from sweave.platform import creationflags_no_window


def free_port(start: int) -> int:
    for port in range(start, start + 16):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port")


def wait_ready(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/session", timeout=2.0).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("serve never became ready")


def main() -> None:
    binary = shutil.which("opencode")
    assert binary, "opencode not on PATH"
    port = free_port(18992)
    workdir = tempfile.mkdtemp(prefix="revert-probe-")
    env = isolated_opencode_env(dict(__import__("os").environ))
    proc = subprocess.Popen(
        [binary, "serve", "--port", str(port), "--hostname", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=workdir,
        env=env,
        creationflags=creationflags_no_window(),
    )
    base = f"http://127.0.0.1:{port}"
    try:
        wait_ready(base)
        print("READY", base, "version-check via /doc:")
        try:
            doc = httpx.get(f"{base}/doc", timeout=5).json()
            paths = sorted(doc.get("paths", {}).keys()) if isinstance(doc, dict) else []
            revert_paths = [p for p in paths if "revert" in p or "abort" in p]
            print("  revert/abort routes in /doc:", revert_paths)
        except Exception as e:
            print("  /doc unavailable:", e)

        with httpx.Client(base_url=base, timeout=60.0) as c:
            sid = c.post("/session", json={"title": "revert-probe"}).json()["id"]
            print("SESSION:", sid)

            msg_ids = []
            for i in (1, 2):
                body = {
                    "parts": [{"type": "text", "text": f"probe message {i} (bogus provider expected)"}],
                    "model": {"providerID": "bogus-probe", "modelID": "no-such-model"},
                }
                r = c.post(f"/session/{sid}/message", json=body)
                print(f"message {i} POST -> {r.status_code}")
            listing = c.get(f"/session/{sid}/message", timeout=15).json()
            if isinstance(listing, dict):
                listing = listing.get("messages", listing.get("info", []))
            def unwrap(items):
                """Listing rows may be {info: {...}} wrappers."""
                out = []
                for m in items:
                    if isinstance(m, dict) and isinstance(m.get("info"), dict):
                        out.append(m["info"])
                    elif isinstance(m, dict):
                        out.append(m)
                return out

            flat = unwrap(listing)
            print("MESSAGES AFTER BOTH:", [(m.get("id"), m.get("role")) for m in flat])
            user_ids = [m["id"] for m in flat if m.get("role") == "user"]
            print("USER IDS:", user_ids)

            first_user = user_ids[0]
            r = c.post(f"/session/{sid}/revert", json={"messageID": first_user})
            print("REVERT ->", r.status_code)
            try:
                info = r.json()
                print("  revert pointer:", json.dumps(info.get("revert"), default=str)[:300])
            except Exception as e:
                print("  body parse:", e)

            listing2 = c.get(f"/session/{sid}/message", timeout=15).json()
            if isinstance(listing2, dict):
                listing2 = listing2.get("messages", listing2.get("info", []))
            flat2 = unwrap(listing2)
            print("MESSAGES AFTER REVERT:", [(m.get("id"), m.get("role")) for m in flat2])

            r = c.post(f"/session/{sid}/unrevert")
            print("UNREVERT ->", r.status_code)
            listing3 = c.get(f"/session/{sid}/message", timeout=15).json()
            if isinstance(listing3, dict):
                listing3 = listing3.get("messages", listing3.get("info", []))
            flat3 = unwrap(listing3)
            print("MESSAGES AFTER UNREVERT:", [(m.get("id"), m.get("role")) for m in flat3])
    finally:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags_no_window(),
            timeout=15,
        )
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
