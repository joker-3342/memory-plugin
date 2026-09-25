"""章节 / 会话接口（SPEC 4.12 / 11.6）。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body

from ..core import chapters
from ..models import ChapterCloseRequest

router = APIRouter(tags=["chapters"])


@router.post("/chapters/close")
def close(payload: ChapterCloseRequest = Body(...)) -> dict:
    return chapters.close_chapter(payload.chapter_id)


@router.post("/chapters/open")
def open_chapter(
    chat_id: str = Body(..., embed=True),
    world_id: str = Body(..., embed=True),
    title: str = Body("", embed=True),
    turn_start: int = Body(0, embed=True),
) -> dict:
    return chapters.open_chapter(chat_id, world_id, title, turn_start)


@router.get("/chapters")
def list_chapters(chat_id: Optional[str] = None, world_id: Optional[str] = None) -> dict:
    return {"ok": True, "chapters": chapters.list_chapters(chat_id, world_id)}


@router.post("/chats/create")
def create_chat(
    chat_id: str = Body(..., embed=True),
    world_id: str = Body(..., embed=True),
    inherit_mode: str = Body("summary", embed=True),
    inherits_from: Optional[str] = Body(None, embed=True),
) -> dict:
    return chapters.create_chat(chat_id, world_id, inherit_mode, inherits_from)


@router.post("/characters/archive")
def archive(world_id: str = Body(..., embed=True), turn: int = Body(0, embed=True)) -> dict:
    return chapters.archive_npc(world_id, turn)


@router.post("/characters/revive")
def revive(char_id: str = Body(..., embed=True)) -> dict:
    return chapters.revive_npc(char_id)


@router.post("/characters/pin")
def pin(char_id: str = Body(..., embed=True), pinned: bool = Body(True, embed=True)) -> dict:
    return chapters.pin_npc(char_id, pinned)


@router.post("/worldbook/bind")
def bind(world_id: str = Body(..., embed=True), entries: list = Body(default_factory=list, embed=True)) -> dict:
    return chapters.bind_worldbook(world_id, entries)