# 前端 UI 定制指南

给「想自己设计界面」的人。整套前端是分层的，你可以只改其中一层。

## 文件职责

| 文件 | 干什么 | 改它会影响 |
| --- | --- | --- |
| `ui/panel.html` | 骨架（导航 + 内容容器 + 设置页 + 调试页） | 页面结构、导航项 |
| `style.css` | 皮肤（全部走 `--mp-*` CSS 变量） | 颜色、间距、圆角 |
| `views.js` | 数据 → HTML（**纯函数**，不碰事件、不发请求） | 每页长什么样 |
| `index.js` | 事件挂载 + 导航切换 + 把 api/views 接起来 | 交互逻辑 |
| `api.js` | 通信 + 自动连接（不碰 DOM） | 请求与连接策略 |
| `anchors.js` | 锚点提取（从对话里抓人名/地名/物品） | 注入准确度 |
| `storage.js` | localStorage 草稿同步 | 离线草稿 |

> **最省力的三种改法**：换肤改 `style.css` 顶部变量；换版式改 `views.js` 的 `renderXxx`；
> 换结构改 `ui/panel.html`。三者互不干扰。

## 数据源：一个接口拿全

所有页面都基于同一个快照接口（`GET /ui/snapshot?world_id=X`），
见 `backend/api/ui.py`。**字段含义如下**（都是原样读出，界面看到的 = 后端记的）：

| 字段 | 类型 | 内容 |
| --- | --- | --- |
| `world` | object | 世界门禁：`type` / `layer` / `rules{power_ceiling,time_ratio,death,forbidden[],allowed[],frozen}` / `status` / `player_present` |
| `stats` | object | 统计：角色 / 事件 / 关系 / 草稿 / 摘要 / 伏笔 / 物品 / 地点 / 因果边 |
| `characters[]` | array | 角色档案：`id` `anchor` `state{}` `knows_worlds[]` `common_sense[]` `foreign_concepts[]` `reaction_rules{}` `archived` `pinned` + **`personality{}`** |
| `characters[].personality` | object | 人格锚点：`core_traits[]` `speech_profile{sentence_length,formality,sarcasm,vocabulary[],forbidden_words[],catchphrases[],emotional_expression}` `values[]` `taboos[]` `quirks[]` `decision_pattern` `appearance_immutable{}` `drift_score` |
| `relations[]` | array | 六维关系：`dimensions{trust,affection,respect,fear,debt,hostility}` `baseline{}` `ledger[]` `anchors[]` `knows[]` `misbeliefs[]` `unknown[]` `momentum{}` `last_contact_turn` |
| `events[]` | array | 事件账本：`id` `turn` `story_time` `content` `state(committed/revoked)` `chars[]` `loc` `importance` `unresolved[]` `source_msg` |
| `causal_edges[]` | array | 因果边：`cause_event_id` `effect_event_id` `relation` `desc` `state` |
| `drafts[]` | array | 因果草稿：`content` `status(draft/pending)` `causes[]` `effects[]` `unresolved[]` `importance` `turn` + **`closed` `blocked[]`**（闭合判定） |
| `summaries[]` | array | 摘要：`level(scene/chapter/volume)` `turn_start` `turn_end` `content` `unresolved[]` |
| `items[]` | array | 物品：`owner` `holder` `status` `abilities[]` `cost` `cross_world` `history[]` |
| `goals[]` | array | 目标：`owner` `priority` `status` `content` `clues[]` `dead_ends[]` `deadline` `failure_consequence` |
| `foreshadows[]` | array | 伏笔：`content` `planted_at_turn` `status` `expected_payoff` `deadline` |
| `locations[]` | array | 地点：`id` `desc` `status` `links` |
| `factions[]` | array | 势力：`relations` `members` `resources` `current_action` |

## 渲染函数（`views.js`）

全是纯函数，输入 snapshot（+ 可选参数），输出 HTML 字符串：

```js
renderOverview(snap)                  // 总览：统计卡 + 世界门禁 + 最近事件
renderSummaries(snap)                 // 剧情摘要
renderCharacterList(snap)             // 角色列表
renderCharacterDetail(snap, charId)   // 角色详情（人格锚点 / 状态 / 认知边界）
renderRelations(snap)                 // 人际关系（六维条 + 账本 + 锚点 + 认知）
renderCausal(snap)                    // 因果链（草稿 + 因果边 + 事件账本）
renderItems(snap)                     // 物品追踪
renderWorld(snap)                     // 世界设定（门禁 + 地点 + 势力）
renderGoals(snap)                     // 目标与伏笔
renderDebugDump(label, data)          // 调试用 JSON 块
```

工具函数（也导出，可复用）：`esc()` 转义、`pill()` 徽章、`tags()` 标签组、`kv()` 键值行、`bar()` 进度条。

## DOM 契约（`index.js` 会去找这些 id）

| id | 用途 |
| --- | --- |
| `mp-content` | 动态页面渲染到这里（总览/摘要/角色/…） |
| `mp-view-settings` / `mp-view-debug` | 静态页，切换时 `hidden` 显隐 |
| `mp-status-dot` / `mp-status-text` | 状态点与状态文案 |
| `mp-nav-item[data-view]` | 导航项；`data-view` 取值见下 |
| `mp-world-id` `mp-budget` `mp-enabled` `mp-save` … | 设置表单控件 |
| `mp-health-dump` | 连接明细（只在「高级」里） |

`data-view` 可选值：`overview` `summaries` `characters` `relations` `causal` `world` `items` `goals` `settings` `debug`。

**动态内容里的约定**：`views.js` 若生成了按钮，需要在 `index.js` 的 `bindDynamic()` 里绑事件
（因为每次重渲染 `innerHTML` 会重建 DOM）。例如 `#mp-commit-ready`。

## 换肤：只改变量

`style.css` 顶部：

```css
.memory-plugin-panel {
  --mp-bg: #15152b;        /* 面板底色 */
  --mp-panel: #1c1c37;     /* 导航/顶栏 */
  --mp-card: #232345;      /* 卡片 */
  --mp-border: #31315a;    /* 描边 */
  --mp-text: #e9e9f6;      /* 正文 */
  --mp-dim: #9a9ab9;       /* 次要文字 */
  --mp-accent: #6c7cff;    /* 主色 */
  --mp-ok: #4dd08a;        /* 正常/已提交 */
  --mp-warn: #ffb44d;      /* 悬空/警告 */
  --mp-bad: #ff6b6b;       /* 错误 */
  --mp-info: #59b7ff;      /* 提示 */
  --mp-radius: 10px;       /* 圆角 */
}
```

想完全跟随酒馆主题：把 `--mp-bg` / `--mp-panel` 设成 `transparent`，
`--mp-text` / `--mp-dim` 设成 `inherit`。

## 完全自己写界面

1. **headless 模式**：设置里勾「隐藏本面板」（或 `extensionSettings['memory-plugin'].headless = true`）
   —— 插件只做注入 + 后台同步，不渲染任何 UI。
2. 然后自己写界面，数据来源两个选择：
   - 直接 `GET /ui/snapshot?world_id=X`（推荐，一个请求拿全）；
   - 或按需调 `/admin/{table}`、`/debug/drafts`、`/summaries` 等细粒度接口。
3. 注入相关的接口：`POST /inject`（同步）、`POST /update`（异步）。

## 事件流（`index.js`）

```
generation_after_commands → onMessage()   → /inject → 注入提示词
message_received          → onAiMessage() → /update（异步，不阻塞）
chat_changed / 场景切换    → onSceneChange() → /world/scene/close + /world/tick
每 N 轮                    → /world/check-points
未连接                     → 后台静默重连（5s→60s 退避）
```

## 改完怎么验

```bash
# 1) 语法（.js 是 ESM，复制成 .mjs 再 check）
node --check <(cp frontend/views.js /tmp/v.mjs && cat /tmp/v.mjs)

# 2) 渲染（喂真实数据，看有没有 undefined/NaN）
curl "http://127.0.0.1:8000/ui/snapshot?world_id=你的世界" > /tmp/snapshot.json
```

改完刷新酒馆页面即可（`index.js` 是 ES module，会重新加载）。
