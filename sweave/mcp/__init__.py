"""Sweave MCP server (M1.6 step 1).

A stdio MCP server that exposes two tools to the orchestrator's
opencode session:

* ``defer(target, task, reason?)`` -- hand work to a named specialist
  via ``POST /api/v2/tasks`` (parent_task_id = caller's delegation;
  agent = target). The submit path runs DelegationManager.validate
  (depth / loop / budget) and the per-specialist ServeRunner; the
  MCP server is a thin client of the existing JobRunner pipeline.

* ``list_specialists()`` -- return the resolved specialist names with
  a one-line description (no secrets). Helps the orchestrator pick
  the right target without guessing.

**Auth**: localhost-only HTTP to the running Sweave server, plus a
shared token in the ``X-Sweave-MCP-Token`` header. The token is
auto-generated at first run and stored at ``~/.sweave/mcp_token``;
the per-project opencode.json (M1.6 step 3) injects it into the
spawned process via the ``environment`` block.

**Tool results** are plain-text success/error strings. Errors are
*instructions* the orchestrator can act on (e.g. "loop detected:
sql-expert already in chain") -- not stack traces. The MCP
protocol's structured ``isError`` flag is set so a code-mode
client can branch, but the orchestrator's text-mode path gets a
human-readable line either way.

**Stateless**: the server holds no per-call state. Opencode's
stdio transport restarts the server on every serve restart (free);
each request is its own HTTP call to the Sweave server.

Usage (when M1.6 step 3's plumbing is in place):

    opencode mcp list  # shows "sweave: connected"

Or directly:

    python -m sweave.mcp
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import httpx
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

logger = logging.getLogger("sweave.mcp")


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------


def _token_path() -> Path:
    """Path of the shared MCP token. Home-anchored (matches the
    anchored ``agents.yaml`` pattern from M1.2; never CWD-relative).
    """
    return Path.home() / ".sweave" / "mcp_token"


def get_or_create_token() -> str:
    """Return the persistent MCP token, creating one if needed.

    The token is 32 URL-safe random bytes (43 chars). Persisted at
    ``~/.sweave/mcp_token`` with mode 0o600 (best-effort on Windows;
    ignored silently if the platform doesn't support it).
    """
    path = _token_path()
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows ACLs; not a hard failure
        pass
    return token


# ---------------------------------------------------------------------------
# Sweave server client
# ---------------------------------------------------------------------------


def _sweave_base_url() -> str:
    """Where the running Sweave server listens. The MCP server is
    a localhost-only client; it MUST point at a local server (the
    token + the loopback are the only auth surface).
    """
    host = os.environ.get("SWEAVE_HOST", "127.0.0.1")
    port = int(os.environ.get("SWEAVE_PORT", "8100"))
    return f"http://{host}:{port}"


def _auth_header(token: str) -> dict[str, str]:
    return {"X-Sweave-MCP-Token": token}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


# Module-level so tests can monkeypatch the network layer.
async def _http_post(path: str, body: dict[str, Any], token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=_sweave_base_url(), timeout=30.0) as client:
        r = await client.post(
            path,
            json=body,
            headers=_auth_header(token),
        )
        # 4xx/5xx -> raise with the API's error detail so the tool
        # result string carries the actionable error.
        if r.status_code >= 400:
            detail: Any
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise RuntimeError(f"{path} -> {r.status_code}: {detail}")
        return r.json()


def _result_text(text: str, *, is_error: bool = False) -> types.CallToolResult:
    """Build a CallToolResult from a plain-text payload. Errors stay
    plain text so the orchestrator's text-mode path can act on them
    directly; the ``isError`` flag is set for code-mode clients.
    """
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        isError=is_error,
    )


# Tool: list_specialists
# Returns the resolved specialist pool as plain text: one per line
# "<name> -- <description>". No secrets, no system prompts, no model
# details -- the orchestrator only needs to pick a target.


async def _list_specialists(ctx: Any, params: types.CallToolRequest) -> types.CallToolResult:
    """List resolved specialists (one per line: "<name> -- <desc>").

    Note: ``params`` is a ``CallToolRequest``; the tool name and
    arguments live on ``params.params`` (a ``CallToolRequestParams``).
    We don't read them here -- list_specialists takes no args.
    """
    from sweave.web.state import AppState  # noqa: F401  (import-time cycle guard)

    token = get_or_create_token()
    try:
        data = await _http_post(
            "/api/mcp/specialists",
            {},
            token,
        )
    except Exception as e:
        logger.exception("list_specialists failed")
        return _result_text(f"error: {type(e).__name__}: {e}", is_error=True)
    specialists = data.get("specialists", [])
    if not specialists:
        return _result_text("(no specialists available)", is_error=False)
    lines = [f"{s['name']} -- {s.get('description', '').strip() or '(no description)'}"
             for s in specialists]
    return _result_text("\n".join(lines), is_error=False)


# Tool: defer
# Submits a child delegation. ``caller_delegation_id`` is supplied via
# the MCP context (opencode includes the session/turn id; we get the
# caller delegation id from the request via the orchestrator's prompt
# contract -- the caller passes it explicitly in the args).
#
# Why explicit: the opencode MCP context doesn't carry an opaque
# sweave-delegation id; the orchestrator's tool-call includes it as
# ``caller_delegation_id`` in the arguments (per the orchestrator
# prompt contract in M1.6 step 3).


async def _defer(ctx: Any, params: types.CallToolRequest) -> types.CallToolResult:
    """Hand ``task`` to specialist ``target`` and return a confirmation line.

    Arguments (per the orchestrator's tool contract):
    * ``target`` (str, required): the specialist name.
    * ``task`` (str, required): the work for the specialist.
    * ``reason`` (str, optional): the orchestrator's rationale; recorded
      on the trace for the R6 dispatch training signal.
    * ``caller_delegation_id`` (str, required): the orchestrator's own
      delegation id; the new delegation's ``parent_task_id`` is set to
      this so the chain links and parent-gating (M1.6 step 3) work.

    Returns plain text:
    * success: "queued: <delegation_id> (target=<target>, depth=<n>)"
    * rejection: "rejected: <reason>" with ``isError=True`` so the
      orchestrator can adjust (pick a different target, wait for a
      child to complete, etc.).
    """
    # The wire shape is CallToolRequest -> .params (CallToolRequestParams)
    # -> .arguments (dict). Pull both layers explicitly.
    req_params = params.params
    args = (req_params.arguments or {}) if req_params is not None else {}
    target = args.get("target")
    task = args.get("task")
    reason = args.get("reason") or ""
    caller_delegation_id = args.get("caller_delegation_id")

    if not isinstance(target, str) or not target.strip():
        return _result_text("rejected: 'target' is required and must be a non-empty string", is_error=True)
    if not isinstance(task, str) or not task.strip():
        return _result_text("rejected: 'task' is required and must be a non-empty string", is_error=True)
    if not isinstance(caller_delegation_id, str) or not caller_delegation_id.strip():
        return _result_text(
            "rejected: 'caller_delegation_id' is required "
            "(the orchestrator's own delegation id; set it in the tool call)",
            is_error=True,
        )

    body: dict[str, Any] = {
        "task": task,
        "agent": target,
        "parent_task_id": caller_delegation_id,
    }
    if reason:
        body["manifest"] = {"intent": reason, "source": "orchestrator_defer"}

    token = get_or_create_token()
    try:
        data = await _http_post("/api/v2/tasks", body, token)
    except Exception as e:
        # Convert the HTTP error into an "instruction" the orchestrator
        # can act on. DelegationManager rejections (loop / depth /
        # budget) come back as 4xx with a "detail" string; surface
        # them as plain text so the orchestrator's text path can read.
        msg = str(e)
        if "loop detected" in msg.lower() or "depth" in msg.lower() or "budget" in msg.lower():
            return _result_text(f"rejected: {msg}", is_error=True)
        return _result_text(f"error: {msg}", is_error=True)
    delegation_id = data.get("delegation_id", "?")
    return _result_text(f"queued: {delegation_id} (target={target})", is_error=False)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def build_server() -> Server:
    server = Server("sweave-mcp")
    server.add_request_handler("tools/list", types.ListToolsRequest, _list_tools_handler)
    server.add_request_handler("tools/call", types.CallToolRequest, _call_tool_dispatcher)
    return server


async def _list_tools_handler(ctx: Any, params: types.ListToolsRequest) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="list_specialists",
                description=(
                    "List the resolved specialists (name + one-line description). "
                    "No secrets, no system prompts. Use this to pick a target "
                    "for the defer tool."
                ),
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="defer",
                description=(
                    "Hand a task to a named specialist. The new delegation is a "
                    "child of the caller's delegation (parent_task_id is set "
                    "from caller_delegation_id). Returns the new delegation id "
                    "on success, or a 'rejected: <reason>' line on loop/depth/"
                    "budget violations (the orchestrator should pick a different "
                    "target or wait for a child to complete)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "Specialist name."},
                        "task": {"type": "string", "description": "The work for the specialist."},
                        "reason": {"type": "string", "description": "Why this specialist? (recorded on the trace.)"},
                        "caller_delegation_id": {
                            "type": "string",
                            "description": "The orchestrator's own delegation id; links the chain.",
                        },
                    },
                    "required": ["target", "task", "caller_delegation_id"],
                },
            ),
        ]
    )


# Dispatcher: route by tool name (the server only registers one
# request handler per JSON-RPC method, not per tool).
async def _call_tool_dispatcher(ctx: Any, params: types.CallToolRequest) -> types.CallToolResult:
    if params.name == "defer":
        return await _defer(ctx, params)
    if params.name == "list_specialists":
        return await _list_specialists(ctx, params)
    return _result_text(f"unknown tool: {params.name}", is_error=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    # Best-effort: pre-create the token so the per-project opencode.json
    # env block can read it via SWEAVE_MCP_TOKEN (set by step 3's plumbing).
    get_or_create_token()
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
