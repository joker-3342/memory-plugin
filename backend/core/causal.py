"""因果闭合与三态模型（SPEC 6 / 12.6）。

draft → pending → committed；因果闭合才写死。
1. 事件必须有前因后果才能提交
2. committed 硬事实，draft 不注入
3. 因果链上下游一起注入
4. 每轮检查行为是否有因果支撑
5. 未解决伏笔常驻注入
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .. import config, db
from . import trace

DRAFT = "draft"
PENDING = "pending"
COMMITTED = "committed"
REVOKED = "revoked"
DISCARDED = "discarded"


def causal_conf() -> Dict[str, Any]:
    return dict(config.get("memory.causal", {}))


# --------------------------------------------------------------- 草稿管理


def upsert_draft(payload: Dict[str, Any]) -> Dict[str, Any]:
    draft_id = payload.get("id") or trace.new_draft_id(payload.get("turn"))
    now = db.now_ms()
    existing = db.one("SELECT * FROM draft_events WHERE id=?", (draft_id,))
    values = (
        payload.get("content", ""),
        json.dumps(payload.get("causes", []), ensure_ascii=False),
        json.dumps(payload.get("effects", []), ensure_ascii=False),
        json.dumps(payload.get("unresolved", []), ensure_ascii=False),
        payload.get("status", DRAFT),
        float(payload.get("importance", 0.5) or 0.5),
        json.dumps(payload.get("chars", []), ensure_ascii=False),
        payload.get("loc"),
        payload.get("turn"),
        payload.get("story_time"),
        payload.get("world_id"),
        now,
    )
    if existing:
        db.execute(
            "UPDATE draft_events SET content=?, causes=?, effects=?, unresolved=?, status=?, importance=?,"
            " chars=?, loc=?, turn=?, story_time=?, world_id=?, updated_at=? WHERE id=?",
            values + (draft_id,),
        )
    else:
        db.execute(
            "INSERT INTO draft_events (content, causes, effects, unresolved, status, importance, chars, loc, turn,"
            " story_time, world_id, updated_at, created_at, id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values + (now, draft_id),
        )
    return get_draft(draft_id) or {}


def get_draft(draft_id: str) -> Optional[Dict[str, Any]]:
    row = db.one("SELECT * FROM draft_events WHERE id=?", (draft_id,))
    if row is None:
        return None
    for field in ("causes", "effects", "unresolved", "chars"):
        row[field] = db.loads(row.get(field), [])
    return row


def list_drafts(world_id: Optional[str] = None, statuses: Tuple[str, ...] = (DRAFT, PENDING)) -> List[Dict[str, Any]]:
    placeholders = ",".join("?" for _ in statuses)
    if world_id:
        rows = db.query(
            f"SELECT * FROM draft_events WHERE world_id=? AND status IN ({placeholders}) ORDER BY updated_at DESC",
            (world_id, *statuses),
        )
    else:
        rows = db.query(f"SELECT * FROM draft_events WHERE status IN ({placeholders}) ORDER BY updated_at DESC", statuses)
    for row in rows:
        for field in ("causes", "effects", "unresolved", "chars"):
            row[field] = db.loads(row.get(field), [])
    return rows


# --------------------------------------------------------------- 闭合判定


def _normalise_links(links: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in links or []:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, int):
            out.append({"event_id": item})
        elif isinstance(item, str):
            if item.startswith("evt_"):
                out.append({"event_id": int(item.split("_")[-1])})
            else:
                out.append({"content": item})
    return out


def _event_state(event_id: Optional[int]) -> Optional[str]:
    if not event_id:
        return None
    row = db.one("SELECT state FROM events WHERE id=?", (event_id,))
    return row["state"] if row else None


def is_closed(draft: Dict[str, Any], world_id: Optional[str] = None) -> Tuple[bool, List[str]]:
    """闭合判定 7 条（SPEC 6）。返回 (是否闭合, 阻塞原因)。"""
    blocked: List[str] = []
    causes = _normalise_links(draft.get("causes"))
    effects = _normalise_links(draft.get("effects"))

    if not causes:
        blocked.append("no_cause")
    if not effects:
        blocked.append("no_effect")

    turn = int(draft.get("turn") or 0)
    for link in causes:
        event_id = link.get("event_id")
        if not event_id:
            continue  # 文本型前因视为未落库，跳过状态校验
        state = _event_state(event_id)
        if state != COMMITTED:
            blocked.append(f"cause_not_committed:{event_id}")
        row = db.one("SELECT turn FROM events WHERE id=?", (event_id,))
        if row and row.get("turn") is not None and int(row["turn"]) > turn:
            blocked.append(f"cause_after_effect:{event_id}")

    for link in effects:
        event_id = link.get("event_id")
        if not event_id:
            continue
        state = _event_state(event_id)
        if state not in (COMMITTED, None) and state != "not_happened":
            blocked.append(f"effect_not_settled:{event_id}")
        row = db.one("SELECT turn FROM events WHERE id=?", (event_id,))
        if row and row.get("turn") is not None and int(row["turn"]) < turn:
            blocked.append(f"effect_before_cause:{event_id}")

    if _has_cycle(draft):
        blocked.append("cycle_dependency")

    world_id = world_id or draft.get("world_id")
    if world_id:
        for char_id in draft.get("chars") or []:
            relation_ok = _char_knows_causes(char_id, causes)
            if not relation_ok:
                blocked.append(f"char_unaware:{char_id}")

    return (len(blocked) == 0, blocked)


def _has_cycle(draft: Dict[str, Any]) -> bool:
    """事件自身的因果不得形成自环。"""
    draft_id = draft.get("id")
    for link in _normalise_links(draft.get("causes")):
        if link.get("draft_id") == draft_id and draft_id:
            return True
    return False


def _char_knows_causes(char_id: str, causes: List[Dict[str, Any]]) -> bool:
    """角色认知一致：参与角色 knows 包含前因。"""
    for link in causes:
        event_id = link.get("event_id")
        if not event_id:
            continue
        row = db.one("SELECT content FROM events WHERE id=?", (event_id,))
        if not row:
            continue
        relation = db.one("SELECT knows FROM relations WHERE to_id=?", (char_id,))
        if not relation:
            continue
        knows = db.loads(relation.get("knows"), []) or []
        if row["content"] and row["content"] not in knows and link.get("content") and link["content"] not in knows:
            return False
    return True


# --------------------------------------------------------------- 提交


def commit(
    world_id: str,
    event: Dict[str, Any],
    draft_id: Optional[str] = None,
    force: bool = False,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """事务写入事件 + 因果边（失败整批回滚）。"""
    causes = _normalise_links(event.get("causes"))
    effects = _normalise_links(event.get("effects"))
    source_msg = event.get("source_msg")

    if not source_msg or not trace.is_valid_msg_id(source_msg):
        if not force:
            return {"ok": False, "blocked": ["invalid_source_msg"], "committed_event_id": None, "causal_edges_created": 0}

    if not causes or not effects:
        if not force:
            return {"ok": False, "blocked": ["no_cause" if not causes else "no_effect"], "committed_event_id": None, "causal_edges_created": 0}

    now = db.now_ms()
    edges_created = 0
    with db.tx():
        cur = db.connection().execute(
            "INSERT INTO events (world_id, turn, story_time, content, state, chars, loc, items, importance,"
            " unresolved, preconditions, source_msg, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                world_id,
                event.get("turn"),
                event.get("story_time"),
                event.get("content", ""),
                COMMITTED,
                json.dumps(event.get("chars", []), ensure_ascii=False),
                event.get("loc"),
                json.dumps(event.get("items", []), ensure_ascii=False),
                float(event.get("importance", 0.5) or 0.5),
                json.dumps(event.get("unresolved", []), ensure_ascii=False),
                json.dumps(event.get("preconditions", []), ensure_ascii=False),
                source_msg,
                now,
            ),
        )
        event_id = int(cur.lastrowid)

        for link in causes:
            cause_id = link.get("event_id")
            if cause_id:
                db.connection().execute(
                    "INSERT INTO causal_edges (cause_event_id, effect_event_id, relation, \"desc\", state)"
                    " VALUES (?,?,?,?,?)",
                    (int(cause_id), event_id, link.get("relation", "导致"), link.get("desc"), COMMITTED),
                )
                edges_created += 1
                _link_graph(int(cause_id), event_id, link.get("relation", "导致"), world_id)

        for link in effects:
            effect_id = link.get("event_id")
            if effect_id:
                db.connection().execute(
                    "INSERT INTO causal_edges (cause_event_id, effect_event_id, relation, \"desc\", state)"
                    " VALUES (?,?,?,?,?)",
                    (event_id, int(effect_id), link.get("relation", "导致"), link.get("desc"), COMMITTED),
                )
                edges_created += 1
                _link_graph(event_id, int(effect_id), link.get("relation", "导致"), world_id)

        _sync_graph(event_id, event, world_id)

        if draft_id:
            db.connection().execute(
                "UPDATE draft_events SET status=?, updated_at=? WHERE id=?",
                (COMMITTED, now, draft_id),
            )

    return {
        "ok": True,
        "committed_event_id": event_id,
        "causal_edges_created": edges_created,
        "trace_id": trace_id or trace.new_trace_id(event.get("turn")),
        "blocked": [],
    }


def _link_graph(cause_id: int, effect_id: int, relation: str, world_id: str) -> None:
    db.connection().execute(
        "INSERT OR REPLACE INTO edges (src, src_type, dst, dst_type, edge_type, weight) VALUES (?,?,?,?,?,?)",
        (f"event:{cause_id}", "event", f"event:{effect_id}", "event", "导致", 1.0),
    )


def _sync_graph(event_id: int, event: Dict[str, Any], world_id: str) -> None:
    """事件与角色/地点的图边。"""
    for char_id in event.get("chars") or []:
        db.connection().execute(
            "INSERT OR REPLACE INTO edges (src, src_type, dst, dst_type, edge_type, weight) VALUES (?,?,?,?,?,?)",
            (char_id, "character", f"event:{event_id}", "event", "参与", 1.0),
        )
    loc = event.get("loc")
    if loc:
        db.connection().execute(
            "INSERT OR REPLACE INTO edges (src, src_type, dst, dst_type, edge_type, weight) VALUES (?,?,?,?,?,?)",
            (loc, "location", f"event:{event_id}", "event", "发生", 1.0),
        )


def revoke(event_id: int, reason: str = "manual") -> Dict[str, Any]:
    """committed → revoked：因果链修正，生成 replacement 标记。"""
    row = db.one("SELECT * FROM events WHERE id=?", (event_id,))
    if row is None:
        return {"ok": False, "reason": "event_not_found"}
    with db.tx():
        db.connection().execute("UPDATE events SET state=? WHERE id=?", (REVOKED, event_id))
        db.connection().execute("UPDATE causal_edges SET state=? WHERE cause_event_id=? OR effect_event_id=?", (REVOKED, event_id, event_id))
    return {"ok": True, "event_id": event_id, "reason": reason}


def set_state(draft_id: str, status: str) -> Dict[str, Any]:
    if status not in (DRAFT, PENDING, COMMITTED, REVOKED, DISCARDED):
        return {"ok": False, "reason": "invalid_status"}
    db.execute("UPDATE draft_events SET status=?, updated_at=? WHERE id=?", (status, db.now_ms(), draft_id))
    return {"ok": True, "draft_id": draft_id, "status": status}


# --------------------------------------------------------------- 自动闭合


def auto_close(world_id: str, turn: int = 0) -> Dict[str, Any]:
    """每 N 轮或场景切换时执行（SPEC 6 自动闭合循环）。"""
    conf = causal_conf()
    auto_commit_importance = float(conf.get("auto_commit_importance", 0.8))
    stale_turns = int(conf.get("stale_turns", 20))
    discard_importance = float(conf.get("discard_importance", 0.5))

    committed: List[str] = []
    forced: List[str] = []
    discarded: List[str] = []
    promoted: List[str] = []

    for draft in list_drafts(world_id, statuses=(DRAFT, PENDING)):
        closed, blocked = is_closed(draft, world_id)
        age = max(0, turn - int(draft.get("turn") or 0))
        importance = float(draft.get("importance") or 0.5)

        if closed and draft["status"] == DRAFT:
            set_state(draft["id"], PENDING)
            promoted.append(draft["id"])
            continue

        if closed:
            result = commit(
                world_id,
                {
                    "content": draft["content"],
                    "causes": draft.get("causes", []),
                    "effects": draft.get("effects", []),
                    "chars": draft.get("chars", []),
                    "loc": draft.get("loc"),
                    "importance": importance,
                    "turn": draft.get("turn"),
                    "story_time": draft.get("story_time"),
                    "unresolved": draft.get("unresolved", []),
                    "source_msg": f"auto:{draft['id']}",
                },
                draft_id=draft["id"],
                force=True,
            )
            if result.get("ok"):
                committed.append(draft["id"])
            continue

        if age >= stale_turns:
            if importance > auto_commit_importance:
                result = commit(
                    world_id,
                    {
                        "content": draft["content"],
                        "causes": draft.get("causes", []) or [{"relation": "未知", "desc": "强制提交"}],
                        "effects": draft.get("effects", []) or [{"relation": "未知", "desc": "强制提交"}],
                        "chars": draft.get("chars", []),
                        "loc": draft.get("loc"),
                        "importance": importance,
                        "turn": draft.get("turn"),
                        "story_time": draft.get("story_time"),
                        "source_msg": f"force:{draft['id']}",
                    },
                    draft_id=draft["id"],
                    force=True,
                )
                if result.get("ok"):
                    forced.append(draft["id"])
            elif importance < discard_importance:
                set_state(draft["id"], DISCARDED)
                discarded.append(draft["id"])
            else:
                db.execute(
                    "UPDATE draft_events SET unresolved=?, updated_at=? WHERE id=?",
                    (json.dumps((draft.get("unresolved") or []) + blocked, ensure_ascii=False), db.now_ms(), draft["id"]),
                )

    return {"promoted": promoted, "committed": committed, "forced": forced, "discarded": discarded}


# --------------------------------------------------------------- 注入块


def causal_block(world_id: str, anchors: List[str], turn: int) -> str:
    """【因果链·当前相关】：committed 硬事实 + pending 软措辞 + 上下游。"""
    lines = ["【因果链·当前相关】"]
    anchor_set = set(anchors or [])
    rows = db.query(
        "SELECT * FROM events WHERE world_id=? AND state=? ORDER BY turn DESC, id DESC LIMIT 60",
        (world_id, COMMITTED),
    )
    picked = []
    for row in rows:
        chars = db.loads(row.get("chars"), []) or []
        if not anchor_set or anchor_set & set(chars) or (row.get("loc") in anchor_set):
            picked.append(row)
        if len(picked) >= 4:
            break
    if not picked:
        picked = rows[:2]

    for row in picked:
        edges = db.query(
            "SELECT * FROM causal_edges WHERE (effect_event_id=? OR cause_event_id=?) AND state=? LIMIT 6",
            (row["id"], row["id"], COMMITTED),
        )
        lines.append(f"- 硬事实 第{row.get('turn')}楼：{row['content']}")
        for edge in edges:
            if edge["effect_event_id"] == row["id"]:
                other = db.one("SELECT content, turn FROM events WHERE id=?", (edge["cause_event_id"],))
                if other:
                    lines.append(f"  前因（{edge['relation']}）：第{other.get('turn')}楼 {other['content']}")
            else:
                other = db.one("SELECT content, turn FROM events WHERE id=?", (edge["effect_event_id"],))
                if other:
                    lines.append(f"  后果（{edge['relation']}）：第{other.get('turn')}楼 {other['content']}")

    pendings = list_drafts(world_id, statuses=(PENDING,))
    for draft in pendings[:2]:
        lines.append(f"- 待确认（软措辞）：可能{draft['content']}（尚未确认）")

    drafts = list_drafts(world_id, statuses=(DRAFT,))
    if drafts and len(lines) > 1:
        lines.append(f"- 未闭合草稿 {len(drafts)} 条，不得当作已发生事实引用")

    return "\n".join(lines) if len(lines) > 1 else ""


def goals_block(world_id: str, owner: Optional[str] = None) -> str:
    """【活跃目标+伏笔】块。"""
    lines = ["【活跃目标+伏笔】"]
    goals = db.query(
        "SELECT * FROM goals WHERE status='active' AND (owner=? OR ? IS NULL) ORDER BY priority LIMIT 5",
        (owner, owner),
    )
    for goal in goals:
        clues = "、".join(db.loads(goal.get("clues"), []) or []) or "—"
        lines.append(f"- 目标[{goal.get('priority')}] {goal.get('content')}（线索：{clues}）")

    shadows = db.query(
        "SELECT * FROM foreshadows WHERE world_id=? AND status='unresolved' ORDER BY (deadline IS NULL), deadline LIMIT 5",
        (world_id,),
    )
    for item in shadows:
        lines.append(f"- 未解决伏笔：{item['content']}（预期回收：{item.get('expected_payoff') or '未定'}）")

    return "\n".join(lines) if len(lines) > 1 else ""


def unresolved_foreshadows(world_id: str) -> List[dict]:
    return db.query("SELECT * FROM foreshadows WHERE world_id=? AND status='unresolved'", (world_id,))


def causal_drift(turn: int, issue: str, severity: str = "low") -> None:
    db.execute(
        "INSERT INTO causal_drift_log (turn, issue, severity, corrected, created_at) VALUES (?,?,?,?,?)",
        (turn, issue, severity, 0, db.now_ms()),
    )


def drift_count() -> int:
    row = db.one("SELECT COUNT(*) AS n FROM causal_drift_log WHERE corrected=0")
    return int((row or {"n": 0})["n"])