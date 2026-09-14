"""Usage-ledger endpoint (``docs/USAGE_LEDGER_PLAN.md`` surface 2).

``GET /api/stats/summary`` projects Delegation records + trace
``tokens_used`` events into per-day / per-model / per-project /
per-agent / per-kind / per-status cells. Compute-on-read (the plan's
no-daemon rule); counts and shapes only, never prompt/response text.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from sweave.stats.ledger import build_summary
from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


def _all_records(state: AppState) -> list:
    """Every Delegation record in every known per-project store."""
    stores = []
    if state.delegation_stores is not None:
        try:
            stores = state.delegation_stores.known_projects_stores()
        except Exception:  # noqa: BLE001 — degrade to empty, never 500
            stores = []
    records: list = []
    for store in stores:
        try:
            records.extend(store.list())
        except Exception:  # noqa: BLE001
            continue
    return records


@router.get("/api/stats/summary")
async def stats_summary(
    days: int = Query(default=30, ge=1, le=365),
    state: AppState = Depends(get_state),
) -> dict:
    """Usage summary over the trailing ``days`` (default 30, max 365).

    The projector never raises on bad rows/traces (degrades to
    zeros); unknown projects/traces simply contribute nothing.
    """
    return build_summary(_all_records(state), days=days)
