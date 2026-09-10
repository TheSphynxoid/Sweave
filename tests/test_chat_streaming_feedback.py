"""Chat streaming feedback: event ordering + threshold flush.

Pins the two backend fixes behind the "streaming text not shown
bit by bit" report:

* ``chat.delta`` (partial snapshots) must ALL precede the
  authoritative assistant ``message.added``. The old order closed
  the coalescer in ``run_turn``'s finally -- after ``_finalise_turn``
  had already emitted ``message.added`` -- so a trailing delta
  arrived late and the UI rendered a second, never-finalized
  streaming bubble with the turn stuck in "running".
* Pushing past ``char_threshold`` schedules an immediate flush
  instead of waiting for the next timer tick (the documented
  "hard cap" that ``push`` never implemented).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sweave.chat.streaming import ChatDeltaCoalescer


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


def _build_chat_loop(*, pm: Any, send_impl: Any):
    """Build a ChatLoop with a stubbed ``_send_message``."""
    from sweave.chat.loop import ChatLoop
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.specialist_store import Specialist
    from sweave.runtime.delegation_store import PerProjectDelegationStores

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    runtime._send_message = send_impl  # type: ignore[assignment]

    factories = {
        "orchestrator": Specialist(
            name="orchestrator",
            scope="project",
            is_orchestrator=True,
            system_prompt="seed",
            harness="opencode",
            current_model=None,
        )
    }
    factory = lambda agent_name: factories.get(agent_name)  # noqa: E731

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
        model_resolver=lambda agent: "deepseek-flash",
        stream_coalesce_ms=20,
        stream_char_threshold=10_000,
    )
    return chat, event_bus


def _publish_order(event_bus: Any) -> list[tuple[str, dict]]:
    return [(c.args[0], c.args[1]) for c in event_bus.publish.call_args_list]


@pytest.mark.asyncio
async def test_deltas_precede_authoritative_message(tmp_path: Path):
    """All chat.delta events come before the assistant message.added."""
    from sweave.projects import ProjectManager

    async def fake_send(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
        for part in ["hello ", "world"]:
            if on_chunk is not None:
                result = on_chunk(part)
                if hasattr(result, "__await__"):
                    await result
        return "hello world"

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, event_bus = _build_chat_loop(pm=pm, send_impl=fake_send)

    result = await chat.run_turn(session_id=session.id, user_content="hi")
    assert result["content"] == "hello world"

    order = _publish_order(event_bus)
    delta_idx = [i for i, (e, _) in enumerate(order) if e == "chat.delta"]
    added_idx = [
        i
        for i, (e, p) in enumerate(order)
        if e == "message.added" and p["message"]["role"] == "assistant"
    ]
    assert delta_idx, "no chat.delta events published"
    assert added_idx, "no assistant message.added published"
    # Every delta precedes the authoritative message; nothing trails it.
    assert max(delta_idx) < min(added_idx)
    assert "".join(order[i][1]["text"] for i in delta_idx) == "hello world"


@pytest.mark.asyncio
async def test_error_turn_still_orders_delta_before_message(tmp_path: Path):
    """A turn that pushes a partial then fails still flushes first."""
    from sweave.projects import ProjectManager

    async def fake_send(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
        if on_chunk is not None:
            result = on_chunk("partial ")
            if hasattr(result, "__await__"):
                await result
        raise RuntimeError("boom")

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, event_bus = _build_chat_loop(pm=pm, send_impl=fake_send)

    result = await chat.run_turn(session_id=session.id, user_content="hi")
    assert "boom" in result["content"]

    order = _publish_order(event_bus)
    delta_idx = [i for i, (e, _) in enumerate(order) if e == "chat.delta"]
    added_idx = [
        i
        for i, (e, p) in enumerate(order)
        if e == "message.added" and p["message"]["role"] == "assistant"
    ]
    assert delta_idx, "partial delta was lost on the error path"
    assert added_idx
    assert max(delta_idx) < min(added_idx)


@pytest.mark.asyncio
async def test_threshold_push_flushes_without_waiting_for_interval():
    """Crossing char_threshold emits promptly, not on the next tick."""
    received: list[str] = []

    async def emit(text: str) -> None:
        received.append(text)

    c = ChatDeltaCoalescer(emit=emit, flush_interval_ms=10_000, char_threshold=10)
    c.start()
    c.push("a" * 20)
    # Far shorter than the 10s timer: the threshold flush fires on its own.
    await asyncio.sleep(0.2)
    assert received == ["a" * 20]
    await c.close_and_flush()
    assert received == ["a" * 20]
