"""Engine journal -> per-turn transcript blocks (step 2a).

Projector: ``sweave/web/transcript_view.py`` + the detail fold's
ADDITIVE ``transcript`` key. One block per ``/run`` turn keyed by
``user_message_id`` (protocol v3); prompt actually sent, assistant
text, tool calls with lifecycle, reasoning (trace-joined), per-turn
tokens. Unknown shapes degrade; a corrupt/missing journal returns
None — never raises, never crashes a pre-change delegation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sweave.web.detail_view import render_detail_view
from sweave.web.transcript_view import (
    engine_journal_path,
    render_transcript_blocks,
)


def _journal(tmp_path: Path, sessions: dict) -> Path:
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps(sessions), encoding="utf-8")
    return path


_SID = "eng_abc123"


def _one_turn_session():
    """Shape pinned from the live journal (2026-09-16): user ->
    assistant(toolCalls + content) -> tool(result) -> assistant."""
    return {
        "id": _SID,
        "messages": [
            {
                "id": "msg_u1",
                "role": "user",
                "content": "[sweave: caller_delegation_id=dlg-1]\n\nTask body here",
                "at": 1789350554169,
            },
            {
                "id": "msg_a1",
                "role": "assistant",
                "content": "Reading the file first.",
                "toolCalls": [
                    {
                        "id": "call_1",
                        "name": "read",
                        "args": {"filePath": "notes.txt"},
                    }
                ],
                "model": "openrouter/x/y:free",
                "at": 1789350599000,
            },
            {
                "id": "msg_t1",
                "role": "tool",
                "toolCallId": "call_1",
                "name": "read",
                "content": "file body",
                "at": 1789350600000,
            },
            {
                "id": "msg_a2",
                "role": "assistant",
                "content": "Done: the notes say x.",
                "model": "openrouter/x/y:free",
                "at": 1789350601000,
            },
        ],
    }


def test_blocks_one_per_run_turn(tmp_path: Path):
    jp = _journal(tmp_path, {_SID: _one_turn_session()})
    blocks = render_transcript_blocks(_SID, [], journal_path=jp)
    assert blocks is not None and len(blocks) == 1
    b = blocks[0]
    assert b["user_message_id"] == "msg_u1"
    assert b["prompt"] == "[sweave: caller_delegation_id=dlg-1]\n\nTask body here"
    # Assistant text parts of one turn (tool call in between) JOIN.
    assert b["text"] == "Reading the file first.Done: the notes say x."
    assert b["model"] == "openrouter/x/y:free"
    assert b["failed"] is False and b["error"] is None
    (entry,) = b["tools"]
    assert entry["callID"] == "call_1"
    assert entry["tool"] == "read"
    assert entry["status"] == "completed"
    assert entry["args"] == {"filePath": "notes.txt"}
    assert entry["result"] == "file body"
    # Timeline shape for the renderer (2026-09-18 bare-tool fix).
    assert entry["input"] == {"filePath": "notes.txt"}
    assert entry["output"] == "file body"


def test_block_multi_turn_and_parts_join(tmp_path: Path):
    session = {
        "id": _SID,
        "messages": [
            {"id": "u1", "role": "user", "content": "one", "at": 1},
            {
                "id": "a1",
                "role": "assistant",
                "content": "partA",
                "toolCalls": [{"id": "c1", "name": "bash", "args": {"command": "x"}}],
                "model": "m/m",
                "at": 2,
            },
            {"id": "a2", "role": "assistant", "content": "partB", "model": "m/m", "at": 3},
            {"id": "t1", "role": "tool", "toolCallId": "c1", "name": "bash", "content": "out", "at": 4},
            {"id": "u2", "role": "user", "content": "two", "at": 5},
            {"id": "a3", "role": "assistant", "content": "final", "model": "m/m", "at": 6},
        ],
    }
    jp = _journal(tmp_path, {_SID: session})
    blocks = render_transcript_blocks(_SID, [], journal_path=jp)
    assert len(blocks) == 2
    assert blocks[0]["user_message_id"] == "u1"
    assert blocks[0]["prompt"] == "one"
    # Assistant text parts within one turn JOIN in arrival order.
    assert blocks[0]["text"] == "partApartB"
    assert blocks[0]["tools"][0]["tool"] == "bash"
    assert blocks[0]["tools"][0]["result"] == "out"
    assert blocks[1]["prompt"] == "two"
    assert blocks[1]["text"] == "final"
    assert blocks[1]["tools"] == []


def test_reasoning_and_tokens_join_from_trace(tmp_path: Path):
    jp = _journal(tmp_path, {_SID: _one_turn_session()})
    trace = [
        {"event": "engine_user_message", "id": "msg_u1"},
        {"event": "reasoning", "text": "hmm, "},
        {"event": "reasoning", "text": "sure."},
        {"event": "tokens_used", "input": 100, "output": 20, "reasoning": 5,
         "cache_read": 3, "cache_write": 0, "context_input": 100},
    ]
    blocks = render_transcript_blocks(_SID, trace, journal_path=jp)
    b = blocks[0]
    assert b["reasoning"] == "hmm, sure."
    assert b["tokens"] == {
        "input": 100, "output": 20, "reasoning": 5,
        "cache_read": 3, "cache_write": 0, "context_input": 100,
    }


def test_two_turns_attribute_trace_per_turn(tmp_path: Path):
    session = {
        "id": _SID,
        "messages": [
            {"id": "u1", "role": "user", "content": "t1", "at": 1},
            {"id": "a1", "role": "assistant", "content": "r1", "at": 2},
            {"id": "u2", "role": "user", "content": "t2", "at": 3},
            {"id": "a2", "role": "assistant", "content": "r2", "at": 4},
        ],
    }
    jp = _journal(tmp_path, {_SID: session})
    trace = [
        {"event": "engine_user_message", "id": "u1"},
        {"event": "reasoning", "text": "first-turn thinking"},
        {"event": "tokens_used", "input": 10, "output": 1},
        {"event": "engine_user_message", "id": "u2"},
        {"event": "reasoning", "text": "second-turn thinking"},
        {"event": "tokens_used", "input": 20, "output": 2},
    ]
    blocks = render_transcript_blocks(_SID, trace, journal_path=jp)
    assert blocks[0]["reasoning"] == "first-turn thinking"
    assert blocks[0]["tokens"]["input"] == 10
    assert blocks[1]["reasoning"] == "second-turn thinking"
    assert blocks[1]["tokens"]["input"] == 20


def test_failed_turn_carries_honest_error(tmp_path: Path):
    session = {
        "id": _SID,
        "messages": [
            {"id": "u1", "role": "user", "content": "go", "at": 1},
            {
                "id": "a1", "role": "assistant", "content": "",
                "failed": True, "error": "turn_timeout_exceeded_1800s", "at": 2,
            },
        ],
    }
    jp = _journal(tmp_path, {_SID: session})
    blocks = render_transcript_blocks(_SID, [], journal_path=jp)
    assert blocks[0]["failed"] is True
    assert blocks[0]["error"] == "turn_timeout_exceeded_1800s"
    assert blocks[0]["text"] == ""


def test_error_tool_result_marks_error_status(tmp_path: Path):
    session = {
        "id": _SID,
        "messages": [
            {"id": "u1", "role": "user", "content": "try", "at": 1},
            {
                "id": "a1", "role": "assistant", "content": "", "model": "m/m",
                "toolCalls": [{"id": "c", "name": "edit", "args": {"filePath": "x"}}],
                "at": 2,
            },
            {
                "id": "t1", "role": "tool", "toolCallId": "c", "name": "edit",
                "content": "old file", "failed": True,
                "error": "oldString not found", "at": 3,
            },
        ],
    }
    jp = _journal(tmp_path, {_SID: session})
    blocks = render_transcript_blocks(_SID, [], journal_path=jp)
    t = blocks[0]["tools"][0]
    assert t["status"] == "error"
    assert t["error"] == "oldString not found"
    assert t["result"] == "old file"


def test_reasoning_attributed_by_id_not_position(tmp_path: Path):
    """Hygiene B6: an empty-reasoning first turn must not shift the
    second turn's thinking onto it (the old positional join did)."""
    session = {
        "id": _SID,
        "messages": [
            {"id": "u1", "role": "user", "content": "t1", "at": 1},
            {"id": "a1", "role": "assistant", "content": "r1", "at": 2},
            {"id": "u2", "role": "user", "content": "t2", "at": 3},
            {"id": "a2", "role": "assistant", "content": "r2", "at": 4},
        ],
    }
    jp = _journal(tmp_path, {_SID: session})
    trace = [
        {"event": "engine_user_message", "id": "u1"},
        {"event": "tokens_used", "input": 10, "output": 1},
        {"event": "engine_user_message", "id": "u2"},
        {"event": "reasoning", "text": "second-turn thinking"},
        {"event": "tokens_used", "input": 20, "output": 2},
    ]
    blocks = render_transcript_blocks(_SID, trace, journal_path=jp)
    assert blocks[0]["reasoning"] == ""
    assert blocks[1]["reasoning"] == "second-turn thinking"


def test_tokens_attributed_by_id_when_present(tmp_path: Path):
    """Hygiene B6: tokens_used carrying user_message_id joins by id
    even when arrival order disagrees with turn order (a failed turn
    emits no anchor, which used to shift every neighbour)."""
    session = {
        "id": _SID,
        "messages": [
            {"id": "u1", "role": "user", "content": "t1", "at": 1},
            {"id": "a1", "role": "assistant", "content": "r1", "at": 2},
            {"id": "u2", "role": "user", "content": "t2", "at": 3},
            {"id": "a2", "role": "assistant", "content": "r2", "at": 4},
        ],
    }
    jp = _journal(tmp_path, {_SID: session})
    trace = [
        {"event": "engine_user_message", "id": "u1"},
        {"event": "engine_user_message", "id": "u2"},
        {"event": "tokens_used", "input": 20, "output": 2, "user_message_id": "u2"},
        {"event": "tokens_used", "input": 10, "output": 1, "user_message_id": "u1"},
    ]
    blocks = render_transcript_blocks(_SID, trace, journal_path=jp)
    assert blocks[0]["tokens"]["input"] == 10
    assert blocks[1]["tokens"]["input"] == 20


def test_dangling_call_flags_result_missing(tmp_path: Path):
    """Hygiene B6: an assistant call with no journaled result (abort /
    crash poison) keeps status unknown for back-compat but carries
    result_missing so renderers can name it never-completed."""
    session = {
        "id": _SID,
        "messages": [
            {"id": "u1", "role": "user", "content": "go", "at": 1},
            {
                "id": "a1", "role": "assistant", "content": "", "model": "m/m",
                "toolCalls": [{"id": "c-dangle", "name": "bash", "args": {}}],
                "at": 2,
            },
        ],
    }
    jp = _journal(tmp_path, {_SID: session})
    blocks = render_transcript_blocks(_SID, [], journal_path=jp)
    (t,) = blocks[0]["tools"]
    assert t["status"] == "unknown"
    assert t["result_missing"] is True
    assert t["result"] is None


def test_unknown_shapes_degrade_never_raise(tmp_path: Path):
    session = {
        "id": _SID,
        "messages": [
            {"id": "u1", "role": "user", "content": 42, "at": 1},  # wrong type
            {"id": "a1", "role": "assistant", "content": None, "toolCalls": "nope", "at": 2},
            {"id": "t1", "role": "orphan-role", "toolCallId": "c", "at": 3},
            "not-a-dict",
            {"role": "tool", "toolCallId": "ghost"},  # result without a call
        ],
    }
    jp = _journal(tmp_path, {_SID: session})
    blocks = render_transcript_blocks(_SID, [{"event": "garbage"}], journal_path=jp)
    assert blocks is not None
    b = blocks[0]
    assert b["prompt"] == "42"  # stringified, never raise
    # toolCalls "nope" (non-list) dropped; the tool result without a
    # call rides as a raw unknown row in the same block, never assume.
    assert len(b["tools"]) == 1
    t = b["tools"][0]
    assert t["callID"] == "ghost" and t["status"] == "unknown"
    assert t["tool"] == "tool"


def test_degrades_to_none_on_missing_sources(tmp_path: Path):
    # No engine session id (opencode turns / pre-change records).
    assert render_transcript_blocks(None, []) is None
    assert render_transcript_blocks("", []) is None
    # Journal file missing.
    assert render_transcript_blocks(_SID, [], journal_path=tmp_path / "none.json") is None
    # Corrupt journal.
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert render_transcript_blocks(_SID, [], journal_path=bad) is None
    # Journal is a list (shape drift), not a dict.
    lst = tmp_path / "list.json"
    lst.write_text("[]", encoding="utf-8")
    assert render_transcript_blocks(_SID, [], journal_path=lst) is None
    # Unknown session id.
    jp = _journal(tmp_path, {})
    assert render_transcript_blocks("eng_unknown", [], journal_path=jp) is None
    # Session without a messages list.
    jp2 = _journal(tmp_path, {"x": {"id": "x"}})
    assert render_transcript_blocks("x", [], journal_path=jp2) is None


def test_assistant_tail_before_any_user_survives(tmp_path: Path):
    """A drifted journal starting mid-turn still projects rows raw
    (never silently dropped)."""
    session = {
        "id": _SID,
        "messages": [
            {"id": "a0", "role": "assistant", "content": "orphan tail", "at": 1},
            {"id": "u1", "role": "user", "content": "after", "at": 2},
        ],
    }
    jp = _journal(tmp_path, {_SID: session})
    blocks = render_transcript_blocks(_SID, [], journal_path=jp)
    assert blocks[0]["prompt"] is None
    assert blocks[0]["user_message_id"] == ""
    assert blocks[0]["text"] == "orphan tail"
    assert len(blocks) == 2
    assert blocks[1]["prompt"] == "after"


def test_env_override_resolves_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SWEAVE_ENGINE_DATA_DIR", str(tmp_path / "eng"))
    p = engine_journal_path()
    assert p == tmp_path / "eng" / "sessions.json"
    (tmp_path / "eng").mkdir()
    (tmp_path / "eng" / "sessions.json").write_text(
        json.dumps({_SID: _one_turn_session()}), encoding="utf-8"
    )
    blocks = render_transcript_blocks(_SID, [])
    assert blocks and blocks[0]["user_message_id"] == "msg_u1"
    monkeypatch.delenv("SWEAVE_ENGINE_DATA_DIR")


def test_detail_fold_carries_transcript_additive(tmp_path: Path):
    jp = engine_journal_path(base_dir=tmp_path / "eng")
    jp.parent.mkdir(parents=True)
    jp.write_text(json.dumps({_SID: _one_turn_session()}), encoding="utf-8")
    detail = render_detail_view(
        "d1",
        trace_dir=tmp_path / "traces",
        engine_session_id=_SID,
        journal_path=jp,
    )
    assert detail["transcript"] is not None
    assert detail["transcript"][0]["user_message_id"] == "msg_u1"
    # Pre-change delegation (no engine session id): None, not a crash.
    detail2 = render_detail_view("d2", trace_dir=tmp_path / "traces")
    assert detail2["transcript"] is None
    # Real-engine-session-shaped record but journal unknown session:
    # None, never a raise.
    detail3 = render_detail_view(
        "d3", trace_dir=tmp_path / "traces",
        engine_session_id="eng_missing",
    )
    assert detail3["transcript"] is None


def test_real_journal_session_projects(monkeypatch, tmp_path):
    """End-to-end shape from the CURRENT live journal (recorded
    fixture, no live server): user prompt + assistant with toolCalls
    + tool results — the shapes found in production."""
    live_shape = {
        "id": "eng_live",
        "messages": [
            {
                "id": "msg_mu0l4bw9_1",
                "role": "user",
                "content": "[sweave: caller_delegation_id=chat-x]\n\n# AGENTS.md — Guide",
                "at": 1789350554169,
            },
            {
                "id": "msg_mu0ldseu_2",
                "role": "assistant",
                "content": "I'll check the harness code.",
                "toolCalls": [
                    {
                        "id": "call-60a8c92d",
                        "name": "defer",
                        "args": {"target": "planner", "task": "Check the harness."},
                    }
                ],
                "model": "openrouter/nvidia/x:free",
                "at": 1789350995478,
            },
            {
                "id": "msg_mu0ldsl0_3",
                "role": "tool",
                "toolCallId": "call-60a8c92d",
                "name": "defer",
                "content": "queued: 6a4d76b82560 (target=planner)",
                "at": 1789351000000,
            },
        ],
    }
    jp = _journal(tmp_path, {"eng_live": live_shape})
    blocks = render_transcript_blocks("eng_live", [], journal_path=jp)
    assert len(blocks) == 1
    b = blocks[0]
    assert b["prompt"].startswith("[sweave: caller_delegation_id=chat-x]")
    assert b["tools"][0]["tool"] == "defer"
    assert b["tools"][0]["status"] == "completed"
    assert "queued: 6a4d76b82560" in b["tools"][0]["result"]


def _bash_turn_session():
    """Bash call + result (the live-journal shape behind the
    bare-'bash' report, 2026-09-18)."""
    return {
        "id": _SID,
        "messages": [
            {
                "id": "msg_u1",
                "role": "user",
                "content": "Check git state",
                "at": 1789350554169,
            },
            {
                "id": "msg_a1",
                "role": "assistant",
                "content": "Checking.",
                "toolCalls": [
                    {
                        "id": "call_b1",
                        "name": "bash",
                        "args": {"command": "git log --oneline -5"},
                    }
                ],
                "model": "openrouter/x/y:free",
                "at": 1789350599000,
            },
            {
                "id": "msg_t1",
                "role": "tool",
                "toolCallId": "call_b1",
                "name": "bash",
                "content": "abc1234 Implement widgets\n",
                "at": 1789350600000,
            },
        ],
    }


def test_journal_bash_carries_timeline_shape(tmp_path: Path):
    """Regression pin for the bare-'bash' report (2026-09-18): the
    renderer reads input/output/detail, so the projector must emit
    them — args/result alone render a verb with no info."""
    jp = _journal(tmp_path, {_SID: _bash_turn_session()})
    blocks = render_transcript_blocks(_SID, [], journal_path=jp)
    assert len(blocks) == 1
    (entry,) = blocks[0]["tools"]
    assert entry["tool"] == "bash"
    assert entry["status"] == "completed"
    # Timeline keys the renderer reads:
    assert entry["input"] == {"command": "git log --oneline -5"}
    assert "abc1234 Implement widgets" in entry["output"]
    assert entry["detail"]["command"] == "git log --oneline -5"
    # Legacy keys stay (back-compat for older readers).
    assert entry["args"] == {"command": "git log --oneline -5"}
    assert "abc1234 Implement widgets" in entry["result"]


def test_journal_read_carries_input_for_card_routing(tmp_path: Path):
    """Read rows route to the window card via input keys — absent
    input falls through to the raw dump."""
    session = _bash_turn_session()
    session["messages"][1]["toolCalls"] = [
        {"id": "call_r1", "name": "read", "args": {"filePath": "notes.txt"}}
    ]
    session["messages"][2]["toolCallId"] = "call_r1"
    session["messages"][2]["name"] = "read"
    session["messages"][2]["content"] = "file body"
    jp = _journal(tmp_path, {_SID: session})
    blocks = render_transcript_blocks(_SID, [], journal_path=jp)
    (entry,) = blocks[0]["tools"]
    assert entry["input"] == {"filePath": "notes.txt"}
    assert entry["output"] == "file body"
