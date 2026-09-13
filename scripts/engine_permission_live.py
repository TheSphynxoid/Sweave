"""Engine step-2 live scenes: permission ask -> allow/deny (FREE-TIER ONLY).

Needs the Sweave server on :8100 (start it first:
`python start_server.py 8100 127.0.0.1` from the repo root).
Drives two real engine turns (sidecar + adapter, lfm :free, $0)
against the LIVE escalation flow, answering as the human via HTTP:

  Scene A (allow): bash ask -> scripted "allow once" -> turn
  completes WITH the tool content.
  Scene B (deny): bash ask -> scripted "deny" -> tool fails loud,
  turn ends with the failure visible (never silent success).

Run:  python scripts/engine_permission_live.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

API = os.environ.get("SWEAVE_API_URL", "http://127.0.0.1:8100")
MODEL = os.environ.get(
    "ENGINE_GATE_MODEL", "openrouter/liquid/lfm-2.5-2.6b:free"
)
PROMPT = (
    "Use the bash tool to run exactly this command: echo {marker}. "
    "When it finishes, reply with exactly DONE and nothing else."
)


def token() -> str:
    direct = os.environ.get("SWEAVE_MCP_TOKEN")
    if direct:
        return direct
    override = os.environ.get("SWEAVE_TOKEN_FILE")
    p = Path(override) if override else Path.home() / ".sweave" / "mcp_token"
    return p.read_text(encoding="utf-8").strip()


def wait_healthy() -> None:
    for _ in range(60):
        try:
            r = httpx.get(f"{API}/api/delegations", timeout=3.0)
            if r.status_code == 200:
                return
        except httpx.ConnectError:
            time.sleep(1.0)
    raise RuntimeError("server :8100 not healthy")


def answer_when_pending(delegation_id: str, response: str, timeout_s: float = 240) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(
                f"{API}/api/delegations/{delegation_id}/escalation", timeout=5.0
            )
            if r.status_code == 200 and r.json().get("status") == "pending":
                break
        except httpx.ConnectError:
            pass
        time.sleep(2.0)
    else:
        return False
    r = httpx.post(
        f"{API}/api/delegations/{delegation_id}/answer",
        json={"response": response},
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    print(f"  answered {delegation_id}: {response!r}")
    return True


async def run_turn(workdir: str, delegation_id: str, marker: str):
    from sweave.harness.base import AgentSpec, Message
    from sweave.harness.engine import SweaveEngineHarness

    spec = AgentSpec(
        name="worker",
        role="specialist",
        model=MODEL,
        system_prompt="",
        worktree_path=Path(workdir),
        memory_bank="session-perm-live",
        tools=["bash"],
        harness="sweave-engine",
    )
    events: list[tuple] = []

    class Trace:
        def append(self, name, payload):
            events.append((name, payload))

    proc = await SweaveEngineHarness().spawn(spec)
    result = await proc.send(
        Message(
            type="user",
            content=PROMPT.format(marker=marker),
            metadata={
                "permission_map": {"bash": "ask"},
                "delegation_id": delegation_id,
                "turn_timeout": 300,
            },
        ),
        trace=Trace(),
    )
    return result, events


def scene(delegation_id: str, answer: str, marker: str) -> tuple:
    workdir = tempfile.mkdtemp(prefix="eng-perm-live-")
    box: dict = {}

    def drive():
        box["result"], box["events"] = asyncio.run(
            run_turn(workdir, delegation_id, marker)
        )

    thread = threading.Thread(target=drive, daemon=True)
    thread.start()
    if not answer_when_pending(delegation_id, answer):
        thread.join(timeout=120)
        result, events = box.get("result"), box.get("events", [])
        tool_events = [(n, (p.get("tool"), (p.get("state") or {}).get("status"))) for n, p in (events or []) if n.startswith("tool.")]
        raise SystemExit(
            f"no escalation appeared for {delegation_id}; "
            f"turn result={result!r} tool_events={tool_events}"
        )
    thread.join(timeout=300)
    assert not thread.is_alive(), "turn thread hung"
    return box["result"], box["events"]


def main() -> int:
    wait_healthy()
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        ["node", str(repo_root / "sweave-engine" / "src" / "serve.js"), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "SWEAVE_MCP_TOKEN": token()},
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line.startswith("SWEAVE_ENGINE_PORT="), line
        os.environ["SWEAVE_ENGINE_URL"] = (
            f"http://127.0.0.1:{line.split('=', 1)[1]}"
        )
        print("sidecar:", os.environ["SWEAVE_ENGINE_URL"])

        print("Scene A (allow once -> content):")
        result, events = scene("live-eng-allow-1", "allow once", "PERMISSION-CONTENT")
        completed = [p for n, p in events if n == "tool.completed"]
        print(f"  output={result.output[:80]!r} error={result.error}")
        assert result.success and "DONE" in result.output, result
        assert completed and "PERMISSION-CONTENT" in completed[0]["state"]["output"], events
        print("  GREEN: allow -> real content")

        print("Scene B (deny -> loud failure):")
        result, events = scene("live-eng-deny-1", "deny", "NEVER-SHOWN")
        failed = [p for n, p in events if n == "tool.failed"]
        print(f"  output={result.output[:80]!r} error={result.error}")
        assert failed and "permission rejected" in failed[0]["state"]["error"], events
        assert not any(
            "NEVER-SHOWN" in (p.get("state", {}).get("output") or "")
            for n, p in events
            if n == "tool.completed"
        )
        print("  GREEN: deny -> loud, no content")
        print("PERMISSION LIVE: GREEN (free tier, $0)")
        return 0
    finally:
        proc.kill()


if __name__ == "__main__":
    sys.exit(main())
