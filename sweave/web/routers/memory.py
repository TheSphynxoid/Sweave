"""Memory tool routes (recall, retain, reflect). The bank-list endpoint
(``GET /api/memory/banks``) lives in ``routers/projects.py`` for historical
reasons; functionally it belongs with memory.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from sweave.web.deps import get_state
from sweave.web.state import AppState

router = APIRouter()


@router.post("/api/memory/recall")
async def memory_recall(
    query: str,
    bank_id: Optional[str] = None,
    limit: int = 10,
    state: AppState = Depends(get_state),
):
    return await state.memory_tool.execute(
        "recall", query=query, bank_id=bank_id, limit=limit
    )


@router.post("/api/memory/retain")
async def memory_retain(
    content: str,
    bank_id: Optional[str] = None,
    tags: list[str] | None = None,
    state: AppState = Depends(get_state),
):
    return await state.memory_tool.execute(
        "retain", content=content, bank_id=bank_id, tags=tags
    )


@router.post("/api/memory/reflect")
async def memory_reflect(
    query: str,
    bank_id: Optional[str] = None,
    state: AppState = Depends(get_state),
):
    return await state.memory_tool.execute("reflect", query=query, bank_id=bank_id)
