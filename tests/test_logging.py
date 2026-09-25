"""日志模块测试（SPEC 12.1 / 10.6）。

覆盖：落盘、上下文注入、密钥脱敏、异常堆栈、慢操作告警、尾部读取、/debug/logs。
"""

from __future__ import annotations

import json

import pytest

from backend import config
from backend import logging as mlog


@pytest.fixture()
def log_env(tmp_path):
    """把日志目录切到临时目录，测试后还原（避免污染工作区 data/logs）。"""
    original = config.CONFIG["logging"]["dir"]
    config.CONFIG["logging"]["dir"] = str(tmp_path / "logs")
    mlog.configure(force=True)
    mlog.clear_context()
    try:
        yield tmp_path / "logs"
    finally:
        config.CONFIG["logging"]["dir"] = original
        mlog.configure(force=True)
        mlog.clear_context()


def test_log_written_to_file(log_env):
    log = mlog.get_logger("test")
    log.info("hello_event", foo="bar")

    lines = mlog.tail(50)
    assert any("hello_event" in line for line in lines), "日志应该落盘"

    entry = mlog.tail_json(50)[-1]
    assert entry["event"] == "hello_event"
    assert entry["foo"] == "bar"
    assert entry["level"] == "INFO"
    assert entry["ts"]


def test_context_is_injected(log_env):
    log = mlog.get_logger("test")
    with mlog.log_context(trace_id="tr_test_1", world_id="青云小世界", turn=7):
        log.info("ctx_event")

    entry = mlog.tail_json(10)[-1]
    assert entry["trace_id"] == "tr_test_1"
    assert entry["world_id"] == "青云小世界"
    assert entry["turn"] == 7

    # 出了 with 就该还原，不该污染后续日志
    log.info("after_ctx")
    assert "trace_id" not in mlog.tail_json(10)[-1]


def test_secret_is_masked(log_env):
    log = mlog.get_logger("test")
    log.info("secret_event", api_key="sk-1234567890abcdef", note="ok")

    entry = mlog.tail_json(10)[-1]
    assert entry["api_key"] == "sk-12****cdef"
    assert "1234567890" not in json.dumps(entry, ensure_ascii=False)
    assert entry["note"] == "ok"


def test_exception_logs_traceback(log_env):
    log = mlog.get_logger("test")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("boom_event", where="unit-test")

    entry = mlog.tail_json(10)[-1]
    assert "ValueError: boom" in entry["traceback"]
    assert entry["where"] == "unit-test"

    # ERROR 以上要单独进 error.log
    assert any("boom_event" in line for line in mlog.tail_errors(10))


def test_slow_operation_warns(log_env):
    log = mlog.get_logger("test")
    with mlog.slow("unit_op", threshold_ms=0):
        pass

    entry = mlog.tail_json(10)[-1]
    assert entry["event"] == "operation_slow"
    assert entry["op"] == "unit_op"


def test_slow_records_failure_and_reraises(log_env):
    with pytest.raises(RuntimeError):
        with mlog.slow("unit_op_fail", threshold_ms=100000):
            raise RuntimeError("nope")

    entry = mlog.tail_json(10)[-1]
    assert entry["event"] == "operation_failed"
    assert "RuntimeError" in entry["traceback"]


def test_sanitize_fields():
    out = mlog.sanitize_fields(
        {"token": "abcdefghijkl", "none_value": None, "long": "x" * 600, "items": [1, 2]}
    )
    assert "none_value" not in out
    assert out["token"] == "abcde****ijkl"
    assert out["long"].endswith("…")
    assert out["items"] == ["1", "2"]


def test_log_file_rotates_config_loaded(log_env):
    """max_bytes / backup_count 应从配置读出来（默认 5MB × 5）。"""
    assert config.get("logging.max_bytes") == 5 * 1024 * 1024
    assert config.get("logging.backup_count") == 5
    assert config.get("logging.slow_ms") == 200


def test_debug_log_test_and_logs_endpoint(client):
    written = client.post("/debug/log-test", json={"level": "info", "message": "from-test"}).json()
    assert written["ok"] is True

    data = client.get("/debug/logs", params={"lines": 100}).json()
    assert data["ok"] is True
    assert data["exists"] is True
    assert any("from-test" in line for line in data["entries"])

    errors = client.get("/debug/logs/errors", params={"lines": 20}).json()
    assert errors["ok"] is True
    assert errors["source"] == "errors"


def test_http_request_logged_with_trace_header(client):
    response = client.get("/health")
    trace_id = response.headers.get("X-Trace-Id")
    assert trace_id, "响应头应回写 X-Trace-Id"

    data = client.get("/debug/logs", params={"lines": 200, "json_format": True}).json()
    entries = [e for e in data["entries"] if e.get("event") == "http_request"]
    assert entries, "每个请求都该有一条 http_request 日志"
    assert any(e.get("path") == "/health" for e in entries)