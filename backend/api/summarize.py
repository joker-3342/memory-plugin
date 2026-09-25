"""摘要接口（SPEC 4.5）。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body

from .. import db
from ..memory import summarizer
from ..models import SummarizeRequest

router = APIRouter(tags=["summarize"])


@router.post("/summarize")
def summarize(payload: SummarizeRequest = Body(...)) -> dict:
    messages = [msg.model_dump() for msg in payload.messages]
    return summarizer.summarize(
        payload.world_id,
        level=payload.level,
        turn_start=payload.turn_start,
        turn_end=payload.turn_end,
        messages=messages,
    )


@router.get("/summaries")
def list_summaries(world_id: str, level: Optional[str] = None, limit: int = 20) -> dict:
    if level:
        rows = db.query(
            "SELECT * FROM summaries WHERE world_id=? AND level=? ORDER BY id DESC LIMIT ?",
            (world_id, level, limit),
        )
    else:
        rows = db.query("SELECT * FROM summaries WHERE world_id=? ORDER BY id DESC LIMIT ?", (world_id, limit))
    for row in rows:
        row["unresolved"] = db.loads(row.get("unresolved"), [])
    return {"ok": True, "summaries": rows}


@router.post("/summaries/compress")
def compress(world_id: str = Body(..., embed=True), level: str = Body("volume", embed=True)) -> dict:
    return summarizer.compress_chapters(world_id, level)