"""Chat turn recovery contract (client refresh + server restart).

The backend half of the refresh-hardening fix. Four pieces are pinned:

* **Turn-activity registry**: while a turn runs on the server, the
  ChatLoop exposes ``active_turn_snapshot`` (served as
  ``GET /api/sessions/{id}/turn``) with the delegation join key, the
  phase (waiting | streaming | question) and the ACCUMULATED stream
  text -- so a freshly loaded page restores the waiting/streaming
  indicator + the partial bubble instead of showing a dead ``idle``
  thread that lets the user double-send.
* **Detached turns**: a client refresh/disconnect cancels the POST
  but not the turn -- the assistant reply is still persisted +
  emitted (the turn body runs in a loop-owned task, shielded from
  the request handler's cancellation).
* **Double-send guard**: the second send while a turn is active is
  rejected (``TurnActiveError`` -> HTTP 409 carrying the snapshot);
  never silently queued.
* **Boot recovery**: server restart policy = fail cleanly. Every
  non-terminal (running/queued) delegation record orphaned by a
  previous process run is marked ``failed`` with an ``interrupted``
  error -- no phantom active turns after a reload -- while chat
  delegations keep the partial ``output`` the streaming coalescer
  persisted periodically (durable stream snapshot).
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sweave.projects import ProjectManager
from sweave.runtime.delegation_store import (
    PerProjectDelegationStores,
)


# ---------------------------------------------------------------------------
# Hermeticity: same seam as test_m1_7_step2_chat_turn_pipeline (M1.8 gotcha #1
# in docs/GOTCHAS.md "Writing runtime tests").
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
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


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _orchestrator_specialist():
    from sweave.runtime.specialist_store import Specialist

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
    chunks: list[str] | None = None,
    push_interval: float = 0.0,
    final_text: str | None = None,
    send_error: Exception | None = None,
):
    """ChatLoop with a stubbed SpecialistRuntime._send_message.

    ``chunks`` are pushed one at a time via on_chunk (with
    ``push_interval`` between pushes) before returning ``final_text``.
    """
    from sweave.chat.loop import ChatLoop
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    pushed = list(chunks or [])
    text = final_text if final_text is not None else "".join(pushed) or "ok"

    if send_error is not None:

        async def fake_send_error(
            self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs
        ):
            raise send_error

        runtime._send_message = fake_send_error  # type: ignore[assignment]
    else:

        async def fake_send(
            self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs
        ):
            for chunk in pushed:
                if on_chunk is not None:
                    result = on_chunk(chunk)
                    if hasattr(result, "__await__"):
                        await result
                if push_interval:
                    await asyncio.sleep(push_interval)
            return text

        runtime._send_message = fake_send  # type: ignore[assignment]

    factories = {"orchestrator": _orchestrator_specialist()}
    factory = lambda agent_name, project_name=None: factories.get(agent_name)  # noqa: E731

    def resolver(name: str | None):
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
        stream_coalesce_ms=20,
        stream_char_threshold=10_000,
    )
    chat.stream_persist_interval = 0.1
    return chat, event_bus


async def _wait_for_active(chat, session_id: str, timeout: float = 2.0) -> dict:
    """Poll until the session's turn-activity registry has an entry."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        snap = chat.active_turn_snapshot(session_id)
        if snap is not None:
            return snap
        await asyncio.sleep(0.02)
    raise AssertionError("turn never registered in the activity registry")


async def _wait_for_idle(chat, session_id: str, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if chat.active_turn_snapshot(session_id) is None:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("turn never unregistered from the activity registry")


# ---------------------------------------------------------------------------
# Registry: active-turn snapshot during a turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_during_streaming_carries_partial_text(tmp_path):
    """A reconnecting client gets delegation id + streaming phase +
    the accumulated reply text while the turn is still running."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _bus = _build_chat_loop(
        pm=pm,
        chunks=["hello ", "world "],
        push_interval=0.15,
        final_text="hello world tail",
    )

    task = asyncio.create_task(
        chat.run_turn(session_id=session.id, user_content="hi")
    )
    snap = await _wait_for_active(chat, session.id)
    assert snap["session_id"] == session.id
    assert snap["delegation_id"] and snap["delegation_id"].startswith("chat-")
    assert snap["status"] == "running"

    # Let some deltas flow (coalescer flush interval is 20ms here).
    for _ in range(40):
        await asyncio.sleep(0.05)
        snap = chat.active_turn_snapshot(session.id)
        if snap["phase"] == "streaming" and snap["stream_text"]:
            break
    assert snap["phase"] == "streaming"
    assert snap["stream_text"], "no stream text in the snapshot"

    result = await task
    assert result["content"] == "hello world tail"
    # Register cleared after completion.
    await _wait_for_idle(chat, session.id)
    assert chat.active_turn_snapshot(session.id) is None


@pytest.mark.asyncio
async def test_snapshot_empty_turn_phase_is_waiting(tmp_path):
    """Before the first delta the phase is 'waiting' (the UI shows the
    waiting indicator, no phantom bubble)."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _bus = _build_chat_loop(
        pm=pm, chunks=[], final_text="done", push_interval=0.5
    )

    task = asyncio.create_task(
        chat.run_turn(session_id=session.id, user_content="hi")
    )
    snap = await _wait_for_active(chat, session.id)
    assert snap["phase"] == "waiting"
    assert snap["stream_text"] == ""

    result = await task
    assert result["content"] == "done"


@pytest.mark.asyncio
async def test_partial_stream_persisted_on_delegation_record(tmp_path):
    """Durable stream snapshot: the accumulated partial text is
    persisted onto the chat delegation record (``output``) while the
    turn runs, so a hard server death leaves the already-streamed
    text on disk."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _bus = _build_chat_loop(
        pm=pm,
        chunks=["alpha ", "beta ", "gamma "],
        push_interval=0.2,
        final_text="alpha beta gamma final",
    )
    chat.stream_persist_interval = 0.05

    task = asyncio.create_task(
        chat.run_turn(session_id=session.id, user_content="hi")
    )
    partial_seen = False
    for _ in range(60):
        await asyncio.sleep(0.05)
        snap = chat.active_turn_snapshot(session.id)
        if snap is None:
            break
        store = await chat.delegation_stores.for_project(tmp_path)
        recs = [r for r in store.list() if r.kind == "chat"]
        rec = recs[0] if recs else None
        if rec is not None and rec.output == snap["stream_text"] != "":
            partial_seen = True
            break
    result = await task
    assert result["content"] == "alpha beta gamma final"
    assert partial_seen, "partial stream text never hit the delegation record"


# ---------------------------------------------------------------------------
# Detached turns: client refresh must not kill the turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_disconnect_turn_continues_detached(tmp_path):
    """Cancelling the run_turn coroutine (uvicorn cancels the HTTP
    handler on client disconnect) must NOT cancel the turn: the reply
    is still persisted and emitted."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, bus = _build_chat_loop(
        pm=pm,
        chunks=["partial-A ", "partial-B "],
        push_interval=0.15,
        final_text="partial-A partial-B done",
    )

    task = asyncio.create_task(
        chat.run_turn(session_id=session.id, user_content="hi")
    )
    await _wait_for_active(chat, session.id)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    # The turn keeps running in the loop-owned task; wait for it to
    # finish, then check the durable result.
    await _wait_for_idle(chat, session.id, timeout=10.0)
    loaded = pm.get_session(session.id)
    assistant = [m for m in loaded.messages if m.role == "assistant"]
    assert len(assistant) == 1
    assert assistant[0].content == "partial-A partial-B done"
    # message.added (assistant) was emitted after the disconnect.
    added = [
        c.args[1]
        for c in bus.publish.call_args_list
        if c.args[0] == "message.added"
    ]
    assert any(
        m["role"] == "assistant" for m in [a["message"] for a in added]
    )


@pytest.mark.asyncio
async def test_mid_body_crash_marks_delegation_failed(tmp_path):
    """A crash during the turn body (before _finalise_turn) marks the
    chat delegation ``failed`` -- no phantom running record."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")

    chat, _bus = _build_chat_loop(
        pm=pm, chunks=["something "], send_error=KeyError("serve exploded")
    )
    with suppress(Exception):
        await chat.run_turn(session_id=session.id, user_content="hi")
    await _wait_for_idle(chat, session.id)

    store = await chat.delegation_stores.for_project(tmp_path)
    chat_recs = [r for r in store.list() if r.kind == "chat"]
    assert len(chat_recs) == 1
    rec = chat_recs[0]
    assert rec.status == "failed"
    assert rec.error is not None and "KeyError" in rec.error


# ---------------------------------------------------------------------------
# Double-send guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_send_rejected_with_snapshot(tmp_path):
    """Second send while a turn is active: TurnActiveError with the
    ACTIVE turn's snapshot (the same payload GET .../turn serves)."""
    from sweave.chat.loop import TurnActiveError

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _bus = _build_chat_loop(
        pm=pm, chunks=["x "], push_interval=0.4, final_text="x done"
    )

    task = asyncio.create_task(
        chat.run_turn(session_id=session.id, user_content="first")
    )
    await _wait_for_active(chat, session.id)
    with pytest.raises(TurnActiveError) as ei:
        await chat.run_turn(session_id=session.id, user_content="second")
    assert ei.value.snapshot["session_id"] == session.id
    assert ei.value.snapshot["status"] == "running"

    await task
    await _wait_for_idle(chat, session.id)
    # The rejected second send persisted nothing.
    loaded = pm.get_session(session.id)
    assert [m.content for m in loaded.messages].count("second") == 0


@pytest.mark.asyncio
async def test_double_send_route_returns_409_with_snapshot(tmp_path):
    """The messages route maps TurnActiveError to HTTP 409 with
    ``{error: 'turn_active', turn: snapshot}``."""
    from fastapi import HTTPException

    from sweave.chat.loop import TurnActiveError
    from sweave.web.routers.projects import (
        MessageCreate,
        api_add_message,
    )

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    snapshot = {
        "session_id": session.id,
        "delegation_id": "chat-abc",
        "status": "running",
        "phase": "streaming",
        "started_at": None,
        "stream_text": "partial",
        "thinking_text": "",
        "pending_question": False,
    }

    class BusyLoop:
        async def run_turn(self, **kwargs):
            raise TurnActiveError("active", snapshot=snapshot)

    state = MagicMock()
    state.chat_loop = BusyLoop()

    with pytest.raises(HTTPException) as ei:
        await api_add_message(
            session.id, MessageCreate(role="user", content="dup"), state
        )
    assert ei.value.status_code == 409
    detail = ei.value.detail
    assert detail["error"] == "turn_active"
    assert detail["turn"]["delegation_id"] == "chat-abc"


# ---------------------------------------------------------------------------
# GET /api/sessions/{id}/turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_turn_endpoint_idle_then_active_then_idle(tmp_path):
    from sweave.web.routers.projects import api_get_session_turn

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _bus = _build_chat_loop(
        pm=pm,
        chunks=["grow "],
        push_interval=0.2,
        final_text="grow done",
    )
    state = MagicMock()
    state.chat_loop = chat

    idle = await api_get_session_turn(session.id, state)
    assert idle == {"active": False, "turn": None}

    task = asyncio.create_task(
        chat.run_turn(session_id=session.id, user_content="hi")
    )
    await _wait_for_active(chat, session.id)
    data = await api_get_session_turn(session.id, state)
    assert data["active"] is True
    turn = data["turn"]
    assert turn["delegation_id"]
    assert turn["status"] == "running"

    result = await task
    assert result["content"] == "grow done"
    await _wait_for_idle(chat, session.id)
    after = await api_get_session_turn(session.id, state)
    assert after["active"] is False


# ---------------------------------------------------------------------------
# Boot recovery: stale non-terminal delegations -> failed
# ---------------------------------------------------------------------------


def _seed_delegations(dir_path: Path, records: list[dict]) -> None:
    (dir_path / ".sweave").mkdir(parents=True, exist_ok=True)
    (dir_path / ".sweave" / "delegations.json").write_text(
        json.dumps({"delegations": records}), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_recover_interrupted_marks_running_and_queued_failed(tmp_path):
    from sweave.runtime.delegation_store import Delegation, DelegationStore

    record = Delegation(
        delegation_id="chat-stale1",
        agent="orchestrator",
        task="hi",
        kind="chat",
        status="running",
        output="partial stream text",  # durable snapshot from streaming
    ).to_dict()
    record["schema_version"] = None  # below-gate record; from_dict migrates
    record["schema_version"] = Delegation().schema_version
    _seed_delegations(
        tmp_path,
        [
            record,
            Delegation(
                delegation_id="task-queued",
                agent="backend",
                task="t",
                status="queued",
            ).to_dict(),
            Delegation(
                delegation_id="task-done",
                agent="backend",
                task="t",
                kind="task",
                status="done",
            ).to_dict(),
            Delegation(
                delegation_id="task-review",
                agent="backend",
                task="t",
                status="review",
            ).to_dict(),
        ],
    )
    store = DelegationStore(tmp_path)
    n = await store.recover_interrupted()
    assert n == 2
    by_id = {r.delegation_id: r for r in store.list()}
    assert by_id["chat-stale1"].status == "failed"
    assert "interrupted" in by_id["chat-stale1"].error
    # The durable partial output survives the recovery.
    assert by_id["chat-stale1"].output == "partial stream text"
    assert by_id["chat-stale1"].completed_at is not None
    assert by_id["task-queued"].status == "failed"
    # Terminal/awaiting-review records are untouched.
    assert by_id["task-done"].status == "done"
    assert by_id["task-review"].status == "review"
    # Idempotent: a second recovery pass recovers nothing.
    assert await store.recover_interrupted() == 0


@pytest.mark.asyncio
async def test_boot_recovery_helper_scans_all_projects(tmp_path):
    from sweave.runtime.delegation_store import Delegation
    from sweave.web.server import _recover_interrupted_delegations

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    _seed_delegations(
        tmp_path,
        [
            Delegation(
                delegation_id="chat-stale2",
                agent="orchestrator",
                task="hi",
                kind="chat",
                status="running",
                output="lost tail",
            ).to_dict(),
        ],
    )
    stores = PerProjectDelegationStores()
    n = await _recover_interrupted_delegations(stores, pm)
    assert n == 1
    store = await stores.for_project(tmp_path)
    rec = store.get("chat-stale2")
    assert rec is not None
    assert rec.status == "failed"
    assert rec.output == "lost tail"


# ---------------------------------------------------------------------------
# Event hygiene: registry cleanup on crash paths (no leaked snapshot)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_cleared_after_error_turn(tmp_path):
    """Turn finishes with an explicit error message and the registry
    is cleared, so a follow-up turn starts immediately (guard passes)."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _bus = _build_chat_loop(pm=pm, send_error=ConnectionError("x"))

    result = await chat.run_turn(session_id=session.id, user_content="hi")
    assert "ConnectionError" in result["content"]
    await _wait_for_idle(chat, session.id)

    # A second canned reply on the SAME loop: the guard must pass.
    async def fast_reply(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
        return "again"

    chat.runtime._send_message = fast_reply  # type: ignore[assignment]
    result2 = await chat.run_turn(session_id=session.id, user_content="again")
    assert result2["content"] == "again"


@pytest.mark.asyncio
async def test_followup_turn_runs_after_turn_completes(tmp_path):
    """The guard releases: a send after the previous turn completes
    runs normally (serial chat still works)."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, _bus = _build_chat_loop(pm=pm, chunks=[], final_text="r1")
    await chat.run_turn(session_id=session.id, user_content="a")

    chat.runtime._send_message = (  # type: ignore[union-attr]
        lambda *a, **k: _static_reply("r2")
    )  # type: ignore[assignment]
    result = await chat.run_turn(session_id=session.id, user_content="b")
    assert result["content"] == "r2"
    loaded = pm.get_session(session.id)
    assert [m.content for m in loaded.messages if m.role == "assistant"] == [
        "r1",
        "r2",
    ]


async def _static_reply(text: str):
    return text
