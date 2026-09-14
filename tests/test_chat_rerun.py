"""Chat rerun tests: edit + resend / retry (supersede, don't delete).

Covers ``ChatLoop.rerun_turn`` + the ``POST /sessions/{id}/rerun`` route
under the revision-preserving rule (chat timeline step 1, 2026-09-14 —
``docs/CHAT_TIMELINE_PLAN.md`` Phase 1):

* Retry (no content, or identical text): the target row stays live,
  later messages are flagged ``metadata["superseded"]`` — record, not
  deletion — and NO duplicate user row is appended. The orchestrator
  session binding is kept (trace shows ``session_resumed``).
* Edit (content differs): APPENDS a new user message at the end of the
  thread carrying ``metadata.fork_from`` (the original message id) +
  ``metadata.revision``, flags the original target PLUS its tail
  superseded, and runs the turn against the new row. The original
  prompt text survives on record — never mutated in place — so the
  pager has something to flip between.
* History rewrite is in place (engine revert, binding always kept; the
  ``rerun`` audit event carries ``edited`` + ``revision`` + ``fork_from``
  + ``new_message_id`` + ``history_rewrite`` + ``superseded_count``; no
  ``session_rotated``).
* ``message.added`` is emitted exactly once for the appended revision —
  the turn body reuses ``existing_user_msg`` and must NOT re-emit it.
* The transcript reference keeps excluding superseded rows (the live
  thread defines LLM context; view-only change).
* Only user messages are rerunnable (assistant -> TypeError/400);
  unknown ids -> ValueError/404.
* Child delegations of superseded turns are never touched.
* The route maps loop errors to HTTP statuses and returns the new
  assistant message.
"""

from __future__ import annotations

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


class _Bus:
    """Recording event bus (same shape others in this file + the chat
    loop suites use: ``await publish(event, data)``)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        self.events.append((event, data))


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
    event_bus: Any | None = None,
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
        specialist_factory=lambda agent_name, project_name=None: factories.get(agent_name),
        project_dir_resolver=resolver,
        delegation_stores=PerProjectDelegationStores(),
        event_bus=event_bus,
        turn_timeout=10.0,
        model_resolver=lambda agent, project=None: "deepseek-flash",
    )
    return chat


def _new_session(pm: ProjectManager, tmp_path: Path):
    pm.create_project("demo", path=tmp_path)
    return pm.create_session("demo", session_name="s1")


def _trace_events(delegation_id: str) -> list[str]:
    from sweave.runtime.trace_log import read_trace

    return [e.get("event") for e in read_trace(delegation_id)]


def _rerun_events(delegation_id: str) -> list[dict[str, Any]]:
    from sweave.runtime.trace_log import read_trace

    return [e for e in read_trace(delegation_id) if e.get("event") == "rerun"]


# ---------------------------------------------------------------------------
# Retry (same / omitted text): reuse the live target row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_reruns_same_content_and_keeps_session(tmp_path: Path):
    """Omitted content = retry: no duplicate row, binding kept."""
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
    assert loaded.messages[0].id == user_id
    assert loaded.messages[0].content == "hi"
    assert loaded.messages[0].metadata.get("superseded") is not True
    # No revision marker: a retry is not a new version.
    assert "fork_from" not in loaded.messages[0].metadata
    assert "revision" not in loaded.messages[0].metadata
    assert loaded.messages[1].metadata.get("superseded") is True
    assert loaded.messages[2].metadata.get("superseded") is not True
    assert loaded.orchestrator_session_id == binding
    # Retry reuses the engine session (no rotation).
    new_chat_id = loaded.messages[2].metadata["delegation_id"]
    events = _trace_events(new_chat_id)
    assert "session_resumed" in events
    assert "session_created" not in events
    reruns = _rerun_events(new_chat_id)
    assert len(reruns) == 1
    assert reruns[0]["edited"] is False
    assert reruns[0]["revision"] is False
    assert reruns[0]["fork_from"] is None
    assert reruns[0]["new_message_id"] is None
    assert reruns[0]["history_rewrite"] == "not_edited"
    assert reruns[0]["superseded_count"] == 1


@pytest.mark.asyncio
async def test_retry_same_text_is_not_an_edit(tmp_path: Path):
    """An explicit content identical to the target is still a retry:
    the row is reused, never appended (edit = text actually differs)."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "second"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    user_id = pm.get_session(session.id).messages[0].id
    result = await chat.rerun_turn(
        session_id=session.id, from_message_id=user_id, content="hi"
    )
    assert result["content"] == "second"

    loaded = pm.get_session(session.id)
    assert [m.role for m in loaded.messages] == ["user", "assistant", "assistant"]
    assert loaded.messages[0].id == user_id
    assert loaded.messages[0].content == "hi"
    assert loaded.messages[0].metadata.get("superseded") is not True
    assert len(loaded.messages) == 3  # no appended revision row
    new_chat_id = loaded.messages[2].metadata["delegation_id"]
    reruns = _rerun_events(new_chat_id)
    assert reruns[0]["edited"] is False
    assert reruns[0]["history_rewrite"] == "not_edited"


# ---------------------------------------------------------------------------
# Edit: append a revision row, preserve the original
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_appends_revision_and_preserves_original(tmp_path: Path):
    """Edit APPENDS (never mutates): original text survives on record,
    the revision carries fork_from + revision, the tail is superseded."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "edited reply"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    binding = pm.get_session(session.id).orchestrator_session_id
    assert binding

    before = pm.get_session(session.id)
    user_id = before.messages[0].id
    first_assistant_id = before.messages[1].id

    result = await chat.rerun_turn(
        session_id=session.id, from_message_id=user_id, content="hi, edited"
    )
    assert result["content"] == "edited reply"

    loaded = pm.get_session(session.id)
    # Appended at the END of the thread; roles/shape unchanged.
    assert [m.role for m in loaded.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]

    # 1) Original preserved — same row, same id, same text, live thread
    #    exit signalled by the superseded flag (not deletion).
    original = loaded.messages[0]
    assert original.id == user_id
    assert original.content == "hi"
    assert original.metadata.get("superseded") is True

    # 2) The superseded tail keeps its row + id too.
    assert loaded.messages[1].id == first_assistant_id
    assert loaded.messages[1].metadata.get("superseded") is True

    # 3) The revision is a NEW row with fork linkage.
    revision = loaded.messages[2]
    assert revision.role == "user"
    assert revision.content == "hi, edited"
    assert revision.id != user_id
    assert revision.metadata["fork_from"] == user_id
    assert revision.metadata["revision"] is True
    assert revision.metadata.get("superseded") is not True
    assert loaded.messages[3].metadata.get("superseded") is not True

    # 4) No-rotation invariant: same binding, no session_rotated.
    assert loaded.orchestrator_session_id == binding
    new_chat_id = loaded.messages[3].metadata["delegation_id"]
    events = _trace_events(new_chat_id)
    assert "session_resumed" in events
    assert "session_created" not in events

    # 5) The rerun audit event carries the edit + revision info. The
    #    canned wire traces no prompt ids, so the history rewrite lands
    #    via the explicit preamble fallback (input-side, never silent).
    reruns = _rerun_events(new_chat_id)
    assert len(reruns) == 1
    assert reruns[0]["edited"] is True
    assert reruns[0]["revision"] is True
    assert reruns[0]["from_message_id"] == user_id
    assert reruns[0]["fork_from"] == user_id
    assert reruns[0]["new_message_id"] == revision.id
    assert reruns[0]["history_rewrite"] == "preamble_fallback:no_mapping"
    assert reruns[0]["superseded_count"] == 2
    assert "session_rotated" not in reruns[0]


@pytest.mark.asyncio
async def test_edit_emits_message_added_once_for_the_appended_row(tmp_path: Path):
    """``rerun_turn`` emits ``message.added`` for the revision — the
    turn body reuses ``existing_user_msg`` and must NOT re-emit it
    (a second add would duplicate the bubble client-side)."""
    bus = _Bus()
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "edited reply"], event_bus=bus)

    await chat.run_turn(session_id=session.id, user_content="hi")
    user_id = pm.get_session(session.id).messages[0].id
    bus.events.clear()

    await chat.rerun_turn(
        session_id=session.id, from_message_id=user_id, content="hi, edited"
    )

    user_adds = [
        data
        for event, data in bus.events
        if event == "message.added" and data["message"]["role"] == "user"
    ]
    assert len(user_adds) == 1, user_adds
    added = user_adds[0]["message"]
    assert added["content"] == "hi, edited"
    assert added["metadata"]["fork_from"] == user_id
    assert added["metadata"]["revision"] is True
    # Every message.added for this session is scoped + unique by id.
    ids = [
        data["message"]["id"]
        for event, data in bus.events
        if event == "message.added" and data["session_id"] == session.id
    ]
    assert len(ids) == len(set(ids)) == 2  # revision user + assistant reply


# ---------------------------------------------------------------------------
# History rewrite (edit): in place, binding kept, preamble names it
# ---------------------------------------------------------------------------


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
async def test_edit_history_rewrite_collects_the_superseded_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``_rewrite_superseded_history(from_index=target)`` still collects
    the *superseded tail's* prompt ids and reverts in place (binding
    kept). The appended revision row sits after the target but carries
    no delegation trace, so it is not dragged into the revert list."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "edited reply"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    first_assistant_id = pm.get_session(session.id).messages[1].id
    tail_chat_id = pm.get_session(session.id).messages[1].metadata["delegation_id"]
    binding = pm.get_session(session.id).orchestrator_session_id

    # The canned wire traces no prompt ids, so teach the trace reader
    # that the superseded tail's turn sent one engine prompt.
    from sweave.runtime import trace_log

    real_read = trace_log.read_trace

    def fake_read(did: str):
        events = list(real_read(did))
        if did == tail_chat_id:
            events.append({"event": "engine_user_message", "id": "engine-msg-1"})
        return events

    monkeypatch.setattr(trace_log, "read_trace", fake_read)

    captured: list[dict[str, Any]] = []

    async def fake_rewrite(**kwargs):
        captured.append(kwargs)
        return "revert:ok"

    chat.runtime.rewrite_history_before = fake_rewrite  # type: ignore[assignment]

    user_id = pm.get_session(session.id).messages[0].id
    await chat.rerun_turn(
        session_id=session.id, from_message_id=user_id, content="hi, edited"
    )

    # Reverted exactly once, in place, against the kept binding, with
    # the superseded tail's prompt ids — the revision contributes none.
    assert len(captured) == 1
    assert captured[0]["specialist_name"] == "orchestrator"
    assert captured[0]["session_id"] == binding
    assert captured[0]["worktree_path"] == tmp_path
    assert captured[0]["before_ids"] == ["engine-msg-1"]

    loaded = pm.get_session(session.id)
    assert loaded.orchestrator_session_id == binding
    assert loaded.messages[1].id == first_assistant_id
    assert loaded.messages[1].metadata.get("superseded") is True
    new_chat_id = loaded.messages[3].metadata["delegation_id"]
    assert _rerun_events(new_chat_id)[0]["history_rewrite"] == "revert:ok"


# ---------------------------------------------------------------------------
# Transcript exclusion (unchanged): only the live thread defines context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_transcript_reference_uses_only_the_live_thread(tmp_path: Path):
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "edited reply"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    user_id = pm.get_session(session.id).messages[0].id
    await chat.rerun_turn(
        session_id=session.id, from_message_id=user_id, content="hi, edited"
    )

    from sweave.chat.transcript import _transcript_reference

    loaded = pm.get_session(session.id)
    ref = _transcript_reference(transcript_messages=list(loaded.messages), budget=200)
    # 4 rows on record, 2 live: the superseded pair is excluded from
    # the count and from "most recent user message".
    assert len(loaded.messages) == 4
    assert "Conversation has 2 messages" in ref
    assert "Most recent user message: hi, edited" in ref


# ---------------------------------------------------------------------------
# Rejections + children
# ---------------------------------------------------------------------------


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

    # A rejected rerun never appends or supersedes anything.
    after = pm.get_session(session.id)
    assert [m.id for m in after.messages] == [m.id for m in loaded.messages]
    assert all(m.metadata.get("superseded") is not True for m in after.messages)


@pytest.mark.asyncio
async def test_superseded_turn_children_untouched(tmp_path: Path):
    """Child delegations of a superseded turn stay exactly as they were
    — on the retry path and on the edit path (they keep pointing at
    their own attempt's ``chat-*`` id, never the tip's)."""
    from sweave.runtime.delegation_store import Delegation

    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["first", "second", "third"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    chat_id = pm.get_session(session.id).messages[1].metadata["delegation_id"]
    store = await chat.delegation_stores.for_project(tmp_path)
    child = Delegation(
        agent="backend", task="old work", parent_task_id=chat_id, status="review",
    )
    await store.add(child)
    before = store.get(child.delegation_id)
    assert before is not None

    user_id = pm.get_session(session.id).messages[0].id

    # Retry: tail superseded, children untouched.
    await chat.rerun_turn(session_id=session.id, from_message_id=user_id)
    kept = store.get(child.delegation_id)
    assert kept is not None
    assert kept.status == "review"
    assert kept.task == "old work"
    assert kept.parent_task_id == chat_id
    assert kept.archived is False

    # Edit of the ORIGINAL prompt: original + tail superseded, the
    # child still hangs off the first attempt's chat id.
    await chat.rerun_turn(
        session_id=session.id, from_message_id=user_id, content="hi, edited"
    )
    kept_after_edit = store.get(child.delegation_id)
    assert kept_after_edit is not None
    assert kept_after_edit.status == "review"
    assert kept_after_edit.task == "old work"
    assert kept_after_edit.parent_task_id == chat_id
    assert kept_after_edit.archived is False
    # The first attempt's chat delegation is still on record.
    assert store.get(chat_id) is not None


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


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
async def test_segments_keep_think_act_think_order(tmp_path: Path):
    """Interleaved callbacks persist arrival-ordered segments (think,
    text, think) instead of one Thinking blob + one answer — plus the
    joined back-compat thinking copy."""
    from sweave.chat.loop import ChatLoop
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)

    async def interleaved_send(self, body=None, trace=None, on_chunk=None,
                               on_reasoning=None, **kwargs):
        if on_chunk is not None:
            on_chunk("First. ")
        if on_reasoning is not None:
            on_reasoning("hmm, ")
            on_reasoning("wait, ")
        if on_chunk is not None:
            on_chunk("Second.")
        return "First. Second."

    runtime._send_message = interleaved_send  # type: ignore[assignment]
    factories = {"orchestrator": _orchestrator_specialist()}

    def resolver(name: str | None):
        if name is None:
            return None
        proj = pm.get_project(name)
        return proj.path if proj else None

    chat = ChatLoop(
        project_manager=pm,
        specialist_runtime=runtime,
        specialist_factory=lambda agent_name, project_name=None: factories.get(agent_name),
        project_dir_resolver=resolver,
        delegation_stores=PerProjectDelegationStores(),
        event_bus=None,
        turn_timeout=10.0,
        model_resolver=lambda agent, project=None: "deepseek-flash",
    )
    result = await chat.run_turn(session_id=session.id, user_content="hi")
    assert result["content"] == "First. Second."
    kinds = [(s["kind"], s["text"]) for s in result["metadata"]["segments"]]
    assert kinds == [
        ("text", "First. "),
        ("reasoning", "hmm, "),
        ("reasoning", "wait, "),
        ("text", "Second."),
    ]
    # Joined back-compat copy intact.
    assert result["metadata"]["thinking"] == "hmm, wait, "
    # Persisted identically.
    loaded = pm.get_session(session.id)
    assistant = next(m for m in loaded.messages if m.role == "assistant")
    assert assistant.metadata["segments"] == result["metadata"]["segments"]
    assert assistant.metadata["thinking"] == "hmm, wait, "


@pytest.mark.asyncio
async def test_no_segments_key_without_reasoning(tmp_path: Path):
    """Text-only turns carry no segments key (payloads stay small;
    the UI keeps the single-block path)."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["answer"])

    result = await chat.run_turn(session_id=session.id, user_content="hi")
    assert result["content"] == "answer"
    assert "segments" not in result["metadata"]
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
