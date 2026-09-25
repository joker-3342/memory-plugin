"""审计接口（SPEC 4.14）。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from ..core import audit, personality

router = APIRouter(tags=["audit"])


@router.get("/audit")
def list_audit(limit: int = 50, target_table: Optional[str] = None) -> dict:
    return {"ok": True, "entries": audit.list_entries(limit=limit, target_table=target_table)}


@router.get("/audit/drift")
def drift(limit: int = 20) -> dict:
    return {"ok": True, "entries": personality.drift_warnings(limit)}