"""TOOL_CARDS step 0 probe: opencode `question` tool schema + per-tool
payload inventory on BOTH paths (engine SSE state + opencode v2 parts).

Hermetic: temp cwd + isolated opencode data dir; the schema half makes
NO model calls (serve + OpenAPI only); the inventory half reuses the
RECORDED part shapes from the view-probe sessions (free-tier probe 0)
plus the engine's own journal/trace artifacts on disk — a live free-tier
turn is only spawned if schemas are missing (the user's $0 ruling).
Kept as a drift script: re-run reproduces the same tables.

Run: PYTHONIOENCODING=utf-8 python scripts/probe_tool_payloads.py
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
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

RELEASE = "1.18.31"


def free_port(start: int) -> int:
    for port in range(start, start + 32):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port")


def probe_opencode_surface() -> dict[str, Any]:
    """Spin one serve; pull the OpenAPI doc + tool-definition surface.

    No model call: tool SCHEMAS are served by the config surface, not
    an inference turn.
    """
    from sweave.harness.opencode import isolated_opencode_env
    from sweave.platform import creationflags_no_window

    binary = shutil.which("opencode")
    assert binary, "opencode not on PATH"
    port = free_port(18990)
    wd = tempfile.mkdtemp(prefix="toolcards-probe-")
    env = isolated_opencode_env(dict(os.environ))
    proc = subprocess.Popen(
        [binary, "serve", "--port", str(port), "--hostname", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=wd,
        env=env,
        creationflags=creationflags_no_window(),
    )
    out: dict[str, Any] = {"version": RELEASE}
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                if httpx.get(base + "/session", timeout=3).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        r = httpx.get(base + "/doc/json", timeout=15)
        out["openapi_status"] = r.status_code
        if r.status_code == 200:
            try:
                d = r.json()
            except Exception:  # noqa: BLE001 — doc surface may be HTML
                out["openapi_body_head"] = str(r.text[:200])
                d = {}
            out["paths"] = sorted(str(p) for p in d.get("paths", {}))
            out["question_in_openapi"] = json.dumps(d).count("question")
        for path in ["/tool", "/tools", "/config/tools", "/project/tools"]:
            try:
                rr = httpx.get(base + path, timeout=8)
                out[path] = (
                    rr.status_code
                    if rr.status_code >= 300
                    else json.dumps(rr.json(), default=str)[:600]
                )
            except Exception as e:  # noqa: BLE001
                out[path] = f"ERR {type(e).__name__}"
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    return out


def probe_engine_journal_shapes() -> dict[str, Any]:
    """Inventory the ENGINE side's per-tool state from the live
    journal + trace artifacts already on disk (no model call)."""
    from sweave.web.transcript_view import engine_journal_path

    journal = engine_journal_path()
    out: dict[str, Any] = {}
    if journal.exists():
        data = json.loads(journal.read_text(encoding="utf-8"))
        per_tool: dict[str, dict[str, Any]] = {}
        for sid, session in data.items():
            if not isinstance(session, dict):
                continue
            for msg in session.get("messages", []):
                if not isinstance(msg, dict):
                    continue
                name = msg.get("name")
                if msg.get("role") == "tool" and name:
                    content_len = len(str(msg.get("content", "")))
                    slot = per_tool.setdefault(
                        name,
                        {"results": 0, "fails": 0, "result_chars_max": 0},
                    )
                    slot["results"] += 1
                    if msg.get("failed"):
                        slot["fails"] += 1
                    slot["result_chars_max"] = max(
                        slot["result_chars_max"], content_len
                    )
        out["journal_tool_results"] = per_tool
        out["journal_note"] = (
            "engine journal persists state.input inside assistant"
            " toolCalls; result content is the tool return."
        )
    # The engine SSE state shape (locked vocabulary) from serve.js/tools.js:
    out["engine_state_shape"] = {
        "loop": "{status, input, output|error, title, time?, metadata?}",
        "emit_sites": "loop.js tool.started/updated/completed/failed",
        "read_hint": "tools.js truncateOutput adds '... [truncated N chars]'",
        "bash_fact": "tools.js truncateTail keeps the tail + exit-code text",
        "read_fact": "readPath pages 2000 default + 'Use offset=' teaching",
        "grep_fact": "caps at 100 matches",
        "editwrite_fact": "short strings only ('edited'/'wrote ')",
    }
    return out


def probe_opencode_recorded_parts() -> dict[str, Any]:
    """Recorded opencode v2 part shapes (free-tier probe 0 + M1.9
    fixtures): the tool part key alpha + the per-tool state alpha."""
    return {
        "tool_part_keys": "callID,id,messageID,metadata,sessionID,state,tool,type",
        "state_keys": "input,metadata,output,status,time,title",
        "statuses_seen": ["pending", "running", "completed", "error"],
        "known_part_types": ["text", "reasoning", "tool", "step-finish"],
        "unknown_drift": "unknown_part traced raw once per type per turn (view 2c)",
    }


def main() -> int:
    print("=" * 72)
    print("TOOL_CARDS step 0 probe")
    print("=" * 72)
    print(json.dumps(probe_opencode_surface(), indent=1))
    print(json.dumps(probe_opencode_recorded_parts(), indent=1))
    eng = probe_engine_journal_shapes()
    print("journal tools:", json.dumps(eng.get("journal_tool_results", {}), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
