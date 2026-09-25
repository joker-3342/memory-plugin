"""调试接口（SPEC 4.18）。"""

from __future__ import annotations

from fastapi import APIRouter, Body

from .. import db
from ..core import causal, graph, injector, lock, personality, queue, world as world_core
from ..logging import get_logger, log_path, tail, tail_errors, tail_json
from ..memory import vector

log = get_logger("debug")

router = APIRouter(tags=["debug"])


@router.get("/debug/last")
def last() -> dict:
    data = injector.last_debug()
    if not data:
        return {
            "turn": 0,
            "trace_id": "",
            "blocks": {},
            "token_count": 0,
            "dropped": [],
            "warnings": [],
            "cache_hit": False,
        }
    return {
        "turn": data.get("turn"),
        "world_id": data.get("world_id"),
        "trace_id": data.get("trace_id"),
        "blocks": data.get("blocks", {}),
        "token_count": data.get("token_count", 0),
        "dropped": data.get("dropped", []),
        "warnings": data.get("warnings", []),
        "cache_hit": data.get("cache_hit", False),
        "latency_ms": data.get("latency_ms", 0),
    }


@router.get("/debug/drafts")
def drafts(world_id: str, include_blocked: bool = True) -> dict:
    """因果预览面板数据（SPEC 13.4）。"""
    items = []
    for draft in causal.list_drafts(world_id, statuses=("draft", "pending")):
        closed, blocked = causal.is_closed(draft, world_id)
        items.append({**draft, "closed": closed, "blocked": blocked})
    return {
        "ok": True,
        "world_id": world_id,
        "open_count": len([i for i in items if not i["closed"]]),
        "drafts": items if include_blocked else [i for i in items if not i["closed"]],
    }


@router.get("/debug/state")
def state(world_id: str) -> dict:
    world = world_core.ensure_world(world_id)
    return {
        "ok": True,
        "world": world,
        "lock": {"locked": lock.is_locked(world_id), "version": lock.current_version(world_id)},
        "jobs": {k: v for k, v in queue.status().items() if k in ("pending", "running", "done", "failed", "dead")},
        "vector_backend": vector.backend_name(),
        "drift_count": personality.drift_count(),
        "causal_drift_count": causal.drift_count(),
        "edges": db.one("SELECT COUNT(*) AS n FROM edges")["n"],
    }


@router.post("/debug/cache/clear")
def clear_cache() -> dict:
    injector.clear_cache()
    graph.clean_cooldown()
    return {"ok": True}


@router.post("/debug/graph")
def graph_view(world_id: str = Body(..., embed=True), anchors: list = Body(default_factory=list, embed=True), turn: int = Body(0, embed=True)) -> dict:
    return {"ok": True, "results": graph.expand(world_id, anchors, turn)}


@router.get("/debug/logs")
def logs(lines: int = 200, source: str = "main", json_format: bool = False) -> dict:
    """读日志尾巴（排查 bug 用）。

    - `source=main` → memory-plugin.log（全量）
    - `source=errors` → error.log（只含 ERROR 以上，带堆栈）
    - `json_format=true` → 解析成对象数组，便于前端渲染
    """
    count = max(1, min(int(lines), 2000))
    path = log_path(source)
    entries = tail_json(count, source) if json_format else tail(count, source)
    return {
        "ok": True,
        "source": source,
        "path": str(path),
        "exists": path.exists(),
        "lines": len(entries),
        "entries": entries,
    }


@router.get("/debug/logs/errors")
def logs_errors(lines: int = 100) -> dict:
    """只读错误日志（带堆栈）——出 bug 时先看这里。"""
    count = max(1, min(int(lines), 1000))
    path = log_path("errors")
    return {
        "ok": True,
        "source": "errors",
        "path": str(path),
        "exists": path.exists(),
        "lines": count,
        "entries": tail_errors(count),
    }


@router.post("/debug/log-test")
def log_test(
    level: str = Body("info", embed=True),
    message: str = Body("hello from /debug/log-test", embed=True),
) -> dict:
    """往日志写一条并回显文件路径，用来确认落盘链路是通的。"""
    method = getattr(log, level.lower(), None) or log.info
    method("log_test", message=message, from_="debug_endpoint")
    path = log_path("main")
    return {"ok": True, "level": level, "path": str(path), "exists": path.exists()}