"""Native-engine endpoints (step 2).

Called by the ``sweave-engine`` sidecar over localhost with the same
``X-Sweave-MCP-Token`` guard as the MCP surface (the sidecar inherits
the server env, same as opencode-spawned MCP children). Not for the
web UI or general API consumers.

* ``POST /api/engine/permission`` — resolve one engine
  ``permission.asked``. The engine enforces the
  orchestrator-rendered map blindly; ``ask`` means ask (no scope
  re-evaluation here — the map already encodes scope, and a second
  evaluation could only drift from it). Creates a blocking human
  escalation (kind=permission, no timeout per the M1.11 ruling),
  waits for answered/skipped/timeout, and returns
  ``{"status", "response": "once"|"always"|"reject"}``. Skip/timeout
  map to ``reject`` (fail closed — same mapping as the opencode
  bridge in ``runtime/permission_bridge.py``). With
  ``{"wait": false}`` the endpoint returns the live escalation
  IMMEDIATELY (``{"status": "pending", "response": null,
  "escalation_id", ...}``) and the caller polls
  ``GET /api/delegations/{id}/escalation`` for the answer instead
  of holding the POST open: a held-open wait dies on the client's
  own idle timeout (Node ~300s, incident 2026-09-16) and takes the
  whole turn down with it. Additive — old callers that omit
  ``wait`` keep the blocking behaviour.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends

from sweave.web.deps import get_state
from sweave.web.routers.mcp import _check_mcp_token
from sweave.web.state import AppState

logger = logging.getLogger(__name__)

router = APIRouter()


def map_engine_answer(status: str, response: Any) -> str:
    """Map an escalation outcome to once|always|reject (fail closed).

    Only an answered escalation can grant; skipped/timeout (or
    anything unparseable) reject. Same mapping as the opencode
    bridge: an "always" answer grants the session approval,
    deny/reject/no reject.
    """
    if str(status or "") != "answered":
        return "reject"
    low = str(response or "").strip().lower()
    if "always" in low:
        return "always"
    if "deny" in low or "reject" in low or low in ("no", "skip"):
        return "reject"
    return "once"


async def resolve_engine_permission(
    payload: dict[str, Any],
    *,
    escalation_store: Any,
) -> dict[str, Any]:
    """Create the blocking human escalation for one engine ask and wait.

    Returns ``{"status", "response", "delegation_id"}``. Never raises
    for payload problems (returns ``rejected``); store failures return
    ``{"status": "error", ...}`` so the engine fails its tool call
    loudly instead of hanging the turn.
    """
    delegation_id = str(payload.get("delegation_id") or "").strip()
    question = str(payload.get("question") or "").strip()
    if not delegation_id or not question:
        return {"status": "rejected", "reason": "missing delegation_id/question"}
    options = payload.get("options") or ["allow once", "always allow", "deny"]
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    request_id = str(metadata.get("requestId") or metadata.get("requestID") or "")
    if not request_id:
        import uuid as _uuid

        request_id = f"eng_{_uuid.uuid4().hex[:10]}"
        metadata = {**metadata, "requestID": request_id}
    try:
        rec, _created = await escalation_store.create_or_reuse(
            delegation_id=delegation_id,
            question=question,
            options=list(options),
            kind="permission",
            audience="human",
            timeout_seconds=None,
            metadata=metadata,
            reuse_request_id=request_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("engine_permission: escalation create failed: %s", e)
        return {"status": "error", "reason": str(e)}
    if payload.get("wait") is False:
        # Poll mode: hand the live escalation back immediately so no
        # socket is held open for minutes (see module docstring).
        # ``response`` stays null until a human settles it — the
        # caller polls GET .../escalation and maps the settled
        # record with map_engine_answer itself.
        return {
            "status": str(rec.get("status", "pending")),
            "response": None,
            "escalation_id": rec.get("escalation_id"),
            "delegation_id": delegation_id,
        }
    while True:
        await asyncio.sleep(0.5)
        try:
            esc = (await escalation_store.get(delegation_id=delegation_id)) or {}
        except Exception:
            esc = {}
        if esc.get("status") != "pending":
            break
    status = str(esc.get("status", ""))
    response_value = map_engine_answer(status, esc.get("response"))
    return {
        "status": status,
        "response": response_value,
        "delegation_id": delegation_id,
    }


@router.post("/api/engine/permission")
async def engine_permission(
    payload: dict[str, Any] = Body(default={}),
    state: AppState = Depends(get_state),
    _token: None = Depends(_check_mcp_token),
) -> dict[str, Any]:
    """Resolve one engine permission ask (blocking; fail-closed)."""
    if state.escalation_store is None:
        return {"status": "error", "reason": "escalation store not wired"}
    try:
        return await resolve_engine_permission(
            payload if isinstance(payload, dict) else {},
            escalation_store=state.escalation_store,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("engine_permission: resolution failed: %s", e)
        return {"status": "error", "reason": str(e)}
