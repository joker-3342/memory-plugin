"""会话锁接口（SPEC 4.6 / 4.7 / 10.1）。"""

from __future__ import annotations

from fastapi import APIRouter, Body

from .. import db
from ..core import lock
from ..models import LockRequest, UnlockRequest

router = APIRouter(tags=["session"])


@router.post("/session/lock")
def acquire(payload: LockRequest = Body(...)) -> dict:
    result = lock.acquire(
        payload.world_id,
        payload.holder,
        lock_type=payload.lock_type,
        timeout_ms=payload.timeout_ms,
        session_id=payload.session_id or payload.holder,
    )
    return {"acquired": result["acquired"], "version": result["version"], **{k: v for k, v in result.items() if k not in ("acquired", "version")}}


@router.post("/session/unlock")
def release(payload: UnlockRequest = Body(...)) -> dict:
    return lock.release(payload.world_id, payload.holder)


@router.post("/session/register")
def register(
    session_id: str = Body(..., embed=True),
    chat_id: str = Body(..., embed=True),
    device: str = Body("unknown", embed=True),
    priority: int = Body(0, embed=True),
) -> dict:
    db.execute(
        "INSERT OR REPLACE INTO sessions (session_id, chat_id, device, last_active, priority) VALUES (?,?,?,?,?)",
        (session_id, chat_id, device, db.now_ms(), priority),
    )
    return {"ok": True, "session_id": session_id}


@router.get("/session/conflicts")
def conflicts(limit: int = 50) -> dict:
    return {"ok": True, "conflicts": lock.conflicts(limit)}


@router.post("/session/purge")
def purge() -> dict:
    return {"ok": True, "removed": lock.purge_expired()}