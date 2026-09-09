"""Chat rerun tests: edit + resend / retry (supersede, don't delete).

Covers ``ChatLoop.rerun_turn`` + the ``POST /sessions/{id}/rerun`` route:

* Retry (no content): same user text, orchestrator session binding
  kept (trace shows ``session_resumed``), later messages flagged
  ``metadata["superseded"]`` — record, not deletion.
* Edit (new content): user text replaced, binding rotated (trace
  shows ``session_created`` on the new turn + the ``rerun`` audit
  event with ``edited=True``).
* Only user messages are rerunnable (assistant -> TypeError/400);
  unknown ids -> ValueError/404.
* Child delegations of superseded turns are never touched.
* The route maps loop errors to HTTP statuses and returns the new
  assistant message.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sweave.projects import ProjectManager
from sweave.runtime.delegation_store import PerProjectDelegationStores
from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist


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


def _build_chat_loop(*, pm: ProjectManager, send_responses: list[str] | None = None):
    """ChatLoop with a canned wire (copy of the M1.7 step-2 helper)."""
    from sweave.chat.loop import ChatLoop

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    responses = list(send_responses or ["ok"])

    async def fake_send(self, body, trace, on_chunk=None):
        if on_chunk is not None:
            on_chunk(responses[0] if responses else "ok")
        if not responses:
            return "ok"
        return responses.pop(0)

    runtime._send_message = fake_send  # type: ignore[assignment]
    factories = {"orchestrator": _orchestrator_specialist()}

    def resolver(name: str | None) -> Path | None:
        if name is None:
            return None
        proj = pm.get_project(name)
        return proj.path if proj else None

    chat = ChatLoop(
        project_manager=pm,
        specialist_runtime=runtime,
        specialist_factory=lambda agent_name: factories.get(agent_name),
        project_dir_resolver=resolver,
        delegation_stores=PerProjectDelegationStores(),
        event_bus=None,
        turn_timeout=10.0,
        model_resolver=lambda agent: "deepseek-flash",
    )
    return chat


def _new_session(pm: ProjectManager, tmp_path: Path):
    pm.create_project("demo", path=tmp_path)
    return pm.create_session("demo", session_name="s1")


def _trace_events(delegation_id: str) -> list[str]:
    from sweave.runtime.trace_log import read_trace

    return [e.get("event") for e in read_trace(delegation_id)]


@pytest.mark.asyncio
async def test_retry_reruns_same_content_and_keeps_session(tmp_path: Path):
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "second"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    binding = pm.get_session(session.id).orchestrator_session_id
    assert binding

    user_id = pm.get_session(session.id).messages[0].id
    result = await chat.rerun_turn(session_id=session.id, from_message_id=user_id)
    assert result["role"] == "assistant"
    assert result["content"] == "second"

    loaded = pm.get_session(session.id)
    # Same text, same binding, no duplicate user row.
    assert [m.role for m in loaded.messages] == ["user", "assistant", "assistant"]
    assert loaded.messages[0].content == "hi"
    assert loaded.messages[0].metadata.get("superseded") is not True
    assert loaded.messages[1].metadata.get("superseded") is True
    assert loaded.messages[2].metadata.get("superseded") is not True
    assert loaded.orchestrator_session_id == binding
    # Retry reuses the engine session (no rotation).
    new_chat_id = loaded.messages[2].metadata["delegation_id"]
    assert "session_resumed" in _trace_events(new_chat_id)


@pytest.mark.asyncio
async def test_edit_replaces_content_and_rotates_session(tmp_path: Path):
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "edited reply"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    assert pm.get_session(session.id).orchestrator_session_id

    user_id = pm.get_session(session.id).messages[0].id
    result = await chat.rerun_turn(
        session_id=session.id, from_message_id=user_id, content="hi, edited"
    )
    assert result["content"] == "edited reply"

    loaded = pm.get_session(session.id)
    assert loaded.messages[0].content == "hi, edited"
    assert loaded.messages[1].metadata.get("superseded") is True
    new_chat_id = loaded.messages[2].metadata["delegation_id"]
    events = _trace_events(new_chat_id)
    # Rotation: the engine session was recreated for the edited turn.
    assert "session_created" in events
    # ... via the loop's own reset (not a stale binding surviving).
    from sweave.runtime.trace_log import read_trace

    reruns = [e for e in read_trace(new_chat_id) if e.get("event") == "rerun"]
    assert len(reruns) == 1
    assert reruns[0]["edited"] is True
    assert reruns[0]["session_rotated"] is True
    assert reruns[0]["superseded_count"] == 1


@pytest.mark.asyncio
async def test_rerun_rejects_non_user_and_unknown_ids(tmp_path: Path):
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    loaded = pm.get_session(session.id)
    assistant_id = loaded.messages[1].id

    with pytest.raises(TypeError):
        await chat.rerun_turn(session_id=session.id, from_message_id=assistant_id)
    with pytest.raises(ValueError):
        await chat.rerun_turn(session_id=session.id, from_message_id="nope")
    with pytest.raises(ValueError):
        await chat.rerun_turn(session_id="no-session", from_message_id="x")


@pytest.mark.asyncio
async def test_superseded_turn_children_untouched(tmp_path: Path):
    """Child delegations of a superseded turn stay exactly as they were."""
    from sweave.runtime.delegation_store import Delegation

    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "second"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    chat_id = pm.get_session(session.id).messages[1].metadata["delegation_id"]
    store = await chat.delegation_stores.for_project(tmp_path)
    child = Delegation(
        agent="backend", task="old work", parent_task_id=chat_id, status="review",
    )
    await store.add(child)

    user_id = pm.get_session(session.id).messages[0].id
    await chat.rerun_turn(session_id=session.id, from_message_id=user_id)

    kept = store.get(child.delegation_id)
    assert kept is not None
    assert kept.status == "review"
    assert kept.task == "old work"


@pytest.mark.asyncio
async def test_rerun_route_shape_and_errors():
    """The route returns the assistant message; errors map to statuses."""
    from fastapi import HTTPException

    from sweave.web.routers.projects import RerunRequest, api_rerun_turn

    loop = AsyncMock()
    loop.rerun_turn.return_value = {"role": "assistant", "content": "again"}
    state = SimpleNamespace(chat_loop=loop)

    ok = await api_rerun_turn(
        "s1", RerunRequest(from_message_id="u1", content="edited"), state
    )
    assert ok == {"success": True, "assistant": {"role": "assistant", "content": "again"}}
    loop.rerun_turn.assert_awaited_once_with(
        session_id="s1", from_message_id="u1", content="edited"
    )

    loop.rerun_turn.side_effect = ValueError("Session 's1' not found")
    with pytest.raises(HTTPException) as exc:
        await api_rerun_turn("s1", RerunRequest(from_message_id="u1"), state)
    assert exc.value.status_code == 404

    loop.rerun_turn.side_effect = TypeError("Can only rerun from a user message")
    with pytest.raises(HTTPException) as exc:
        await api_rerun_turn("s1", RerunRequest(from_message_id="a1"), state)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await api_rerun_turn("s1", RerunRequest(from_message_id="u1"), SimpleNamespace(chat_loop=None))
    assert exc.value.status_code == 500
