"""导入导出（SPEC 11.5）。

GET  /export?world_id=X        导出完整 JSON
POST /import                   合并或覆盖
POST /export/partial           只导出指定表
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import db

EXPORT_TABLES = [
    "worlds",
    "characters",
    "personality",
    "relations",
    "locations",
    "events",
    "causal_edges",
    "edges",
    "summaries",
    "items",
    "goals",
    "info",
    "factions",
    "foreshadows",
    "draft_events",
    "chats",
    "chapters",
    "sessions",
    "world_book_links",
    "audit_log",
]

SCHEMA_VERSION = 2

# 表 → 过滤用列（世界维度）
WORLD_COLUMN = {
    "worlds": "id",
    "characters": "world_id",
    "personality": None,
    "relations": "world_id",
    "locations": "world_id",
    "events": "world_id",
    "causal_edges": None,
    "edges": None,
    "summaries": "world_id",
    "items": "world_id",
    "goals": None,
    "info": "world_id",
    "factions": "world_id",
    "foreshadows": "world_id",
    "draft_events": "world_id",
    "chats": "world_id",
    "chapters": "world_id",
    "sessions": None,
    "world_book_links": "world_id",
    "audit_log": None,
}


def export_world(world_id: str, tables: Optional[List[str]] = None) -> Dict[str, Any]:
    tables = tables or EXPORT_TABLES
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": db.now_ms(),
        "world_id": world_id,
        "tables": {},
    }
    for table in tables:
        column = WORLD_COLUMN.get(table)
        if column is None:
            if table in ("personality",):
                rows = db.query(
                    "SELECT p.* FROM personality p JOIN characters c ON c.id = p.char_id WHERE c.world_id=?",
                    (world_id,),
                )
            elif table == "edges":
                rows = db.query("SELECT * FROM edges")
            elif table == "causal_edges":
                rows = db.query(
                    "SELECT ce.* FROM causal_edges ce"
                    " JOIN events e ON e.id = ce.effect_event_id WHERE e.world_id=?",
                    (world_id,),
                )
            elif table == "sessions":
                rows = db.query(
                    "SELECT s.* FROM sessions s JOIN chats c ON c.chat_id = s.chat_id WHERE c.world_id=?",
                    (world_id,),
                )
            elif table == "audit_log":
                rows = db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT 500")
            else:
                rows = db.query(f"SELECT * FROM {table}")
        else:
            rows = db.query(f"SELECT * FROM {table} WHERE {column}=?", (world_id,))
        payload["tables"][table] = rows
    return payload


def export_partial(world_id: str, tables: List[str]) -> Dict[str, Any]:
    return export_world(world_id, tables=tables)


def export_all() -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": db.now_ms(),
        "tables": {},
    }
    for table in EXPORT_TABLES:
        payload["tables"][table] = db.query(f"SELECT * FROM {table}")
    return payload


def to_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)