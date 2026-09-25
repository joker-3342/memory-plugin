"""SQLite 连接 + 建表 + 迁移。

SPEC 10.2：
- 多表写入用 BEGIN IMMEDIATE 包事务；
- journal_mode=WAL、foreign_keys=ON；
- 失败整批回滚，禁止部分写入；
- 事务内不做 LLM 调用、不做网络请求。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import config
from .logging import get_logger, slow

log = get_logger("db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 世界
CREATE TABLE IF NOT EXISTS worlds (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  parent_id TEXT,
  layer INTEGER DEFAULT 1,
  rules TEXT NOT NULL,
  status TEXT DEFAULT 'active',
  player_present INTEGER DEFAULT 1,
  player_left_at_turn INTEGER,
  player_left_at_story_time TEXT,
  time_ratio REAL DEFAULT 1.0,
  off_screen_events TEXT,
  meta_time INTEGER DEFAULT 0,
  version INTEGER DEFAULT 0,
  created_at INTEGER
);

-- 角色
CREATE TABLE IF NOT EXISTS characters (
  id TEXT PRIMARY KEY,
  world_id TEXT NOT NULL,
  home_world TEXT,
  anchor TEXT NOT NULL,
  first_impression TEXT,
  state TEXT,
  knows_worlds TEXT,
  common_sense TEXT,
  foreign_concepts TEXT,
  reaction_rules TEXT,
  archived INTEGER DEFAULT 0,
  pinned INTEGER DEFAULT 0,
  last_seen_turn INTEGER,
  version INTEGER DEFAULT 0,
  created_at INTEGER
);

-- 人格锚点（values 为 SQLite 保留字，必须加引号）
CREATE TABLE IF NOT EXISTS personality (
  char_id TEXT PRIMARY KEY,
  core_traits TEXT NOT NULL,
  speech_profile TEXT NOT NULL,
  "values" TEXT,
  taboos TEXT,
  quirks TEXT,
  decision_pattern TEXT,
  appearance_immutable TEXT,
  drift_score REAL DEFAULT 0,
  updated_at INTEGER
);

-- 关系
CREATE TABLE IF NOT EXISTS relations (
  from_id TEXT,
  to_id TEXT,
  dimensions TEXT NOT NULL,
  baseline TEXT,
  ledger TEXT,
  anchors TEXT,
  knows TEXT,
  misbeliefs TEXT,
  unknown TEXT,
  momentum TEXT,
  last_contact_turn INTEGER,
  last_contact_story_time TEXT,
  world_id TEXT,
  version INTEGER DEFAULT 0,
  PRIMARY KEY (from_id, to_id)
);

-- 地点（desc 为 SQLite 保留字，必须加引号）
CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY,
  world_id TEXT NOT NULL,
  "desc" TEXT,
  status TEXT DEFAULT 'active',
  links TEXT,
  last_updated TEXT
);

-- 事件
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  world_id TEXT NOT NULL,
  turn INTEGER,
  story_time TEXT,
  content TEXT NOT NULL,
  state TEXT DEFAULT 'committed',
  chars TEXT,
  loc TEXT,
  items TEXT,
  importance REAL DEFAULT 0.5,
  unresolved TEXT,
  preconditions TEXT,
  source_msg TEXT,
  created_at INTEGER
);

-- 因果边
CREATE TABLE IF NOT EXISTS causal_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cause_event_id INTEGER,
  effect_event_id INTEGER,
  relation TEXT NOT NULL,
  "desc" TEXT,
  state TEXT DEFAULT 'committed'
);

-- 图边
CREATE TABLE IF NOT EXISTS edges (
  src TEXT,
  src_type TEXT,
  dst TEXT,
  dst_type TEXT,
  edge_type TEXT,
  weight REAL DEFAULT 1.0,
  PRIMARY KEY (src, dst, edge_type)
);

-- 摘要
CREATE TABLE IF NOT EXISTS summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  world_id TEXT NOT NULL,
  level TEXT NOT NULL,
  turn_start INTEGER,
  turn_end INTEGER,
  content TEXT NOT NULL,
  unresolved TEXT,
  created_at INTEGER
);

-- 物品
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  world_id TEXT NOT NULL,
  owner TEXT,
  holder TEXT,
  status TEXT DEFAULT 'normal',
  abilities TEXT,
  cost TEXT,
  cross_world TEXT DEFAULT 'valid',
  history TEXT,
  created_at INTEGER
);

-- 目标
CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY,
  owner TEXT,
  priority TEXT DEFAULT 'main',
  status TEXT DEFAULT 'active',
  content TEXT,
  clues TEXT,
  dead_ends TEXT,
  deadline TEXT,
  failure_consequence TEXT,
  created_at INTEGER
);

-- 信息传播
CREATE TABLE IF NOT EXISTS info (
  id TEXT PRIMARY KEY,
  truth INTEGER DEFAULT 1,
  known_by TEXT,
  spread_log TEXT,
  secrecy TEXT DEFAULT 'medium',
  world_id TEXT
);

-- 势力
CREATE TABLE IF NOT EXISTS factions (
  id TEXT PRIMARY KEY,
  world_id TEXT NOT NULL,
  relations TEXT,
  members TEXT,
  resources TEXT,
  current_action TEXT,
  created_at INTEGER
);

-- 伏笔
CREATE TABLE IF NOT EXISTS foreshadows (
  id TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  planted_at_turn INTEGER,
  planted_at_event INTEGER,
  status TEXT DEFAULT 'unresolved',
  expected_payoff TEXT,
  deadline INTEGER,
  related_chars TEXT,
  related_locs TEXT,
  world_id TEXT
);

-- 人格漂移日志
CREATE TABLE IF NOT EXISTS drift_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  char_id TEXT,
  turn INTEGER,
  issue TEXT,
  severity TEXT,
  corrected INTEGER DEFAULT 0,
  created_at INTEGER
);

-- 因果漂移日志
CREATE TABLE IF NOT EXISTS causal_drift_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn INTEGER,
  issue TEXT,
  severity TEXT,
  corrected INTEGER DEFAULT 0,
  created_at INTEGER
);

-- 草稿事件
CREATE TABLE IF NOT EXISTS draft_events (
  id TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  causes TEXT,
  effects TEXT,
  unresolved TEXT,
  status TEXT DEFAULT 'draft',
  importance REAL DEFAULT 0.5,
  chars TEXT,
  loc TEXT,
  turn INTEGER,
  story_time TEXT,
  world_id TEXT,
  updated_at INTEGER,
  created_at INTEGER
);

-- 会话锁
CREATE TABLE IF NOT EXISTS session_locks (
  world_id TEXT PRIMARY KEY,
  lock_type TEXT,
  holder TEXT,
  acquired_at INTEGER,
  timeout_ms INTEGER DEFAULT 5000,
  version INTEGER DEFAULT 0
);

-- 冲突日志
CREATE TABLE IF NOT EXISTS conflict_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  world_id TEXT,
  resource TEXT,
  resource_id TEXT,
  old_version INTEGER,
  new_version INTEGER,
  session_id TEXT,
  resolved TEXT,
  created_at INTEGER
);

-- 任务队列
CREATE TABLE IF NOT EXISTS job_queue (
  id TEXT PRIMARY KEY,
  job_type TEXT,
  world_id TEXT,
  payload TEXT,
  status TEXT DEFAULT 'pending',
  retries INTEGER DEFAULT 0,
  max_retries INTEGER DEFAULT 3,
  next_retry_at INTEGER,
  created_at INTEGER,
  started_at INTEGER,
  finished_at INTEGER,
  error TEXT
);

-- 死信
CREATE TABLE IF NOT EXISTS dead_letter (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  job_type TEXT,
  payload TEXT,
  error TEXT,
  created_at INTEGER
);

-- 世界书绑定
CREATE TABLE IF NOT EXISTS world_book_links (
  world_id TEXT,
  wb_entry_id TEXT,
  wb_keywords TEXT,
  inject_position TEXT,
  priority INTEGER DEFAULT 0,
  auto_activate INTEGER DEFAULT 1,
  PRIMARY KEY (world_id, wb_entry_id)
);

-- 会话
CREATE TABLE IF NOT EXISTS chats (
  chat_id TEXT PRIMARY KEY,
  world_id TEXT,
  chapter_id TEXT,
  inherits_from TEXT,
  inherit_mode TEXT DEFAULT 'summary',
  created_at INTEGER
);

-- 章节
CREATE TABLE IF NOT EXISTS chapters (
  chapter_id TEXT PRIMARY KEY,
  chat_id TEXT,
  world_id TEXT,
  title TEXT,
  turn_start INTEGER,
  turn_end INTEGER,
  status TEXT DEFAULT 'active',
  created_at INTEGER
);

-- 会话（设备）
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  chat_id TEXT,
  device TEXT,
  last_active INTEGER,
  priority INTEGER DEFAULT 0
);

-- 审计
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT,
  target_table TEXT,
  target_id TEXT,
  old_value TEXT,
  new_value TEXT,
  operator TEXT,
  created_at INTEGER
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_events_world ON events(world_id, state);
CREATE INDEX IF NOT EXISTS idx_events_turn ON events(turn);
CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src, src_type);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst, dst_type);
CREATE INDEX IF NOT EXISTS idx_summaries_world ON summaries(world_id, level);
CREATE INDEX IF NOT EXISTS idx_draft_status ON draft_events(status);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON job_queue(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_chars_world ON characters(world_id, archived);
"""

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def now_ms() -> int:
    return int(time.time() * 1000)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.db_path()), timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def connection() -> sqlite3.Connection:
    """每线程一个连接（sqlite3 连接不跨线程共享）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def close_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _local.conn = None


def init_db() -> None:
    """建表 + 索引 + 迁移。可重复调用。"""
    global _initialized
    with _init_lock:
        conn = connection()
        conn.executescript(SCHEMA)
        _migrate(conn)
        _initialized = True


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：缺列则补列。"""
    wanted = {
        "worlds": {"off_screen_events": "TEXT", "meta_time": "INTEGER DEFAULT 0"},
        "events": {"preconditions": "TEXT"},
        "characters": {"pinned": "INTEGER DEFAULT 0"},
    }
    for table, columns in wanted.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


@contextmanager
def tx(immediate: bool = True):
    """事务上下文：BEGIN IMMEDIATE + 失败整批回滚。"""
    conn = connection()
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _sql_short(sql: str, limit: int = 200) -> str:
    """SQL 压成一行短串（日志里不放整条语句）。"""
    flat = " ".join(str(sql or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def query(sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    # slow()：慢查询 warning、异常带完整堆栈记 error（见 logging.slow）
    with slow("db.query", sql=_sql_short(sql)):
        cur = connection().execute(sql, tuple(params))
        return [dict(row) for row in cur.fetchall()]


def one(sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    with slow("db.execute", sql=_sql_short(sql)):
        cur = connection().execute(sql, tuple(params))
        return cur.rowcount


def insert_many(sql: str, rows: Iterable[Sequence[Any]]) -> int:
    with slow("db.insert_many", sql=_sql_short(sql)):
        cur = connection().executemany(sql, [tuple(r) for r in rows])
        return cur.rowcount


# ---------- JSON 字段助手 ----------


def dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def loads(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def row_json(row: Optional[Dict[str, Any]], fields: Iterable[str]) -> Optional[Dict[str, Any]]:
    """把行里的 JSON 文本字段解回对象。"""
    if row is None:
        return None
    out = dict(row)
    for field in fields:
        if field in out:
            out[field] = loads(out[field])
    return out
