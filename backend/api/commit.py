"""POST /commit —— 因果提交（SPEC 4.3）。

- 没有因果不入 committed
- 事务写入，失败回滚
"""

from __future__ import annotations

from fastapi import APIRouter, Body

from ..core import causal, lock, trace
from ..models import CommitRequest

router = APIRouter(tags=["commit"])


def _event_dict(payload: CommitRequest) -> dict:
    event = payload.event.model_dump()
    return event


@router.post("/commit")
def commit(payload: CommitRequest = Body(...)) -> dict:
    trace_id = trace.new_trace_id(payload.event.turn)
    holder = f"commit:{payload.draft_id or payload.world_id}"
    acquired = lock.acquire(payload.world_id, holder, lock_type="commit")
    if not acquired["acquired"]:
        return {
            "ok": False,
            "committed_event_id": None,
            "causal_edges_created": 0,
            "trace_id": trace_id,
            "blocked": ["lock_busy"],
        }
    try:
        result = causal.commit(
            payload.world_id,
            _event_dict(payload),
            draft_id=payload.draft_id,
            force=payload.force,
            trace_id=trace_id,
        )
    finally:
        lock.release(payload.world_id, holder)
    return result


@router.post("/commit/preview")
def preview(payload: CommitRequest = Body(...)) -> dict:
    """提交前预览闭合判定（不写库）。"""
    event = _event_dict(payload)
    draft = {
        "id": payload.draft_id,
        "content": event.get("content"),
        "causes": event.get("causes"),
        "effects": event.get("effects"),
        "chars": event.get("chars"),
        "turn": event.get("turn"),
        "world_id": payload.world_id,
    }
    closed, blocked = causal.is_closed(draft, payload.world_id)
    return {"closed": closed, "blocked": blocked}


@router.post("/commit/auto")
def auto_close(world_id: str = Body(..., embed=True), turn: int = Body(0, embed=True)) -> dict:
    """执行自动闭合循环。"""
    return {"ok": True, **causal.auto_close(world_id, turn)}


@router.post("/commit/revoke")
def revoke(event_id: int = Body(..., embed=True), reason: str = Body("manual", embed=True)) -> dict:
    return causal.revoke(event_id, reason)
