/**
 * 锚点提取（SPEC 13.2）。
 *
 * 策略：
 * 1. 优先精确匹配已知实体表（人物 / 地点 / 物品，由后端提供）；
 * 2. 无匹配则用正则提取专有名词（中文引号、书名号、连续的 CJK 片段）；
 * 3. 去重、去停用词、限制数量。
 */

const STOP_WORDS = new Set([
  '然后', '于是', '因为', '所以', '如果', '可是', '但是', '这个', '那个', '什么', '一个',
  '自己', '我们', '你们', '他们', '现在', '已经', '还是', '只是', '就是', '可能', '应该',
]);

const KNOWN = {
  characters: new Set(),
  locations: new Set(),
  items: new Set(),
};

export function setKnownEntities({ characters = [], locations = [], items = [] } = {}) {
  KNOWN.characters = new Set(characters);
  KNOWN.locations = new Set(locations);
  KNOWN.items = new Set(items);
}

export function getKnownEntities() {
  return {
    characters: [...KNOWN.characters],
    locations: [...KNOWN.locations],
    items: [...KNOWN.items],
  };
}

const entityPattern = /[\u4e00-\u9fa5A-Za-z]{2,12}/g;

function matchKnown(text, set) {
  const hits = [];
  for (const name of set) {
    if (name && text.includes(name)) hits.push(name);
  }
  return hits;
}

export function extractAnchors(message, limit = 12) {
  const text = typeof message === 'string' ? message : String(message?.mes || message?.content || '');
  if (!text) return [];

  const found = new Set();

  // 1) 已知实体优先
  matchKnown(text, KNOWN.characters).forEach((n) => found.add(n));
  matchKnown(text, KNOWN.locations).forEach((n) => found.add(n));
  matchKnown(text, KNOWN.items).forEach((n) => found.add(n));

  // 2) 引号/书名号中的专有名词
  const quoted = text.match(/[「『“"《]([^」』”"》]{1,12})[」』”"》]/g) || [];
  quoted.forEach((chunk) => {
    const inner = chunk.replace(/[「『“"《」』”"》]/g, '').trim();
    if (inner.length >= 2 && !STOP_WORDS.has(inner)) found.add(inner);
  });

  // 3) 正则兜底（仅在已知实体命中不足时使用，避免噪声）
  if (found.size < 4) {
    const candidates = text.match(entityPattern) || [];
    candidates.forEach((word) => {
      if (word.length >= 2 && word.length <= 6 && !STOP_WORDS.has(word)) found.add(word);
      if (found.size >= limit) return;
    });
  }

  return [...found].slice(0, limit);
}

/** 场景切换判定（SPEC 11.4）。 */
export function detectSceneChange(prevScene, nextScene) {
  if (!prevScene) return { changed: true, reason: 'first_scene' };
  if (prevScene.loc !== nextScene.loc) return { changed: true, reason: 'location_changed' };

  const prev = new Set(prevScene.chars || []);
  const next = new Set(nextScene.chars || []);
  const union = new Set([...prev, ...next]);
  if (union.size === 0) return { changed: false, reason: '' };

  let shared = 0;
  next.forEach((c) => {
    if (prev.has(c)) shared += 1;
  });
  const delta = 1 - shared / union.size;
  if (delta > 0.5) return { changed: true, reason: 'cast_changed' };
  return { changed: false, reason: '' };
}