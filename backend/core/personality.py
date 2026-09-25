"""人格锚点与人格漂移（SPEC 12.5）。

1. personality 每轮常驻注入
2. 每轮 AI 回复后跑行为一致性校验
3. drift_score > 0.5 强制注入纠正块
4. speech_profile 每轮注入
5. appearance_immutable 每轮注入
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .. import config, db

DRIFT_CORRECT_THRESHOLD = 0.5


def get(char_id: str) -> Optional[Dict[str, Any]]:
    row = db.one("SELECT * FROM personality WHERE char_id=?", (char_id,))
    if row is None:
        return None
    for field in ("core_traits", "speech_profile", "values", "taboos", "quirks", "appearance_immutable"):
        raw = db.loads(row.get(field), [] if field in ("core_traits", "values", "taboos", "quirks") else {})
        row[field] = raw
    return row


def upsert(char_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "core_traits": data.get("core_traits", []),
        "speech_profile": data.get("speech_profile", {}),
        "values": data.get("values", []),
        "taboos": data.get("taboos", []),
        "quirks": data.get("quirks", []),
        "decision_pattern": data.get("decision_pattern", ""),
        "appearance_immutable": data.get("appearance_immutable", {}),
        "drift_score": float(data.get("drift_score", 0.0) or 0.0),
    }
    existing = db.one("SELECT char_id FROM personality WHERE char_id=?", (char_id,))
    if existing:
        db.execute(
            "UPDATE personality SET core_traits=?, speech_profile=?, \"values\"=?, taboos=?, quirks=?,"
            " decision_pattern=?, appearance_immutable=?, drift_score=?, updated_at=? WHERE char_id=?",
            (
                json.dumps(payload["core_traits"], ensure_ascii=False),
                json.dumps(payload["speech_profile"], ensure_ascii=False),
                json.dumps(payload["values"], ensure_ascii=False),
                json.dumps(payload["taboos"], ensure_ascii=False),
                json.dumps(payload["quirks"], ensure_ascii=False),
                payload["decision_pattern"],
                json.dumps(payload["appearance_immutable"], ensure_ascii=False),
                payload["drift_score"],
                db.now_ms(),
                char_id,
            ),
        )
    else:
        db.execute(
            "INSERT INTO personality (char_id, core_traits, speech_profile, \"values\", taboos, quirks,"
            " decision_pattern, appearance_immutable, drift_score, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                char_id,
                json.dumps(payload["core_traits"], ensure_ascii=False),
                json.dumps(payload["speech_profile"], ensure_ascii=False),
                json.dumps(payload["values"], ensure_ascii=False),
                json.dumps(payload["taboos"], ensure_ascii=False),
                json.dumps(payload["quirks"], ensure_ascii=False),
                payload["decision_pattern"],
                json.dumps(payload["appearance_immutable"], ensure_ascii=False),
                payload["drift_score"],
                db.now_ms(),
            ),
        )
    return get(char_id) or {}


# --------------------------------------------------------------- 注入


def char_priority_map() -> Dict[str, float]:
    return dict(config.get("memory.char_priority", {}))


def rank_chars(chars: List[str], speaking: Optional[List[str]] = None) -> List[tuple]:
    """多角色优先级排序，超过 3 个在场角色只保留 top 3。"""
    weights = char_priority_map()
    speaking = speaking or []
    scored = []
    for char_id in chars:
        if char_id in speaking:
            level = "speaking"
        else:
            level = "present"
        scored.append((char_id, weights.get(level, 0.7), level))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:3]


def block(char_ids: List[str], speaking: Optional[List[str]] = None) -> str:
    """【人格锚点·在场角色】块。"""
    if not char_ids:
        return ""
    lines = ["【人格锚点·在场角色】"]
    for char_id, weight, level in rank_chars(char_ids, speaking):
        row = get(char_id)
        if row is None:
            char = db.one("SELECT anchor, state FROM characters WHERE id=?", (char_id,))
            if char:
                lines.append(f"- {char_id}（{level}）：{char.get('anchor') or ''}")
            continue
        profile = row.get("speech_profile") or {}
        vocab = "、".join(profile.get("vocabulary", []) or []) or "—"
        forbidden = "、".join(profile.get("forbidden_words", []) or []) or "—"
        traits = "、".join(row.get("core_traits") or []) or "—"
        taboos = "、".join(row.get("taboos") or []) or "—"
        lines.append(f"- {char_id}（{level}）核心特质：{traits}")
        lines.append(
            f"  说话方式：句长={profile.get('sentence_length', 'medium')} 正式度={profile.get('formality', 'low')}"
            f" 讽刺度={profile.get('sarcasm', 'low')} 常用词={vocab} 禁用词={forbidden}"
        )
        lines.append(f"  行为底线（禁忌）：{taboos}")
        lines.append(f"  决策模式：{row.get('decision_pattern') or '—'}")
        appearance = row.get("appearance_immutable") or {}
        if appearance:
            desc = "；".join(f"{k}：{v}" for k, v in appearance.items())
            lines.append(f"  不可变外观（不得改写）：{desc}")
    return "\n".join(lines)


def warning_block() -> str:
    """【人格/因果警告】块：drift_score > 0.5 的角色强制纠正。"""
    rows = db.query("SELECT char_id, drift_score, core_traits FROM personality WHERE drift_score > ?", (DRIFT_CORRECT_THRESHOLD,))
    lines = []
    for row in rows:
        traits = "、".join(db.loads(row.get("core_traits"), []) or [])
        lines.append(f"- {row['char_id']} 人设漂移 {row['drift_score']:.2f}，必须严格遵守：{traits}")
    if not lines:
        return ""
    return "【人格/因果警告】\n" + "\n".join(lines)


def correction_block(char_ids: List[str]) -> str:
    lines = []
    for char_id in char_ids:
        row = get(char_id)
        if row and float(row.get("drift_score") or 0) > DRIFT_CORRECT_THRESHOLD:
            profile = row.get("speech_profile") or {}
            lines.append(
                f"- {char_id}：纠正！使用「{'、'.join(profile.get('catchphrases', []) or ['—'])}」，"
                f"禁止「{'、'.join(profile.get('forbidden_words', []) or ['—'])}」，保持{'、'.join(row.get('core_traits') or [])}"
            )
    if not lines:
        return ""
    return "【人格/因果警告】\n" + "\n".join(lines)


# --------------------------------------------------------------- 一致性校验


def check_consistency(char_id: str, text: str, turn: int = 0) -> Dict[str, Any]:
    """每轮 AI 回复后跑行为一致性校验。"""
    row = get(char_id)
    issues: List[Dict[str, str]] = []
    if row is None or not text:
        return {"char_id": char_id, "issues": issues, "drift_score": 0.0}

    profile = row.get("speech_profile") or {}
    for word in profile.get("forbidden_words", []) or []:
        if word and word in text:
            issues.append({"issue": f"forbidden_word:{word}", "severity": "medium"})

    vocab = profile.get("vocabulary", []) or []
    if vocab and not any(word in text for word in vocab):
        issues.append({"issue": "missing_speech_markers", "severity": "low"})

    for taboo in row.get("taboos") or []:
        if taboo and taboo in text:
            issues.append({"issue": f"taboo_hit:{taboo}", "severity": "high"})

    sentence_length = profile.get("sentence_length")
    if sentence_length == "short" and len(text) > 200:
        issues.append({"issue": "sentence_too_long", "severity": "low"})

    severity_weight = {"low": 0.1, "medium": 0.25, "high": 0.5}
    score = round(min(1.0, sum(severity_weight.get(i["severity"], 0.1) for i in issues)), 3)

    for issue in issues:
        db.execute(
            "INSERT INTO drift_log (char_id, turn, issue, severity, corrected, created_at) VALUES (?,?,?,?,?,?)",
            (char_id, turn, issue["issue"], issue["severity"], 0, db.now_ms()),
        )
    if issues:
        new_score = round(min(1.0, float(row.get("drift_score") or 0.0) * 0.5 + score), 3)
        db.execute("UPDATE personality SET drift_score=?, updated_at=? WHERE char_id=?", (new_score, db.now_ms(), char_id))
    else:
        db.execute(
            "UPDATE personality SET drift_score=?, updated_at=? WHERE char_id=?",
            (round(float(row.get("drift_score") or 0.0) * 0.9, 3), db.now_ms(), char_id),
        )
    return {"char_id": char_id, "issues": issues, "drift_score": score}


def drift_warnings(limit: int = 20) -> List[dict]:
    return db.query("SELECT * FROM drift_log ORDER BY id DESC LIMIT ?", (limit,))


def drift_count() -> int:
    row = db.one("SELECT COUNT(*) AS n FROM drift_log WHERE corrected=0")
    return int((row or {"n": 0})["n"])