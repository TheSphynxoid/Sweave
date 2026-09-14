"""Orchestrator mailbox rule (specialist notices resolve as seen).

A specialist's ``escalate`` record (kind ``escalation``, audience
``orchestrator``) was acknowledged only by humans — the record stayed
pending with ``needs_attention`` until a human acked mail that was
never addressed to them, while the orchestrator (the actual
audience) only ever saw it second-hand at synthesis. Now the chat
loop resolves consumed notices as ``seen`` after the synthesis turn
that incorporated them; questions and permission asks are never
touched (human decisions), and failed synthesis leaves notices
pending for the human fallback.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from sweave.runtime.escalation import EscalationStore


@pytest.fixture(autouse=True, scope="module")
def _mock_opencode_env():
    import os
    old = os.environ.get("SWEAVE_MOCK_OPENCODE")
    os.environ["SWEAVE_MOCK_OPENCODE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SWEAVE_MOCK_OPENCODE", None)
        else:
            os.environ["SWEAVE_MOCK_OPENCODE"] = old


def _store(tmp_path: Path, flagger=None) -> EscalationStore:
    return EscalationStore(
        base_dir=tmp_path / "esc",
        timeout_seconds=None,
        event_bus=None,
        delegation_flagger=flagger,
    )


def test_mark_seen_resolves_pending_notice(tmp_path: Path):
    calls: list = []
    events: list = []

    class _Bus:
        async def publish(self, event, data):
            events.append((event, data))

    store = EscalationStore(
        base_dir=tmp_path / "esc",
        timeout_seconds=None,
        event_bus=_Bus(),
        delegation_flagger=lambda did, v: calls.append((did, v)),
    )

    async def _run():
        rec = await store.create(
            delegation_id="d1", question="blocked on X",
            kind="escalation", audience="orchestrator",
        )
        assert rec["status"] == "pending"
        out = await store.mark_seen(delegation_id="d1")
        assert out is not None and out["status"] == "seen"
        assert "orchestrator" in (out["response"] or "")
        assert await store.get(delegation_id="d1") == out
        return out

    asyncio.run(_run())
    assert ("d1", False) in calls
    resolved = [d for e, d in events if e == "specialist.escalation_resolved"]
    assert len(resolved) == 1 and resolved[0]["status"] == "seen"


def test_mark_seen_noop_paths(tmp_path: Path):
    store = _store(tmp_path)

    async def _run():
        assert await store.mark_seen(delegation_id="missing") is None
        await store.create(delegation_id="d2", question="q?")
        await store.answer(delegation_id="d2", response="yes")
        # Already resolved: returned as-is, never re-marked.
        out = await store.mark_seen(delegation_id="d2")
        assert out is not None and out["status"] == "answered"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Loop end-to-end: synthesis consumes notices, spares decisions
# ---------------------------------------------------------------------------


def _build_loop(pm, stores, esc, workdir: Path, calls: dict):
    from sweave.chat.loop import ChatLoop
    from sweave.runtime.serve_runner import ServeRunnerRegistry
    from sweave.runtime.specialist_runtime import SpecialistRuntime
    from sweave.runtime.specialist_store import Specialist
    from sweave.runtime.delegation_store import Delegation

    runners = ServeRunnerRegistry()
    runtime = SpecialistRuntime(runners=runners)

    async def fake_send(self, body=None, trace=None, on_chunk=None,
                        on_reasoning=None, on_tool=None, **kwargs):
        calls["n"] = calls.get("n", 0) + 1
        text = ""
        try:
            text = (body.get("parts") or [{}])[0].get("text", "")
        except Exception:  # noqa: BLE001
            text = ""
        match = re.search(r"caller_delegation_id=([A-Za-z0-9_-]+)", text)
        calls["bodies"] = [*calls.get("bodies", []), text]
        if calls["n"] == 1 and match:
            chat_id = match.group(1)
            store = await stores.for_project(workdir)
            for agent, kind, audience, question in [
                ("backend", "escalation", "orchestrator", "blocked on X"),
                ("reviewer", "permission", "human", "allow /tmp?"),
            ]:
                # blocking=True: like real chat-turn defers (omitted
                # `blocking` joins by default), so the children join
                # synthesis instead of the fire-and-forget lane.
                child = Delegation(
                    agent=agent, task=f"{agent} work", project_name="demo",
                    parent_task_id=chat_id, status="done", blocking=True,
                )
                await store.add(child)
                await esc.create(
                    delegation_id=child.delegation_id, question=question,
                    options=["allow once", "deny"] if kind == "permission" else None,
                    kind=kind, audience=audience,
                )
                calls.setdefault("children", []).append(child.delegation_id)
            return "first turn narration"
        return "final synthesis"

    runtime._send_message = fake_send  # type: ignore[assignment]

    factories = {
        "orchestrator": Specialist(
            name="orchestrator", scope="project", is_orchestrator=True,
            system_prompt="seed", harness="opencode", current_model=None,
        )
    }
    return ChatLoop(
        project_manager=pm,
        specialist_runtime=runtime,
        specialist_factory=lambda n, project=None: factories.get(n),
        project_dir_resolver=lambda name: workdir,
        delegation_stores=stores,
        event_bus=None,
        turn_timeout=10.0,
        model_resolver=lambda agent, project=None: "deepseek-flash",
        escalation_store=esc,
    )


@pytest.mark.asyncio
async def test_synthesis_marks_notices_seen_spares_permission(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from sweave.projects import ProjectManager
    from sweave.runtime.delegation_store import PerProjectDelegationStores
    from sweave.runtime.trace_log import read_trace

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    stores = PerProjectDelegationStores()
    esc = _store(tmp_path)
    calls: dict[str, Any] = {}
    chat = _build_loop(pm, stores, esc, tmp_path, calls)

    result = await chat.run_turn(session_id=session.id, user_content="go")
    assert result["content"] == "final synthesis"
    # The synthesis body incorporated the notice text.
    assert "blocked on X" in calls["bodies"][-1]

    notice_id, perm_id = calls["children"]
    notice = await esc.get(delegation_id=notice_id)
    assert notice is not None and notice["status"] == "seen"
    perm = await esc.get(delegation_id=perm_id)
    assert perm is not None and perm["status"] == "pending"

    events = read_trace(result["metadata"]["delegation_id"])
    auto = [e for e in events if e.get("event") == "escalation_auto_seen"]
    assert len(auto) == 1 and auto[0]["delegation_ids"] == [notice_id]


@pytest.mark.asyncio
async def test_failed_synthesis_keeps_notices_pending(
    monkeypatch, tmp_path: Path
):
    """A failed synthesis never consumes mail — the human fallback
    keeps the pending notice (and its lanes)."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from sweave.projects import ProjectManager
    from sweave.runtime.delegation_store import PerProjectDelegationStores

    pm = ProjectManager(base_path=tmp_path / "projects")
    pm.create_project("demo", path=tmp_path)
    session = pm.create_session("demo", session_name="s1")
    stores = PerProjectDelegationStores()
    esc = _store(tmp_path)
    calls: dict[str, Any] = {}
    chat = _build_loop(pm, stores, esc, tmp_path, calls)

    # Poison the synthesis (second) turn only.
    orig = chat._run_orchestrator_turn

    async def _flaky(**kwargs: Any) -> str:
        calls["turns"] = calls.get("turns", 0) + 1
        if calls["turns"] >= 2:
            return "[chat error: boom]"
        return await orig(**kwargs)

    chat._run_orchestrator_turn = _flaky  # type: ignore[assignment]
    result = await chat.run_turn(session_id=session.id, user_content="go")
    assert "boom" in result["content"]
    notice = await esc.get(delegation_id=calls["children"][0])
    assert notice is not None and notice["status"] == "pending"
