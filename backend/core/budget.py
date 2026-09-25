"""token 预算（SPEC 9）。

- 固定注入模板 + 按优先级裁剪；
- 世界门禁永不裁剪；
- 单类上限 + 冷却机制。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .. import config

CJK_START = 0x4E00
CJK_END = 0x9FFF


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中日韩字符 ~0.7 token/字，其余 ~0.3 token/字符。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if CJK_START <= ord(ch) <= CJK_END)
    other = len(text) - cjk
    return max(1, int(cjk * 0.7 + other * 0.3))


def total_budget(override: int | None = None) -> int:
    return int(override or config.get("memory.total_budget", 1300))


def hard_limit() -> int:
    return int(config.get("memory.hard_limit", 1500))


def priority() -> List[str]:
    return list(config.get("memory.budget.priority", []))


def section_budget() -> Dict[str, int]:
    return dict(config.get("memory.budget.section_budget", {}))


def max_per_category() -> Dict[str, int]:
    return dict(config.get("memory.budget.max_per_category", {}))


def cache_ttl() -> Dict[str, int]:
    return dict(config.get("memory.budget.cache_ttl", {}))


def char_priority() -> Dict[str, float]:
    return dict(config.get("memory.char_priority", {}))


def alloc_for(block: str, budget: int) -> int:
    """按模板比例给出该块的预算。"""
    template = section_budget()
    total_template = sum(template.values()) or 1
    share = template.get(block, 50) / total_template
    return max(20, int(budget * share))


def trim_text(text: str, max_tokens: int) -> str:
    """把文本裁到 max_tokens 以内（按行裁，保留完整行）。"""
    if estimate_tokens(text) <= max_tokens:
        return text
    lines = text.split("\n")
    kept: List[str] = []
    used = 0
    for line in lines:
        cost = estimate_tokens(line) + 1
        if used + cost > max_tokens:
            break
        kept.append(line)
        used += cost
    if not kept and lines:
        # 至少保留首行的前缀
        head = lines[0]
        keep_chars = max(4, int(max_tokens / 0.7))
        kept = [head[:keep_chars] + "…"]
    return "\n".join(kept)


def select_by_priority(blocks: Dict[str, str], budget: int, hard: int | None = None) -> Tuple[Dict[str, str], List[str], str]:
    """按优先级从低到高裁剪，直到总量落进预算。

    返回 (保留块, 被丢弃块名, 原因)。
    """
    order = priority()
    dropped: List[str] = []
    reason = ""
    limit = hard or hard_limit()

    def total(items: Dict[str, str]) -> int:
        return sum(estimate_tokens(v) for v in items.values())

    work: Dict[str, str] = {k: v for k, v in blocks.items() if v}

    # 第一轮：超过 hard_limit，从优先级最低的开始整块丢弃（warnings 例外，最后才丢）
    for name in reversed(order):
        if total(work) <= budget:
            break
        if name in work and name != "world_gate":
            work.pop(name)
            dropped.append(name)
            reason = "budget_exceeded"
    if total(work) > limit and "world_gate" in work:
        # 世界门禁永不裁剪，其余全丢
        keep = {"world_gate": work["world_gate"]}
        dropped.extend([k for k in work if k != "world_gate"])
        work = keep
        reason = "hard_limit"

    # 第二轮：逐块限长
    for name in list(work.keys()):
        cap = alloc_for(name, budget)
        work[name] = trim_text(work[name], cap)

    return work, dropped, reason


def assemble(blocks: Dict[str, str], order: List[str] | None = None) -> str:
    """按固定顺序拼装注入文本。"""
    seq = order or priority()
    parts = [blocks[name] for name in seq if blocks.get(name)]
    return "\n".join(parts).strip()


def used_tokens(text: str) -> int:
    return estimate_tokens(text)