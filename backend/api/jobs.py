"""任务队列接口（SPEC 4.8 / 4.9）。"""

from __future__ import annotations

from fastapi import APIRouter, Body

from ..core import queue
from ..memory import updater
from ..models import JobRetryRequest

router = APIRouter(tags=["jobs"])


@router.get("/jobs/status")
def status() -> dict:
    data = queue.status()
    return {
        "pending": data["pending"],
        "running": data["running"],
        "done": data["done"],
        "failed": data["failed"],
        "dead": data["dead"],
        "items": data["items"],
        "dead_letters": data["dead_letters"],
    }


@router.post("/jobs/retry")
def retry(payload: JobRetryRequest = Body(...)) -> dict:
    if payload.replay_dead_letters:
        return queue.replay_dead_letters()
    if not payload.job_id:
        return {"ok": False, "reason": "job_id_required"}
    return queue.retry(payload.job_id)


@router.post("/jobs/process")
def process(limit: int = Body(20, embed=True)) -> dict:
    processed = updater.process_pending(limit)
    data = queue.status()
    return {"ok": True, "processed": processed, "pending": data["pending"], "dead": data["dead"]}