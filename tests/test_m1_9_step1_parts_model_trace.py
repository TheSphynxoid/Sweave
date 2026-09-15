"""M1.9 step 1 tests: trace completeness (parts-model adoption).

Covers:

* **Tool parts**: the harness's stream reader captures tool lifecycle
  (pending -> running -> completed | error) keyed by callID. Each state
  transition emits a ``tool.started | tool.updated | tool.completed |
  tool.failed`` trace event with input / output / title / time fields.
* **Step boundaries**: ``step-start`` / ``step-finish`` parts emit
  ``step.boundary`` events carrying the reason, cost, and token
  breakdown (input / output / reasoning / cache read+write). The
  per-turn ``tokens_used`` audit event aggregates per step.
* **Reasoning parts**: optional flag on/off (``trace_reasoning=True``
  default) — when off, reasoning parts are skipped from the trace (they
  stay in the engine's session).
* **Terminal detection**: turn complete = ``info.time.completed`` set
  AND ``info.finish`` present (replaces the every-chunk "assistant +
  parts" match). Regression: an empty stream with no terminal flag
  does NOT prematurely terminate.
* **Dead branch removed**: ``type: "error"`` parts no longer exist on the
  v2 wire — errors come from ``info.error``. The reader no longer
  treats such a part as a per-chunk error.

Tests are test-first: the file was written before the
implementation. The mock opencode serves canned multi-chunk streams
that exercise the parts model; the trace_log records the resulting
events; assertions check the capture shape and ordering.
"""

from __future__ import annotations

import json as _json
import os
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Hermeticity: same SWEAVE_MOCK_OPENCODE gate as M1.3 step 3 / M1.8
# step 1. The runtime + harness are stubbed; the parts model is
# exercised on the stub wire format. Removing this fixture makes the
# test fail at the env-var assertion (the runtime path would spawn a
# real opencode subprocess).
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


# Each test gets its own trace_dir so the per-delegation JSONL files
# don't bleed between tests.
@pytest.fixture
def trace_dir(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parts_model_stream(
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    steps: list[dict[str, Any]] | None = None,
    text_parts: list[str] | None = None,
    reasoning_parts: list[str] | None = None,
    terminal: dict[str, Any] | None = None,
    info_error: dict[str, Any] | None = None,
) -> list[str]:
    """Build a chunked stream that mimics the opencode v2 parts wire:
    one or more ``{"info": ..., "parts": [...]}`` JSON objects per chunk.

    * ``tool_calls`` -- list of {callID, tool, states: [{status, ...}]}.
      Each state yields a tool part; the harness snapshots by callID.
    * ``steps`` -- list of {type: "step-start" | "step-finish", ...}.
    * ``text_parts`` -- list of assistant text chunks.
    * ``reasoning_parts`` -- list of reasoning text chunks.
    * ``terminal`` -- dict of ``{"time.completed": ..., "finish": "..."}``
      applied to the last info object (the terminal signal).
    * ``info_error`` -- optional dict applied as ``info.error`` on the
      terminal info (the v2 error path).
    """
    chunks: list[str] = []

    info_base: dict[str, Any] = {"role": "assistant"}
    if terminal:
        info_base["time"] = {"created": 0, "completed": terminal.get("completed", 1)}
        info_base["finish"] = terminal.get("finish", "stop")
    else:
        info_base["time"] = {"created": 0}
    if info_error:
        info_base["error"] = info_error

    parts: list[dict[str, Any]] = []
    if steps:
        for s in steps:
            parts.append(dict(s))
    if text_parts:
        for t in text_parts:
            parts.append({"type": "text", "text": t})
    if reasoning_parts:
        for r in reasoning_parts:
            parts.append({"type": "reasoning", "text": r})
    if tool_calls:
        for tc in tool_calls:
            states = tc.get("states") or []
            call_id = tc["callID"]
            tool = tc["tool"]
            for state in states:
                p: dict[str, Any] = {
                    "type": "tool",
                    "callID": call_id,
                    "tool": tool,
                    "state": dict(state),
                }
                parts.append(p)
    chunks.append(_json.dumps({"info": info_base, "parts": parts}))
    return chunks


def _build_opencode_with_chunks(chunks: list[str]):
    """Return an OpenCodeProcess whose stream returns the canned chunks.

    Reuses the existing ``_spawn_mock`` skeleton and patches the
    stream() to serve our canned multi-chunk response. The harness's
    reading code (the part under test) is unchanged.
    """
    from sweave.harness.opencode import OpenCodeHarness
    from sweave.harness.base import AgentSpec

    spec = AgentSpec(
        name="trace-test",
        role="trace-test",
        model="",
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="",
        tools=[],
        env={},
        harness="opencode",
    )
    proc = OpenCodeHarness()._spawn_mock(spec)

    class _StubStreamResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "application/json"}
            self._chunks = [c.encode("utf-8") for c in chunks]

        def raise_for_status(self) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def aiter_text(self):
            for c in self._chunks:
                yield c.decode("utf-8")

        async def aiter_bytes(self):
            for c in self._chunks:
                yield c

    def _stream(method, url, json=None, headers=None, **kw):
        return _StubStreamResponse()

    proc._client.stream = _stream  # type: ignore[assignment]
    return proc


def _read_trace_events(trace_dir: Path, delegation_id: str) -> list[tuple[str, dict[str, Any]]]:
    """Read the JSONL trace file into a list of (event, payload)."""
    from sweave.runtime.trace_log import read_trace

    raw = read_trace(delegation_id, base_dir=trace_dir)
    return [(r.get("event"), {k: v for k, v in r.items() if k not in {"ts", "event", "delegation_id"}}) for r in raw]


# ---------------------------------------------------------------------------
# OpenCodeProcess.send: parts-model capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_parts_capture_full_lifecycle_pending_running_completed(trace_dir):
    """A single tool that goes pending -> running -> completed emits
    three events keyed by callID. The harness snapshots by callID
    (replace semantics) so the trace sees the final state per tool.
    """
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    chunks = _parts_model_stream(
        tool_calls=[
            {
                "callID": "call_1",
                "tool": "bash",
                "states": [
                    {"status": "pending", "input": {"cmd": "ls"}, "raw": "ls"},
                    {
                        "status": "running",
                        "input": {"cmd": "ls"},
                        "title": "ls",
                        "time": {"start": 1},
                    },
                    {
                        "status": "completed",
                        "input": {"cmd": "ls"},
                        "output": "file.txt",
                        "title": "ls",
                        "time": {"start": 1, "end": 2},
                    },
                ],
            }
        ],
        text_parts=["done"],
        terminal={"completed": 2, "finish": "stop"},
    )
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    result = await proc.send(Message(type="user", content="hi"), trace=trace)
    assert result.success is True
    events = _read_trace_events(trace_dir, "t-1")
    tool_events = [e for e in events if e[0].startswith("tool.")]
    names = [n for n, _ in tool_events]
    # Lifecycle order: started (pending), updated (running), completed
    assert names[0] == "tool.started"
    assert "tool.updated" in names
    assert names[-1] == "tool.completed"
    # All tool events carry the callID
    for _name, payload in tool_events:
        assert payload.get("callID") == "call_1"
        assert payload.get("tool") == "bash"
    # The completion payload carries output + time
    completion_payload = [p for n, p in tool_events if n == "tool.completed"][0]
    assert completion_payload.get("state", {}).get("output") == "file.txt"
    assert completion_payload.get("state", {}).get("time", {}).get("end") == 2


@pytest.mark.asyncio
async def test_tool_parts_failure_emits_tool_failed_not_completed(trace_dir):
    """A tool state ``status == 'error'`` emits ``tool.failed`` (NOT
    ``tool.completed``) and surfaces the error string verbatim."""
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    chunks = _parts_model_stream(
        tool_calls=[
            {
                "callID": "call_fail",
                "tool": "edit",
                "states": [
                    {"status": "pending", "input": {"path": "x"}, "raw": "x"},
                    {
                        "status": "error",
                        "input": {"path": "x"},
                        "error": "ENOENT",
                        "time": {"start": 1, "end": 2},
                    },
                ],
            }
        ],
        text_parts=["failed"],
        terminal={"completed": 2, "finish": "stop"},
    )
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    await proc.send(Message(type="user", content="x"), trace=trace)
    events = _read_trace_events(trace_dir, "t-1")
    tool_events = [(n, p) for n, p in events if n.startswith("tool.")]
    names = [n for n, _ in tool_events]
    assert "tool.failed" in names
    assert "tool.completed" not in names
    fail_payload = [p for n, p in tool_events if n == "tool.failed"][0]
    assert fail_payload.get("state", {}).get("error") == "ENOENT"


@pytest.mark.asyncio
async def test_step_start_and_finish_emit_step_boundary_event(trace_dir):
    """step-start + step-finish parts emit a single ``step.boundary``
    event carrying the tokens breakdown + reason + the step-finish
    cost."""
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    chunks = _parts_model_stream(
        steps=[
            {"type": "step-start", "snapshot": "abc123"},
            {
                "type": "step-finish",
                "reason": "stop",
                "snapshot": "def456",
                "cost": 0.001,
                "tokens": {
                    "input": 10,
                    "output": 5,
                    "reasoning": 2,
                    "cache": {"read": 1, "write": 0},
                },
            },
        ],
        text_parts=["hello"],
        terminal={"completed": 3, "finish": "stop"},
    )
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    await proc.send(Message(type="user", content="hi"), trace=trace)
    events = _read_trace_events(trace_dir, "t-1")
    boundary_events = [(n, p) for n, p in events if n == "step.boundary"]
    assert len(boundary_events) == 1
    payload = boundary_events[0][1]
    assert payload.get("reason") == "stop"
    assert payload.get("cost") == 0.001
    tokens = payload.get("tokens") or {}
    assert tokens.get("input") == 10
    assert tokens.get("output") == 5
    assert tokens.get("reasoning") == 2
    cache = tokens.get("cache") or {}
    assert cache.get("read") == 1


@pytest.mark.asyncio
async def test_per_turn_tokens_used_event_is_emitted(trace_dir):
    """The per-turn ``tokens_used`` event aggregates token usage across
    all step-finish parts in this turn. It is emitted exactly once per
    turn (after the terminal)."""
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    chunks = _parts_model_stream(
        steps=[
            {
                "type": "step-finish",
                "reason": "tool",
                "cost": 0.0005,
                "tokens": {
                    "input": 5,
                    "output": 2,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
            },
            {
                "type": "step-finish",
                "reason": "stop",
                "cost": 0.001,
                "tokens": {
                    "input": 3,
                    "output": 4,
                    "reasoning": 1,
                    "cache": {"read": 0, "write": 0},
                },
            },
        ],
        text_parts=["a", "b"],
        terminal={"completed": 5, "finish": "stop"},
    )
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    await proc.send(Message(type="user", content="hi"), trace=trace)
    events = _read_trace_events(trace_dir, "t-1")
    tu = [(n, p) for n, p in events if n == "tokens_used"]
    assert len(tu) == 1
    payload = tu[0][1]
    # Aggregated: input=5+3=8, output=2+4=6, reasoning=0+1=1, cost=0.0015
    assert payload.get("input") == 8
    assert payload.get("output") == 6
    assert payload.get("reasoning") == 1
    assert abs(payload.get("cost", 0) - 0.0015) < 1e-9
    # Peak live context (max single-step prompt), not the billed sum.
    assert payload.get("context_input") == 5


@pytest.mark.asyncio
async def test_reasoning_parts_are_skipped_by_default(trace_dir):
    """Reasoning parts default OFF (they stay in the engine's session
    but don't bloat the JSONL). ``trace_reasoning=True`` includes them."""
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    chunks = _parts_model_stream(
        reasoning_parts=["thinking..."],
        text_parts=["answer"],
        terminal={"completed": 2, "finish": "stop"},
    )
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    await proc.send(Message(type="user", content="hi"), trace=trace)
    events = _read_trace_events(trace_dir, "t-1")
    reasoning_events = [e for e in events if e[0] == "reasoning"]
    assert reasoning_events == []


@pytest.mark.asyncio
async def test_reasoning_parts_are_captured_when_trace_reasoning_enabled(trace_dir):
    """With ``trace_reasoning=True`` on the harness, reasoning parts
    emit ``reasoning`` events with the raw text."""
    from sweave.harness.opencode import OpenCodeHarness
    from sweave.harness.base import AgentSpec, Message
    from sweave.runtime.trace_log import TraceLog

    chunks = _parts_model_stream(
        reasoning_parts=["think1", "think2"],
        text_parts=["final"],
        terminal={"completed": 3, "finish": "stop"},
    )

    spec = AgentSpec(
        name="trace-test",
        role="trace-test",
        model="",
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="",
        tools=[],
        env={},
        harness="opencode",
    )
    proc = OpenCodeHarness()._spawn_mock(spec)
    proc._client.stream = _build_opencode_with_chunks(chunks)._client.stream  # type: ignore[assignment]
    proc.trace_reasoning = True

    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    await proc.send(Message(type="user", content="hi"), trace=trace)
    events = _read_trace_events(trace_dir, "t-1")
    reasoning = [e for e in events if e[0] == "reasoning"]
    assert [p.get("text") for _, p in reasoning] == ["think1", "think2"]


# ---------------------------------------------------------------------------
# Terminal detection regression (the core M1.9 fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_only_with_completed_and_finish(trace_dir):
    """A response with parts but NO ``info.time.completed`` AND NO
    ``info.finish`` is treated as NOT-terminal: the harness returns
    the empty-response error. This is the M1.9 fix; the pre-M1.9
    heuristic would have falsely declared success because parts
    were emitted alongside the assistant role.

    The response object here has parts + the assistant role (so the
    pre-M1.9 heuristic would fire), but ``info.time.completed`` is
    absent and ``info.finish`` is absent -- terminal is therefore not
    declared, and the harness surfaces an explicit empty-response
    error.
    """
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    body = {
        "info": {"role": "assistant", "time": {"created": 0}},
        # NOTE: no "completed" on time; no "finish"
        "parts": [{"type": "text", "text": "partial answer"}],
    }
    chunks = [_json.dumps(body)]
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    result = await proc.send(Message(type="user", content="hi"), trace=trace)
    assert result.success is False
    assert "empty response" in (result.error or "")


@pytest.mark.asyncio
async def test_terminal_with_completed_and_finish_returns_success(trace_dir):
    """A response with parts AND ``info.time.completed`` AND
    ``info.finish`` is the success path. Result text is joined."""
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    body = {
        "info": {
            "role": "assistant",
            "time": {"created": 0, "completed": 1},
            "finish": "stop",
        },
        "parts": [{"type": "text", "text": "all done"}],
    }
    chunks = [_json.dumps(body)]
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    result = await proc.send(Message(type="user", content="hi"), trace=trace)
    assert result.success is True
    assert "all done" in result.output


# ---------------------------------------------------------------------------
# Dead-branch removal: type:"error" parts no longer treated as errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_error_part_branch_removes_dead_handling(trace_dir):
    """A ``type: "error"`` part is no longer treated as an error. The
    v2 wire doesn't emit such parts (errors come from info.error).
    The reader must not interpret a non-existent part type as a
    failure. We exercise the inverse: a parts-only response with
    info.error set surfaces verbatim, NOT a per-part error."""
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    body = {
        "info": {
            "role": "assistant",
            "time": {"created": 0, "completed": 1},
            "finish": "stop",
            "error": {"name": "ProviderAuthError", "data": {"message": "bad key"}},
        },
        "parts": [{"type": "text", "text": "x"}],
    }
    chunks = [_json.dumps(body)]
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    result = await proc.send(Message(type="user", content="hi"), trace=trace)
    # info.error -> the existing pre-M1.9 path surfaces it as the
    # error payload; this regression test pins the boundary so the
    # dead part branch removal does not regress the info.error path.
    assert result.error is not None
    assert "ProviderAuthError" in result.error or "bad key" in result.error


# ---------------------------------------------------------------------------
# Backwards-compatibility for text-only streams
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_only_stream_unchanged_from_pre_m1_9(trace_dir):
    """A text-only response (no tool parts, no step boundaries) with
    a proper terminal returns success with the joined text. This
    pins the no-behaviour-change guarantee called out in the plan."""
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    chunks = _parts_model_stream(
        text_parts=["hello ", "world"],
        terminal={"completed": 1, "finish": "stop"},
    )
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    result = await proc.send(Message(type="user", content="hi"), trace=trace)
    assert result.success is True
    assert "hello world" in result.output


# ---------------------------------------------------------------------------
# Mocked multi-tool stream: ordered tool lifecycle + steps + tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_tool_stream_full_audit_trail(trace_dir):
    """A realistic multi-tool turn: 2 tools (one OK, one error), a
    step boundary, a per-turn tokens_used event, and a terminal.
    The trace must capture all events in order with no gaps."""
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    chunks = _parts_model_stream(
        tool_calls=[
            {
                "callID": "call_a",
                "tool": "bash",
                "states": [
                    {"status": "pending", "input": {"cmd": "ls"}, "raw": "ls"},
                    {
                        "status": "running",
                        "input": {"cmd": "ls"},
                        "title": "ls",
                        "time": {"start": 1},
                    },
                    {
                        "status": "completed",
                        "input": {"cmd": "ls"},
                        "output": "ok",
                        "title": "ls",
                        "time": {"start": 1, "end": 2},
                    },
                ],
            },
            {
                "callID": "call_b",
                "tool": "read",
                "states": [
                    {
                        "status": "error",
                        "input": {"path": "missing"},
                        "error": "ENOENT",
                        "time": {"start": 3, "end": 4},
                    },
                ],
            },
        ],
        steps=[
            {
                "type": "step-finish",
                "reason": "stop",
                "cost": 0.002,
                "tokens": {
                    "input": 7,
                    "output": 3,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
            }
        ],
        text_parts=["final answer"],
        terminal={"completed": 10, "finish": "stop"},
    )
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog(delegation_id="t-1", base_dir=trace_dir)
    result = await proc.send(Message(type="user", content="hi"), trace=trace)
    assert result.success is True
    events = _read_trace_events(trace_dir, "t-1")
    names = [n for n, _ in events]
    # Required events all present
    assert "tool.started" in names
    assert "tool.completed" in names
    assert "tool.failed" in names
    assert "step.boundary" in names
    assert "tokens_used" in names
    # Final tokens_used is the audit anchor: it must be last among
    # the audit events (terminal-bound).
    tu_index = names.index("tokens_used")
    assert tu_index == len(names) - 1