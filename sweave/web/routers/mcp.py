"""MCP-only API endpoints (M1.6 step 1).

These endpoints exist solely to back the stdio MCP server (sweave.mcp).
They require the ``X-Sweave-MCP-Token`` header (the shared token at
``~/.sweave/mcp_token``) -- they're not for the web UI or general API
consumers. The MCP server is the only intended caller; the auth is a
belt-and-suspenders guard against accidental LAN exposure.

Endpoints:

* ``GET /api/mcp/specialists`` -- list resolved specialists (name +
  one-line description). The MCP ``list_specialists`` tool calls this.

The ``defer`` MCP tool calls the existing ``POST /api/v2/tasks``
endpoint (no MCP-specific URL); the DelegationManager (M1.6 step 2)
runs inside that handler. We don't shadow v2 here.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException

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
