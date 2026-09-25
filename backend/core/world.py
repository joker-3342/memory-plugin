"""世界状态机 + 世界门禁 + 防串世界（SPEC 5.World / 12.4）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import db

DEFAULT_RULES = {
    "power_ceiling": "金丹",
    "time_ratio": "1:1",
    "death": "魂魄入轮回",
    "forbidden": [],
    "allowed": [],
}

META_TICKS_PER_DAY = 100  # meta_time 每 100 单位推进 1 个"故事日"基准


def create_world(
    world_id: str,
    type_: str = "小千世界",
    rules: Optional[Dict[str, Any]] = None,
    parent_id: Optional[str] = None,
    layer: int = 1,
    time_ratio: float = 1.0,
    player_present: bool = True,
) -> Dict[str, Any]:
    payload = {**DEFAULT_RULES, **(rules or {})}
    existing = db.one("SELECT id FROM worlds WHERE id=?", (world_id,))
    now = db.now_ms()
    if existing:
        db.execute(
            "UPDATE worlds SET type=?, parent_id=?, layer=?, rules=?, time_ratio=?, version=version+1 WHERE id=?",
            (type_, parent_id, layer, json.dumps(payload, ensure_ascii=False), time_ratio, world_id),
        )
    else:
        db.execute(
            "INSERT INTO worlds (id, type, parent_id, layer, rules, status, player_present, time_ratio,"
            " off_screen_events, meta_time, version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                world_id,
                type_,
                parent_id,
                layer,
                json.dumps(payload, ensure_ascii=False),
                "active",
                1 if player_present else 0,
                time_ratio,
                json.dumps([], ensure_ascii=False),
                0,
                0,
                now,
            ),
        )
    return get_world(world_id) or {}


def get_world(world_id: str) -> Optional[Dict[str, Any]]:
    row = db.one("SELECT * FROM worlds WHERE id=?", (world_id,))
    if row is None:
        return None
    row["rules"] = db.loads(row.get("rules"), {})
    row["off_screen_events"] = db.loads(row.get("off_screen_events"), [])
    row["player_present"] = bool(row["player_present"])
    return row


def ensure_world(world_id: str) -> Dict[str, Any]:
    world = get_world(world_id)
    if world is None:
        world = create_world(world_id)
    return world


def is_frozen(world: Dict[str, Any]) -> bool:
    rules = world.get("rules") or {}
    return bool(rules.get("frozen"))


# --------------------------------------------------------------- 世界门禁


def gate_text(world: Dict[str, Any]) -> str:
    """【世界门禁】块，永不裁剪。"""
    rules = world.get("rules") or {}
    forbidden = "、".join(rules.get("forbidden", []) or []) or "无"
    allowed = "、".join(rules.get("allowed", []) or []) or "无限制"
    status = world.get("status", "active")
    frozen = "是" if is_frozen(world) else "否"
    player = "在场" if world.get("player_present") else "不在场"
    return "\n".join(
        [
            "【世界门禁】",
            f"- 世界：{world.get('id')}（{world.get('type')} · 第{world.get('layer')}层）状态：{status}",
            f"- 力量上限：{rules.get('power_ceiling', '未知')}",
            f"- 时间比例：{rules.get('time_ratio', '1:1')}（主角{player}）",
            f"- 死亡规则：{rules.get('death', '未知')}",
            f"- 禁止出现：{forbidden}",
            f"- 允许体系：{allowed}",
            f"- 时间冻结：{frozen}；主角教训：不得使用本世界之外的能力与概念",
        ]
    )


def check_cross_world(world: Dict[str, Any], scene: Dict[str, Any], anchors: List[str]) -> List[str]:
    """防串世界校验，返回警告列表。"""
    warnings: List[str] = []
    world_id = world.get("id")
    rules = world.get("rules") or {}
    forbidden = [str(x) for x in (rules.get("forbidden") or [])]
    text = " ".join([scene.get("loc") or "", " ".join(scene.get("chars") or []), " ".join(anchors or [])])
    for token in forbidden:
        if token and token in text:
            warnings.append(f"cross_world_term:{token}")

    loc = scene.get("loc")
    if loc:
        row = db.one("SELECT world_id, status FROM locations WHERE id=?", (loc,))
        if row:
            if row["world_id"] != world_id:
                warnings.append(f"location_in_other_world:{loc}")
            if row["status"] == "destroyed":
                warnings.append(f"location_destroyed:{loc}")

    for char_id in scene.get("chars") or []:
        row = db.one("SELECT world_id, knows_worlds, archived FROM characters WHERE id=?", (char_id,))
        if not row:
            continue
        if row["archived"]:
            warnings.append(f"character_archived:{char_id}")
        knows = db.loads(row.get("knows_worlds"), []) or []
        if world_id not in knows and row["world_id"] != world_id:
            warnings.append(f"character_not_in_world:{char_id}")
    return warnings


def transition(world_id: str, to_world: str, char_id: str, reason: str = "") -> Dict[str, Any]:
    """记录跨世界转移（防串世界第 6 条：必须有 world_transition 记录）。"""
    loc_id = f"transition:{char_id}:{world_id}->{to_world}"
    db.execute(
        "INSERT OR REPLACE INTO locations (id, world_id, \"desc\", status, links, last_updated) VALUES (?,?,?,?,?,?)",
        (
            loc_id,
            world_id,
            f"{char_id} 从 {world_id} 转移到 {to_world}（{reason}）",
            "active",
            json.dumps({"to_world": to_world}, ensure_ascii=False),
            str(db.now_ms()),
        ),
    )
    row = db.one("SELECT knows_worlds FROM characters WHERE id=?", (char_id,))
    if row:
        knows = db.loads(row.get("knows_worlds"), []) or []
        if to_world not in knows:
            knows.append(to_world)
        db.execute(
            "UPDATE characters SET knows_worlds=?, world_id=?, version=version+1 WHERE id=?",
            (json.dumps(knows, ensure_ascii=False), to_world, char_id),
        )
    return {"ok": True, "transition_id": loc_id}


# --------------------------------------------------------------- 时间推进


def advance(
    world_id: str,
    meta_time: Optional[int] = None,
    player_present: bool = False,
    current_turn: Optional[int] = None,
    story_time: Optional[str] = None,
) -> Dict[str, Any]:
    """世界推进（/world/tick）：主角不在场时推进 off_screen 事件。"""
    world = ensure_world(world_id)
    now = db.now_ms()
    target_meta = int(meta_time if meta_time is not None else (world.get("meta_time") or 0))
    frozen = is_frozen(world)
    ratio = float(world.get("time_ratio") or 1.0)

    if current_turn is None:
        latest = db.one("SELECT MAX(turn) AS t FROM events WHERE world_id=?", (world_id,))
        current_turn = int((latest or {}).get("t") or 0)

    left_at_turn = world.get("player_left_at_turn")
    left_at_story = world.get("player_left_at_story_time")

    if player_present:
        db.execute(
            "UPDATE worlds SET player_present=1, player_left_at_turn=NULL, player_left_at_story_time=NULL,"
            " meta_time=?, version=version+1 WHERE id=?",
            (target_meta, world_id),
        )
        fresh = get_world(world_id) or world
        return {
            "off_screen_events": [],
            "world_status": fresh.get("status", "active"),
            "local_time": local_time(fresh),
            "advanced": False,
            "frozen": frozen,
        }

    if world.get("player_present"):
        left_at_turn = current_turn
        left_at_story = story_time or (world.get("rules") or {}).get("time_ratio", "")
        db.execute(
            "UPDATE worlds SET player_present=0, player_left_at_turn=?, player_left_at_story_time=? WHERE id=?",
            (left_at_turn, story_time or str(left_at_story or ""), world_id),
        )

    events: List[str] = list(world.get("off_screen_events") or [])
    if not frozen:
        candidates = db.query(
            "SELECT id, content, turn, story_time, importance FROM events"
            " WHERE world_id=? AND state='committed' AND (turn IS NULL OR turn > ?)"
            " ORDER BY importance DESC, turn ASC LIMIT 5",
            (world_id, int(left_at_turn or 0)),
        )
        known = set(events)
        for item in candidates:
            day = _day_index(item.get("turn") or 0, ratio, world.get("meta_time") or 0, target_meta)
            line = f"第{day}天：{item['content']}"
            if line not in known:
                events.append(line)
                known.add(line)

        # 未解决伏笔按概率式规则推进（确定性：取 deadline 最近的一个）
        fs = db.query(
            "SELECT content, deadline FROM foreshadows WHERE world_id=? AND status='unresolved'"
            " ORDER BY (deadline IS NULL), deadline ASC LIMIT 1",
            (world_id,),
        )
        for item in fs:
            day = _day_index(current_turn, ratio, world.get("meta_time") or 0, target_meta)
            line = f"第{day}天：{item['content']}仍在暗处发酵"
            if line not in known:
                events.append(line)
                known.add(line)

    events = events[-20:]
    db.execute(
        "UPDATE worlds SET off_screen_events=?, meta_time=?, version=version+1 WHERE id=?",
        (json.dumps(events, ensure_ascii=False), target_meta, world_id),
    )
    fresh = get_world(world_id) or world
    return {
        "off_screen_events": events,
        "world_status": fresh.get("status", "active"),
        "local_time": local_time(fresh),
        "advanced": not frozen and target_meta != (world.get("meta_time") or 0),
        "frozen": frozen,
    }


def _day_index(turn: int, ratio: float, meta_before: int, meta_after: int) -> int:
    delta = max(0, int(meta_after) - int(meta_before))
    span = delta / META_TICKS_PER_DAY * (ratio or 1.0)
    base = max(1, int(turn))
    return max(1, int(base + span))


def local_time(world: Dict[str, Any]) -> str:
    """故事内时间文本。"""
    meta = int(world.get("meta_time") or 0)
    ratio = float(world.get("time_ratio") or 1.0)
    day = max(1, int(meta / META_TICKS_PER_DAY * (ratio or 1.0)) + 1)
    return f"第{day}天"


def scene_text(world: Dict[str, Any], scene: Dict[str, Any], scene_type: str = "present") -> str:
    """【当前场景】块。"""
    chars = "、".join(scene.get("chars") or []) or "无"
    return "\n".join(
        [
            "【当前场景】",
            f"- 地点：{scene.get('loc') or '未定'}",
            f"- 在场角色：{chars}",
            f"- 故事时间：{scene.get('story_time') or local_time(world)}",
            f"- 场景类型：{scene_type}",
        ]
    )