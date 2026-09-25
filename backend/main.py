"""FastAPI 入口（SPEC 任务9）。

启动时初始化数据库；挂载 API 路由 + OpenAI 兼容网关。

日志：
- 每个请求一条结构化日志（方法 / 路径 / 状态码 / 耗时 + trace_id）；
- 异常带完整堆栈落 `data/logs/error.log`；
- 响应头回写 `X-Trace-Id`，方便把前端报错和日志对上号。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, config, db
from .api import api_router
from .core import injector, lock, queue, trace
from .gateway import router as gateway_router
from .logging import configure as configure_logging
from .logging import get_logger, install_excepthooks, log_context, log_path
from .memory import updater, vector

configure_logging(logging.INFO)
install_excepthooks()
log = get_logger()

app = FastAPI(
    title="SillyTavern 记忆插件",
    description="独立后端记忆引擎：世界门禁 / 人格锚点 / 因果链 / 关系衰减 / 向量检索",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(gateway_router.router)


@app.middleware("http")
async def log_requests(request, call_next):
    """每个请求一条日志：方法 / 路径 / 状态码 / 耗时 + trace_id。

    trace_id 同时写进响应头 `X-Trace-Id`，出问题时能直接对到日志行。
    """
    trace_id = trace.new_trace_id()
    started = time.perf_counter()
    with log_context(trace_id=trace_id):
        try:
            response = await call_next(request)
        except Exception as exc:
            cost_ms = round((time.perf_counter() - started) * 1000, 2)
            log.exception(
                "http_request_failed",
                method=request.method,
                path=request.url.path,
                cost_ms=cost_ms,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        cost_ms = round((time.perf_counter() - started) * 1000, 2)
        client = request.client.host if request.client else None
        if cost_ms >= 500 or response.status_code >= 500:
            log.warning(
                "http_request_slow",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                cost_ms=cost_ms,
                client=client,
            )
        else:
            log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                cost_ms=cost_ms,
                client=client,
            )
        response.headers["X-Trace-Id"] = trace_id
        return response


_worker: Dict[str, Any] = {"thread": None, "stop": False}


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    log.info(
        "startup",
        version=__version__,
        db=str(config.db_path()),
        vector=vector.backend_name(),
        log_file=str(log_path("main")),
        error_log=str(log_path("errors")),
    )
    _start_worker()


@app.on_event("shutdown")
def on_shutdown() -> None:
    _worker["stop"] = True
    _worker["thread"] = None
    log.info("shutdown", version=__version__)


def _worker_loop(interval: float = 2.0) -> None:
    """后台消费任务队列（不阻塞主对话）。"""
    while not _worker["stop"]:
        try:
            processed = updater.process_pending(50)
            lock.purge_expired()
            if processed == 0:
                time.sleep(interval)
        except Exception as exc:  # 后台线程绝不因单次失败退出
            # 完整堆栈进 error.log：线程里的 bug 最难查，必须留痕
            log.exception("worker_error", error=f"{type(exc).__name__}: {exc}")
            time.sleep(interval)


def _start_worker() -> None:
    if _worker["thread"] is not None:
        return
    workers = int(config.get("runtime.job_workers", 1) or 1)
    if workers <= 0:
        log.warning("worker_disabled", job_workers=workers)
        return
    thread = threading.Thread(target=_worker_loop, name="memory-plugin-worker", daemon=True)
    thread.start()
    _worker["thread"] = thread
    log.info("worker_started", thread=thread.name, workers=workers)


@app.get("/")
def root() -> Dict[str, Any]:
    return {"ok": True, "service": "memory-plugin", "version": __version__}


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "db": str(config.db_path()),
        "vector": vector.backend_name(),
        "log_file": str(log_path("main")),
        "queue": {k: v for k, v in queue.status().items() if k in ("pending", "running", "done", "failed", "dead")},
    }


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception) -> JSONResponse:
    """统一错误出口：对外不泄漏堆栈，对内把堆栈写进日志。"""
    trace_id = (get_context_trace() or "") or trace.new_trace_id()
    # 完整堆栈 → error.log；响应里只给类型 + trace_id
    log.exception(
        "unhandled_exception",
        trace_id=trace_id,
        path=str(request.url),
        error=f"{type(exc).__name__}: {exc}",
        hint="完整堆栈见 data/logs/error.log",
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": False,
            "reason": f"{type(exc).__name__}",
            "detail": str(exc)[:300],
            "trace_id": trace_id,
        },
    )


def get_context_trace() -> str:
    """当前请求的 trace_id（异常处理器里用来回填）。"""
    from .logging import get_context

    return get_context().get("trace_id") or ""


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=str(config.get("server.host", "127.0.0.1")),
        port=int(config.get("server.port", 8000)),
        reload=False,
    )