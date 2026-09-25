/**
 * 后端通信层（SPEC 13）。
 *
 * 原则：
 * - 注入同步、更新异步；
 * - 注入超时 > 500ms 只用本地常驻核心；
 * - 后端不可用 → 不注入，酒馆照常跑。
 *
 * 自动连接（新增）：
 * - `baseUrl` 留空 = 自动探测。按候选地址列表逐个打 `/health`，命中即用并写回设置；
 * - 后端重启 / 换端口后，请求失败会自动重新探测一次（不用手动改地址）；
 * - 连接状态暴露给 UI（绿点 / 红点 + 后端版本、向量后端）。
 */

export const DEFAULT_SETTINGS = {
  enabled: true,
  baseUrl: '', // 留空 = 自动探测
  autoConnect: true,
  worldId: '', // 留空 = 自动取角色卡名
  chatId: '',
  budget: 1300,
  injectTimeoutMs: 500,
  updateTimeoutMs: 10000,
  fetchTimeoutMs: 8000,
  fullCheckEvery: 20,
  preloadOnType: true,
  debugPanel: true,
};

/** 探测超时：要短，别拖住酒馆启动。 */
const PROBE_TIMEOUT_MS = 900;

/**
 * 候选端口：8000 是默认，但用户机器上 8000 经常被别的服务占用，
 * 所以再备 8080 / 8001。探测是**并行**的，多几个候选不会变慢。
 */
const CANDIDATE_PORTS = [8000, 8080, 8001];

/**
 * 候选后端地址（按优先级排序）：
 * 1. 本机 127.0.0.1 —— 后端和酒馆同机（最常见）
 * 2. localhost —— 部分环境只解析这个
 * 3. 酒馆所在主机 —— 酒馆在电脑、后端也在电脑
 * 4. 10.0.2.2 —— Android 模拟器访问宿主机
 *
 * 校验规则：只有 `/health` 返回 `{ok:true}` 才算命中。
 * 所以即使 8000 上跑着别的服务（返回 HTML/404），也不会被误认成后端。
 */
export function candidateBases() {
  const hosts = ['http://127.0.0.1', 'http://localhost'];
  try {
    const { protocol, hostname } = window.location;
    if (hostname && hostname !== 'localhost' && hostname !== '127.0.0.1') {
      hosts.push(`${protocol}//${hostname}`);
    }
  } catch (error) {
    /* 非浏览器环境忽略 */
  }
  hosts.push('http://10.0.2.2'); // Android 模拟器访问宿主机

  const list = [];
  // 先按端口分组：8000 的所有主机优先，再 8080、8001
  for (const port of CANDIDATE_PORTS) {
    for (const host of hosts) {
      const url = `${host}:${port}`;
      if (!list.includes(url)) list.push(url);
    }
  }
  return list;
}

let settings = { ...DEFAULT_SETTINGS };

const connection = {
  connected: false,
  baseUrl: '',
  health: null,
  tried: [],
  lastError: '',
  checkedAt: 0,
};

/** 常驻核心本地缓存：后端不可用时的降级注入。 */
const localCore = {
  worldId: '',
  lastInject: '',
  lastInjectTurn: -1,
  lastCore: '',
};

export function configure(next) {
  settings = { ...settings, ...(next || {}) };
  return settings;
}

export function getSettings() {
  return settings;
}

export function getConnection() {
  return { ...connection, health: connection.health ? { ...connection.health } : null };
}

function normalize(url) {
  return String(url || '').trim().replace(/\/+$/, '');
}

/** 探测单个地址：`/health` 返回 `{ok:true}` 才算通。 */
export async function probeBase(baseUrl, timeoutMs = PROBE_TIMEOUT_MS) {
  const base = normalize(baseUrl);
  if (!base) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${base}/health`, { signal: controller.signal });
    if (!response.ok) return null;
    const health = await response.json();
    if (!health || health.ok !== true) return null;
    return { baseUrl: base, health };
  } catch (error) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 自动连接：先用已配置地址（若有），再按候选列表逐个探测。
 * 成功后写回 settings.baseUrl，后续请求直接用。
 */
export async function autoConnect(preferred) {
  const tried = [];
  const ordered = [];
  const pref = normalize(preferred ?? settings.baseUrl);
  if (pref) ordered.push(pref);
  candidateBases().forEach((url) => {
    if (!ordered.includes(url)) ordered.push(url);
  });

  // 并行探测：候选多了也不会变慢（总耗时 ≈ 单次超时）。
  // push 在 map 回调里同步执行，所以 tried 的顺序 = 候选优先级顺序。
  const results = await Promise.all(
    ordered.map(async (base) => {
      tried.push(base);
      return { base, hit: await probeBase(base) };
    }),
  );

  // 按候选顺序取第一个命中的（保证「已配置地址」和「8000 端口」优先级最高）
  const winner = results.find((item) => item.hit);
  if (winner) {
    const hit = winner.hit;
    connection.connected = true;
    connection.baseUrl = hit.baseUrl;
    connection.health = hit.health;
    connection.tried = tried;
    connection.lastError = '';
    connection.checkedAt = Date.now();
    settings.baseUrl = hit.baseUrl;
    return { ok: true, baseUrl: hit.baseUrl, health: hit.health, tried };
  }

  connection.connected = false;
  // 关键：清掉上次探测到的地址。
  // 否则后续请求会一直打一个已经失效的地址（后端重启/换端口后最容易踩）。
  connection.baseUrl = '';
  connection.health = null;
  connection.tried = tried;
  connection.lastError = 'no_backend_responded';
  connection.checkedAt = Date.now();
  return { ok: false, tried, error: connection.lastError };
}

function effectiveBase() {
  return normalize(settings.baseUrl || connection.baseUrl);
}

async function doRequest(base, path, method, body, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || settings.fetchTimeoutMs);
  try {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === null || body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch (error) {
      return { ok: false, reason: 'invalid_json', raw: text.slice(0, 400) };
    }
  } catch (error) {
    return {
      ok: false,
      reason: error.name === 'AbortError' ? 'timeout' : 'network_error',
      detail: String(error),
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 统一请求出口。
 * - 没有可用地址 → 先自动探测；
 * - 连接类失败 → 重新探测一次（后端可能刚起来/换了端口）。
 */
async function request(path, { method = 'GET', body = null, timeoutMs = null, retryOnFail = true } = {}) {
  let base = effectiveBase();
  if (!base) {
    const conn = await autoConnect();
    base = conn.ok ? conn.baseUrl : '';
    if (!base) {
      return { ok: false, reason: 'no_backend', detail: '自动探测未找到后端', tried: conn.tried };
    }
  }

  const result = await doRequest(base, path, method, body, timeoutMs);

  if (retryOnFail && result && (result.reason === 'network_error' || result.reason === 'timeout')) {
    const conn = await autoConnect();
    if (conn.ok && conn.baseUrl !== base) {
      return doRequest(conn.baseUrl, path, method, body, timeoutMs);
    }
  }
  return result;
}

// --------------------------------------------------------------- 健康 / 连接

export function fetchHealth() {
  return request('/health', { timeoutMs: 3000, retryOnFail: false });
}

export async function testConnection() {
  const conn = await autoConnect();
  if (!conn.ok) return conn;
  const health = await fetchHealth();
  return { ok: true, baseUrl: conn.baseUrl, health, tried: conn.tried };
}

// --------------------------------------------------------------- 注入 / 更新

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
    connection.connected = true;
    return result;
  }
  connection.connected = false;
  connection.lastError = (result && result.reason) || 'unknown';
  return {
    inject_text: localCore.lastCore,
    token_count: 0,
    cache_hit: true,
    latency_ms: 0,
    trace_id: '',
    debug: {
      blocks: ['world_gate', 'scene', 'relations'],
      dropped: [],
      reason: 'degraded_local_core',
      warnings: [],
    },
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

// --------------------------------------------------------------- 世界 / 会话

export function createWorld(worldId, extra = {}) {
  return request('/world/create', {
    method: 'POST',
    body: { world_id: worldId, type: '小千世界', rules: {}, ...extra },
  });
}

export function fetchWorld(worldId) {
  return request(`/world/${encodeURIComponent(worldId)}`);
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

// --------------------------------------------------------------- 草稿 / 因果

export function fetchDrafts(worldId) {
  return request(`/debug/drafts?world_id=${encodeURIComponent(worldId)}`);
}

export function upsertDraft(worldId, draft) {
  return request('/drafts', { method: 'POST', body: { world_id: worldId, draft } });
}

export function setDraftState(draftId, status) {
  return request('/drafts/state', { method: 'POST', body: { draft_id: draftId, status } });
}

// --------------------------------------------------------------- 调试 / 运维

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

export function attachDebug() {
  return { connection: getConnection(), localCore };
}
