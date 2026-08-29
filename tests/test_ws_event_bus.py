"""WSEventBus tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from sweave.web.events import WSEventBus


class FakeWebSocket:
    """Minimal stand-in for fastapi.WebSocket for unit tests."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_subscribe_and_publish():
    bus = WSEventBus()
    ws = FakeWebSocket()
    await bus.subscribe(ws)
    await bus.publish("hello", {"x": 1})
    assert len(ws.sent) == 1
    msg = json.loads(ws.sent[0])
    assert msg["event"] == "hello"
    assert msg["data"] == {"x": 1}
    assert "timestamp" in msg


@pytest.mark.asyncio
async def test_multiple_subscribers_all_receive():
    bus = WSEventBus()
    ws_a, ws_b, ws_c = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()
    for w in (ws_a, ws_b, ws_c):
        await bus.subscribe(w)
    await bus.publish("e", {"k": "v"})
    assert len(ws_a.sent) == 1
    assert len(ws_b.sent) == 1
    assert len(ws_c.sent) == 1


@pytest.mark.asyncio
async def test_unsubscribe_removes_subscriber():
    bus = WSEventBus()
    ws = FakeWebSocket()
    await bus.subscribe(ws)
    await bus.unsubscribe(ws)
    await bus.publish("e", {})
    assert ws.sent == []


@pytest.mark.asyncio
async def test_dead_subscriber_is_dropped():
    """A subscriber whose send raises is removed from the list."""
    bus = WSEventBus()

    class BrokenWS:
        def __init__(self) -> None:
            self.calls = 0

        async def send_text(self, text: str) -> None:
            self.calls += 1
            raise RuntimeError("connection closed")

    broken = BrokenWS()
    good = FakeWebSocket()
    await bus.subscribe(broken)  # type: ignore[arg-type]
    await bus.subscribe(good)  # type: ignore[arg-type]
    await bus.publish("e", {})
    assert broken.calls == 1
    assert len(good.sent) == 1
    # Subsequent publish: broken should be gone
    broken.calls = 0
    good.sent.clear()
    await bus.publish("e2", {})
    assert broken.calls == 0
    assert len(good.sent) == 1


@pytest.mark.asyncio
async def test_publish_swallows_bad_payload():
    """Non-serialisable payload does not raise to the caller."""
    bus = WSEventBus()
    ws = FakeWebSocket()
    await bus.subscribe(ws)  # type: ignore[arg-type]
    # set() is not JSON-serialisable
    await bus.publish("bad", {"x": set()})  # type: ignore[dict-item]
    assert ws.sent == [], "bad payload should not have been sent"
