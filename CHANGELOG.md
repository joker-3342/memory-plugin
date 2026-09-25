# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)：`主版本.次版本.修订号`。

## v2.3.0 — 2026-09-25

### 改动

- **界面改成悬浮弹窗**（不再塞在扩展设置的下拉框里）
  - 屏幕右下角常驻**悬浮按钮**（右下角小圆点：绿=已连接 / 红=未连接）
  - 点开是**全屏级弹窗**：头部（品牌 + 状态 + 刷新 + ✕）+ 左侧导航 + 右侧内容
  - 关闭方式：✕ / 点遮罩 / `ESC`
  - 手机端弹窗自动全屏，导航收成横排
  - 扩展设置里保留一个「打开记忆面板」入口按钮
  - 「隐藏悬浮按钮」（headless）可关掉整套 UI，只保留注入

### 说明

- 悬浮按钮/弹窗挂在 `document.body` 上，`z-index` 30000+，不会被酒馆 UI 压住

---

## v2.2.0 — 2026-09-25

### 新增

- **记忆浏览界面**（`frontend/views.js` + `ui/panel.html` + `style.css`）
  - 左侧导航 + 右侧内容，10 个页面：总览 / 剧情摘要 / 角色档案 / 人际关系 / 因果链 /
    世界设定 / 物品追踪 / 目标与伏笔 / 设置 / 调试
  - **把后端记的东西真正显示出来**：人格锚点（核心特质 / 说话方式 / 禁用词 / 不可变外观 / 漂移值）、
    六维关系（条形图 + 账本时间线 + 永久锚点 + 知道/误以为/不知道）、事件账本、
    因果链（含闭合判定）、摘要、物品、目标、伏笔、世界门禁
  - 深色主题，颜色全部走 `--mp-*` CSS 变量（改一行换肤）
  - 窄屏自动折叠导航（手机可用）
- **`GET /ui/snapshot?world_id=X`**：一个请求拿全世界的记忆（界面渲染只用 1 个请求）
- **`frontend/UI.md`**：UI 定制指南（数据字段表 / 渲染函数 / DOM 契约 / 换肤 / headless）
- **headless 模式**：设置里勾「隐藏本面板」→ 只注入不渲染，方便自己写界面
- 后台**静默重连**（5s→60s 退避）：后端没起来时不再要求用户点「重连」
- 连上的地址写入 localStorage，下次启动 0 延迟直连

### 修复

| 问题 | 影响 |
| --- | --- |
| `/admin/{table}` 强制要求主键 | **自增主键表（events / causal_edges / summaries …）无法新增行** |
| 界面把「后端地址 / 请启动后端」暴露给用户 | 不该给用户看开发者文案；连接细节收进「高级」 |

### 测试

- 后端 `pytest`：**62 passed**
- 界面渲染测试：**18 / 18**（9 个页面渲染成功 + 9 项内容抽查：核心特质 / 禁用词 /
  不可变外观 / 认知边界 / 六维 / 账本 / 误解 / 闭合判定 / 事件账本）

---

## v2.1.0 — 2026-09-25

### 新增

- **结构化日志模块**（`backend/logging.py`，427 行）
  - 控制台 + 文件双出口：`data/logs/memory-plugin.log`（5MB × 5 轮转）
  - `error.log` 单独收 ERROR 以上，**带完整堆栈**
  - `trace_id` / `world_id` / `chat_id` / `turn` 上下文自动注入（`log_context` / `bind_context`）
  - 密钥字段（`*_key` / `*_token` / `password` …）自动打码为 `sk-12****ef90`
  - 慢操作告警 `slow()`（默认阈值 200ms）、未捕获异常钩子（主线程 + 子线程）
  - 读尾巴：`tail()` / `tail_json()` / `tail_errors()`
- **日志接入点**：db（慢查询 + 异常堆栈）、queue（job_done/retry/dead_letter）、lock（锁冲突）、
  injector（注入块与 token 统计）、api/inject、api/debug、main（每请求一条 + `X-Trace-Id` 响应头）
- **调试接口**：`GET /debug/logs`、`GET /debug/logs/errors`、`POST /debug/log-test`
- **后端自动探测**（前端）：候选地址**并行**探测 `/health`，命中即用并写回设置；
  请求失败自动重新探测；端口候选 `8000 / 8080 / 8001`，兼容「8000 被别的服务占用」的情况
- **世界 ID 自动推断**：留空时自动取当前角色卡名，一般无需手填
- **完整 UI 面板**：连接状态栏（绿/红点 + 后端版本 + 队列计数）、设置区、开关、
  操作按钮（保存 / 测试连接 / 初始化世界 / 应用模板 / 导出世界 / 同步实体表）、
  因果卡片列表（前因 / 后果 / 可提交判定）+「提交已闭合」、调试区、折叠式启动引导
- **界面兜底**：`ui/*.html` 取不到时使用 `index.js` 内置的同款 HTML，保证不白屏
- **根目录 `manifest.json`**：支持酒馆「Install extension」直接填仓库 URL 安装
- `CHANGELOG.md`（本文件）

### 修复

| 问题 | 影响 |
| --- | --- |
| `get_logger("db")` 等子模块 logger 未挂到主 logger | 日志**静默丢失**（handler 挂不上） |
| `autoConnect` 探测失败未清空旧地址 | 后续请求持续打一个**已失效的地址** |
| SQLite 保留字 `values` / `desc` 未加引号 | 建表直接报 `syntax error` |
| `/admin/override` 被 `/admin/{table}` 通配路由抢占 | 手动覆盖接口 404/错路由 |
| `Body(embed=True)` 与普通 `Body` 混用 | 管理接口整包 422 |
| `slow()` 的 `threshold_ms=0` 被当成「关闭」 | 显式阈值失效 |

### 测试

- 后端 `pytest`：**52 → 62 passed**（新增 `tests/test_logging.py` 10 项）
- 前端 mock 逻辑测试：**24 / 24**（自动探测、多端口候选、降级、`extractCore`）
- 前端真机 HTTP 联调：**14 / 14**（自动跳过被占用的 8000，命中 8080，注入 112 tokens）

### 工程

- 后端 Python **7303 行**，前端 JS/CSS/HTML **1487 行**
- 仓库 71 个文件，无缓存 / 日志 / 数据库 / 密钥入库

---

## v2.0.0 — 2026-09-25

按 SPEC v2.0 一次性生成的全部 12 个任务：

- **核心引擎**：世界门禁 / 防串世界、人格锚点与漂移校验、三态因果（draft → pending → committed）、
  单维度半衰期关系衰减（双轨 + 锚点不衰减）、锚点图扩散、token 预算与优先级裁剪
- **工程模块**：并发锁（version 冲突拒绝）、任务队列（1s/4s/16s 退避 + 死信）、
  提示注入防御、审计日志、导入导出、章节与会话
- **记忆模块**：摘要（LLM + 启发式降级）、向量检索（Chroma / 本地哈希降级，按世界隔离）、
  更新流水线（异步）
- **API 层**：14 个路由模块（inject / update / commit / world / summarize / admin / debug /
  session / jobs / metrics / io / chapters / templates / audit）
- **网关**：OpenAI 兼容（`/v1/chat/completions`、`/v1/responses`、`/v1/embeddings`、`/v1/models`）
  + OpenCode Go 适配（session 头自动复用、多 Key 轮询、Anthropic 协议回退）
- **前端**：SillyTavern 扩展（事件挂载、锚点提取、localStorage 草稿同步）
- **预设模板**：仙侠小千 / 主神空间 / 都市低魔
- **测试**：pytest 52 项全绿