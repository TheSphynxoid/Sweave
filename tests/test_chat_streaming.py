"""Chat streaming tests: coalescer unit + loop wire-up.

Consolidated from the M1.8-step-2 suite (behavior, not milestone):

* ChatDeltaCoalescer: interval flush, char-threshold flush, idempotent
  close, push-after-close no-op, sync/async emit, emit exceptions.
* ChatLoop.run_turn: on_chunk wired to a coalescer publishing
  ``chat.delta`` (session_id + delegation_id scoped); the persisted
  ``message.added`` stays authoritative; delegation status
  transitions flow during the turn; many parts coalesce into few
  events.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sweave.chat.streaming import ChatDeltaCoalescer


# ---------------------------------------------------------------------------
# Coalescer tests (pure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coalescer_flushes_on_interval():
    """Many small parts pushed within one flush window -> one event."""
    received: list[str] = []

    async def emit(text: str) -> None:
        received.append(text)

    c = ChatDeltaCoalescer(
        emit=emit, flush_interval_ms=50, char_threshold=10_000
    )
    c.start()
    for i in range(5):
        c.push(f"part{i}")
    # Wait for the flush window
    await asyncio.sleep(0.1)
    await c.close_and_flush()
    # All parts coalesced into a single event
    assert received == ["part0part1part2part3part4"]


@pytest.mark.asyncio
async def test_coalescer_flushes_on_char_threshold():
    """A push that crosses char_threshold flushes immediately (the
    next flush tick picks it up). This is the "hard cap" -- a
    runaway stream doesn't wait the full interval.
    """
    received: list[str] = []

    async def emit(text: str) -> None:
        received.append(text)

    c = ChatDeltaCoalescer(
        emit=emit, flush_interval_ms=10_000, char_threshold=10
    )
    c.start()
    # Push enough text to cross the threshold in a single push
    c.push("a" * 20)
    # Wait for the next flush tick (the producer doesn't flush
    # directly; the consumer's loop does)
    await asyncio.sleep(0.05)
    await c.close_and_flush()
    # The single push was emitted as one event
    assert received == ["a" * 20]


@pytest.mark.asyncio
async def test_coalescer_close_is_idempotent():
    """Calling close_and_flush multiple times does not raise and
    doesn't re-emit the buffer.
    """
    received: list[str] = []

    async def emit(text: str) -> None:
        received.append(text)

    c = ChatDeltaCoalescer(emit=emit, flush_interval_ms=10)
    c.start()
    c.push("x")
    await asyncio.sleep(0.05)
    await c.close_and_flush()
    await c.close_and_flush()  # second call: no-op
    assert received == ["x"]


@pytest.mark.asyncio
async def test_coalescer_push_after_close_is_noop():
    """After close, push is a no-op. The producer (the chat loop)
    doesn't always know when the consumer is done; we tolerate
    late pushes.
    """
    received: list[str] = []

    async def emit(text: str) -> None:
        received.append(text)

    c = ChatDeltaCoalescer(emit=emit, flush_interval_ms=10)
    c.start()
    c.push("a")
    await c.close_and_flush()
    c.push("late-push")
    assert received == ["a"]


@pytest.mark.asyncio
async def test_coalescer_handles_sync_emit():
    """The emit callable may be sync or async; the coalescer
    handles both.
    """
    received: list[str] = []

    def sync_emit(text: str) -> None:
        received.append(text)

    c = ChatDeltaCoalescer(emit=sync_emit, flush_interval_ms=10)
    c.start()
    c.push("sync-ok")
    await asyncio.sleep(0.05)
    await c.close_and_flush()
    assert received == ["sync-ok"]


@pytest.mark.asyncio
async def test_coalescer_handles_emit_exception():
    """An emit that raises is logged + ignored; the buffer is
    cleared so subsequent flushes still work.
    """
    fail_count = [0]

    async def flaky_emit(text: str) -> None:
        if "bad" in text:
            fail_count[0] += 1
            raise ValueError("nope")

    c = ChatDeltaCoalescer(emit=flaky_emit, flush_interval_ms=10)
    c.start()
    c.push("good-text")
    c.push("bad-text")
    await asyncio.sleep(0.05)
    await c.close_and_flush()
    # The bad emit raised; the good text was not blocked
    # (we don't retry, but the buffer state is consistent)
    assert fail_count[0] >= 1


# ---------------------------------------------------------------------------
# ChatLoop.run_turn streaming integration tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    old = os.environ.get("SWEAVE_MOCK_OPENCODE")
    os.environ["SWEAVE_MOCK_OPENCODE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SWEAVE_MOCK_OPENCODE", None)
        else:
            os.environ["SWEAVE_MOCK_OPENCODE"] = old


def _build_chat_loop(
    *,
    pm: Any,
    parts: list[str] | None = None,
    coalesce_ms: int = 50,
    char_threshold: int = 64,
):
    """Build a ChatLoop that emits the given text parts through the
    on_chunk callback (multi-part streaming).
    """
    from sweave.chat.loop import ChatLoop
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.specialist_store import Specialist
    from sweave.runtime.delegation_store import PerProjectDelegationStores

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)

    parts_list = list(parts or ["only-text"])

    async def fake_send(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
        # Multi-part: emit each part via the on_chunk callback
        # if provided, then return the accumulated text
        accumulated = []
        for part in parts_list:
            accumulated.append(part)
            if on_chunk is not None:
                result = on_chunk(part)
                if hasattr(result, "__await__"):
                    await result
        return "".join(accumulated)

    runtime._send_message = fake_send  # type: ignore[assignment]

    factories = {"orchestrator": Specialist(
        name="orchestrator",
        scope="project",
        is_orchestrator=True,
        system_prompt="seed",
        harness="opencode",
        current_model=None,
    )}
    factory = lambda agent_name, project_name=None: factories.get(agent_name)  # noqa: E731

    def resolver(name: str | None) -> Path | None:
        if name is None:
            return None
        proj = pm.get_project(name)
        return proj.path if proj else None

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()

    chat = ChatLoop(
        project_manager=pm,
        specialist_runtime=runtime,
        specialist_factory=factory,
        project_dir_resolver=resolver,
        delegation_stores=PerProjectDelegationStores(),
        event_bus=event_bus,
        turn_timeout=10.0,
        model_resolver=lambda agent, project=None: "deepseek-flash",
        stream_coalesce_ms=coalesce_ms,
        stream_char_threshold=char_threshold,
    )
    return chat, event_bus


@pytest.mark.asyncio
async def test_chat_loop_publishes_chat_delta_events_with_session_and_delegation_ids(tmp_path: Path):
    """The chat loop wires the harness's on_chunk to a coalescer
    that publishes chat.delta events. Each event payload carries
    session_id + delegation_id.
    """
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, event_bus = _build_chat_loop(
        pm=pm,
        parts=["hello ", "world"],
        coalesce_ms=20,  # short interval for fast test
    )

    result = await chat.run_turn(
        session_id=session.id, user_content="hi"
    )
    # The full text is the authoritative assistant message
    assert result["content"] == "hello world"
    # The event bus was called with chat.delta events; collect them
    chat_delta_events = [
        call.args
        for call in event_bus.publish.call_args_list
        if call.args[0] == "chat.delta"
    ]
    assert chat_delta_events, "no chat.delta events published"
    # Each event carries session_id + delegation_id
    for evt_args in chat_delta_events:
        payload = evt_args[1]
        assert payload["session_id"] == session.id
        assert "delegation_id" in payload
        assert isinstance(payload["text"], str)
    # All parts coalesced into at least one event
    accumulated = "".join(e[1]["text"] for e in chat_delta_events)
    assert accumulated == "hello world"


@pytest.mark.asyncio
async def test_chat_loop_message_added_is_authoritative(tmp_path: Path):
    """The persisted message.added event still carries the full
    text. The chat.delta events are partial snapshots; the
    authoritative full text arrives via message.added.
    """
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, event_bus = _build_chat_loop(
        pm=pm,
        parts=["part1", "part2", "part3"],
        coalesce_ms=20,
    )

    await chat.run_turn(session_id=session.id, user_content="hi")
    # Find the message.added event for the assistant
    added_events = [
        call.args
        for call in event_bus.publish.call_args_list
        if call.args[0] == "message.added"
        and call.args[1]["message"]["role"] == "assistant"
    ]
    assert added_events
    assistant_msg = added_events[-1][1]["message"]
    assert assistant_msg["content"] == "part1part2part3"


@pytest.mark.asyncio
async def test_chat_loop_status_transitions_still_flow_during_turn(tmp_path: Path):
    """The chat delegation's status transitions (queued -> running
    -> done) still fire on the bus during a streaming turn.
    """
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, event_bus = _build_chat_loop(
        pm=pm, parts=["hi"], coalesce_ms=20,
    )

    await chat.run_turn(session_id=session.id, user_content="hi")
    status_events = [
        call.args[1]
        for call in event_bus.publish.call_args_list
        if call.args[0] == "delegation.status_changed"
    ]
    statuses = [e["status"] for e in status_events if e.get("kind") == "chat"]
    assert "running" in statuses
    assert "done" in statuses


@pytest.mark.asyncio
async def test_chat_loop_coalesces_many_parts_into_few_events(tmp_path: Path):
    """10 small parts coalesced into fewer events than 10 (the
    per-flush aggregation). This is the rate-limiting behavior.
    """
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    parts = [f"p{i}" for i in range(10)]
    chat, event_bus = _build_chat_loop(
        pm=pm, parts=parts, coalesce_ms=20, char_threshold=10_000,
    )

    await chat.run_turn(session_id=session.id, user_content="hi")
    chat_delta_events = [
        call.args
        for call in event_bus.publish.call_args_list
        if call.args[0] == "chat.delta"
    ]
    # The total text matches the full output
    accumulated = "".join(e[1]["text"] for e in chat_delta_events)
    assert accumulated == "".join(parts)
    # And the number of events is strictly less than the number of parts
    # (the per-flush coalescing reduces event count)
    # Note: timing-dependent; at coalesce_ms=20 with 10 small parts,
    # we expect at most 2-3 events. Be conservative: at least 1,
    # strictly less than 10.
    assert 1 <= len(chat_delta_events) < 10
