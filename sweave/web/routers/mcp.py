"""MCP-only API endpoints (M1.6 step 1).

These endpoints exist solely to back the stdio MCP server (sweave.mcp).
They require the ``X-Sweave-MCP-Token`` header (the shared token at
``~/.sweave/mcp_token``) -- they're not for the web UI or general API
consumers. The MCP server is the only intended caller; the auth is a
belt-and-suspenders guard against accidental LAN exposure.

Endpoints:

* ``GET /api/mcp/specialists`` -- list resolved specialists (name +
  one-line description). The MCP ``list_specialists`` tool calls this.

* ``POST /api/permission/hijack`` -- M1.12 amendment 1: the
  sweave-permission plugin (inside the opencode serve) ferries
  ``permission.asked`` here; scope-checked (auto-allow) or turned
  into a blocking human question; the pinned reply is POSTed back
  to the serve by sweave.

* ``POST /api/activity/tool-started`` -- supervisor step 4: the
  same island plugin ferries ``tool.execute.before`` here (the
  SSE bus is silent mid-tool). Attributed via the session
  registry into a ``tool.started`` trace pulse the supervisor
  already counts — no supervision logic lives here.

The ``defer`` MCP tool calls the existing ``POST /api/v2/tasks``
endpoint (no MCP-specific URL); the DelegationManager (M1.6 step 2)
runs inside that handler. We don't shadow v2 here.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from sweave.web.deps import get_state
from sweave.web.state import AppState

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_mcp_token(
    x_sweave_mcp_token: Annotated[str | None, Header()] = None,
) -> None:
    """Validate the ``X-Sweave-MCP-Token`` header against the shared
    token at ``~/.sweave/mcp_token``.

    The shared token is auto-generated on first run (see
    ``sweave.mcp.get_or_create_token``). Both the MCP server and the
    API read the same file, so they agree without a config knob.
    """
    from sweave.mcp import get_or_create_token

    expected = get_or_create_token()
    if x_sweave_mcp_token is None or x_sweave_mcp_token != expected:
        raise HTTPException(
            status_code=401,
            detail="invalid or missing X-Sweave-MCP-Token header",
        )


@router.get("/api/mcp/specialists")
async def list_specialists(
    state: AppState = Depends(get_state),
    _token: None = Depends(_check_mcp_token),
) -> dict[str, Any]:
    """Return the resolved specialist pool (project -> global -> seed).

    Mirrors the public ``/api/specialists`` shape but strips secrets
    and large fields: only ``name`` + ``description`` are returned
    (orchestrator only needs to pick a target).
    """
    proj_dir = _resolve_active_project_dir(state)
    resolver = _resolver(state)
    specialists = resolver.list_resolved(project_dir=proj_dir) if proj_dir else resolver.list_resolved()
    return {
        "specialists": [
            {"name": s.name, "description": s.description or ""}
            for s in specialists
            if not s.is_orchestrator  # orchestrator is never a defer target
        ],
    }


# ---------------------------------------------------------------------------
# Helpers (mirrors of the specialists router; kept local so this router
# stays decoupled from the v1 surface -- the MCP caller is a different
# concern from the web UI)
# ---------------------------------------------------------------------------


def _resolve_active_project_dir(state: AppState) -> "Path | None":
    from pathlib import Path

    from sweave.projects import project_manager

    active = project_manager.get_active_project()
    return Path(active.path) if active is not None else None


def _resolver(state: AppState):  # type: ignore[no-untyped-def]
    if state.specialist_resolver is None:
        raise HTTPException(503, "specialist resolver not initialised")
    return state.specialist_resolver


# ---------------------------------------------------------------------------
# M1.12 amendment 1 (2026-09-10): in-band permission bridge
# ---------------------------------------------------------------------------


@router.post("/api/permission/hijack")
async def permission_hijack(
    payload: dict[str, Any] = Body(default={}),
    state: AppState = Depends(get_state),
    _token: None = Depends(_check_mcp_token),
) -> dict[str, Any]:
    """Resolve one opencode ``permission.asked`` (in-band bridge).

    Called by the sweave-permission plugin ferrying the ask from the
    serve process. The decision happens here and the pinned reply
    (``POST {serve}/session/{sid}/permissions/{rid}``) is POSTed by
    sweave before this returns:

    * in scope (project dir / worktrees / ``~/.sweave`` / declared
      roots) → auto-allow ``once``;
    * out of scope → blocking human escalation (kind=permission,
      no timeout, M1.11 ruling) → answer POSTed as
      ``once``/``always``/``reject``.

    Never raises for payload problems: the plugin is fire-and-forget
    and unresolvable asks must fail LOUD on the sweave side (logged),
    not 500 the plugin silently.
    """
    from sweave.projects import project_manager
    from sweave.runtime.permission_bridge import resolve_hijack_request

    if state.escalation_store is None:
        return {"status": "error", "reason": "escalation store not wired"}
    try:
        return await resolve_hijack_request(
            payload if isinstance(payload, dict) else {},
            escalation_store=state.escalation_store,
            project_manager=project_manager,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("permission_hijack: resolution failed: %s", e)
        return {"status": "error", "reason": str(e)}


# ---------------------------------------------------------------------------
# Supervisor step 4 (2026-09-15): activity ferry
# ---------------------------------------------------------------------------


@router.post("/api/activity/tool-started")
async def activity_tool_started(
    payload: dict[str, Any] = Body(default={}),
    state: AppState = Depends(get_state),
    _token: None = Depends(_check_mcp_token),
) -> dict[str, Any]:
    """Attribute one ferried opencode tool start as a trace pulse.

    Called fire-and-forget by the island plugin's
    ``tool.execute.before`` hook (same token guard as the hijack
    endpoint). Resolves session → delegation via the runtime's
    session registry and appends ``tool.started`` (source=ferry)
    to the delegation trace — the supervisor's pulse layer counts
    it with zero code change. Unknown sessions degrade to a 200
    no-op (a serve outliving its registry entry must never break);
    payload problems likewise never 500 (mirror the hijack
    never-raise contract).
    """
    from sweave.runtime.permission_bridge import record_ferried_tool_started

    return record_ferried_tool_started(
        payload if isinstance(payload, dict) else {},
        traces_dir=state.traces_dir,
    )
