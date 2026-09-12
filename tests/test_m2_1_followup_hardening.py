"""M2.1 follow-up hardening (incident Sweave-20260911-213619-096e65).

Pins, test-first:

* The templated system-prompt send runs under the stall bound. Live
  evidence: a 16m40s invisible hang — ``run()`` used the harness
  ``process.send`` (httpx 1000s only, result ignored) while the
  watchdog only wrapped the main send.
* Stall errors carry turn age when the caller passes ``t0`` (the
  "stalled after 300s" message for a 21-minute hang). Exact legacy
  strings are preserved when ``t0`` is absent.
* Per-delegation ``engine_session_id`` (schema v9): migration
  default, set on run, echoed in the detail fold.
* The orchestrator prompt documents ``blocking``/``estimate`` plus
  the follow-up rules (no promises for fire-and-forget; previous
  turns' children are checked, not assumed).
"""

from __future__ import annotations

import asyncio
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


def _make_runtime(monkeypatch, tmp_path: Path):
    """Real runtime on a mock serve; task sends faked.

    Mirrors tests/test_prompt_template.py::_make_runtime (the proven
    run-level harness): the system-prompt send goes through the real
    ``OpenCodeProcess.send`` seam so the stall bound is exercised.
    """
    from sweave.harness.opencode import OpenCodeProcess
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())

    async def fake_send_message(self, body=None, trace=None, on_chunk=None,
                                on_reasoning=None, **kwargs):
        return "task-output"

    runtime._send_message = fake_send_message  # type: ignore[assignment]
    monkeypatch.setattr(OpenCodeProcess, "send", _passthrough_send)
    return runtime


async def _passthrough_send(self, message, on_chunk=None, trace=None,
                            trace_reasoning=False):
    """Default system-send double: instant success (replaces per-test)."""
    from sweave.harness.base import AgentResult

    return AgentResult(success=True, output="", metadata={})


def _spec(name: str, prompt: str):
    from sweave.runtime.specialist_store import Specialist

    return Specialist(name=name, scope="project", is_orchestrator=False,
                      system_prompt=prompt, harness="opencode")


def _delegation(agent: str, task: str):
    from sweave.runtime.delegation_store import Delegation

    return Delegation(agent=agent, task=task, project_name="shop")


def _trace_for(delegation_id: str, tmp_path: Path):
    from sweave.runtime.trace_log import TraceLog

    return TraceLog(delegation_id, base_dir=tmp_path / "traces")


def _trace_events(delegation_id: str, tmp_path: Path) -> list[dict]:
    from sweave.runtime.trace_log import read_trace

    return read_trace(delegation_id, base_dir=tmp_path / "traces")


@pytest.mark.asyncio
async def test_template_send_hang_fails_fast_with_phase(monkeypatch, tmp_path: Path):
    """A hung templated system-prompt send trips the stall bound fast.

    Live shape: 16m40s of silence on the harness send path while the
    watchdog watched only the main send. The bound must cover the
    system send too, traced as phase=system_prompt.
    """
    import sweave.runtime.specialist_runtime as rt
    from sweave.harness.opencode import OpenCodeProcess

    monkeypatch.setattr(rt, "STALL_TIMEOUT_SECONDS", 0.05)
    runtime = _make_runtime(monkeypatch, tmp_path)

    async def hanging_send(self, message, on_chunk=None, trace=None,
                           trace_reasoning=False):
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    monkeypatch.setattr(OpenCodeProcess, "send", hanging_send)

    spec = _spec("templ", "Work in {{worktree_path}}.")
    d = _delegation("templ", "do it")
    trace = _trace_for(d.delegation_id, tmp_path)
    out = await asyncio.wait_for(
        runtime.run(specialist=spec, delegation=d, worktree_path=tmp_path,
                    message=d.task, trace=trace),
        timeout=10.0,
    )
    trace.close()
    assert out.startswith("[chat error:"), out
    assert "system-prompt" in out, out
    events = _trace_events(d.delegation_id, tmp_path)
    stalled = [e for e in events if e.get("event") == "stalled"]
    assert stalled and stalled[0].get("phase") == "system_prompt"


@pytest.mark.asyncio
async def test_template_send_failure_is_loud(monkeypatch, tmp_path: Path):
    """A failed system-prompt send (e.g. swallowed ReadTimeout) fails
    the turn loudly instead of proceeding without an identity prompt."""
    from sweave.harness.base import AgentResult
    from sweave.harness.opencode import OpenCodeProcess

    runtime = _make_runtime(monkeypatch, tmp_path)

    async def failing_send(self, message, on_chunk=None, trace=None,
                           trace_reasoning=False):
        return AgentResult(success=False, output="", error="ReadTimeout: boom")

    monkeypatch.setattr(OpenCodeProcess, "send", failing_send)

    spec = _spec("templ", "Work in {{worktree_path}}.")
    d = _delegation("templ", "do it")
    trace = _trace_for(d.delegation_id, tmp_path)
    out = await asyncio.wait_for(
        runtime.run(specialist=spec, delegation=d, worktree_path=tmp_path,
                    message=d.task, trace=trace),
        timeout=10.0,
    )
    trace.close()
    assert out.startswith("[chat error:"), out
    assert "boom" in out, out


class _HangingStreamClient:
    """Stream CM whose open never completes (header hang)."""

    def stream(self, method: str, url: str, **kwargs: Any) -> Any:
        class _Resp:
            async def __aenter__(self) -> Any:
                await asyncio.sleep(3600)
                raise AssertionError("unreachable")

            async def __aexit__(self, *args: Any) -> bool:
                return False

        return _Resp()


def _hanging_proc(tmp_path: Path):
    """Mock process whose /message open never completes (header hang).

    Local mirror of the MockOpenCodeProcess shape in
    test_specialist_runtime.py (deliberately not imported: the seam
    is three attributes wide and coupling test files rots).
    """

    class _LocalMockProcess:
        def __init__(self, client, session_id="ses_hang"):
            self._client = client
            self._session_id = session_id

    from sweave.runtime.trace_log import TraceLog

    proc = _LocalMockProcess(client=_HangingStreamClient())
    return proc, TraceLog("d-hang", base_dir=tmp_path)


@pytest.mark.asyncio
async def test_stall_message_carries_turn_age(tmp_path: Path):
    """With t0 passed, the header-trip names both windows: the 300s of
    silence AND the total turn age (the 21-minute hang reported as
    "stalled after 300s")."""
    import asyncio as _aio

    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    proc, trace = _hanging_proc(tmp_path)
    t0 = _aio.get_running_loop().time() - 1000.0
    out = await runtime._send_message(
        proc, {"parts": [{"type": "text", "text": "hi"}]}, trace,
        stall_seconds=0.05, t0=t0,
    )
    assert "turn age 1000s" in out, out
    assert out.startswith("[chat error: stalled after 0s"), out


@pytest.mark.asyncio
async def test_stall_message_unchanged_without_t0(tmp_path: Path):
    """Without t0 the legacy exact string is preserved (existing
    callers + pinned assertions are unaffected)."""
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    proc, trace = _hanging_proc(tmp_path)
    out = await runtime._send_message(
        proc, {"parts": [{"type": "text", "text": "hi"}]}, trace,
        stall_seconds=0.05,
    )
    assert out == (
        "[chat error: stalled after 0s without data "
        "(response headers never arrived; the turn may still be "
        "running server-side; retry starts a fresh session)]"
    )


def test_engine_session_id_migrates_to_none():
    """A v8 record (pre follow-up) loads with engine_session_id=None
    and stamps v9."""
    from sweave.runtime.delegation_store import SCHEMA_VERSION, Delegation

    v8_record = {
        "schema_version": 8,
        "delegation_id": "del-v8",
        "task_id": "t8",
        "agent": "backend",
        "model": "",
        "task": "x",
        "status": "done",
        "created_at": "2026-09-12T00:00:00",
        "updated_at": "2026-09-12T00:00:00",
        "completed_at": "2026-09-12T00:00:01",
        "kind": "task",
    }
    rec = Delegation.from_dict(v8_record)
    assert rec.engine_session_id is None
    assert rec.schema_version == SCHEMA_VERSION
    assert SCHEMA_VERSION == 9


@pytest.mark.asyncio
async def test_engine_session_id_set_on_run(monkeypatch, tmp_path: Path):
    """After a mocked run, the delegation carries the engine session
    id that ran it (display + future forensics without trace-digging)."""
    runtime = _make_runtime(monkeypatch, tmp_path)
    spec = _spec("plain", "You are a plain specialist.")
    d = _delegation("plain", "do it")
    assert d.engine_session_id is None
    trace = _trace_for(d.delegation_id, tmp_path)
    out = await asyncio.wait_for(
        runtime.run(specialist=spec, delegation=d, worktree_path=tmp_path,
                    message=d.task, trace=trace),
        timeout=10.0,
    )
    trace.close()
    assert out == "task-output"
    assert (d.engine_session_id or "").startswith("ses_"), d.engine_session_id


def test_detail_fold_echoes_engine_session_id(tmp_path: Path):
    """The detail projection carries the engine session (None-safe)."""
    from sweave.web.detail_view import render_detail_view

    out = render_detail_view("d1", trace_dir=tmp_path,
                             engine_session_id="ses_abc123")
    assert out["engine_session_id"] == "ses_abc123"
    out2 = render_detail_view("d1", trace_dir=tmp_path)
    assert out2["engine_session_id"] is None


def test_orchestrator_prompt_documents_blocking_followup_rules():
    """The defer contract must document blocking/estimate plus the
    follow-up rules (no promises for fire-and-forget; previous turns'
    children are checked, not assumed). The [22]/[23] incident was a
    charter gap as much as a runtime gap."""
    from pathlib import Path as P

    prompt = (P(__file__).parent.parent / "sweave" / "agents"
              / "orchestrator" / "config.yaml").read_text(encoding="utf-8")
    for phrase in (
        "blocking",
        "estimate",
        "follow-up",
        "Children",
    ):
        assert phrase in prompt, f"orchestrator prompt missing: {phrase!r}"
