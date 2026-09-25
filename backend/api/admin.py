"""管理接口（SPEC 4.13 / 11.9）。

1. 管理面板可编辑任意事件、关系、人格
2. 编辑带 manual_override=true，自动更新不覆盖
3. "忘记此事"按钮 → 事件标记 revoked
4. "修正关系" → 生成 ledger 条目，source=manual
5. 所有手动操作进 audit_log
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from .. import db
from ..core import audit, causal, relation
from ..models import AdminOverrideRequest

router = APIRouter(tags=["admin"])

# 可管理的表白名单：表 → 主键
TABLES: Dict[str, List[str]] = {
    "worlds": ["id"],
    "characters": ["id"],
    "personality": ["char_id"],
    "relations": ["from_id", "to_id"],
    "locations": ["id"],
    "events": ["id"],
    "causal_edges": ["id"],
    "edges": ["src", "dst", "edge_type"],
    "summaries": ["id"],
    "items": ["id"],
    "goals": ["id"],
    "info": ["id"],
    "factions": ["id"],
    "foreshadows": ["id"],
    "draft_events": ["id"],
    "chats": ["chat_id"],
    "chapters": ["chapter_id"],
    "world_book_links": ["world_id", "wb_entry_id"],
}

JSON_FIELDS = {
    "characters": ("state", "knows_worlds", "common_sense", "foreign_concepts", "reaction_rules"),
    "personality": ("core_traits", "speech_profile", "values", "taboos", "quirks", "appearance_immutable"),
    "relations": ("dimensions", "baseline", "ledger", "anchors", "knows", "misbeliefs", "unknown", "momentum"),
    "events": ("chars", "items", "unresolved", "preconditions"),
    "worlds": ("rules", "off_screen_events"),
    "locations": ("links",),
    "summaries": ("unresolved",),
    "goals": ("clues", "dead_ends"),
    "foreshadows": ("related_chars", "related_locs"),
    "draft_events": ("causes", "effects", "unresolved", "chars"),
    "items": ("abilities", "cost", "history"),
    "factions": ("relations", "members", "resources"),
    "info": ("known_by", "spread_log"),
    "world_book_links": ("wb_keywords",),
}


def _prepare(table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for field in JSON_FIELDS.get(table, ()):
        if field in out and isinstance(out[field], (dict, list)):
            out[field] = json.dumps(out[field], ensure_ascii=False)
    return out


def _decode(table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    for field in JSON_FIELDS.get(table, ()):
        if field in row:
            row[field] = db.loads(row[field], None)
    return row

@router.get("/admin/tables")
def tables() -> dict:
    return {"ok": True, "tables": list(TABLES.keys())}


# ---- 具体路径必须注册在 /admin/{table} 之前，避免被通配匹配 ----
@router.post("/admin/override")
def override(payload: AdminOverrideRequest = Body(...)) -> dict:
    """手动覆盖任意字段（支持 relations 的 "dimensions.trust" 形式）。"""
    return _override_impl(payload)


@router.post("/admin/forget")
def forget(
    event_id: int = Body(..., embed=True),
    reason: str = Body("用户忘记此事", embed=True),
    operator: str = Body("user", embed=True),
) -> dict:
    """忘记此事 → 事件标记 revoked（不注入，但保留历史）。"""
    return _forget_impl(event_id, reason, operator)


@router.post("/admin/relation-fix")
def relation_fix(
    from_id: str = Body(..., embed=True),
    to_id: str = Body(..., embed=True),
    delta: Dict[str, float] = Body(..., embed=True),
    reason: str = Body("手动修正", embed=True),
    turn: int = Body(0, embed=True),
    operator: str = Body("user", embed=True),
) -> dict:
    """修正关系 → 生成 source=manual 的 ledger 条目。"""
    return _relation_fix_impl(from_id, to_id, delta, reason, turn, operator)



@router.get("/admin/{table}")
def list_rows(table: str, limit: int = 100, world_id: Optional[str] = None) -> dict:
    if table not in TABLES:
        raise HTTPException(status_code=400, detail="unknown_table")
    if world_id and table not in ("personality", "edges", "causal_edges"):
        rows = db.query(f"SELECT * FROM {table} WHERE world_id=? LIMIT ?", (world_id, limit))
    else:
        rows = db.query(f"SELECT * FROM {table} LIMIT ?", (limit,))
    return {"ok": True, "table": table, "rows": [_decode(table, row) for row in rows]}


@router.get("/admin/{table}/item")
def get_row(table: str, key: str = Query(...)) -> dict:
    if table not in TABLES:
        raise HTTPException(status_code=400, detail="unknown_table")
    keys = TABLES[table]
    if len(keys) == 1:
        row = db.one(f"SELECT * FROM {table} WHERE {keys[0]}=?", (key,))
    else:
        parts = key.split(":", 1)
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail="composite_key_format_error")
        row = db.one(f"SELECT * FROM {table} WHERE {keys[0]}=? AND {keys[1]}=?", (parts[0], parts[1]))
    if row is None:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "row": _decode(table, row)}


@router.post("/admin/{table}")
def upsert_row(table: str, row: Dict[str, Any] = Body(...), operator: str = Query("user")) -> dict:
    """整行 upsert：body 直接是行对象，operator 走 query（避免 Body 混用导致 422）。"""
    if table not in TABLES:
        raise HTTPException(status_code=400, detail="unknown_table")
    keys = TABLES[table]
    prepared = _prepare(table, row)
    if not all(k in prepared for k in keys):
        return {"ok": False, "reason": "missing_primary_key", "required": keys}

    where = " AND ".join(f"{k}=?" for k in keys)
    params = tuple(prepared[k] for k in keys)
    existing = db.one(f"SELECT * FROM {table} WHERE {where}", params)

    columns = list(prepared.keys())
    values = [prepared[c] for c in columns]
    quoted = [f'"{c}"' for c in columns]
    if existing:
        assignments = ",".join(f"{q}=?" for q in quoted)
        with db.tx():
            db.connection().execute(f"UPDATE {table} SET {assignments} WHERE {where}", values + list(params))
    else:
        placeholders = ",".join("?" for _ in columns)
        with db.tx():
            db.connection().execute(
                f"INSERT INTO {table} ({','.join(quoted)}) VALUES ({placeholders})", values
            )
    audit.record("admin_upsert", table, ":".join(str(p) for p in params), existing, prepared, operator)
    return {"ok": True, "table": table, "created": existing is None}


@router.delete("/admin/{table}")
def delete_row(table: str, key: str = Query(...), operator: str = "user") -> dict:
    if table not in TABLES:
        raise HTTPException(status_code=400, detail="unknown_table")
    keys = TABLES[table]
    if len(keys) == 1:
        where, params = f"{keys[0]}=?", (key,)
    else:
        parts = key.split(":", 1)
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail="composite_key_format_error")
        where, params = f"{keys[0]}=? AND {keys[1]}=?", (parts[0], parts[1])
    existing = db.one(f"SELECT * FROM {table} WHERE {where}", params)
    if existing is None:
        return {"ok": False, "reason": "not_found"}
    with db.tx():
        db.connection().execute(f"DELETE FROM {table} WHERE {where}", params)
    audit.record("admin_delete", table, key, existing, None, operator)
    return {"ok": True, "table": table}


def _override_impl(payload: AdminOverrideRequest) -> dict:
    table = payload.target_table
    if table not in TABLES:
        return {"ok": False, "reason": "unknown_table"}

    if table == "relations" and ":" in payload.target_id:
        from_id, to_id = payload.target_id.split(":", 1)
        relation.ensure(from_id, to_id)
        if payload.field.startswith("dimensions."):
            dim = payload.field.split(".", 1)[1]
            relation.apply_delta(
                from_id,
                to_id,
                {dim: float(payload.value)},
                turn=0,
                event=payload.reason,
                source_msg="manual",
                permanent=True,
            )
        else:
            db.execute(
                f'UPDATE relations SET "{payload.field}"=? WHERE from_id=? AND to_id=?',
                (payload.value, from_id, to_id),
            )
        audit.record("manual_override", table, payload.target_id, None, {payload.field: payload.value}, payload.operator)
        return {"ok": True, "table": table, "field": payload.field}

    keys = TABLES[table]
    if len(keys) == 1:
        where, params = f"{keys[0]}=?", (payload.target_id,)
    else:
        parts = payload.target_id.split(":", 1)
        where, params = f"{keys[0]}=? AND {keys[1]}=?", (parts[0], parts[1])
    existing = db.one(f"SELECT * FROM {table} WHERE {where}", params)
    if existing is None:
        return {"ok": False, "reason": "not_found"}

    if payload.field in JSON_FIELDS.get(table, ()):
        new_value = payload.value
        db.execute(f'UPDATE {table} SET "{payload.field}"=? WHERE {where}', (db.dumps(new_value),) + params)
    else:
        db.execute(f'UPDATE {table} SET "{payload.field}"=? WHERE {where}', (payload.value,) + params)

    audit.record("manual_override", table, payload.target_id, existing, {payload.field: payload.value}, payload.operator)
    return {"ok": True, "table": table, "field": payload.field}


def _forget_impl(event_id: int, reason: str, operator: str) -> dict:
    """忘记此事 → 事件标记 revoked（不注入，但保留历史）。"""
    result = causal.revoke(event_id, reason)
    if result.get("ok"):
        audit.record("forget_event", "events", event_id, None, {"reason": reason}, operator)
    return result


def _relation_fix_impl(
    from_id: str, to_id: str, delta: Dict[str, float], reason: str, turn: int, operator: str
) -> dict:
    """修正关系 → 生成 source=manual 的 ledger 条目。"""
    result = relation.apply_delta(
        from_id, to_id, delta, turn=turn, event=reason, source_msg="manual", permanent=True
    )
    audit.record("relation_fix", "relations", f"{from_id}:{to_id}", None, {"delta": delta, "reason": reason}, operator)
    return {"ok": True, **result}


# --------------------------------------------------------------- 草稿同步


@router.get("/drafts")
def list_drafts(world_id: str, chat_id: Optional[str] = None) -> dict:
    rows = causal.list_drafts(world_id)
    if chat_id:
        rows = [row for row in rows if (row.get("chat_id") in (None, chat_id))]
    return {"ok": True, "drafts": rows}


@router.post("/drafts")
def upsert_draft(payload: Dict[str, Any] = Body(...)) -> dict:
    """前端 localStorage 草稿同步（SPEC 11.3）；冲突以 updated_at 最新为准。"""
    draft = payload.get("draft") or payload
    world_id = payload.get("world_id") or draft.get("world_id")
    if not world_id:
        return {"ok": False, "reason": "world_id_required"}
    draft["world_id"] = world_id
    existing = db.one("SELECT updated_at FROM draft_events WHERE id=?", (draft.get("id"),)) if draft.get("id") else None
    if existing and draft.get("updated_at") and int(draft["updated_at"]) < int(existing["updated_at"]):
        return {"ok": True, "skipped": True, "reason": "stale_local_copy"}
    row = causal.upsert_draft(draft)
    return {"ok": True, "draft": row}


@router.post("/drafts/state")
def set_state(draft_id: str = Body(..., embed=True), status: str = Body(..., embed=True)) -> dict:
    return causal.set_state(draft_id, status)


@router.get("/drafts/local")
def local_drafts(chat_id: str) -> dict:
    """返回给前端写入 localStorage 的草稿快照。"""
    rows = db.query("SELECT * FROM draft_events ORDER BY updated_at DESC LIMIT 200")
    return {
        "ok": True,
        "chat_id": chat_id,
        "version": 1,
        "drafts": [_decode("draft_events", row) for row in rows],
        "last_sync": db.now_ms(),
    }