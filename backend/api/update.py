"""POST /update —— 异步更新（SPEC 4.2）。

1. 写操作必须抢锁，抢不到排队
2. 队列执行失败不阻塞主对话
3. 失败任务进 dead_letter
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Body

from .. import db
from ..core import lock, trace
from ..memory import updater
from ..models import UpdateRequest

router = APIRouter(tags=["update"])

REQUIRED_LOCK_ERROR = "lock_busy"


@router.post("/update")
def update(payload: UpdateRequest, background: BackgroundTasks) -> dict:
    trace_id = trace.new_trace_id(payload.turn)
    holder = f"session:{payload.chat_id}"

    acquired = lock.acquire(payload.world_id, holder, lock_type="update", session_id=payload.chat_id)
    if not acquired["acquired"]:
        # 抢不到锁 → 排队（入队后由后台消费）
        queued = updater.enqueue_update(payload.model_dump())
        background.add_task(updater.process_pending, 20)
        return {
            "ok": True,
            "job_id": queued["job_ids"][0] if queued["job_ids"] else "",
            "queued": queued["queued"],
            "trace_id": trace_id,
            "lock": "queued",
            "reason": REQUIRED_LOCK_ERROR,
        }

    try:
        # 消息 ID 校验：没有 msg_id 不入库
        for msg in (payload.user_msg, payload.ai_msg):
            if msg.id and not trace.is_valid_msg_id(msg.id):
                return {
                    "ok": False,
                    "job_id": "",
                    "queued": [],
                    "trace_id": trace_id,
                    "reason": "invalid_msg_id",
                }

        queued = updater.enqueue_update(payload.model_dump())
        background.add_task(updater.process_pending, 20)
    finally:
        lock.release(payload.world_id, holder)

    return {
        "ok": True,
        "job_id": queued["job_ids"][0] if queued["job_ids"] else "",
        "queued": queued["queued"],
        "trace_id": trace_id,
    }


@router.post("/update/sync")
def update_sync(payload: UpdateRequest = Body(...)) -> dict:
    """同步执行（调试用；正式流程请用 /update）。"""
    queued = updater.enqueue_update(payload.model_dump())
    processed = updater.process_pending(20)
    status = updater.queue.status()
    return {
        "ok": True,
        "queued": queued["queued"],
        "job_ids": queued["job_ids"],
        "processed": processed,
        "status": {"pending": status["pending"], "done": status["done"], "dead": status["dead"]},
        "db_write": db.now_ms(),
    }
