"""Stall/hold coherence (incident 2026-09-11, slice 2).

``EscalationStore.create`` overwrites per delegation_id, and two
concurrent finders (in-band bridge, stall branch) both created
unconditionally: a second finder could destroy a live record (or a
recorded answer), and the 300s stall timer could kill a turn the
total budget was holding for a recorded question.

Slice 2 rules (in ``SpecialistRuntime._resolve_pending_permission``):
* same ask already recorded -> reuse it (``permission_reused``), no
  second record, exactly one reply POST (the first finder owns it);
* recorded hold but no bus signal -> wait on it
  (``permission_hold_wait``), never fail, never reply (bridge owns it);
* bus ask with no record -> create (unchanged behaviour).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sweave.runtime.escalation import EscalationStore
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.trace_log import TraceLog, read_trace


class FakeWatcher:
    def __init__(self, pending: list[dict] | None = None):
        self.pending = pending or []
        self.wait_idles: list[tuple[str, int]] = []

    def pending_for(self, session_id: str) -> list[dict]:
        return [p for p in self.pending if p["sessionID"] == session_id]

    async def wait_idle(self, session_id: str, baseline: int, timeout: float = 0.0) -> bool:
        self.wait_idles.append((session_id, baseline))
        return True

    def idle_snapshot(self, session_id: str) -> int:
        return 0


class FakeStreamResp:
    def raise_for_status(self) -> None:
        return None

    def aiter_text(self):
        async def gen():
            await asyncio.sleep(10 * 3600)
            yield ""

        return gen()


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
        self._session_id = "ses_coherencetest12345678"

    def _default_headers(self):
        return {}


ASK = {
    "id": "per_cohere0001",
    "sessionID": "ses_coherencetest12345678",
    "permission": "external_directory",
    "patterns": ["C:\\Data\\*"],
    "metadata": {"command": "type C:\\Data\\x.txt"},
}


@pytest.fixture()
def mock_env(monkeypatch, tmp_path: Path):
    from sweave.runtime import permission_watch as pw

    holder: dict = {}

    def _watcher(base):
        return holder.get("watcher", FakeWatcher([]))

    monkeypatch.setattr(pw, "get_permission_watcher", _watcher)
    replies: list[tuple[str, str]] = []

    async def fake_reply(client, base_url, sid, rid, value):
        replies.append((rid, value))
        return 200

    async def fake_fetch(client, base_url, sid):
        return [
            {
                "role": "assistant",
                "time": {"completed": 7},
                "parts": [{"type": "text", "text": "recovered!"}],
            }
        ]

    monkeypatch.setattr(pw, "reply_permission_request", fake_reply)
    monkeypatch.setattr(pw, "fetch_messages", fake_fetch)

    store = EscalationStore(base_dir=tmp_path, timeout_seconds=None)
    runtime = SpecialistRuntime(runners=None, escalation_store=store)
    return {"runtime": runtime, "replies": replies, "store": store, "holder": holder}


def _trace(tmp_path: Path, did: str = "chat-cohere"):
    return TraceLog(did, base_dir=tmp_path)


@pytest.mark.asyncio
async def test_reuse_recorded_ask_no_duplicate_no_double_reply(mock_env, tmp_path):
    """Bus ask + same request already recorded (bridge won the race):
    one record (same escalation_id), NO reply POST from our side (the
    first finder owns it — a second POST corrupts the serve's
    permission state), text recovered."""
    env = mock_env
    env["holder"]["watcher"] = FakeWatcher([dict(ASK)])
    first = await env["store"].create(
        delegation_id="chat-cohere",
        question="Permission required: ...",
        options=["allow once", "always allow", "deny"],
        kind="permission",
        audience="human",
        timeout_seconds=None,
        metadata={
            "requestID": "per_cohere0001",
            "sessionID": ASK["sessionID"],
            "permission": "external_directory",
            "patterns": ["C:\\Data\\*"],
            "command": "type C:\\Data\\x.txt",
        },
    )
    stop = asyncio.Event()

    async def answer_after_reuse():
        # Deterministic reuse: answer ONLY after the trace proves the
        # claim path took the reuse branch (answering earlier would
        # resolve the record first and route into supersede-create —
        # correct behaviour, but a different path).
        for _ in range(1000):
            await asyncio.sleep(0.01)
            if stop.is_set():
                return
            try:
                log = (tmp_path / "chat-cohere.jsonl").read_text(encoding="utf-8")
            except OSError:
                continue
            if "permission_reused" in log:
                rec = await env["store"].get(delegation_id="chat-cohere")
                if rec and rec.get("status") == "pending":
                    await env["store"].answer(
                        delegation_id="chat-cohere", response="allow once"
                    )
                    return
        raise AssertionError("reuse path never taken")

    task = asyncio.create_task(answer_after_reuse())
    out = await env["runtime"]._send_message(
        FakeProcess(), {"parts": [{"type": "text", "text": "hi"}]},
        _trace(tmp_path), stall_seconds=0.05, delegation_id="chat-cohere",
    )
    stop.set()
    await task
    assert out == "recovered!", out
    rec = await env["store"].get(delegation_id="chat-cohere")
    assert rec["escalation_id"] == first["escalation_id"]
    assert env["replies"] == []
    log = (tmp_path / "chat-cohere.jsonl").read_text(encoding="utf-8")
    assert "permission_reused" in log
    assert "permission_answered" in log
    assert "permission_recovered" in log


@pytest.mark.asyncio
async def test_hold_without_bus_signal_waits_never_replies(mock_env, tmp_path):
    """Recorded hold, silent bus (bridge owns the reply): the stalled
    turn waits (no stall death), posts no reply, recovers text."""
    env = mock_env
    env["holder"]["watcher"] = FakeWatcher([])
    first = await env["store"].create(
        delegation_id="chat-cohere",
        question="Permission required: ...",
        options=["allow once", "always allow", "deny"],
        kind="permission",
        audience="human",
        timeout_seconds=None,
        metadata={
            "requestID": "per_cohere0001",
            "sessionID": ASK["sessionID"],
            "permission": "external_directory",
            "patterns": ["C:\\Data\\*"],
            "command": "type C:\\Data\\x.txt",
        },
    )
    stop = asyncio.Event()

    async def answer_after_hold_wait():
        # Deterministic hold path: answer ONLY after the trace proves
        # the stall branch saw the recorded hold (answering earlier
        # would resolve the record first and route into late-answer
        # recovery — also correct, but a different path).
        for _ in range(1000):
            await asyncio.sleep(0.01)
            if stop.is_set():
                return
            try:
                log = (tmp_path / "chat-cohere.jsonl").read_text(encoding="utf-8")
            except OSError:
                continue
            if "permission_hold_wait" in log:
                rec = await env["store"].get(delegation_id="chat-cohere")
                if rec and rec.get("status") == "pending":
                    await env["store"].answer(
                        delegation_id="chat-cohere", response="allow once"
                    )
                    return
        raise AssertionError("hold-wait path never taken")

    task = asyncio.create_task(answer_after_hold_wait())
    out = await env["runtime"]._send_message(
        FakeProcess(), {"parts": [{"type": "text", "text": "hi"}]},
        _trace(tmp_path), stall_seconds=0.05, delegation_id="chat-cohere",
    )
    stop.set()
    await task
    assert out == "recovered!", out
    assert env["replies"] == []
    rec = await env["store"].get(delegation_id="chat-cohere")
    assert rec["escalation_id"] == first["escalation_id"]
    log = (tmp_path / "chat-cohere.jsonl").read_text(encoding="utf-8")
    assert "permission_hold_wait" in log
    # The body-silence stall fired (it is what entered the hold path),
    # but the turn recovered instead of dying on it.
    events = [
        e["event"] for e in read_trace("chat-cohere", base_dir=tmp_path)
    ]
    assert "stalled" in events
    assert "permission_recovered" in events


@pytest.mark.asyncio
async def test_new_ask_supersedes_resolved_record(mock_env, tmp_path):
    """A bus ask with no pending record still creates (resolved history
    never blocks a fresh ask)."""
    env = mock_env
    env["holder"]["watcher"] = FakeWatcher([dict(ASK)])
    old = await env["store"].create(
        delegation_id="chat-cohere",
        question="old",
        kind="permission",
        audience="human",
        timeout_seconds=None,
        metadata={"requestID": "per_older0000", "sessionID": ASK["sessionID"]},
    )
    await env["store"].answer(delegation_id="chat-cohere", response="deny")
    stop = asyncio.Event()

    async def answer_fresh_ask():
        # Answer the NEW record only (the resolved old one must not be
        # touched — supersede creates a fresh pending card).
        for _ in range(1000):
            await asyncio.sleep(0.01)
            if stop.is_set():
                return
            rec = await env["store"].get(delegation_id="chat-cohere")
            if (
                rec
                and rec.get("status") == "pending"
                and (rec.get("metadata") or {}).get("requestID") == "per_cohere0001"
            ):
                await env["store"].answer(
                    delegation_id="chat-cohere", response="allow once"
                )
                return
        raise AssertionError("fresh ask never went pending")

    task = asyncio.create_task(answer_fresh_ask())
    out = await env["runtime"]._send_message(
        FakeProcess(), {"parts": [{"type": "text", "text": "hi"}]},
        _trace(tmp_path), stall_seconds=0.05, delegation_id="chat-cohere",
    )
    stop.set()
    await task
    assert out == "recovered!", out
    rec = await env["store"].get(delegation_id="chat-cohere")
    assert rec["escalation_id"] != old["escalation_id"]
    assert rec["metadata"]["requestID"] == "per_cohere0001"
    assert env["replies"] == [("per_cohere0001", "once")]


@pytest.mark.asyncio
async def test_late_answer_recovers_without_new_record(mock_env, tmp_path):
    """Answer lands before the stall branch looks (human faster than
    the watchdog): no new record, no reply POST, text recovered via
    one fetch (``permission_recovered_late``)."""
    env = mock_env
    env["holder"]["watcher"] = FakeWatcher([])
    first = await env["store"].create(
        delegation_id="chat-cohere",
        question="Permission required: ...",
        options=["allow once", "always allow", "deny"],
        kind="permission",
        audience="human",
        timeout_seconds=None,
        metadata={
            "requestID": "per_cohere0001",
            "sessionID": ASK["sessionID"],
            "permission": "external_directory",
            "patterns": ["C:\\Data\\*"],
            "command": "type C:\\Data\\x.txt",
        },
    )
    await env["store"].answer(delegation_id="chat-cohere", response="allow once")
    out = await env["runtime"]._send_message(
        FakeProcess(), {"parts": [{"type": "text", "text": "hi"}]},
        _trace(tmp_path), stall_seconds=0.05, delegation_id="chat-cohere",
    )
    assert out == "recovered!", out
    assert env["replies"] == []
    rec = await env["store"].get(delegation_id="chat-cohere")
    assert rec["escalation_id"] == first["escalation_id"]
    log = (tmp_path / "chat-cohere.jsonl").read_text(encoding="utf-8")
    assert "permission_recovered_late" in log


@pytest.mark.asyncio
async def test_concurrent_same_ask_claims_single_record(mock_env):
    """Two finders racing on the same ask claim one record (atomicity
    lives in the store lock, not in check-then-act)."""
    store = mock_env["store"]
    kwargs: dict = dict(
        delegation_id="chat-cohere",
        question="Permission required: ...",
        options=["allow once", "always allow", "deny"],
        kind="permission",
        audience="human",
        timeout_seconds=None,
        metadata={"requestID": "per_cohere0001", "sessionID": ASK["sessionID"]},
        reuse_request_id="per_cohere0001",
    )
    rec_a, created_a = await store.create_or_reuse(**kwargs)
    rec_b, created_b = await store.create_or_reuse(**kwargs)
    assert created_a is True
    assert created_b is False
    assert rec_a["escalation_id"] == rec_b["escalation_id"]
