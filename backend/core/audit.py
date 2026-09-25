"""审计日志（SPEC 11.9 / 全局原则 15）：所有手动修正进 audit_log。"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from .. import db


def record(
    action: str,
    target_table: str,
    target_id: str,
    old_value: Any = None,
    new_value: Any = None,
    operator: str = "system",
) -> int:
    return db.execute(
        "INSERT INTO audit_log (action, target_table, target_id, old_value, new_value, operator, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            action,
            target_table,
            str(target_id),
            json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
            json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
            operator,
            db.now_ms(),
        ),
    )


def list_entries(limit: int = 50, target_table: Optional[str] = None) -> List[dict]:
    if target_table:
        rows = db.query(
            "SELECT * FROM audit_log WHERE target_table=? ORDER BY id DESC LIMIT ?",
            (target_table, limit),
        )
    else:
        rows = db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    for row in rows:
        row["old_value"] = db.loads(row.get("old_value"))
        row["new_value"] = db.loads(row.get("new_value"))
    return rows