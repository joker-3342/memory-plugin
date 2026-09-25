"""导入（SPEC 11.5）。

1. 导入前校验 version 兼容
2. 覆盖模式：先清空再导入
3. 合并模式：按 ID 去重，冲突以导入为准
4. 导入全程事务，失败回滚
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .. import db
from .exporter import SCHEMA_VERSION

# 表 → 主键列
PRIMARY_KEYS = {
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
    "sessions": ["session_id"],
    "chats": ["chat_id"],
    "chapters": ["chapter_id"],
    "world_book_links": ["world_id", "wb_entry_id"],
    "audit_log": ["id"],
}

ORDER = [
    "worlds",
    "chapters",
    "chats",
    "characters",
    "personality",
    "locations",
    "items",
    "goals",
    "factions",
    "info",
    "foreshadows",
    "relations",
    "events",
    "causal_edges",
    "edges",
    "summaries",
    "draft_events",
    "sessions",
    "world_book_links",
    "audit_log",
]


def import_data(mode: str, data: Dict[str, Any]) -> Dict[str, Any]:
    version = int(data.get("schema_version") or 0)
    if version > SCHEMA_VERSION:
        return {"ok": False, "reason": f"incompatible_schema:{version}>{SCHEMA_VERSION}"}
    if version < 1:
        return {"ok": False, "reason": "invalid_payload"}

    tables: Dict[str, List[Dict[str, Any]]] = data.get("tables") or {}
    if mode not in ("merge", "overwrite"):
        return {"ok": False, "reason": "invalid_mode"}

    stats: Dict[str, int] = {}
    try:
        with db.tx():
            conn = db.connection()
            if mode == "overwrite":
                for table in reversed(ORDER):
                    if table in tables:
                        conn.execute(f"DELETE FROM {table}")
            for table in ORDER:
                rows = tables.get(table)
                if not rows:
                    continue
                stats[table] = _insert_rows(conn, table, rows)
    except Exception as exc:  # 失败整批回滚
        return {"ok": False, "reason": f"import_failed:{type(exc).__name__}: {exc}", "stats": stats}

    return {"ok": True, "mode": mode, "schema_version": version, "stats": stats}


def _insert_rows(conn, table: str, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    keys = list(rows[0].keys())
    placeholders = ",".join("?" for _ in keys)
    columns = ",".join(f'"{key}"' for key in keys)
    sql = f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})"
    count = 0
    for row in rows:
        values: Tuple[Any, ...] = tuple(row.get(key) for key in keys)
        conn.execute(sql, values)
        count += 1
    return count