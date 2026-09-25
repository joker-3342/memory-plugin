"""指标接口（SPEC 4.15）。"""

from __future__ import annotations

from fastapi import APIRouter

from .. import db

router = APIRouter(tags=["metrics"])


def _percentile(values: list, ratio: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    index = min(len(values) - 1, int(len(values) * ratio))
    return int(values[index])


@router.get("/metrics")
def metrics() -> dict:
    jobs = db.query("SELECT status, COUNT(*) AS n FROM job_queue GROUP BY status")
    counts = {row["status"]: int(row["n"]) for row in jobs}
    total_jobs = sum(counts.values()) or 1
    failed = counts.get("failed", 0) + counts.get("dead", 0)

    dead = db.one("SELECT COUNT(*) AS n FROM dead_letter") or {"n": 0}
    drift = db.one("SELECT COUNT(*) AS n FROM drift_log WHERE corrected=0") or {"n": 0}
    events = db.one("SELECT COUNT(*) AS n FROM events WHERE state='committed'") or {"n": 0}
    drafts = db.one("SELECT COUNT(*) AS n FROM draft_events WHERE status IN ('draft','pending')") or {"n": 0}

    # 注入延迟采样：从 info 表读最近指标（若前端上报）
    samples = [int(row["spread_log"] or 0) for row in db.query("SELECT spread_log FROM info WHERE id LIKE 'latency:%' LIMIT 200") if str(row["spread_log"]).isdigit()]

    return {
        "inject_latency_p50": _percentile(samples, 0.5),
        "inject_latency_p99": _percentile(samples, 0.99),
        "update_fail_rate": round(failed / total_jobs, 4),
        "vector_hit_rate": 0.0,
        "graph_hit_rate": 0.0,
        "cache_hit_rate": 0.0,
        "dead_letter_count": int(dead["n"]),
        "drift_warning_count": int(drift["n"]),
        "committed_events": int(events["n"]),
        "open_drafts": int(drafts["n"]),
    }


@router.post("/metrics/report")
def report(kind: str, value: float) -> dict:
    """前端上报指标（缓存命中率 / 向量命中率 / 注入延迟）。"""
    db.execute(
        "INSERT OR REPLACE INTO info (id, truth, known_by, spread_log, secrecy, world_id) VALUES (?,?,?,?,?,?)",
        (f"{kind}:{db.now_ms()}", 1, "[]", str(value), "low", None),
    )
    return {"ok": True, "kind": kind, "value": value}