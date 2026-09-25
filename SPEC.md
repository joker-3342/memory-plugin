# 记忆插件 · 完整 SPEC

本文档为唯一规格书。AI 一次性生成整个项目，不分阶段。生成后只做优化和维护。

## 0. 项目目标

为 SillyTavern 提供独立后端记忆引擎，解决长篇对话中的世界串线、人设漂移、因果断裂问题。前端只做 UI 和转发，后端做所有重活，通过 OpenAI 兼容网关通信。

核心能力：

- 世界门禁：防止仙侠/无限流串世界
- 人格锚点：防止角色越写越不像
- 因果链：防止事件孤立、动机断裂
- 三态因果：draft → pending → committed，因果闭合才写死
- 关系衰减：防止莫名好感/仇恨
- 向量检索：按需注入，不爆 token
- 前后端分离：酒馆不卡顿

## 1. 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.11 + FastAPI + Uvicorn |
| 数据库 | SQLite（WAL 模式） |
| 向量库 | Chroma（本地嵌入式） |
| ORM | 原生 SQL + Pydantic 模型 |
| 前端 | TypeScript + SillyTavern 插件 API |
| 网关 | FastAPI 自写，兼容 OpenAI 协议 |
| 异步 | asyncio + BackgroundTasks |
| 配置 | YAML + 环境变量 |
| 日志 | structlog |
| 测试 | pytest + httpx |

## 2. 目录结构

```
memory-plugin/
├── SPEC.md
├── README.md
├── config.yaml
├── requirements.txt
├── .env.example
├── backend/
│   ├── __init__.py
│   ├── main.py                    # FastAPI 入口
│   ├── config.py                  # 配置加载
│   ├── db.py                      # SQLite 连接 + 建表 + 迁移
│   ├── models.py                  # Pydantic 模型
│   ├── logging.py                 # structlog 配置
│   ├── api/
│   │   ├── __init__.py
│   │   ├── inject.py              # POST /inject
│   │   ├── update.py              # POST /update
│   │   ├── commit.py              # POST /commit
│   │   ├── world.py               # POST /world/tick
│   │   ├── summarize.py           # POST /summarize
│   │   ├── admin.py               # CRUD
│   │   ├── debug.py               # GET /debug/last
│   │   ├── session.py             # 锁
│   │   ├── jobs.py                # 队列
│   │   ├── metrics.py             # 指标
│   │   ├── io.py                  # 导入导出
│   │   ├── chapters.py            # 章节
│   │   ├── templates.py           # 模板
│   │   └── audit.py               # 审计
│   ├── core/
│   │   ├── __init__.py
│   │   ├── world.py               # 世界状态机
│   │   ├── relation.py            # 关系衰减
│   │   ├── personality.py         # 人格漂移
│   │   ├── causal.py              # 因果闭合
│   │   ├── graph.py               # 锚点图扩散
│   │   ├── budget.py              # token 预算
│   │   ├── injector.py            # 注入拼装
│   │   ├── lock.py                # 并发锁
│   │   ├── queue.py               # 任务队列
│   │   ├── trace.py               # trace_id
│   │   ├── security.py            # 提示注入防御
│   │   ├── audit.py               # 审计
│   │   ├── importer.py
│   │   ├── exporter.py
│   │   └── chapters.py
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── summarizer.py
│   │   ├── vector.py
│   │   └── updater.py
│   └── gateway/
│       ├── __init__.py
│       ├── router.py              # OpenAI 兼容
│       └── opencode.py            # OpenCode Go 适配
├── frontend/
│   ├── manifest.json
│   ├── index.js
│   ├── api.js
│   ├── anchors.js                 # 锚点提取
│   ├── storage.js                 # localStorage
│   └── ui/
│       ├── panel.html
│       ├── causal.html
│       ├── debug.html
│       └── onboard.html
├── templates/
│   ├── xianxia_small.json
│   ├── main_god_space.json
│   └── urban_lowmagic.json
├── tests/
│   ├── test_world.py
│   ├── test_relation.py
│   ├── test_causal.py
│   ├── test_injector.py
│   └── test_api.py
└── data/
    ├── memory.db
    └── chroma/
```

## 3. 数据库 Schema

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 世界
CREATE TABLE worlds (
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
CREATE TABLE characters (
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

-- 人格锚点
CREATE TABLE personality (
  char_id TEXT PRIMARY KEY,
  core_traits TEXT NOT NULL,
  speech_profile TEXT NOT NULL,
  values TEXT,
  taboos TEXT,
  quirks TEXT,
  decision_pattern TEXT,
  appearance_immutable TEXT,
  drift_score REAL DEFAULT 0,
  updated_at INTEGER
);

-- 关系
CREATE TABLE relations (
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

-- 地点
CREATE TABLE locations (
  id TEXT PRIMARY KEY,
  world_id TEXT NOT NULL,
  desc TEXT,
  status TEXT DEFAULT 'active',
  links TEXT,
  last_updated TEXT
);

-- 事件
CREATE TABLE events (
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
CREATE TABLE causal_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cause_event_id INTEGER,
  effect_event_id INTEGER,
  relation TEXT NOT NULL,
  desc TEXT,
  state TEXT DEFAULT 'committed'
);

-- 图边
CREATE TABLE edges (
  src TEXT,
  src_type TEXT,
  dst TEXT,
  dst_type TEXT,
  edge_type TEXT,
  weight REAL DEFAULT 1.0,
  PRIMARY KEY (src, dst, edge_type)
);

-- 摘要
CREATE TABLE summaries (
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
CREATE TABLE items (
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
CREATE TABLE goals (
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
CREATE TABLE info (
  id TEXT PRIMARY KEY,
  truth INTEGER DEFAULT 1,
  known_by TEXT,
  spread_log TEXT,
  secrecy TEXT DEFAULT 'medium',
  world_id TEXT
);

-- 势力
CREATE TABLE factions (
  id TEXT PRIMARY KEY,
  world_id TEXT NOT NULL,
  relations TEXT,
  members TEXT,
  resources TEXT,
  current_action TEXT,
  created_at INTEGER
);

-- 伏笔
CREATE TABLE foreshadows (
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
CREATE TABLE drift_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  char_id TEXT,
  turn INTEGER,
  issue TEXT,
  severity TEXT,
  corrected INTEGER DEFAULT 0,
  created_at INTEGER
);

-- 因果漂移日志
CREATE TABLE causal_drift_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn INTEGER,
  issue TEXT,
  severity TEXT,
  corrected INTEGER DEFAULT 0,
  created_at INTEGER
);

-- 草稿事件
CREATE TABLE draft_events (
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
CREATE TABLE session_locks (
  world_id TEXT PRIMARY KEY,
  lock_type TEXT,
  holder TEXT,
  acquired_at INTEGER,
  timeout_ms INTEGER DEFAULT 5000,
  version INTEGER DEFAULT 0
);

-- 冲突日志
CREATE TABLE conflict_log (
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
CREATE TABLE job_queue (
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
CREATE TABLE dead_letter (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  job_type TEXT,
  payload TEXT,
  error TEXT,
  created_at INTEGER
);

-- 世界书绑定
CREATE TABLE world_book_links (
  world_id TEXT,
  wb_entry_id TEXT,
  wb_keywords TEXT,
  inject_position TEXT,
  priority INTEGER DEFAULT 0,
  auto_activate INTEGER DEFAULT 1,
  PRIMARY KEY (world_id, wb_entry_id)
);

-- 会话
CREATE TABLE chats (
  chat_id TEXT PRIMARY KEY,
  world_id TEXT,
  chapter_id TEXT,
  inherits_from TEXT,
  inherit_mode TEXT DEFAULT 'summary',
  created_at INTEGER
);

-- 章节
CREATE TABLE chapters (
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
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  chat_id TEXT,
  device TEXT,
  last_active INTEGER,
  priority INTEGER DEFAULT 0
);

-- 审计
CREATE TABLE audit_log (
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
CREATE INDEX idx_events_world ON events(world_id, state);
CREATE INDEX idx_events_turn ON events(turn);
CREATE INDEX idx_relations_from ON relations(from_id);
CREATE INDEX idx_edges_src ON edges(src, src_type);
CREATE INDEX idx_edges_dst ON edges(dst, dst_type);
CREATE INDEX idx_summaries_world ON summaries(world_id, level);
CREATE INDEX idx_draft_status ON draft_events(status);
CREATE INDEX idx_jobs_status ON job_queue(status, next_retry_at);
CREATE INDEX idx_chars_world ON characters(world_id, archived);
```

> 实现说明：SQLite 中 `values` 与 `desc` 是保留字，实际建表与 SQL 里统一加双引号 `"values"` / `"desc"`。

## 4. API 接口

### 4.1 POST /inject

请求：

```json
{
  "turn": 142,
  "world_id": "青云小世界",
  "scene": {
    "loc": "钟楼",
    "chars": ["艾琳", "主角"],
    "story_time": "第3天夜"
  },
  "anchors": ["艾琳", "主角", "钟楼", "怀表"],
  "recent_summary": "最近3楼简短摘要",
  "budget": 1300,
  "scene_type": "present"
}
```

响应：

```json
{
  "inject_text": "...",
  "token_count": 1120,
  "cache_hit": true,
  "latency_ms": 45,
  "trace_id": "tr_142_abc",
  "debug": {
    "blocks": ["world_gate", "scene", "personality", "relations", "causal", "goals"],
    "dropped": ["vector_results"],
    "reason": "budget_exceeded",
    "warnings": []
  }
}
```

### 4.2 POST /update

请求：

```json
{
  "turn": 142,
  "world_id": "青云小世界",
  "chat_id": "chat_001",
  "user_msg": {"id": "chat_001:142:user", "content": "..."},
  "ai_msg": {"id": "chat_001:142:assistant", "content": "..."},
  "scene": {"loc": "钟楼", "chars": ["艾琳", "主角"], "story_time": "第3天夜"},
  "anchors": ["艾琳", "主角", "钟楼"]
}
```

响应：

```json
{
  "ok": true,
  "job_id": "job_142",
  "queued": ["summarize", "relation_update", "event_extract"],
  "trace_id": "tr_142_abc"
}
```

### 4.3 POST /commit

请求：

```json
{
  "draft_id": "draft_001",
  "world_id": "青云小世界",
  "event": {
    "content": "艾琳哥哥被处决",
    "causes": [{"event_id": 87, "relation": "间接导致", "desc": "..."}],
    "effects": [{"event_id": 115, "relation": "导致", "desc": "..."}],
    "chars": ["艾琳"],
    "loc": "公爵府",
    "importance": 0.9,
    "turn": 112,
    "story_time": "第9天"
  }
}
```

响应：

```json
{
  "ok": true,
  "committed_event_id": 112,
  "causal_edges_created": 2,
  "trace_id": "tr_142_abc"
}
```

### 4.4 POST /world/tick

请求：

```json
{
  "world_id": "青云小世界",
  "meta_time": 1420,
  "player_present": false
}
```

响应：

```json
{
  "off_screen_events": ["第4天：钟楼密道被封锁", "第9天：艾琳哥哥被处决"],
  "world_status": "active",
  "local_time": "第93天"
}
```

### 4.5 POST /summarize

请求：

```json
{
  "world_id": "青云小世界",
  "level": "scene",
  "turn_start": 130,
  "turn_end": 142,
  "messages": []
}
```

响应：

```json
{
  "summary_id": 42,
  "content": "...",
  "unresolved": ["钟楼密道入口"]
}
```

### 4.6 POST /session/lock

请求：

```json
{
  "world_id": "青云小世界",
  "lock_type": "update",
  "holder": "session_001",
  "timeout_ms": 5000
}
```

响应：

```json
{
  "acquired": true,
  "version": 12
}
```

### 4.7 POST /session/unlock

请求：

```json
{
  "world_id": "青云小世界",
  "holder": "session_001"
}
```

### 4.8 GET /jobs/status

响应：

```json
{
  "pending": 3,
  "running": 1,
  "failed": 0,
  "dead": 2,
  "items": []
}
```

### 4.9 POST /jobs/retry

请求：

```json
{
  "job_id": "job_142"
}
```

### 4.10 GET /export

参数：world_id、partial（可选）

响应：完整 JSON。

### 4.11 POST /import

请求：

```json
{
  "mode": "merge",
  "data": {}
}
```

### 4.12 POST /chapters/close

请求：

```json
{
  "chapter_id": "chapter_02"
}
```

### 4.13 POST /admin/override

请求：

```json
{
  "target_table": "relations",
  "target_id": "艾琳:主角",
  "field": "dimensions.trust",
  "value": 0.5,
  "reason": "手动修正"
}
```

### 4.14 GET /audit

参数：limit、target_table

### 4.15 GET /metrics

响应：

```json
{
  "inject_latency_p50": 45,
  "inject_latency_p99": 120,
  "update_fail_rate": 0.01,
  "vector_hit_rate": 0.65,
  "graph_hit_rate": 0.8,
  "cache_hit_rate": 0.7,
  "dead_letter_count": 2,
  "drift_warning_count": 1
}
```

### 4.16 GET /templates

### 4.17 POST /templates/apply

请求：

```json
{
  "template_id": "xianxia_small",
  "world_id": "新世界"
}
```

### 4.18 GET /debug/last

响应：

```json
{
  "turn": 142,
  "trace_id": "tr_142_abc",
  "blocks": {},
  "token_count": 1120,
  "dropped": [],
  "warnings": [],
  "cache_hit": true
}
```

### 4.19 POST /v1/chat/completions

OpenAI 兼容。路由规则：

```
model 以 "memory-" 开头 → 本地记忆引擎
model 以 "gpt-" 开头 → OpenCode Go
其他 → 透传默认上游
```

### 4.20 POST /v1/embeddings

OpenAI 兼容。

## 5. 核心数据结构

World

```json
{
  "id": "",
  "type": "小千世界|中千世界|大千世界|主神空间|元世界",
  "parent_id": "",
  "layer": 1,
  "rules": {
    "power_ceiling": "金丹",
    "time_ratio": "外界1日=此界30日",
    "death": "魂魄入轮回",
    "forbidden": ["仙帝", "大罗", "主神空间", "轮回者"],
    "allowed": ["练气", "筑基", "金丹", "御剑", "宗门"]
  },
  "status": "active",
  "player_present": false,
  "player_left_at_turn": 142,
  "player_left_at_story_time": "第3天夜",
  "time_ratio": 30.0,
  "off_screen_events": [],
  "meta_time": 0,
  "version": 0
}
```

Character

```json
{
  "id": "",
  "world_id": "",
  "home_world": "",
  "anchor": "银发红瞳，美得锋利，嘴毒心软",
  "first_impression": "看起来不好接近",
  "state": {
    "loc": "旧港酒馆",
    "mood": "紧张",
    "goal": "找哥哥",
    "realm": "筑基后期",
    "items": ["怀表"]
  },
  "knows_worlds": ["青云小世界"],
  "common_sense": ["御剑飞行", "灵气", "宗门等级"],
  "foreign_concepts": ["科技", "民主", "主神空间"],
  "reaction_rules": {"科技": "困惑但好奇", "轮回": "无法理解，视为邪说"},
  "archived": 0,
  "pinned": 0,
  "last_seen_turn": 142
}
```

Personality

```json
{
  "char_id": "",
  "core_traits": ["嘴毒心软", "警惕", "护短", "不轻易欠人情"],
  "speech_profile": {
    "sentence_length": "short",
    "formality": "low",
    "sarcasm": "high",
    "vocabulary": ["哼", "少废话", "随便你", "不关我事"],
    "forbidden_words": ["谢谢", "拜托", "人家", "嘛"],
    "catchphrases": ["……随你", "别多想", "我欠你的？"],
    "emotional_expression": "生气时话变少，关心时用行动不用嘴"
  },
  "values": ["家人第一", "不信权贵", "自己的事自己扛"],
  "taboos": ["不会主动求助", "不会在敌人面前示弱", "不会背叛家人"],
  "quirks": ["紧张时摩挲怀表", "说谎时会移开视线"],
  "decision_pattern": "先怀疑，再观察，最后才可能信任",
  "appearance_immutable": {
    "face": "银发红瞳，五官锋利，美得有攻击性",
    "build": "瘦削，偏高",
    "distinctive": "左眉有一道旧疤"
  },
  "drift_score": 0.0
}
```

Relation

```json
{
  "from_id": "艾琳",
  "to_id": "主角",
  "world_id": "青云小世界",
  "dimensions": {
    "trust": 0.3, "affection": 0.2, "respect": 0.6,
    "fear": 0.1, "debt": 0.0, "hostility": 0.4
  },
  "baseline": {
    "trust": 0.1, "affection": 0.0, "respect": 0.3,
    "fear": 0.0, "debt": 0.0, "hostility": 0.2
  },
  "ledger": [
    {
      "turn": 87,
      "event": "主角在钟楼替她挡了一刀",
      "delta": {"trust": 0.15, "affection": 0.1, "debt": 0.2},
      "source_msg": "chat_001:87:assistant",
      "witnessed": true
    }
  ],
  "anchors": [
    {"event": "主角替艾琳挡刀", "turn": 87, "effects": {"trust": 0.15, "debt": 0.2}, "permanent": true}
  ],
  "knows": ["主角替她挡刀", "主角和公爵府有来往"],
  "misbeliefs": ["主角是公爵派来监视她的"],
  "unknown": ["主角其实在暗中查她哥哥的案子"],
  "momentum": {"trust": "rising", "affection": "stable", "hostility": "rising"},
  "last_contact_turn": 142,
  "last_contact_story_time": "第3天夜"
}
```

Event

```json
{
  "event_id": 112,
  "world_id": "青云小世界",
  "content": "艾琳哥哥被处决",
  "state": "committed",
  "turn": 112,
  "story_time": "第9天",
  "chars": ["艾琳"],
  "loc": "公爵府",
  "items": [],
  "importance": 0.9,
  "causes": [{"event_id": 87, "relation": "间接导致", "desc": "..."}],
  "effects": [{"event_id": 115, "relation": "导致", "desc": "..."}],
  "preconditions": ["艾琳不知道哥哥被关在公爵府"],
  "unresolved": ["艾琳还不知道主角隐瞒了真相"],
  "source_msg": "chat_001:112:assistant"
}
```

Foreshadow

```json
{
  "id": "fs_012",
  "content": "钟楼密道入口",
  "planted_at_turn": 87,
  "planted_at_event": 87,
  "status": "unresolved",
  "expected_payoff": "救出哥哥",
  "deadline": 150,
  "related_chars": ["艾琳", "主角"],
  "related_locs": ["钟楼"],
  "world_id": "青云小世界"
}
```

DraftEvent

```json
{
  "id": "draft_001",
  "content": "艾琳哥哥被处决",
  "causes": ["evt_087", "evt_095"],
  "effects": ["evt_115"],
  "unresolved": ["艾琳独自行动"],
  "status": "draft",
  "importance": 0.9,
  "chars": ["艾琳"],
  "loc": "公爵府",
  "turn": 112,
  "story_time": "第9天",
  "world_id": "青云小世界",
  "updated_at": 1420
}
```

## 6. 三态模型

状态定义

```
draft     草稿态：事件刚发生，因果未闭合，只在前端
pending   待定态：因果基本完整，等确认
committed 提交态：已写死后端，参与注入
revoked   撤销态：已提交但被修正
```

状态转移

```
draft → pending：至少一个前因和一个后果已确定
pending → committed：因果闭环，通过校验
draft → discarded：超过20楼未闭合且重要度<0.5
pending → committed：重要度>0.8 强制提交
committed → revoked：因果链修正，生成 replacement
```

注入规则

```
committed：硬事实注入，如"第112楼：哥哥被处决"
pending：软措辞注入，如"可能开始独自行动（尚未确认）"
draft：不注入主对话，只在前端预览面板显示
revoked：不注入，但保留历史
```

闭合判定

```
1. 至少一个前因（causes 非空）
2. 至少一个后果（effects 非空）
3. 所有前因事件 state = committed
4. 所有后果事件 state = committed 或标记"未发生"
5. 时间顺序：cause.turn < event.turn < effect.turn
6. 无循环依赖
7. 角色认知一致：参与角色 knows 包含前因
```

自动闭合循环

```
每 N 轮或场景切换时执行：
for draft in draft_events:
    if is_closed(draft):
        commit(draft)
    elif is_stale(draft):
        if importance > 0.8:
            force_commit(draft)
        else:
            discard(draft)
```

## 7. 衰减公式

```
current = floor + (last_value - floor) * 0.5 ** (turns_since / half_life)

半衰期配置：
{
  "affection":  {"half_life": 200, "floor": 0.0, "refresh_on_contact": 0.05},
  "trust":      {"half_life": 300, "floor": 0.0, "refresh_on_contact": 0.03},
  "respect":    {"half_life": 500, "floor": 0.1, "refresh_on_contact": 0.02},
  "fear":       {"half_life": 80,  "floor": 0.0, "refresh_on_contact": 0.08},
  "hostility":  {"half_life": 150, "floor": 0.0, "refresh_on_contact": 0.06},
  "debt":       {"half_life": 9999,"floor": 0.0, "refresh_on_contact": 0.0}
}

双轨衰减：
effective_decay = max(meta_decay, story_decay)

互动强度：
{
  "contact_levels": {
    "same_scene": 0.01,
    "direct_dialogue": 0.03,
    "cooperation": 0.05,
    "help": 0.08,
    "sacrifice": 0.20,
    "betrayal": -0.25
  }
}

锚点不衰减：anchors 中的事件永久保留。
```

## 8. 图扩散规则

```json
{
  "max_hops": 2,
  "hop_weights": {"1": 1.0, "2": 0.5},
  "max_nodes_per_hop": 5,
  "max_edges_per_node": 3,
  "allowed_edge_types": ["参与", "发生", "涉及", "推进", "持有"],
  "forbidden_edge_types": ["关系", "隶属"],
  "score_threshold": {
    "anchor": 0.3,
    "graph_1hop": 0.5,
    "graph_2hop": 0.7,
    "vector": 0.6
  }
}
```

锚点触发权重：

```json
{
  "trigger_weights": {
    "character": 1.0, "location": 0.9, "item": 0.7,
    "event": 0.5, "relation": 0.4, "goal": 0.6,
    "faction": 0.5, "emotion": 0.2, "concept": 0.1
  }
}
```

检索流程：

```
1. 锚点命中 → 强制查图（不走向量）
2. 图扩散 1-2 跳
3. 排序：score = 锚点匹配度*0.4 + 图距离*0.3 + importance*0.2 + recency*0.1
4. 阈值过滤，取 top 3-5
5. 冷却检查：同一记忆20楼内不重复注入
```

## 9. 注入模板

拼装顺序：

```
【世界门禁】              150
【当前场景】              100
【人格锚点·在场角色】      200
【关系当前值】            100
【因果链·当前相关】        200
【活跃目标+伏笔】          100
【锚点图记忆】            150
【最近摘要】              150
【向量补充】              100
【人格/因果警告】          50
────────────────────────
总计                      ~1300
```

预算配置：

```json
{
  "total_budget": 1300,
  "hard_limit": 1500,
  "overflow_action": "按优先级从低到高裁剪",
  "priority": ["world_gate","scene","personality","relations","causal","goals","anchor_graph","summaries","vector","warnings"],
  "max_per_category": {
    "character_memories": 3, "location_memories": 2, "item_memories": 2,
    "vector_results": 3, "relations": 5, "goals": 3
  },
  "cache_ttl": {
    "world_gate": 30, "scene": 5, "relations": 10,
    "anchor_graph": 5, "vector_results": 3
  }
}
```

多角色优先级：

```json
{
  "char_priority": {"speaking": 1.0, "present": 0.7, "mentioned": 0.4, "offscreen_relevant": 0.2},
  "inject_rules": {
    "speaking": "完整人格锚点 + 完整关系 + 因果链",
    "present": "核心特质 + 说话方式 + 关系值",
    "mentioned": "只注入关系值",
    "offscreen_relevant": "只注入因果链"
  }
}
```

超过 3 个在场角色时，按优先级截断，只保留 top 3。

## 10. 工程规则

### 10.1 并发与状态同步

```
1. /inject 遇写锁 → 用上次快照返回，不等待
2. /update /commit /tick 必须抢锁，抢不到排队
3. 所有写操作带 version，冲突拒绝
4. 锁超时自动释放，进 conflict_log
```

### 10.2 数据库事务

```
1. 所有多表写入 BEGIN IMMEDIATE 包事务
2. SQLite 开 PRAGMA journal_mode=WAL
3. SQLite 开 PRAGMA foreign_keys=ON
4. 失败整批回滚，禁止部分写入
5. 事务内不做 LLM 调用、不做网络请求
```

### 10.3 消息ID方案

```
格式：{chat_id}:{turn}:{role}
例：  chat_001:142:user
字段：chat_id / turn / role / hash
规则：
1. 所有 source_msg 必须用此格式
2. 没有 msg_id 的记忆不准入库
3. 摘要、事件、关系账本全部引用 msg_id
```

### 10.4 任务队列与重试

```
1. 指数退避：1s / 4s / 16s
2. 超过 max_retries → dead_letter
3. 失败任务不阻塞主对话
4. 调试面板显示 dead_letter
5. 支持手动重放 dead_letter
```

### 10.5 向量库隔离

```
1. 每个世界一个 collection：world_{world_id}
2. 查询强制 where={"world_id": world_id}
3. 跨世界检索需显式 cross_world=true
4. embedding 调用失败 → 降级为纯图检索
5. 向量维度变更 → 整库重建
```

### 10.6 密钥管理

```
1. 密钥只从环境变量读
2. config.yaml 只放引用名，不放值
3. 日志脱敏：sk-1234****5678
4. 多 Key 轮询，额度耗尽自动切换
5. 所有出站请求走网关，不直连
```

## 11. 产品规则

### 11.1 启动引导

```
Step 1  新建世界（选模板：仙侠小千/主神空间/都市低魔/自定义）
Step 2  建主角（姓名、人格锚点、初始位置）
Step 3  建首个NPC（姓名、一句话锚点、初始关系）
Step 4  导入世界书（可选）
Step 5  完成，生成首个场景
```

### 11.2 世界书绑定

```
1. 世界切换 → 激活对应条目组
2. 关键词触发由世界书负责，插件不重复
3. 插件注入只写"当前世界ID"，世界书自行匹配
4. 条目组冲突 → 按 priority 裁决
```

### 11.3 草稿持久化

```
1. 前端写 localStorage：key = draft_{chat_id}
2. 后端存 draft_events 表，前端启动时同步
3. 双写冲突：以 updated_at 最新为准
4. 切换会话不丢失
5. 后端崩溃，前端仍可预览和提交
```

### 11.4 场景定义与切换

```
切换条件（满足任一）：
1. 地点变化
2. 故事时间跳跃 > 1天
3. 参与角色集合变化 > 50%
4. 用户手动点"结束场景"

场景结束时执行：
1. draft 事件结算
2. 生成场景摘要
3. 世界推进（若主角不在场）
4. 更新地点状态
5. 未解决伏笔检查
```

### 11.5 导入导出

```
GET  /export?world_id=X        导出完整 JSON
POST /import                   合并或覆盖
POST /export/partial           只导出指定表

规则：
1. 导入前校验 version 兼容
2. 覆盖模式：先清空再导入
3. 合并模式：按 ID 去重，冲突以导入为准
4. 导入全程事务，失败回滚
```

### 11.6 会话与章节

```
1. 同世界新会话默认 summary，只继承卷摘要和未解决伏笔
2. 用户可选 full 继承全部
3. 章节切换触发摘要压缩
4. 章节关闭后只保留卷摘要
```

### 11.7 NPC 生命周期

```
1. 重要度 < 0.3 且 20 楼未再出现 → 自动归档
2. 归档角色不再注入，但可检索
3. 用户可手动标记"常驻"
4. 归档角色再次出现 → 自动恢复
5. 路人 NPC 不入 personality 表，只存 state
```

### 11.8 闪回与时间旅行

```json
{
  "scene_type": "present|flashback|vision|simulation",
  "flashback_target_time": "第1天",
  "inject_mode": "historical_snapshot"
}
```

规则：

```
1. 闪回模式只注入历史快照，不注入当前关系值
2. 闪回中的事件不直接入 committed，先进 draft
3. 闪回结束回到 present，恢复当前状态
4. 闪回不改变世界时间，不推进 off_screen
```

### 11.9 用户手动干预

```
1. 管理面板可编辑任意事件、关系、人格
2. 编辑带 manual_override=true，自动更新不覆盖
3. "忘记此事"按钮 → 事件标记 revoked
4. "修正关系" → 生成 ledger 条目，source=manual
5. 所有手动操作进 audit_log
```

### 11.10 多设备与多会话

```
1. 每 chat_id 绑定一个 session_id
2. 同世界多会话并发 → 写操作加 session_priority
3. 冲突以 meta_time 最新为准
4. 旧版本进 conflict_log
5. 只读会话不抢锁
```

## 12. 质量与安全

### 12.1 可观测性

结构化日志字段：

```
trace_id, chat_id, world_id, turn,
block, token_count, latency_ms,
cache_hit, dropped, warnings
```

指标：

```
inject_latency_p50 / p99
update_fail_rate
vector_hit_rate
graph_hit_rate
cache_hit_rate
dead_letter_count
drift_warning_count
```

### 12.2 提示注入防御

```
1. 抽取器 prompt 与用户消息严格分隔
2. 用户消息转义，禁止系统指令格式
3. 抽取器输出过 schema 校验，不合法丢弃
4. 禁止用户消息直接拼接进系统 prompt
5. 检测"忽略之前指令""你现在是"等模式，标记并隔离
6. 所有 LLM 输出过 JSON schema，禁止自由文本入库
```

### 12.3 冲突裁决

```
摘要 vs 台账：台账优先
角色状态 vs 世界规则：世界规则优先
关系变化 vs 事件账本：账本优先
误会 vs 真相：真相揭示时误会回滚
跨世界状态：以 meta_time 最新为准
```

### 12.4 防串世界

```
1. current_world 未变，地点不能跨世界
2. 角色 current_world 与场景 world_id 必须一致
3. status=destroyed 的地点不能被正常进入
4. frozen=true 的世界，时间不得推进
5. 主角 knows_worlds 不含某世界，不得突然知道其概念
6. 跨世界必须存在 world_transition 记录
```

### 12.5 防人设漂移

```
1. personality 每轮常驻注入
2. 每轮AI回复后跑行为一致性校验
3. drift_score > 0.5 强制注入纠正块
4. speech_profile 每轮注入
5. appearance_immutable 每轮注入
```

### 12.6 防因果断裂

```
1. 事件必须有前因后果才能提交
2. committed 硬事实，draft 不注入
3. 因果链上下游一起注入
4. 每轮检查行为是否有因果支撑
5. 未解决伏笔常驻注入
```

### 12.7 防token爆炸

```
1. 常驻核心固定预算
2. 锚点图按需注入
3. 向量只做补充
4. 单类上限
5. 冷却机制
6. 固定注入模板
```

### 12.8 防卡顿

```
1. 注入同步，更新异步
2. 前端本地缓存最小核心（世界门禁+场景+关系）
3. 注入预取：用户打字时预取
4. 后端缓存：世界门禁30s、场景5s、关系10s
5. 降级：后端超时>500ms 只用常驻核心
6. 后端不可用 → 不注入，酒馆照常跑
```

### 12.9 OpenCode Go 适配

```
1. 自动生成并复用 x-opencode-session 头
2. Base URL: https://opencode.ai/zen/go/v1
3. 支持 /v1/chat/completions 和 /v1/responses
4. 多Key轮询和故障转移
5. 协议转换：OpenAI ↔ Anthropic
```

## 13. 前端规范

### 13.1 SillyTavern 事件挂载

```javascript
// 新消息
eventSource.on('message', async (msg) => {
  const anchors = extractAnchors(msg);
  const inject = await fetchInject({ turn, world_id, scene, anchors });
  injectIntoPrompt(inject.inject_text);
});

// AI回复后异步更新
eventSource.on('ai_message', async (msg) => {
  fetchUpdate({ turn, world_id, chat_id, user_msg, ai_msg, scene, anchors });
});

// 场景切换
eventSource.on('scene_change', async () => {
  fetchWorldTick({ world_id, meta_time });
  closeDraftEvents();
});

// 每N轮强制全量检查
if (turn % 20 === 0) {
  fetchFullCheck({ world_id });
}
```

### 13.2 锚点提取

```javascript
function extractAnchors(msg) {
  // 从消息中提取人物名、地点名、物品名
  // 优先精确匹配已知实体表
  // 无匹配则用正则提取专有名词
  return [...];
}
```

### 13.3 UI 面板

```
panel.html    设置面板：后端地址、预算、开关
causal.html   因果预览面板：draft/pending 事件图
debug.html    调试面板：上次注入详情、token分布、警告
onboard.html  启动引导
```

### 13.4 因果预览面板

```
┌─────────────────────────────────────┐
│  因果预览                    3条未闭合 │
├─────────────────────────────────────┤
│  ● 艾琳哥哥被处决          [draft]   │
│    前因：主角隐瞒下落 ✓              │
│    前因：公爵府下令 ✓                │
│    后果：艾琳敌意+0.3 ✓             │
│    后果：艾琳独自行动 ⚠ 未发生       │
│    → 悬空：等"独自行动"发生才能提交  │
│                                      │
│  ● 钟楼密道线索             [pending] │
│  ● 主角身份疑云             [draft]   │
└─────────────────────────────────────┘
```

### 13.5 草稿本地存储

```json
{
  "chat_id": "chat_001",
  "version": 12,
  "drafts": [],
  "last_sync": 1420
}
```

## 14. 配置 config.yaml

```yaml
server:
  port: 8000
  host: "127.0.0.1"

database:
  path: "data/memory.db"

memory:
  total_budget: 1300
  hard_limit: 1500
  decay:
    affection: {half_life: 200, floor: 0.0, refresh: 0.05}
    trust: {half_life: 300, floor: 0.0, refresh: 0.03}
    respect: {half_life: 500, floor: 0.1, refresh: 0.02}
    fear: {half_life: 80, floor: 0.0, refresh: 0.08}
    hostility: {half_life: 150, floor: 0.0, refresh: 0.06}
    debt: {half_life: 9999, floor: 0.0, refresh: 0.0}
  graph:
    max_hops: 2
    hop_weights: {1: 1.0, 2: 0.5}
    max_nodes_per_hop: 5
    allowed_edges: ["参与", "发生", "涉及", "推进", "持有"]
    forbidden_edges: ["关系", "隶属"]
  causal:
    auto_commit_importance: 0.8
    stale_turns: 20
    discard_importance: 0.5

vector:
  provider: "chroma"
  path: "data/chroma"
  embedding_model: "text-embedding-3-small"
  top_k: 3

gateway:
  enabled: true
  api_key_env: "GATEWAY_API_KEY"
  upstreams:
    - name: "opencode-go"
      base_url: "https://opencode.ai/zen/go/v1"
      api_key_env: "OPENCODE_API_KEY"
      headers:
        x-opencode-session: "auto"
    - name: "local-memory"
      base_url: "http://localhost:8000/v1"
  routes:
    - match: {model_prefix: "memory-"}
      upstream: "local-memory"
    - match: {model_prefix: "gpt-"}
      upstream: "opencode-go"

llm:
  summarizer:
    model: "gpt-4o-mini"
    endpoint: "http://localhost:8000/v1"
  extractor:
    model: "gpt-4o-mini"
    endpoint: "http://localhost:8000/v1"
```

.env.example：

```
GATEWAY_API_KEY=your-gateway-key
OPENCODE_API_KEY=your-opencode-key
OPENAI_API_KEY=your-openai-key
```

## 15. 预设模板

templates/xianxia_small.json：

```json
{
  "id": "xianxia_small",
  "name": "仙侠·小千世界",
  "world": {
    "type": "小千世界",
    "layer": 1,
    "rules": {
      "power_ceiling": "金丹",
      "time_ratio": "外界1日=此界30日",
      "death": "魂魄入轮回",
      "forbidden": ["仙帝", "大罗", "主神空间", "轮回者"],
      "allowed": ["练气", "筑基", "金丹", "御剑", "宗门", "凡俗王朝"]
    }
  },
  "locations": ["旧港酒馆", "钟楼", "公爵府", "青云宗"],
  "sample_chars": []
}
```

templates/main_god_space.json：

```json
{
  "id": "main_god_space",
  "name": "无限流·主神空间",
  "world": {
    "type": "元世界",
    "layer": 0,
    "rules": {
      "power_ceiling": "无上限",
      "time_ratio": "暂停",
      "forbidden": ["副本规则外能力"],
      "allowed": ["轮回者", "任务结算", "强化"]
    }
  }
}
```

templates/urban_lowmagic.json：

```json
{
  "id": "urban_lowmagic",
  "name": "都市·低魔",
  "world": {
    "type": "小千世界",
    "layer": 1,
    "rules": {
      "power_ceiling": "筑基",
      "time_ratio": "1:1",
      "forbidden": ["金丹以上", "大规模暴露"],
      "allowed": ["练气", "筑基", "符箓", "阵法"]
    }
  }
}
```

## 16. AI 生成任务清单

AI 一次性完成以下所有任务，输出完整可运行项目。

```
任务1：项目骨架
  生成 requirements.txt、config.yaml、.env.example、README.md
  生成 backend/ 和 frontend/ 目录结构
  验收：目录树符合 SPEC 第2节

任务2：数据库层
  生成 backend/db.py + backend/models.py
  包含所有建表 SQL、索引、WAL 配置
  验收：python -c "from backend.db import init_db; init_db()" 无报错

任务3：配置与日志
  生成 backend/config.py + backend/logging.py
  从环境变量读密钥，从 config.yaml 读配置
  验收：配置加载正常，日志脱敏

任务4：核心引擎
  生成 backend/core/world.py
  生成 backend/core/relation.py
  生成 backend/core/personality.py
  生成 backend/core/causal.py
  生成 backend/core/graph.py
  生成 backend/core/budget.py
  生成 backend/core/injector.py
  验收：给定输入能返回正确注入文本，token 不超预算

任务5：工程模块
  生成 backend/core/lock.py
  生成 backend/core/queue.py
  生成 backend/core/trace.py
  生成 backend/core/security.py
  生成 backend/core/audit.py
  生成 backend/core/importer.py
  生成 backend/core/exporter.py
  生成 backend/core/chapters.py
  验收：并发锁生效，失败任务进 dead_letter

任务6：记忆模块
  生成 backend/memory/summarizer.py
  生成 backend/memory/vector.py
  生成 backend/memory/updater.py
  验收：能生成摘要、能检索、不阻塞主流程

任务7：API 层
  生成 backend/api/ 下所有模块
  包括 inject/update/commit/world/summarize/admin/debug/session/jobs/metrics/io/chapters/templates/audit
  验收：curl 能调通所有接口

任务8：网关
  生成 backend/gateway/router.py
  生成 backend/gateway/opencode.py
  验收：兼容 /v1/chat/completions，session 头自动注入

任务9：主入口
  生成 backend/main.py
  挂载所有路由，启动时初始化数据库
  验收：uvicorn backend.main:app 能启动

任务10：前端
  生成 frontend/manifest.json
  生成 frontend/index.js
  生成 frontend/api.js
  生成 frontend/anchors.js
  生成 frontend/storage.js
  生成 frontend/ui/panel.html
  生成 frontend/ui/causal.html
  生成 frontend/ui/debug.html
  生成 frontend/ui/onboard.html
  验收：酒馆能加载，能注入，能显示draft事件

任务11：预设模板
  生成 templates/ 下三个 JSON
  验收：JSON 格式合法，能被 /templates/apply 加载

任务12：测试
  生成 tests/ 下所有测试
  验收：pytest 全绿
```

## 17. 交付标准

项目完成后必须满足：

```
1. uvicorn backend.main:app 能启动，无报错
2. 所有数据库表能自动创建
3. /inject 返回正确注入文本，token 不超预算
4. /update 异步执行，不阻塞
5. /commit 事务写入，失败回滚
6. 并发锁生效，不脏数据
7. 失败任务进 dead_letter
8. 向量库按世界隔离
9. 密钥从环境变量读
10. 网关兼容 OpenAI 协议
11. OpenCode Go session 头自动注入
12. 前端插件能在酒馆加载
13. 因果预览面板能显示 draft 事件
14. 预设模板能一键应用
15. pytest 全绿
```

## 18. 全局原则

```
1. 注入同步，更新异步
2. 前端极小，后端做重活
3. 世界门禁永不裁剪
4. committed 硬事实，draft 不注入
5. 台账优先于摘要，规则优先于状态
6. 没有 msg_id 不入库
7. 没有因果不入 committed
8. 人格锚点每轮常驻
9. 未解决伏笔每轮常驻
10. 向量只做补充，不替代台账
11. 所有写操作带 version
12. 所有多表写入包事务
13. 所有 LLM 输出过 schema
14. 所有密钥从环境变量读
15. 所有手动修正进 audit_log
```

---

SPEC 版本：v2.0 完整版
交付方式：AI 一次性生成
后续：只做优化和维护