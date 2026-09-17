"""TOOL_CARDS_PLAN §3 Step 3 tests: ask_human batch contract (backend).

Locked semantics (user 2026-09-16 F5 + 2026-09-17):
* ``questions`` additive (≤5) alongside legacy ``question``; legacy
  normalizes to a 1-elem batch server-side; >5 -> ``rejected:`` line
  (never crash) with IDENTICAL strings on MCP + engine.
* Record gains ``questions[]`` + ``answers[]``; single-question
  records project as 1-elem; pre-batch files read as 1-elem via
  ``get()`` projection.
* Answer accepts ``{answers: []}`` alongside ``{response}`` (legacy
  single path byte-identical). Locked: partial posts PERSIST
  without resolving (status stays pending) so per-question UI works;
  status flips ONLY when ALL answered (all-at-once).
* Skip is single + whole batch -> best judgment.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def home_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Store: record shape
# ---------------------------------------------------------------------------


def test_legacy_question_projects_as_one_elem_batch(home_dir):
    """Single-question create still works; record carries additive
    questions[]/answers[] as 1-elem (compat-single)."""
    from sweave.runtime.escalation import EscalationStore

    store = EscalationStore(base_dir=home_dir)
    rec = asyncio.run(
        store.create(
            delegation_id="d-1", question="Q?", options=["a", "b"]
        )
    )
    assert rec["questions"] == [{"question": "Q?", "options": ["a", "b"]}]
    assert rec["answers"] == []
    # legacy keys unchanged
    assert rec["question"] == "Q?"
    assert rec["options"] == ["a", "b"]
    assert rec["status"] == "pending"


def test_batch_create_stores_questions_additive(home_dir):
    from sweave.runtime.escalation import EscalationStore

    store = EscalationStore(base_dir=home_dir)
    rec = asyncio.run(
        store.create(
            delegation_id="d-2",
            question="",
            questions=[
                {"question": "Q1?"},
                {"question": "Q2?", "options": ["x"]},
            ],
        )
    )
    assert [q["question"] for q in rec["questions"]] == ["Q1?", "Q2?"]
    assert rec["questions"][1]["options"] == ["x"]
    assert len(rec["questions"]) == 2


def test_pre_batch_record_reads_as_one_elem(home_dir):
    """Legacy projection: a pre-batch FILE without questions[]/answers[]
    reads (via a fresh store) as a 1-elem batch."""
    from sweave.runtime.escalation import EscalationStore

    esc_dir = home_dir / "escalations"
    esc_dir.mkdir(parents=True)
    (esc_dir / "d-old.json").write_text(
        json.dumps(
            {
                "escalation_id": "esc-old",
                "delegation_id": "d-old",
                "question": "Old Q?",
                "options": None,
                "kind": "question",
                "audience": "human",
                "status": "pending",
                "created_at": "2026-01-01T00:00:00",
                "deadline_at": None,
                "answered_at": None,
                "response": None,
                "escalation_timeout_seconds": None,
                "metadata": None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    store = EscalationStore(base_dir=home_dir)
    rec = asyncio.run(store.get(delegation_id="d-old"))
    assert rec is not None
    assert rec["questions"] == [{"question": "Old Q?", "options": None}]
    assert rec["answers"] == []


# ---------------------------------------------------------------------------
# Answer semantics: all-at-once + partial persist (LOCKED)
# ---------------------------------------------------------------------------


def test_single_answer_resolves_and_response_is_bare(home_dir):
    """Legacy single path byte-identical: status answered, response is
    the bare answer, event fires."""
    from sweave.runtime.escalation import EscalationStore

    captured: list[tuple[str, dict]] = []
    store = EscalationStore(base_dir=home_dir, event_bus=lambda e, d: captured.append((e, d)))
    asyncio.run(store.create(delegation_id="d-3", question="Q?"))
    rec = asyncio.run(
        store.answer(delegation_id="d-3", response="Use JWT")
    )
    assert rec["status"] == "answered"
    assert rec["response"] == "Use JWT"
    assert rec["resolved"] is True
    assert rec["answers"] == ["Use JWT"]
    resolved = [d for e, d in captured if e == "specialist.escalation_resolved"]
    assert resolved and resolved[0]["status"] == "answered"


def test_partial_answer_persists_without_resolving(home_dir):
    """LOCKED: a partial batch answer persists the answers but the
    record stays pending (no resolved event, no flag clear)."""
    from sweave.runtime.escalation import EscalationStore

    captured: list[tuple[str, dict]] = []
    store = EscalationStore(base_dir=home_dir, event_bus=lambda e, d: captured.append((e, d)))
    asyncio.run(
        store.create(
            delegation_id="d-4",
            question="",
            questions=[{"question": "Q1?"}, {"question": "Q2?"}],
        )
    )
    rec = asyncio.run(
        store.answer(
            delegation_id="d-4",
            response="",
            answers=["first answer only"],
        )
    )
    assert rec["status"] == "pending"
    assert rec["resolved"] is False
    assert rec["answers"] == ["first answer only"]
    assert not [
        d for e, d in captured if e == "specialist.escalation_resolved"
    ]
    # status flips ONLY when ALL answered
    rec2 = asyncio.run(
        store.answer(
            delegation_id="d-4",
            response="",
            answers=["", "second answer"],
        )
    )
    assert rec2["status"] == "answered"
    assert rec2["resolved"] is True
    assert rec2["answers"] == ["first answer only", "second answer"]
    assert rec2["response"] == "1) first answer only  2) second answer"


def test_skip_whole_batch(home_dir):
    """Skip = whole batch -> best judgment."""
    from sweave.runtime.escalation import EscalationStore

    store = EscalationStore(base_dir=home_dir)
    asyncio.run(
        store.create(
            delegation_id="d-5",
            question="",
            questions=[{"question": "Q1?"}, {"question": "Q2?"}],
        )
    )
    rec = asyncio.run(store.skip(delegation_id="d-5"))
    assert rec["status"] == "skipped"
    assert len(rec["answers"]) == 2
    assert "best judgment" in rec["response"]
    assert "all questions skipped" in rec["response"]


def test_sync_skip_single_record_unchanged(home_dir):
    """Single-record skip text is unchanged for UI-compat."""
    from sweave.runtime.escalation import EscalationStore

    store = EscalationStore(base_dir=home_dir)
    asyncio.run(store.create(delegation_id="d-6", question="Q?"))
    rec = asyncio.run(store.skip(delegation_id="d-6"))
    assert rec["response"] == "skipped by user — proceed with best judgment"


# ---------------------------------------------------------------------------
# MCP _ask_human: batch validation, rejected strings
# ---------------------------------------------------------------------------


def _mcp_rejected(cases: list[dict]) -> list[str]:
    from sweave.mcp import _ask_human
    from mcp.types import CallToolRequestParams

    lines = []
    for args in cases:
        async def _run(a=args):
            return await _ask_human(
                None, CallToolRequestParams(name="ask_human", arguments=dict(a))
            )

        r = asyncio.run(_run())
        assert r.is_error
        lines.append(r.content[0].text)
    return lines


def test_mcp_batch_rejections(home_dir):
    lines = _mcp_rejected(
        [
            {"questions": []},
            {"questions": [{}]},
            {"questions": [{"question": str(i)} for i in range(6)]},
            {"questions": [{"question": "a", "options": "no"}]},
        ]
    )
    assert (
        lines[0]
        == "rejected: 'questions' must be a non-empty list of {question, options?} objects when provided"
    )
    assert (
        lines[2]
        == "rejected: at most 5 questions per ask_human call (batch cap; split into multiple asks)"
    )


def test_mcp_and_engine_rejected_strings_identical(home_dir):
    """Contract parity: MCP _ask_human and engine callAskHuman return
    IDENTICAL rejected: strings for the same bad inputs."""
    from sweave.mcp import _ask_human
    from mcp.types import CallToolRequestParams

    cases = [
        {"questions": []},
        {"questions": [{}]},
        {"questions": [{"question": str(i)} for i in range(6)]},
        {"questions": [{"question": "a", "options": "no"}]},
        {"caller_delegation_id": "d-x"},
    ]
    import sweave.mcp as mcp_mod

    async def fake_http_post(path, body, token):
        return {"escalation_id": "esc-z", "delegation_id": "d-x"}

    orig = mcp_mod._http_post
    mcp_mod._http_post = fake_http_post
    try:
        mcp_lines = []
        for args in cases:
            async def _run(a=dict(args)):
                return await mcp_mod._ask_human(
                    None,
                    CallToolRequestParams(name="ask_human", arguments=a),
                )

            r = asyncio.run(_run())
            mcp_lines.append(r.content[0].text)
    finally:
        mcp_mod._http_post = orig

    engine_cases = [
        {"questions": [], "caller_delegation_id_x": 1},
        {"questions": [{}]},
        {"questions": [{"question": str(i)} for i in range(6)]},
        {"questions": [{"question": "a", "options": "no"}]},
        {"caller_delegation_id": None},
    ]
    engine_lines = []
    for args in engine_cases:
        # engine gate: caller id rides runCtx, not args
        rc = {"delegationId": None, "isAborted": lambda: False}
        if args.get("caller_delegation_id"):
            rc["delegationId"] = args["caller_delegation_id"]
        r = _engine_call(args, rc)
        engine_lines.append(r["text"])
    for mcp_line, engine_line in zip(mcp_lines, engine_lines):
        assert mcp_line == engine_line, (mcp_line, engine_line)


def _engine_call(args: dict, rc: dict) -> dict:
    import subprocess, sys

    script = (
        "import { callAskHuman } from './sweave-engine/src/sweave.js';"
        f"const r = await callAskHuman({json.dumps(args)},"
        "{ delegationId: null, isAborted: () => false });"
        "console.log(JSON.stringify(r));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_mcp_batch_body_posts_questions(home_dir):
    """Batch body carries questions[]; MCP posts kind=question."""
    from sweave.mcp import _ask_human
    from mcp.types import CallToolRequestParams
    import sweave.mcp as mcp_mod

    captured: list[dict] = []

    async def fake_http_post(path, body, token):
        captured.append(body)
        return {"escalation_id": "esc-2", "delegation_id": "d-1"}

    orig = mcp_mod._http_post
    mcp_mod._http_post = fake_http_post
    try:
        r = asyncio.run(
            _ask_human(
                None,
                CallToolRequestParams(
                    name="ask_human",
                    arguments={
                        "questions": [
                            {"question": "A?"},
                            {"question": "B?", "options": ["x", "y"]},
                        ],
                        "caller_delegation_id": "d-1",
                    },
                ),
            )
        )
    finally:
        mcp_mod._http_post = orig
    assert not r.is_error
    body = captured[0]
    assert [q["question"] for q in body["questions"]] == ["A?", "B?"]
    assert body["questions"][1]["options"] == ["x", "y"]
    assert body["kind"] == "question" and body["audience"] == "human"


# ---------------------------------------------------------------------------
# Router (fastapi): escalate + answers end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    from pathlib import Path as PathCls
    from fastapi.testclient import TestClient

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("SWEAVE_MOCK_OPENCODE", "1")
    from sweave.web.server import app

    with TestClient(app) as c:
        state = c.app.state.app_state
        yield c, state


def _seed_delegation(state, delegation_id: str) -> None:
    from sweave.runtime.delegation_store import Delegation

    for store in state.delegation_stores.known_projects_stores():
        asyncio.run(
            store.add(
                Delegation(
                    delegation_id=delegation_id,
                    task_id=delegation_id,
                    agent="orchestrator",
                )
            )
        )
        return
    stores = asyncio.run(
        state.delegation_stores.for_project("/tmp")
    )
    asyncio.run(
        stores.add(
            Delegation(
                delegation_id=delegation_id, task_id=delegation_id, agent="o"
            )
        )
    )


def test_router_batch_escalate_and_full_answers(client, tmp_path):
    c, state = client
    _seed_delegation(state, "batch-1")
    r = c.post(
        "/api/delegations/batch-1/escalate",
        json={
            "question": "",
            "questions": [
                {"question": "Q1?"},
                {"question": "Q2?", "options": ["a"]},
            ],
        },
    )
    assert r.status_code == 200, r.text
    r = c.patch("/api/delegations/batch-1/zen", json={}) if False else None
    rec = c.get("/api/delegations/batch-1/escalation").json()
    assert len(rec["questions"]) == 2
    # partial answer -> still pending
    r = c.post(
        "/api/delegations/batch-1/answer",
        json={"response": "", "answers": ["one"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    assert r.json()["resolved"] is False
    # final answer -> resolved, joined response
    r = c.post(
        "/api/delegations/batch-1/answer",
        json={"response": "", "answers": ["", "two"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "answered"
    assert body["resolved"] is True
    assert body["response"] == "1) one  2) two"


def test_router_router_batch_over_5_is_rejected_line(client):
    c, state = client
    _seed_delegation(state, "batch-2")
    r = c.post(
        "/api/delegations/batch-2/escalate",
        json={
            "question": "x",
            "kind": "question",
            "questions": [{"question": f"Q{i}"} for i in range(6)],
        },
    )
    assert r.status_code == 400
    assert "rejected" in r.json()["detail"]


def test_router_legacy_single_question_byte_compatible(client):
    c, state = client
    _seed_delegation(state, "legacy-1")
    r = c.post(
        "/api/delegations/legacy-1/escalate",
        json={"question": "Only?", "options": ["a", "b"]},
    )
    assert r.status_code == 200, r.text
    rec = c.get("/api/delegations/legacy-1/escalation").json()
    # legacy keys unchanged + batch projection 1-elem
    assert rec["question"] == "Only?"
    assert rec["options"] == ["a", "b"]
    assert rec["questions"] == [
        {"question": "Only?", "options": ["a", "b"]}
    ]
    assert rec["answers"] == []
    r = c.post(
        "/api/delegations/legacy-1/answer", json={"response": "the answer"}
    )
    body = r.json()
    assert body["status"] == "answered"
    assert body["response"] == "the answer"  # bare, byte-identical
    assert body["answers"] == ["the answer"]


# ---------------------------------------------------------------------------
# ChatLoop synthesis note: batch quotes all pairs; legacy unchanged
# ---------------------------------------------------------------------------


def _note(rec: dict) -> str:
    from sweave.chat.loop import ChatLoop

    return ChatLoop._escalation_note(rec)


def test_escalation_note_batch_quotes_all_pairs():
    rec = {
        "kind": "question",
        "status": "answered",
        "questions": [{"question": "Q1?"}, {"question": "Q2?"}],
        "answers": ["a1", "a2"],
    }
    note = _note(rec)
    assert "1) Q: Q1? A: a1" in note
    assert "2) Q: Q2? A: a2" in note
    # legacy single note byte-identical
    single = _note(
        {
            "kind": "question",
            "status": "answered",
            "question": "Q?",
            "response": "A!",
        }
    )
    assert single == "Human answer (question): Q: Q? A: A!"


def test_escalation_note_batch_skip():
    rec = {
        "kind": "question",
        "status": "skipped",
        "questions": [{"question": "Q1?"}, {"question": "Q2?"}],
        "answers": ["s", "s"],
    }
    note = _note(rec)
    assert "skipped" in note
    assert "1) Q: Q1?" in note and "2) Q: Q2?" in note
    assert "best judgment" in note


# ---------------------------------------------------------------------------
# Engine: rejected strings + batch schema (JS mirror via node)
# ---------------------------------------------------------------------------


def test_engine_schema_carries_questions():
    script = (
        "import { SWEAVE_TOOL_DEFS } from './sweave-engine/src/sweave.js';"
        "const t = SWEAVE_TOOL_DEFS.find((t) => t.name === 'ask_human');"
        "console.log(JSON.stringify({schema: t.parameters,"
        " maxItems: t.parameters.properties.questions.maxItems,"
        " qRequired: t.parameters.properties.questions.items.required}));"
    )
    import subprocess

    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["maxItems"] == 5
    assert data["qRequired"] == ["question"]
