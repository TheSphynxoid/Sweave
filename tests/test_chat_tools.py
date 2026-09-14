"""Chat tool transparency (opencode-style tool rows in the thread).

* ``sweave.chat.tools``: one-liner summarizer + compact persisted rows.
* Harness ``on_tool``: ``OpenCodeProcess.send`` forwards one normalized
  event per tool transition (pending -> completed).
* ChatLoop: ``chat.tool`` WS events + ``metadata.tools`` persisted on
  the assistant message (live + reload); UI-only (never composed into
  the model prompt).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sweave.chat.tools import (
    MAX_TOOLS_PER_MESSAGE,
    compact_tool_record,
    summarize_tool_call,
    tool_event,
)


# ---------------------------------------------------------------------------
# Summarizer + compact rows (pure)
# ---------------------------------------------------------------------------


def test_summarize_read_shows_path():
    assert summarize_tool_call("read", {"filePath": "src/foo.ts"}) == "src/foo.ts"


def test_summarize_bash_shows_command():
    assert summarize_tool_call("bash", {"command": "ls -la"}) == "ls -la"


def test_summarize_legacy_underscore_keys():
    assert summarize_tool_call("read", {"file_path": "a/b.py"}) == "a/b.py"


def test_summarize_unknown_shape_degrades_to_empty():
    assert summarize_tool_call("read", {"weird": {"nested": 1}}) == ""
    assert summarize_tool_call("read", None) == ""
    assert summarize_tool_call("read", 42) == ""


def test_summarize_never_raises_on_garbage():
    class _Bad:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    assert summarize_tool_call(_Bad(), object()) == ""


def test_compact_record_read_is_one_liner_no_input():
    row = compact_tool_record(
        {
            "callID": "c1",
            "tool": "read",
            "status": "completed",
            "input": {"filePath": "src/foo.ts"},
        }
    )
    assert row["callID"] == "c1"
    assert row["tool"] == "read"
    assert row["status"] == "completed"
    assert row["summary"] == "src/foo.ts"
    assert row["input"] is None  # reads stay one-liners


def test_compact_record_edit_keeps_capped_input_for_diff():
    row = compact_tool_record(
        {
            "callID": "c2",
            "tool": "edit",
            "status": "completed",
            "input": {
                "filePath": "src/foo.ts",
                "oldString": "aaa",
                "newString": "bbb",
            },
        }
    )
    assert row["summary"] == "src/foo.ts"
    assert row["input"]["oldString"] == "aaa"
    assert row["input"]["newString"] == "bbb"


def test_compact_record_missing_fields_degrade():
    row = compact_tool_record({})
    assert row["callID"] == ""
    assert row["tool"] == "tool"
    assert row["status"] == "unknown"
    assert row["summary"] == ""


def test_compact_record_never_raises():
    assert compact_tool_record(None)["tool"] == "tool"
    assert compact_tool_record("garbage")["status"] == "unknown"


def test_tool_event_normalizes_state_shape():
    ev = tool_event("c1", "read", {"status": "completed", "input": {"filePath": "x"}})
    assert ev == {
        "callID": "c1",
        "tool": "read",
        "status": "completed",
        "input": {"filePath": "x"},
        "output": None,
        "error": None,
        "title": None,
    }


def test_tool_event_never_raises():
    assert tool_event(None, None, None)["status"] == "unknown"


def test_max_tools_guard_is_sane():
    assert MAX_TOOLS_PER_MESSAGE >= 10


# ---------------------------------------------------------------------------
# OpenCodeProcess.send forwards on_tool per transition
# ---------------------------------------------------------------------------


def _tool_chunks() -> list[str]:
    obj = {
        "info": {"time": {"completed": 1}, "finish": "stop"},
        "parts": [
            {
                "type": "tool",
                "callID": "c1",
                "tool": "read",
                "state": {"status": "pending", "input": {"filePath": "a.txt"}},
            },
            {
                "type": "tool",
                "callID": "c1",
                "tool": "read",
                "state": {
                    "status": "completed",
                    "input": {"filePath": "a.txt"},
                    "output": "hi",
                },
            },
            {"type": "text", "text": "done"},
        ],
    }
    return [json.dumps(obj)]


def _proc_with_chunks(chunks: list[str]):
    from sweave.harness.base import AgentSpec
    from sweave.harness.opencode import OpenCodeHarness

    spec = AgentSpec(
        name="tool-test",
        role="tool-test",
        model="",
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="",
        tools=[],
        env={},
        harness="opencode",
    )
    proc = OpenCodeHarness()._spawn_mock(spec)
    proc._session_created = True  # type: ignore[attr-defined]
    proc._session_id = "ses_tooltest123"  # type: ignore[attr-defined]

    class _StubStreamResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def aiter_text(self):
            for c in chunks:
                yield c

    def _stream(method, url, json=None, headers=None, **kw):
        return _StubStreamResponse()

    proc._client.stream = _stream  # type: ignore[assignment]
    return proc


@pytest.mark.asyncio
async def test_opencode_send_forwards_on_tool_per_transition(tmp_path: Path):
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    proc = _proc_with_chunks(_tool_chunks())
    seen: list[dict[str, Any]] = []

    async def _on_tool(ev: dict[str, Any]) -> None:
        seen.append(ev)

    trace = TraceLog("tool-on-tool", base_dir=tmp_path)
    result = await proc.send(
        Message(type="user", content="hi"), trace=trace, on_tool=_on_tool
    )
    assert result.success is True
    assert result.output == "done"
    # pending (started) + completed
    assert [(e["callID"], e["tool"], e["status"]) for e in seen] == [
        ("c1", "read", "pending"),
        ("c1", "read", "completed"),
    ]
    assert seen[0]["input"] == {"filePath": "a.txt"}


@pytest.mark.asyncio
async def test_opencode_send_without_on_tool_still_traces(tmp_path: Path):
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog, read_trace

    proc = _proc_with_chunks(_tool_chunks())
    trace = TraceLog("tool-no-cb", base_dir=tmp_path)
    result = await proc.send(Message(type="user", content="hi"), trace=trace)
    assert result.success is True
    names = [e.get("event") for e in read_trace("tool-no-cb", base_dir=tmp_path)]
    assert "tool.started" in names
    assert "tool.completed" in names


# ---------------------------------------------------------------------------
# ChatLoop: chat.tool WS + metadata.tools persist (UI-only)
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


def _build_chat_loop(*, pm: Any, on_tool_events: list[dict[str, Any]] | None = None):
    from sweave.chat.loop import ChatLoop
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.specialist_store import Specialist

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)

    async def fake_send(
        self, body=None, trace=None, on_chunk=None, on_reasoning=None,
        on_tool=None, **kwargs,
    ):
        if on_tool is not None:
            for ev in on_tool_events or []:
                result = on_tool(dict(ev))
                if hasattr(result, "__await__"):
                    await result
        if on_chunk is not None:
            result = on_chunk("answer text")
            if hasattr(result, "__await__"):
                await result
        return "answer text"

    runtime._send_message = fake_send  # type: ignore[assignment]

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
        stream_coalesce_ms=20,
        stream_char_threshold=4,
    )
    return chat, event_bus


@pytest.mark.asyncio
async def test_chat_turn_emits_chat_tool_and_persists_metadata_tools(tmp_path: Path):
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, event_bus = _build_chat_loop(
        pm=pm,
        on_tool_events=[
            {
                "callID": "c1",
                "tool": "read",
                "status": "pending",
                "input": {"filePath": "src/foo.ts"},
            },
            {
                "callID": "c1",
                "tool": "read",
                "status": "completed",
                "input": {"filePath": "src/foo.ts"},
            },
            {
                "callID": "c2",
                "tool": "edit",
                "status": "completed",
                "input": {
                    "filePath": "src/foo.ts",
                    "oldString": "a",
                    "newString": "b",
                },
            },
        ],
    )

    result = await chat.run_turn(session_id=session.id, user_content="hi")
    assert result["content"] == "answer text"

    tool_events = [
        call.args for call in event_bus.publish.call_args_list
        if call.args[0] == "chat.tool"
    ]
    assert len(tool_events) == 3, "one WS event per tool transition"
    for _name, payload in tool_events:
        assert payload["session_id"] == session.id
        assert payload["round"] == 0
        assert payload["tool"]["callID"] in {"c1", "c2"}

    tools = result.get("metadata", {}).get("tools")
    assert isinstance(tools, list) and len(tools) == 2, tools
    by_call = {t["callID"]: t for t in tools}
    # Latest-status-wins per callID
    assert by_call["c1"]["status"] == "completed"
    assert by_call["c1"]["summary"] == "src/foo.ts"
    assert by_call["c1"]["input"] is None  # reads stay one-liners
    assert by_call["c2"]["input"]["oldString"] == "a"

    # Reload path: the persisted rows survive on the session record.
    reloaded = pm.get_session(session.id)
    assert reloaded is not None
    persisted = [m for m in reloaded.messages if m.role == "assistant"][-1]
    assert persisted.metadata.get("tools") == tools


@pytest.mark.asyncio
async def test_chat_turn_without_tools_persists_no_tools_key(tmp_path: Path):
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    chat, event_bus = _build_chat_loop(pm=pm, on_tool_events=[])

    result = await chat.run_turn(session_id=session.id, user_content="hi")
    assert "tools" not in result.get("metadata", {})
    tool_events = [
        call.args for call in event_bus.publish.call_args_list
        if call.args[0] == "chat.tool"
    ]
    assert tool_events == []


@pytest.mark.asyncio
async def test_tool_markers_persist_in_arrival_order(tmp_path: Path):
    """Think → act → think → act → text persists interleaved segments
    (tool markers ride as {"kind": "tool", "callID"}; the row lookup
    stays metadata.tools)."""
    from sweave.chat.loop import ChatLoop
    from sweave.projects import ProjectManager
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.specialist_store import Specialist

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)

    async def interleaved_send(
        self, body=None, trace=None, on_chunk=None, on_reasoning=None,
        on_tool=None, **kwargs,
    ):
        async def _call(cb, value):
            if cb is None:
                return
            result = cb(value)
            if hasattr(result, "__await__"):
                await result

        await _call(on_chunk, "First. ")
        await _call(on_tool, {
            "callID": "c1", "tool": "read", "status": "completed",
            "input": {"filePath": "a.txt"},
        })
        await _call(on_reasoning, "hmm, ")
        await _call(on_chunk, "Second.")
        await _call(on_tool, {
            "callID": "c1", "tool": "read", "status": "completed",
            "input": {"filePath": "a.txt"},
        })
        await _call(on_tool, {
            "callID": "c2", "tool": "edit", "status": "completed",
            "input": {"filePath": "b.txt", "oldString": "x", "newString": "y"},
        })
        return "First. Second."

    runtime._send_message = interleaved_send  # type: ignore[assignment]
    factories = {
        "orchestrator": Specialist(
            name="orchestrator", scope="project", is_orchestrator=True,
            system_prompt="seed", harness="opencode", current_model=None,
        )
    }

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
        event_bus=None,
        turn_timeout=10.0,
        model_resolver=lambda agent, project=None: "deepseek-flash",
        stream_coalesce_ms=20,
        stream_char_threshold=4,
    )
    result = await chat.run_turn(session_id=session.id, user_content="hi")
    kinds = [
        (s["kind"], s.get("text", s.get("callID")))
        for s in result["metadata"]["segments"]
    ]
    assert kinds == [
        ("text", "First. "),
        ("tool", "c1"),
        ("reasoning", "hmm, "),
        ("text", "Second."),
        ("tool", "c2"),
    ]
    # Status re-fire of c1 adds no second marker; one row per callID.
    assert [t["callID"] for t in result["metadata"]["tools"]] == ["c1", "c2"]
    """The composer reads role/content/superseded only — metadata.tools
    (however large an edit diff) cannot bloat or pollute the model
    context. Regression guard for the UI-only contract."""
    from sweave.chat.transcript import compose_turn_prompt
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    session.add_message(role="user", content="do it")
    session.add_message(
        role="assistant",
        content="did it",
        agent="orchestrator",
        metadata={
            "delegation_id": "chat-abc",
            "tools": [
                {
                    "callID": "c9",
                    "tool": "edit",
                    "status": "completed",
                    "summary": "big.ts",
                    "input": {"oldString": "x" * 5000, "newString": "y" * 5000},
                    "round": 0,
                }
            ],
        },
    )
    composed = await compose_turn_prompt(
        session=session,
        user_message="again",
        project_dir=None,
        memory_bank_id=None,
        memory_backend=None,
        git_snapshotter=None,
        children=None,
        transcript_messages=list(session.messages),
    )
    body = composed.to_body()
    assert "x" * 100 not in body
    assert "big.ts" not in body
