"""One persisted assistant message per orchestrator round (2026-09-11).

Transparency fix: a turn that defers used to persist ONLY the synthesis
result — the first-turn narration (and any failed synthesis's context)
vanished from the transcript (the incident's ``[chat error:
ReadTimeout: ]``-only final message). Now the first-turn reply persists
immediately as round 0 (``turn_final: False``) before the child wait,
and the synthesis lands as round 1 (final). The UI renders round-0
messages collapsible; the streaming protocol scopes bubbles per round
(``chat.delta`` carries ``round``).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sweave.projects import ProjectManager
from sweave.runtime.delegation_store import Delegation, PerProjectDelegationStores
from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist


# Hermeticity: same seam as test_m1_7_step2 (GOTCHAS "Writing runtime
# tests" #1) — without it SpecialistRuntime.run boots a real opencode
# serve and the turn hangs to its timeout.
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


def _orchestrator_specialist() -> Specialist:
    return Specialist(
        name="orchestrator",
        scope="project",
        is_orchestrator=True,
        system_prompt="seed",
        harness="opencode",
        current_model=None,
    )


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        self.events.append((event, dict(data)))


def _build_chat_loop(*, pm: ProjectManager, responses: list[str], bus: _Bus):
    from sweave.chat.loop import ChatLoop

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    queue = list(responses)

    async def fake_send(self, body=None, trace=None, on_chunk=None,
                        on_reasoning=None, **kwargs):
        text = queue.pop(0) if queue else "ok"
        if on_chunk is not None:
            on_chunk(text)
        return text

    runtime._send_message = fake_send  # type: ignore[assignment]

    def resolver(name: str | None):
        if name is None:
            return None
        proj = pm.get_project(name)
        return proj.path if proj else None

    stores = PerProjectDelegationStores()
    chat = ChatLoop(
        project_manager=pm,
        specialist_runtime=runtime,
        specialist_factory=lambda agent_name, project_name=None: _orchestrator_specialist(),
        project_dir_resolver=resolver,
        delegation_stores=stores,
        event_bus=bus,
        turn_timeout=10.0,
        model_resolver=lambda agent, project=None: "deepseek-flash",
    )
    return chat, stores


async def _inject_done_child(stores: PerProjectDelegationStores, project_dir: Path) -> None:
    store = await stores.for_project(project_dir)
    for rec in store.list():
        if rec.kind == "chat":
            await store.add(Delegation(
                delegation_id="child-round-1",
                task_id="child-round-1",
                agent="backend",
                model="hy3",
                task="do it",
                parent_task_id=rec.delegation_id,
                project_name="demo",
                status="done",
                output="did it",
                completed_at=datetime.now(),
            ))
            return
    raise AssertionError("no chat delegation to attach the child to")


def _assistant_messages(pm: ProjectManager, session_id: str):
    loaded = pm.get_session(session_id)
    assert loaded is not None
    return [m for m in loaded.messages if m.role == "assistant"]


@pytest.mark.asyncio
async def test_first_and_synthesis_persist_as_two_rounds(tmp_path: Path):
    """Defer turn: round-0 narration persists before the wait, round-1
    synthesis persists after; delegation output stays the final text."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    bus = _Bus()
    chat, stores = _build_chat_loop(
        pm=pm,
        responses=["Dispatching to backend.", "Backend finished the work."],
        bus=bus,
    )
    # Inject the child when the first turn runs (same trick as the
    # synthesis tests): wrap _send_message once.
    orig = chat.runtime._send_message
    injected: list[bool] = []

    async def injecting_send(self, body=None, trace=None, on_chunk=None,
                             on_reasoning=None, **kwargs):
        out = await orig(self, body, trace, on_chunk, on_reasoning, **kwargs)
        if not injected:
            injected.append(True)
            await _inject_done_child(stores, tmp_path)
        return out

    chat.runtime._send_message = injecting_send  # type: ignore[assignment]

    result = await chat.run_turn(session_id=session.id, user_content="do X")
    assert result["content"] == "Backend finished the work."

    assistants = _assistant_messages(pm, session.id)
    assert len(assistants) == 2
    first, final = assistants
    assert first.content.startswith("Dispatching to backend.")
    assert first.metadata["turn_round"] == 0
    assert first.metadata["turn_final"] is False
    assert first.metadata["delegation_id"]
    assert final.content == "Backend finished the work."
    assert final.metadata["turn_round"] == 1
    assert final.metadata["turn_final"] is True
    assert final.metadata["delegation_id"] == first.metadata["delegation_id"]

    store = await stores.for_project(tmp_path)
    chat_records = [r for r in store.list() if r.kind == "chat"]
    assert len(chat_records) == 1
    assert chat_records[0].status == "done"
    assert chat_records[0].output == "Backend finished the work."

    rounds = {
        e["round"]
        for name, e in bus.events
        if name == "chat.delta"
    }
    assert rounds == {0, 1}
    added = [
        e["message"] for name, e in bus.events if name == "message.added"
    ]
    assert sum(1 for m in added if m["role"] == "assistant") == 2


@pytest.mark.asyncio
async def test_failed_synthesis_keeps_round_zero(tmp_path: Path):
    """The incident shape: synthesis dies, but round 0 survives — the
    thread shows narration + error instead of error-only."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    bus = _Bus()
    chat, stores = _build_chat_loop(
        pm=pm,
        responses=[
            "Dispatching to backend.",
            "[chat error: ReadTimeout: ]",
        ],
        bus=bus,
    )
    orig = chat.runtime._send_message
    injected: list[bool] = []

    async def injecting_send(self, body=None, trace=None, on_chunk=None,
                             on_reasoning=None, **kwargs):
        out = await orig(self, body, trace, on_chunk, on_reasoning, **kwargs)
        if not injected:
            injected.append(True)
            await _inject_done_child(stores, tmp_path)
        return out

    chat.runtime._send_message = injecting_send  # type: ignore[assignment]

    result = await chat.run_turn(session_id=session.id, user_content="do X")
    assert result["content"] == "[chat error: ReadTimeout: ]"

    assistants = _assistant_messages(pm, session.id)
    assert len(assistants) == 2
    assert assistants[0].content.startswith("Dispatching to backend.")
    assert assistants[0].metadata["turn_final"] is False
    assert assistants[1].content == "[chat error: ReadTimeout: ]"
    assert assistants[1].metadata["turn_round"] == 1

    store = await stores.for_project(tmp_path)
    chat_records = [r for r in store.list() if r.kind == "chat"]
    assert chat_records[0].status == "failed"
    assert chat_records[0].error == "[chat error: ReadTimeout: ]"


@pytest.mark.asyncio
async def test_fast_path_stays_single_final_message(tmp_path: Path):
    """Childless turn: unchanged shape, plus round metadata."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _stores = _build_chat_loop(
        pm=pm, responses=["hello back"], bus=_Bus()
    )
    result = await chat.run_turn(session_id=session.id, user_content="hello")
    assert result["content"] == "hello back"
    assistants = _assistant_messages(pm, session.id)
    assert len(assistants) == 1
    assert assistants[0].metadata["turn_round"] == 0
    assert assistants[0].metadata["turn_final"] is True
