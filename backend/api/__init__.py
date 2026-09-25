"""API 层：14 个路由模块 + 聚合路由。"""

from fastapi import APIRouter

from . import (
    admin,
    audit,
    chapters,
    commit,
    debug,
    inject,
    io,
    jobs,
    metrics,
    session,
    summarize,
    templates,
    ui,
    update,
    world,
)

api_router = APIRouter()
api_router.include_router(inject.router)
api_router.include_router(update.router)
api_router.include_router(commit.router)
api_router.include_router(world.router)
api_router.include_router(summarize.router)
api_router.include_router(admin.router)
api_router.include_router(debug.router)
api_router.include_router(session.router)
api_router.include_router(jobs.router)
api_router.include_router(metrics.router)
api_router.include_router(io.router)
api_router.include_router(chapters.router)
api_router.include_router(templates.router)
api_router.include_router(audit.router)
api_router.include_router(ui.router)

__all__ = ["api_router"]