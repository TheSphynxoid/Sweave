"""Thinking-capture tests: reasoning parts flow end-to-end.

Covers:
* ``SpecialistRuntime.run`` forwards reasoning parts to
  ``on_reasoning`` while the returned text output excludes them.
* Reasoning parts are traced (``reasoning`` events on the TraceLog).
* The mock emits a reasoning part under
  ``SWEAVE_MOCK_OPENCODE_REASONING=1`` and stays text-only without it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sweave.runtime.delegation_store import Delegation
from sweave.runtime.serve_runner import ServeRunnerRegistry
from sweave.runtime.specialist_runtime import SpecialistRuntime
from sweave.runtime.specialist_store import Specialist
from sweave.runtime.trace_log import TraceLog


# ---------------------------------------------------------------------------
# Hermeticity: same fixture as test_m1_4_5_step1_model_ref_contract.py
# (module-scoped, autouse). The tests below drive
# ``SpecialistRuntime.run`` end-to-end; without the env var the
# ServeRunner would try to spawn a real ``opencode serve`` subprocess.
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


def _make_runtime_case(tmp_path: Path, agent: str = "thinker"):
    worktree = tmp_path / "wt"
    worktree.mkdir(parents=True, exist_ok=True)
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    specialist = Specialist(
        name=agent,
        scope="global",
        is_orchestrator=True,
        system_prompt="",
        harness="opencode",
        current_model=None,
    )
    delegation = Delegation(agent=agent, task="think then answer", model="")
    trace = TraceLog(f"d-think-{agent}", base_dir=tmp_path)
    return runtime, specialist, delegation, worktree, trace


def _trace_events(trace: TraceLog) -> list[dict]:
    return [
        json.loads(line)
        for line in trace.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_runtime_forwards_reasoning_to_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """With a reasoning part on the wire, on_reasoning gets it and the
    returned text output excludes it."""
    monkeypatch.setenv("SWEAVE_MOCK_OPENCODE_REASONING", "1")
    runtime, specialist, delegation, worktree, trace = _make_runtime_case(tmp_path)

    chunks: list[str] = []
    reasonings: list[str] = []
    output = await runtime.run(
        specialist=specialist,
        delegation=delegation,
        worktree_path=worktree,
        message="hello",
        trace=trace,
        on_chunk=chunks.append,
        on_reasoning=reasonings.append,
    )
    assert reasonings == ["mock thinking for thinker"]
    assert "mock thinking" not in output
    assert "ACK from mock opencode" in output
    assert chunks == [output]


@pytest.mark.asyncio
async def test_reasoning_is_traced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Reasoning parts land on the TraceLog (the turn's audit trail)
    and output_text counts them separately from text chunks."""
    monkeypatch.setenv("SWEAVE_MOCK_OPENCODE_REASONING", "1")
    runtime, specialist, delegation, worktree, trace = _make_runtime_case(tmp_path)

    await runtime.run(
        specialist=specialist,
        delegation=delegation,
        worktree_path=worktree,
        message="hello",
        trace=trace,
    )
    events = _trace_events(trace)
    reasoning = [e for e in events if e.get("event") == "reasoning"]
    assert len(reasoning) == 1
    assert reasoning[0]["text"] == "mock thinking for thinker"
    output_text = [e for e in events if e.get("event") == "output_text"]
    assert len(output_text) == 1
    assert output_text[0]["reasoning_chunks"] == 1
    assert output_text[0]["chunks"] == 1


@pytest.mark.asyncio
async def test_no_reasoning_without_mock_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Default mock wire stays text-only: no reasoning callback, no
    reasoning trace events, no metadata to persist."""
    monkeypatch.delenv("SWEAVE_MOCK_OPENCODE_REASONING", raising=False)
    runtime, specialist, delegation, worktree, trace = _make_runtime_case(tmp_path)

    reasonings: list[str] = []
    output = await runtime.run(
        specialist=specialist,
        delegation=delegation,
        worktree_path=worktree,
        message="hello",
        trace=trace,
        on_reasoning=reasonings.append,
    )
    assert reasonings == []
    assert "ACK from mock opencode" in output
    events = _trace_events(trace)
    assert [e for e in events if e.get("event") == "reasoning"] == []
    output_text = [e for e in events if e.get("event") == "output_text"]
    assert output_text[0]["reasoning_chunks"] == 0
