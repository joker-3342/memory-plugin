"""trace_id 生成与上下文（SPEC 12.1）。"""

from __future__ import annotations

import secrets
import time

_counter = 0


def new_trace_id(turn: int | None = None) -> str:
    """形如 tr_142_a1b2c3。"""
    global _counter
    _counter = (_counter + 1) % 100000
    token = secrets.token_hex(3)
    return f"tr_{turn if turn is not None else 0}_{token}{_counter:02d}"


def new_job_id(prefix: str = "job") -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def new_draft_id(turn: int | None = None) -> str:
    return f"draft_{turn if turn is not None else 0}_{secrets.token_hex(3)}"


def new_msg_id(chat_id: str, turn: int, role: str) -> str:
    """消息 ID 方案（SPEC 10.3）：{chat_id}:{turn}:{role}。"""
    return f"{chat_id}:{turn}:{role}"


def parse_msg_id(msg_id: str) -> dict:
    parts = (msg_id or "").split(":")
    if len(parts) != 3:
        return {"valid": False, "chat_id": None, "turn": None, "role": None}
    chat_id, turn, role = parts
    try:
        turn_value = int(turn)
    except ValueError:
        return {"valid": False, "chat_id": chat_id, "turn": None, "role": role}
    return {"valid": role in ("user", "assistant", "system"), "chat_id": chat_id, "turn": turn_value, "role": role}


def is_valid_msg_id(msg_id: str) -> bool:
    """没有 msg_id 的记忆不准入库（全局原则 6）。"""
    return bool(msg_id) and parse_msg_id(msg_id)["valid"]