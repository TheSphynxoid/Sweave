"""Live probe 2: file-state restore via revert (free model, real turn).

1. free-model turn: "create probe_revert.txt with content X"  -> file exists
2. revert to that user message  -> pointer at msg1, file should be UNDONE
3. unrevert                     -> file should be BACK
"""

from __future__ import annotations

import json
import os
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

MODEL = {"providerID": "opencode", "modelID": "muse-spark-1.3-contributor-free"}


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


def turn(c: httpx.Client, sid: str, text: str) -> str:
    """Send one message with the free model; return the final assistant text.

    Uses brace-depth splitting (GOTCHAS: the serve may deliver the
    whole stream as one newline-free JSON document).
    """
    from sweave.harness.opencode import _split_json_stream

    body = {"parts": [{"type": "text", "text": text}], "model": MODEL}
    out: list[str] = []
    errors: list[str] = []
    with c.stream("POST", f"/session/{sid}/message", json=body, timeout=300.0) as r:
        print("  stream status:", r.status_code)
        carry = ""
        seen = 0
        for chunk in r.iter_text():
            seen += len(chunk)
            pieces, carry = _split_json_stream(chunk, carry)
            for piece in pieces:
                try:
                    obj = json.loads(piece)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                info = obj.get("info") or {}
                if isinstance(info, dict) and info.get("error"):
                    errors.append(json.dumps(info["error"], default=str)[:200])
                for part in obj.get("parts", []) or []:
                    if isinstance(part, dict) and part.get("type") == "text":
                        out.append(part.get("text", ""))
        print("  stream bytes:", seen, "| errors:", errors[:2])
    if errors:
        return "[provider error: " + errors[0] + "]"
    return "".join(out)[-200:]


def main() -> None:
    binary = shutil.which("opencode")
    assert binary, "opencode not on PATH"
    port = free_port(19092)
    workdir = tempfile.mkdtemp(prefix="revert-file-probe-")
    # Snapshot (file-state revert) is enabled ONLY in git repos
    # (snapshot.enabled: state.vcs !== "git" -> false).
    subprocess.run(["git", "init", "-b", "main", workdir],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", workdir, "add", "-A"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", workdir, "-c", "user.email=p@p", "-c", "user.name=p",
         "commit", "-m", "init", "--allow-empty"],
        capture_output=True, check=True,
    )
    env = isolated_opencode_env(dict(os.environ))
    proc = subprocess.Popen(
        [binary, "serve", "--port", str(port), "--hostname", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=workdir,
        env=env,
        creationflags=creationflags_no_window(),
    )
    base = f"http://127.0.0.1:{port}"
    target = Path(workdir) / "probe_revert.txt"
    try:
        wait_ready(base)
        with httpx.Client(base_url=base, timeout=300.0) as c:
            sid = c.post("/session", json={"title": "revert-file-probe"}).json()["id"]
            print("SESSION:", sid)

            t1 = turn(c, sid, "Create a file named probe_revert.txt in the current directory containing exactly: hello-from-probe. Then stop.")
            print("TURN1 tail:", t1.replace("\n", " ")[:160])
            print("FILE EXISTS after turn1:", target.exists(),
                  "| content:", target.read_text(encoding="utf-8")[:40] if target.exists() else "-")

            listing = c.get(f"/session/{sid}/message", timeout=15).json()
            if isinstance(listing, dict):
                listing = listing.get("messages", listing.get("info", []))
            flat = [m.get("info", m) for m in listing]
            user_ids = [m["id"] for m in flat if m.get("role") == "user"]
            first_user = user_ids[0]

            r = c.post(f"/session/{sid}/revert", json={"messageID": first_user})
            print("REVERT ->", r.status_code, "| pointer:", json.dumps(r.json().get("revert"), default=str)[:200])
            print("FILE EXISTS after revert:", target.exists(),
                  "| content:", target.read_text(encoding="utf-8")[:40] if target.exists() else "-")

            r = c.post(f"/session/{sid}/unrevert")
            print("UNREVERT ->", r.status_code)
            print("FILE EXISTS after unrevert:", target.exists(),
                  "| content:", target.read_text(encoding="utf-8")[:40] if target.exists() else "-")
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
