"""M1.7 step 3 tests: synthesis loop.

Covers:

* ``build_synthesis_prompt`` (server-side) composes a per-child
  structured prompt with status, task, output, and error fields.
* Token cap: when children overflow the cap, output is truncated
  oldest-first.
* Children with status ``failed`` are still included in the
  synthesis prompt (with their error text) -- the orchestrator
  needs to acknowledge the failure.
* Chat turn with no children: fast path, the first turn's reply is
  the final assistant message; delegation auto-``done``.
* Chat turn with children: the chat loop waits for the children,
  builds a synthesis prompt, runs a second orchestrator turn, and
  persists the synthesis reply as the final assistant message.
* Children that fail: synthesis still runs (no hang).
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from sweave.chat.synthesis import (
    _approx_tokens,
    build_synthesis_prompt,
    truncate_to_tokens,
)
from sweave.projects import ProjectManager
from sweave.runtime.delegation_store import (
    Delegation,
    PerProjectDelegationStores,
)
from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist


# ---------------------------------------------------------------------------
# Hermeticity
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


# ---------------------------------------------------------------------------
# Synthesis prompt builder tests (pure)
# ---------------------------------------------------------------------------


def _make_child(
    *,
    agent: str,
    task: str,
    status: str,
    output: str = "",
    error: str | None = None,
    completed_at: datetime | None = None,
) -> Delegation:
    d = Delegation(
        delegation_id=f"d-{agent}",
        task_id=f"t-{agent}",
        agent=agent,
        model="hy3",
        task=task,
        status=status,
        output=output,
        error=error,
    )
    if completed_at is not None:
        d.completed_at = completed_at
    return d


def test_synthesis_prompt_includes_each_child_status_and_output():
    children = [
        _make_child(agent="backend", task="make hello.py", status="done",
                    output="created hello.py"),
        _make_child(agent="frontend", task="make style.css", status="done",
                    output="created style.css"),
    ]
    prompt = build_synthesis_prompt(
        children=children,
        original_user_message="scaffold a tiny site",
    )
    assert "backend" in prompt
    assert "frontend" in prompt
    assert "done" in prompt
    assert "created hello.py" in prompt
    assert "created style.css" in prompt
    assert "scaffold a tiny site" in prompt


def test_synthesis_prompt_includes_failed_children_with_error():
    """A failed child is not silently dropped -- the orchestrator
    needs to know the failure to acknowledge it.
    """
    children = [
        _make_child(agent="backend", task="make hello.py", status="done",
                    output="ok"),
        _make_child(agent="frontend", task="make style.css", status="failed",
                    error="permission denied"),
    ]
    prompt = build_synthesis_prompt(
        children=children,
        original_user_message="scaffold a tiny site",
    )
    assert "frontend" in prompt
    assert "failed" in prompt
    assert "permission denied" in prompt


def test_synthesis_prompt_truncates_when_overflowing_cap():
    """A child whose output is larger than the per-child budget is
    truncated; the prefix is preserved.
    """
    long_output = "word " * 5_000  # ~6667 tokens at the rough heuristic
    children = [
        _make_child(agent="backend", task="big task", status="done",
                    output=long_output),
    ]
    prompt = build_synthesis_prompt(
        children=children,
        original_user_message="big",
        token_cap=1_000,
    )
    assert "truncated" in prompt
    # The original 5_000 words shouldn't be in the prompt verbatim
    # (they would push the prompt well over the cap).
    assert prompt.count("word") < 5_000


def test_synthesis_prompt_empty_children_returns_empty():
    """No children -> empty string. The fast path doesn't build a
    synthesis prompt.
    """
    assert build_synthesis_prompt(
        children=[], original_user_message="hello"
    ) == ""


def test_truncate_to_tokens_preserves_short_text():
    text = "hello world"
    assert truncate_to_tokens(text, token_cap=100) == text


def test_truncate_to_tokens_cuts_long_text():
    text = "word " * 1_000  # ~1333 tokens
    out = truncate_to_tokens(text, token_cap=100)
    assert len(out) < len(text)
    assert "truncated" in out


def test_approx_tokens_zero_for_empty():
    assert _approx_tokens("") == 0


# ---------------------------------------------------------------------------
# ChatLoop end-to-end synthesis tests
# ---------------------------------------------------------------------------


def _build_chat_loop(
    *,
    pm: ProjectManager,
    send_responses: list[str] | None = None,
    send_delay: float = 0.0,
):
    from sweave.chat.loop import ChatLoop

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)

    responses = list(send_responses or ["first-reply"])

    async def fake_send(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
        if send_delay:
            await asyncio.sleep(send_delay)
        if on_chunk is not None and responses:
            on_chunk(responses[0])
        if not responses:
            return "ok"
        return responses.pop(0)

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

    stores = PerProjectDelegationStores()
    chat = ChatLoop(
        project_manager=pm,
        specialist_runtime=runtime,
        specialist_factory=factory,
        project_dir_resolver=resolver,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=10.0,
        model_resolver=lambda agent, project=None: "deepseek-flash",
        synthesis_token_cap=8_000,
    )
    return chat, stores


@pytest.mark.asyncio
async def test_chat_turn_no_children_auto_done_with_first_reply(tmp_path: Path):
    """Fast path: orchestrator returns without deferring. First
    reply is the final assistant message; chat delegation auto-``done``.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, stores = _build_chat_loop(pm=pm, send_responses=["hi back"])

    result = await chat.run_turn(
        session_id=session.id, user_content="hello"
    )
    assert result["content"] == "hi back"
    loaded = pm.get_session(session.id)
    assert loaded.messages[-1].content == "hi back"

    store = await stores.for_project(tmp_path)
    chat_records = [r for r in store.list() if r.kind == "chat"]
    assert len(chat_records) == 1
    rec = chat_records[0]
    # Auto-done (M1.7 step 3 ruling): success -> done, NOT review.
    assert rec.status == "done"


@pytest.mark.asyncio
async def test_chat_turn_with_children_runs_synthesis(tmp_path: Path):
    """The orchestrator defers to a specialist (parent_task_id points
    at the chat delegation). The chat loop waits, builds a synthesis
    prompt, and runs a second orchestrator turn. The synthesis reply
    is the final assistant message; auto-``done``.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, stores = _build_chat_loop(
        pm=pm,
        send_responses=[
            "I'll ask backend to do that.",  # first turn
            "Backend did the thing.",        # synthesis turn
        ],
    )

    # Pre-seed: in real life, the orchestrator's first turn would
    # call MCP defer -> child Delegation is created with
    # parent_task_id = chat_d.delegation_id. We can't easily exercise
    # that path in this test (it'd require a live MCP stub), so we
    # run the first turn, peek at the chat_d.id, then add a child
    # to the store, then run again. For the synthesis to fire, the
    # child must be present BEFORE the chat loop's child-scan at
    # the end of the first turn. So we need a different approach:
    # have the first-turn stub create the child.
    # Approach: build a special send stub that on the first call,
    # posts a child Delegation to the store via the /api/v2/tasks
    # path, simulating a defer.
    # Simpler: hook into the ChatLoop's first turn to add a child
    # record after the first turn returns but before the chat loop's
    # _wait_for_children is called. We can do this by patching the
    # chat loop's runtime._send_message to inject a child record.
    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)

    responses = [
        "I'll ask backend to do that.",
        "Backend did the thing.",
    ]

    # Capture the chat delegation id from the first turn
    chat_d_id_holder: list[str] = []
    stores_real = stores

    async def fake_send(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
        # The runtime body[parts][0][text] includes the prompt
        # the chat loop built; we don't need to inspect it -- we
        # just inject a child after the first turn.
        if not chat_d_id_holder:
            # First turn: record the chat delegation id, then
            # inject a child Delegation before returning the
            # orchestrator's first reply.
            # The chat delegation id is the *parent* of the
            # synthesis children. We need to find it. The chat
            # loop has already created the delegation; we can
            # peek at the store for kind=chat records.
            store = await stores_real.for_project(tmp_path)
            for rec in store.list():
                if rec.kind == "chat":
                    chat_d_id_holder.append(rec.delegation_id)
                    # Inject a synthetic child that is already
                    # terminal (done). The chat loop will see it
                    # in its child-scan and synthesise.
                    child = Delegation(
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
                    )
                    await store.add(child)
                    break
        if not responses:
            return "ok"
        return responses.pop(0)

    runtime._send_message = fake_send  # type: ignore[assignment]
    chat.runtime = runtime  # swap in the injecting runtime

    result = await chat.run_turn(
        session_id=session.id, user_content="ask backend to do X"
    )
    # The synthesis reply is the final assistant message
    assert result["content"] == "Backend did the thing."

    loaded = pm.get_session(session.id)
    # Multi-message turns (2026-09-11): the first-turn narration
    # persists as round 0 (turn_final False) before the child wait;
    # the synthesis persists as round 1 (final). Both carry the
    # chat delegation id.
    user_msgs = [m for m in loaded.messages if m.role == "user"]
    assistant_msgs = [m for m in loaded.messages if m.role == "assistant"]
    assert len(user_msgs) == 1
    assert len(assistant_msgs) == 2
    assert assistant_msgs[0].content == "I'll ask backend to do that."
    assert assistant_msgs[0].metadata["turn_round"] == 0
    assert assistant_msgs[0].metadata["turn_final"] is False
    assert assistant_msgs[1].content == "Backend did the thing."
    assert assistant_msgs[1].metadata["turn_round"] == 1
    assert assistant_msgs[1].metadata["turn_final"] is True

    store = await stores_real.for_project(tmp_path)
    chat_records = [r for r in store.list() if r.kind == "chat"]
    assert len(chat_records) == 1
    assert chat_records[0].status == "done"  # auto-done


@pytest.mark.asyncio
async def test_chat_turn_with_failing_child_synthesis_still_runs(tmp_path: Path):
    """A child failing during the wait does not hang the chat. The
    synthesis runs with the failure included.
    """
    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, stores = _build_chat_loop(
        pm=pm,
        send_responses=[
            "trying...",            # first turn
            "backend failed; sorry.",  # synthesis turn
        ],
    )

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)
    responses = ["trying...", "backend failed; sorry."]
    first_turn = [True]

    async def fake_send(self, body=None, trace=None, on_chunk=None, on_reasoning=None, **kwargs):
        if first_turn[0]:
            first_turn[0] = False
            store = await stores.for_project(tmp_path)
            for rec in store.list():
                if rec.kind == "chat":
                    child = Delegation(
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
                    )
                    await store.add(child)
                    break
        if not responses:
            return "ok"
        return responses.pop(0)

    runtime._send_message = fake_send  # type: ignore[assignment]
    chat.runtime = runtime

    result = await chat.run_turn(
        session_id=session.id, user_content="do X"
    )
    assert "backend failed" in result["content"]

    loaded = pm.get_session(session.id)
    assistant_msgs = [m for m in loaded.messages if m.role == "assistant"]
    # Multi-message turns (2026-09-11): the first-turn narration
    # persists as round 0 even when the child failed.
    assert len(assistant_msgs) == 2
    assert assistant_msgs[0].content == "trying..."
    assert assistant_msgs[0].metadata["turn_round"] == 0
    assert "backend failed" in assistant_msgs[1].content
