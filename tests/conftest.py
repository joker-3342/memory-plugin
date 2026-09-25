"""pytest 全局夹具：隔离的临时数据库 + 应用实例。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 必须在导入 backend 之前设置，config 在导入时读取环境变量
_TMP_DB = Path(tempfile.mkdtemp(prefix="memory-plugin-test-")) / "memory.db"
os.environ["MEMORY_PLUGIN_DB"] = str(_TMP_DB)
os.environ.setdefault("MEMORY_PLUGIN_VECTOR_DIR", str(_TMP_DB.parent / "chroma"))

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from backend import config, db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare(tmp_path_factory):
    config.CONFIG["database"]["path"] = str(_TMP_DB)
    config.CONFIG["vector"]["path"] = str(_TMP_DB.parent / "chroma")
    db.init_db()
    yield


@pytest.fixture()
def clean_db():
    """每个用例前清空业务表（保留表结构）。"""
    db.init_db()
    for table in (
        "events",
        "causal_edges",
        "edges",
        "draft_events",
        "relations",
        "personality",
        "characters",
        "worlds",
        "locations",
        "summaries",
        "foreshadows",
        "goals",
        "items",
        "info",
        "factions",
        "session_locks",
        "conflict_log",
        "job_queue",
        "dead_letter",
        "chats",
        "chapters",
        "sessions",
        "audit_log",
        "drift_log",
        "causal_drift_log",
        "world_book_links",
    ):
        db.execute(f"DELETE FROM {table}")
    from backend.core import graph, injector

    injector.clear_cache()
    graph.clean_cooldown()
    yield


@pytest.fixture()
def client():
    """FastAPI 测试客户端（fastapi/httpx 缺失时跳过）。"""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as test_client:
        yield test_client
