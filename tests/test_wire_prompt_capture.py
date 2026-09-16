"""Actually-sent prompt capture for opencode turns (step 2b).

View-plan Amendment 2026-09-16 step 2b: the opencode send sites
(``_run_opencode`` body construction + ``_bounded_system_send``
templated render) trace a ``wire_prompt`` event — what the wire
actually carried, as SIZES + bounded preview (the locked shape,
mirroring the chat ``composed_prompt`` audit; sizes bound trace
growth on long turns, the full task text already rides the
runner's ``prompt_sent`` and the full rendered system text stays in
memory). Static prompts keep the legacy one-off path untouched —
no event, no wire change. The capture never fails a turn.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


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


def _make_runtime():
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    return SpecialistRuntime(runners=ServeRunnerRegistry())


def _make_specialist(*, orchestrator=False, templated=False):
    from sweave.runtime.specialist_store import Specialist

    prompt = (
        "Work as {{agent}} for {{task}}."
        if templated
        else "SEED PROMPT"
    )
    return Specialist(
        name="orchestrator" if orchestrator else "backend",
        scope="project",
        is_orchestrator=orchestrator,
        system_prompt=prompt,
        harness="opencode",
        current_model=None,
    )


def _make_delegation(agent="backend"):
    from sweave.runtime.delegation_store import Delegation

    return Delegation(
        delegation_id="del-wire-1",
        agent=agent,
        task="do the thing",
        status="running",
    )


def _wire_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("event") == "wire_prompt"]


@pytest.mark.asyncio
async def test_turn_wire_prompt_event_present(tmp_path: Path):
    """The body-construction site traces sizes + preview of the wire
    message (preamble split from the task)."""
    from sweave.runtime.trace_log import TraceLog
    from sweave.runtime.trace_log import read_trace

    runtime = _make_runtime()

    async def fake_send(self, body=None, trace=None, **kwargs):
        return "ok"

    runtime._send_message = fake_send  # type: ignore[assignment]
    trace = TraceLog("del-wire-1", base_dir=tmp_path / "traces")

    task = "patch the login flow"
    await runtime.run(
        specialist=_make_specialist(),
        delegation=_make_delegation(),
        worktree_path=tmp_path,
        message=task,
        trace=trace,
    )
    events = read_trace("del-wire-1", tmp_path / "traces")
    wire = _wire_events(events)
    assert len(wire) == 1
    ev = wire[0]
    assert ev["phase"] == "turn"
    assert ev["task_chars"] == len(task)
    assert ev["total_chars"] == len(task) + ev["preamble_chars"]
    assert ev["preamble_chars"] > 0
    assert "Task working directory:" in ev["preview"]
    assert task in ev["preview"]


@pytest.mark.asyncio
async def test_rendered_preamble_turn_yields_capture(tmp_path: Path):
    """A templated specialist renders its system prompt per turn:
    the render send gets its own ``wire_prompt`` (system_render
    phase) BEFORE the turn event, both on one turn's trace."""
    from sweave.runtime.trace_log import TraceLog
    from sweave.runtime.trace_log import read_trace

    runtime = _make_runtime()

    async def fake_send(self, body=None, trace=None, **kwargs):
        return "ok"

    runtime._send_message = fake_send  # type: ignore[assignment]
    trace = TraceLog("del-wire-2", base_dir=tmp_path / "traces")

    spec = _make_specialist(templated=True)
    await runtime.run(
        specialist=spec,
        delegation=_make_delegation(),
        worktree_path=tmp_path,
        message="the task",
        trace=trace,
    )
    events = read_trace("del-wire-2", tmp_path / "traces")
    wire = _wire_events(events)
    phases = [e["phase"] for e in wire]
    assert phases[0] == "system_render"
    assert "turn" in phases
    sys_ev = wire[0]
    assert sys_ev["chars"] > 0
    assert len(sys_ev["preview"]) <= 200
    # `preview` carries the rendered identity doc head.
    assert "Work as" in sys_ev["preview"]

    turn_ev = [e for e in wire if e["phase"] == "turn"][0]
    assert turn_ev["task_chars"] == len("the task")


@pytest.mark.asyncio
async def test_static_prompt_path_untouched(tmp_path: Path):
    """Static prompts keep the legacy one-off send in
    _ensure_session — NO wire_prompt system_render event (its
    presence is the templated-path marker), and the turn event
    still rides (the body construction always traces)."""
    from sweave.runtime.trace_log import TraceLog
    from sweave.runtime.trace_log import read_trace

    runtime = _make_runtime()

    async def fake_send(self, body=None, trace=None, **kwargs):
        return "ok"

    runtime._send_message = fake_send  # type: ignore[assignment]
    trace = TraceLog("del-wire-3", base_dir=tmp_path / "traces")

    await runtime.run(
        specialist=_make_specialist(),  # static "SEED PROMPT"
        delegation=_make_delegation(),
        worktree_path=tmp_path,
        message="hi",
        trace=trace,
    )
    events = read_trace("del-wire-3", tmp_path / "traces")
    phases = [e["phase"] for e in _wire_events(events)]
    assert "system_render" not in phases
    assert "turn" in phases


@pytest.mark.asyncio
async def test_capture_never_fails_the_turn(tmp_path: Path, monkeypatch):
    """A trace write blowing up must not fail the turn (best-effort
    capture, both sites)."""
    from sweave.runtime.trace_log import TraceLog

    runtime = _make_runtime()

    async def fake_send(self, body=None, trace=None, **kwargs):
        return "wire	ok"

    runtime._send_message = fake_send  # type: ignore[assignment]

    class _ExplodingTrace:
        def append(self, event, payload=None):
            if event == "wire_prompt":
                raise RuntimeError("disk full")
            return None

    spec = _make_specialist(templated=True)
    out = await runtime.run(
        specialist=spec,
        delegation=_make_delegation(),
        worktree_path=tmp_path,
        message="hi",
        trace=_ExplodingTrace(),  # type: ignore[arg-type]
    )
    assert out == "wire	ok"
