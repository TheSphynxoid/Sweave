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
    return proc, TraceLog("d-hang", base_dir=tmp_path / "traces")


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
    callers + pinned assertions are unaffected) — plus the abort
    outcome suffix (this fake has no abort channel, so UNCONFIRMED)."""
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
        "running server-side; retry starts a fresh session"
        "; stop UNCONFIRMED — orphaned run possible (no abort channel))]"
    )
    events = [e["event"] for e in _trace_events("d-hang", tmp_path)]
    assert "abort_skipped" in events


@pytest.mark.asyncio
async def test_stall_attempts_abort_acknowledged(tmp_path: Path):
    """The stall trip POSTs /session/{id}/abort; a 2xx names the
    acknowledged stop in the message (paradox resolved)."""
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    posted: list[str] = []

    class _AbortClient(_HangingStreamClient):
        async def post(self, url: str, **kwargs):
            posted.append(url)

            class _Resp:
                status_code = 200

            return _Resp()

    from sweave.runtime.trace_log import TraceLog

    class _Proc:
        _session_id = "ses_abortme"
        _client = _AbortClient()

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    trace = TraceLog("d-abort", base_dir=tmp_path)
    out = await runtime._send_message(
        _Proc(), {"parts": [{"type": "text", "text": "hi"}]}, trace,
        stall_seconds=0.05,
    )
    assert posted == ["/session/ses_abortme/abort"]
    assert "serve acknowledged stop" in out, out
    from sweave.runtime.trace_log import read_trace

    events = {e["event"]: e for e in read_trace("d-abort", base_dir=tmp_path)}
    assert events["abort_sent"]["acknowledged"] is True


@pytest.mark.asyncio
async def test_stall_abort_rejection_stays_loud(tmp_path: Path):
    """A rejected/failed abort is UNCONFIRMED in the message — never
    silent (the orphaned-run case stays visible)."""
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.trace_log import TraceLog

    class _RefusingClient(_HangingStreamClient):
        async def post(self, url: str, **kwargs):
            raise RuntimeError("connection reset")

    class _Proc:
        _session_id = "ses_nope"
        _client = _RefusingClient()

    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    trace = TraceLog("d-abortfail", base_dir=tmp_path)
    out = await runtime._send_message(
        _Proc(), {"parts": [{"type": "text", "text": "hi"}]}, trace,
        stall_seconds=0.05,
    )
    assert "stop UNCONFIRMED" in out, out
    from sweave.runtime.trace_log import read_trace

    events = [e["event"] for e in read_trace("d-abortfail", base_dir=tmp_path)]
    assert "abort_failed" in events


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
    assert rec.review_bundle is None
    assert rec.schema_version == SCHEMA_VERSION
    assert SCHEMA_VERSION == 10


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


# ---------------------------------------------------------------------------
# M2.1 follow-up §A step 1: review-entry attention.
#
# Entering ``review`` sets ``needs_attention=True`` (traced with source);
# ``promote`` clears it. ``answer``/``skip`` clear only when no other
# attention source remains (an unpromoted review still owes promotion;
# a pending question still owes an answer). Step-0 consumer audit:
# escalate/answer/skip/promote endpoints (routers/delegations.py),
# EscalationStore docstring (runtime/escalation.py), the M1.12
# server.py flagger (unchanged — question arrival still sets), and the
# UI readers (TurnDelegations badge + ChildEscalationPreview 404-hide,
# DetailView EscalationSection 404-hide, LiveTree ring, plan board bugs
# lane) — all 404-safe today, so no wrong answer buttons appear on
# promotions; R4-thread adds the review hint inline (contracts only).
# ---------------------------------------------------------------------------


def _stub_job_runner(stores, succeed: bool, project_dir: Path):
    """JobRunner with a fake delegate tool (self-contained mirror of
    the step-4 suite's seam — deliberately not imported)."""
    from sweave.runtime.job_runner import JobRunner
    from sweave.tools import DelegationResult

    class _StubDelegateTool:
        async def execute(self, agent, task, model=None, task_id=None):
            if succeed:
                return DelegationResult(
                    success=True, agent=agent, task_id=task_id or "stub",
                    output="did the work", error=None,
                )
            return DelegationResult(
                success=False, agent=agent, task_id=task_id or "stub",
                output="", error="boom",
            )

    return JobRunner(
        delegate_tool=_StubDelegateTool(),  # type: ignore[arg-type]
        delegation_stores=stores,
        project_dir_resolver=lambda name: project_dir,
        turn_timeout=10.0,
    )


def _run_trace_events(project_dir: Path, delegation_id: str) -> list[dict]:
    import json

    from sweave.runtime.trace_log import TraceLog

    path = TraceLog(delegation_id, base_dir=project_dir).path
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_review_entry_sets_needs_attention_with_source(tmp_path: Path):
    """The success branch lands in review WITH the attention flag set
    and a sourced trace event (the §A ruling: review awaits human
    promotion, so it must join the attention surfaces)."""
    import tempfile

    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-attn-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    runner = _stub_job_runner(stores, succeed=True, project_dir=project_dir)

    d = Delegation(agent="backend", task="t", project_name="p")
    await store.add(d)
    await runner._run(d, TraceLog(d.delegation_id, base_dir=project_dir))

    rec = store.get(d.delegation_id)
    assert rec is not None and rec.status == "review"
    assert rec.needs_attention is True
    flags = [
        e for e in _run_trace_events(project_dir, d.delegation_id)
        if e.get("event") == "attention_flag"
    ]
    assert len(flags) == 1
    assert flags[0]["value"] is True
    assert flags[0]["source"] == "review_entry"


@pytest.mark.asyncio
async def test_failed_run_leaves_attention_flag_clear(tmp_path: Path):
    """The failure branch attaches no flag and no attention event
    (failed work owes no promotion; a pending question's flag — set
    by the ask path, not the runner — is untouched)."""
    import tempfile

    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.runtime.trace_log import TraceLog

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-attn-fail-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    runner = _stub_job_runner(stores, succeed=False, project_dir=project_dir)

    d = Delegation(agent="backend", task="t", project_name="p")
    await store.add(d)
    await runner._run(d, TraceLog(d.delegation_id, base_dir=project_dir))

    rec = store.get(d.delegation_id)
    assert rec is not None and rec.status == "failed"
    assert rec.needs_attention is False
    assert not [
        e for e in _run_trace_events(project_dir, d.delegation_id)
        if e.get("event") == "attention_flag"
    ]


def _endpoint_state(tmp_path: Path, stores, project_dir: Path):
    """Minimal router state: real stores, no app (direct coroutine
    calls — deterministic, no background runner)."""
    import types

    from sweave.runtime.escalation import EscalationStore

    return types.SimpleNamespace(
        delegation_stores=stores,
        escalation_store=EscalationStore(base_dir=tmp_path / "esc"),
        event_bus=None,
        traces_dir=tmp_path / "traces",
    )


@pytest.mark.asyncio
async def test_answer_keeps_flag_while_review_owed(tmp_path: Path):
    """Answering the question on a review-owed delegation resolves
    the question but keeps the flag (promotion still owed)."""
    import tempfile

    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.web.routers.delegations import (
        AnswerRequest,
        answer_delegation,
    )

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-attn-ans-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    state = _endpoint_state(tmp_path, stores, project_dir)

    d = Delegation(agent="backend", task="t", project_name="p",
                   status="review", needs_attention=True)
    await store.add(d)
    await state.escalation_store.create(
        delegation_id=d.delegation_id, question="q?",
        options=None, kind="question", audience="human",
    )

    rec = await answer_delegation(d.delegation_id, AnswerRequest(response="yes"),
                                  state)  # type: ignore[arg-type]
    assert rec["status"] == "answered"
    assert store.get(d.delegation_id).needs_attention is True


@pytest.mark.asyncio
async def test_answer_clears_flag_when_no_review_owed(tmp_path: Path):
    """The pre-follow-up behavior is preserved: answering the only
    attention source clears the flag."""
    import tempfile

    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.web.routers.delegations import (
        AnswerRequest,
        answer_delegation,
    )

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-attn-ans2-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    state = _endpoint_state(tmp_path, stores, project_dir)

    d = Delegation(agent="backend", task="t", project_name="p",
                   status="running", needs_attention=True)
    await store.add(d)
    await state.escalation_store.create(
        delegation_id=d.delegation_id, question="q?",
        options=None, kind="question", audience="human",
    )

    await answer_delegation(d.delegation_id, AnswerRequest(response="yes"),
                            state)  # type: ignore[arg-type]
    assert store.get(d.delegation_id).needs_attention is False


@pytest.mark.asyncio
async def test_skip_keeps_flag_while_review_owed(tmp_path: Path):
    """Skip resolves the question; the unpromoted review keeps the flag."""
    import tempfile

    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.web.routers.delegations import (
        SkipRequest,
        skip_delegation,
    )

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-attn-skip-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    state = _endpoint_state(tmp_path, stores, project_dir)

    d = Delegation(agent="backend", task="t", project_name="p",
                   status="review", needs_attention=True)
    await store.add(d)
    await state.escalation_store.create(
        delegation_id=d.delegation_id, question="q?",
        options=None, kind="question", audience="human",
    )

    rec = await skip_delegation(d.delegation_id, SkipRequest(confirmed=True),
                                state)  # type: ignore[arg-type]
    assert rec["status"] == "skipped"
    assert store.get(d.delegation_id).needs_attention is True


@pytest.mark.asyncio
async def test_promote_clears_flag_without_pending_question(tmp_path: Path):
    """Promote flips review → done, clears the flag (traced), and
    keeps the review_request as history (ruling 3 intact)."""
    import tempfile

    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.web.routers.delegations import promote_delegation

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-attn-prom-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    state = _endpoint_state(tmp_path, stores, project_dir)

    d = Delegation(agent="backend", task="t", project_name="p",
                   status="review", needs_attention=True,
                   review_request={"reviewer_hint": "reviewer",
                                   "diff_ref": None,
                                   "manifest_summary": None,
                                   "confidence": None,
                                   "requested_at": "2026-09-12T00:00:00"})
    await store.add(d)

    done = await promote_delegation(d.delegation_id, state)  # type: ignore[arg-type]
    assert done["status"] == "done"
    assert done["review_request"] is not None
    assert store.get(d.delegation_id).needs_attention is False
    flags = [
        e for e in _run_trace_events(state.traces_dir, d.delegation_id)
        if e.get("event") == "attention_flag"
    ]
    assert len(flags) == 1
    assert flags[0]["value"] is False
    assert flags[0]["source"] == "human_promote"


@pytest.mark.asyncio
async def test_promote_keeps_flag_with_pending_question(tmp_path: Path):
    """Promoting a review that still has a pending question resolves
    the review but keeps the flag (the answer is still owed)."""
    import tempfile

    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.web.routers.delegations import promote_delegation

    project_dir = Path(tempfile.mkdtemp(prefix="sweave-m21-attn-prom2-"))
    stores = PerProjectDelegationStores()
    store = await stores.for_project(project_dir)
    state = _endpoint_state(tmp_path, stores, project_dir)

    d = Delegation(agent="backend", task="t", project_name="p",
                   status="review", needs_attention=True)
    await store.add(d)
    await state.escalation_store.create(
        delegation_id=d.delegation_id, question="q?",
        options=None, kind="question", audience="human",
    )

    done = await promote_delegation(d.delegation_id, state)  # type: ignore[arg-type]
    assert done["status"] == "done"
    assert store.get(d.delegation_id).needs_attention is True


# ---------------------------------------------------------------------------
# M2.1 follow-up §A step 3 (backend half): synthesis handoff note.
#
# When the wait-set join is empty but fire-and-forget children are
# still running, the synthesis turn must carry a server-built handoff
# note naming them — otherwise the orchestrator sees an empty result
# set that reads as a stall (and, pre-prompt-rule, promised
# follow-ups the machinery cannot keep). Step-2 audit note: the
# late-settle WS pulse already re-renders TurnDelegations rows online
# (pulse + refetch, no payload read), so step 2's remainder is pure
# R4 UI (row state + inline review hint) — no backend change here.
# ---------------------------------------------------------------------------


def test_handoff_note_none_without_skipped():
    """No fire-and-forget children → no note (leaf fast path unchanged)."""
    from sweave.chat.synthesis import fire_and_forget_handoff

    assert fire_and_forget_handoff([]) is None


def test_handoff_note_none_when_skipped_all_settled():
    """Skipped children that already settled need no handoff — they
    sit in the Children lane with their results; nothing is running."""
    from sweave.chat.synthesis import fire_and_forget_handoff
    from sweave.runtime.delegation_store import Delegation

    skipped = [
        Delegation(agent="w1", task="a", status="done"),
        Delegation(agent="w2", task="b", status="review"),
        Delegation(agent="w3", task="c", status="failed"),
    ]
    assert fire_and_forget_handoff(skipped) is None


def test_handoff_note_names_running_children():
    """Running fire-and-forget children are named (agent + task) with
    the settle-time delivery contract and the no-promises rule."""
    from sweave.chat.synthesis import fire_and_forget_handoff
    from sweave.runtime.delegation_store import Delegation

    skipped = [
        Delegation(agent="worker", task="build the widget",
                   status="running"),
        Delegation(agent="scout", task="probe the api",
                   status="queued"),
        Delegation(agent="old", task="finished work", status="done"),
    ]
    note = fire_and_forget_handoff(skipped)
    assert note is not None
    assert "worker" in note and "build the widget" in note
    assert "scout" in note and "probe the api" in note
    assert "old" not in note
    assert "Children" in note
    assert "do not promise" in note


def test_handoff_note_truncates_long_tasks():
    """Task snippet rule: 140 chars (the TurnDelegations snippet
    precedent), pinned with an ellipsis marker."""
    from sweave.chat.synthesis import fire_and_forget_handoff
    from sweave.runtime.delegation_store import Delegation

    long_task = "x" * 300
    note = fire_and_forget_handoff(
        [Delegation(agent="w", task=long_task, status="running")]
    )
    assert note is not None
    assert "x" * 300 not in note
    assert "…" in note


def _handoff_chat_loop(pm, stores, runtime, capture: list):
    """ChatLoop wiring for the handoff tests (self-contained mirror
    of the M1.7 pipeline seam — deliberately not imported)."""
    from sweave.chat.loop import ChatLoop

    factories = {
        "orchestrator": _handoff_orchestrator(),
    }

    def resolver(name):
        if name is None:
            return None
        proj = pm.get_project(name)
        return proj.path if proj else None

    return ChatLoop(
        project_manager=pm,
        specialist_runtime=runtime,
        specialist_factory=lambda agent_name: factories.get(agent_name),
        project_dir_resolver=resolver,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=10.0,
        model_resolver=lambda agent: "deepseek-flash",
    )


def _handoff_orchestrator():
    from sweave.runtime.specialist_store import Specialist

    return Specialist(
        name="orchestrator",
        scope="project",
        is_orchestrator=True,
        system_prompt="seed",
        harness="opencode",
        current_model=None,
    )


@pytest.mark.asyncio
async def test_synthesis_carries_handoff_for_running_fire_and_forget(
    tmp_path: Path,
):
    """Wiring: a running fire-and-forget child present at scan time
    (empty join set) lands a handoff note in the synthesis-turn
    message the orchestrator sees."""
    import re

    from sweave.projects import ProjectManager
    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    session = pm.create_session("demo", session_name="s1")
    messages: list[str] = []

    async def fake_send(self, body=None, trace=None, on_chunk=None,
                        on_reasoning=None, **kwargs):
        text = body["parts"][0]["text"] if body else ""
        messages.append(text)
        if len(messages) == 1:
            # First turn: the orchestrator "defers" a fire-and-forget
            # child — injected directly (the scan reads the store).
            m = re.search(r"caller_delegation_id=([^\]\s]+)", text)
            assert m is not None
            await store.add(Delegation(
                agent="worker", task="build the widget",
                project_name="demo", parent_task_id=m.group(1),
                status="running",
            ))
            return "kicked off background work"
        return "turn closed on what is known"

    runtime._send_message = fake_send  # type: ignore[assignment]
    chat = _handoff_chat_loop(pm, stores, runtime, messages)

    result = await chat.run_turn(session_id=session.id, user_content="go")
    assert result["role"] == "assistant"
    assert len(messages) == 2, messages
    synth = messages[1]
    assert "worker" in synth and "build the widget" in synth
    assert "Children" in synth


@pytest.mark.asyncio
async def test_synthesis_omits_handoff_when_fire_and_forget_settled(
    tmp_path: Path,
):
    """Control: a settled fire-and-forget child (empty join set)
    produces no handoff note — nothing is running."""
    import re

    from sweave.projects import ProjectManager
    from sweave.runtime.delegation_store import (
        Delegation,
        PerProjectDelegationStores,
    )
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    stores = PerProjectDelegationStores()
    store = await stores.for_project(tmp_path)
    runtime = SpecialistRuntime(runners=ServeRunnerRegistry())
    session = pm.create_session("demo", session_name="s1")
    messages: list[str] = []

    async def fake_send(self, body=None, trace=None, on_chunk=None,
                        on_reasoning=None, **kwargs):
        text = body["parts"][0]["text"] if body else ""
        messages.append(text)
        if len(messages) == 1:
            m = re.search(r"caller_delegation_id=([^\]\s]+)", text)
            assert m is not None
            await store.add(Delegation(
                agent="worker", task="already done",
                project_name="demo", parent_task_id=m.group(1),
                status="done",
            ))
            return "kicked off background work"
        return "turn closed"

    runtime._send_message = fake_send  # type: ignore[assignment]
    chat = _handoff_chat_loop(pm, stores, runtime, messages)

    await chat.run_turn(session_id=session.id, user_content="go")
    assert len(messages) == 2, messages
    assert "Fire-and-forget" not in messages[1]
