"""并发锁（SPEC 10.1）。

1. /inject 遇写锁 → 用上次快照返回，不等待
2. /update /commit /tick 必须抢锁，抢不到排队
3. 所有写操作带 version，冲突拒绝
4. 锁超时自动释放，进 conflict_log
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import config, db
from ..logging import get_logger

log = get_logger("lock")


def _row(world_id: str) -> Optional[Dict[str, Any]]:
    return db.one("SELECT * FROM session_locks WHERE world_id=?", (world_id,))


def is_locked(world_id: str) -> bool:
    row = _row(world_id)
    if not row:
        return False
    return row["acquired_at"] + row["timeout_ms"] > db.now_ms()


def current_version(world_id: str) -> int:
    row = _row(world_id)
    return int(row["version"]) if row else 0


def acquire(
    world_id: str,
    holder: str,
    lock_type: str = "update",
    timeout_ms: Optional[int] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """抢锁；超时自动释放覆盖；冲突写 conflict_log。"""
    timeout = int(timeout_ms or config.get("runtime.lock_timeout_ms", 5000))
    now = db.now_ms()
    with db.tx():
        row = _row(world_id)
        if row is None:
            db.execute(
                "INSERT INTO session_locks (world_id, lock_type, holder, acquired_at, timeout_ms, version)"
                " VALUES (?,?,?,?,?,1)",
                (world_id, lock_type, holder, now, timeout),
            )
            return {"acquired": True, "version": 1, "holder": holder, "expired": False}

        expired = row["acquired_at"] + row["timeout_ms"] <= now
        same_holder = row["holder"] == holder
        if expired or same_holder:
            version = int(row["version"]) + 1
            db.execute(
                "UPDATE session_locks SET lock_type=?, holder=?, acquired_at=?, timeout_ms=?, version=?"
                " WHERE world_id=?",
                (lock_type, holder, now, timeout, version, world_id),
            )
            if expired and not same_holder:
                log.warning(
                    "lock_expired_released",
                    world_id=world_id,
                    stale_holder=row["holder"],
                    new_holder=holder,
                    stale_version=int(row["version"]),
                )
                db.execute(
                    "INSERT INTO conflict_log (world_id, resource, resource_id, old_version, new_version,"
                    " session_id, resolved, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        world_id,
                        "session_lock",
                        world_id,
                        int(row["version"]),
                        version,
                        session_id,
                        "auto_released_timeout",
                        now,
                    ),
                )
            return {
                "acquired": True,
                "version": version,
                "holder": holder,
                "expired": expired and not same_holder,
                "lock_type": lock_type,
            }

        db.execute(
            "INSERT INTO conflict_log (world_id, resource, resource_id, old_version, new_version,"
            " session_id, resolved, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (world_id, "session_lock", world_id, int(row["version"]), None, session_id, "pending", now),
        )
        return {
            "acquired": False,
            "version": int(row["version"]),
            "holder": row["holder"],
            "lock_type": row["lock_type"],
            "retry_after_ms": max(0, row["acquired_at"] + row["timeout_ms"] - now),
        }


def release(world_id: str, holder: str) -> Dict[str, Any]:
    with db.tx():
        row = _row(world_id)
        if row is None:
            return {"released": True, "reason": "no_lock"}
        if row["holder"] != holder:
            return {"released": False, "reason": "not_holder", "holder": row["holder"]}
        db.execute("DELETE FROM session_locks WHERE world_id=?", (world_id,))
        return {"released": True, "reason": "ok"}


def check_version(world_id: str, version: Optional[int], resource: str = "session_lock", resource_id: Optional[str] = None) -> bool:
    """所有写操作带 version，冲突拒绝。"""
    if version is None:
        return True
    current = current_version(world_id)
    if int(version) == current:
        return True
    db.execute(
        "INSERT INTO conflict_log (world_id, resource, resource_id, old_version, new_version, session_id,"
        " resolved, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (world_id, resource, resource_id or world_id, int(version), current, None, "rejected", db.now_ms()),
    )
    return False


def conflicts(limit: int = 50) -> list:
    return db.query("SELECT * FROM conflict_log ORDER BY id DESC LIMIT ?", (limit,))


def purge_expired() -> int:
    """清理已超时的锁。"""
    now = db.now_ms()
    rows = db.query("SELECT world_id, acquired_at, timeout_ms FROM session_locks")
    removed = 0
    for row in rows:
        if row["acquired_at"] + row["timeout_ms"] <= now:
            db.execute("DELETE FROM session_locks WHERE world_id=?", (row["world_id"],))
            removed += 1
    return removed