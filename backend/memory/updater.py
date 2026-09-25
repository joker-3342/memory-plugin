"""更新流水线（SPEC 4.2 / 10.4）。

/inject 同步，/update 异步：
1. 入队 summarize / relation_update / event_extract；
2. 队列处理失败不阻塞主对话；
3. 提取结果必须过 schema 校验，不合法丢弃；
4. 事件先进 draft，因果闭合才 committed。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from .. import config, db
from ..core import causal, personality, queue, relation, security, trace
from ..memory import summarizer, vector

# 互动强度关键词 → 接触等级
CONTACT_KEYWORDS: List[tuple] = [
    ("牺牲", "sacrifice", ("替他挡", "替她挡", "挡下", "牺牲", "舍命", "付出性命")),
    ("背叛", "betrayal", ("背叛", "出卖", "欺骗", "陷害", "下毒")),
    ("帮助", "help", ("救", "帮", "挡刀", "解围", "递", "掩护")),
    ("合作", "cooperation", ("一起", "合作", "联手", "同行", "配合")),
    ("对话", "direct_dialogue", ("说", "问", "答", "道", "喊", "骂")),
]

# 事件句特征
EVENT_MARKERS = ("被", "杀", "死", "决定", "发现", "宣布", "处决", "抓住", "逃", "封锁", "引爆", "交出", "答应", "拒绝")


def enqueue_update(payload: Dict[str, Any], jobs: Optional[List[str]] = None) -> Dict[str, Any]:
    """为一次 /update 入队任务。"""
    jobs = jobs or ["summarize", "relation_update", "event_extract"]
    job_ids: List[str] = []
    for job_type in jobs:
        job_ids.append(queue.enqueue(job_type, payload.get("world_id"), payload))
    return {"job_ids": job_ids, "queued": list(jobs)}


# --------------------------------------------------------------- 处理器


def handle_summarize(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    turn = int(payload.get("turn") or 0)
    messages = [payload.get("user_msg") or {}, payload.get("ai_msg") or {}]
    result = summarizer.summarize(
        payload.get("world_id"),
        level="scene",
        turn_start=max(0, turn - 3),
        turn_end=turn,
        messages=messages,
    )
    # 摘要进向量库
    vector.add(payload.get("world_id"), f"summary:{result['summary_id']}", result["content"], {"type": "summary"})
    return result


def _detect_contact(text: str) -> str:
    for _name, level, keywords in CONTACT_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return level
    return "same_scene"


def handle_relation_update(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    world_id = payload.get("world_id")
    turn = int(payload.get("turn") or 0)
    scene = payload.get("scene") or {}
    chars: List[str] = list(scene.get("chars") or [])
    ai_text = str((payload.get("ai_msg") or {}).get("content") or "")
    ai_msg_id = (payload.get("ai_msg") or {}).get("id")
    if not trace.is_valid_msg_id(ai_msg_id or ""):
        raise ValueError("invalid source_msg")

    level = _detect_contact(ai_text)
    updated = []
    for from_id in chars:
        for to_id in chars:
            if from_id == to_id:
                continue
            relation.touch(from_id, to_id, turn, level=level, world_id=world_id)
            updated.append(f"{from_id}->{to_id}")

    # 冲突裁决：关系变化 vs 事件账本 → 账本优先，这里只做接触刷新，不凭空改值
    if level == "betrayal":
        for from_id in chars:
            for to_id in chars:
                if from_id != to_id:
                    relation.apply_delta(
                        from_id,
                        to_id,
                        {"trust": -0.1, "hostility": 0.1},
                        turn,
                        event="疑似背叛行为",
                        source_msg=ai_msg_id,
                        world_id=world_id,
                    )
    return {"ok": True, "level": level, "updated": updated}


def extract_event_drafts(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 AI 回复中抽取事件草稿（启发式；LLM 输出需过 schema）。"""
    world_id = payload.get("world_id")
    turn = int(payload.get("turn") or 0)
    scene = payload.get("scene") or {}
    ai_msg = payload.get("ai_msg") or {}
    ai_msg_id = ai_msg.get("id")
    text = str(ai_msg.get("content") or "")

    drafts: List[Dict[str, Any]] = []
    sentences = [s.strip() for s in text.replace("\n", "。").split("。") if s.strip()]
    for sentence in sentences:
        if len(sentence) < 6 or len(sentence) > 80:
            continue
        if not any(marker in sentence for marker in EVENT_MARKERS):
            continue
        drafts.append(
            {
                "content": sentence,
                "causes": [{"relation": "前置", "desc": "由上下文推断"}],
                "effects": [{"relation": "待定", "desc": "尚未闭合"}],
                "unresolved": [],
                "status": "draft",
                "importance": 0.6,
                "chars": scene.get("chars") or [],
                "loc": scene.get("loc"),
                "turn": turn,
                "story_time": scene.get("story_time"),
                "world_id": world_id,
                "source_msg": ai_msg_id,
            }
        )
    return drafts[:5]


def handle_event_extract(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    drafts = extract_event_drafts(payload)
    stored = []
    for draft in drafts:
        valid = security.guard_extractor(
            draft,
            lambda d: isinstance(d.get("content"), str) and len(d["content"]) > 0 and trace.is_valid_msg_id(d.get("source_msg") or ""),
        )
        if not valid:
            continue
        row = causal.upsert_draft(valid)
        stored.append(row.get("id"))
        vector.add(valid["world_id"], f"draft:{row.get('id')}", valid["content"], {"type": "draft"})
    return {"ok": True, "drafts": stored}


def handle_drift_check(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    turn = int(payload.get("turn") or 0)
    text = str((payload.get("ai_msg") or {}).get("content") or "")
    results = []
    for char_id in (payload.get("scene") or {}).get("chars") or []:
        results.append(personality.check_consistency(char_id, text, turn))
    return {"ok": True, "results": results}


def handle_autoclose(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = job.get("payload") or {}
    return causal.auto_close(payload.get("world_id"), int(payload.get("turn") or 0))


HANDLERS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "summarize": handle_summarize,
    "relation_update": handle_relation_update,
    "event_extract": handle_event_extract,
    "drift_check": handle_drift_check,
    "auto_close": handle_autoclose,
}


def process_pending(limit: int = 20) -> int:
    """消费队列（可在 BackgroundTasks 或后台线程里跑）。"""
    return queue.drain(HANDLERS, limit=limit)


def update_metrics_after_job(job_type: str, ok: bool) -> None:
    db.execute(
        "INSERT INTO info (id, truth, known_by, spread_log, secrecy, world_id) VALUES (?,?,?,?,?,?)",
        (f"metric:{job_type}:{db.now_ms()}", 1, "[]", json.dumps({"ok": ok}), "low", None),
    )