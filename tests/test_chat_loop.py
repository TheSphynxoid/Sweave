"""Chat loop tests: turn pipeline, synthesis, rerun, rounds, thinking.

Behavioral consolidation of the M1.7-step-2, M1.7-step-3 (backend
half), rerun, and rounds suites: one shared builder, one mock-env
fixture, no milestone framing. (Pure synthesis-prompt builder tests
live in test_chat_synthesis.py; streaming/coalescer tests in
test_chat_streaming.py; Delegation.kind schema tests in
test_delegation_store.py.)

Covers:

* Turn pipeline: user in -> orchestrator runs -> assistant out (both
  persisted); per-Session orchestrator binding (independent across
  sessions); double-send guard (TurnActiveError, HTTP 409); explicit
  error messages on unreachable/timeout (never silent); chat
  Delegation record; parallel sessions run in parallel.
* Synthesis backend: childless fast path (single final message,
  auto-done); defer turns persist round-0 narration before the child
  wait and synthesis as round 1 (failed synthesis keeps round 0;
  failing children still synthesize).
* Rerun (edit + resend / retry): same-text retry keeps the binding;
  edits rewrite history in place (revert; explicit preamble fallback
  without an id mapping); only user messages rerunnable; superseded
  turns keep their children; route shape/errors.
* Thinking: reasoning lands on metadata.thinking; interleaved
  think/text persists arrival-ordered metadata.segments (+ the joined
  back-compat copy); text-only turns carry neither key.
* Stall/content errors: silence-class failures kill and keep the
  session; content-class failures keep it untouched.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from sweave.projects import ProjectManager
from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
)
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _orchestrator_specialist() -> Specialist:
    return Specialist(
        name="orchestrator",
        scope="project",
        is_orchestrator=True,
        system_prompt="seed",
        harness="opencode",
        current_model=None,
    )


def _new_session(pm: ProjectManager, tmp_path: Path):
    pm.create_project("demo", path=tmp_path)
    return pm.create_session("demo", session_name="s1")


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        self.events.append((event, dict(data)))


def _build_chat_loop(
    *,
    pm: ProjectManager,
    send_responses: list[str] | None = None,
    reason_responses: list[str] | None = None,
    send_delay: float = 0.0,
    send_error: Exception | None = None,
    event_bus: Any = None,
    turn_timeout: float = 10.0,
    escalation_store: Any = None,
):
    """ChatLoop with a canned wire (unified M1.7/M1.8/rounds helper).

    The runtime's real run() executes (registry runner + session
    lifecycle), but ``_send_message`` returns canned text. One
    response is consumed per send call (FIFO; ``"ok"`` when
    exhausted); each consumed response is also emitted via on_chunk
    (and every reason via on_reasoning) so streaming + thinking
    paths exercise. ``send_error`` raises instead.
    """
    from sweave.chat.loop import ChatLoop

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    responses = list(send_responses or ["ok"])
    reasons = list(reason_responses or [])

    if send_error is not None:
        async def fake_send_error(self, body=None, trace=None, on_chunk=None,
                                  on_reasoning=None, **kwargs):
            raise send_error
        runtime._send_message = fake_send_error  # type: ignore[assignment]
    else:
        async def fake_send(self, body=None, trace=None, on_chunk=None,
                            on_reasoning=None, **kwargs):
            if send_delay:
                await asyncio.sleep(send_delay)
            for r in reasons:
                if on_reasoning is not None:
                    on_reasoning(r)
            reasons.clear()
            text = responses.pop(0) if responses else "ok"
            if on_chunk is not None:
                on_chunk(text)
            return text

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
        turn_timeout=turn_timeout,
        model_resolver=lambda agent, project=None: "deepseek-flash",
        escalation_store=escalation_store,
    )
    return chat


def _assistant_messages(pm: ProjectManager, session_id: str):
    loaded = pm.get_session(session_id)
    assert loaded is not None
    return [m for m in loaded.messages if m.role == "assistant"]


def _trace_events(delegation_id: str) -> list[str]:
    from sweave.runtime.trace_log import read_trace

    return [e.get("event") for e in read_trace(delegation_id)]


def _rerun_events(delegation_id: str) -> list[dict[str, Any]]:
    from sweave.runtime.trace_log import read_trace

    return [e for e in read_trace(delegation_id) if e.get("event") == "rerun"]


def _inject_done_child(stores: PerProjectDelegationStores, project_dir: Path,
                       *, status: str = "done", error: str = "",
                       output: str = "did it") -> None:
    """Synchronous helper for tests that pre-seed children: finds the
    chat delegation in the store and attaches one terminal child."""
    import asyncio as _aio

    async def _go() -> None:
        store = await stores.for_project(project_dir)
        for rec in store.list():
            if rec.kind == "chat":
                await store.add(Delegation(
                    delegation_id=f"child-{rec.delegation_id[:6]}",
                    task_id=f"child-{rec.delegation_id[:6]}",
                    agent="backend",
                    model="hy3",
                    task="do it",
                    parent_task_id=rec.delegation_id,
                    project_name="demo",
                    status=status,
                    output=output,
                    error=error or None,
                    completed_at=datetime.now(),
                ))
                return
        raise AssertionError("no chat delegation to attach the child to")

    _aio.get_event_loop().run_until_complete(_go())


# ---------------------------------------------------------------------------
# Turn pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_loop_persists_user_and_assistant_messages(tmp_path: Path):
    """End-to-end: user message in -> orchestrator runs -> assistant out.

    Both messages land in Session.messages; the Session is persisted
    after each (so the audit trail and the binding survive restart).
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["hello back"])

    result = await chat.run_turn(
        session_id=session.id, user_content="hello"
    )
    assert result["role"] == "assistant"
    assert result["content"] == "hello back"
    assert result["agent"] == "orchestrator"

    # Reload and inspect the persisted Session
    loaded = pm.get_session(session.id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.messages[0].role == "user"
    assert loaded.messages[0].content == "hello"
    assert loaded.messages[1].role == "assistant"
    assert loaded.messages[1].content == "hello back"
    assert loaded.messages[1].agent == "orchestrator"


@pytest.mark.asyncio
async def test_chat_loop_persists_orchestrator_session_binding(tmp_path: Path):
    """The binding is written: the chat loop runs the runtime with
    session_id callbacks, so the runtime persists the opencode
    session id on the Session record (not on the Specialist record).
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["hi"])

    await chat.run_turn(session_id=session.id, user_content="hi")
    loaded = pm.get_session(session.id)
    # The stub opencode serve returns a canned id; the binding
    # landed on the Session record via the callback.
    assert loaded.orchestrator_session_id is not None
    assert loaded.orchestrator_session_id != ""


@pytest.mark.asyncio
async def test_two_sessions_get_independent_orchestrator_bindings(tmp_path: Path):
    """Two chat turns in two sessions -- two independent orchestrator
    bindings stored on the Session records (per-Session binding).

    The mock opencode serve returns a stable per-specialist id
    (``ses_mock_orchestrator``), so the invariant pinned is placement
    (Session record, not Specialist record), not string inequality:
    the factory returns a fresh Specialist per call (no persistence),
    so the only place the binding can land is the Session record.
    Each session's binding is persisted to its own file on disk.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    s1 = pm.create_session("demo", session_name="s1")
    s2 = pm.create_session("demo", session_name="s2")
    chat = _build_chat_loop(pm=pm, send_responses=["a", "b"])

    await chat.run_turn(session_id=s1.id, user_content="hi")
    await chat.run_turn(session_id=s2.id, user_content="hello")

    loaded_s1 = pm.get_session(s1.id)
    loaded_s2 = pm.get_session(s2.id)
    assert loaded_s1.orchestrator_session_id is not None
    assert loaded_s2.orchestrator_session_id is not None
    assert loaded_s1.orchestrator_session_id == "ses_mock_orchestrator"
    assert loaded_s2.orchestrator_session_id == "ses_mock_orchestrator"


@pytest.mark.asyncio
async def test_chat_loop_double_send_rejected_with_turn_active(tmp_path: Path):
    """Double-send guard: a second user message arriving while a turn
    is active is REJECTED with ``TurnActiveError`` (HTTP 409), never
    silently queued -- a queued POST would hang the client's HTTP
    request for up to ``turn_timeout``, and a refreshed client could
    never see it waiting. The error carries the active turn's
    snapshot.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(
        pm=pm,
        send_responses=["first-reply"],
        send_delay=0.15,
    )

    from sweave.chat.loop import TurnActiveError

    # Fire both concurrently: the second must be rejected, not queued.
    results = await asyncio.gather(
        chat.run_turn(session_id=session.id, user_content="first-msg"),
        chat.run_turn(session_id=session.id, user_content="second-msg"),
        return_exceptions=True,
    )
    # Exactly one of the two raised TurnActiveError (the racing
    # double-send); the other completed as a normal turn.
    errs = [r for r in results if isinstance(r, TurnActiveError)]
    oks = [r for r in results if not isinstance(r, Exception)]
    assert len(oks) == 1
    assert len(errs) == 1
    err = errs[0]
    assert err.snapshot["session_id"] == session.id
    assert err.snapshot["status"] == "running"

    loaded = pm.get_session(session.id)
    assert loaded is not None
    # Only ONE turn ran: one user + one assistant message; the
    # rejected double-send never persisted a message.
    assert len(loaded.messages) == 2
    assistant_msgs = [m for m in loaded.messages if m.role == "assistant"]
    assert assistant_msgs[0].content == "first-reply"


@pytest.mark.asyncio
async def test_chat_loop_orchestrator_unreachable_persists_explicit_error(tmp_path: Path):
    """When the runtime raises, the loop persists an explicit error
    message -- never silent, never swallowed. The user sees the
    failure in the chat.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(
        pm=pm, send_error=ConnectionError("serve unreachable")
    )

    result = await chat.run_turn(
        session_id=session.id, user_content="hello"
    )
    assert result["role"] == "assistant"
    assert "ConnectionError" in result["content"]
    assert "serve unreachable" in result["content"]

    loaded = pm.get_session(session.id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.messages[1].role == "assistant"
    assert "ConnectionError" in loaded.messages[1].content


@pytest.mark.asyncio
async def test_chat_loop_turn_timeout_persists_explicit_error(tmp_path: Path):
    """The turn timeout (asyncio.wait_for) surfaces as an explicit
    error message, not a hung HTTP request.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    # send_delay > turn_timeout forces a TimeoutError.
    chat = _build_chat_loop(pm=pm, send_delay=0.5)
    # Override the timeout to a tiny value so the test is fast.
    chat.turn_timeout = 0.05

    result = await chat.run_turn(
        session_id=session.id, user_content="hello"
    )
    assert result["role"] == "assistant"
    assert "timeout" in result["content"].lower()


@pytest.mark.asyncio
async def test_chat_loop_writes_chat_delegation_record(tmp_path: Path):
    """The chat turn creates a Delegation with kind='chat' and the
    correct session binding. The audit trail exists.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["hi"])

    await chat.run_turn(session_id=session.id, user_content="hello")

    store = await chat.delegation_stores.for_project(tmp_path)
    all_records = store.list()
    chat_records = [r for r in all_records if r.kind == "chat"]
    assert len(chat_records) == 1
    rec = chat_records[0]
    assert rec.agent == "orchestrator"
    assert rec.parent_session_id == session.id
    assert rec.project_name == "demo"
    # Final status: success -> auto-done (implementation children
    # still stop at review per the promotion ruling).
    assert rec.status in {"review", "done"}
    assert rec.output == "hi"


@pytest.mark.asyncio
async def test_chat_loop_different_sessions_run_in_parallel(tmp_path: Path):
    """The per-session lock is per-session, not global. Two sessions
    can run chat turns in parallel.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    s1 = pm.create_session("demo", session_name="s1")
    s2 = pm.create_session("demo", session_name="s2")
    # send_delay 0.1s per call; if the lock were global, two parallel
    # calls would take ~0.2s. We assert they finish in ~0.1s.
    chat = _build_chat_loop(pm=pm, send_delay=0.1)

    t0 = time.monotonic()
    await asyncio.gather(
        chat.run_turn(session_id=s1.id, user_content="a"),
        chat.run_turn(session_id=s2.id, user_content="b"),
    )
    elapsed = time.monotonic() - t0
    # Generous upper bound (10x delay) -- parallel execution should
    # easily fit. A serial global lock would be ~0.2s.
    assert elapsed < 0.5, f"parallel turns took {elapsed:.2f}s; expected <0.5s"


# ---------------------------------------------------------------------------
# Synthesis backend (fast path, defer rounds, failures)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_path_auto_done_single_final_message(tmp_path: Path):
    """Childless turn: the first reply is the final assistant message
    (single message, round 0, final); the chat delegation auto-dones
    with the reply as its output.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(pm=pm, send_responses=["hi back"])

    result = await chat.run_turn(session_id=session.id, user_content="hello")
    assert result["content"] == "hi back"
    assistants = _assistant_messages(pm, session.id)
    assert len(assistants) == 1
    assert assistants[0].metadata["turn_round"] == 0
    assert assistants[0].metadata["turn_final"] is True

    store = await chat.delegation_stores.for_project(tmp_path)
    chat_records = [r for r in store.list() if r.kind == "chat"]
    assert len(chat_records) == 1
    assert chat_records[0].status == "done"
    assert chat_records[0].output == "hi back"


@pytest.mark.asyncio
async def test_defer_turn_persists_narration_then_synthesis(tmp_path: Path):
    """Defer turn: the first-turn narration persists as round 0
    (turn_final False) before the child wait; the synthesis reply
    persists as round 1 (final) under the same delegation id; the
    delegation auto-dones with the final text. Streaming bubbles
    scope per round (chat.delta rounds {0, 1}).
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    bus = _Bus()
    chat = _build_chat_loop(
        pm=pm,
        send_responses=[
            "I'll ask backend to do that.",
            "Backend did the thing.",
        ],
        event_bus=bus,
    )
    # The first-turn stub injects a terminal child (simulating the
    # MCP defer) before returning the first reply.
    orig = chat.runtime._send_message
    injected: list[bool] = []

    async def injecting_send(self, body=None, trace=None, on_chunk=None,
                             on_reasoning=None, **kwargs):
        out = await orig(self, body, trace, on_chunk, on_reasoning, **kwargs)
        if not injected:
            injected.append(True)
            store = await chat.delegation_stores.for_project(tmp_path)
            for rec in store.list():
                if rec.kind == "chat":
                    await store.add(Delegation(
                        delegation_id="child-backend-1",
                        task_id="child-backend-1",
                        agent="backend",
                        model="hy3",
                        task="create hello.py",
                        parent_task_id=rec.delegation_id,
                        project_name="demo",
                        status="done",
                        output="created hello.py printing OK",
                        completed_at=datetime.now(),
                    ))
                    break
        return out

    chat.runtime._send_message = injecting_send  # type: ignore[assignment]

    result = await chat.run_turn(
        session_id=session.id, user_content="ask backend to do X"
    )
    assert result["content"] == "Backend did the thing."

    assistants = _assistant_messages(pm, session.id)
    assert len(assistants) == 2
    first, final = assistants
    assert first.content == "I'll ask backend to do that."
    assert first.metadata["turn_round"] == 0
    assert first.metadata["turn_final"] is False
    assert first.metadata["delegation_id"]
    assert final.content == "Backend did the thing."
    assert final.metadata["turn_round"] == 1
    assert final.metadata["turn_final"] is True
    assert final.metadata["delegation_id"] == first.metadata["delegation_id"]

    store = await chat.delegation_stores.for_project(tmp_path)
    chat_records = [r for r in store.list() if r.kind == "chat"]
    assert len(chat_records) == 1
    assert chat_records[0].status == "done"
    assert chat_records[0].output == "Backend did the thing."

    rounds = {e["round"] for name, e in bus.events if name == "chat.delta"}
    assert rounds == {0, 1}
    added = [e["message"] for name, e in bus.events if name == "message.added"]
    assert sum(1 for m in added if m["role"] == "assistant") == 2


@pytest.mark.asyncio
async def test_failing_child_still_synthesizes(tmp_path: Path):
    """A child failing during the wait does not hang the chat; the
    synthesis runs with the failure included and round 0 survives.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(
        pm=pm,
        send_responses=["trying...", "backend failed; sorry."],
    )
    orig = chat.runtime._send_message
    first_turn = [True]

    async def fake_send(self, body=None, trace=None, on_chunk=None,
                        on_reasoning=None, **kwargs):
        if first_turn[0]:
            first_turn[0] = False
            store = await chat.delegation_stores.for_project(tmp_path)
            for rec in store.list():
                if rec.kind == "chat":
                    await store.add(Delegation(
                        delegation_id="child-1",
                        task_id="child-1",
                        agent="backend",
                        model="hy3",
                        task="x",
                        parent_task_id=rec.delegation_id,
                        project_name="demo",
                        status="failed",
                        error="permission denied",
                        output="",
                        completed_at=datetime.now(),
                    ))
                    break
        out = await orig(self, body, trace, on_chunk, on_reasoning, **kwargs)
        # NOTE: orig pops its own canned queue; mirror the two replies
        # by consuming in order (see send_responses above).
        return out

    chat.runtime._send_message = fake_send  # type: ignore[assignment]

    result = await chat.run_turn(session_id=session.id, user_content="do X")
    assert "backend failed" in result["content"]

    assistants = _assistant_messages(pm, session.id)
    assert len(assistants) == 2
    assert assistants[0].content == "trying..."
    assert assistants[0].metadata["turn_round"] == 0
    assert "backend failed" in assistants[1].content


@pytest.mark.asyncio
async def test_failed_synthesis_keeps_round_zero(tmp_path: Path):
    """The incident shape: synthesis dies, but round 0 survives — the
    thread shows narration + error instead of error-only, and the
    delegation fails with the synthesis error."""
    pm = ProjectManager(base_path=tmp_path / "projects")
    session = _new_session(pm, tmp_path)
    chat = _build_chat_loop(
        pm=pm,
        send_responses=[
            "Dispatching to backend.",
            "[chat error: ReadTimeout: ]",
        ],
    )
    orig = chat.runtime._send_message
    injected: list[bool] = []

    async def injecting_send(self, body=None, trace=None, on_chunk=None,
                             on_reasoning=None, **kwargs):
        out = await orig(self, body, trace, on_chunk, on_reasoning, **kwargs)
        if not injected:
            injected.append(True)
            store = await chat.delegation_stores.for_project(tmp_path)
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
                    break
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

    store = await chat.delegation_stores.for_project(tmp_path)
    chat_records = [r for r in store.list() if r.kind == "chat"]
    assert chat_records[0].status == "failed"
    assert chat_records[0].error == "[chat error: ReadTimeout: ]"


# ---------------------------------------------------------------------------
# Rerun (edit + resend / retry)
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


@pytest.mark.asyncio
async def test_rerun_route_shape_and_errors():
    """The route returns the assistant message; errors map to statuses."""
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from sweave.web.routers.projects import RerunRequest, api_rerun_turn
    from types import SimpleNamespace

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
# Thinking + segments
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
