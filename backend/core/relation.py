"""关系衰减（SPEC 7）。

current = floor + (last_value - floor) * 0.5 ** (turns_since / half_life)
双轨衰减：effective_decay = max(meta_decay, story_decay)
锚点不衰减：anchors 中的事件永久保留。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import config, db

DIMENSIONS = ("trust", "affection", "respect", "fear", "debt", "hostility")

DEFAULT_BASELINE = {d: 0.0 for d in DIMENSIONS}
DEFAULT_DIMENSIONS = {d: 0.0 for d in DIMENSIONS}


def decay_table() -> Dict[str, Dict[str, float]]:
    return dict(config.get("memory.decay", {}))


def contact_levels() -> Dict[str, float]:
    return dict(config.get("memory.contact_levels", {}))


def decay_value(value: float, turns_since: int, dimension: str) -> float:
    """单维度衰减。"""
    spec = decay_table().get(dimension, {})
    half_life = float(spec.get("half_life", 200) or 200)
    floor = float(spec.get("floor", 0.0) or 0.0)
    if turns_since <= 0 or half_life <= 0:
        return round(value, 4)
    factor = 0.5 ** (turns_since / half_life)
    return round(floor + (value - floor) * factor, 4)


def effective_decay(value: float, meta_turns: int, story_turns: int, dimension: str) -> float:
    """双轨衰减取更强者（衰减更多者）。"""
    meta_value = decay_value(value, meta_turns, dimension)
    story_value = decay_value(value, story_turns, dimension)
    if dimension in ("trust", "affection", "respect", "debt"):
        return min(meta_value, story_value)
    return max(meta_value, story_value)


def get(from_id: str, to_id: str) -> Optional[Dict[str, Any]]:
    row = db.one("SELECT * FROM relations WHERE from_id=? AND to_id=?", (from_id, to_id))
    if row is None:
        return None
    for field in ("dimensions", "baseline", "ledger", "anchors", "knows", "misbeliefs", "unknown", "momentum"):
        row[field] = db.loads(row.get(field), [] if field in ("ledger", "anchors", "knows", "misbeliefs", "unknown") else {})
    return row


def ensure(from_id: str, to_id: str, world_id: Optional[str] = None) -> Dict[str, Any]:
    row = get(from_id, to_id)
    if row:
        return row
    db.execute(
        "INSERT INTO relations (from_id, to_id, dimensions, baseline, ledger, anchors, knows, misbeliefs,"
        " unknown, momentum, world_id, version) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
        (
            from_id,
            to_id,
            json.dumps(DEFAULT_DIMENSIONS, ensure_ascii=False),
            json.dumps(DEFAULT_BASELINE, ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps({d: "stable" for d in DIMENSIONS}, ensure_ascii=False),
            world_id,
        ),
    )
    return get(from_id, to_id) or {}


def current_value(relation: Dict[str, Any], turn: int, story_turn: Optional[int] = None) -> Dict[str, float]:
    """按最后接触时间计算当前关系值（含双轨衰减与接触刷新）。"""
    last_turn = int(relation.get("last_contact_turn") or 0)
    turns_since = max(0, turn - last_turn)
    story_turn = story_turn if story_turn is not None else turn
    story_since = max(0, story_turn - int(relation.get("last_contact_story_time") or 0) if str(relation.get("last_contact_story_time") or "").isdigit() else turns_since)

    dims = relation.get("dimensions") or DEFAULT_DIMENSIONS
    out: Dict[str, float] = {}
    for dim in DIMENSIONS:
        out[dim] = effective_decay(float(dims.get(dim, 0.0)), turns_since, story_since, dim)
    return out


def apply_delta(
    from_id: str,
    to_id: str,
    delta: Dict[str, float],
    turn: int,
    event: str = "",
    source_msg: Optional[str] = None,
    witnessed: bool = True,
    world_id: Optional[str] = None,
    story_time: Optional[str] = None,
    permanent: bool = False,
) -> Dict[str, Any]:
    """写一条 ledger 并更新维度值（台账优先于摘要）。"""
    relation = ensure(from_id, to_id, world_id)
    dims = dict(relation.get("dimensions") or DEFAULT_DIMENSIONS)
    ledger = list(relation.get("ledger") or [])
    anchors = list(relation.get("anchors") or [])

    for dim, value in (delta or {}).items():
        if dim in DIMENSIONS:
            dims[dim] = round(min(1.0, max(-1.0, float(dims.get(dim, 0.0)) + float(value))), 4)

    entry = {
        "turn": turn,
        "event": event,
        "delta": {k: float(v) for k, v in (delta or {}).items()},
        "source_msg": source_msg,
        "witnessed": witnessed,
        "manual": source_msg == "manual",
    }
    ledger.append(entry)
    ledger = ledger[-200:]

    if permanent:
        anchors.append({"event": event, "turn": turn, "effects": entry["delta"], "permanent": True})

    db.execute(
        "UPDATE relations SET dimensions=?, ledger=?, anchors=?, last_contact_turn=?,"
        " last_contact_story_time=?, version=version+1 WHERE from_id=? AND to_id=?",
        (
            json.dumps(dims, ensure_ascii=False),
            json.dumps(ledger, ensure_ascii=False),
            json.dumps(anchors, ensure_ascii=False),
            turn,
            story_time,
            from_id,
            to_id,
        ),
    )
    return {"dimensions": dims, "entry": entry}


def touch(from_id: str, to_id: str, turn: int, level: str = "same_scene", story_time: Optional[str] = None, world_id: Optional[str] = None) -> Dict[str, Any]:
    """接触刷新：按互动强度回补一点关系值。"""
    factor = contact_levels().get(level, 0.0)
    relation = ensure(from_id, to_id, world_id)
    dims = dict(relation.get("dimensions") or DEFAULT_DIMENSIONS)
    spec = decay_table()
    delta: Dict[str, float] = {}
    for dim in DIMENSIONS:
        refresh = float((spec.get(dim) or {}).get("refresh", 0.0) or 0.0)
        if refresh and factor:
            step = refresh * (factor / 0.03)
            before = dims.get(dim, 0.0)
            dims[dim] = round(min(1.0, max(-1.0, before + step)), 4)
            delta[dim] = round(dims[dim] - before, 4)
    db.execute(
        "UPDATE relations SET dimensions=?, last_contact_turn=?, last_contact_story_time=?, version=version+1"
        " WHERE from_id=? AND to_id=?",
        (json.dumps(dims, ensure_ascii=False), turn, story_time, from_id, to_id),
    )
    return {"dimensions": dims, "refresh": delta, "level": level}


def momentum(relation: Dict[str, Any]) -> Dict[str, str]:
    """从 ledger 尾部推断走向。"""
    ledger = relation.get("ledger") or []
    out = {d: "stable" for d in DIMENSIONS}
    for entry in ledger[-5:]:
        for dim, value in (entry.get("delta") or {}).items():
            if dim not in out:
                continue
            if float(value) > 0:
                out[dim] = "rising"
            elif float(value) < 0:
                out[dim] = "falling"
    return out


def relation_text(rows: List[Dict[str, Any]], turn: int) -> str:
    """【关系当前值】块。"""
    if not rows:
        return ""
    limit = int(config.get("memory.budget.max_per_category", {}).get("relations", 5))
    lines = ["【关系当前值】"]
    for row in rows[:limit]:
        relation = row if "dimensions" in row else get(row["from_id"], row["to_id"]) or {}
        dims = current_value(relation, turn)
        pairs = " ".join(f"{k}={dims.get(k, 0.0):.2f}" for k in ("trust", "affection", "respect", "hostility", "fear", "debt"))
        lines.append(f"- {relation.get('from_id')} → {relation.get('to_id')}：{pairs}")
    return "\n".join(lines)