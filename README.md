# 记忆插件 · Memory Engine

**v2.1.0** · [更新日志](CHANGELOG.md) · [规格书](SPEC.md)

为 SillyTavern 提供**独立后端记忆引擎**，解决长篇对话中的三大顽疾：

| 问题 | 对策 |
| --- | --- |
| 世界串线（仙侠 / 无限流串味） | 世界门禁 + 跨世界校验 |
| 人设漂移（角色越写越不像） | 人格锚点常驻注入 + 逐轮一致性校验 |
| 因果断裂（事件孤立、动机断裂） | 三态因果（draft → pending → committed）+ 因果链注入 |

前端只做 UI 与转发，后端做所有重活，通过 OpenAI 兼容网关通信。

---

## 1. 快速开始

### 1.1 装到 SillyTavern（前端插件）

**方式 A：URL 安装（推荐）**

酒馆 → 扩展面板 → **Install extension** → 粘贴：

```
https://github.com/joker-3342/memory-plugin
```

仓库根目录的 `manifest.json` 会把酒馆指向 `frontend/`，装完重启酒馆即可。

**方式 B：手动放**

```bash
# 克隆后，只把 frontend/ 的内容拷进扩展目录
git clone https://github.com/joker-3342/memory-plugin.git
mkdir -p "SillyTavern/data/<user>/extensions/memory-plugin"
cp -r memory-plugin/frontend/* "SillyTavern/data/<user>/extensions/memory-plugin/"
```

> `frontend/` 里也带一份 `manifest.json`（js 指向同目录的 `index.js`），
> 所以方式 B 直接可用；根目录那份是给方式 A 的，两者不冲突。

装好后在「扩展」面板找到 **记忆插件 · Memory Engine**：

**它会自己找后端** —— 启动时按候选列表**并行**探测 `/health`，命中即用，
状态栏显示绿点 + 后端版本：

```
● 已连接 http://127.0.0.1:8080 · 后端 v2.1.0 · 向量 local-hash · 队列 pending=0/dead=0
```

探测不到就显示红点并提示启动命令，此时插件降级运行（不注入，酒馆照常跑）。

**「世界 ID」也可以留空** —— 留空时自动取当前角色卡名，一般不用手填。

候选地址（并行探测，约 1 秒出结果）：

```
http://127.0.0.1:8000   http://localhost:8000   http://<酒馆主机>:8000   http://10.0.2.2:8000
http://127.0.0.1:8080   http://localhost:8080   …（端口还会试 8080 / 8001）
```

> 只有 `/health` 返回 `{"ok":true}` 才算命中 ——
> 所以 8000 上跑着别的 Web 服务时**不会被误连**（这点有测试覆盖）。

**界面长这样：**

| 区域 | 内容 |
| --- | --- |
| 状态栏 | 绿/红点 · 后端地址与版本 · 队列 pending/dead · 「重新连接」 |
| 设置 | 后端地址（留空 = 自动）· 世界 ID（留空 = 自动）· token 预算 · 注入超时 · 全量检查间隔 |
| 开关 | 启用插件 · 自动探测后端 · 显示调试区 |
| 操作 | 保存 · 测试连接 · 初始化世界 · 应用模板 · 导出世界 · 同步实体表 |
| 因果预览 | 「未闭合 N / 共 M」计数 + 卡片列表（前因 / 后果 / 是否可提交）+「提交已闭合」 |
| 调试区 | 注入详情 · 队列 · 日志 · 错误日志 |
| 启动引导 | 折叠区，5 步上手 |

> 界面片段放在 `frontend/ui/*.html`；万一取不到（CSP / 路径异常），
> `index.js` 里内置了一份同样的兜底 HTML，**保证任何环境都有界面**。

### 1.2 启动后端（**必须**，否则插件只会降级不注入）

前端插件只是 UI 与转发，**所有记忆逻辑都在后端**。后端没跑起来时，
插件会自动降级为「只注入常驻核心」，并在酒馆里提示「后端无响应」。

```bash
cd memory-plugin

# 1) 依赖
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 2) 密钥（只从环境变量读，config.yaml 里只放引用名）
cp .env.example .env       # 填入 GATEWAY_API_KEY / OPENCODE_API_KEY
export $(grep -v '^#' .env | xargs)

# 3) 启动
uvicorn backend.main:app --host 127.0.0.1 --port 8000
# 或： python -m backend.main
```

> 手机端（Termux/Operit）同样可跑：`pip install -r requirements.txt` 后
> `python -m backend.main` 即可；插件会自动探测到它，同机无需填地址。

> **端口被占用怎么办**：默认端口是 8000，但不少机器上 8000 已被别的服务占用
> （uvicorn 会报 `[Errno 98] address already in use`）。换个端口，插件会自动发现：
>
> ```bash
> # 方式一：环境变量（插件候选端口含 8080，能自动命中）
> MEMORY_PLUGIN_PORT=8080 python -m backend.main
>
> # 方式二：uvicorn 直接指定
> uvicorn backend.main:app --host 127.0.0.1 --port 8080
>
> # 方式三：改 config.yaml 的 server.port
> ```
>
> 若你的端口不在候选列表里（8000/8080/8001），在面板「后端地址」填完整地址即可，
> 例如 `http://127.0.0.1:9000`。

### 1.3 自检

```bash
curl http://127.0.0.1:8000/health
python -c "from backend.db import init_db; init_db()"   # 建表无报错
pytest -q                                                # 全绿
```

---

## 2. 验收对照（SPEC 第 17 节）

| # | 交付标准 | 状态 | 验证方式 |
| --- | --- | --- | --- |
| 1 | `uvicorn backend.main:app` 能启动 | ✅ | 冒烟测试：`/health` 返回 `{"ok":true,...}` |
| 2 | 所有数据库表能自动创建 | ✅ | 启动即建表，实测 **27 张表** |
| 3 | `/inject` 返回注入文本且不超预算 | ✅ | 实测 `token_count=142`，硬上限 1500 |
| 4 | `/update` 异步执行不阻塞 | ✅ | 入队 3 个任务，后台线程消费，`done=3` |
| 5 | `/commit` 事务写入，失败回滚 | ✅ | `db.tx()` 包事务，异常整批 `ROLLBACK` |
| 6 | 并发锁生效，无脏数据 | ✅ | 跨 holder 抢锁返回 `acquired=false` |
| 7 | 失败任务进 dead_letter | ✅ | 1s/4s/16s 退避，超限入死信 |
| 8 | 向量库按世界隔离 | ✅ | collection = `world_{world_id}`，查询强制 `where` |
| 9 | 密钥从环境变量读 | ✅ | `config.secret()` 只读 env，日志 `mask()` 脱敏 |
| 10 | 网关兼容 OpenAI 协议 | ✅ | `/v1/chat/completions`、`/v1/embeddings`、`/v1/models` |
| 11 | OpenCode Go session 头自动注入 | ✅ | `x-opencode-session: auto` → 自动生成并复用 |
| 12 | 前端插件能在酒馆加载 | ✅ | ES module + `manifest.json`，事件挂载齐全 |
| 13 | 因果预览面板能显示 draft 事件 | ✅ | `ui/causal.html` + `/debug/drafts` |
| 14 | 预设模板能一键应用 | ✅ | 3 个模板，`/templates/apply` 实测通过 |
| 15 | pytest 全绿 | ✅ | **52 passed** |

---

## 3. 目录结构

```
memory-plugin/
├── SPEC.md                     # 唯一规格书（本项目实现的依据）
├── README.md
├── config.yaml                 # 配置（只放引用名，不放密钥）
├── requirements.txt
├── .env.example
├── backend/
│   ├── main.py                 # FastAPI 入口 + 后台任务线程
│   ├── config.py               # 配置加载（YAML + env 覆盖）
│   ├── db.py                   # SQLite(WAL) 连接 / 建表 / 迁移 / 事务
│   ├── models.py               # Pydantic 请求响应模型
│   ├── logging.py              # structlog（缺失时自动降级）
│   ├── api/                    # 14 个路由模块（见第 5 节）
│   ├── core/                   # 世界 / 人格 / 因果 / 图 / 预算 / 锁 / 队列 …
│   ├── memory/                 # 摘要 / 向量 / 更新流水线
│   └── gateway/                # OpenAI 兼容网关 + OpenCode Go 适配
├── frontend/                   # SillyTavern 扩展
│   ├── manifest.json / index.js / api.js / anchors.js / storage.js / style.css
│   └── ui/{panel,causal,debug,onboard}.html
├── templates/                  # 仙侠小千 / 主神空间 / 都市低魔
├── tests/                      # pytest（world / relation / causal / injector / api）
└── data/                       # 运行时：memory.db + chroma/
```

---

## 4. 核心设计要点

### 4.1 三态因果（拒绝"想到哪写到哪"）

```
draft     草稿态：因果未闭合，只在前端预览面板显示，不注入主对话
pending   待定态：因果基本完整，以软措辞注入（"可能…（尚未确认）"）
committed 提交态：硬事实注入（"第112楼：哥哥被处决"）
revoked   撤销态：不注入，但保留历史
```

闭合判定 7 条（`core/causal.py::is_closed`）：有前因、有后果、前因均为 committed、
后果已结算、时间顺序正确、无循环依赖、角色认知一致。

### 4.2 单维度半衰期衰减（拒绝"莫名好感"）

```
current = floor + (last_value - floor) * 0.5 ** (turns_since / half_life)
effective_decay = max(meta_decay, story_decay)      # 双轨衰减
```

affection 半衰期 200、trust 300、respect 500、fear 80、hostility 150、debt 9999；
`anchors`（挡刀、救命之恩）**永不衰减**。

### 4.3 固定注入模板 + 优先级裁剪

```
【世界门禁】150 →【当前场景】100 →【人格锚点·在场角色】200 →【关系当前值】100
→【因果链·当前相关】200 →【活跃目标+伏笔】100 →【锚点图记忆】150
→【最近摘要】150 →【向量补充】100 →【人格/因果警告】50      合计 ~1300
```

超预算时从优先级**最低**处裁剪；**世界门禁永不裁剪**。

### 4.4 降级策略（酒馆永远不卡）

- 注入同步、更新异步：`/inject` 同步返回，`/update` 只入队；
- `/inject` 遇写锁 → 直接返回上次快照，**不等待**；
- 前端注入超时 500ms → 只用本地常驻核心（世界门禁 + 场景 + 关系）；
- 后端整体不可用 → 前端不注入，酒馆照常跑；
- 向量库不可用 → 降级为纯图检索；embedding 不可用 → 降级为本地哈希嵌入。

### 4.5 安全

- 用户消息**严格围栏**，不拼接进系统 prompt；
- 检测"忽略之前的指令""你现在是"等模式并标记隔离；
- 所有 LLM 输出过 JSON schema 校验，不合法丢弃；
- 密钥只从环境变量读，日志脱敏为 `sk-12****ef90`。

---

## 5. API 一览

### 写入 & 读取

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/inject` | 同步注入（拼装 10 个块 + 预算裁剪） |
| POST | `/update` | 异步更新（入队 summarize / relation_update / event_extract） |
| POST | `/update/sync` | 同步执行（调试用） |
| POST | `/commit` | 因果提交（事务写事件 + 因果边） |
| POST | `/commit/preview` | 提交前闭合判定预览 |
| POST | `/commit/auto` | 自动闭合循环 |
| POST | `/commit/revoke` | 撤销事件 |
| POST | `/world/tick` | 世界推进（off_screen 事件） |
| POST | `/world/create` · `GET /world/{id}` · `GET /worlds` | 世界管理 |
| POST | `/world/transition` | 跨世界转移（防串世界第 6 条） |
| POST | `/world/scene/close` | 场景结束流程 |
| POST | `/world/check-points` | 每 N 轮强制全量检查 |
| POST | `/summarize` · `GET /summaries` · POST `/summaries/compress` | 摘要 |
| POST | `/session/lock` · `/session/unlock` · `GET /session/conflicts` | 并发锁 |
| GET | `/jobs/status` · POST `/jobs/retry` · `/jobs/process` | 任务队列 |
| GET | `/export` · POST `/import` · POST `/export/partial` | 导入导出 |
| POST | `/chapters/close` · `/chapters/open` · `GET /chapters` · `/chats/create` | 章节会话 |
| GET | `/templates` · POST `/templates/apply` | 预设模板 |
| POST | `/admin/{table}` · GET · DELETE | 通用 CRUD（白名单 18 张表） |
| POST | `/admin/override` · `/admin/forget` · `/admin/relation-fix` | 手动干预（全部进 audit_log） |
| GET | `/debug/last` · `/debug/drafts` · `/debug/state` | 调试面板数据 |
| GET | `/audit` · `/audit/drift` | 审计 |
| GET | `/metrics` | 指标 |

### OpenAI 兼容网关

| 方法 | 路径 | 路由规则 |
| --- | --- | --- |
| POST | `/v1/chat/completions` | `memory-*` → 本地记忆引擎；`gpt-*` → OpenCode Go；其他 → 默认上游 |
| POST | `/v1/responses` | 同上 |
| POST | `/v1/embeddings` | 本地哈希嵌入兜底（无外网可用） |
| GET | `/v1/models` | 模型列表 |

---

## 6. 配置

`config.yaml` 只放**引用名**，值来自环境变量：

```yaml
gateway:
  upstreams:
    - name: "opencode-go"
      base_url: "https://opencode.ai/zen/go/v1"
      api_key_env: "OPENCODE_API_KEY"     # ← 只放名字
      headers:
        x-opencode-session: "auto"        # ← 自动生成并复用
```

多 Key 轮询：`OPENCODE_API_KEY`、`OPENCODE_API_KEY_2`、`OPENCODE_API_KEY_3` …
或逗号分隔。额度耗尽自动切换。

环境变量覆盖：`MEMORY_PLUGIN_HOST`、`MEMORY_PLUGIN_PORT`、`MEMORY_PLUGIN_DB`。

---

## 7. 调试与运维

```bash
# 上次注入详情（token 分布、被丢弃的块、警告）
curl http://127.0.0.1:8000/debug/last

# 未闭合的因果草稿（因果预览面板数据源）
curl "http://127.0.0.1:8000/debug/drafts?world_id=青云小世界"

# 世界状态 + 锁 + 队列 + 漂移计数
curl "http://127.0.0.1:8000/debug/state?world_id=青云小世界"

# 死信重放
curl -X POST http://127.0.0.1:8000/jobs/retry -d '{"replay_dead_letters":true}' -H 'Content-Type: application/json'

# 手动修正关系（生成 source=manual 的 ledger 条目）
curl -X POST http://127.0.0.1:8000/admin/relation-fix -H 'Content-Type: application/json' \
  -d '{"from_id":"艾琳","to_id":"主角","delta":{"trust":0.2},"reason":"手动修正","turn":142}'
```

### 日志与排查（出 bug 先看这里）

日志双出口：控制台 + 文件。**进程退了控制台就没了，文件才是证据。**

```
data/logs/memory-plugin.log   全量（JSON 一行一条，5MB × 5 轮转）
data/logs/error.log           只收 ERROR 以上，带完整堆栈
```

```bash
# 看日志尾巴（不用连服务器，直接读文件也行）
curl "http://127.0.0.1:8000/debug/logs?lines=200"
curl "http://127.0.0.1:8000/debug/logs?lines=200&json_format=true"   # 解析成对象
curl "http://127.0.0.1:8000/debug/logs/errors?lines=100"             # 只看错误 + 堆栈

# 确认落盘链路是通的
curl -X POST http://127.0.0.1:8000/debug/log-test -d '{"level":"info","message":"hello"}' \
  -H 'Content-Type: application/json'

# 命令行直查
tail -f data/logs/memory-plugin.log
grep '"event": "job_dead_letter"' data/logs/error.log
grep '"trace_id": "tr_142_xxx"' data/logs/memory-plugin.log      # 顺着一条链路看全过程
```

**日志里能直接回答的问题：**

| 症状 | 看哪条 |
| --- | --- |
| 注入为什么没生效 / 是旧的 | `inject_served_from_snapshot`（撞写锁）、`inject_built`（token 数、被丢弃的块） |
| 更新任务为什么没写库 | `job_failed`（堆栈）、`job_retry`、`job_dead_letter` |
| 哪里慢 | `operation_slow`（`slow_ms` 默认 200ms）、`job_slow`、`http_request_slow` |
| 线程悄悄崩了 | `uncaught_exception_thread`、`worker_error`（都带堆栈） |
| 某个接口报错 | `unhandled_exception`（带 `trace_id`，响应体里也有同一个 id） |

每条日志都带上下文：`trace_id` / `world_id` / `chat_id` / `turn`（`with log_context(...)` 或
`bind_context(...)` 自动注入），以及 `module`（db / queue / inject / lock …）用来定位模块。
密钥类字段（`*_key` / `*_token` / `password` …）自动打码为 `sk-12****ef90`，**日志里不会有明文密钥**。

配置：

```yaml
logging:
  dir: "data/logs"
  level: "INFO"      # 也可用环境变量 MEMORY_PLUGIN_LOG_LEVEL 覆盖
  slow_ms: 200       # 慢操作告警阈值；0 = 关闭
  max_bytes: 5242880
  backup_count: 5
```

`MEMORY_PLUGIN_LOG_JSON=1` 可强制控制台输出 JSON（默认 TTY 下是人读格式，重定向时自动 JSON）。

---

## 8. 测试

```bash
pytest -q                      # 52 passed
pytest tests/test_causal.py -q # 只跑因果链
```

覆盖：世界状态机 / 门禁 / 防串世界、半衰期衰减 / 双轨衰减 / 锚点不衰减、
三态因果闭环 / 自动闭合 / 撤销、注入拼装 / 预算裁剪 / 闪回模式、
人格一致性校验、以及 18 个 API 端到端用例。

---

## 9. 已验证的运行时事实

- 启动日志：`{"event":"startup","version":"2.1.0","vector":"local-hash"}` → `Application startup complete.`
- 建表：27 张表（含索引）
- `/inject` 实测输出：世界门禁 → 当前场景 → 人格锚点 → 锚点图记忆，`token_count=142`
- `/update` 实测：入队 `["summarize","relation_update","event_extract"]`，后台线程消费 3 个任务全部 `done`
- 因果草稿自动提取：从 AI 回复中抽出「艾琳被发现了，她决定独自行动」进入 draft 态
- `pytest -q` → **62 passed**（v2.1.0；v2.0.0 时为 52）
- 日志落盘：`data/logs/memory-plugin.log` 每行一条 JSON；ERROR 另存 `error.log`（含 `traceback`）
- 未捕获异常定位：故意传坏参数 → `error.log` 直接指到 `backend/api/admin.py:205` 的具体行号，响应体带同一个 `trace_id`
- 前端自动连接：8000 被别的服务占用时正确跳过（不误连），命中 8080，`/inject` 返回 112 tokens

---

SPEC 版本：v2.0 完整版 · 交付方式：一次性生成 · 后续：只做优化和维护
