"""M1.6 step 1 tests: Sweave MCP server.

Covers:
* ``list_specialists`` -- reads the resolved pool, returns plain-text
  one-per-line, omits the orchestrator singleton.
* ``defer`` -- posts to ``/api/v2/tasks`` with the chain link, returns
  the delegation id on success; surfaces loop/depth/budget rejections
  as a ``rejected: <reason>`` line with ``isError=True`` (orchestrator
  reads the text and adjusts).
* Auth: ``X-Sweave-MCP-Token`` header is required; missing or wrong
  returns 401.
* Token persistence: ``~/.sweave/mcp_token`` is created on first
  call and reused thereafter (the MCP server and the API read the
  same file).
* Server smoke: the stdio server starts and answers initialize +
  tools/list + tools/call round-trip against a running test app.

Tests use httpx ASGI against the FastAPI app (no real opencode; no
real subprocess for the MCP server -- we drive the tool handlers
directly).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from sweave.mcp import get_or_create_token


# ---------------------------------------------------------------------------
# AppState stub (mirrors the M1.1 step 4 + M1.4+M1.5 step 3 pattern)
# ---------------------------------------------------------------------------


def _build_state(monkeypatch, tmp_path: Path):
    """Build a fresh AppState backed by a tmp home (so the MCP token
    is created under tmp). Traces + agents.yaml point at tmp dirs
    so nothing leaks to the real home.
    """
    from pathlib import Path as PathCls
    from sweave.web import state as state_mod

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))

    original_build = state_mod.AppState.build

    @classmethod
    def build_with_stub_agents(cls, config_manager):
        state = original_build.__func__(cls, config_manager)
        state.dynamic_agents_path = tmp_path / "agents.yaml"
        state.traces_dir = tmp_path / "traces"
        state.traces_dir.mkdir(parents=True, exist_ok=True)
        # Token goes in tmp home (monkeypatched above).
        state.mcp_token = get_or_create_token()
        return state

    monkeypatch.setattr(state_mod.AppState, "build", build_with_stub_agents)
    from sweave.web.server import app
    return app


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    app = _build_state(monkeypatch, tmp_path)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Auth: token is required
# ---------------------------------------------------------------------------


def test_mcp_specialists_requires_token(client: TestClient, tmp_path: Path):
    r = client.get("/api/mcp/specialists")
    assert r.status_code == 401
    assert "X-Sweave-MCP-Token" in r.json()["detail"]


def test_mcp_specialists_rejects_wrong_token(client: TestClient, tmp_path: Path):
    r = client.get("/api/mcp/specialists", headers={"X-Sweave-MCP-Token": "wrong"})
    assert r.status_code == 401


def test_mcp_specialists_accepts_valid_token(client: TestClient, tmp_path: Path):
    token = get_or_create_token()
    r = client.get("/api/mcp/specialists", headers={"X-Sweave-MCP-Token": token})
    assert r.status_code == 200
    data = r.json()
    assert "specialists" in data
    assert isinstance(data["specialists"], list)


def test_mcp_token_is_persisted(tmp_path: Path, monkeypatch):
    """The token lives at ~/.sweave/mcp_token. First call creates it;
    second call reuses the same value (idempotent)."""
    from pathlib import Path as PathCls

    monkeypatch.setattr(PathCls, "home", classmethod(lambda cls: tmp_path))
    t1 = get_or_create_token()
    t2 = get_or_create_token()
    assert t1 == t2
    assert (tmp_path / ".sweave" / "mcp_token").exists()


# ---------------------------------------------------------------------------
# list_specialists: shape + orchestrator exclusion
# ---------------------------------------------------------------------------


def _create_session(client: TestClient, tmp_path: Path, label: str) -> tuple[str, str]:
    import uuid

    name = f"p-mcp-{label}-{uuid.uuid4().hex[:8]}"
    proj_dir = tmp_path / name
    proj_dir.mkdir(parents=True, exist_ok=True)
    r = client.post(
        "/api/projects", json={"name": name, "path": str(proj_dir), "description": ""}
    )
    assert r.status_code == 200, r.text
    client.post(f"/api/projects/{name}/active")
    r = client.post(
        "/api/sessions", json={"name": f"S-{label}", "project_name": name}
    )
    assert r.status_code == 200, r.text
    return name, r.json()["session"]["id"]


def test_list_specialists_excludes_orchestrator_and_strips_secrets(
    client: TestClient, tmp_path: Path
):
    """The MCP-facing list returns only {name, description} per
    specialist; the orchestrator singleton is excluded (it's the
    caller, not a defer target).
    """
    name, _ = _create_session(client, tmp_path, "list")
    token = get_or_create_token()
    r = client.get(
        "/api/mcp/specialists", headers={"X-Sweave-MCP-Token": token}
    )
    assert r.status_code == 200
    data = r.json()
    names = {s["name"] for s in data["specialists"]}
    # Orchestrator singleton is filtered out.
    assert "orchestrator" not in names
    # The seed specialists are present (the loader renames them to
    # "<role>-specialist" by default -- "backend-specialist",
    # "frontend-specialist", "reviewer-specialist").
    assert any("backend" in n for n in names)
    assert any("frontend" in n for n in names)
    assert any("reviewer" in n for n in names)
    # No secrets / system prompts leak.
    for s in data["specialists"]:
        assert set(s.keys()) <= {"name", "description"}


# ---------------------------------------------------------------------------
# defer: tool handler logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defer_submits_child_via_v2_tasks_with_parent_link(
    monkeypatch, tmp_path: Path
):
    """The defer tool posts to /api/v2/tasks with parent_task_id =
    caller_delegation_id and agent = target. Returns the delegation
    id as plain text on success.

    We stub the HTTP layer (httpx) so the MCP server talks to a
    recorded client, not a real server.
    """
    captured: list[tuple[str, dict]] = []

    async def fake_post(path: str, body: dict, token: str) -> dict:
        captured.append((path, body))
        return {"delegation_id": "del-abc123", "status": "queued"}

    monkeypatch.setattr("sweave.mcp._http_post", fake_post)

    from sweave.mcp import _defer
    from mcp.types import CallToolRequestParams

    req = CallToolRequestParams(
        name="defer",
        arguments={
            "target": "backend",
            "task": "Write a hello.py that prints OK",
            "reason": "minimal reproducible task",
            "caller_delegation_id": "del-parent-xyz",
        },
    )
    result = await _defer(ctx=None, params=req)
    assert result.is_error is False
    assert len(result.content) == 1
    assert "del-abc123" in result.content[0].text
    assert "target=backend" in result.content[0].text
    # And the HTTP call carried the chain link.
    assert len(captured) == 1
    path, body = captured[0]
    assert path == "/api/v2/tasks"
    assert body["agent"] == "backend"
    assert body["task"] == "Write a hello.py that prints OK"
    assert body["parent_task_id"] == "del-parent-xyz"
    # The reason is recorded as a manifest.intent hint (R6 dispatch
    # training signal).
    assert body["manifest"]["intent"] == "minimal reproducible task"


@pytest.mark.asyncio
async def test_defer_surfaces_rejections_as_instructions(monkeypatch):
    """When DelegationManager rejects the child (loop/depth/budget),
    the tool returns a 'rejected: <reason>' line with isError=True.
    The orchestrator can read the text and pick a different target.
    """
    async def fake_post(path, body, token):
        # Simulate DelegationManager's loop-detect response shape.
        raise RuntimeError(
            "loop detected: 'backend' is already in the active chain (A->backend)"
        )

    monkeypatch.setattr("sweave.mcp._http_post", fake_post)

    from sweave.mcp import _defer
    from mcp.types import CallToolRequestParams

    req = CallToolRequestParams(
        name="defer",
        arguments={
            "target": "backend",
            "task": "x",
            "caller_delegation_id": "del-parent-1",
        },
    )
    result = await _defer(ctx=None, params=req)
    assert result.is_error is True
    text = result.content[0].text
    assert text.startswith("rejected:")
    assert "loop" in text.lower()


@pytest.mark.asyncio
async def test_defer_validates_required_arguments():
    """Missing target / task / caller_delegation_id is rejected at
    the tool boundary (before any HTTP call). This is the orchestrator
    contract; the harness should never have to reason about partial
    args.
    """
    from sweave.mcp import _defer
    from mcp.types import CallToolRequestParams

    def make_req(args: dict) -> CallToolRequestParams:
        return CallToolRequestParams(name="defer", arguments=args)

    # Missing target
    r = await _defer(
        ctx=None,
        params=make_req({"task": "x", "caller_delegation_id": "p1"}),
    )
    assert r.is_error is True
    assert "target" in r.content[0].text

    # Missing task
    r = await _defer(
        ctx=None,
        params=make_req({"target": "backend", "caller_delegation_id": "p1"}),
    )
    assert r.is_error is True
    assert "task" in r.content[0].text

    # Missing caller_delegation_id
    r = await _defer(
        ctx=None,
        params=make_req({"target": "backend", "task": "x"}),
    )
    assert r.is_error is True
    assert "caller_delegation_id" in r.content[0].text


@pytest.mark.asyncio
async def test_defer_unknown_target_message_surfaces_4xx(monkeypatch):
    """A 4xx with a non-loop/depth/budget error (e.g. unknown agent
    name) is surfaced as 'error: <message>' with isError=True. The
    orchestrator treats both shapes as actionable; the wording
    distinguishes 'rejected' (the chain rules said no) from 'error'
    (something else went wrong).
    """
    async def fake_post(path, body, token):
        raise RuntimeError("404: specialist 'ghost' not found")

    monkeypatch.setattr("sweave.mcp._http_post", fake_post)

    from sweave.mcp import _defer
    from mcp.types import CallToolRequestParams

    req = CallToolRequestParams(
        name="defer",
        arguments={"target": "ghost", "task": "x", "caller_delegation_id": "p1"},
    )
    result = await _defer(ctx=None, params=req)
    assert result.is_error is True
    text = result.content[0].text
    # "error" prefix (not "rejected") because the message doesn't
    # contain loop/depth/budget keywords.
    assert text.startswith("error:")
    assert "ghost" in text


# ---------------------------------------------------------------------------
# Tool registry: list_specialists + defer registered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_list_exposes_both_tools(monkeypatch):
    # Provisioned session (Sweave-managed serve): full tool surface.
    monkeypatch.setenv("SWEAVE_MCP_TOKEN", "test-token")
    from sweave.mcp import _list_tools_handler
    from mcp.types import PaginatedRequestParams

    result = await _list_tools_handler(ctx=None, params=PaginatedRequestParams())
    # M1.9 step 3 added ask_human alongside the original two tools;
    # M1.11 adds escalate (specialist -> orchestrator notice).
    # The expected set is the superset; future additions (R4+)
    # update this test, not the other way around.
    names = {t.name for t in result.tools}
    assert names == {"list_specialists", "defer", "ask_human", "escalate"}
    # The defer schema requires caller_delegation_id (orchestrator
    # contract; nothing about the chain link is optional).
    defer_tool = next(t for t in result.tools if t.name == "defer")
    assert "caller_delegation_id" in defer_tool.input_schema["required"]
    assert "target" in defer_tool.input_schema["required"]
    assert "task" in defer_tool.input_schema["required"]


@pytest.mark.asyncio
async def test_tools_list_empty_outside_managed_sessions(monkeypatch):
    """Standalone opencode discovers the same per-project opencode.json
    via upward config resolution. Without SWEAVE_MCP_TOKEN the tools
    cannot work (no Sweave API + token) and their schemas are pure
    context overhead — list nothing, silently (no MCP-error spam)."""
    monkeypatch.delenv("SWEAVE_MCP_TOKEN", raising=False)
    from sweave.mcp import _list_tools_handler
    from mcp.types import PaginatedRequestParams

    result = await _list_tools_handler(ctx=None, params=PaginatedRequestParams())
    assert result.tools == []


@pytest.mark.asyncio
async def test_tool_call_rejected_outside_managed_sessions(monkeypatch):
    """Belt-and-braces with the empty list: cached/blind calls get a
    clean rejection, never an authenticated call."""
    monkeypatch.delenv("SWEAVE_MCP_TOKEN", raising=False)
    from sweave.mcp import _call_tool_dispatcher
    from mcp.types import CallToolRequestParams

    req = CallToolRequestParams(
        name="defer",
        arguments={"target": "backend", "task": "x", "caller_delegation_id": "p1"},
    )
    result = await _call_tool_dispatcher(ctx=None, params=req)
    assert result.is_error is True
    assert "outside Sweave-managed sessions" in result.content[0].text


# ---------------------------------------------------------------------------
# End-to-end stdio smoke: the server starts, answers initialize +
# tools/list + tools/call against a live httpx ASGI test app.
#
# This is the M1.6 step 1 live gate per the plan: "tool handlers
# against a running test app (httpx ASGI)".
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_server_stdio_round_trip(monkeypatch, tmp_path: Path):
    """Boot the stdio MCP server as a subprocess; the client drives
    initialize + tools/list + tools/call against a real uvicorn test
    app. Confirms the wire format end-to-end without depending on
    opencode.

    SWEAVE_PORT points the MCP server at the uvicorn test port.
    Regression pin for the 2026-09-09 -32602 incident: handlers used
    to be registered against the full request models, so the runner
    rejected every tools/call with "Invalid request parameters"
    (tools/list only worked by accident of all-default fields).
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from sweave.web.server import app

    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    import uvicorn

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve())
    for _ in range(50):
        await asyncio.sleep(0.1)
        if server.started:
            break
    assert server.started, "uvicorn did not start"

    try:
        from pathlib import Path as PathCls

        orig_home = PathCls.home
        PathCls.home = classmethod(lambda cls: tmp_path)  # type: ignore[assignment]
        try:
            # Production-faithful token seam: the lifespan exports
            # the canonical token into the server env and opencode's
            # ``{env:SWEAVE_MCP_TOKEN}`` expansion hands it to the
            # MCP child. Mirror that here explicitly (the test env
            # may carry a foreign SWEAVE_MCP_TOKEN from an outer
            # lifespan export, which must not shadow the tmp token).
            from sweave.mcp import get_or_create_token

            tmp_token = get_or_create_token()
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "sweave.mcp"],
                env={
                    **os.environ.copy(),
                    "SWEAVE_PORT": str(port),
                    "SWEAVE_MCP_TOKEN": tmp_token,
                    "HOME": str(tmp_path),
                    "USERPROFILE": str(tmp_path),
                },
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    assert init.server_info.name == "sweave-mcp"
                    tools = await session.list_tools()
                    tool_names = {t.name for t in tools.tools}
                    # M1.9 step 3 added ask_human; M1.11 adds escalate.
                    assert tool_names == {"list_specialists", "defer", "ask_human", "escalate"}
                    # tools/call over the wire (the -32602 pin): the
                    # no-arg tool works with and without arguments.
                    for arguments in (None, {}):
                        result = await session.call_tool(
                            "list_specialists", arguments=arguments
                        )
                        assert result.is_error is not True
                        texts = [
                            c.text
                            for c in result.content
                            if getattr(c, "type", "") == "text"
                        ]
                        assert texts, "list_specialists returned no text content"
                        assert "backend" in texts[0]
                    # Unknown tool names route to the dispatcher's
                    # plain-text error, not a protocol error.
                    unknown = await session.call_tool("nope", arguments={})
                    assert unknown.is_error is True
        finally:
            PathCls.home = orig_home  # type: ignore[assignment]
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            server_task.cancel()
