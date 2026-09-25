"""导入导出接口（SPEC 4.10 / 4.11 / 11.5）。"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Query

from ..core import exporter, importer
from ..models import ImportRequest

router = APIRouter(tags=["io"])


@router.get("/export")
def export(world_id: Optional[str] = None, partial: bool = False) -> dict:
    if world_id:
        return exporter.export_world(world_id)
    return exporter.export_all()


@router.post("/export/partial")
def export_partial(world_id: str = Body(..., embed=True), tables: List[str] = Body(..., embed=True)) -> dict:
    return exporter.export_partial(world_id, tables)


@router.post("/import")
def import_data(payload: ImportRequest = Body(...)) -> dict:
    return importer.import_data(payload.mode, payload.data)


@router.get("/export/json")
def export_json(world_id: str = Query(...)) -> str:
    """导出为 JSON 文本（便于前端直接下载）。"""
    return exporter.to_json(exporter.export_world(world_id))