"""预设模板接口（SPEC 4.16 / 4.17 / 15）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Body

from .. import db
from ..core import world as world_core
from ..models import TemplateApplyRequest

router = APIRouter(tags=["templates"])

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


def _load_templates() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not TEMPLATE_DIR.exists():
        return out
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
    return out


@router.get("/templates")
def list_templates() -> dict:
    return {"ok": True, "templates": _load_templates()}


@router.post("/templates/apply")
def apply(payload: TemplateApplyRequest = Body(...)) -> dict:
    templates = {t.get("id"): t for t in _load_templates()}
    template = templates.get(payload.template_id)
    if template is None:
        return {"ok": False, "reason": "template_not_found"}

    world_id = payload.world_id or template.get("name") or payload.template_id
    world_conf = template.get("world") or {}
    world = world_core.create_world(
        world_id,
        type_=world_conf.get("type", "小千世界"),
        rules=world_conf.get("rules", {}),
        layer=int(world_conf.get("layer", 1)),
        time_ratio=_ratio(world_conf.get("rules", {}).get("time_ratio")),
    )

    created_locations = []
    for loc in template.get("locations", []) or []:
        db.execute(
            "INSERT OR REPLACE INTO locations (id, world_id, \"desc\", status, links, last_updated) VALUES (?,?,?,?,?,?)",
            (loc, world_id, template.get("name", ""), "active", "[]", str(db.now_ms())),
        )
        created_locations.append(loc)

    created_chars = []
    for char in template.get("sample_chars", []) or []:
        char_id = char.get("id") or char.get("name")
        if not char_id:
            continue
        db.execute(
            "INSERT OR REPLACE INTO characters (id, world_id, home_world, anchor, state, knows_worlds, archived,"
            " pinned, version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                char_id,
                world_id,
                char.get("home_world", world_id),
                char.get("anchor", ""),
                json.dumps(char.get("state", {}), ensure_ascii=False),
                json.dumps([world_id], ensure_ascii=False),
                0,
                0,
                0,
                db.now_ms(),
            ),
        )
        created_chars.append(char_id)

    return {
        "ok": True,
        "template_id": payload.template_id,
        "world": world,
        "locations": created_locations,
        "characters": created_chars,
    }


def _ratio(text: Any) -> float:
    """把 "外界1日=此界30日" 这类描述换算成数值比例。"""
    if isinstance(text, (int, float)):
        return float(text)
    if not isinstance(text, str):
        return 1.0
    import re

    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    if len(numbers) >= 2 and numbers[0]:
        return round(numbers[1] / numbers[0], 4)
    return 1.0