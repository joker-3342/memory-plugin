/**
 * 记忆插件 · 前端主入口（SPEC 13.1）
 *
 * 职责划分（方便你自己改 UI）：
 *   api.js    —— 通信 + 自动连接（不碰 DOM）
 *   views.js  —— 数据 → HTML（纯函数，不碰事件）
 *   index.js  —— 事件挂载 + 导航 + 把两者接起来（本文件）
 *   ui/*.html —— 骨架；style.css —— 皮肤（改 CSS 变量即可换肤）
 *
 * 自动连接：启动即并行探测候选地址；失败则后台静默重连（5s→60s 退避），
 * 用户不需要填地址、也不需要点「重连」。
 */

import {
  configure,
  fetchInject,
  fetchUpdate,
  fetchWorldTick,
  fetchSceneClose,
  fetchFullCheck,
  fetchSnapshot,
  fetchDrafts,
  fetchLastDebug,
  fetchMetrics,
  fetchJobs,
  fetchLogs,
  fetchErrorLogs,
  fetchTemplates,
  applyTemplate,
  fetchCommit,
  createWorld,
  testConnection,
  getConnection,
  exportWorld,
  getSettings,
  DEFAULT_SETTINGS,
} from './api.js';
import { extractAnchors, detectSceneChange, setKnownEntities, getKnownEntities } from './anchors.js';
import { loadLocal, syncWithBackend, loadSettings, persistSettings } from './storage.js';
import * as views from './views.js';

const EXTENSION_NAME = 'memory-plugin';
const PROMPT_KEY = `${EXTENSION_NAME}-inject`;

let context = null;
const state = {
  turn: 0,
  scene: null,
  drafts: [],
  lastDebug: null,
  lastInject: null,
  sceneType: 'present',
  view: 'overview',
  snapshot: null,
  selectedChar: null,
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

/** 世界 ID：优先用户填的；留空则自动取当前角色卡名。 */
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

function syncSettings() {
  const s = extSettings();
  configure({ ...s, worldId: worldId(), chatId: chatId() });
  return s;
}

// --------------------------------------------------------------- 酒馆事件

function inferScene(chat) {
  const last = chat && chat.length ? chat[chat.length - 1] : null;
  const anchors = extractAnchors(String(last?.mes || ''));
  return {
    loc: state.scene?.loc || anchors[0] || null,
    chars: state.scene?.chars?.length ? state.scene.chars : anchors.slice(0, 3),
    story_time: state.scene?.story_time || null,
  };
}

function recentSummary() {
  try {
    return (ctx().chat || [])
      .slice(-3)
      .map((m) => String(m.mes || '').slice(0, 60))
      .join(' / ');
  } catch (error) {
    return null;
  }
}

function injectIntoPrompt(text) {
  if (!text) return;
  try {
    ctx().setExtensionPrompt(PROMPT_KEY, text, 1, 0, false, 0);
  } catch (error) {
    console.warn('[memory-plugin] setExtensionPrompt 失败', error);
  }
}

async function onMessage(message) {
  syncSettings();
  if (!extSettings().enabled) return;
  const chat = ctx().chat || [];
  state.turn = chat.length;

  const anchors = extractAnchors(message);
  const scene = inferScene(chat);
  const change = detectSceneChange(state.scene, scene);
  state.scene = scene;
  if (change.changed) await onSceneChange(change.reason);

  const result = await fetchInject({
    turn: state.turn,
    scene: state.scene,
    anchors,
    recentSummary: recentSummary(),
    sceneType: state.sceneType,
    speaking: [],
  });
  state.lastInject = result;
  injectIntoPrompt(result.inject_text);
  renderStatus();

  if (result.degraded) toast('记忆引擎未就绪，已降级为常驻核心注入');
  else if (state.view !== 'debug') refreshSnapshot();

  if (state.turn % Math.max(1, extSettings().fullCheckEvery) === 0) {
    fetchFullCheck(worldId(), state.turn);
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
  fetchUpdate(payload).then(() => {
    renderStatus();
    setTimeout(() => refreshSnapshot(), 1200); // 等后台任务落库后再刷新界面
  });
}

async function onSceneChange(reason) {
  syncSettings();
  const wid = worldId();
  const cid = chatId();
  await fetchSceneClose(wid, state.turn);
  await fetchWorldTick({ world_id: wid, meta_time: Math.max(0, state.turn * 10), player_present: true });
  state.drafts = await syncWithBackend(cid, wid, state.drafts);
  refreshSnapshot();
  if (reason) console.info('[memory-plugin] 场景切换：', reason);
}

async function prefetch() {
  if (!extSettings().enabled) return;
  syncSettings();
  try {
    state.drafts = await syncWithBackend(chatId(), worldId(), loadLocal(chatId()).drafts);
  } catch (error) {
    /* 忽略：界面稍后会刷新 */
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

/** 面向用户的状态文案：只说「就绪/未就绪」，技术细节在「设置 → 高级」。 */
function renderStatus() {
  const conn = getConnection();
  const dot = document.getElementById('mp-status-dot');
  const text = document.getElementById('mp-status-text');
  const dump = document.getElementById('mp-health-dump');

  if (dot) dot.className = `mp-dot ${conn.connected ? 'ok' : conn.checkedAt ? 'bad' : ''}`.trim();
  if (text) {
    if (conn.connected) {
      const h = conn.health || {};
      const q = h.queue || {};
      text.textContent = `记忆引擎就绪 · v${h.version || '?'} · 待处理 ${q.pending ?? 0}${
        q.dead ? ` · 死信 ${q.dead}` : ''
      }`;
    } else if (conn.checkedAt) {
      text.textContent = '记忆引擎未就绪（后台自动重连中…）';
    } else {
      text.textContent = '记忆引擎初始化中…';
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

// 后台静默重连：5s → 60s 指数退避，连上即停
let reconnectTimer = null;
let reconnectDelay = 5000;
let readyNotified = false;

function scheduleReconnect() {
  if (reconnectTimer || !extSettings().enabled) return;
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    if (getConnection().connected) return;
    const result = await testConnection();
    renderStatus();
    if (result.ok) {
      reconnectDelay = 5000;
      if (!readyNotified) {
        readyNotified = true;
        toast('记忆引擎已就绪');
      }
      refreshSnapshot();
      return;
    }
    reconnectDelay = Math.min(Math.round(reconnectDelay * 1.5), 60000);
    scheduleReconnect();
  }, reconnectDelay);
}

function stopReconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

async function refreshHealth(silent = false) {
  const result = await testConnection();
  renderStatus();
  if (result.ok) {
    stopReconnect();
    if (!silent) toast('记忆引擎已就绪');
  } else {
    scheduleReconnect();
    if (!silent) toast('记忆引擎暂未就绪，会在后台自动重试');
  }
  return result;
}

// --------------------------------------------------------------- 视图渲染

async function refreshSnapshot() {
  if (!extSettings().enabled) return null;
  const snap = await fetchSnapshot(worldId());
  state.snapshot = snap && snap.ok ? snap : null;
  renderView();
  return state.snapshot;
}

function switchView(name) {
  state.view = name;
  document.querySelectorAll('.mp-nav-item').forEach((el) => {
    el.classList.toggle('active', el.dataset.view === name);
  });
  renderView();
  if (name === 'settings' || name === 'debug') return;
  if (!state.snapshot) refreshSnapshot();
}

function renderView() {
  const host = document.getElementById('mp-content');
  if (!host) return;

  const settingsView = document.getElementById('mp-view-settings');
  const debugView = document.getElementById('mp-view-debug');
  const view = state.view;
  const isStatic = view === 'settings' || view === 'debug';

  if (settingsView) settingsView.hidden = view !== 'settings';
  if (debugView) debugView.hidden = view !== 'debug';
  host.hidden = isStatic;
  if (isStatic) return;

  const snap = state.snapshot;
  if (!snap) {
    host.innerHTML = '<div class="mp-empty">记忆引擎未就绪 —— 后台会自动重连，稍后点「刷新」即可。</div>';
    return;
  }

  switch (view) {
    case 'summaries':
      host.innerHTML = views.renderSummaries(snap);
      break;
    case 'characters': {
      const chars = snap.characters || [];
      if (chars.length && !chars.some((c) => c.id === state.selectedChar)) state.selectedChar = chars[0].id;
      host.innerHTML =
        views.renderCharacterList(snap) +
        (state.selectedChar ? views.renderCharacterDetail(snap, state.selectedChar) : '');
      break;
    }
    case 'relations':
      host.innerHTML = views.renderRelations(snap);
      break;
    case 'causal':
      host.innerHTML = views.renderCausal(snap);
      break;
    case 'world':
      host.innerHTML = views.renderWorld(snap);
      break;
    case 'items':
      host.innerHTML = views.renderItems(snap);
      break;
    case 'goals':
      host.innerHTML = views.renderGoals(snap);
      break;
    case 'overview':
    default:
      host.innerHTML = views.renderOverview(snap);
      break;
  }
  bindDynamic();
}

/** 动态生成的内容每次重渲染后都要重新绑事件。 */
function bindDynamic() {
  document.getElementById('mp-commit-ready')?.addEventListener('click', commitReadyDrafts);
  document.getElementById('mp-refresh-drafts')?.addEventListener('click', () => refreshSnapshot());
  document.querySelectorAll('#mp-content .mp-item[data-char]').forEach((el) => {
    el.classList.toggle('selected', el.dataset.char === state.selectedChar);
    el.addEventListener('click', () => {
      state.selectedChar = el.dataset.char;
      renderView();
    });
  });
}

/** 手动提交「已闭合」的草稿（前端确认 = 人工背书）。 */
async function commitReadyDrafts() {
  const ready = ((state.snapshot?.drafts) || []).filter((d) => d.closed);
  if (!ready.length) return toast('没有可提交的草稿（因果未闭合）');
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
  toast(`已提交 ${ok}/${ready.length} 条`);
  refreshSnapshot();
}

// --------------------------------------------------------------- UI 绑定

async function loadTemplate(name) {
  try {
    const response = await fetch(new URL(`./ui/${name}`, import.meta.url));
    if (response.ok) {
      const text = await response.text();
      if (text && text.trim()) return text;
    }
  } catch (error) {
    /* 落到兜底 */
  }
  return '';
}

const FALLBACK_HTML = `<div class="mp-app">
  <nav class="mp-nav">
    <div class="mp-brand"><span class="mp-brand-mark">记</span><span class="mp-brand-text">记忆引擎<small>Memory Engine</small></span></div>
    <div class="mp-nav-group">记忆浏览</div>
    <button class="mp-nav-item" data-view="overview">总览</button>
    <button class="mp-nav-item" data-view="summaries">剧情摘要</button>
    <button class="mp-nav-item" data-view="characters">角色档案</button>
    <button class="mp-nav-item" data-view="relations">人际关系</button>
    <button class="mp-nav-item" data-view="causal">因果链</button>
    <div class="mp-nav-group">世界</div>
    <button class="mp-nav-item" data-view="world">世界设定</button>
    <button class="mp-nav-item" data-view="items">物品追踪</button>
    <button class="mp-nav-item" data-view="goals">目标与伏笔</button>
    <div class="mp-nav-group">系统</div>
    <button class="mp-nav-item" data-view="settings">设置</button>
    <button class="mp-nav-item" data-view="debug">调试</button>
  </nav>
  <main class="mp-main">
    <header class="mp-topbar">
      <span class="mp-dot" id="mp-status-dot"></span>
      <span class="mp-status-text" id="mp-status-text">记忆引擎初始化中…</span>
      <button id="mp-refresh" class="menu_button mp-mini">刷新</button>
    </header>
    <div class="mp-content" id="mp-content">加载中……</div>
    <div class="mp-view" id="mp-view-settings" hidden>
      <div class="mp-grid">
        <label for="mp-world-id">世界 ID</label><input type="text" id="mp-world-id" class="text_pole" placeholder="留空 = 自动取角色卡名">
        <label for="mp-budget">token 预算</label><input type="number" id="mp-budget" class="text_pole" min="200" max="4000" step="50">
      </div>
      <div class="mp-row"><label class="mp-check"><input type="checkbox" id="mp-enabled"> 启用插件</label></div>
      <div class="mp-row">
        <button id="mp-save" class="menu_button">保存</button>
        <button id="mp-init-world" class="menu_button">初始化世界</button>
        <button id="mp-template" class="menu_button">应用模板</button>
        <button id="mp-export" class="menu_button">导出世界</button>
      </div>
      <details class="mp-advanced"><summary>高级</summary>
        <div class="mp-grid">
          <label for="mp-base-url">后端地址</label><input type="text" id="mp-base-url" class="text_pole" placeholder="留空 = 自动探测（推荐）">
          <label for="mp-inject-timeout">注入超时(ms)</label><input type="number" id="mp-inject-timeout" class="text_pole" min="100" max="5000" step="50">
          <label for="mp-full-check">全量检查间隔</label><input type="number" id="mp-full-check" class="text_pole" min="1" max="200" step="1">
        </div>
        <div class="mp-row">
          <label class="mp-check"><input type="checkbox" id="mp-autoconnect"> 自动探测后端</label>
          <label class="mp-check"><input type="checkbox" id="mp-debug-panel"> 显示调试页</label>
          <label class="mp-check"><input type="checkbox" id="mp-headless"> 隐藏本面板</label>
        </div>
        <div class="mp-row">
          <button id="mp-reconnect" class="menu_button">重新连接</button>
          <button id="mp-test" class="menu_button">测试连接</button>
          <button id="mp-sync-entities" class="menu_button">同步实体表</button>
        </div>
        <pre id="mp-health-dump" class="mp-dump mp-health">（未连接后端）</pre>
      </details>
    </div>
    <div class="mp-view" id="mp-view-debug" hidden>
      <div class="mp-row">
        <button id="mp-refresh-debug" class="menu_button">注入详情</button>
        <button id="mp-refresh-jobs" class="menu_button">队列</button>
        <button id="mp-refresh-logs" class="menu_button">日志</button>
        <button id="mp-refresh-errors" class="menu_button">错误日志</button>
      </div>
      <pre id="mp-debug-dump" class="mp-dump">（尚无注入记录）</pre>
      <pre id="mp-jobs-dump" class="mp-dump">（尚未拉取队列）</pre>
      <pre id="mp-log-dump" class="mp-dump">（点「日志」查看最近 200 行）</pre>
    </div>
  </main>
</div>`;

function setValue(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value ?? '';
}
function setChecked(id, value) {
  const el = document.getElementById(id);
  if (el) el.checked = !!value;
}
function readInt(id, fallback) {
  const parsed = parseInt(document.getElementById(id)?.value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
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
  setChecked('mp-headless', !!s.headless);

  document.getElementById('mp-refresh')?.addEventListener('click', async () => {
    await refreshHealth(true);
    await refreshSnapshot();
    toast('已刷新');
  });

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
      headless: !!document.getElementById('mp-headless')?.checked,
    };
    Object.assign(s, next);
    syncSettings();
    persistSettings(s);
    applyDebugVisibility();
    if (next.autoConnect) await refreshHealth(true);
    await refreshSnapshot();
    renderStatus();
    toast(`已保存（世界 ID = ${worldId()}）`);
  });

  document.getElementById('mp-reconnect')?.addEventListener('click', () => refreshHealth());
  document.getElementById('mp-test')?.addEventListener('click', () => refreshHealth());

  document.getElementById('mp-init-world')?.addEventListener('click', async () => {
    const wid = worldId();
    const result = await createWorld(wid);
    toast(result && (result.ok || result.id) ? `世界「${wid}」已就绪` : '初始化失败，请确认后端在运行');
    refreshSnapshot();
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
    if (!list.length) return toast('模板加载失败');
    const choice = prompt(
      `可用模板：\n${list.map((t) => `${t.id}（${t.name || ''}）`).join('\n')}\n\n输入要应用的模板 ID：`,
      list[0].id,
    );
    if (!choice) return;
    const applied = await applyTemplate(choice, worldId());
    toast(applied && applied.ok ? `已应用模板 ${choice}` : '模板应用失败');
    refreshSnapshot();
  });

  document.getElementById('mp-sync-entities')?.addEventListener('click', async () => {
    const templates = await fetchTemplates();
    toast(templates && templates.ok ? '实体表已同步' : '后端未连接');
  });
}

function bindDebug() {
  document.getElementById('mp-refresh-debug')?.addEventListener('click', refreshDebug);
  document.getElementById('mp-refresh-jobs')?.addEventListener('click', refreshJobs);
  document.getElementById('mp-refresh-logs')?.addEventListener('click', () => refreshLogs('main'));
  document.getElementById('mp-refresh-errors')?.addEventListener('click', () => refreshLogs('errors'));
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
  if (target) target.textContent = JSON.stringify(await fetchJobs(), null, 2);
}

async function refreshLogs(source = 'main') {
  const target = document.getElementById('mp-log-dump');
  if (!target) return;
  target.textContent = '加载中……';
  const data = source === 'errors' ? await fetchErrorLogs(200) : await fetchLogs(200, 'main');
  const entries = (data && data.entries) || [];
  target.textContent = entries.length
    ? entries.join('\n')
    : `（${source} 日志为空；路径：${(data && data.path) || '未知'}）`;
  target.scrollTop = target.scrollHeight;
}

function applyDebugVisibility() {
  const show = extSettings().debugPanel !== false;
  const navItem = document.querySelector('.mp-nav-item[data-view="debug"]');
  if (navItem) navItem.hidden = !show;
  if (!show && state.view === 'debug') switchView('overview');
}

// --------------------------------------------------------------- 初始化

async function registerSettings() {
  try {
    const { extension_settings } = ctx();
    extension_settings[EXTENSION_NAME] = extension_settings[EXTENSION_NAME] || { ...DEFAULT_SETTINGS };
  } catch (error) {
    console.warn('[memory-plugin] extension_settings 不可用', error);
  }

  const saved = loadSettings();
  configure({ ...DEFAULT_SETTINGS, ...extSettings(), ...(saved || {}) });
  syncSettings();

  // headless：只注入、不显示面板（自己写 UI 时用）
  if (extSettings().headless) {
    console.info('[memory-plugin] headless 模式：不渲染面板，仅注入 + 后台同步');
    return;
  }

  const container = document.createElement('div');
  container.id = 'memory-plugin-settings';
  container.className = 'memory-plugin-panel';
  container.innerHTML = `
    <div class="inline-drawer">
      <div class="inline-drawer-toggle inline-drawer-header">
        <b>记忆引擎 · Memory Engine</b>
        <div class="inline-drawer-icon fa-solid fa-circle-chevron-down down"></div>
      </div>
      <div class="inline-drawer-content" id="mp-root"></div>
    </div>`;

  const host = document.getElementById('extensions_settings') || document.getElementById('extensions_settings2');
  host?.appendChild(container);

  const root = document.getElementById('mp-root');
  const html = (await loadTemplate('panel.html')) || FALLBACK_HTML;
  root.innerHTML = html;

  document.querySelectorAll('.mp-nav-item').forEach((el) => {
    el.addEventListener('click', () => switchView(el.dataset.view));
  });
  bindPanel();
  bindDebug();
  applyDebugVisibility();
  switchView('overview');
  renderStatus();
}

async function bootstrap() {
  const c = ctx();
  syncSettings();

  await registerSettings();

  // 自动连接：启动即探测，失败就后台静默重连（用户不用管）
  if (extSettings().autoConnect !== false) {
    const conn = await refreshHealth(true);
    console.info(
      conn.ok ? `[memory-plugin] 已自动连接 ${conn.baseUrl}` : '[memory-plugin] 未探测到后端，转入后台重连',
    );
  }
  if (!extSettings().headless) await refreshSnapshot();
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
    await onAiMessage(chat[messageIndex] || chat[chat.length - 1]);
  });

  source.on(events.MESSAGE_SENT || 'message_sent', () => prefetch());

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
  refreshSnapshot,
  switchView,
};