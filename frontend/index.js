/**
 * 记忆插件 · 前端主入口（SPEC 13.1）。
 *
 * 事件挂载：
 *   message        → 提取锚点 + 同步注入
 *   ai_message     → 异步 /update
 *   scene_change   → /world/tick + 闭合草稿
 *   每 N 轮         → 强制全量检查
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
  fetchCommitPreview,
  fetchSummaries,
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
  preloaded: null,
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

function currentTurn() {
  try {
    const chat = ctx().chat || [];
    return chat.length;
  } catch (error) {
    return state.turn;
  }
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
    console.warn('[memory-plugin] setExtensionPrompt 失败，退回扩展 prompt 缓存', error);
  }
}

// --------------------------------------------------------------- 事件流程

async function onMessage(message) {
  if (!extSettings().enabled) return;
  const chat = ctx().chat || [];
  const turn = chat.length;
  state.turn = turn;

  const anchors = extractAnchors(message);
  const scene = inferScene(chat);
  const change = detectSceneChange(state.scene, scene);
  if (change.changed) {
    state.scene = scene;
    await onSceneChange(change.reason);
  } else {
    state.scene = scene;
  }

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

  if (result.degraded) {
    toast('记忆插件：后端无响应，已降级为常驻核心注入');
  }

  if (turn % Math.max(1, extSettings().fullCheckEvery) === 0) {
    const check = await fetchFullCheck(extSettings().worldId, turn);
    if (check && check.personality_drift?.length) {
      toast(`记忆插件：${check.personality_drift.length} 个角色人设出现漂移`);
    }
    refreshDrafts();
  }
}

async function onAiMessage(message, userMessage) {
  if (!extSettings().enabled) return;
  const turn = ctx().chat?.length || state.turn;
  const chat = ctx().chat || [];
  const userMsg = userMessage || (chat.length >= 2 ? chat[chat.length - 2] : null);
  const worldId = extSettings().worldId;
  const cid = chatId();

  const payload = {
    turn,
    world_id: worldId,
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
  });
}

async function onSceneChange(reason) {
  const worldId = extSettings().worldId;
  const cid = chatId();
  await fetchSceneClose(worldId, state.turn);
  await fetchWorldTick({ world_id: worldId, meta_time: Math.max(0, state.turn * 10), player_present: true });
  const drafts = await syncWithBackend(cid, worldId, state.drafts);
  state.drafts = drafts;
  renderCausalPanel();
  if (reason) console.info('[memory-plugin] 场景切换：', reason);
}

async function prefetch() {
  if (!extSettings().preloadOnType || !extSettings().enabled) return;
  const worldId = extSettings().worldId;
  const cid = chatId();
  const drafts = loadLocal(cid).drafts;
  try {
    const response = await fetchDrafts(worldId);
    if (response && response.drafts) {
      state.drafts = await syncWithBackend(cid, worldId, response.drafts);
      renderCausalPanel();
    }
  } catch (error) {
    state.drafts = drafts;
  }
}

// --------------------------------------------------------------- UI

function toast(text) {
  try {
    const c = ctx();
    if (c.toastr) c.toastr.info(text);
    else console.info('[memory-plugin]', text);
  } catch (error) {
    console.info('[memory-plugin]', text);
  }
}

async function loadTemplate(name) {
  try {
    const url = new URL(`./ui/${name}`, import.meta.url);
    const response = await fetch(url);
    return await response.text();
  } catch (error) {
    return '';
  }
}

function mountPanels() {
  const container = document.getElementById('memory-plugin-settings');
  if (!container) return;

  loadTemplate('panel.html').then((html) => {
    document.getElementById('mp-panel-body').innerHTML = html || '';
    bindPanel();
    loadTemplate('causal.html').then((causalHtml) => {
      document.getElementById('mp-causal-body').innerHTML = causalHtml || '';
      loadTemplate('debug.html').then((debugHtml) => {
        document.getElementById('mp-debug-body').innerHTML = debugHtml || '';
        bindDebug();
        refreshDrafts();
        refreshDebug();
      });
    });
  });
}

function bindPanel() {
  const s = extSettings();
  const set = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.value = value;
  };
  const setChecked = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.checked = !!value;
  };

  set('mp-base-url', s.baseUrl);
  set('mp-world-id', s.worldId);
  set('mp-budget', s.budget);
  set('mp-inject-timeout', s.injectTimeoutMs);
  set('mp-full-check', s.fullCheckEvery);
  setChecked('mp-enabled', s.enabled);
  setChecked('mp-debug-panel', s.debugPanel);

  document.getElementById('mp-save')?.addEventListener('click', () => {
    const next = {
      ...s,
      baseUrl: document.getElementById('mp-base-url').value.trim() || s.baseUrl,
      worldId: document.getElementById('mp-world-id').value.trim() || s.worldId,
      budget: parseInt(document.getElementById('mp-budget').value, 10) || s.budget,
      injectTimeoutMs: parseInt(document.getElementById('mp-inject-timeout').value, 10) || s.injectTimeoutMs,
      fullCheckEvery: parseInt(document.getElementById('mp-full-check').value, 10) || s.fullCheckEvery,
      enabled: document.getElementById('mp-enabled').checked,
      debugPanel: document.getElementById('mp-debug-panel').checked,
    };
    Object.assign(s, next);
    configure(s);
    persistSettings(s);
    toast('记忆插件：设置已保存');
  });

  document.getElementById('mp-export')?.addEventListener('click', async () => {
    const data = await exportWorld(s.worldId);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${s.worldId}-memory-export.json`;
    link.click();
  });

  document.getElementById('mp-template')?.addEventListener('click', async () => {
    const templates = await fetchTemplates();
    const list = templates?.templates || [];
    if (!list.length) return toast('记忆插件：模板加载失败');
    const choice = prompt(`可用模板：\n${list.map((t) => t.id).join('\n')}\n输入要应用的模板 ID：`, list[0].id);
    if (!choice) return;
    await applyTemplate(choice, s.worldId);
    toast(`记忆插件：已应用模板 ${choice}`);
  });

  document.getElementById('mp-sync-entities')?.addEventListener('click', async () => {
    const templates = await fetchTemplates();
    if (templates && templates.ok) toast('记忆插件：实体表同步完成');
  });
}

function bindDebug() {
  document.getElementById('mp-refresh-debug')?.addEventListener('click', refreshDebug);
  document.getElementById('mp-refresh-drafts')?.addEventListener('click', refreshDrafts);
  document.getElementById('mp-refresh-jobs')?.addEventListener('click', refreshJobs);
  document.getElementById('mp-refresh-logs')?.addEventListener('click', () => refreshLogs('main'));
  document.getElementById('mp-refresh-errors')?.addEventListener('click', () => refreshLogs('errors'));
}

async function refreshDebug() {
  const target = document.getElementById('mp-debug-dump');
  const last = await fetchLastDebug();
  const metrics = await fetchMetrics();
  state.lastDebug = last;
  if (target) {
    target.textContent = JSON.stringify({ last, metrics }, null, 2);
  }
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
  const worldId = extSettings().worldId;
  const response = await fetchDrafts(worldId);
  if (response && response.drafts) {
    state.drafts = response.drafts;
    renderCausalPanel();
  }
}

function renderCausalPanel() {
  const target = document.getElementById('mp-causal-list');
  if (!target) return;
  const drafts = state.drafts || [];
  if (!drafts.length) {
    target.textContent = '当前没有未闭合的因果草稿。';
    return;
  }
  target.innerHTML = drafts
    .map((d) => {
      const causes = (d.causes || []).map((c) => `前因：${c.desc || c.event_id || c.content || '—'}`).join('<br>');
      const effects = (d.effects || []).map((c) => `后果：${c.desc || c.event_id || c.content || '—'}`).join('<br>');
      const state_ = d.closed ? '可提交' : `悬空：${(d.blocked || []).join('、') || '等待因果闭合'}`;
      return `<div class="mp-row"><span class="mp-badge ${d.status}">${d.status}</span>
        <strong>${d.content}</strong></div>
        <div style="margin-left:12px;opacity:.8">${causes}<br>${effects}<br>→ ${state_}</div><hr>`;
    })
    .join('');
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
        <b>调试</b>
        <div id="mp-debug-body"></div>
      </div>
    </div>`;

  const host = document.getElementById('extensions_settings') || document.getElementById('extensions_settings2');
  host?.appendChild(container);

  const saved = loadSettings();
  configure({ ...DEFAULT_SETTINGS, ...extSettings(), ...(saved || {}), chatId: chatId() });
  mountPanels();
}

async function bootstrap() {
  const c = ctx();
  const s = extSettings();
  configure({ ...DEFAULT_SETTINGS, ...s, chatId: chatId() });

  await registerSettings();
  await prefetch();

  const events = c.eventTypes || c.event_types || {};
  const source = c.eventSource;

  source.on(events.GENERATION_AFTER_COMMANDS || 'generation_after_commands', async () => {
    const chat = c.chat || [];
    const lastUser = [...chat].reverse().find((m) => !m.is_user === false || m.is_user);
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
    configure({ ...getSettings(), chatId: chatId() });
    await onSceneChange('chat_changed');
  });

  console.info('[memory-plugin] 已加载', getKnownEntities());
}

if (typeof jQuery !== 'undefined') {
  jQuery(async () => {
    await bootstrap();
  });
} else {
  window.addEventListener('DOMContentLoaded', bootstrap);
}

export { state, onMessage, onAiMessage, onSceneChange, extractAnchors, setKnownEntities };