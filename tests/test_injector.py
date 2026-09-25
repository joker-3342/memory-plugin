"""注入拼装 / 预算裁剪 / 人格锚点 测试（SPEC 6 / 9 / 12.5）。"""

from __future__ import annotations

import json

from backend import db
from backend.core import budget, injector, personality, world
from backend.models import InjectRequest, Scene


def _seed_world(world_id="青云小世界"):
    return world.create_world(
        world_id,
        rules={
            "power_ceiling": "金丹",
            "forbidden": ["仙帝", "主神空间"],
            "allowed": ["练气", "筑基"],
        },
    )


def _seed_character(char_id="艾琳", world_id="青云小世界"):
    db.execute(
        "INSERT INTO characters (id, world_id, anchor, knows_worlds, archived, version, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (char_id, world_id, "银发红瞳，美得锋利，嘴毒心软", json.dumps([world_id], ensure_ascii=False), 0, 0, db.now_ms()),
    )
    personality.upsert(
        char_id,
        {
            "core_traits": ["嘴毒心软", "警惕", "护短"],
            "speech_profile": {
                "sentence_length": "short",
                "vocabulary": ["哼", "少废话", "随便你"],
                "forbidden_words": ["谢谢", "拜托"],
            },
            "taboos": ["不会主动求助"],
            "appearance_immutable": {"face": "银发红瞳", "distinctive": "左眉旧疤"},
        },
    )


def test_token_estimate_reasonable(clean_db):
    assert budget.estimate_tokens("") == 0
    assert budget.estimate_tokens("你好") > 0
    assert budget.estimate_tokens("hello world") > 0


def test_inject_contains_core_blocks(clean_db):
    _seed_world()
    _seed_character()

    request = InjectRequest(
        turn=142,
        world_id="青云小世界",
        scene=Scene(loc="钟楼", chars=["艾琳", "主角"], story_time="第3天夜"),
        anchors=["艾琳", "钟楼"],
        budget=1300,
    )
    result = injector.build(request)
    assert result["token_count"] > 0
    assert result["token_count"] <= 1500
    assert "【世界门禁】" in result["inject_text"]
    assert "【当前场景】" in result["inject_text"]
    assert "【人格锚点·在场角色】" in result["inject_text"]
    assert result["debug"]["blocks"]


def test_world_gate_never_trimmed(clean_db):
    _seed_world()
    request = InjectRequest(turn=1, world_id="青云小世界", budget=60, scene=Scene(loc="钟楼", chars=["艾琳"]))
    result = injector.build(request)
    assert "【世界门禁】" in result["inject_text"]


def test_budget_priority_drops_low_priority_first(clean_db):
    blocks = {
        "world_gate": "【世界门禁】\n" + "禁" * 200,
        "scene": "【当前场景】\n" + "景" * 200,
        "vector": "【向量补充】\n" + "向" * 200,
        "summaries": "【最近摘要】\n" + "摘" * 200,
    }
    kept, dropped, reason = budget.select_by_priority(blocks, budget=200, hard=400)
    assert "world_gate" in kept
    assert dropped
    assert reason in ("budget_exceeded", "hard_limit")


def test_personality_consistency_detects_forbidden_word(clean_db):
    _seed_world()
    _seed_character()
    result = personality.check_consistency("艾琳", "谢谢你，拜托你了。", turn=10)
    issues = [item["issue"] for item in result["issues"]]
    assert any(i.startswith("forbidden_word") for i in issues)
    assert result["drift_score"] > 0


def test_drift_warning_block_when_score_high(clean_db):
    _seed_character()
    db.execute("UPDATE personality SET drift_score=0.9 WHERE char_id=?", ("艾琳",))
    block = personality.warning_block()
    assert "人设漂移" in block


def test_flashback_skips_current_relations(clean_db):
    _seed_world()
    _seed_character()
    request = InjectRequest(
        turn=50,
        world_id="青云小世界",
        scene=Scene(loc="钟楼", chars=["艾琳"]),
        anchors=["艾琳"],
        scene_type="flashback",
        flashback_target_time="第1天",
    )
    result = injector.build(request)
    assert "【闪回快照】" in result["inject_text"]
    assert "【关系当前值】" not in result["inject_text"]


def test_cross_world_term_produces_warning(clean_db):
    _seed_world()
    request = InjectRequest(
        turn=1,
        world_id="青云小世界",
        scene=Scene(loc="仙帝殿", chars=["艾琳"]),
        anchors=["仙帝"],
    )
    result = injector.build(request)
    assert any("cross_world_term" in w for w in result["debug"]["warnings"])


def test_second_call_hits_cache(clean_db):
    _seed_world()
    _seed_character()
    request = InjectRequest(turn=7, world_id="青云小世界", scene=Scene(loc="钟楼", chars=["艾琳"]), anchors=["艾琳"])
    injector.build(request)
    second = injector.build(request)
    assert second["cache_hit"] is True


def test_last_debug_recorded(clean_db):
    _seed_world()
    injector.build(InjectRequest(turn=3, world_id="青云小世界", scene=Scene(loc="钟楼", chars=[]), anchors=[]))
    debug = injector.last_debug()
    assert debug["turn"] == 3
    assert "blocks" in debug