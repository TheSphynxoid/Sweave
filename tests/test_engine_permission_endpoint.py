"""Engine step 2: POST /api/engine/permission (ask -> human -> response).

Covers the endpoint core directly (FakeStore precedent from
test_m1_12_permission_bridge.py — no waiting threads: stores answer
immediately except the one wait-path test) plus the HTTP guard rails
(401 without token, 400 on missing fields) via the full-app
TestClient pattern (temp home so the token file stays hermetic).

Contract: ask means ask (no scope re-evaluation — the
orchestrator-rendered map already encodes scope); kind=permission, no
timeout; answered maps once|always|reject, skipped/timeout fail
closed to reject; store failures return error (never hang).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from sweave.web.routers.engine import (
    map_engine_answer,
    resolve_engine_permission,
)


class FakeStore:
    def __init__(self, responses: dict | None = None):
        self.responses: dict = dict(responses or {})
        self.created: list[dict] = []

    async def create_or_reuse(self, **kwargs):
        self.created.append(kwargs)
        did = kwargs["delegation_id"]
        self.responses.setdefault(
            did, {"status": "answered", "response": "allow once"}
        )
        return {"status": "pending"}, True

    async def get(self, *, delegation_id: str):
        return self.responses.get(delegation_id)


class FlipStore(FakeStore):
    """Answers after N polls (proves the wait path, fast)."""

    def __init__(self, flips_after: int = 2):
        super().__init__({"d-wait": {"status": "pending"}})
        self.flips_after = flips_after
        self.polls = 0

    async def get(self, *, delegation_id: str):
        self.polls += 1
        if self.polls >= self.flips_after:
            return {"status": "answered", "response": "allow once"}
        return {"status": "pending"}


class BoomStore:
    async def create_or_reuse(self, **kwargs):
        raise RuntimeError("store down")

    async def get(self, *, delegation_id: str):
        raise RuntimeError("store down")


def _payload(**overrides):
    body = {
        "delegation_id": "d-1",
        "question": "Engine asks bash for echo hi. allow once / always allow / deny?",
        "options": ["allow once", "always allow", "deny"],
        "metadata": {"requestID": "eng_test_1", "permission": "bash"},
    }
    body.update(overrides)
    return body


# --- answer mapping ------------------------------------------------------


@pytest.mark.parametrize(
    "status,response,expected",
    [
        ("answered", "allow once", "once"),
        ("answered", "yes, allow once please", "once"),
        ("answered", "always allow", "always"),
        ("answered", "ALWAYS", "always"),
        ("answered", "deny", "reject"),
        ("answered", "no", "reject"),
        ("answered", "", "once"),
        ("skipped", "", "reject"),
        ("timeout", "", "reject"),
    ],
)
def test_answer_mapping(status, response, expected):
    assert map_engine_answer(status, response) == expected


# --- core -----------------------------------------------------------------


def test_resolve_allow_once():
    out = asyncio.run(
        resolve_engine_permission(_payload(), escalation_store=FakeStore())
    )
    assert out == {"status": "answered", "response": "once", "delegation_id": "d-1"}


def test_resolve_records_permission_kind_without_timeout():
    store = FakeStore({"d-1": {"status": "answered", "response": "always allow"}})
    out = asyncio.run(resolve_engine_permission(_payload(), escalation_store=store))
    assert out["response"] == "always"
    assert store.created[0]["kind"] == "permission"
    assert store.created[0]["audience"] == "human"
    assert store.created[0]["timeout_seconds"] is None
    assert store.created[0]["delegation_id"] == "d-1"


def test_resolve_deny_and_skip_fail_closed():
    store = FakeStore({"d-1": {"status": "answered", "response": "deny"}})
    out = asyncio.run(resolve_engine_permission(_payload(), escalation_store=store))
    assert out["response"] == "reject"
    store2 = FakeStore({"d-1": {"status": "skipped", "response": ""}})
    out2 = asyncio.run(resolve_engine_permission(_payload(), escalation_store=store2))
    assert out2 == {"status": "skipped", "response": "reject", "delegation_id": "d-1"}


def test_resolve_waits_for_late_answer():
    store = FlipStore(flips_after=2)
    out = asyncio.run(
        resolve_engine_permission(
            _payload(delegation_id="d-wait"), escalation_store=store
        )
    )
    assert out["response"] == "once"
    assert store.polls >= 2


class NeverStore:
    """Create-only store that never settles (proves the no-wait path)."""

    def __init__(self):
        self.polls = 0

    async def create_or_reuse(self, **kwargs):
        return {
            "status": "pending",
            "escalation_id": "esc-wait-1",
            "delegation_id": kwargs["delegation_id"],
        }, True

    async def get(self, *, delegation_id: str):
        self.polls += 1
        return {"status": "pending"}


def test_resolve_wait_false_returns_immediately():
    """Incident 2026-09-16: with wait:false the endpoint hands the
    live escalation back at once (no socket held open for minutes)
    and never polls — the caller waits via GET polling."""
    store = NeverStore()
    out = asyncio.run(
        resolve_engine_permission(
            _payload(wait=False), escalation_store=store
        )
    )
    assert out == {
        "status": "pending",
        "response": None,
        "escalation_id": "esc-wait-1",
        "delegation_id": "d-1",
    }
    assert store.polls == 0


def test_resolve_missing_fields_rejected():
    out = asyncio.run(
        resolve_engine_permission({"question": "q"}, escalation_store=FakeStore())
    )
    assert out["status"] == "rejected"
    out = asyncio.run(
        resolve_engine_permission({"delegation_id": "d"}, escalation_store=FakeStore())
    )
    assert out["status"] == "rejected"


def test_resolve_store_failure_is_error_not_hang():
    out = asyncio.run(resolve_engine_permission(_payload(), escalation_store=BoomStore()))
    assert out["status"] == "error"


# --- HTTP guards -----------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from sweave.web import state as state_mod

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):  # type: ignore[no-untyped-def]
        state = original_build.__func__(cls, config_manager)  # type: ignore[attr-defined]
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)
    from sweave.web.server import app

    with TestClient(app) as c:
        yield c


def _token() -> str:
    from sweave.mcp import get_or_create_token

    return get_or_create_token()


def test_http_rejects_missing_token(client):
    r = client.post("/api/engine/permission", json=_payload())
    assert r.status_code == 401


def test_http_rejects_missing_fields(client):
    r = client.post(
        "/api/engine/permission",
        json={"question": "q"},
        headers={"X-Sweave-MCP-Token": _token()},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


def test_http_wait_false_returns_pending_immediately(client):
    """The blocking default would hang this test forever against the
    real store — wait:false must create and return at once."""
    r = client.post(
        "/api/engine/permission",
        json=_payload(wait=False),
        headers={"X-Sweave-MCP-Token": _token()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["response"] is None
    assert body["escalation_id"]
    assert body["delegation_id"] == "d-1"
