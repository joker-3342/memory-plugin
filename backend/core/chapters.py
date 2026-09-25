"""会话与章节（SPEC 11.6）。

1. 同世界新会话默认 summary，只继承卷摘要和未解决伏笔
2. 用户可选 full 继承全部
3. 章节切换触发摘要压缩
4. 章节关闭后只保留卷摘要
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .. import db
from ..memory import summarizer
from . import causal, trace


def create_chat(
    chat_id: str,
    world_id: str,
    inherit_mode: str = "summary",
    inherits_from: Optional[str] = None,
) -> Dict[str, Any]:
    chapter_id = f"{chat_id}:chapter_01"
    with db.tx():
        db.connection().execute(
            "INSERT OR REPLACE INTO chats (chat_id, world_id, chapter_id, inherits_from, inherit_mode, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (chat_id, world_id, chapter_id, inherits_from, inherit_mode, db.now_ms()),
        )
        db.connection().execute(
            "INSERT OR REPLACE INTO chapters (chapter_id, chat_id, world_id, title, turn_start, turn_end, status,"
            " created_at) VALUES (?,?,?,?,?,?,?,?)",
            (chapter_id, chat_id, world_id, "第1章", 0, 0, "active", db.now_ms()),
        )

    if inherit_mode == "summary":
        inherited = {"volume_summary": None, "foreshadows": []}
        latest = summarizer.latest(world_id, level="volume")
        if latest:
            inherited["volume_summary"] = latest["content"]
        inherited["foreshadows"] = [item["content"] for item in causal.unresolved_foreshadows(world_id)]
        return {"ok": True, "chat_id": chat_id, "chapter_id": chapter_id, "inherited": inherited}

    # full：全部继承（同世界共享状态，无需复制数据）
    return {
        "ok": True,
        "chat_id": chat_id,
        "chapter_id": chapter_id,
        "inherited": {"mode": "full", "world_id": world_id},
    }


def open_chapter(chat_id: str, world_id: str, title: str = "", turn_start: int = 0) -> Dict[str, Any]:
    existing = db.query(
        "SELECT chapter_id FROM chapters WHERE chat_id=? AND status='active'", (chat_id,)
    )
    index = len(existing) + 1
    chapter_id = f"{chat_id}:chapter_{index:02d}"
    db.execute(
        "INSERT OR REPLACE INTO chapters (chapter_id, chat_id, world_id, title, turn_start, turn_end, status,"
        " created_at) VALUES (?,?,?,?,?,?,?,?)",
        (chapter_id, chat_id, world_id, title or f"第{index}章", turn_start, turn_start, "active", db.now_ms()),
    )
    db.execute("UPDATE chats SET chapter_id=? WHERE chat_id=?", (chapter_id, chat_id))
    return {"ok": True, "chapter_id": chapter_id}


def close_chapter(chapter_id: str, turn_end: Optional[int] = None) -> Dict[str, Any]:
    """章节关闭 → 触发摘要压缩 → 只保留卷摘要。"""
    row = db.one("SELECT * FROM chapters WHERE chapter_id=?", (chapter_id,))
    if row is None:
        return {"ok": False, "reason": "chapter_not_found"}

    if turn_end is None:
        latest = db.one("SELECT MAX(turn) AS t FROM events WHERE world_id=?", (row["world_id"],))
        turn_end = int((latest or {}).get("t") or 0)

    compressed = summarizer.compress_chapters(row["world_id"], level="volume")
    with db.tx():
        db.connection().execute(
            "UPDATE chapters SET status='closed', turn_end=? WHERE chapter_id=?", (turn_end, chapter_id)
        )
        # 章节关闭后只保留卷摘要
        db.connection().execute(
            "DELETE FROM summaries WHERE world_id=? AND level='scene'", (row["world_id"],)
        )
    return {"ok": True, "chapter_id": chapter_id, "volume_summary": compressed["content"], "turn_end": turn_end}


def list_chapters(chat_id: Optional[str] = None, world_id: Optional[str] = None) -> list:
    if chat_id:
        return db.query("SELECT * FROM chapters WHERE chat_id=? ORDER BY created_at", (chat_id,))
    if world_id:
        return db.query("SELECT * FROM chapters WHERE world_id=? ORDER BY created_at", (world_id,))
    return db.query("SELECT * FROM chapters ORDER BY created_at DESC LIMIT 100")


def scene_close(world_id: str, turn: int = 0) -> Dict[str, Any]:
    """场景结束流程（SPEC 11.4）：draft 结算 / 摘要 / 世界推进 / 地点更新 / 伏笔检查。"""
    from . import world as world_core

    settled = causal.auto_close(world_id, turn)
    tick = world_core.advance(world_id, player_present=False, current_turn=turn)
    unknown = db.one("SELECT COUNT(*) AS n FROM foreshadows WHERE world_id=? AND status='unresolved'", (world_id,))
    return {
        "ok": True,
        "settled": settled,
        "off_screen_events": tick["off_screen_events"],
        "unresolved_foreshadows": int((unknown or {"n": 0})["n"]),
    }


def archive_npc(world_id: str, turn: int, inactivity: int = 20, importance_floor: float = 0.3) -> Dict[str, Any]:
    """NPC 生命周期（SPEC 11.7）：重要度低且长期未出现 → 归档。"""
    rows = db.query(
        "SELECT * FROM characters WHERE world_id=? AND archived=0 AND pinned=0", (world_id,)
    )
    archived = []
    for row in rows:
        last_seen = int(row.get("last_seen_turn") or 0)
        if row.get("anchor") and _anchor_importance(row) >= importance_floor:
            continue
        if turn - last_seen >= inactivity:
            db.execute("UPDATE characters SET archived=1 WHERE id=?", (row["id"],))
            archived.append(row["id"])
    return {"ok": True, "archived": archived}


def _anchor_importance(row: Dict[str, Any]) -> float:
    anchor = row.get("anchor") or ""
    return min(1.0, len(anchor) / 60.0)


def revive_npc(char_id: str) -> Dict[str, Any]:
    row = db.one("SELECT id FROM characters WHERE id=?", (char_id,))
    if row is None:
        return {"ok": False, "reason": "not_found"}
    db.execute("UPDATE characters SET archived=0 WHERE id=?", (char_id,))
    return {"ok": True, "char_id": char_id}


def pin_npc(char_id: str, pinned: bool = True) -> Dict[str, Any]:
    db.execute("UPDATE characters SET pinned=?, archived=0 WHERE id=?", (1 if pinned else 0, char_id))
    return {"ok": True, "char_id": char_id, "pinned": pinned}


def bind_worldbook(world_id: str, entries: list) -> Dict[str, Any]:
    """世界书绑定（SPEC 11.2）：条目组冲突按 priority 裁决。"""
    stored = []
    with db.tx():
        for entry in entries or []:
            entry_id = str(entry.get("wb_entry_id") or entry.get("id") or "")
            if not entry_id:
                continue
            db.connection().execute(
                "INSERT OR REPLACE INTO world_book_links (world_id, wb_entry_id, wb_keywords, inject_position,"
                " priority, auto_activate) VALUES (?,?,?,?,?,?)",
                (
                    world_id,
                    entry_id,
                    json.dumps(entry.get("wb_keywords", []), ensure_ascii=False),
                    entry.get("inject_position", "anchor_bottom"),
                    int(entry.get("priority", 0)),
                    1 if entry.get("auto_activate", True) else 0,
                ),
            )
            stored.append(entry_id)
    return {"ok": True, "world_id": world_id, "entries": stored}