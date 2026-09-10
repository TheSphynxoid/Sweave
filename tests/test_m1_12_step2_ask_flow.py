"""M1.12 step 2 gate: the permission ask-flow on the runtime's
stall path (mock watcher + escalation store; reply/fetch faked at
the permission_watch module boundary).

Scenes:
1. Stalled turn + pending permission + answer "always allow" ->
   escalation created (kind=permission, no deadline, metadata
   carries the requestID), reply POST ``always``, idle confirmed,
   recovered text returned.
2. Skipped question -> reply POST ``reject`` -> loud chat error.
3. No pending permission -> unchanged plain stall error.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sweave.runtime.escalation import EscalationStore
from sweave.runtime.specialist_runtime import SpecialistRuntime


class FakeWatcher:
    def __init__(self, pending: list[dict] | None = None):
        self.pending = pending or []
        self.wait_idles: list[tuple[str, int]] = []
        self.idle_now = 0

    def pending_for(self, session_id: str) -> list[dict]:
        return [p for p in self.pending if p["sessionID"] == session_id]

    async def wait_idle(self, session_id: str, baseline: int, timeout: float = 0.0) -> bool:
        self.wait_idles.append((session_id, baseline))
        return True

    def idle_snapshot(self, session_id: str) -> int:
        return self.idle_now


class FakeBody:
    def get(self, key, default=None):
        return {"parts": [{"text": "hello"}]}.get(key, default)


class FakeStreamResp:
    """Never produces bytes -> stall."""

    def __init__(self):
        self.status_code = 200

    def raise_for_status(self):
        return None

    def aiter_text(self):
        async def gen():
            await asyncio.sleep(10 * 3600)  # hangs forever
            yield ""
        return gen()

    def is_stream(self):  # pragma: no cover - unused
        return True


class FakeStreamCtx:
    def __init__(self):
        self.resp = FakeStreamResp()

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *args):
        return False


class FakeClient:
    def stream(self, *args, **kwargs):
        return FakeStreamCtx()


class FakeProcess:
    def __init__(self):
        self._client = FakeClient()
        self.base_url = "http://mock-opencode"
        self._session_id = "ses_faketest1234567890ab"

    def _default_headers(self):
        return {}


ASK = {
    "id": "per_probe0001",
    "sessionID": "ses_faketest1234567890ab",
    "permission": "external_directory",
    "patterns": ["C:\\Windows\\*"],
    "metadata": {"command": "cat C:\\Windows\\win.ini"},
}


@pytest.fixture()
def mock_env(monkeypatch, tmp_path: Path):
    from sweave.runtime import permission_watch as pw

    fake = FakeWatcher([ASK])
    monkeypatch.setattr(pw, "get_permission_watcher", lambda base: fake)
    replies: list[tuple[str, str]] = []

    async def fake_reply(client, base_url, sid, rid, value):
        replies.append((rid, value))
        return 200

    async def fake_fetch(client, base_url, sid):
        return [
            {"info": {"role": "user", "parts": [{"type": "text", "text": "q"}]}},
            {
                "info": {
                    "role": "assistant",
                    "time": {"completed": 123},
                    "parts": [{"type": "text", "text": "recovered!"}],
                }
            },
        ]

    monkeypatch.setattr(pw, "reply_permission_request", fake_reply)
    monkeypatch.setattr(pw, "fetch_messages", fake_fetch)

    store = EscalationStore(base_dir=tmp_path, timeout_seconds=None)
    runtime = SpecialistRuntime(runners=None, escalation_store=store)
    return {
        "runtime": runtime,
        "watcher": fake,
        "replies": replies,
        "store": store,
        "pw": pw,
    }


def _trace(tmp_path: Path):
    from sweave.runtime.trace_log import TraceLog

    return TraceLog("chat-test", base_dir=tmp_path)


@pytest.mark.asyncio
async def test_stall_with_pending_permission_answer_always(
    mock_env, tmp_path
):
    env = mock_env
    runtime: SpecialistRuntime = env["runtime"]

    # Auto-answer the escalation once it appears ("always allow").
    async def answerer():
        for _ in range(200):
            await asyncio.sleep(0.01)
            rec = await env["store"].get(delegation_id="chat-test")
            if rec and rec.get("status") == "pending":
                await env["store"].answer(
                    delegation_id="chat-test", response="always allow it"
                )
                return

    task = asyncio.create_task(answerer())
    trace = _trace(tmp_path)
    result = await runtime._send_message(
        FakeProcess(),
        {},
        trace,
        stall_seconds=0.1,
        delegation_id="chat-test",
    )
    await task
    assert result == "recovered!", result
    # Reply POSTed as "always".
    assert env["replies"] == [("per_probe0001", "always")]
    assert env["watcher"].wait_idles[0][0] == ASK["sessionID"]
    tr = json.loads((tmp_path / "chat-test.jsonl").read_text(encoding="utf-8").splitlines()[-3])
    names = {e.get("event") for e in (tr,)}
    # trace events landed at line repeated; check file content broadly
    log = (tmp_path / "chat-test.jsonl").read_text(encoding="utf-8")
    assert "permission_answered" in log
    assert "permission_recovered" in log
    rec = await env["store"].get(delegation_id="chat-test")
    assert rec["kind"] == "permission"
    assert rec["metadata"]["requestID"] == "per_probe0001"
    assert rec["metadata"]["command"].endswith("win.ini")


@pytest.mark.asyncio
async def test_stall_skip_maps_to_reject(mock_env, tmp_path):
    env = mock_env
    runtime: SpecialistRuntime = env["runtime"]

    async def skipper():
        for _ in range(200):
            await asyncio.sleep(0.01)
            rec = await env["store"].get(delegation_id="chat-test")
            if rec and rec.get("status") == "pending":
                await env["store"].skip(delegation_id="chat-test")
                return

    task = asyncio.create_task(skipper())
    result = await env["runtime"]._send_message(
        FakeProcess(), {}, _trace(tmp_path), delegation_id="chat-test",
        stall_seconds=0.05,
    )
    await task
    assert env["replies"] == [("per_probe0001", "reject")]
    assert result.startswith("[chat error: permission denied:")


def test_stall_without_pending_unchanged(mock_env, tmp_path, monkeypatch):
    env = mock_env
    pw = env["pw"]
    empty = FakeWatcher([])
    monkeypatch.setattr(pw, "get_permission_watcher", lambda base: empty)
    runtime = env["runtime"]

    async def main():
        return await runtime._send_message(
            FakeProcess(), {}, _trace(tmp_path), delegation_id="chat-test",
            stall_seconds=0.05,
        )

    result = asyncio.run(main())
    assert result.startswith("[chat error: stalled after")
    assert env["replies"] == []
