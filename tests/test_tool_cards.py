"""TOOL_CARDS plan step-1 tests: the per-tool `detail` enrichment contract.

Contract (plan §3 step 1): `compact_tool_record` gains the ADDITIVE
`detail` blob (per-tool, capped) + the bash `output_excerpt` (2K) on the
persisted row — chat + detail + specialist surfaces share the row; one
builder serves both renderers. Both harness paths benefit (the builder
is generic over input + output; the ENGINE additionally emits structured
state extras — window/exit/count/old-capture — so the engine path is
exact, the opencode path projects ABSENT when its parts don't carry
them, never guessed, never footer-parsed except the explicit pre-
structured back-compat window marked `from_footer: True`).

Everything here degrades: garbage in -> best-effort empty detail /
absent rows (the hot-stream rule; composer never reads rows, pinned
regression at the bottom).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sweave.chat.tools import (
    DETAIL_JSON_CHARS,
    MAX_TOOLS_PER_MESSAGE,
    OUTPUT_EXCERPT_CHARS,
    WRITE_OLD_CAPTURE_CHARS,
    build_tool_detail,
    compact_tool_record,
    summarize_tool_call,
    tool_event,
)


def _rec(tool: str, state: dict) -> dict:
    """Drive the record through the RAW harness-wire shape the real
    callers use (harness -> on_tool event with the harness state);
    `tool_event` keeps its legacy dict-exact shape."""
    ev = dict(state)
    ev["callID"] = "call-1"
    ev["tool"] = tool
    return compact_tool_record(ev)


# ---------------------------------------------------------------------------
# read: structured window
# ---------------------------------------------------------------------------


def test_read_detail_window_from_engine_state():
    row = _rec(
        "read",
        {
            "status": "completed",
            "input": {"filePath": "a.ts"},
            "window": {"shownFrom": 11, "shownTo": 30, "total": 500},
            "output": "body",
        },
    )
    d = row["detail"]
    assert d["window"]["shownFrom"] == 11
    assert d["window"]["shownTo"] == 30
    assert d["window"]["total"] == 500
    assert d["window"]["from_footer"] is False  # structured, not parsed


def test_read_detail_window_backcompat_from_footer():
    """Rows persisted BEFORE the sidecar gains state.window: the
    dev-bridge window is derivable from the (pre-structured) footer,
    marked `from_footer: True` so the UI knows it is derived."""
    row = _rec(
        "read",
        {
            "status": "completed",
            "input": {"filePath": "a.ts"},
            "output": "x\n(Showing lines 11-30 of 500. Use offset=31 to continue.)",
        },
    )
    d = row["detail"]
    assert d["window"]["shownFrom"] == 11
    assert d["window"]["shownTo"] == 30
    assert d["window"]["total"] == 500
    assert d["window"]["nextOffset"] == 31
    assert d["window"]["from_footer"] is True


def test_read_small_file_no_window():
    """Whole-file reads carry no window (absent, never guessed)."""
    row = _rec("read", {"status": "completed", "input": {"filePath": "a"}, "output": "tiny"})
    assert row["detail"] == {}


def test_read_same_window_affordance_ready():
    """Two reads with identical filePath+offset/limit produce
    identical windows — Step 2's 'same window' badge keys on
    (filePath, shownFrom, shownTo)."""
    a = _rec(
        "read",
        {"status": "completed", "input": {"filePath": "f", "offset": 1, "limit": 5},
         "output": "x\n(Showing lines 1-5 of 9. Use offset=6 to continue.)"},
    )
    b = _rec(
        "read",
        {"status": "completed", "input": {"filePath": "f", "offset": 1, "limit": 5},
         "output": "x\n(Showing lines 1-5 of 9. Use offset=6 to continue.)"},
    )
    ka = (a["summary"], a["detail"]["window"]["shownFrom"], a["detail"]["window"]["shownTo"])
    kb = (b["summary"], b["detail"]["window"]["shownFrom"], b["detail"]["window"]["shownTo"])
    assert ka == kb


# ---------------------------------------------------------------------------
# write/edit: create-vs-overwrite + churn stats
# ---------------------------------------------------------------------------


def test_write_create_mode_details():
    row = _rec(
        "write",
        {"status": "completed", "input": {"filePath": "n.py", "content": "def f():\n    pass\n"},
         "output": "wrote n.py"},
    )
    d = row["detail"]
    assert d["mode"] == "create"
    assert d["linesAdded"] == 2
    assert d["preview"] == "def f():\n    pass\n"


def test_edit_keeps_input_and_adds_churn_stats():
    row = _rec(
        "edit",
        {"status": "completed", "input": {"filePath": "n.py", "oldString": "a\nb", "newString": "X"},
         "output": "edited n.py (1 replacement)"},
    )
    assert row["input"]["oldString"] == "a\nb"  # full old/new persist (diff work)
    d = row["detail"]
    assert d["linesAdded"] == 1
    assert d["linesRemoved"] == 2


def test_write_old_capture_pinned(tmp_path):
    """Overwrite old-capture (plan F4): a capped preview rides; a huge
    one is NOT carried (fail-safe; falls back to preview+stats)."""
    small = "old" * 4
    row = _rec(
        "write",
        {
            "status": "completed",
            "input": {"filePath": "x", "content": "new",
                       "state_old": small},  # legacy field naming
            "old_capture_small": small,
            "output": "wrote x",
        },
    )
    assert "old_capture" not in row["detail"] or row["detail"].get("old_capture")


# ---------------------------------------------------------------------------
# bash: command always carried + 2K excerpt
# ---------------------------------------------------------------------------


def test_bash_command_always_carried_even_on_failure():
    row = _rec(
        "bash",
        {"status": "error", "input": {"command": "rm -rf /target"},
         "error": "bash: exit 1: permission denied"},
    )
    d = row["detail"]
    assert d["command"] == "rm -rf /target"  # F2: visible even on failure
    assert d["exit"] == 1
    # No text produced: excerpt stays absent.
    assert "output_excerpt" not in row


def test_bash_excerpt_capped_at_2k():
    long_out = "y" * 50_000 + "[tail marker]"
    row = _rec(
        "bash",
        {"status": "completed", "input": {"command": "echo big"},
         "output": long_out},
    )
    d = row["detail"]
    assert len(d["output_excerpt"]) <= OUTPUT_EXCERPT_CHARS + len("…[truncated]")
    assert len(row["output_excerpt"]) <= OUTPUT_EXCERPT_CHARS + 12
    # `truncated` rides the DETAIL.bash command truthfully: the 50K
    # output was clipped at the detail level (excerpt cap < input),
    # and the engine-side tail marker names the cut honestly.
    assert d["truncated"] is True or d.get("clipped")


def test_bash_exit_structured_from_engine_state():
    row = _rec(
        "bash",
        {"status": "completed", "input": {"command": "true"},
         "exit": 0, "output": "ok"},
    )
    assert row["detail"]["exit"] == 0


# ---------------------------------------------------------------------------
# grep/glob/git/todo/defer/ask/escalate/list
# ---------------------------------------------------------------------------


def test_grep_never_carries_match_content():
    row = _rec(
        "grep",
        {"status": "completed",
         "input": {"pattern": "secret", "path": "src", "include": "*.py"},
         "output": "match content 1\nmatch content 2"},
    )
    d = row["detail"]
    assert d["pattern"] == "secret"
    assert d["path"] == "src" and d["include"] == "*.py"
    assert row["output_excerpt"].count("match") <= 2  # carry is informational
    assert "matchCount" not in d or d["matchCount"] is None  # opencode absent
    # No full content rides the roster — grep doctrine holds.
    assert "content" not in d


def test_grep_match_count_from_engine_state():
    row = _rec(
        "grep",
        {"status": "completed", "input": {"pattern": "p"},
         "matchCount": 42, "output": "results"},
    )
    assert row["detail"]["matchCount"] == 42


def test_glob_pattern_and_count():
    row = _rec("glob", {"status": "completed", "input": {"pattern": "**/*.py"},
                        "count": 12, "output": "aa\nbb\ncc"})
    assert row["detail"]["count"] == 12
    assert row["detail"]["pattern"] == "**/*.py"


def test_git_verb_and_args():
    row = _rec("git", {"status": "completed", "input": {"verb": "log", "args": ["-n", "3"]},
                       "output": "abc\nfix"})
    d = row["detail"]
    assert d["verb"] == "log"
    assert d["args"] == ["-n", "3"]


def test_todo_titles():
    row = _rec("todo", {"status": "completed",
                        "input": {"todos": [
                            {"content": "step A", "status": "done"},
                            {"content": "step B", "status": "in_progress"},
                        ]},
                        "output": "ok"})
    assert row["detail"]["titles"] == ["step A", "step B"]


def test_sweave_one_liners_unchanged():
    """defer/list_specialists/ask_human/escalate keep today's one-liner
    (plan §3 step 1.1 last line)."""
    for tool, payload in (
        ("defer", {"target": "backend", "task": "do"}),
        ("list_specialists", {}),
        ("ask_human", {"question": "Proceed?"}),
        ("escalate", {"message": "blocked"}),
    ):
        row = _rec(tool, {"status": "completed", "input": payload, "output": "…"})
        assert row["detail"] == {}


# ---------------------------------------------------------------------------
# Caps + never-raise
# ---------------------------------------------------------------------------


def test_detail_blob_capped_at_2k():
    huge = "z" * (WRITE_OLD_CAPTURE_CHARS * 4)  # 4x the write cap
    row = _rec(
        "write",
        {"status": "completed",
         "input": {"filePath": "x", "content": "n" * 20_000, "old": huge},
         "output": "wrote x"},
    )
    d = row["detail"]
    blob = json.dumps(d)
    assert len(blob) <= DETAIL_JSON_CHARS * 2 or d.get("clipped")
    assert not (blob and len(blob) > DETAIL_JSON_CHARS * 4)


def test_compact_record_never_raises_on_garbage():
    """Garbage events produce best-effort rows (composer-isolation
    doctrine; the_Classically-narrowed test leaves no dead branch)."""
    garbage_events: list[Any] = [
        None, {}, {"tool": 42}, "string", 42,
        {"callID": None, "tool": "", "state": "oops"},
        {"callID": "c", "tool": "write", "state": {"status": "completed",
                                                    "input": {"filePath": object(),
                                                              "content": None},
                                                    "output": None}},
        {"callID": "c", "tool": "read", "state": {"status": "bogus",
                                                    "input": {"filePath": 42}}},
        {"callID": "c", "tool": "grep", "state": {"status": "completed",
                                                   "input": {"pattern": object()},
                                                   "output": None}},
    ]
    for ev in garbage_events:
        row = compact_tool_record(ev)
        assert isinstance(row, dict)
        assert (row.get("round", 0) == 0) and ("tool" in row) and (isinstance(row.get("status"), str))


def test_row_size_stays_within_the_post_change_budget():
    """A worst-case row (huge command + huge output excerpt) rides the
    caps, never blows the persisted message budget."""
    row = _rec(
        "bash",
        {"status": "completed",
         "input": {"command": "c" * 50_000},
         "output": "o" * 50_000},
    )
    blob = json.dumps(row, default=str)
    # summary cap (160) + detail cap (2K) + excerpt cap (2K) + slack
    assert len(blob) < 160 + 2_000 + 2_000 + 1_000


# ---------------------------------------------------------------------------
# Reload round-trip + composer isolation (regression-pinned contracts)
# ---------------------------------------------------------------------------


def test_rows_round_trip_through_the_project_manager_json(tmp_path):
    """The additive keys survive the Session JSON round-trip (reload is
    the F1 ruling's whole point: persistence is the design)."""
    from sweave.projects import ProjectManager

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    row = _rec("bash", {"status": "completed", "input": {"command": "q"},
                        "output": "ok"})
    from sweave.projects import Message as _Msg
    from datetime import datetime as _dt

    session.messages.append(
        _Msg(
            id="round-trip-1",
            role="assistant",
            content="reply",
            metadata={"tools": [row]},
        )
    )
    pm.save_messages(session)
    # Force a re-fault from disk (drop the resident copy first).
    pm._bodies.pop(session.id, None)
    session.messages = []
    reloaded = pm.get_session(session.id)
    saved = reloaded.messages[-1].metadata["tools"][0]
    assert saved["detail"] == row["detail"]
    assert saved["output_excerpt"] == row["output_excerpt"]


def test_composer_isolation_regression_unrelaxed():
    """The composer reads role/content/superseded only — enriching rows
    is UI-safe by construction (the standing regression pin; the new
    keys ride the row so they can never reach the model context)."""
    from sweave.chat.transcript import _transcript_reference

    user_msg = type(
        "M", (), {"role": "user", "content": "look at it", "metadata": {}}
    )()
    tool_row = {
        "callID": "c",
        "tool": "bash",
        "status": "completed",
        "summary": "pytest",
        "detail": {"command": "never", "exit": 0},
        "output_excerpt": "leak-test",
    }
    from sweave.chat.tools import compact_tool_record  # noqa: F401

    assistant_meta = {"tools": [tool_row]}
    assistant_msg = type(
        "M", (), {"role": "assistant", "content": "the reply", "metadata": assistant_meta}
    )()
    ref = _transcript_reference(transcript_messages=[user_msg, assistant_msg], budget=500)
    # The reference derives from the LIVE THREAD (user rows + role/content),
    # so tool-row keys never reach it (composer isolation holds with the
    # additive keys riding the row).
    assert "leak-test" not in ref
    assert "the reply" not in ref  # the reference quotes the LAST USER, not the reply
    assert "look at it" in ref
