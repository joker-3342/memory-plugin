"""记忆浏览接口（给人看的）。

`GET /ui/snapshot?world_id=X` 一次性返回界面需要的**全部**数据：
世界规则、角色档案（含人格锚点）、六维关系（含账本/锚点/误解）、事件账本、
因果草稿（含闭合判定）、摘要、物品、目标、伏笔、地点、势力、统计。

为什么要有这个接口：
1. 界面渲染只需 **1 个请求**，不用发十几个；
2. 想自己写 UI 的人，只有一个数据源要接（见 frontend/UI.md）。

数据都是**原样读出**（不做加工），保证界面看到的和后端记的一致。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from .. import db
from ..core import causal, world as world_core

router = APIRouter(tags=["ui"])

MAX_ROWS = 500

_JSON_FIELDS = {
    "characters": ("state", "knows_worlds", "common_sense", "foreign_concepts", "reaction_rules"),
    "events": ("chars", "items", "unresolved", "preconditions"),
    "relations": ("dimensions", "baseline", "ledger", "anchors", "knows", "misbeliefs", "unknown", "momentum"),
    "summaries": ("unresolved",),
    "items": ("abilities", "cost", "history"),
    "goals": ("clues", "dead_ends"),
    "foreshadows": ("related_chars", "related_locs"),
    "locations": ("links",),
    "factions": ("relations", "members", "resources"),
    "worlds": ("rules", "off_screen_events"),
}


def _decode(table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    for field in _JSON_FIELDS.get(table, ()):
        if field in row:
            row[field] = db.loads(row[field], None)
    return row


def _rows(sql: str, params: tuple, table: str) -> List[Dict[str, Any]]:
    return [_decode(table, row) for row in db.query(sql, params)]


def _count(sql: str, params: tuple) -> int:
    row = db.one(sql, params)
    return int((row or {"n": 0}).get("n") or 0)


@router.get("/ui/snapshot")
def snapshot(world_id: str = Query(..., description="世界 ID"), limit: int = 100) -> Dict[str, Any]:
    """一个请求拿全世界的记忆快照。"""
    limit = max(1, min(int(limit), MAX_ROWS))

    world = world_core.get_world(world_id) or world_core.ensure_world(world_id)

    # ---- 角色 + 人格锚点（合并成一条，界面直接可用）
    characters = _rows(
        "SELECT * FROM characters WHERE world_id=?"
        " ORDER BY pinned DESC, archived ASC, last_seen_turn DESC LIMIT ?",
        (world_id, limit),
        "characters",
    )
    for char in characters:
        person = db.one("SELECT * FROM personality WHERE char_id=?", (char["id"],))
        if person:
            char["personality"] = {
                "core_traits": db.loads(person.get("core_traits"), []),
                "speech_profile": db.loads(person.get("speech_profile"), {}),
                "values": db.loads(person.get("values"), []),
                "taboos": db.loads(person.get("taboos"), []),
                "quirks": db.loads(person.get("quirks"), []),
                "decision_pattern": person.get("decision_pattern") or "",
                "appearance_immutable": db.loads(person.get("appearance_immutable"), {}),
                "drift_score": float(person.get("drift_score") or 0.0),
            }
        else:
            char["personality"] = None

    # ---- 关系（六维 + 账本 + 锚点 + 认知）
    relations = _rows(
        "SELECT * FROM relations WHERE world_id=? OR world_id IS NULL LIMIT ?",
        (world_id, limit),
        "relations",
    )

    # ---- 事件账本（committed 硬事实）+ 因果边
    events = _rows(
        "SELECT * FROM events WHERE world_id=? ORDER BY turn DESC, id DESC LIMIT ?",
        (world_id, limit),
        "events",
    )
    event_ids = [e["id"] for e in events]
    edges: List[Dict[str, Any]] = []
    if event_ids:
        placeholders = ",".join("?" for _ in event_ids)
        edges = db.query(
            f"SELECT * FROM causal_edges WHERE cause_event_id IN ({placeholders})"
            f" OR effect_event_id IN ({placeholders}) LIMIT ?",
            (*event_ids, *event_ids, limit * 2),
        )

    # ---- 因果草稿（带闭合判定，界面直接显示「能不能提交」）
    drafts = causal.list_drafts(world_id, statuses=("draft", "pending"))
    for draft in drafts:
        closed, blocked = causal.is_closed(draft, world_id)
        draft["closed"] = closed
        draft["blocked"] = blocked

    # ---- 其余记忆
    summaries = _rows(
        "SELECT * FROM summaries WHERE world_id=? ORDER BY id DESC LIMIT ?", (world_id, limit), "summaries"
    )
    items = _rows("SELECT * FROM items WHERE world_id=? LIMIT ?", (world_id, limit), "items")
    goals = _rows("SELECT * FROM goals LIMIT ?", (limit,), "goals")
    foreshadows = _rows(
        "SELECT * FROM foreshadows WHERE world_id=? ORDER BY (deadline IS NULL), deadline LIMIT ?",
        (world_id, limit),
        "foreshadows",
    )
    locations = _rows("SELECT * FROM locations WHERE world_id=? LIMIT ?", (world_id, limit), "locations")
    factions = _rows("SELECT * FROM factions WHERE world_id=? LIMIT ?", (world_id, limit), "factions")

    stats = {
        "characters": _count("SELECT COUNT(*) AS n FROM characters WHERE world_id=?", (world_id,)),
        "characters_active": _count(
            "SELECT COUNT(*) AS n FROM characters WHERE world_id=? AND archived=0", (world_id,)
        ),
        "relations": len(relations),
        "events_committed": _count(
            "SELECT COUNT(*) AS n FROM events WHERE world_id=? AND state='committed'", (world_id,)
        ),
        "events_revoked": _count("SELECT COUNT(*) AS n FROM events WHERE world_id=? AND state='revoked'", (world_id,)),
        "drafts_open": len(drafts),
        "drafts_ready": len([d for d in drafts if d.get("closed")]),
        "summaries": _count("SELECT COUNT(*) AS n FROM summaries WHERE world_id=?", (world_id,)),
        "foreshadows_unresolved": _count(
            "SELECT COUNT(*) AS n FROM foreshadows WHERE world_id=? AND status='unresolved'", (world_id,)
        ),
        "items": _count("SELECT COUNT(*) AS n FROM items WHERE world_id=?", (world_id,)),
        "locations": _count("SELECT COUNT(*) AS n FROM locations WHERE world_id=?", (world_id,)),
        "goals_active": _count("SELECT COUNT(*) AS n FROM goals WHERE status='active'", ()),
        "edges": len(edges),
    }

    return {
        "ok": True,
        "world_id": world_id,
        "world": world,
        "stats": stats,
        "characters": characters,
        "relations": relations,
        "events": events,
        "causal_edges": edges,
        "drafts": drafts,
        "summaries": summaries,
        "items": items,
        "goals": goals,
        "foreshadows": foreshadows,
        "locations": locations,
        "factions": factions,
        "generated_at": db.now_ms(),
    }