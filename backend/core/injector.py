"""注入拼装（SPEC 9 / 全局原则）。

固定顺序：
【世界门禁】【当前场景】【人格锚点·在场角色】【关系当前值】
【因果链·当前相关】【活跃目标+伏笔】【锚点图记忆】【最近摘要】【向量补充】【人格/因果警告】
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .. import config, db
from ..logging import get_logger
from . import budget, causal, graph, personality, relation, security, trace, world as world_core
from ..memory import vector as vector_memory

log = get_logger("injector")

# 简易 TTL 缓存：{key: (expire_at, value)}
_cache: Dict[str, Tuple[float, str]] = {}
_last_debug: Dict[str, Any] = {}


def _cache_get(key: str) -> Optional[str]:
    entry = _cache.get(key)
    if not entry:
        return None
    expire_at, value = entry
    if expire_at < time.time():
        _cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: str, ttl: int) -> None:
    _cache[key] = (time.time() + max(1, ttl), value)


def clear_cache() -> None:
    _cache.clear()


def _relations_for(scene_chars: List[str], world_id: str, turn: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not scene_chars:
        return rows
    for from_id in scene_chars:
        for to_id in scene_chars:
            if from_id == to_id:
                continue
            rel = relation.get(from_id, to_id)
            if rel:
                rows.append(rel)
    return rows


def _summaries_block(world_id: str, recent_summary: Optional[str]) -> str:
    lines: List[str] = []
    rows = db.query(
        "SELECT * FROM summaries WHERE world_id=? ORDER BY created_at DESC LIMIT 3",
        (world_id,),
    )
    for row in rows:
        lines.append(f"- [{row.get('level')}] 第{row.get('turn_start')}-{row.get('turn_end')}楼：{row['content']}")
    if recent_summary:
        lines.append(f"- [最近] {recent_summary}")
    if not lines:
        return ""
    return "【最近摘要】\n" + "\n".join(lines)


def _flashback_block(world_id: str, target_time: Optional[str]) -> str:
    """闪回模式只注入历史快照，不注入当前关系值。"""
    rows = db.query(
        "SELECT * FROM events WHERE world_id=? AND state='committed' AND (story_time=? OR ? IS NULL)"
        " ORDER BY turn ASC LIMIT 5",
        (world_id, target_time, target_time),
    )
    if not rows:
        return "【闪回快照】\n- 无该时间点的历史记录"
    lines = ["【闪回快照】"]
    for row in rows:
        lines.append(f"- 第{row.get('turn')}楼（{row.get('story_time')}）：{row['content']}")
    lines.append("- 注意：闪回中不得引用当前关系值与当前世界状态")
    return "\n".join(lines)


def build(req: Any) -> Dict[str, Any]:
    """构造注入文本（/inject 核心）。"""
    started = time.perf_counter()
    turn = int(getattr(req, "turn", 0) or 0)
    world_id = getattr(req, "world_id")
    scene = getattr(req, "scene", None)
    scene_dict = {
        "loc": getattr(scene, "loc", None),
        "chars": list(getattr(scene, "chars", []) or []),
        "story_time": getattr(scene, "story_time", None),
    }
    anchors: List[str] = list(getattr(req, "anchors", []) or [])
    scene_type = getattr(req, "scene_type", "present") or "present"
    speaking = list(getattr(req, "speaking", []) or [])
    trace_id = trace.new_trace_id(turn)

    world = world_core.ensure_world(world_id)
    warnings: List[str] = []

    # 防串世界校验
    warnings.extend(world_core.check_cross_world(world, scene_dict, anchors))

    # 提示注入检测（对场景文本与锚点）
    probe_text = " ".join([scene_dict.get("loc") or "", " ".join(scene_dict.get("chars") or []), " ".join(anchors)])
    for hit in security.scan(probe_text):
        warnings.append(f"prompt_injection:{hit}")

    cache_hit = False
    blocks: Dict[str, str] = {}

    # 1) 世界门禁（缓存 30s，永不裁剪）
    gate_key = f"{world_id}:world_gate"
    gate = _cache_get(gate_key)
    if gate is None:
        gate = world_core.gate_text(world)
        _cache_put(gate_key, gate, int(budget.cache_ttl().get("world_gate", 30)))
    else:
        cache_hit = True
    blocks["world_gate"] = gate

    # 2) 当前场景（缓存 5s）
    scene_key = f"{world_id}:scene:{turn}:{scene_dict.get('loc')}"
    scene_text = _cache_get(scene_key)
    if scene_text is None:
        scene_text = world_core.scene_text(world, scene_dict, scene_type)
        _cache_put(scene_key, scene_text, int(budget.cache_ttl().get("scene", 5)))
    else:
        cache_hit = True
    blocks["scene"] = scene_text

    if scene_type == "flashback":
        # 闪回：不注入当前关系值，只注入历史快照
        blocks["personality"] = personality.block(scene_dict["chars"], speaking)
        blocks["causal"] = _flashback_block(world_id, getattr(req, "flashback_target_time", None))
        blocks["goals"] = ""
        blocks["anchor_graph"] = ""
    else:
        blocks["personality"] = personality.block(scene_dict["chars"], speaking)

        rel_key = f"{world_id}:relations:{turn}:{','.join(scene_dict['chars'])}"
        rel_text = _cache_get(rel_key)
        if rel_text is None:
            rows = _relations_for(scene_dict["chars"], world_id, turn)
            rel_text = relation.relation_text(rows, turn)
            _cache_put(rel_key, rel_text, int(budget.cache_ttl().get("relations", 10)))
        else:
            cache_hit = True
        blocks["relations"] = rel_text

        blocks["causal"] = causal.causal_block(world_id, anchors, turn)
        blocks["goals"] = causal.goals_block(world_id, scene_dict["chars"][0] if scene_dict["chars"] else None)
        blocks["anchor_graph"] = graph.graph_text(world_id, anchors, turn)

    blocks["summaries"] = _summaries_block(world_id, getattr(req, "recent_summary", None))
    blocks["vector"] = vector_memory.search_text(world_id, anchors or scene_dict["chars"], top_k=int(config.get("vector.top_k", 3)))

    # 10) 人格/因果警告
    warn_lines = []
    correction = personality.correction_block(scene_dict["chars"])
    if correction:
        warn_lines.append(correction)
    if warnings:
        warn_lines.append("【人格/因果警告】\n" + "\n".join(f"- {w}" for w in warnings))
    blocks["warnings"] = "\n".join(warn_lines)

    # 预算裁剪 + 拼装
    hard = budget.hard_limit()
    budget_value = int(getattr(req, "budget", None) or budget.total_budget())
    kept, dropped, reason = budget.select_by_priority(blocks, budget_value, hard)
    inject_text = budget.assemble(kept)
    token_count = budget.used_tokens(inject_text)

    # 超过硬上限时兜底（世界门禁永不裁剪）
    if token_count > hard:
        inject_text = budget.trim_text(inject_text, hard)
        token_count = budget.used_tokens(inject_text)
        reason = reason or "hard_limit"

    latency_ms = int((time.perf_counter() - started) * 1000)
    debug = {
        "blocks": list(kept.keys()),
        "dropped": dropped,
        "reason": reason,
        "warnings": warnings,
    }
    _last_debug.clear()
    _last_debug.update(
        {
            "turn": turn,
            "world_id": world_id,
            "trace_id": trace_id,
            "blocks": {k: budget.estimate_tokens(v) for k, v in kept.items()},
            "token_count": token_count,
            "dropped": dropped,
            "warnings": warnings,
            "cache_hit": cache_hit,
            "latency_ms": latency_ms,
        }
    )

    # 结构化日志（SPEC 12.1：token_count / latency_ms / cache_hit / dropped / warnings）
    log.info(
        "inject_built",
        world_id=world_id,
        turn=turn,
        token_count=token_count,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        blocks=debug["blocks"],
        dropped=dropped,
        reason=reason or None,
        warning_count=len(warnings),
    )
    if warnings:
        log.warning("inject_warnings", world_id=world_id, turn=turn, detail=warnings)

    return {
        "inject_text": inject_text,
        "token_count": token_count,
        "cache_hit": cache_hit,
        "latency_ms": latency_ms,
        "trace_id": trace_id,
        "debug": debug,
    }


def last_debug() -> Dict[str, Any]:
    return dict(_last_debug)