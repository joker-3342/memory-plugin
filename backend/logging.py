"""结构化日志：控制台 + 文件落盘 + 上下文追踪（SPEC 12.1 / 10.6）。

排查 bug 的三件套：

1. **双出口** —— 控制台看得见，文件（`data/logs/memory-plugin.log`）留得住。
   5MB × 5 轮转；ERROR 额外单独再落一份 `data/logs/error.log`（带完整堆栈）。
2. **上下文** —— `bind_context(trace_id=..., world_id=..., turn=...)` 之后，
   同一条链路里所有日志自动带上这些字段，不用每次手动传。
3. **脱敏** —— 键名形如 `*_key` / `*_secret` / `token` / `password` / `authorization`
   的字段自动打码，日志里永远不出现明文密钥（SPEC 10.6）。

附加能力：

- `log_context(**fields)`：`with` 语句临时绑定上下文，退出自动还原；
- `slow(op, threshold_ms=...)`：慢操作计时，超过阈值自动 warning（默认 200ms）；
- `exception(event, **fields)`：带当前异常堆栈；
- `install_excepthooks()`：主线程 / 子线程未捕获异常也进日志；
- `tail()` / `tail_errors()` / `log_path()`：给 `/debug/logs` 读尾巴用。

依赖说明：只用标准库，**不依赖 structlog**（装了也不会用，避免两套格式打架）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

_LOGGER_NAME = "memory_plugin"
_configured = False
_config_lock = threading.Lock()
_handlers: List[logging.Handler] = []

# 上下文变量：一次请求 / 一个任务链路里共享
_CONTEXT: Dict[str, ContextVar] = {
    "trace_id": ContextVar("mp_trace_id", default=None),
    "chat_id": ContextVar("mp_chat_id", default=None),
    "world_id": ContextVar("mp_world_id", default=None),
    "turn": ContextVar("mp_turn", default=None),
}

# 需要打码的键名特征
_SECRET_MARKERS = (
    "_key",
    "_secret",
    "_token",
    "token",
    "secret",
    "password",
    "authorization",
    "apikey",
    "api_key",
)

_MAX_FIELD_LEN = 500


# --------------------------------------------------------------- 上下文


def bind_context(**fields: Any) -> None:
    """绑定上下文（None 值会被忽略）。"""
    for key, var in _CONTEXT.items():
        if key in fields and fields[key] is not None:
            var.set(fields[key])


def clear_context() -> None:
    for var in _CONTEXT.values():
        var.set(None)


def get_context() -> Dict[str, Any]:
    return {key: var.get() for key, var in _CONTEXT.items()}


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """临时绑定上下文，退出时还原（可嵌套）。"""
    tokens = {}
    for key, var in _CONTEXT.items():
        if key in fields and fields[key] is not None:
            tokens[key] = var.set(fields[key])
    try:
        yield
    finally:
        for key, token in tokens.items():
            _CONTEXT[key].reset(token)


@contextlib.contextmanager
def slow(op: str, threshold_ms: Optional[int] = None, **fields: Any) -> Iterator[None]:
    """慢操作计时：超过阈值打 warning，异常时打 error 并向上抛。"""
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:
        cost = (time.perf_counter() - started) * 1000
        get_logger().exception(
            "operation_failed", op=op, cost_ms=round(cost, 2), error=f"{type(exc).__name__}: {exc}", **fields
        )
        raise
    cost = (time.perf_counter() - started) * 1000
    if threshold_ms is not None:
        # 显式给了阈值：0 也算数（用于测试/严格模式）
        limit, enabled = threshold_ms, True
    else:
        # 配置为 0 表示关闭慢操作告警
        limit = _slow_ms()
        enabled = limit > 0
    if enabled and cost >= limit:
        get_logger().warning("operation_slow", op=op, cost_ms=round(cost, 2), threshold_ms=limit, **fields)


# --------------------------------------------------------------- 脱敏


def _is_secret_key(key: str) -> bool:
    low = key.lower()
    return any(marker in low for marker in _SECRET_MARKERS)


def _mask(value: str) -> str:
    """日志脱敏：sk-1234****5678。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:5]}****{value[-4:]}"


def sanitize_fields(fields: Dict[str, Any]) -> Dict[str, Any]:
    """过滤 None、打码密钥、截断超长值（复杂对象转字符串）。"""
    out: Dict[str, Any] = {}
    for key, value in (fields or {}).items():
        if value is None:
            continue
        if _is_secret_key(str(key)):
            out[key] = _mask(str(value))
            continue
        if isinstance(value, (str, int, float, bool)):
            text = str(value)
            out[key] = (text[:_MAX_FIELD_LEN] + "…") if len(text) > _MAX_FIELD_LEN else value
        elif isinstance(value, (list, tuple)):
            out[key] = [str(v)[:80] for v in list(value)[:20]]
        elif isinstance(value, dict):
            out[key] = {str(k): str(v)[:80] for k, v in list(value.items())[:20]}
        else:
            out[key] = str(value)[:_MAX_FIELD_LEN]
    return out


def _clip(text: Any, limit: int = 300) -> str:
    """把 SQL 之类的长文本压成一行短串。"""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


# --------------------------------------------------------------- 格式化


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.ctx = get_context()
        fields = getattr(record, "fields", None) or {}
        # 子模块名优先（db / queue / inject …），一眼看出是哪个模块出的问题
        record.module_name = fields.get("module") or record.name.split(".")[-1]
        return True


class JsonFormatter(logging.Formatter):
    """文件格式：一行一个 JSON，方便 grep / jq。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in (getattr(record, "ctx", None) or {}).items():
            if value is not None:
                payload[key] = value
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(fields)
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps(
                {"ts": payload["ts"], "level": payload["level"], "event": payload["event"]}, ensure_ascii=False
            )


class ConsoleFormatter(logging.Formatter):
    """控制台格式：人能读的一行 + `k=v` 尾巴。"""

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        head = f"{stamp} {record.levelname:<5} [{getattr(record, 'module_name', record.name)}] {record.getMessage()}"
        extras: List[str] = []
        for key, value in (getattr(record, "ctx", None) or {}).items():
            if value is not None:
                extras.append(f"{key}={value}")
        for key, value in (getattr(record, "fields", None) or {}).items():
            extras.append(f"{key}={value}")
        if extras:
            head += " | " + " ".join(extras)
        if record.exc_info:
            head += "\n" + self.formatException(record.exc_info)
        return head


# --------------------------------------------------------------- 配置


def _log_dir() -> Path:
    from . import config

    raw = str(config.get("logging.dir", "data/logs") or "data/logs")
    path = Path(raw)
    if not path.is_absolute():
        path = config.ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path(source: str = "main") -> Path:
    """main → memory-plugin.log；errors → error.log。"""
    return _log_dir() / ("error.log" if source in ("error", "errors") else "memory-plugin.log")


def _slow_ms() -> int:
    from . import config

    return int(config.get("logging.slow_ms", 200) or 0)


def _level_from_env() -> int:
    """优先级：环境变量 MEMORY_PLUGIN_LOG_LEVEL > config.yaml 的 logging.level > INFO。"""
    raw = str(os.getenv("MEMORY_PLUGIN_LOG_LEVEL", "")).upper()
    if raw:
        return getattr(logging, raw, logging.INFO)
    try:
        from . import config

        configured = str(config.get("logging.level", "INFO")).upper()
        return getattr(logging, configured, logging.INFO)
    except Exception:  # pragma: no cover - 配置不可用时退回 INFO
        return logging.INFO


def configure(level: Optional[int] = None, force: bool = False) -> Any:
    """装配日志出口。可重复调用；`force=True` 用于测试重建。"""
    global _configured
    with _config_lock:
        if _configured and not force:
            return get_logger()

        root = logging.getLogger(_LOGGER_NAME)
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        _handlers.clear()

        resolved = level if level is not None else _level_from_env()
        root.setLevel(resolved)
        root.propagate = False
        ctx_filter = _ContextFilter()

        # 1) 控制台：TTY 给人看，非 TTY（被重定向）给 JSON
        console = logging.StreamHandler(sys.stdout)
        use_json_console = os.getenv("MEMORY_PLUGIN_LOG_JSON") == "1" or not sys.stdout.isatty()
        console.setFormatter(JsonFormatter() if use_json_console else ConsoleFormatter())
        console.addFilter(ctx_filter)
        root.addHandler(console)
        _handlers.append(console)

        # 2) 文件：滚动落盘（真正的"查案现场"）
        try:
            from . import config

            max_bytes = int(config.get("logging.max_bytes", 5 * 1024 * 1024) or 5 * 1024 * 1024)
            backup_count = int(config.get("logging.backup_count", 5) or 5)
            file_handler = logging.handlers.RotatingFileHandler(
                log_path("main"), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8", delay=True
            )
            file_handler.setFormatter(JsonFormatter())
            file_handler.addFilter(ctx_filter)
            root.addHandler(file_handler)
            _handlers.append(file_handler)

            # 3) 错误单独一份：只收 ERROR 以上，带完整堆栈
            error_handler = logging.handlers.RotatingFileHandler(
                log_path("errors"), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8", delay=True
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(JsonFormatter())
            error_handler.addFilter(ctx_filter)
            root.addHandler(error_handler)
            _handlers.append(error_handler)
        except Exception as exc:  # pragma: no cover - 落盘失败不该拖垮服务
            root.warning("log_file_handler_failed", extra={"fields": {"error": str(exc)}})

        _configured = True
        return get_logger()


# --------------------------------------------------------------- Logger


class BoundLogger:
    """轻量 logger：`log.info("event", key=value)`，字段自动过脱敏与上下文。

    注意：底层始终挂在 `memory_plugin` 这一个 logger 上。
    （否则 `get_logger("db")` 会拿到一个没挂 handler 的 logger，日志静默丢失。）
    `name` 只作为 `module` 字段，用来标识来源模块，方便定位是哪儿出的问题。
    """

    def __init__(self, name: str = _LOGGER_NAME) -> None:
        self._log = logging.getLogger(_LOGGER_NAME)
        self._name = name

    def _emit(
        self,
        level: int,
        event: str,
        exc_info: Any = False,
        stack_info: bool = False,
        fields: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not _configured:
            configure()
        payload = sanitize_fields(fields or {})
        if self._name and self._name != _LOGGER_NAME:
            payload.setdefault("module", self._name)
        self._log.log(
            level,
            event,
            extra={"fields": payload},
            exc_info=exc_info,
            stack_info=stack_info,
            stacklevel=3,
        )

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, fields=fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, fields=fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, fields=fields)

    warn = warning

    def error(self, event: str, **fields: Any) -> None:
        self._emit(logging.ERROR, event, fields=fields)

    def exception(self, event: str, **fields: Any) -> None:
        """带当前异常堆栈（只能在 except 块里调用）。"""
        self._emit(logging.ERROR, event, exc_info=True, fields=fields)

    def critical(self, event: str, **fields: Any) -> None:
        self._emit(logging.CRITICAL, event, fields=fields)

    def stack(self, event: str, **fields: Any) -> None:
        """记录调用栈（不想抛异常也想看"谁调用的"）。"""
        self._emit(logging.INFO, event, stack_info=True, fields=fields)


_logger = BoundLogger()


def get_logger(name: Optional[str] = None) -> BoundLogger:
    return BoundLogger(name) if name else _logger


def log_block(**fields: Any) -> None:
    """兼容旧接口：一次打一批字段（自动脱敏）。"""
    _logger.info("log_block", **fields)


# --------------------------------------------------------------- 异常钩子


def install_excepthooks() -> None:
    """主线程 / 子线程未捕获异常也进日志（否则线程一崩什么都没留下）。"""

    previous = sys.excepthook

    def _main_hook(exc_type, exc_value, exc_tb):
        _logger._emit(
            logging.CRITICAL,
            "uncaught_exception",
            exc_info=(exc_type, exc_value, exc_tb),
            fields={"thread": threading.current_thread().name},
        )
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _main_hook

    if hasattr(threading, "excepthook"):

        def _thread_hook(args):
            _logger._emit(
                logging.CRITICAL,
                "uncaught_exception_thread",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                fields={"thread": getattr(args.thread, "name", None)},
            )

        threading.excepthook = _thread_hook


# --------------------------------------------------------------- 读取尾巴


def tail(lines: int = 200, source: str = "main") -> List[str]:
    """读最近 N 行原始日志（给 /debug/logs 用）。"""
    path = log_path(source)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines()[-max(1, lines):]
    except OSError:
        return []


def tail_errors(lines: int = 100) -> List[str]:
    return tail(lines, source="errors")


def tail_json(lines: int = 200, source: str = "main") -> List[Dict[str, Any]]:
    """读最近 N 行并解析成 dict（解析失败的保留 raw）。"""
    out: List[Dict[str, Any]] = []
    for line in tail(lines, source):
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"raw": line})
    return out


def format_current_exception() -> str:
    """当前异常的可读堆栈（异常处理器里用）。"""
    return traceback.format_exc()
