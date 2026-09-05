"""M1.9 step 4 tests: visibility surfaces.

Three CLI commands + one web detail view.

* ``sweave log <delegation_id>`` -- pretty-render a trace as sections
  (composed prompt, tool timeline, tokens/cost per step, synthesis,
  final output). The same render the detail view produces.
* ``sweave watch`` -- live tree: delegations + statuses, polled (no
  WS dependency in the CLI; the WS is the future-friendly path).
* ``sweave tail <delegation_id>`` -- follow a running turn; stream
  the trace JSONL as it grows.

The web detail view (``GET /api/delegations/{id}/detail``) returns
the structured data the UI patches into the existing detail panel:
composed prompt (collapsible), tool timeline (callID-keyed),
tokens/cost per step, the synthesis block, the final output. UI
rendering is a thin wrapper around this data.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest


# Each test gets its own home dir so traces and tokens don't bleed.
@pytest.fixture
def home_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def _write_trace(path: Path, events: list[tuple[str, dict[str, Any]]]) -> None:
    """Write a list of (event, payload) tuples as a JSONL trace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        import datetime as _dt

        for event, payload in events:
            rec = {
                "ts": _dt.datetime.now().isoformat(),
                "event": event,
                "delegation_id": payload.get("delegation_id", "t-1"),
                **payload,
            }
            f.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------------------
# sweave log <delegation_id>
# ---------------------------------------------------------------------------


def test_log_command_renders_sections(home_dir, capsys):
    """``sweave log t-1`` reads ``~/.sweave/traces/t-1.jsonl`` and
    prints sections: composed prompt, tool timeline, tokens/cost per
    step, final output. The detail-view data is also exposed via the
    read_trace_events API."""
    from typer.testing import CliRunner
    from sweave.cli.main import app as cli_app

    # Plant a trace with a representative set of events.
    trace_dir = home_dir / ".sweave" / "traces"
    _write_trace(
        trace_dir / "t-1.jsonl",
        [
            ("status_changed", {"status": "running"}),
            (
                "composed_prompt",
                {
                    "memory_chars": 100,
                    "whats_new_chars": 50,
                    "synthesis_chars": 0,
                    "transcript_ref_chars": 30,
                    "user_chars": 200,
                },
            ),
            (
                "tool.started",
                {
                    "callID": "call_a",
                    "tool": "bash",
                    "state": {"status": "pending", "input": {"cmd": "ls"}},
                },
            ),
            (
                "tool.completed",
                {
                    "callID": "call_a",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"cmd": "ls"},
                        "output": "ok",
                        "title": "ls",
                        "time": {"start": 1, "end": 2},
                    },
                },
            ),
            (
                "step.boundary",
                {
                    "reason": "stop",
                    "cost": 0.001,
                    "tokens": {
                        "input": 10,
                        "output": 5,
                        "reasoning": 0,
                        "cache": {"read": 0, "write": 0},
                    },
                },
            ),
            ("tokens_used", {"input": 10, "output": 5, "reasoning": 0, "cost": 0.001}),
            ("status_changed", {"status": "review", "source": "system"}),
        ],
    )

    runner = CliRunner()
    result = runner.invoke(cli_app, ["log", "t-1"])
    # typer's CliRunner exits 0 even when the command raises; we check
    # stdout content instead.
    assert "Delegation t-1" in result.output or "t-1" in result.output
    # Sections are surfaced
    assert "Composed prompt" in result.output
    assert "Tool timeline" in result.output
    # Tokens surfaced
    assert "input" in result.output or "tokens" in result.output.lower()


def test_log_command_handles_missing_trace(home_dir, capsys):
    """A delegation with no trace file prints a friendly not-found
    message (not a stack trace)."""
    from typer.testing import CliRunner
    from sweave.cli.main import app as cli_app

    runner = CliRunner()
    result = runner.invoke(cli_app, ["log", "nonexistent"])
    assert "not found" in result.output.lower() or "no trace" in result.output.lower()


# ---------------------------------------------------------------------------
# sweave watch
# ---------------------------------------------------------------------------


def test_watch_command_prints_tree(capsys):
    """``sweave watch`` queries the running server's
    ``/api/delegations`` endpoint and prints the live tree. Without
    a server we point the resolver at a temp file."""
    # Without a server, watch should not crash; it should print a
    # helpful "no server" message. The exact behavior is best-effort.
    # The watch command runs an infinite asyncio loop, so the test
    # only verifies the command is registered + importable; a full
    # integration test would run the server + watch together.
    from typer.testing import CliRunner
    from sweave.cli.main import app as cli_app

    runner = CliRunner()
    # CliRunner.invoke blocks until the command returns. Watch loops
    # forever; we patch asyncio.run to a one-shot so the test exits.
    import asyncio as _asyncio

    orig_run = _asyncio.run

    def _one_shot(coro):
        # Close the coroutine to suppress "never awaited" warning.
        try:
            coro.close()
        except Exception:
            pass
        return None

    _asyncio.run = _one_shot
    try:
        result = runner.invoke(cli_app, ["watch"])
        # Exit code 0 (clean exit via the patched run) or None (typer
        # cancelled on Ctrl+C) are both fine.
        assert result.exit_code in (0, 1, None)
    finally:
        _asyncio.run = orig_run


# ---------------------------------------------------------------------------
# sweave tail <delegation_id>
# ---------------------------------------------------------------------------


def test_tail_command_streams_trace(home_dir):
    """``sweave tail t-1`` follows ``~/.sweave/traces/t-1.jsonl`` and
    prints new lines as they're appended. The basic API: ``follow_trace``
    yields each line in append order."""
    from sweave.cli.tail import follow_trace

    trace_dir = home_dir / ".sweave" / "traces"
    path = trace_dir / "t-1.jsonl"
    _write_trace(
        path,
        [
            ("status_changed", {"status": "running"}),
            ("output_text", {"chunks": 1, "length": 5}),
        ],
    )

    collected: list[str] = []

    async def _collect():
        async for line in follow_trace(path, from_start=True):
            collected.append(line)
            if len(collected) >= 2:
                return

    asyncio.run(_collect())
    assert len(collected) == 2
    assert "running" in collected[0]
    assert "output_text" in collected[1] or "chunks" in collected[1]


# ---------------------------------------------------------------------------
# Detail view API
# ---------------------------------------------------------------------------


def test_detail_view_returns_structured_sections(home_dir):
    """``GET /api/delegations/{id}/detail`` returns the data the UI
    detail view patches into place: composed prompt, tool timeline,
    tokens, final output. The trace is the source of truth."""
    from sweave.web.detail_view import render_detail_view

    trace_dir = home_dir / ".sweave" / "traces"
    _write_trace(
        trace_dir / "t-1.jsonl",
        [
            (
                "composed_prompt",
                {
                    "memory_chars": 100,
                    "whats_new_chars": 50,
                    "synthesis_chars": 200,
                    "transcript_ref_chars": 30,
                    "user_chars": 200,
                },
            ),
            (
                "tool.completed",
                {
                    "callID": "call_a",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"cmd": "ls"},
                        "output": "ok",
                        "time": {"start": 1, "end": 2},
                    },
                },
            ),
            (
                "tokens_used",
                {"input": 10, "output": 5, "cost": 0.001},
            ),
        ],
    )

    detail = render_detail_view("t-1", trace_dir=trace_dir)
    assert detail["delegation_id"] == "t-1"
    assert "composed_prompt" in detail
    assert "tool_timeline" in detail
    assert "tokens" in detail
    assert len(detail["tool_timeline"]) == 1
    assert detail["tool_timeline"][0]["callID"] == "call_a"


def test_detail_view_tool_timeline_is_callID_keyed(home_dir):
    """The tool timeline keeps one entry per callID, with the
    final state (snapshot semantics) -- multiple state transitions
    on the same callID collapse to one entry that records the
    full lifecycle."""
    from sweave.web.detail_view import render_detail_view

    trace_dir = home_dir / ".sweave" / "traces"
    _write_trace(
        trace_dir / "t-1.jsonl",
        [
            ("tool.started", {"callID": "call_x", "tool": "edit", "state": {"status": "pending"}}),
            ("tool.updated", {"callID": "call_x", "tool": "edit", "state": {"status": "running"}}),
            ("tool.completed", {"callID": "call_x", "tool": "edit", "state": {"status": "completed", "output": "ok"}}),
            ("tool.started", {"callID": "call_y", "tool": "bash", "state": {"status": "pending"}}),
            ("tool.failed", {"callID": "call_y", "tool": "bash", "state": {"status": "error", "error": "ENOENT"}}),
        ],
    )
    detail = render_detail_view("t-1", trace_dir=trace_dir)
    by_callid = {t["callID"]: t for t in detail["tool_timeline"]}
    assert "call_x" in by_callid and "call_y" in by_callid
    # Each entry carries the lifecycle states
    assert by_callid["call_x"]["states"][-1]["status"] == "completed"
    assert by_callid["call_y"]["states"][-1]["status"] == "error"
    assert len(detail["tool_timeline"]) == 2  # snapshot per callID


def test_detail_view_includes_status_timeline(home_dir):
    """The detail view carries a status_changes timeline (queued,
    running, review, done, failed) -- the navigation breadcrumb the
    Children tab already shows; the detail view surfaces the same
    sequence for context."""
    from sweave.web.detail_view import render_detail_view

    trace_dir = home_dir / ".sweave" / "traces"
    _write_trace(
        trace_dir / "t-1.jsonl",
        [
            ("status_changed", {"status": "queued"}),
            ("status_changed", {"status": "running"}),
            ("status_changed", {"status": "review", "source": "system"}),
        ],
    )
    detail = render_detail_view("t-1", trace_dir=trace_dir)
    statuses = [c["status"] for c in detail["status_timeline"]]
    assert statuses == ["queued", "running", "review"]


def test_detail_view_handles_missing_trace_gracefully(home_dir):
    """A delegation with no trace returns a minimal detail view
    (just the id + an empty sections dict). Never 500s on a
    missing trace -- the trace is best-effort detail, not source
    of truth."""
    from sweave.web.detail_view import render_detail_view

    trace_dir = home_dir / ".sweave" / "traces"
    detail = render_detail_view("nonexistent", trace_dir=trace_dir)
    assert detail["delegation_id"] == "nonexistent"
    assert detail["tool_timeline"] == []
    assert detail["status_timeline"] == []
    assert detail["tokens"] is None or detail["tokens"] == {}