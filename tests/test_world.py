"""世界状态机 / 门禁 / 防串世界测试（SPEC 5 / 12.4）。"""

from __future__ import annotations

from backend import db
from backend.core import world


def _world(clean_db):
    return world.create_world(
        "青云小世界",
        type_="小千世界",
        rules={
            "power_ceiling": "金丹",
            "time_ratio": "外界1日=此界30日",
            "death": "魂魄入轮回",
            "forbidden": ["仙帝", "主神空间"],
            "allowed": ["练气", "筑基", "金丹"],
        },
        time_ratio=30.0,
    )


def test_create_and_get_world(clean_db):
    created = _world(clean_db)
    assert created["id"] == "青云小世界"
    assert created["rules"]["power_ceiling"] == "金丹"

    fetched = world.get_world("青云小世界")
    assert fetched is not None
    assert fetched["time_ratio"] == 30.0


def test_gate_text_contains_forbidden(clean_db):
    created = _world(clean_db)
    text = world.gate_text(created)
    assert "【世界门禁】" in text
    assert "仙帝" in text
    assert "金丹" in text


def test_cross_world_detects_forbidden_term(clean_db):
    created = _world(clean_db)
    warnings = world.check_cross_world(
        created,
        {"loc": "钟楼", "chars": ["艾琳"], "story_time": "第3天"},
        ["仙帝"],
    )
    assert any(w.startswith("cross_world_term") for w in warnings)


def test_tick_generates_off_screen_events(clean_db):
    world.create_world("小世界", rules={"frozen": False}, time_ratio=1.0)
    db.execute(
        "INSERT INTO events (world_id, turn, story_time, content, state, chars, loc, importance, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("小世界", 10, "第1天", "钟楼密道被封锁", "committed", "[]", "钟楼", 0.9, db.now_ms()),
    )

    first = world.advance("小世界", meta_time=100, player_present=False, current_turn=5)
    assert first["world_status"] == "active"
    assert isinstance(first["off_screen_events"], list)


def test_frozen_world_does_not_advance(clean_db):
    world.create_world("冻结世界", rules={"frozen": True}, time_ratio=1.0)
    result = world.advance("冻结世界", meta_time=500, player_present=False, current_turn=3)
    assert result["frozen"] is True
    assert result["advanced"] is False


def test_player_present_clears_pending_events(clean_db):
    world.create_world("归位世界", rules={}, time_ratio=1.0)
    result = world.advance("归位世界", meta_time=100, player_present=True, current_turn=1)
    assert result["off_screen_events"] == []


def test_transition_records_knowledge(clean_db):
    world.create_world("A世界")
    world.create_world("B世界")
    db.execute(
        "INSERT INTO characters (id, world_id, anchor, knows_worlds, archived, version, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("主角", "A世界", "穿越者", '["A世界"]', 0, 0, db.now_ms()),
    )
    result = world.transition("A世界", "B世界", "主角", "任务传送")
    assert result["ok"] is True
    row = db.one("SELECT knows_worlds, world_id FROM characters WHERE id=?", ("主角",))
    assert "B世界" in db.loads(row["knows_worlds"], [])
    assert row["world_id"] == "B世界"