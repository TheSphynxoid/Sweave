"""Tool-lifecycle re-verification + unknown-part degrade (step 2c).

Specialist-view plan Amendment 2026-09-16 step 2c — re-verified by
GREP, not docstring (2026-09-16):

* ``sweave/harness/opencode.py::_emit_tool_trace``: lifecycle
  complete on every observed status — pending -> ``tool.started``;
  running -> ``tool.updated``; completed -> ``tool.completed``;
  error -> ``tool.failed``. First-time-only started: the first part
  for a callID initializes first_state and a non-pending status
  synthesizes the ``tool.started`` begin marker (v2 wire is
  permissive about pending). Other statuses after the first are
  intentionally not re-emitted.
* Both stream readers forward every emitted transition to
  ``on_tool`` (harness ``OpenCodeProcess.send`` + runtime
  ``_send_message``), so the ``tool_timeline`` projector lights up
  unchanged from either path.
* GAP CLOSED by this slice: unknown part types were silently
  ignored by BOTH readers' type switches. Now they trace
  ``unknown_part {type, raw}`` once per type per turn (raw values
  stringified/capped at 300 chars), never failing the turn — a
  version bump that renames parts degrades to "unknown part" rows.

The fixture replays recorded v2 part shapes (matching the probe
inventory: part keys callID/id/messageID/metadata/sessionID/state/
tool/type; state keys input/metadata/output/status/time/title).
"""

from __future__ import annotations

import json as _json
import os
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def trace_dir(tmp_path):
    return tmp_path


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


def _chunks_to_stream(chunks: list[str]):
    """Async iterator over canned text chunks."""
    class _Iter:
        def __init__(self) -> None:
            self._i = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._i >= len(chunks):
                raise StopAsyncIteration
            c = chunks[self._i]
            self._i += 1
            return c

    return _Iter()


def _cm_for(chunks: list[str]):
    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        _chunks = list(chunks)

        def raise_for_status(self) -> None:
            pass

        def aiter_text(self):
            return _chunks_to_stream(self._chunks)

    class _CM:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *exc):
            return False

    return _CM()


def _build_opencode_with_chunks(chunks: list[str]):
    """OpenCodeProcess whose stream serves the canned chunks (the
    reading code under test is unchanged)."""
    from sweave.harness.opencode import OpenCodeHarness, OpenCodeProcess
    from sweave.harness.base import AgentSpec

    spec = AgentSpec(
        name="tl-test",
        role="tl-test",
        model="",
        system_prompt="",
        worktree_path=Path("."),
        memory_bank="",
        tools=[],
        env={},
        harness="opencode",
    )
    proc = OpenCodeHarness()._spawn_mock(spec)
    cm = _cm_for(chunks)
    proc._client.stream = lambda *a, **k: cm  # type: ignore[method-assign]
    return proc


def _read(trace_dir: Path, did: str) -> list[dict]:
    from sweave.runtime.trace_log import read_trace

    return read_trace(did, base_dir=trace_dir)


def _recorded_tool_stream() -> list[str]:
    """Recorded shapes: the probe's bash part (input + timeout in ms)
    through the pending->running->completed lifecycle."""
    return [
        _json.dumps(
            {
                "info": {"role": "assistant", "time": {}},
                "parts": [
                    {
                        "type": "tool",
                        "callID": "call_rec",
                        "tool": "bash",
                        "state": {
                            "status": "pending",
                            "input": {"command": "ls", "timeout": 170000},
                        },
                    }
                ],
            }
        ),
        _json.dumps(
            {
                "info": {"role": "assistant", "time": {}},
                "parts": [
                    {
                        "type": "tool",
                        "callID": "call_rec",
                        "tool": "bash",
                        "state": {
                            "status": "running",
                            "input": {"command": "ls", "timeout": 170000},
                            "title": "ls",
                            "time": {"start": 1},
                        },
                    }
                ],
            }
        ),
        _json.dumps(
            {
                "info": {
                    "role": "assistant",
                    "time": {"completed": 3},
                    "finish": "stop",
                },
                "parts": [
                    {"type": "text", "text": "listed"},
                    {
                        "type": "tool",
                        "callID": "call_rec",
                        "tool": "bash",
                        "state": {
                            "status": "completed",
                            "input": {"command": "ls"},
                            "output": "f.txt",
                            "title": "ls",
                            "time": {"start": 1, "end": 2},
                        },
                    },
                ],
            }
        ),
    ]


@pytest.mark.asyncio
async def test_recorded_shapes_yield_populated_timeline_with_lifecycle(
    trace_dir,
):
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog
    from sweave.web.detail_view import render_detail_view

    proc = _build_opencode_with_chunks(_recorded_tool_stream())
    trace = TraceLog("tl-1", base_dir=trace_dir)
    result = await proc.send(Message(type="user", content="hi"), trace=trace)
    assert result.success is True
    events = _read(trace_dir, "tl-1")
    names = [e["event"] for e in events if e["event"].startswith("tool.")]
    assert names == ["tool.started", "tool.updated", "tool.completed"]
    completed = [e for e in events if e["event"] == "tool.completed"][0]
    assert completed["state"]["output"] == "f.txt"
    assert completed["state"]["title"] == "ls"
    # The detail projector lights up unchanged (snapshot + states).
    detail = render_detail_view("tl-1", trace_dir=trace_dir)
    assert len(detail["tool_timeline"]) == 1
    entry = detail["tool_timeline"][0]
    assert entry["status"] == "completed"
    assert [s.get("status") for s in entry["states"]] == [
        "pending",
        "running",
        "completed",
    ]


@pytest.mark.asyncio
async def test_error_only_shape_emits_started_then_failed(trace_dir):
    """The permissive wire shape: a single error part with no prior
    pending/running still produces started -> failed (the synthesized
    begin marker)."""
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    chunks = [
        _json.dumps(
            {
                "info": {
                    "role": "assistant",
                    "time": {"completed": 2},
                    "finish": "stop",
                },
                "parts": [
                    {
                        "type": "tool",
                        "callID": "call_e",
                        "tool": "edit",
                        "state": {
                            "status": "error",
                            "input": {"filePath": "x"},
                            "error": "ENOENT",
                        },
                    }
                ],
            }
        )
    ]
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog("tl-2", base_dir=trace_dir)
    await proc.send(Message(type="user", content="hi"), trace=trace)
    events = _read(trace_dir, "tl-2")
    names = [e["event"] for e in events if e["event"].startswith("tool.")]
    assert names == ["tool.started", "tool.failed"]
    failed = [e for e in events if e["event"] == "tool.failed"][0]
    assert failed["state"]["error"] == "ENOENT"


@pytest.mark.asyncio
async def test_unknown_part_types_trace_raw_never_fail(trace_dir):
    """Version-bump drift: an unrecognized part type produces ONE
    ``unknown_part`` row (bounded) and the turn still completes —
    never a turn failure. Same type twice collapses to one event."""
    from sweave.harness.base import Message
    from sweave.runtime.trace_log import TraceLog

    chunks = [
        _json.dumps(
            {
                "info": {"role": "assistant", "time": {}},
                "parts": [
                    {
                        "type": "lets-call-it-a-snack",
                        "callID": "call_x",
                        "metadata": {"deep": {"nested": [1, 2] * 200}},
                        "sessionID": "ses_x",
                    },
                    {"type": "lets-call-it-a-snack", "sessionID": "ses_y"},
                ],
            }
        ),
        _json.dumps(
            {
                "info": {
                    "role": "assistant",
                    "time": {"completed": 2},
                    "finish": "stop",
                },
                "parts": [{"type": "text", "text": "still fine"}],
            }
        ),
    ]
    proc = _build_opencode_with_chunks(chunks)
    trace = TraceLog("tl-3", base_dir=trace_dir)
    result = await proc.send(Message(type="user", content="hi"), trace=trace)
    assert result.success is True
    assert result.output == "still fine"
    events = _read(trace_dir, "tl-3")
    unknowns = [e for e in events if e["event"] == "unknown_part"]
    assert len(unknowns) == 1  # once per type per turn, not per occurrence
    assert unknowns[0]["type"] == "lets-call-it-a-snack"
    raw = unknowns[0]["raw"]
    assert raw["sessionID"] == "ses_x"  # first occurrence's fields
    # Deep shapes are stringified + hard-capped at 300 chars.
    assert len(raw["metadata"]) == 300
    # No tool events, no turn failure.
    assert [e for e in events if e["event"].startswith("tool.")] == []


@pytest.mark.asyncio
async def test_runtime_reader_unknown_part_degrades_too(trace_dir):
    """The runtime path (_send_message's own stream reader) matches:
    same unknown -> raw trace, still-fine return, never a raise."""
    import asyncio
    import json as _j

    from sweave.harness.opencode import OpenCodeProcess
    from sweave.runtime.trace_log import TraceLog

    stream_text = "\n".join(
        [
            _j.dumps(
                {
                    "info": {
                        "role": "assistant",
                        "time": {"completed": 1},
                        "finish": "stop",
                    },
                    "parts": [
                        {
                            "type": "totally-new-kind",
                            "callID": "c9",
                            "blob": {"payload": "zoom" * 300},
                            "sessionID": "ses_rt",
                        },
                        {"type": "text", "text": "ok"},
                    ],
                }
            )
        ]
    )

    cm = _cm_for([stream_text])
    spec = type(
        "_S",
        (),
        {
            "name": "rt",
            "role": "rt",
            "model": "",
            "system_prompt": "",
            "worktree_path": None,
            "tools": [],
        },
    )()
    process = OpenCodeProcess(
        spec=spec,  # type: ignore[arg-type]
        process=type("_P", (), {"pid": 0})(),  # dummy subprocess handle
        base_url="http://x",
        session_id="ses_rt",
    )
    process._client.stream = lambda *a, **k: cm  # type: ignore[method-assign]
    trace = TraceLog("rt-1", base_dir=trace_dir)
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    # _send_message touches nothing from self except the logger; a
    # bare instance is enough (the reader under test is a method).
    runtime = SpecialistRuntime.__new__(SpecialistRuntime)
    body = {"parts": [{"type": "text", "text": "go"}], "agent": "sweave-specialist"}
    loop = asyncio.get_running_loop()
    out = await asyncio.wait_for(
        runtime._send_message(
            process, body, trace, delegation_id="del-rt-1", t0=loop.time()
        ),
        timeout=10,
    )
    assert out == "ok"
    events = _read(trace_dir, "rt-1")
    unknowns = [e for e in events if e["event"] == "unknown_part"]
    assert len(unknowns) == 1
    assert unknowns[0]["type"] == "totally-new-kind"
    raw = unknowns[0]["raw"]
    # Deep shapes are stringified + hard-capped at 300.
    assert isinstance(raw["blob"], str) and raw["blob"].startswith("{'payload': 'zoom")
    assert len(raw["blob"]) == 300
    assert raw["sessionID"] == "ses_rt"
