"""Escalation single-slot guards (hygiene B4).

One record per delegation: a pending ask of ANY family owns the slot.
Before this batch, the pulsed-rerun filer overwrote unconditionally,
the soft-limit filer only respected soft records (a permission ask
was destroyed), and the permission bridge overwrote any pending
stranger — with the answer then steering the wrong ask, or a live
question answered into a dead turn.

Covers:
* bridge refuses an occupied slot (slot_occupied, record untouched,
  no serve POST);
* bridge never POSTs for a changed-hands slot (slot_changed);
* pulsed + soft filers return False on a pending permission ask;
* late answer/skip/seen/timeout carry additive resolved flags so a
  200 reads as "late no-op", never "steered".
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sweave.runtime.delegation_store import Delegation, PerProjectDelegationStores
from sweave.runtime.escalation import EscalationStore
from sweave.runtime.job_runner import JobRunner
from sweave.runtime.trace_log import TraceLog


class _StubTool:
    async def execute(self, agent, task, model=None, task_id=None):
        raise AssertionError("slot tests never touch the tool")


def _runner(tmp_path: Path, store) -> JobRunner:
    rt = SimpleNamespace(escalation_store=store)
    return JobRunner(
        delegate_tool=_StubTool(),
        delegation_stores=PerProjectDelegationStores(),
        specialist_runtime=rt,  # type: ignore[arg-type]
        turn_timeout=0.2,
    )


def _trace(tmp_path: Path, did: str = "d-slot") -> TraceLog:
    return TraceLog(did, base_dir=tmp_path)


class _FakeProjects:
    def __init__(self, projects: list) -> None:
        self._projects = projects

    def list_projects(self):
        return self._projects


class _FakeProject:
    def __init__(self, name: str, path: str, roots) -> None:
        self.name = name
        self.path = path
        self.permission_roots = roots


async def test_bridge_refuses_occupied_slot(tmp_path, monkeypatch):
    """Out-of-scope ask on an occupied slot: slot_occupied, no create, no POST."""
    from sweave.runtime import permission_bridge as pb

    proj = tmp_path / "proj"
    proj.mkdir(parents=True)
    pb.register_session("ses_slot", "http://127.0.0.1:4099", proj, "chat-slot")
    store = EscalationStore(base_dir=tmp_path / "esc", timeout_seconds=None)
    await store.create(
        delegation_id="chat-slot",
        question="Keep waiting or stop it?",
        options=["Keep waiting", "Stop it"],
        kind="question",
        audience="human",
        metadata={"soft_limit": True, "agent": "backend"},
    )
    posted: list = []

    async def fake_reply(client, base_url, session_id, request_id, value):
        posted.append((base_url, session_id, request_id, value))
        return 200

    # The bridge imports reply_permission_request locally at call
    # time, so patch the source module (same as the bridge tests).
    monkeypatch.setattr(
        "sweave.runtime.permission_watch.reply_permission_request", fake_reply
    )

    out = await pb.resolve_hijack_request(
        {
            "session_id": "ses_slot",
            "request_id": "per_new",
            "permission": "external_directory",
            "patterns": [str(tmp_path / "elsewhere" / "*")],
            "metadata": {},
        },
        escalation_store=store,
        project_manager=_FakeProjects([_FakeProject("proj", str(proj), None)]),
    )
    assert out["status"] == "error"
    assert out["reason"] == "slot_occupied"
    assert posted == []
    # The stranger's question survives untouched.
    rec = await store.get(delegation_id="chat-slot")
    assert rec is not None and rec["status"] == "pending"
    assert (rec.get("metadata") or {}).get("soft_limit") is True


async def test_bridge_slot_changed_never_posts(tmp_path, monkeypatch):
    """Record changed hands mid-wait: slot_changed, and no serve POST."""

    class _ScriptedStore:
        async def create_or_reuse(self, **kwargs):
            return {"status": "pending"}, True

        async def get(self, *, delegation_id: str):
            return {
                "status": "answered",
                "response": "allow once",
                "metadata": {"requestID": "per_OTHER"},
            }

    from sweave.runtime import permission_bridge as pb

    proj = tmp_path / "proj"
    proj.mkdir(parents=True)
    pb.register_session("ses_swap", "http://127.0.0.1:4100", proj, "chat-swap")
    posted: list = []

    async def fake_reply(client, base_url, session_id, request_id, value):
        posted.append((base_url, session_id, request_id, value))
        return 200

    # Same local-import patch point as above.
    monkeypatch.setattr(
        "sweave.runtime.permission_watch.reply_permission_request", fake_reply
    )

    out = await pb.resolve_hijack_request(
        {
            "session_id": "ses_swap",
            "request_id": "per_mine",
            "permission": "external_directory",
            "patterns": [str(tmp_path / "elsewhere" / "*")],
            "metadata": {},
        },
        escalation_store=_ScriptedStore(),
        project_manager=_FakeProjects([_FakeProject("proj", str(proj), None)]),
    )
    assert out["status"] == "error"
    assert out["reason"] == "slot_changed"
    assert posted == []


async def test_soft_filers_respect_permission_ask(tmp_path):
    """Pulsed + soft filers refuse to overwrite a pending permission ask."""
    store = EscalationStore(base_dir=tmp_path / "esc", timeout_seconds=None)
    runner = _runner(tmp_path, store)
    d = Delegation(agent="backend", task="slow work", model="")
    await store.create(
        delegation_id=d.delegation_id,
        question="Permission required: opencode asks ...",
        options=["allow once", "always allow", "deny"],
        kind="permission",
        audience="human",
        metadata={"requestID": "per_1", "permission": "external_directory"},
    )
    assert await runner._ask_soft_limit(d, _trace(tmp_path), budget=900.0) is False
    assert (
        await runner._ask_pulsed_rerun(
            d,
            _trace(tmp_path),
            failure_error="boom",
            last_pulse_age=3.0,
            last_pulse_desc="tool.completed",
        )
        is False
    )
    rec = await store.get(delegation_id=d.delegation_id)
    assert rec is not None and rec["status"] == "pending"
    assert rec["kind"] == "permission"


async def test_late_answers_carry_resolved_flags(tmp_path):
    """Second terminal call reads as late no-op, never as a steer."""
    store = EscalationStore(base_dir=tmp_path / "esc", timeout_seconds=None)
    first = await store.create(delegation_id="d-late", question="Q?")
    assert first["status"] == "pending"
    answered = await store.answer(delegation_id="d-late", response="yes")
    assert answered is not None and answered["resolved"] is True
    late = await store.answer(delegation_id="d-late", response="yes-again")
    assert late is not None and late["resolved"] is False
    assert late["resolved_reason"] == "already_answered"
    assert late["response"] == "yes"  # first answer stands

    await store.create(delegation_id="d-skip", question="Q?")
    await store.skip(delegation_id="d-skip")
    late_skip = await store.skip(delegation_id="d-skip")
    assert late_skip is not None and late_skip["resolved"] is False
    assert late_skip["resolved_reason"] == "already_skipped"

    await store.create(delegation_id="d-seen", question="Q?")
    await store.mark_seen(delegation_id="d-seen")
    late_seen = await store.mark_seen(delegation_id="d-seen")
    assert late_seen is not None and late_seen["resolved"] is False
    assert late_seen["resolved_reason"] == "already_seen"

    await store.create(delegation_id="d-to", question="Q?")
    await store.force_timeout(delegation_id="d-to")
    late_to = await store.force_timeout(delegation_id="d-to")
    assert late_to is not None and late_to["resolved"] is False
    assert late_to["resolved_reason"] == "already_timeout"
