"""API 层端到端测试（SPEC 任务7 / 任务9 / 交付标准）。"""

from __future__ import annotations

import json

from backend import db
from backend.core import world


def _bootstrap(client):
    client.post(
        "/world/create",
        json={
            "world_id": "测试世界",
            "type": "小千世界",
            "rules": {"power_ceiling": "金丹", "forbidden": ["仙帝"], "allowed": ["练气"]},
            "time_ratio": 1.0,
        },
    )
    client.post(
        "/admin/characters",
        json={
            "id": "艾琳",
            "world_id": "测试世界",
            "anchor": "银发红瞳，嘴毒心软",
            "knows_worlds": ["测试世界"],
            "state": {"loc": "钟楼"},
        },
    )
    client.post(
        "/admin/personality",
        json={
            "char_id": "艾琳",
            "core_traits": ["嘴毒心软", "警惕"],
            "speech_profile": {"sentence_length": "short", "vocabulary": ["哼"], "forbidden_words": ["谢谢"]},
            "taboos": ["不会主动求助"],
        },
    )


def test_root_and_health(client):
    assert client.get("/").json()["ok"] is True
    health = client.get("/health").json()
    assert health["ok"] is True


def test_create_world_and_list(client):
    client.post("/world/create", json={"world_id": "W1", "type": "小千世界", "rules": {}})
    data = client.get("/worlds").json()
    assert any(row["id"] == "W1" for row in data["worlds"])
    detail = client.get("/world/W1").json()
    assert detail["ok"] is True


def test_inject_endpoint(client):
    _bootstrap(client)
    response = client.post(
        "/inject",
        json={
            "turn": 142,
            "world_id": "测试世界",
            "scene": {"loc": "钟楼", "chars": ["艾琳"], "story_time": "第3天夜"},
            "anchors": ["艾琳", "钟楼"],
            "budget": 1300,
        },
    ).json()
    assert response["token_count"] > 0
    assert "【世界门禁】" in response["inject_text"]
    assert response["trace_id"]


def test_update_enqueues_jobs_and_processes(client):
    _bootstrap(client)
    response = client.post(
        "/update",
        json={
            "turn": 10,
            "world_id": "测试世界",
            "chat_id": "chat_001",
            "user_msg": {"id": "chat_001:10:user", "content": "我们去钟楼看看"},
            "ai_msg": {"id": "chat_001:10:assistant", "content": "艾琳被发现了，她决定独自行动。"},
            "scene": {"loc": "钟楼", "chars": ["艾琳"], "story_time": "第1天"},
            "anchors": ["艾琳", "钟楼"],
        },
    ).json()
    assert response["ok"] is True
    assert "summarize" in response["queued"]

    processed = client.post("/jobs/process", json={"limit": 20}).json()
    assert processed["ok"] is True

    status = client.get("/jobs/status").json()
    assert status["dead"] == 0
    assert status["done"] >= 1


def test_update_rejects_invalid_msg_id(client):
    _bootstrap(client)
    response = client.post(
        "/update",
        json={
            "turn": 11,
            "world_id": "测试世界",
            "chat_id": "chat_001",
            "user_msg": {"id": "bad-id", "content": "x"},
            "ai_msg": {"id": "also-bad", "content": "y"},
            "scene": {"loc": "钟楼", "chars": ["艾琳"]},
        },
    ).json()
    assert response["ok"] is False
    assert response["reason"] == "invalid_msg_id"


def test_commit_flow(client):
    _bootstrap(client)
    db.execute(
        "INSERT INTO events (world_id, turn, story_time, content, state, chars, loc, importance, source_msg, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("测试世界", 87, "第2天", "主角替艾琳挡刀", "committed", '["艾琳"]', "钟楼", 0.8, "chat_001:87:assistant", db.now_ms()),
    )
    cause_id = int(db.one("SELECT id FROM events ORDER BY id DESC LIMIT 1")["id"])

    preview = client.post(
        "/commit/preview",
        json={
            "world_id": "测试世界",
            "event": {
                "content": "艾琳哥哥被处决",
                "causes": [{"event_id": cause_id, "relation": "间接导致"}],
                "effects": [{"relation": "导致", "desc": "艾琳复仇"}],
                "turn": 112,
                "chars": ["艾琳"],
                "source_msg": "chat_001:112:assistant",
            },
        },
    ).json()
    assert "closed" in preview

    committed = client.post(
        "/commit",
        json={
            "world_id": "测试世界",
            "event": {
                "content": "艾琳哥哥被处决",
                "causes": [{"event_id": cause_id, "relation": "间接导致"}],
                "effects": [{"relation": "导致", "desc": "艾琳复仇"}],
                "turn": 112,
                "chars": ["艾琳"],
                "loc": "公爵府",
                "importance": 0.9,
                "source_msg": "chat_001:112:assistant",
            },
        },
    ).json()
    assert committed["ok"] is True
    assert committed["committed_event_id"]


def test_world_tick_endpoint(client):
    _bootstrap(client)
    response = client.post("/world/tick", json={"world_id": "测试世界", "meta_time": 1420, "player_present": False}).json()
    assert response["world_status"] == "active"
    assert isinstance(response["off_screen_events"], list)


def test_session_lock_conflict(client):
    first = client.post("/session/lock", json={"world_id": "锁世界", "holder": "s1", "lock_type": "update"}).json()
    assert first["acquired"] is True
    second = client.post("/session/lock", json={"world_id": "锁世界", "holder": "s2", "lock_type": "update"}).json()
    assert second["acquired"] is False
    released = client.post("/session/unlock", json={"world_id": "锁世界", "holder": "s1"}).json()
    assert released["released"] is True


def test_summarize_and_metrics(client):
    _bootstrap(client)
    summary = client.post(
        "/summarize",
        json={
            "world_id": "测试世界",
            "level": "scene",
            "turn_start": 1,
            "turn_end": 10,
            "messages": [{"role": "assistant", "content": "艾琳还没找到哥哥？"}],
        },
    ).json()
    assert summary["content"]
    assert isinstance(summary["unresolved"], list)

    metrics = client.get("/metrics").json()
    assert "dead_letter_count" in metrics


def test_templates_and_apply(client):
    templates = client.get("/templates").json()
    ids = [t["id"] for t in templates["templates"]]
    assert "xianxia_small" in ids

    applied = client.post("/templates/apply", json={"template_id": "xianxia_small", "world_id": "模板世界"}).json()
    assert applied["ok"] is True
    assert "钟楼" in applied["locations"]


def test_export_import_roundtrip(client):
    _bootstrap(client)
    exported = client.get("/export", params={"world_id": "测试世界"}).json()
    assert exported["schema_version"] >= 1
    assert exported["tables"]["worlds"]

    imported = client.post("/import", json={"mode": "merge", "data": exported}).json()
    assert imported["ok"] is True


def test_admin_override_and_audit(client):
    _bootstrap(client)
    result = client.post(
        "/admin/override",
        json={
            "target_table": "characters",
            "target_id": "艾琳",
            "field": "anchor",
            "value": "银发红瞳，嘴硬心软",
            "reason": "手动修正人设",
        },
    ).json()
    assert result["ok"] is True

    audit = client.get("/audit").json()
    assert audit["entries"]
    assert audit["entries"][0]["target_table"] == "characters"


def test_admin_relation_fix_creates_ledger(client):
    _bootstrap(client)
    result = client.post(
        "/admin/relation-fix",
        json={"from_id": "艾琳", "to_id": "主角", "delta": {"trust": 0.2}, "reason": "手动修正", "turn": 12},
    ).json()
    assert result["ok"] is True
    row = db.one("SELECT ledger FROM relations WHERE from_id=? AND to_id=?", ("艾琳", "主角"))
    ledger = db.loads(row["ledger"], [])
    assert ledger and ledger[-1]["manual"] is True


def test_admin_forget_marks_revoked(client):
    _bootstrap(client)
    db.execute(
        "INSERT INTO events (world_id, turn, content, state, chars, importance, source_msg, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("测试世界", 5, "未发生的事件", "committed", "[]", 0.5, "chat_001:5:assistant", db.now_ms()),
    )
    event_id = int(db.one("SELECT id FROM events ORDER BY id DESC LIMIT 1")["id"])
    result = client.post("/admin/forget", json={"event_id": event_id, "reason": "用户忘记"}).json()
    assert result["ok"] is True
    assert db.one("SELECT state FROM events WHERE id=?", (event_id,))["state"] == "revoked"


def test_drafts_endpoints(client):
    _bootstrap(client)
    created = client.post(
        "/drafts",
        json={
            "world_id": "测试世界",
            "draft": {"id": "draft_001", "content": "主角身份疑云", "causes": [{"relation": "前置"}], "effects": [], "importance": 0.7, "turn": 30},
        },
    ).json()
    assert created["ok"] is True

    listing = client.get("/debug/drafts", params={"world_id": "测试世界"}).json()
    assert listing["drafts"]

    state = client.post("/drafts/state", json={"draft_id": "draft_001", "status": "pending"}).json()
    assert state["status"] == "pending"


def test_gateway_local_memory_model(client):
    _bootstrap(client)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "memory-engine",
            "messages": [{"role": "user", "content": "艾琳在钟楼"}],
            "metadata": {"world_id": "测试世界", "turn": 20, "scene": {"loc": "钟楼", "chars": ["艾琳"]}},
        },
    ).json()
    assert response["object"] == "chat.completion"
    assert "【世界门禁】" in response["choices"][0]["message"]["content"]


def test_gateway_embeddings(client):
    response = client.post("/v1/embeddings", json={"input": ["钟楼密道"], "model": "local-hash"}).json()
    assert response["data"][0]["embedding"]
    assert len(response["data"][0]["embedding"]) == 256


def test_debug_state_and_cache_clear(client):
    _bootstrap(client)
    state = client.get("/debug/state", params={"world_id": "测试世界"}).json()
    assert state["ok"] is True
    assert client.post("/debug/cache/clear").json()["ok"] is True