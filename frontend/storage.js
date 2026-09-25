/**
 * 草稿本地存储（SPEC 11.3）。
 *
 * 1. key = draft_{chat_id}
 * 2. 后端存 draft_events，前端启动时同步
 * 3. 双写冲突：以 updated_at 最新为准
 * 4. 切换会话不丢失
 * 5. 后端崩溃，前端仍可预览和提交
 */

import { upsertDraft } from './api.js';

const PREFIX = 'memory_plugin:draft_';
const SETTINGS_KEY = 'memory_plugin:settings';

function key(chatId) {
  return `${PREFIX}${chatId || 'default'}`;
}

export function loadLocal(chatId) {
  try {
    const raw = localStorage.getItem(key(chatId));
    if (!raw) return { chat_id: chatId, version: 1, drafts: [], last_sync: 0 };
    const parsed = JSON.parse(raw);
    return {
      chat_id: parsed.chat_id || chatId,
      version: parsed.version || 1,
      drafts: Array.isArray(parsed.drafts) ? parsed.drafts : [],
      last_sync: parsed.last_sync || 0,
    };
  } catch (error) {
    return { chat_id: chatId, version: 1, drafts: [], last_sync: 0 };
  }
}

export function saveLocal(chatId, payload) {
  try {
    localStorage.setItem(key(chatId), JSON.stringify({ ...payload, last_sync: Date.now() }));
    return true;
  } catch (error) {
    return false;
  }
}

export function upsertLocalDraft(chatId, draft) {
  const store = loadLocal(chatId);
  const index = store.drafts.findIndex((d) => d.id === draft.id);
  const merged = { ...draft, updated_at: draft.updated_at || Date.now() };
  if (index >= 0) {
    // 冲突以 updated_at 最新为准
    if ((store.drafts[index].updated_at || 0) > merged.updated_at) return store.drafts[index];
    store.drafts[index] = merged;
  } else {
    store.drafts.push(merged);
  }
  saveLocal(chatId, store);
  return merged;
}

export function updateDraftState(chatId, draftId, status) {
  const store = loadLocal(chatId);
  const target = store.drafts.find((d) => d.id === draftId);
  if (target) {
    target.status = status;
    target.updated_at = Date.now();
    saveLocal(chatId, store);
  }
  return target;
}

export function removeDraft(chatId, draftId) {
  const store = loadLocal(chatId);
  store.drafts = store.drafts.filter((d) => d.id !== draftId);
  saveLocal(chatId, store);
  return store.drafts;
}

/** 与后端双向同步：本地较新的推给后端，后端较新的覆盖本地。 */
export async function syncWithBackend(chatId, worldId, backendDrafts = []) {
  const store = loadLocal(chatId);
  const remoteById = new Map((backendDrafts || []).map((d) => [d.id, d]));
  const merged = [];

  for (const local of store.drafts) {
    const remote = remoteById.get(local.id);
    if (!remote) {
      merged.push(local);
      await upsertDraft(worldId, local);
      continue;
    }
    if ((local.updated_at || 0) >= (remote.updated_at || 0)) {
      merged.push(local);
      await upsertDraft(worldId, local);
    } else {
      merged.push(remote);
    }
    remoteById.delete(local.id);
  }

  for (const remote of remoteById.values()) merged.push(remote);

  saveLocal(chatId, { chat_id: chatId, version: store.version, drafts: merged, last_sync: Date.now() });
  return merged;
}

/** 插件设置持久化。 */
export function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY) || 'null');
  } catch (error) {
    return null;
  }
}

export function persistSettings(settings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    return true;
  } catch (error) {
    return false;
  }
}