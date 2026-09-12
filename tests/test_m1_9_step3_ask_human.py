"""M1.9 step 3 tests: output funnel completion.

Two halves:

* **ask_human MCP tool** -- a sibling of ``defer`` (same MCP server,
  same auth). The asking delegation is flagged ``needs_attention``;
  the WS bus publishes ``specialist.escalated`` with the question.
  The Children tab surfaces it in the escalation lane. The answer path:
  ``POST /api/delegations/{id}/answer {response}`` returns the response
  as the tool result into the asking session. Timeout (default 15 min,
  configurable) returns a "no answer received" string so the LLM can
  proceed with best judgment rather than hanging.

* **Children tab live tree** -- WS-driven status pulses per delegation
  (queued / running / review / done / failed + needs-attention at the
  top), promote + answer buttons inline. The M1.8 no-rerender invariant
  (single-node patches only) carries over.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest


# Each test gets its own home dir so the mcp_token file doesn't bleed.
@pytest.fixture
def home_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Set MCP env so the server's localhost client points at a stub.
    return tmp_path


# ---------------------------------------------------------------------------
# ask_human MCP tool
# ---------------------------------------------------------------------------


def test_ask_human_tool_is_registered(monkeypatch):
    """The sweave MCP server registers ``ask_human`` alongside
    ``defer`` and ``list_specialists``. The tool is on the same
    auth / wire surface."""
    # Managed session: listing requires the provisioned env token
    # (unprovisioned servers list nothing — context-overhead gate).
    monkeypatch.setenv("SWEAVE_MCP_TOKEN", "test-token")
    from sweave.mcp import _list_tools_handler
    from mcp.types import PaginatedRequestParams

    async def _list():
        return await _list_tools_handler(None, PaginatedRequestParams())

    tools = asyncio.run(_list()).tools
    names = sorted(t.name for t in tools)
    assert "ask_human" in names
    assert "defer" in names
    assert "list_specialists" in names


def test_ask_human_tool_schema(monkeypatch):
    """``ask_human(question, options?, caller_delegation_id?)`` is the
    schema. ``options`` is optional (free-form vs multiple-choice);
    ``caller_delegation_id`` is required (the asking delegation id,
    same pattern as ``defer``)."""
    monkeypatch.setenv("SWEAVE_MCP_TOKEN", "test-token")
    from sweave.mcp import _list_tools_handler
    from mcp.types import PaginatedRequestParams

    async def _list():
        return await _list_tools_handler(None, PaginatedRequestParams())

    tools = asyncio.run(_list()).tools
    by_name = {t.name: t for t in tools}
    assert "ask_human" in by_name
    schema = by_name["ask_human"].input_schema
    properties = schema.get("properties") or {}
    assert "question" in properties
    assert "caller_delegation_id" in properties
    # caller_delegation_id is required (the ask needs to know which
    # delegation is asking); question is required.
    assert set(schema.get("required") or []) >= {
        "question",
        "caller_delegation_id",
    }


def test_ask_human_returns_escalation_id_and_publishes_event(home_dir):
    """``ask_human`` posts to ``/api/delegations/{id}/escalate``,
    publishes ``specialist.escalated`` on the WS bus, returns an
    ``escalation_id`` the LLM can use to fetch the answer later."""
    from sweave.mcp import _ask_human
    from mcp.types import CallToolRequestParams
    import sweave.mcp as mcp_mod

    captured: list[tuple[str, dict[str, Any]]] = []

    async def fake_http_post(path, body, token):
        captured.append((path, body))
        return {"escalation_id": "esc-1", "delegation_id": body.get("caller_delegation_id")}

    orig = mcp_mod._http_post
    mcp_mod._http_post = fake_http_post
    try:
        async def _run():
            req = CallToolRequestParams(
                name="ask_human",
                arguments={
                    "question": "Which auth strategy?",
                    "options": ["JWT", "session", "OAuth"],
                    "caller_delegation_id": "d-1",
                },
            )
            return await _ask_human(None, req)

        result = asyncio.run(_run())
    finally:
        mcp_mod._http_post = orig

    assert not result.is_error
    text = result.content[0].text
    assert "esc-1" in text
    # The HTTP path was hit with the right shape
    paths = [p[0] for p in captured]
    assert "/api/delegations/d-1/escalate" in paths
    bodies = [p[1] for p in captured if p[0] == "/api/delegations/d-1/escalate"]
    assert bodies
    assert bodies[0]["question"] == "Which auth strategy?"
    assert bodies[0]["options"] == ["JWT", "session", "OAuth"]


def test_ask_human_requires_question_and_caller_delegation_id(home_dir):
    """Missing required fields -> the tool returns a structured
    rejection (isError=True) with a "rejected: <reason>" line. Same
    contract as ``defer``: a code-mode client branches on isError,
    a text-mode client reads the line."""
    from sweave.mcp import _ask_human
    from mcp.types import CallToolRequestParams

    def _req(args):
        return CallToolRequestParams(name="ask_human", arguments=args)

    async def _run(args):
        return await _ask_human(None, _req(args))

    # Missing question
    r1 = asyncio.run(_run({"caller_delegation_id": "d-1"}))
    assert r1.is_error
    assert "rejected" in r1.content[0].text
    assert "question" in r1.content[0].text.lower()

    # Missing caller_delegation_id
    r2 = asyncio.run(_run({"question": "x"}))
    assert r2.is_error
    assert "rejected" in r2.content[0].text
    assert "caller_delegation_id" in r2.content[0].text


# ---------------------------------------------------------------------------
# Answer path: POST /api/delegations/{id}/answer
# ---------------------------------------------------------------------------


def test_delegation_answer_endpoint_resolves_escalation(home_dir):
    """The ``POST /api/delegations/{id}/answer {response}`` endpoint
    completes the escalation by setting the response and unblocking
    the asking session. The endpoint returns the escalation_id + the
    stored answer."""
    # We exercise the helpers directly; the router wiring is
    # integration-tested in step 4's live gate.
    from sweave.runtime.escalation import EscalationStore

    store = EscalationStore(base_dir=home_dir)
    asyncio.run(store.create(delegation_id="d-1", question="Q?", options=None))
    out = asyncio.run(store.answer(delegation_id="d-1", response="JWT"))
    assert out["delegation_id"] == "d-1"
    assert out["response"] == "JWT"
    assert out["status"] == "answered"


def test_delegation_answer_endpoint_returns_timeout_when_unanswered(home_dir):
    """When the timeout elapses without an answer, the escalation
    is auto-resolved with status=timeout. The asking session sees
    a "no answer received" placeholder string so it can proceed
    with best judgment rather than hanging."""
    from sweave.runtime.escalation import EscalationStore

    store = EscalationStore(base_dir=home_dir, timeout_seconds=0.0)
    asyncio.run(store.create(delegation_id="d-1", question="Q?"))
    asyncio.run(store.force_timeout(delegation_id="d-1"))
    out = asyncio.run(store.get(delegation_id="d-1"))
    assert out["status"] == "timeout"
    assert out["response"] == "no answer received"


def test_escalation_store_persists_across_instances(home_dir):
    """An escalation record is persisted to disk; a fresh store
    sees the same data. The MCP server (stateless stdio) reads
    from disk on every call."""
    from sweave.runtime.escalation import EscalationStore

    s1 = EscalationStore(base_dir=home_dir)
    asyncio.run(s1.create(delegation_id="d-1", question="Q?"))
    s2 = EscalationStore(base_dir=home_dir)
    out = asyncio.run(s2.get(delegation_id="d-1"))
    assert out["question"] == "Q?"


def test_escalation_store_emits_ws_event_on_create(home_dir):
    """When an escalation is created, the store publishes
    ``specialist.escalated`` with the question + delegation_id.
    The Children tab patches the escalation lane in place (M1.8
    no-rerender invariant). The AppState bridges the store's
    callable emitter to the WSEventBus on the production path."""
    from sweave.runtime.escalation import EscalationStore

    captured: list[tuple[str, dict[str, Any]]] = []

    async def _capture(event, data):
        captured.append((event, data))

    store = EscalationStore(base_dir=home_dir, event_bus=_capture)
    asyncio.run(store.create(delegation_id="d-1", question="Q?"))
    assert any(e == "specialist.escalated" for e, _ in captured)
    payload = [p for e, p in captured if e == "specialist.escalated"][0]
    assert payload["delegation_id"] == "d-1"
    assert payload["question"] == "Q?"
    assert "escalation_id" in payload


def test_escalation_store_emits_ws_event_on_answer(home_dir):
    """When the answer is posted, the store publishes
    ``specialist.escalation_resolved``. The Children tab patches
    the lane back to its non-attention state."""
    from sweave.runtime.escalation import EscalationStore

    captured: list[tuple[str, dict[str, Any]]] = []
    async def _capture(event, data):
        captured.append((event, data))

    store = EscalationStore(base_dir=home_dir, event_bus=_capture)
    asyncio.run(store.create(delegation_id="d-1", question="Q?"))
    asyncio.run(store.answer(delegation_id="d-1", response="JWT"))
    assert any(e == "specialist.escalation_resolved" for e, _ in captured)
    payload = [p for e, p in captured if e == "specialist.escalation_resolved"][0]
    assert payload["delegation_id"] == "d-1"
    assert payload["response"] == "JWT"
    assert payload["status"] == "answered"


# ---------------------------------------------------------------------------
# Children tab live tree: needs_attention lane + answer button
# ---------------------------------------------------------------------------


def test_delegation_record_has_needs_attention_flag(home_dir):
    """A Delegation carries ``needs_attention: bool = False``. The
    Children tab patches the lane to top when this flips True. R4
    may store this as a separate Escalation record (DR 2 in scope).
    For now, the boolean on the Delegation is the renderer contract.
    """
    from sweave.runtime.delegation_store import Delegation

    d = Delegation(delegation_id="d-1", task_id="t-1", agent="x")
    assert d.needs_attention is False


def test_delegation_needs_attention_can_be_updated(home_dir):
    """The Delegation store's update path accepts the new field."""
    import asyncio
    from pathlib import Path
    from sweave.runtime.delegation_store import DelegationStore, Delegation

    async def _run():
        store = DelegationStore(Path(home_dir))
        await store.add(
            Delegation(delegation_id="d-1", task_id="t-1", agent="x")
        )
        updated = await store.update("d-1", needs_attention=True)
        return updated

    d = asyncio.run(_run())
    assert d.needs_attention is True


# ---------------------------------------------------------------------------
# MCP token / env wiring (from M1.6 step 0; pin for step 3)
# ---------------------------------------------------------------------------


def test_mcp_token_env_var_passes_to_sweave_server(home_dir):
    """``SWEAVE_MCP_TOKEN`` env var is the seam for the opencode-spawned
    subprocess (M1.6 step 3): the per-project opencode.json's
    ``environment.SWEAVE_MCP_TOKEN`` block reads the token from the
    app's lifespan export, so the spawned subprocess doesn't need to
    read the home file directly. The MCP server's
    ``get_or_create_token()`` is the home-file reader; the runtime
    HTTP client (the auth header) reads from the env first to honour
    that seam. Pin the env-first behaviour."""
    from sweave.mcp import _token_from_env_or_file

    # When the env var is set, it wins.
    import os
    os.environ["SWEAVE_MCP_TOKEN"] = "test-token-from-env"
    assert _token_from_env_or_file() == "test-token-from-env"


def test_mcp_token_falls_back_to_home_file_when_env_unset(home_dir):
    """When ``SWEAVE_MCP_TOKEN`` is unset, the token comes from the
    home file (the M1.6 default)."""
    import os
    from sweave.mcp import _token_from_env_or_file, get_or_create_token

    os.environ.pop("SWEAVE_MCP_TOKEN", None)
    # The home file under the test's HOME was just generated by the
    # previous test's get_or_create_token call (the file is persistent
    # across instances). Read it back.
    expected = get_or_create_token()
    assert _token_from_env_or_file() == expected