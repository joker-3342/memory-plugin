"""世界相关接口（SPEC 4.4）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body

from .. import db
from ..core import causal, chapters, lock, world as world_core
from ..models import TickRequest

router = APIRouter(tags=["world"])


@router.post("/world/tick")
def tick(payload: TickRequest = Body(...)) -> dict:
    holder = f"tick:{payload.world_id}"
    acquired = lock.acquire(payload.world_id, holder, lock_type="tick")
    if not acquired["acquired"]:
        world = world_core.ensure_world(payload.world_id)
        return {
            "off_screen_events": world.get("off_screen_events") or [],
            "world_status": world.get("status", "active"),
            "local_time": world_core.local_time(world),
            "queued": True,
        }
    try:
        result = world_core.advance(payload.world_id, payload.meta_time, payload.player_present)
    finally:
        lock.release(payload.world_id, holder)
    return {
        "off_screen_events": result["off_screen_events"],
        "world_status": result["world_status"],
        "local_time": result["local_time"],
    }


@router.post("/world/create")
def create(
    world_id: str = Body(..., embed=True),
    type_: str = Body("小千世界", embed=True, alias="type"),
    rules: Dict[str, Any] = Body(default_factory=dict, embed=True),
    layer: int = Body(1, embed=True),
    time_ratio: float = Body(1.0, embed=True),
    parent_id: Optional[str] = Body(None, embed=True),
) -> dict:
    return world_core.create_world(world_id, type_, rules, parent_id, layer, time_ratio)


@router.get("/world/{world_id}")
def detail(world_id: str) -> dict:
    world = world_core.get_world(world_id)
    if world is None:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "world": world}


@router.get("/worlds")
def list_worlds() -> dict:
    rows = db.query("SELECT id, type, status, layer, version FROM worlds")
    return {"ok": True, "worlds": rows}


@router.post("/world/transition")
def transition(
    world_id: str = Body(..., embed=True),
    to_world: str = Body(..., embed=True),
    char_id: str = Body(..., embed=True),
    reason: str = Body("", embed=True),
) -> dict:
    return world_core.transition(world_id, to_world, char_id, reason)


@router.post("/world/scene/close")
def scene_close(world_id: str = Body(..., embed=True), turn: int = Body(0, embed=True)) -> dict:
    """场景结束流程（SPEC 11.4）。"""
    return chapters.scene_close(world_id, turn)


@router.post("/world/check-points")
def check_points(
    world_id: str = Body(..., embed=True),
    turn: int = Body(0, embed=True),
) -> dict:
    """每 N 轮强制全量检查（SPEC 13.1）。"""
    closed = causal.auto_close(world_id, turn)
    unresolved = causal.unresolved_foreshadows(world_id)
    drifted = db.query("SELECT char_id, drift_score FROM personality WHERE drift_score > 0.5")
    return {
        "ok": True,
        "auto_close": closed,
        "unresolved_foreshadows": [item["content"] for item in unresolved],
        "personality_drift": drifted,
    }