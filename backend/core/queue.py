"""任务队列（SPEC 10.4）。

1. 指数退避：1s / 4s / 16s
2. 超过 max_retries → dead_letter
3. 失败任务不阻塞主对话
4. 调试面板显示 dead_letter
5. 支持手动重放 dead_letter

日志：每次执行都会记录 `job_start` / `job_done` / `job_slow` /
`job_retry` / `job_dead_letter`，并自动带上 world_id / turn 上下文。
失败一定带完整堆栈 —— 想知道"哪里出了 bug"，直接看 `data/logs/error.log`。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from .. import config, db
from ..logging import get_logger, log_context
from . import trace

log = get_logger("queue")

BACKOFF_MS = [1000, 4000, 16000]


def _slow_limit() -> int:
    return int(config.get("logging.slow_ms", 200) or 0)


def enqueue(job_type: str, world_id: str, payload: Optional[Dict[str, Any]] = None, max_retries: int = 3) -> str:
    job_id = trace.new_job_id()
    now = db.now_ms()
    db.execute(
        "INSERT INTO job_queue (id, job_type, world_id, payload, status, retries, max_retries, next_retry_at,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (job_id, job_type, world_id, json.dumps(payload or {}, ensure_ascii=False), "pending", 0, max_retries, now, now),
    )
    log.debug("job_enqueued", job_id=job_id, job_type=job_type, world_id=world_id)
    return job_id


def claim_next() -> Optional[Dict[str, Any]]:
    """取一个到期任务并标记 running。"""
    now = db.now_ms()
    with db.tx():
        row = db.one(
            "SELECT * FROM job_queue WHERE status='pending' AND (next_retry_at IS NULL OR next_retry_at<=?)"
            " ORDER BY created_at LIMIT 1",
            (now,),
        )
        if row is None:
            return None
        db.execute("UPDATE job_queue SET status='running', started_at=? WHERE id=?", (now, row["id"]))
        row["status"] = "running"
        row["payload"] = db.loads(row.get("payload"), {})
        return row


def complete(job_id: str) -> None:
    db.execute(
        "UPDATE job_queue SET status='done', finished_at=?, error=NULL WHERE id=?",
        (db.now_ms(), job_id),
    )


def fail(job_id: str, error: str, job_type: Optional[str] = None, payload: Any = None) -> Dict[str, Any]:
    """失败 → 指数退避重试；超限 → 死信。"""
    row = db.one("SELECT * FROM job_queue WHERE id=?", (job_id,))
    if row is None:
        log.warning("job_fail_unknown", job_id=job_id, error=error)
        return {"ok": False, "reason": "job_not_found"}
    retries = int(row["retries"]) + 1
    max_retries = int(row["max_retries"] or 3)
    if retries > max_retries:
        db.execute("UPDATE job_queue SET status='failed', retries=?, error=?, finished_at=? WHERE id=?",
                   (retries, error, db.now_ms(), job_id))
        db.execute(
            "INSERT INTO dead_letter (job_id, job_type, payload, error, created_at) VALUES (?,?,?,?,?)",
            (job_id, job_type or row["job_type"], payload if payload is not None else row["payload"], error, db.now_ms()),
        )
        return {"ok": False, "dead_letter": True, "retries": retries}
    delay = BACKOFF_MS[min(retries - 1, len(BACKOFF_MS) - 1)]
    next_retry_at = db.now_ms() + delay
    db.execute(
        "UPDATE job_queue SET status='pending', retries=?, next_retry_at=?, error=? WHERE id=?",
        (retries, next_retry_at, error, job_id),
    )
    return {"ok": False, "dead_letter": False, "retries": retries, "next_retry_at": next_retry_at}


def retry(job_id: str) -> Dict[str, Any]:
    row = db.one("SELECT * FROM job_queue WHERE id=?", (job_id,))
    if row is None:
        return {"ok": False, "reason": "job_not_found"}
    db.execute(
        "UPDATE job_queue SET status='pending', next_retry_at=?, error=NULL WHERE id=?",
        (db.now_ms(), job_id),
    )
    log.info("job_manual_retry", job_id=job_id, job_type=row.get("job_type"))
    return {"ok": True, "job_id": job_id}


def replay_dead_letters() -> Dict[str, Any]:
    rows = db.query("SELECT * FROM dead_letter ORDER BY id")
    replayed: List[str] = []
    for row in rows:
        new_id = enqueue(row["job_type"] or "unknown", None, db.loads(row.get("payload"), {}), max_retries=3)
        replayed.append(new_id)
        db.execute("DELETE FROM dead_letter WHERE id=?", (row["id"],))
    if replayed:
        log.info("dead_letter_replayed", count=len(replayed), job_ids=replayed[:10])
    return {"ok": True, "replayed": replayed}


def status() -> Dict[str, Any]:
    counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "dead": 0}
    for row in db.query("SELECT status, COUNT(*) AS n FROM job_queue GROUP BY status"):
        counts[row["status"]] = int(row["n"])
    counts["dead"] = int((db.one("SELECT COUNT(*) AS n FROM dead_letter") or {"n": 0})["n"])
    items = db.query("SELECT * FROM job_queue ORDER BY created_at DESC LIMIT 20")
    for item in items:
        item["payload"] = db.loads(item.get("payload"), {})
    dead = db.query("SELECT * FROM dead_letter ORDER BY id DESC LIMIT 20")
    for item in dead:
        item["payload"] = db.loads(item.get("payload"), {})
    return {**counts, "items": items, "dead_letters": dead}


def run_once(handlers: Dict[str, Callable[[Dict[str, Any]], Any]]) -> Optional[str]:
    """执行一个任务，返回 job_id（没有任务则 None）。

    所有日志自动带 world_id / turn 上下文；失败带完整堆栈。
    """
    job = claim_next()
    if job is None:
        return None

    job_id = job["id"]
    job_type = job["job_type"]
    payload = job.get("payload") or {}
    started = time.perf_counter()

    with log_context(world_id=job.get("world_id"), turn=payload.get("turn")):
        log.debug("job_start", job_id=job_id, job_type=job_type)
        handler = handlers.get(job_type)
        if handler is None:
            log.error("job_no_handler", job_id=job_id, job_type=job_type, available=sorted(handlers.keys()))
            fail(job_id, f"no handler for {job_type}")
            return job_id

        try:
            handler(job)
            cost_ms = round((time.perf_counter() - started) * 1000, 2)
            complete(job_id)
            log.info("job_done", job_id=job_id, job_type=job_type, cost_ms=cost_ms)
            limit = _slow_limit()
            if limit and cost_ms >= limit:
                log.warning("job_slow", job_id=job_id, job_type=job_type, cost_ms=cost_ms, threshold_ms=limit)
        except Exception as exc:  # 失败任务不阻塞主对话
            error = f"{type(exc).__name__}: {exc}"
            # 完整堆栈 → error.log，方便直接定位
            log.exception("job_failed", job_id=job_id, job_type=job_type, error=error)
            result = fail(job_id, error)
            if result.get("dead_letter"):
                log.error(
                    "job_dead_letter",
                    job_id=job_id,
                    job_type=job_type,
                    retries=result.get("retries"),
                    hint="重试已超限，可 POST /jobs/retry {\"replay_dead_letters\":true} 重放",
                )
            else:
                log.warning(
                    "job_retry",
                    job_id=job_id,
                    job_type=job_type,
                    retries=result.get("retries"),
                    next_retry_at=result.get("next_retry_at"),
                )
    return job_id


def drain(handlers: Dict[str, Callable[[Dict[str, Any]], Any]], limit: int = 50) -> int:
    processed = 0
    while processed < limit:
        if run_once(handlers) is None:
            break
        processed += 1
    return processed