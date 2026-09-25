"""关系衰减测试（SPEC 7）。"""

from __future__ import annotations

from backend.core import relation


def test_decay_half_life(clean_db):
    # 半衰期 200 楼：经过 200 楼后应衰减到一半
    value = relation.decay_value(1.0, 200, "affection")
    assert abs(value - 0.5) < 0.01


def test_decay_no_change_when_no_turns(clean_db):
    assert relation.decay_value(0.8, 0, "trust") == 0.8


def test_decay_respects_floor(clean_db):
    value = relation.decay_value(0.5, 100000, "respect")
    assert value >= 0.1 - 1e-6


def test_effective_decay_takes_stronger(clean_db):
    meta = relation.decay_value(1.0, 100, "fear")
    story = relation.decay_value(1.0, 400, "fear")
    combined = relation.effective_decay(1.0, 100, 400, "fear")
    assert combined == max(meta, story)


def test_apply_delta_updates_dimensions_and_ledger(clean_db):
    relation.ensure("艾琳", "主角", "青云小世界")
    result = relation.apply_delta(
        "艾琳",
        "主角",
        {"trust": 0.15, "debt": 0.2},
        turn=87,
        event="主角替她挡了一刀",
        source_msg="chat_001:87:assistant",
        world_id="青云小世界",
        permanent=True,
    )
    assert result["dimensions"]["trust"] == 0.15
    stored = relation.get("艾琳", "主角")
    assert stored["ledger"][-1]["event"] == "主角替她挡了一刀"
    assert stored["anchors"][-1]["permanent"] is True


def test_delta_is_clamped(clean_db):
    relation.ensure("A", "B")
    result = relation.apply_delta("A", "B", {"trust": 5.0}, turn=1, event="异常")
    assert result["dimensions"]["trust"] <= 1.0


def test_contact_refresh_raises_value(clean_db):
    relation.ensure("A", "B")
    before = relation.get("A", "B")["dimensions"]["affection"]
    relation.touch("A", "B", turn=10, level="help")
    after = relation.get("A", "B")["dimensions"]["affection"]
    assert after > before


def test_relation_text_contains_dimensions(clean_db):
    relation.ensure("艾琳", "主角")
    relation.apply_delta("艾琳", "主角", {"trust": 0.3}, turn=5, event="合作")
    text = relation.relation_text([relation.get("艾琳", "主角")], turn=5)
    assert "【关系当前值】" in text
    assert "trust" in text


def test_anchors_do_not_decay(clean_db):
    relation.ensure("A", "B")
    relation.apply_delta("A", "B", {"debt": 0.5}, turn=1, event="救命之恩", permanent=True)
    stored = relation.get("A", "B")
    assert stored["anchors"], "锚点应永久保留"
    # debt 半衰期 9999，长期后仍接近原值
    assert relation.decay_value(0.5, 100, "debt") > 0.49