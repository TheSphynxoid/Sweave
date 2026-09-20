"""Version-matrix endpoint (versioning ruling 2026-09-20).

``GET /api/version`` renders ``sweave/version.py``: part versions
(backend/engine/web) plus the contract versions that gate
compatibility (engine protocol, Delegation schema). Pure read —
no state, no auth, safe as a smoke probe (``run.py --check``).
"""

from __future__ import annotations

from fastapi import APIRouter

from sweave.version import get_version_matrix

router = APIRouter()


@router.get("/api/version")
async def version_matrix() -> dict:
    """Part + contract versions (never raises on unreadable files)."""
    return get_version_matrix()
