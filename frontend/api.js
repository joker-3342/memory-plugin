/**
 * 后端通信层（SPEC 13）。
 *
 * 原则：
 * - 注入同步、更新异步；
 * - 注入超时 > 500ms 只用本地常驻核心；
 * - 后端不可用 → 不注入，酒馆照常跑。
 */

export const DEFAULT_SETTINGS = {
  enabled: true,
  baseUrl: 'http://127.0.0.1:8000',
  worldId: 'default-world',
  chatId: '',
  budget: 1300,
  injectTimeoutMs: 500,
  updateTimeoutMs: 10000,
  fetchTimeoutMs: 8000,
  fullCheckEvery: 20,
  preloadOnType: true,
  debugPanel: true,
};

/** 常驻核心本地缓存：后端不可用时的降级注入。 */
const localCore = {
  worldId: '',
  lastInject: '',
  lastInjectTurn: -1,
  lastCore: '',
};

let settings = { ...DEFAULT_SETTINGS };

export function configure(next) {
  settings = { ...settings, ...(next || {}) };
  return settings;
}

export function getSettings() {
  return settings;
}

async function request(path, { method = 'GET', body = null, timeoutMs = null } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || settings.fetchTimeoutMs);
  try {
    const response = await fetch(`${settings.baseUrl}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === null ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch (error) {
      return { ok: false, reason: 'invalid_json', raw: text.slice(0, 400) };
    }
  } catch (error) {
    return { ok: false, reason: error.name === 'AbortError' ? 'timeout' : 'network_error', detail: String(error) };
  } finally {
    clearTimeout(timer);
  }
}

/** 同步注入；超时/失败时退回本地常驻核心。 */
export async function fetchInject({ turn, scene, anchors, recentSummary, sceneType, speaking }) {
  const payload = {
    turn,
    world_id: settings.worldId,
    chat_id: settings.chatId,
    scene,
    anchors,
    recent_summary: recentSummary || null,
    budget: settings.budget,
    scene_type: sceneType || 'present',
    speaking: speaking || [],
  };
  const result = await request('/inject', { method: 'POST', body: payload, timeoutMs: settings.injectTimeoutMs });
  if (result && result.inject_text) {
    localCore.lastInject = result.inject_text;
    localCore.lastInjectTurn = turn;
    localCore.lastCore = extractCore(result.inject_text);
    return result;
  }
  return {
    inject_text: localCore.lastCore,
    token_count: 0,
    cache_hit: true,
    latency_ms: 0,
    trace_id: '',
    debug: { blocks: ['world_gate', 'scene', 'relations'], dropped: [], reason: 'degraded_local_core', warnings: [] },
    degraded: true,
  };
}

/** 从完整注入文本里截出常驻核心（世界门禁 + 场景 + 关系）。 */
export function extractCore(text) {
  if (!text) return '';
  const blocks = ['【世界门禁】', '【当前场景】', '【关系当前值】'];
  return text
    .split(/(?=【)/)
    .filter((chunk) => blocks.some((head) => chunk.startsWith(head)))
    .join('\n');
}

export function fetchUpdate(payload) {
  return request('/update', { method: 'POST', body: payload, timeoutMs: settings.updateTimeoutMs });
}

export function fetchCommit(payload) {
  return request('/commit', { method: 'POST', body: payload });
}

export function fetchCommitPreview(payload) {
  return request('/commit/preview', { method: 'POST', body: payload });
}

export function fetchWorldTick(payload) {
  return request('/world/tick', { method: 'POST', body: payload });
}

export function fetchSceneClose(worldId, turn) {
  return request('/world/scene/close', { method: 'POST', body: { world_id: worldId, turn } });
}

export function fetchFullCheck(worldId, turn) {
  return request('/world/check-points', { method: 'POST', body: { world_id: worldId, turn } });
}

export function fetchDrafts(worldId) {
  return request(`/debug/drafts?world_id=${encodeURIComponent(worldId)}`);
}

export function fetchLastDebug() {
  return request('/debug/last');
}

export function fetchMetrics() {
  return request('/metrics');
}

export function fetchJobs() {
  return request('/jobs/status');
}

/** 读日志尾巴（排查 bug 用）。source: main | errors */
export function fetchLogs(lines = 200, source = 'main') {
  return request(`/debug/logs?lines=${lines}&source=${encodeURIComponent(source)}`);
}

export function fetchErrorLogs(lines = 100) {
  return request(`/debug/logs/errors?lines=${lines}`);
}

export function fetchTemplates() {
  return request('/templates');
}

export function applyTemplate(templateId, worldId) {
  return request('/templates/apply', { method: 'POST', body: { template_id: templateId, world_id: worldId } });
}

export function fetchSummaries(worldId) {
  return request(`/summaries?world_id=${encodeURIComponent(worldId)}`);
}

export function exportWorld(worldId) {
  return request(`/export?world_id=${encodeURIComponent(worldId)}`);
}

export function importWorld(mode, data) {
  return request('/import', { method: 'POST', body: { mode, data } });
}

export function fetchConflicts() {
  return request('/session/conflicts');
}

export function upsertDraft(worldId, draft) {
  return request('/drafts', { method: 'POST', body: { world_id: worldId, draft } });
}

export function saveSettingsToBackend(worldId, world) {
  return request('/world/create', { method: 'POST', body: { world_id: worldId, ...world } });
}

export function attachDebug() {
  return localCore;
}