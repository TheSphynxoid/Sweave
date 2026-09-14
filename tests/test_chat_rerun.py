"""Chat rerun tests: edit + resend / retry (supersede, don't delete).

Covers ``ChatLoop.rerun_turn`` + the ``POST /sessions/{id}/rerun`` route:

* Retry (no content): same user text, orchestrator session binding
  kept (trace shows ``session_resumed``), later messages flagged
  ``metadata["superseded"]`` — record, not deletion.
* Edit (new content): user text replaced, binding KEPT (no-rotation
  invariant — history is rewritten via revert, never discarded; the
  ``rerun`` audit event carries ``edited=True`` + ``history_rewrite``).
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


def _build_chat_loop(
    *,
    pm: ProjectManager,
    send_responses: list[str] | None = None,
    reason_responses: list[str] | None = None,
):
    """ChatLoop with a canned wire (copy of the M1.7 step-2 helper)."""
    from sweave.chat.loop import ChatLoop

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    responses = list(send_responses or ["ok"])
    reasons = list(reason_responses or [])

    async def fake_send(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
        if on_reasoning is not None:
            for r in reasons:
                on_reasoning(r)
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
async def test_edit_rewrites_history_and_keeps_session(tmp_path: Path):
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "edited reply"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    binding = pm.get_session(session.id).orchestrator_session_id
    assert binding

    user_id = pm.get_session(session.id).messages[0].id
    result = await chat.rerun_turn(
        session_id=session.id, from_message_id=user_id, content="hi, edited"
    )
    assert result["content"] == "edited reply"

    loaded = pm.get_session(session.id)
    assert loaded.messages[0].content == "hi, edited"
    assert loaded.messages[1].metadata.get("superseded") is True
    # No-rotation invariant: the same session continues. The canned
    # wire traces no prompt ids, so the rewrite lands via the explicit
    # preamble fallback (input-side, named, never silent).
    assert loaded.orchestrator_session_id == binding
    new_chat_id = loaded.messages[2].metadata["delegation_id"]
    events = _trace_events(new_chat_id)
    assert "session_resumed" in events
    assert "session_created" not in events
    from sweave.runtime.trace_log import read_trace

    reruns = [e for e in read_trace(new_chat_id) if e.get("event") == "rerun"]
    assert len(reruns) == 1
    assert reruns[0]["edited"] is True
    assert reruns[0]["history_rewrite"] == "preamble_fallback:no_mapping"
    assert "session_rotated" not in reruns[0]
    assert reruns[0]["superseded_count"] == 1


@pytest.mark.asyncio
async def test_edit_preamble_fallback_reaches_the_prompt(tmp_path: Path):
    """Without an id mapping the rewrite preamble is composed into the
    re-run's sent prompt (input-side): the model is told the edit
    replaces the superseded turns."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "edited reply"])
    sent_bodies: list[str] = []
    orig_send = chat.runtime._send_message

    async def _recording_send(self, body=None, trace=None, **kwargs):
        try:
            parts = (body or {}).get("parts", [{}])
            sent_bodies.append(str(parts[0].get("text", "")))
        except Exception:  # noqa: BLE001
            pass
        return await orig_send(body=body, trace=trace, **kwargs)

    chat.runtime._send_message = _recording_send  # type: ignore[assignment]

    await chat.run_turn(session_id=session.id, user_content="hi")
    user_id = pm.get_session(session.id).messages[0].id
    await chat.rerun_turn(
        session_id=session.id, from_message_id=user_id, content="hi, edited"
    )
    assert len(sent_bodies) == 2
    assert "[sweave: history rewrite" in sent_bodies[1]
    assert "hi, edited" in sent_bodies[1]


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


# ---------------------------------------------------------------------------
# Thinking capture: reasoning flows to the assistant message metadata.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_turn_persists_thinking_metadata(tmp_path: Path):
    """Reasoning supplied via on_reasoning lands on the persisted
    assistant message as metadata["thinking"] (the durable copy of
    the live chat.thinking deltas)."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(
        pm=pm, send_responses=["answer"], reason_responses=["hmm ", "ok, "]
    )

    result = await chat.run_turn(session_id=session.id, user_content="hi")
    assert result["role"] == "assistant"
    assert result["content"] == "answer"
    assert result["metadata"]["thinking"] == "hmm ok, "

    loaded = pm.get_session(session.id)
    assistant = next(m for m in loaded.messages if m.role == "assistant")
    assert assistant.metadata["thinking"] == "hmm ok, "
    assert assistant.metadata["delegation_id"]


@pytest.mark.asyncio
async def test_no_thinking_key_without_reasoning(tmp_path: Path):
    """Without reasoning parts the metadata carries no thinking key
    (payloads stay small; the UI renders no Thinking block)."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["answer"])

    result = await chat.run_turn(session_id=session.id, user_content="hi")
    assert result["content"] == "answer"
    assert "thinking" not in result["metadata"]

STALL_TEXT = "[chat error: stalled after 300s without data (the stalled work was killed; the session is kept — retry continues it)]"
AUTH_TEXT = "[chat error: APIError: Insufficient balance.]"


@pytest.mark.asyncio
async def test_stall_error_kills_and_keeps_session(tmp_path: Path):
    """A silence-class first-turn failure kills the work and KEEPS the
    binding (no-rotation invariant): retry continues the same session.
    Regression for the stream-probe cascade (two hung-tool timeouts,
    then a third turn that got nothing) — now fixed by killing, not
    by discarding the conversation."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first ok", STALL_TEXT])

    await chat.run_turn(session_id=session.id, user_content="hi")
    binding = pm.get_session(session.id).orchestrator_session_id
    assert binding
    result = await chat.run_turn(session_id=session.id, user_content="hi again")
    assert result["content"] == STALL_TEXT
    assert pm.get_session(session.id).orchestrator_session_id == binding
    new_chat_id = pm.get_session(session.id).messages[3].metadata["delegation_id"]
    events = _trace_events(new_chat_id)
    assert "stall_killed" in events
    assert "turn_killed" in events
    assert "session_rotated_after_stall" not in events


@pytest.mark.asyncio
async def test_content_error_keeps_engine_session(tmp_path: Path):
    """A content-class failure (auth/model rejection) leaves the
    binding alone: the session is healthy, rotating would just burn
    context."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first ok", AUTH_TEXT])

    await chat.run_turn(session_id=session.id, user_content="hi")
    binding = pm.get_session(session.id).orchestrator_session_id
    assert binding
    result = await chat.run_turn(session_id=session.id, user_content="hi again")
    assert result["content"] == AUTH_TEXT
    assert pm.get_session(session.id).orchestrator_session_id == binding


def test_stale_session_error_markers():
    from sweave.chat.loop import _is_stale_session_error

    assert _is_stale_session_error(STALL_TEXT)
    assert _is_stale_session_error("[chat error: orchestrator turn exceeded 900s timeout]")
    assert _is_stale_session_error("[chat error: ReadTimeout: ]")
    assert _is_stale_session_error("[chat error: opencode serve: incomplete turn (x)]")
    assert not _is_stale_session_error(AUTH_TEXT)
    assert not _is_stale_session_error("a fine answer")
