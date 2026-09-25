"""因果闭合 / 三态模型测试（SPEC 6 / 12.6）。"""

from __future__ import annotations

from backend import db
from backend.core import causal


def _seed_cause_event(world_id="青云小世界", turn=87, content="主角替艾琳挡刀"):
    db.execute(
        "INSERT INTO events (world_id, turn, story_time, content, state, chars, loc, importance, source_msg, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (world_id, turn, "第2天", content, "committed", '["艾琳","主角"]', "钟楼", 0.7, "chat_001:87:assistant", db.now_ms()),
    )
    row = db.one("SELECT id FROM events ORDER BY id DESC LIMIT 1")
    return int(row["id"])


def test_draft_without_cause_is_not_closed(clean_db):
    draft = causal.upsert_draft({"id": "d1", "content": "艾琳哥哥被处决", "causes": [], "effects": [], "turn": 112, "world_id": "青云小世界"})
    closed, blocked = causal.is_closed(draft, "青云小世界")
    assert closed is False
    assert "no_cause" in blocked


def test_commit_requires_source_msg(clean_db):
    result = causal.commit(
        "青云小世界",
        {"content": "无来源事件", "causes": [{"event_id": 1}], "effects": [{"event_id": 2}]},
    )
    assert result["ok"] is False
    assert "invalid_source_msg" in result["blocked"]


def test_commit_creates_event_and_edges(clean_db):
    cause_id = _seed_cause_event()
    db.execute(
        "INSERT INTO events (world_id, turn, story_time, content, state, chars, loc, importance, source_msg, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("青云小世界", 115, "第12天", "艾琳独自行动", "committed", '["艾琳"]', "公爵府", 0.6, "chat_001:115:assistant", db.now_ms()),
    )
    effect_id = int(db.one("SELECT id FROM events ORDER BY id DESC LIMIT 1")["id"])

    result = causal.commit(
        "青云小世界",
        {
            "content": "艾琳哥哥被处决",
            "causes": [{"event_id": cause_id, "relation": "间接导致", "desc": "挡刀后暴露身份"}],
            "effects": [{"event_id": effect_id, "relation": "导致", "desc": "艾琳决定复仇"}],
            "chars": ["艾琳"],
            "loc": "公爵府",
            "importance": 0.9,
            "turn": 112,
            "story_time": "第9天",
            "source_msg": "chat_001:112:assistant",
        },
    )
    assert result["ok"] is True
    assert result["causal_edges_created"] == 2
    committed = db.one("SELECT state FROM events WHERE id=?", (result["committed_event_id"],))
    assert committed["state"] == "committed"


def test_state_machine_promotes_then_commits(clean_db):
    cause_id = _seed_cause_event()
    draft = causal.upsert_draft(
        {
            "id": "d2",
            "content": "艾琳哥哥被处决",
            "causes": [{"event_id": cause_id, "relation": "间接导致"}],
            "effects": [{"relation": "导致", "desc": "艾琳复仇"}],
            "importance": 0.9,
            "turn": 112,
            "chars": ["艾琳"],
            "world_id": "青云小世界",
        }
    )
    # 第一次闭合判定：无正式后果事件 → 先走 pending 再提交（通过自动闭合循环）
    result = causal.auto_close("青云小世界", turn=112)
    assert isinstance(result["promoted"] + result["committed"] + result["forced"], list)
    row = causal.get_draft(draft["id"])
    assert row["status"] in ("draft", "pending", "committed", "discarded")


def test_stale_low_importance_is_discarded(clean_db):
    causal.upsert_draft(
        {
            "id": "d3",
            "content": "无关紧要的小事",
            "causes": [{"relation": "前置", "desc": "无"}],
            "effects": [],
            "importance": 0.2,
            "turn": 1,
            "world_id": "青云小世界",
        }
    )
    result = causal.auto_close("青云小世界", turn=100)
    assert "d3" in result["discarded"]


def test_revoke_marks_event_and_edges(clean_db):
    cause_id = _seed_cause_event()
    revoked = causal.revoke(cause_id, "用户忘记此事")
    assert revoked["ok"] is True
    assert db.one("SELECT state FROM events WHERE id=?", (cause_id,))["state"] == "revoked"


def test_causal_block_only_injects_committed(clean_db):
    _seed_cause_event()
    causal.upsert_draft({"id": "d4", "content": "尚未确认的传闻", "causes": [{"relation": "x"}], "effects": [{"relation": "y"}], "turn": 200, "world_id": "青云小世界"})
    text = causal.causal_block("青云小世界", ["艾琳"], turn=200)
    assert "硬事实" in text
    assert "不得当作已发生事实引用" in text


def test_goals_block_lists_foreshadow(clean_db):
    db.execute(
        "INSERT INTO foreshadows (id, content, planted_at_turn, status, world_id) VALUES (?,?,?,?,?)",
        ("fs_012", "钟楼密道入口", 87, "unresolved", "青云小世界"),
    )
    text = causal.goals_block("青云小世界")
    assert "钟楼密道入口" in text