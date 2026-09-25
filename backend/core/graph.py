"""锚点图扩散（SPEC 8）。

1. 锚点命中 → 强制查图（不走向量）
2. 图扩散 1-2 跳
3. 排序：score = 锚点匹配度*0.4 + 图距离*0.3 + importance*0.2 + recency*0.1
4. 阈值过滤，取 top 3-5
5. 冷却检查：同一记忆 20 楼内不重复注入
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .. import config, db

COOLDOWN_TURNS = 20
_cooldown: Dict[str, Dict[str, int]] = {}


def graph_conf() -> Dict[str, Any]:
    return dict(config.get("memory.graph", {}))


def add_edge(src: str, src_type: str, dst: str, dst_type: str, edge_type: str, weight: float = 1.0) -> None:
    db.execute(
        "INSERT OR REPLACE INTO edges (src, src_type, dst, dst_type, edge_type, weight) VALUES (?,?,?,?,?,?)",
        (src, src_type, dst, dst_type, edge_type, weight),
    )


def neighbours(node: str, node_type: Optional[str] = None) -> List[Dict[str, Any]]:
    conf = graph_conf()
    allowed = set(conf.get("allowed_edges", []))
    forbidden = set(conf.get("forbidden_edges", []))
    if node_type:
        rows = db.query(
            "SELECT * FROM edges WHERE (src=? AND src_type=?) OR (dst=? AND dst_type=?)",
            (node, node_type, node, node_type),
        )
    else:
        rows = db.query("SELECT * FROM edges WHERE src=? OR dst=?", (node, node))
    out = []
    for row in rows:
        if allowed and row["edge_type"] not in allowed:
            continue
        if row["edge_type"] in forbidden:
            continue
        if row["src"] == node:
            out.append({"node": row["dst"], "type": row["dst_type"], "edge_type": row["edge_type"], "weight": row["weight"]})
        else:
            out.append({"node": row["src"], "type": row["src_type"], "edge_type": row["edge_type"], "weight": row["weight"]})
    return out


def _node_text(node: str, node_type: str) -> Tuple[str, float]:
    """返回 (文本, importance)。"""
    if node_type == "event" and node.startswith("event:"):
        event_id = int(node.split(":")[-1])
        row = db.one("SELECT * FROM events WHERE id=?", (event_id,))
        if row:
            return f"[事件] 第{row.get('turn')}楼：{row['content']}", float(row.get("importance") or 0.5)
        return "", 0.0
    if node_type == "character":
        row = db.one("SELECT anchor, state FROM characters WHERE id=?", (node,))
        if row:
            return f"[角色] {node}：{row.get('anchor') or ''}", 0.6
        return "", 0.0
    if node_type == "location":
        row = db.one("SELECT desc FROM locations WHERE id=?", (node,))
        if row:
            return f"[地点] {node}：{row.get('desc') or ''}", 0.5
        return "", 0.0
    if node_type == "item":
        row = db.one("SELECT abilities, status FROM items WHERE id=?", (node,))
        if row:
            return f"[物品] {node}：{row.get('abilities') or ''}（{row.get('status')}）", 0.5
        return "", 0.0
    return "", 0.0


def cooldown_ok(world_id: str, node: str, turn: int) -> bool:
    last = (_cooldown.get(world_id) or {}).get(node)
    if last is None:
        return True
    return turn - last >= COOLDOWN_TURNS


def mark_cooldown(world_id: str, node: str, turn: int) -> None:
    _cooldown.setdefault(world_id, {})[node] = turn


def expand(world_id: str, anchors: List[str], turn: int, recency: float = 1.0) -> List[Dict[str, Any]]:
    """从锚点出发做 1-2 跳扩散，返回打分结果。"""
    conf = graph_conf()
    max_hops = int(conf.get("max_hops", 2))
    hop_weights = conf.get("hop_weights", {1: 1.0, 2: 0.5})
    max_nodes_per_hop = int(conf.get("max_nodes_per_hop", 5))
    max_edges_per_node = int(conf.get("max_edges_per_node", 3))
    thresholds = conf.get("score_threshold", {})

    seen: Dict[str, Dict[str, Any]] = {}
    frontier: List[Tuple[str, int]] = [(anchor, 0) for anchor in anchors or []]
    for anchor in anchors or []:
        row = db.one("SELECT id FROM characters WHERE id=?", (anchor,))
        if not row:
            row = db.one("SELECT id FROM locations WHERE id=?", (anchor,))
        seen[anchor] = {
            "node": anchor,
            "type": "character" if row and db.one("SELECT id FROM characters WHERE id=?", (anchor,)) else "location",
            "hop": 0,
            "anchor_match": 1.0,
        }

    for hop in range(1, max_hops + 1):
        next_frontier: List[Tuple[str, int]] = []
        taken = 0
        for node, _ in frontier:
            for edge in neighbours(node)[:max_edges_per_node]:
                child = edge["node"]
                if child in seen:
                    seen[child]["hop"] = min(seen[child]["hop"], hop)
                    continue
                seen[child] = {
                    "node": child,
                    "type": edge["type"] or "concept",
                    "hop": hop,
                    "anchor_match": 0.0,
                    "weight": edge["weight"],
                }
                next_frontier.append((child, hop))
                taken += 1
                if taken >= max_nodes_per_hop:
                    break
            if taken >= max_nodes_per_hop:
                break
        if not next_frontier:
            break
        frontier = next_frontier

    results: List[Dict[str, Any]] = []
    for item in seen.values():
        text, importance = _node_text(item["node"], item.get("type") or "concept")
        if not text and item["hop"] > 0:
            continue
        hop_weight = float(hop_weights.get(item["hop"], 0.4) or 0.4)
        score = (
            item["anchor_match"] * 0.4
            + hop_weight * 0.3
            + importance * 0.2
            + recency * 0.1
        )
        threshold = thresholds.get("anchor" if item["hop"] == 0 else f"graph_{item['hop']}hop", 0.3)
        results.append({"node": item["node"], "hop": item["hop"], "score": round(score, 4), "text": text, "threshold": threshold})

    results.sort(key=lambda x: x["score"], reverse=True)
    keep = [r for r in results if r["score"] >= r["threshold"] and cooldown_ok(world_id, r["node"], turn)]
    return keep[:5]


def graph_text(world_id: str, anchors: List[str], turn: int) -> str:
    """【锚点图记忆】块。"""
    results = expand(world_id, anchors, turn)
    if not results:
        return ""
    lines = ["【锚点图记忆】"]
    for item in results:
        if item["text"]:
            lines.append(f"- {item['text']}（{item['hop']}跳，score={item['score']:.2f}）")
        mark_cooldown(world_id, item["node"], turn)
    return "\n".join(lines) if len(lines) > 1 else ""


def clean_cooldown(world_id: Optional[str] = None) -> None:
    if world_id:
        _cooldown.pop(world_id, None)
    else:
        _cooldown.clear()