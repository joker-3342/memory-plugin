"""POST /inject —— 同步注入（SPEC 4.1）。

1. /inject 遇写锁 → 用上次快照返回，不等待
2. 注入同步，更新异步
3. 降级：后端超时 > 500ms 只用常驻核心（由前端兜底，这里返回已装配结果）

日志：整条链路绑定 trace_id / world_id / turn 上下文，
内部日志（含 DB 慢查询、注入块统计）都会自动带上这些字段。
"""

from __future__ import annotations

from fastapi import APIRouter, Body

from ..core import injector, lock, trace
from ..logging import get_logger, log_context
from ..models import InjectRequest

router = APIRouter(tags=["inject"])

log = get_logger("inject")

_last_snapshot: dict = {}


@router.post("/inject")
def inject(payload: InjectRequest = Body(...)) -> dict:
    world_id = payload.world_id
    trace_id = trace.new_trace_id(payload.turn)

    with log_context(trace_id=trace_id, world_id=world_id, chat_id=payload.chat_id, turn=payload.turn):
        # 遇写锁：不等待，直接用上次快照（并记一条 warning，方便排查"注入为什么是旧的"）
        if lock.is_locked(world_id) and lock.current_version(world_id) > 0:
            snapshot = _last_snapshot.get(world_id)
            if snapshot:
                log.warning("inject_served_from_snapshot", reason="write_lock_held")
                stale = dict(snapshot)
                stale["cache_hit"] = True
                stale["warnings"] = list(stale.get("warnings", [])) + ["served_from_snapshot_lock_held"]
                stale["debug"] = {**stale.get("debug", {}), "reason": "write_lock_held"}
                return stale

        result = injector.build(payload)
        # 让响应里的 trace_id 与日志上下文一致，排查时能直接对上
        result["trace_id"] = trace_id
        _last_snapshot[world_id] = result
        return result