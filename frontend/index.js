/**
 * 记忆插件 · 前端主入口（SPEC 13.1）。
 *
 * 事件挂载：
 *   generation_after_commands → 提取锚点 + 同步注入
 *   message_received          → 异步 /update
 *   chat_changed / 场景切换    → /world/scene/close + /world/tick
 *   每 N 轮                    → 强制全量检查
 *
 * 自动连接：
 *   启动即探测后端（候选地址列表），成功后写回设置并显示绿点；
 *   「世界 ID」留空时自动取当前角色卡名，一般无需手填。
 *
 * 前端极小：只做 UI 与转发，后端做重活。
 */

import {
  configure,
  fetchInject,
  fetchUpdate,
  fetchWorldTick,
  fetchSceneClose,
  fetchFullCheck,
  fetchDrafts,
  fetchLastDebug,
  fetchMetrics,
  fetchJobs,
  fetchLogs,
  fetchErrorLogs,
  fetchTemplates,
  applyTemplate,
  fetchCommit,
  fetchWorld,
  createWorld,
  testConnection,
  getConnection,
  exportWorld,
  upsertDraft,
  getSettings,
  DEFAULT_SETTINGS,
} from './api.js';
import { extractAnchors, detectSceneChange, setKnownEntities, getKnownEntities } from './anchors.js';
import { loadLocal, upsertLocalDraft, syncWithBackend, loadSettings, persistSettings } from './storage.js';

const EXTENSION_NAME = 'memory-plugin';
const PROMPT_KEY = `${EXTENSION_NAME}-inject`;

let context = null;
let state = {
  turn: 0,
  scene: null,
  drafts: [],
  lastDebug: null,
  lastInject: null,
  sceneType: 'present',
};

function ctx() {
  if (!context) context = SillyTavern.getContext();
  return context;
}

function extSettings() {
  const c = ctx();
  c.extensionSettings[EXTENSION_NAME] = c.extensionSettings[EXTENSION_NAME] || { ...DEFAULT_SETTINGS };
  return c.extensionSettings[EXTENSION_NAME];
}

function chatId() {
  try {
    return ctx().chatId || ctx().getCurrentChatId?.() || 'default';
  } catch (error) {
    return 'default';
  }
}

/** 世界 ID：优先用户填的；留空则自动取当前角色卡名（再退回会话名）。 */
function autoWorldId() {
  try {
    const c = ctx();
    const char = c.characters?.[c.characterId];
    if (char?.name) return char.name;
  } catch (error) {
    /* ignore */
  }
  const cid = chatId();
  if (cid && cid !== 'default') return String(cid).replace(/\.jsonl$/i, '');
  return 'default-world';
}

function worldId() {
  return String(extSettings().worldId || '').trim() || autoWorldId();
}

/** 把解析后的 worldId / chatId 写进运行时设置，供 api.js 使用。 */
function syncSettings() {
  const s = extSettings();
  configure({ ...s, worldId: worldId(), chatId: chatId() });
  return s;
}

/** 从最近消息里推断场景（地点 / 在场角色 / 故事时间）。 */
function inferScene(chat) {
  const last = chat && chat.length ? chat[chat.length - 1] : null;
  const text = String(last?.mes || '');
  const anchors = extractAnchors(text);
  return {
    loc: state.scene?.loc || anchors[0] || null,
    chars: state.scene?.chars?.length ? state.scene.chars : anchors.slice(0, 3),
    story_time: state.scene?.story_time || null,
  };
}

function recentSummary() {
  try {
    const chat = ctx().chat || [];
    return chat
      .slice(-3)
      .map((m) => String(m.mes || '').slice(0, 60))
      .join(' / ');
  } catch (error) {
    return null;
  }
}

/** 注入到提示词（世界门禁永不裁剪）。 */
function injectIntoPrompt(text) {
  if (!text) return;
  try {
    ctx().setExtensionPrompt(PROMPT_KEY, text, 1, 0, false, 0);
  } catch (error) {
    console.warn('[memory-plugin] setExtensionPrompt 失败', error);
  }
}

// --------------------------------------------------------------- 事件流程

async function onMessage(message) {
  syncSettings();
  if (!extSettings().enabled) return;
  const chat = ctx().chat || [];
  const turn = chat.length;
  state.turn = turn;

  const anchors = extractAnchors(message);
  const scene = inferScene(chat);
  const change = detectSceneChange(state.scene, scene);
  state.scene = scene;
  if (change.changed) await onSceneChange(change.reason);

  const result = await fetchInject({
    turn,
    scene: state.scene,
    anchors,
    recentSummary: recentSummary(),
    sceneType: state.sceneType,
    speaking: [],
  });
  state.lastInject = result;
  injectIntoPrompt(result.inject_text);
  renderStatus();

  if (result.degraded) {
    toast('记忆插件：后端无响应，已降级为常驻核心注入');
  }

  if (turn % Math.max(1, extSettings().fullCheckEvery) === 0) {
    const check = await fetchFullCheck(worldId(), turn);
    if (check && check.personality_drift?.length) {
      toast(`记忆插件：${check.personality_drift.length} 个角色人设出现漂移`);
    }
    refreshDrafts();
  }
}

async function onAiMessage(message, userMessage) {
  syncSettings();
  if (!extSettings().enabled) return;
  const turn = ctx().chat?.length || state.turn;
  const chat = ctx().chat || [];
  const userMsg = userMessage || (chat.length >= 2 ? chat[chat.length - 2] : null);
  const cid = chatId();

  const payload = {
    turn,
    world_id: worldId(),
    chat_id: cid,
    user_msg: { id: `${cid}:${turn}:user`, content: String(userMsg?.mes || '') },
    ai_msg: { id: `${cid}:${turn}:assistant`, content: String(message?.mes || message?.content || '') },
    scene: state.scene || { loc: null, chars: [], story_time: null },
    anchors: extractAnchors(String(message?.mes || '')),
    scene_type: state.sceneType,
  };

  // 更新异步：不 await，不阻塞酒馆
  fetchUpdate(payload).then((response) => {
    if (response && response.queued) refreshDrafts();
    renderStatus();
  });
}

async function onSceneChange(reason) {
  syncSettings();
  const wid = worldId();
  const cid = chatId();
  await fetchSceneClose(wid, state.turn);
  await fetchWorldTick({ world_id: wid, meta_time: Math.max(0, state.turn * 10), player_present: true });
  const drafts = await syncWithBackend(cid, wid, state.drafts);
  state.drafts = drafts;
  renderCausalPanel();
  if (reason) console.info('[memory-plugin] 场景切换：', reason);
}

async function prefetch() {
  if (!extSettings().preloadOnType || !extSettings().enabled) return;
  syncSettings();
  const wid = worldId();
  const cid = chatId();
  const local = loadLocal(cid).drafts;
  try {
    const response = await fetchDrafts(wid);
    if (response && response.drafts) {
      state.drafts = await syncWithBackend(cid, wid, response.drafts);
      renderCausalPanel();
    }
  } catch (error) {
    state.drafts = local;
  }
}

// --------------------------------------------------------------- 连接状态

function toast(text) {
  try {
    const c = ctx();
    if (c.toastr) c.toastr.info(text);
    else console.info('[memory-plugin]', text);
  } catch (error) {
    console.info('[memory-plugin]', text);
  }
}

function renderStatus() {
  const conn = getConnection();
  const dot = document.getElementById('mp-status-dot');
  const text = document.getElementById('mp-status-text');
  const dump = document.getElementById('mp-health-dump');

  if (dot) {
    const cls = conn.connected ? 'ok' : conn.checkedAt ? 'bad' : '';
    dot.className = `mp-dot ${cls}`.trim();
  }

  if (text) {
    if (conn.connected) {
      const h = conn.health || {};
      const q = h.queue || {};
      text.textContent = `已连接 ${conn.baseUrl} · 后端 v${h.version || '?'} · 向量 ${h.vector || '?'} · 队列 pending=${q.pending ?? 0}/dead=${q.dead ?? 0}`;
    } else if (conn.checkedAt) {
      text.textContent = `未连接后端（已试 ${conn.tried.length} 个地址）。请先启动后端：python -m backend.main`;
    } else {
      text.textContent = '尚未探测后端…';
    }
  }

  if (dump) {
    dump.textContent = JSON.stringify(
      { connection: conn, world_id: worldId(), chat_id: chatId(), turn: state.turn },
      null,
      2,
    );
  }
}

/** 重新探测 + 刷新状态；返回结果供按钮提示用。 */
async function refreshHealth(silent = false) {
  const result = await testConnection();
  renderStatus();
  if (!silent) {
    toast(result.ok ? `记忆插件：已连接 ${result.baseUrl}` : '记忆插件：未找到后端，请确认已启动');
  }
  return result;
}

// --------------------------------------------------------------- UI

const FALLBACK_HTML = {
  panel: `
    <div class="mp-status">
      <span class="mp-dot" id="mp-status-dot"></span>
      <span class="mp-status-text" id="mp-status-text">尚未探测后端…</span>
      <button id="mp-reconnect" class="menu_button mp-mini">重新连接</button>
    </div>
    <div class="mp-grid">
      <label for="mp-base-url">后端地址</label>
      <input type="text" id="mp-base-url" class="text_pole" placeholder="留空 = 自动探测（推荐）">
      <label for="mp-world-id">世界 ID</label>
      <input type="text" id="mp-world-id" class="text_pole" placeholder="留空 = 自动取角色卡名">
      <label for="mp-budget">token 预算</label>
      <input type="number" id="mp-budget" class="text_pole" min="200" max="4000" step="50">
      <label for="mp-inject-timeout">注入超时(ms)</label>
      <input type="number" id="mp-inject-timeout" class="text_pole" min="100" max="5000" step="50">
      <label for="mp-full-check">全量检查间隔</label>
      <input type="number" id="mp-full-check" class="text_pole" min="1" max="200" step="1">
    </div>
    <div class="mp-row">
      <label class="mp-check"><input type="checkbox" id="mp-enabled"> 启用插件</label>
      <label class="mp-check"><input type="checkbox" id="mp-autoconnect"> 自动探测后端</label>
      <label class="mp-check"><input type="checkbox" id="mp-debug-panel"> 显示调试区</label>
    </div>
    <div class="mp-row">
      <button id="mp-save" class="menu_button">保存设置</button>
      <button id="mp-test" class="menu_button">测试连接</button>
      <button id="mp-init-world" class="menu_button">初始化世界</button>
      <button id="mp-template" class="menu_button">应用模板</button>
      <button id="mp-export" class="menu_button">导出世界</button>
      <button id="mp-sync-entities" class="menu_button">同步实体表</button>
    </div>
    <pre id="mp-health-dump" class="mp-dump mp-health">（未连接后端）</pre>`,
  causal: `
    <div class="mp-row">
      <button id="mp-refresh-drafts" class="menu_button">刷新</button>
      <button id="mp-commit-ready" class="menu_button">提交已闭合</button>
      <span id="mp-draft-count" class="mp-badge pending">—</span>
    </div>
    <div id="mp-causal-list" class="mp-list mp-muted">加载中……</div>`,
  debug: `
    <div class="mp-row">
      <button id="mp-refresh-debug" class="menu_button">注入详情</button>
      <button id="mp-refresh-jobs" class="menu_button">队列</button>
      <button id="mp-refresh-logs" class="menu_button">日志</button>
      <button id="mp-refresh-errors" class="menu_button">错误日志</button>
    </div>
    <pre id="mp-debug-dump" class="mp-dump">（尚无注入记录）</pre>
    <pre id="mp-jobs-dump" class="mp-dump">（尚未拉取队列）</pre>
    <pre id="mp-log-dump" class="mp-dump">（点「日志」查看最近 200 行）</pre>`,
};

/** 优先读 ui/*.html；读不到就用内置兜底（保证任何环境都有界面）。 */
async function loadTemplate(name, fallbackKey) {
  try {
    const url = new URL(`./ui/${name}`, import.meta.url);
    const response = await fetch(url);
    if (response.ok) {
      const text = await response.text();
      if (text && text.trim()) return text;
    }
  } catch (error) {
    /* 落到兜底 */
  }
  return FALLBACK_HTML[fallbackKey] || '';
}

async function mountPanels() {
  const container = document.getElementById('memory-plugin-settings');
  if (!container) return;

  const panel = document.getElementById('mp-panel-body');
  const causal = document.getElementById('mp-causal-body');
  const debug = document.getElementById('mp-debug-body');
  const onboard = document.getElementById('mp-onboard-body');

  if (panel) panel.innerHTML = await loadTemplate('panel.html', 'panel');
  if (causal) causal.innerHTML = await loadTemplate('causal.html', 'causal');
  if (debug) debug.innerHTML = await loadTemplate('debug.html', 'debug');
  if (onboard) onboard.innerHTML = await loadTemplate('onboard.html', '');

  bindPanel();
  bindDebug();
  applyDebugVisibility();
  renderStatus();
  refreshDrafts();
}

function setValue(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value ?? '';
}

function setChecked(id, value) {
  const el = document.getElementById(id);
  if (el) el.checked = !!value;
}

function readInt(id, fallback) {
  const el = document.getElementById(id);
  const parsed = parseInt(el?.value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function applyDebugVisibility() {
  const show = extSettings().debugPanel !== false;
  const debugBody = document.getElementById('mp-debug-body');
  const debugTitle = document.getElementById('mp-debug-title');
  if (debugBody) debugBody.style.display = show ? '' : 'none';
  if (debugTitle) debugTitle.style.display = show ? '' : 'none';
}

function bindPanel() {
  const s = extSettings();
  setValue('mp-base-url', s.baseUrl);
  setValue('mp-world-id', s.worldId);
  setValue('mp-budget', s.budget);
  setValue('mp-inject-timeout', s.injectTimeoutMs);
  setValue('mp-full-check', s.fullCheckEvery);
  setChecked('mp-enabled', s.enabled);
  setChecked('mp-autoconnect', s.autoConnect !== false);
  setChecked('mp-debug-panel', s.debugPanel !== false);

  document.getElementById('mp-save')?.addEventListener('click', async () => {
    const next = {
      ...s,
      baseUrl: (document.getElementById('mp-base-url')?.value || '').trim(),
      worldId: (document.getElementById('mp-world-id')?.value || '').trim(),
      budget: readInt('mp-budget', s.budget),
      injectTimeoutMs: readInt('mp-inject-timeout', s.injectTimeoutMs),
      fullCheckEvery: readInt('mp-full-check', s.fullCheckEvery),
      enabled: !!document.getElementById('mp-enabled')?.checked,
      autoConnect: !!document.getElementById('mp-autoconnect')?.checked,
      debugPanel: !!document.getElementById('mp-debug-panel')?.checked,
    };
    Object.assign(s, next);
    syncSettings();
    persistSettings(s);
    applyDebugVisibility();
    if (next.autoConnect) await refreshHealth(true);
    renderStatus();
    toast(`记忆插件：已保存（世界 ID = ${worldId()}）`);
  });

  document.getElementById('mp-reconnect')?.addEventListener('click', () => refreshHealth());
  document.getElementById('mp-test')?.addEventListener('click', () => refreshHealth());

  document.getElementById('mp-init-world')?.addEventListener('click', async () => {
    const wid = worldId();
    const result = await createWorld(wid);
    if (result && (result.ok || result.id)) {
      toast(`记忆插件：世界「${wid}」已就绪`);
    } else {
      toast('记忆插件：初始化失败，先确认后端在跑');
    }
    renderStatus();
  });

  document.getElementById('mp-export')?.addEventListener('click', async () => {
    const data = await exportWorld(worldId());
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${worldId()}-memory-export.json`;
    link.click();
  });

  document.getElementById('mp-template')?.addEventListener('click', async () => {
    const templates = await fetchTemplates();
    const list = templates?.templates || [];
    if (!list.length) return toast('记忆插件：模板加载失败');
    const choice = prompt(
      `可用模板：\n${list.map((t) => `${t.id}（${t.name || ''}）`).join('\n')}\n\n输入要应用的模板 ID：`,
      list[0].id,
    );
    if (!choice) return;
    const applied = await applyTemplate(choice, worldId());
    toast(applied && applied.ok ? `记忆插件：已应用模板 ${choice}` : '记忆插件：模板应用失败');
  });

  document.getElementById('mp-sync-entities')?.addEventListener('click', async () => {
    const templates = await fetchTemplates();
    if (templates && templates.ok) toast('记忆插件：实体表同步完成');
    else toast('记忆插件：后端未连接');
  });
}

function bindDebug() {
  document.getElementById('mp-refresh-debug')?.addEventListener('click', refreshDebug);
  document.getElementById('mp-refresh-drafts')?.addEventListener('click', refreshDrafts);
  document.getElementById('mp-refresh-jobs')?.addEventListener('click', refreshJobs);
  document.getElementById('mp-refresh-logs')?.addEventListener('click', () => refreshLogs('main'));
  document.getElementById('mp-refresh-errors')?.addEventListener('click', () => refreshLogs('errors'));
  document.getElementById('mp-commit-ready')?.addEventListener('click', commitReadyDrafts);
}

async function refreshDebug() {
  const target = document.getElementById('mp-debug-dump');
  const last = await fetchLastDebug();
  const metrics = await fetchMetrics();
  state.lastDebug = last;
  if (target) target.textContent = JSON.stringify({ last, metrics }, null, 2);
}

async function refreshJobs() {
  const target = document.getElementById('mp-jobs-dump');
  const jobs = await fetchJobs();
  if (target) target.textContent = JSON.stringify(jobs, null, 2);
}

/** 拉日志：source = main（全量）| errors（只含 ERROR + 堆栈）。 */
async function refreshLogs(source = 'main') {
  const target = document.getElementById('mp-log-dump');
  if (!target) return;
  target.textContent = '加载中……';
  const data = source === 'errors' ? await fetchErrorLogs(200) : await fetchLogs(200, 'main');
  const entries = (data && data.entries) || [];
  if (!entries.length) {
    target.textContent = `（${source} 日志为空；路径：${(data && data.path) || '未知'}）`;
    return;
  }
  target.textContent = entries.join('\n');
  target.scrollTop = target.scrollHeight;
}

async function refreshDrafts() {
  const response = await fetchDrafts(worldId());
  if (response && response.drafts) {
    state.drafts = response.drafts;
    renderCausalPanel();
  }
}

function renderCausalPanel() {
  const target = document.getElementById('mp-causal-list');
  const counter = document.getElementById('mp-draft-count');
  const drafts = state.drafts || [];

  if (counter) {
    const openCount = drafts.filter((d) => !d.closed).length;
    counter.textContent = `未闭合 ${openCount} / 共 ${drafts.length}`;
    counter.className = `mp-badge ${openCount ? 'draft' : 'committed'}`;
  }

  if (!target) return;
  if (!drafts.length) {
    target.textContent = '当前没有未闭合的因果草稿。';
    target.classList.add('mp-muted');
    return;
  }
  target.classList.remove('mp-muted');

  target.innerHTML = drafts
    .map((d) => {
      const causes = (d.causes || [])
        .map((c) => `前因：${c.desc || c.event_id || c.content || '—'}`)
        .join('<br>');
      const effects = (d.effects || [])
        .map((c) => `后果：${c.desc || c.event_id || c.content || '—'}`)
        .join('<br>');
      const verdict = d.closed
        ? '<span class="mp-badge committed">可提交</span>'
        : `<span class="mp-warn">悬空：${(d.blocked || []).join('、') || '等待因果闭合'}</span>`;
      const meta = [`第${d.turn ?? '?'}楼`, d.loc || '', (d.chars || []).join('、')].filter(Boolean).join(' · ');
      return `<div class="mp-card ${d.status}">
          <div class="mp-head"><span class="mp-badge ${d.status}">${d.status}</span><strong>${d.content}</strong></div>
          <div class="mp-meta">${causes}${causes && effects ? '<br>' : ''}${effects}</div>
          <div class="mp-meta">${verdict}</div>
          <div class="mp-meta mp-muted">${meta}</div>
        </div>`;
    })
    .join('');
}

/** 手动提交「已闭合」的草稿（前端确认 = 人工背书，force 提交）。 */
async function commitReadyDrafts() {
  const ready = (state.drafts || []).filter((d) => d.closed);
  if (!ready.length) return toast('记忆插件：没有可提交的草稿（因果未闭合）');

  let ok = 0;
  for (const d of ready) {
    const result = await fetchCommit({
      world_id: worldId(),
      draft_id: d.id,
      force: true,
      event: {
        content: d.content,
        causes: d.causes || [],
        effects: d.effects || [],
        chars: d.chars || [],
        loc: d.loc,
        importance: d.importance ?? 0.5,
        turn: d.turn,
        story_time: d.story_time,
        unresolved: d.unresolved || [],
        source_msg: `${chatId()}:${d.turn ?? state.turn}:assistant`,
      },
    });
    if (result && result.ok) ok += 1;
  }
  toast(`记忆插件：已提交 ${ok}/${ready.length} 条`);
  refreshDrafts();
}

// --------------------------------------------------------------- 初始化

async function registerSettings() {
  try {
    const { extension_settings } = ctx();
    extension_settings[EXTENSION_NAME] = extension_settings[EXTENSION_NAME] || { ...DEFAULT_SETTINGS };
  } catch (error) {
    console.warn('[memory-plugin] extension_settings 不可用', error);
  }

  const container = document.createElement('div');
  container.id = 'memory-plugin-settings';
  container.className = 'memory-plugin-panel';
  container.innerHTML = `
    <div class="inline-drawer">
      <div class="inline-drawer-toggle inline-drawer-header">
        <b>记忆插件 · Memory Engine</b>
        <div class="inline-drawer-icon fa-solid fa-circle-chevron-down down"></div>
      </div>
      <div class="inline-drawer-content">
        <div id="mp-panel-body"></div>
        <hr>
        <b>因果预览</b>
        <div id="mp-causal-body"></div>
        <b id="mp-debug-title">调试</b>
        <div id="mp-debug-body"></div>
        <details class="mp-muted">
          <summary>启动引导</summary>
          <div id="mp-onboard-body"></div>
        </details>
      </div>
    </div>`;

  const host = document.getElementById('extensions_settings') || document.getElementById('extensions_settings2');
  host?.appendChild(container);

  const saved = loadSettings();
  configure({ ...DEFAULT_SETTINGS, ...extSettings(), ...(saved || {}) });
  syncSettings();
  await mountPanels();
}

async function bootstrap() {
  const c = ctx();
  syncSettings();

  await registerSettings();

  // 自动连接：baseUrl 留空或开启自动探测时，启动即找后端
  if (extSettings().autoConnect !== false) {
    const conn = await refreshHealth(true);
    if (conn.ok) console.info(`[memory-plugin] 已自动连接后端 ${conn.baseUrl}`);
    else console.warn('[memory-plugin] 未探测到后端，插件将以降级模式运行');
  }

  await prefetch();

  const events = c.eventTypes || c.event_types || {};
  const source = c.eventSource;

  source.on(events.GENERATION_AFTER_COMMANDS || 'generation_after_commands', async () => {
    const chat = c.chat || [];
    const lastUser = [...chat].reverse().find((m) => m.is_user);
    await onMessage(lastUser?.mes || '');
  });

  source.on(events.MESSAGE_RECEIVED || 'message_received', async (messageIndex) => {
    const chat = c.chat || [];
    const message = chat[messageIndex] || chat[chat.length - 1];
    await onAiMessage(message);
  });

  source.on(events.MESSAGE_SENT || 'message_sent', () => {
    prefetch();
  });

  source.on(events.CHAT_CHANGED || 'chat_changed', async () => {
    syncSettings();
    renderStatus();
    await onSceneChange('chat_changed');
  });

  console.info('[memory-plugin] 已加载', { world: worldId(), chat: chatId(), entities: getKnownEntities() });
}

if (typeof jQuery !== 'undefined') {
  jQuery(async () => {
    await bootstrap();
  });
} else {
  window.addEventListener('DOMContentLoaded', bootstrap);
}

export {
  state,
  onMessage,
  onAiMessage,
  onSceneChange,
  extractAnchors,
  setKnownEntities,
  worldId,
  refreshHealth,
};