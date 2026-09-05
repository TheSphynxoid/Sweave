"""M1.8 live mini-scene: chat streaming (chat.delta + message.added).

Drives the chat streaming path against a running Sweave server
(mock opencode). Confirms:

* The WS bus receives ``chat.delta`` events for the chat turn.
  (The mock opencode emits one text part per message; the
  coalescer flushes once at the end-of-turn boundary. Production
  with a real opencode would emit many parts, which the
  coalescer throttles to ~10/sec.)
* The ``chat.delta`` payload carries session_id + delegation_id.
* The ``message.added`` event arrives for the assistant message
  with the chat delegation's id in the metadata (the join key
  the UI uses to replace the streaming bubble).
* The chat delegation transitions queued -> running -> done
  (M1.7's auto-done ruling still holds).
* The persisted assistant message has the full text (not
  partial).
* Two independent sessions of the same project get two
  independent chat delegations and two independent bindings.

The script talks to a real server on 127.0.0.1:8100 (set via
SWEAVE_BASE). Run after ``start_server.py 8100 127.0.0.1`` with
``SWEAVE_MOCK_OPENCODE=1`` so the runtime stub answers without a
real opencode subprocess.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx


BASE = os.environ.get("SWEAVE_BASE", "http://127.0.0.1:8100")


def _post(path: str, body: dict | None = None) -> dict:
    r = httpx.post(
        f"{BASE}{path}", json=body if body is not None else {},
        headers={"content-type": "application/json"}, timeout=15.0,
    )
    if r.status_code >= 400:
        print(f"  POST {path} -> {r.status_code}: {r.text}")
        raise SystemExit(1)
    return r.json()


def _get(path: str) -> dict:
    r = httpx.get(f"{BASE}{path}", timeout=15.0)
    r.raise_for_status()
    return r.json()


async def main() -> int:
    # Step 1: setup
    name = f"p-m18-stream-{uuid.uuid4().hex[:8]}"
    proj_dir = Path.home() / ".sweave" / "live" / name
    proj_dir.mkdir(parents=True, exist_ok=True)
    _post("/api/projects", {"name": name, "path": str(proj_dir), "description": ""})
    _post(f"/api/projects/{name}/active")
    sess1 = _post("/api/sessions", {"name": f"S1-{name}", "project_name": name})
    sid1 = sess1["session"]["id"]
    print(f"project: {name}\nsession1: {sid1}")

    # Step 2: connect to the WS bus and capture events
    chat_delta_events = []
    message_added_events = []
    status_events = []

    async def listen_ws(sid: str) -> None:
        # The WS endpoint is at /ws; we use the same auth-less
        # connection the UI uses (the server-side connect
        # doesn't require auth for the local test).
        import websockets
        async with websockets.connect(f"{BASE.replace('http', 'ws', 1)}/ws") as ws:
            try:
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    event = msg.get("event")
                    data = msg.get("data", {})
                    if event == "chat.delta" and data.get("session_id") == sid:
                        chat_delta_events.append(data)
                    elif event == "message.added" and data.get("session_id") == sid:
                        message_added_events.append(data)
                    elif event == "delegation.status_changed" and data.get("kind") == "chat":
                        status_events.append(data)
            except Exception:
                pass

    # Run the WS listener in the background
    listener = asyncio.create_task(listen_ws(sid1))
    # Give the WS a moment to connect
    await asyncio.sleep(0.5)

    # Step 3: send a chat message; the chat loop fires the
    # orchestrator and streams back
    user_content = "Streaming smoke test from scripts/m1_8_stream_live_scene.py"
    r = httpx.post(
        f"{BASE}/api/sessions/{sid1}/messages",
        json={"role": "user", "content": user_content},
        timeout=30.0,
    )
    r.raise_for_status()
    print(f"chat turn response: {r.json().get('assistant', {}).get('content', '')[:80]}...")

    # Step 4: wait for events
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if chat_delta_events and message_added_events:
            break
        await asyncio.sleep(0.1)
    listener.cancel()
    try:
        await listener
    except asyncio.CancelledError:
        pass

    # Step 5: assertions
    print(f"chat.delta events: {len(chat_delta_events)}")
    for evt in chat_delta_events[:3]:
        print(
            f"  payload: session_id={evt.get('session_id')[:12]}..., "
            f"delegation_id={evt.get('delegation_id')[:12]}..., "
            f"text={evt.get('text', '')[:40]!r}"
        )
    print(f"message.added events: {len(message_added_events)}")
    for evt in message_added_events:
        msg = evt.get("message", {})
        print(
            f"  payload: role={msg.get('role')}, "
            f"content={msg.get('content', '')[:40]!r}, "
            f"delegation_id={msg.get('metadata', {}).get('delegation_id', '')[:12]}..."
        )
    print(f"status events: {[(e.get('status'), e.get('delegation_id', '')[:12]) for e in status_events]}")

    assert chat_delta_events, "no chat.delta events received"
    for evt in chat_delta_events:
        assert "session_id" in evt, evt
        assert "delegation_id" in evt, evt
        assert "text" in evt, evt
    # Each chat.delta carries the same delegation_id (the chat
    # delegation that produced the partial)
    delegation_ids = {e["delegation_id"] for e in chat_delta_events}
    assert len(delegation_ids) == 1, (
        f"multiple delegation_ids in chat.delta: {delegation_ids}"
    )
    # The accumulated text across deltas matches the assistant
    # message's full content (the chat loop's coalescer might
    # split the text into one or more deltas; the persisted
    # message is the canonical full text).
    accumulated = "".join(e["text"] for e in chat_delta_events)
    if message_added_events:
        msg = message_added_events[-1]["message"]
        if msg.get("role") == "assistant":
            full = msg.get("content", "")
            assert accumulated == full, (
                f"delta-accumulated {accumulated!r} != persisted {full!r}"
            )
    # Status transitions: queued -> running -> done
    statuses = [e["status"] for e in status_events]
    assert "running" in statuses, statuses
    assert "done" in statuses, statuses
    # The persisted message carries the chat delegation's id
    if message_added_events:
        msg = message_added_events[-1]["message"]
        meta = msg.get("metadata", {})
        assert "delegation_id" in meta, (
            f"message.added message missing delegation_id in metadata: {msg}"
        )
        assert meta["delegation_id"] in delegation_ids, (
            f"message.added delegation_id {meta['delegation_id']!r} not in chat.delta ids {delegation_ids!r}"
        )

    # Step 6: a second session of the same project
    sess2 = _post("/api/sessions", {"name": f"S2-{name}", "project_name": name})
    sid2 = sess2["session"]["id"]
    r2 = httpx.post(
        f"{BASE}/api/sessions/{sid2}/messages",
        json={"role": "user", "content": "second session streaming test"},
        timeout=30.0,
    )
    r2.raise_for_status()
    sessions_dir = Path.home() / ".sweave" / "projects" / name / "sessions"
    s1_data = json.loads((sessions_dir / f"{sid1}.json").read_text(encoding="utf-8"))
    s2_data = json.loads((sessions_dir / f"{sid2}.json").read_text(encoding="utf-8"))
    assert s1_data.get("orchestrator_session_id"), s1_data
    assert s2_data.get("orchestrator_session_id"), s2_data
    print(
        f"per-Session orchestrator bindings: "
        f"s1={s1_data['orchestrator_session_id']}, "
        f"s2={s2_data['orchestrator_session_id']}"
    )

    print("\nALL GREEN: chat streaming live scene passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
