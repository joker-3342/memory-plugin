/**
 * 记忆视图层 —— 把 `/ui/snapshot` 的数据渲染成 HTML。
 *
 * 这里全是**纯函数**：数据进、HTML 字符串出。不发请求、不绑事件。
 * 想自己设计界面，只需要改这个文件（或整个替换掉），
 * 数据来源与字段含义见 `frontend/UI.md`。
 */

// --------------------------------------------------------------- 小工具

/** HTML 转义（数据都来自后端，但别偷懒）。 */
export function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '"');
}

function pill(text, cls = '') {
  return `<span class="mp-pill ${cls}">${esc(text)}</span>`;
}

function tags(list, cls = '') {
  const items = (list || []).filter(Boolean);
  if (!items.length) return '<span class="mp-dim">—</span>';
  return items.map((t) => `<span class="mp-tag ${cls}">${esc(t)}</span>`).join('');
}

function kv(label, value) {
  return `<div class="mp-kv"><span class="mp-kv-k">${esc(label)}</span><span class="mp-kv-v">${value}</span></div>`;
}

function bar(value, max = 1) {
  const v = Number(value) || 0;
  const pct = Math.max(0, Math.min(100, (v / max) * 100));
  const tone = v < 0 ? 'neg' : v >= 0.7 ? 'high' : v >= 0.35 ? 'mid' : 'low';
  return `<span class="mp-bar"><i class="${tone}" style="width:${pct.toFixed(0)}%"></i></span>`;
}

function section(title, body, extra = '') {
  if (!body) return '';
  return `<section class="mp-section">
      <h4 class="mp-section-title">${esc(title)}${extra}</h4>
      <div class="mp-section-body">${body}</div>
    </section>`;
}

function empty(text) {
  return `<div class="mp-empty">${esc(text)}</div>`;
}

const MOMENTUM_ARROW = { rising: '↑', falling: '↓', stable: '→' };
const DIM_LABEL = {
  trust: '信任',
  affection: '好感',
  respect: '敬重',
  fear: '恐惧',
  debt: '人情债',
  hostility: '敌意',
};
const STATE_LABEL = { draft: '草稿', pending: '待确认', committed: '已提交', revoked: '已撤销' };

// --------------------------------------------------------------- 总览

export function renderOverview(snap) {
  const s = snap.stats || {};
  const world = snap.world || {};
  const rules = world.rules || {};

  const cards = [
    ['角色', s.characters_active ?? 0, `共 ${s.characters ?? 0}`],
    ['事件账本', s.events_committed ?? 0, s.events_revoked ? `已撤销 ${s.events_revoked}` : '硬事实'],
    ['人际关系', s.relations ?? 0, '含账本'],
    ['未闭合因果', s.drafts_open ?? 0, (s.drafts_ready ?? 0) ? `可提交 ${s.drafts_ready}` : '等闭合'],
    ['摘要', s.summaries ?? 0, '场景/卷'],
    ['未回收伏笔', s.foreshadows_unresolved ?? 0, '需跟进'],
    ['物品', s.items ?? 0, ''],
    ['地点', s.locations ?? 0, ''],
  ];

  const grid = `<div class="mp-cards">${cards
    .map(
      ([label, value, sub]) => `<div class="mp-card-stat">
        <div class="mp-card-num">${esc(value)}</div>
        <div class="mp-card-label">${esc(label)}</div>
        ${sub ? `<div class="mp-card-sub">${esc(sub)}</div>` : ''}
      </div>`,
    )
    .join('')}</div>`;

  const gate = [
    ['世界', `${world.id || snap.world_id}（${world.type || '?'} · 第${world.layer ?? 1}层）`],
    ['状态', world.status === 'active' ? '运行中' : esc(world.status)],
    ['力量上限', esc(rules.power_ceiling || '—')],
    ['时间比例', esc(rules.time_ratio || '—')],
    ['死亡规则', esc(rules.death || '—')],
    ['禁止出现', tags(rules.forbidden)],
    ['允许体系', tags(rules.allowed)],
  ]
    .map(([k, v]) => kv(k, v))
    .join('');

  const recent = (snap.events || [])
    .slice(0, 6)
    .map(
      (e) => `<div class="mp-line">
        ${pill(`第${e.turn ?? '?'}楼`, e.state === 'revoked' ? 'muted' : 'ok')}
        <span class="mp-line-main ${e.state === 'revoked' ? 'mp-strike' : ''}">${esc(e.content)}</span>
        <span class="mp-dim">${esc(e.loc || '')}</span>
      </div>`,
    )
    .join('');

  const ready = (snap.drafts || []).filter((d) => d.closed);
  const readyBlock = ready.length
    ? `<div class="mp-alert">有 ${ready.length} 条因果已闭合、可以提交：${ready
        .map((d) => esc(d.content))
        .join('、')}（到「因果链」页点「提交已闭合」）</div>`
    : '';

  return (
    grid +
    readyBlock +
    section('世界门禁', `<div class="mp-kvs">${gate}</div>`) +
    section('最近事件', recent || empty('还没有已提交的事件'))
  );
}

// --------------------------------------------------------------- 剧情摘要

export function renderSummaries(snap) {
  const list = snap.summaries || [];
  if (!list.length) return empty('还没有摘要。聊几轮之后，/update 会自动生成场景摘要。');
  return list
    .map((s) => {
      const level = { scene: '场景', chapter: '章节', volume: '卷' }[s.level] || s.level;
      const unresolved = (s.unresolved || []).length
        ? `<div class="mp-meta">未解决：${tags(s.unresolved)}</div>`
        : '';
      return `<div class="mp-block">
        <div class="mp-block-head">
          ${pill(level, 'info')}
          <span class="mp-dim">第${s.turn_start ?? '?'}–${s.turn_end ?? '?'} 楼</span>
        </div>
        <div class="mp-block-body">${esc(s.content)}</div>
        ${unresolved}
      </div>`;
    })
    .join('');
}

// --------------------------------------------------------------- 角色档案

export function renderCharacterList(snap) {
  const list = snap.characters || [];
  if (!list.length) return empty('还没有角色。用「初始化世界」或直接对话，插件会记录出场角色。');
  return list
    .map((c) => {
      const state = c.state || {};
      const badges = [
        c.archived ? pill('已归档', 'muted') : pill('在场', 'ok'),
        c.pinned ? pill('常驻', 'info') : '',
        c.personality?.drift_score > 0.5 ? pill('人设漂移', 'warn') : '',
      ].join('');
      return `<div class="mp-item" data-char="${esc(c.id)}">
        <div class="mp-item-main">
          <div class="mp-item-title">${esc(c.id)} ${badges}</div>
          <div class="mp-dim">${esc(c.anchor || '')}</div>
        </div>
        <div class="mp-item-side">${esc(state.loc || '')}</div>
      </div>`;
    })
    .join('');
}

export function renderCharacterDetail(snap, charId) {
  const c = (snap.characters || []).find((x) => x.id === charId);
  if (!c) return empty('选一个角色看看他记了什么。');

  const p = c.personality || {};
  const sp = p.speech_profile || {};
  const state = c.state || {};

  const traits = section('人格锚点 · 核心特质', tags(p.core_traits, 'strong'));
  const speech = section(
    '说话方式',
    `<div class="mp-kvs">
      ${kv('句长', esc(sp.sentence_length || '—'))}
      ${kv('正式度', esc(sp.formality || '—'))}
      ${kv('讽刺度', esc(sp.sarcasm || '—'))}
      ${kv('常用词', tags(sp.vocabulary))}
      ${kv('禁用词', tags(sp.forbidden_words, 'warn'))}
      ${kv('口头禅', tags(sp.catchphrases))}
      ${kv('情绪表达', esc(sp.emotional_expression || '—'))}
    </div>`,
  );
  const bottom = section(
    '底线与习惯',
    `<div class="mp-kvs">
      ${kv('价值观', tags(p.values))}
      ${kv('禁忌', tags(p.taboos, 'warn'))}
      ${kv('小动作', tags(p.quirks))}
      ${kv('决策模式', esc(p.decision_pattern || '—'))}
    </div>`,
  );
  const appearance = section(
    '不可变外观（不得改写）',
    `<div class="mp-kvs">${Object.entries(p.appearance_immutable || {})
      .map(([k, v]) => kv(k, esc(v)))
      .join('')}</div>`,
  );
  const drift = p.drift_score
    ? section(
        '人设漂移',
        `<div class="mp-kv"><span class="mp-kv-k">漂移值</span><span class="mp-kv-v">${bar(
          p.drift_score,
        )} ${Number(p.drift_score).toFixed(2)}${p.drift_score > 0.5 ? ' · 已触发强制纠正' : ''}</span></div>`,
      )
    : '';

  const stateBlock = section(
    '当前状态',
    `<div class="mp-kvs">${Object.entries(state)
      .map(([k, v]) => kv(k, Array.isArray(v) ? tags(v) : esc(v)))
      .join('')}</div>`,
  );
  const cognition = section(
    '认知边界（防串世界）',
    `<div class="mp-kvs">
      ${kv('知道的世界', tags(c.knows_worlds))}
      ${kv('常识体系', tags(c.common_sense))}
      ${kv('无法理解', tags(c.foreign_concepts, 'warn'))}
      ${kv('外来概念反应', esc(JSON.stringify(c.reaction_rules || {}) === '{}' ? '—' : JSON.stringify(c.reaction_rules)))}
      ${kv('初印象', esc(c.first_impression || '—'))}
    </div>`,
  );

  return (
    `<div class="mp-detail-head">
      <div class="mp-avatar">${esc(String(c.id).slice(0, 1))}</div>
      <div>
        <div class="mp-detail-title">${esc(c.id)}</div>
        <div class="mp-dim">${esc(c.anchor || '')}</div>
      </div>
    </div>` +
    stateBlock +
    traits +
    speech +
    bottom +
    appearance +
    drift +
    cognition
  );
}

// --------------------------------------------------------------- 人际关系

export function renderRelations(snap) {
  const list = snap.relations || [];
  if (!list.length) return empty('还没有关系记录。角色之间发生互动后，/update 会写入关系账本。');

  return list
    .map((r) => {
      const dims = r.dimensions || {};
      const momentum = r.momentum || {};
      const bars = ['trust', 'affection', 'respect', 'hostility', 'fear', 'debt']
        .map((dim) => {
          const arrow = MOMENTUM_ARROW[momentum[dim]] || '';
          return `<div class="mp-rel-row">
            <span class="mp-rel-label">${DIM_LABEL[dim]}</span>
            ${bar(dims[dim] ?? 0)}
            <span class="mp-rel-value">${Number(dims[dim] ?? 0).toFixed(2)}${arrow}</span>
          </div>`;
        })
        .join('');

      const ledger = (r.ledger || [])
        .slice(-6)
        .reverse()
        .map((e) => {
          const delta = Object.entries(e.delta || {})
            .map(([k, v]) => `${DIM_LABEL[k] || k}${v > 0 ? '+' : ''}${Number(v).toFixed(2)}`)
            .join(' ');
          return `<div class="mp-line">
            ${pill(`第${e.turn ?? '?'}楼`, e.manual ? 'warn' : 'info')}
            <span class="mp-line-main">${esc(e.event || '—')}</span>
            <span class="mp-delta">${esc(delta)}</span>
          </div>`;
        })
        .join('');

      const anchors = (r.anchors || []).length
        ? `<div class="mp-meta">永久锚点（不衰减）：${tags((r.anchors || []).map((a) => a.event))}</div>`
        : '';

      const cognition = [
        r.knows?.length ? kv('知道', tags(r.knows)) : '',
        r.misbeliefs?.length ? kv('误以为', tags(r.misbeliefs, 'warn')) : '',
        r.unknown?.length ? kv('不知道', tags(r.unknown)) : '',
      ]
        .filter(Boolean)
        .join('');

      return `<div class="mp-block">
        <div class="mp-block-head">
          <strong>${esc(r.from_id)}</strong><span class="mp-arrow">→</span><strong>${esc(r.to_id)}</strong>
          <span class="mp-dim">最后接触：第${r.last_contact_turn ?? '?'}楼</span>
        </div>
        <div class="mp-rel">${bars}</div>
        ${ledger ? `<div class="mp-meta mp-meta-title">关系账本（台账优先于摘要）</div>${ledger}` : ''}
        ${anchors}
        ${cognition ? `<div class="mp-kvs mp-meta-title">角色认知</div><div class="mp-kvs">${cognition}</div>` : ''}
      </div>`;
    })
    .join('');
}

// --------------------------------------------------------------- 因果链

export function renderCausal(snap) {
  const drafts = snap.drafts || [];
  const events = snap.events || [];
  const edges = snap.causal_edges || [];
  const byId = new Map(events.map((e) => [e.id, e]));

  const draftBlock = drafts.length
    ? drafts
        .map((d) => {
          const causes = (d.causes || [])
            .map((c) => {
              const ev = c.event_id ? byId.get(c.event_id) : null;
              const ok = !c.event_id || ev;
              return `<div class="mp-line">${pill('前因', ok ? 'ok' : 'warn')}
                <span class="mp-line-main">${esc(ev ? `第${ev.turn}楼 ${ev.content}` : c.desc || c.content || '—')}</span></div>`;
            })
            .join('');
          const effects = (d.effects || [])
            .map((c) => {
              const ev = c.event_id ? byId.get(c.event_id) : null;
              const ok = !c.event_id || ev;
              return `<div class="mp-line">${pill('后果', ok ? 'ok' : 'warn')}
                <span class="mp-line-main">${esc(ev ? `第${ev.turn}楼 ${ev.content}` : c.desc || c.content || '—')}</span></div>`;
            })
            .join('');
          const verdict = d.closed
            ? pill('因果已闭合 · 可提交', 'ok')
            : `<span class="mp-warn">悬空：${esc((d.blocked || []).join('、') || '等待闭合')}</span>`;
          const unresolved = (d.unresolved || []).length
            ? `<div class="mp-meta">未解决：${tags(d.unresolved)}</div>`
            : '';
          return `<div class="mp-block">
            <div class="mp-block-head">
              ${pill(STATE_LABEL[d.status] || d.status, d.status)}
              <strong>${esc(d.content)}</strong>
              <span class="mp-dim">第${d.turn ?? '?'}楼 · 重要度 ${Number(d.importance ?? 0).toFixed(2)}</span>
            </div>
            ${causes}${effects}
            <div class="mp-meta">${verdict}</div>
            ${unresolved}
          </div>`;
        })
        .join('')
    : empty('没有未闭合的因果草稿。');

  const edgeLines = edges.length
    ? edges
        .map((e) => {
          const a = byId.get(e.cause_event_id);
          const b = byId.get(e.effect_event_id);
          return `<div class="mp-line">
            <span class="mp-line-main">${esc(a ? a.content : `#${e.cause_event_id}`)}</span>
            <span class="mp-dim">—${esc(e.relation)}→</span>
            <span class="mp-line-main">${esc(b ? b.content : `#${e.effect_event_id}`)}</span>
          </div>`;
        })
        .join('')
    : empty('还没有因果边。');

  const eventLines = events
    .map(
      (e) => `<div class="mp-line">
        ${pill(`第${e.turn ?? '?'}楼`, e.state === 'revoked' ? 'muted' : 'ok')}
        <span class="mp-line-main ${e.state === 'revoked' ? 'mp-strike' : ''}">${esc(e.content)}</span>
        <span class="mp-dim">${esc(e.loc || '')}${e.unresolved?.length ? ' · 有未解决项' : ''}</span>
      </div>`,
    )
    .join('');

  return (
    `<div class="mp-row mp-toolbar">
      <button id="mp-commit-ready" class="menu_button">提交已闭合</button>
      <button id="mp-refresh-drafts" class="menu_button">刷新</button>
      <span class="mp-dim">未闭合 ${drafts.filter((d) => !d.closed).length} / 共 ${drafts.length}</span>
    </div>` +
    section('未闭合的因果（draft / pending）', draftBlock) +
    section('因果边（上游 → 下游）', edgeLines) +
    section('事件账本（已写死的硬事实）', eventLines || empty('还没有事件'))
  );
}

// --------------------------------------------------------------- 物品

export function renderItems(snap) {
  const list = snap.items || [];
  if (!list.length) return empty('还没有物品记录。');
  return list
    .map(
      (i) => `<div class="mp-block">
        <div class="mp-block-head">
          <strong>${esc(i.id)}</strong>
          ${pill(i.status === 'normal' ? '正常' : i.status, i.status === 'normal' ? 'ok' : 'warn')}
          ${i.cross_world && i.cross_world !== 'valid' ? pill(`跨世界:${i.cross_world}`, 'warn') : ''}
        </div>
        <div class="mp-kvs">
          ${kv('归属', esc(i.owner || '—'))}
          ${kv('持有者', esc(i.holder || '—'))}
          ${kv('能力', tags(i.abilities))}
          ${kv('代价', esc(i.cost || '—'))}
          ${kv('来历', tags(i.history))}
        </div>
      </div>`,
    )
    .join('');
}

// --------------------------------------------------------------- 世界设定

export function renderWorld(snap) {
  const world = snap.world || {};
  const rules = world.rules || {};
  const locations = snap.locations || [];
  const factions = snap.factions || [];

  const gate = `<div class="mp-kvs">
    ${kv('世界 ID', esc(world.id || snap.world_id))}
    ${kv('类型', esc(world.type || '—'))}
    ${kv('层级', esc(world.layer ?? '—'))}
    ${kv('父世界', esc(world.parent_id || '—'))}
    ${kv('状态', esc(world.status || '—'))}
    ${kv('力量上限', esc(rules.power_ceiling || '—'))}
    ${kv('时间比例', esc(rules.time_ratio || '—'))}
    ${kv('死亡规则', esc(rules.death || '—'))}
    ${kv('禁止出现', tags(rules.forbidden, 'warn'))}
    ${kv('允许体系', tags(rules.allowed))}
    ${kv('主角在场', world.player_present ? '是' : '否')}
    ${kv('时间冻结', rules.frozen ? '是' : '否')}
  </div>`;

  const locs = locations.length
    ? locations
        .map(
          (l) => `<div class="mp-line">
            ${pill(l.status || 'active', l.status === 'destroyed' ? 'warn' : 'ok')}
            <span class="mp-line-main">${esc(l.id)}</span>
            <span class="mp-dim">${esc(l.desc || '')}</span>
          </div>`,
        )
        .join('')
    : empty('还没有地点记录');

  const fac = factions.length
    ? factions.map((f) => `<div class="mp-line"><span class="mp-line-main">${esc(f.id)}</span>
        <span class="mp-dim">${esc(f.current_action || '')}</span></div>`).join('')
    : '';

  return (
    section('世界门禁（永不裁剪，每轮注入）', gate) +
    section('地点', locs) +
    (fac ? section('势力', fac) : '')
  );
}

// --------------------------------------------------------------- 目标与伏笔

export function renderGoals(snap) {
  const goals = snap.goals || [];
  const shadows = snap.foreshadows || [];

  const goalBlock = goals.length
    ? goals
        .map(
          (g) => `<div class="mp-block">
        <div class="mp-block-head">
          ${pill(g.priority === 'main' ? '主线' : g.priority || '支线', g.priority === 'main' ? 'info' : 'muted')}
          <strong>${esc(g.content)}</strong>
          ${pill(g.status === 'active' ? '进行中' : g.status, g.status === 'active' ? 'ok' : 'muted')}
        </div>
        <div class="mp-kvs">
          ${kv('归属', esc(g.owner || '—'))}
          ${kv('线索', tags(g.clues))}
          ${kv('死路', tags(g.dead_ends, 'muted'))}
          ${kv('期限', esc(g.deadline || '—'))}
          ${kv('失败后果', esc(g.failure_consequence || '—'))}
        </div>
      </div>`,
        )
        .join('')
    : empty('还没有目标。');

  const shadowBlock = shadows.length
    ? shadows
        .map(
          (f) => `<div class="mp-line">
        ${pill(f.status === 'unresolved' ? '未回收' : f.status, f.status === 'unresolved' ? 'warn' : 'ok')}
        <span class="mp-line-main">${esc(f.content)}</span>
        <span class="mp-dim">埋于第${f.planted_at_turn ?? '?'}楼${f.deadline ? ` · 死线 ${f.deadline}` : ''}${
            f.expected_payoff ? ` · 预期：${esc(f.expected_payoff)}` : ''
          }</span>
      </div>`,
        )
        .join('')
    : empty('没有未回收的伏笔。');

  return section('目标', goalBlock) + section('伏笔（每轮常驻注入）', shadowBlock);
}

// --------------------------------------------------------------- 调试

export function renderDebugDump(label, data) {
  return `<div class="mp-meta mp-meta-title">${esc(label)}</div>
    <pre class="mp-dump">${esc(typeof data === 'string' ? data : JSON.stringify(data, null, 2))}</pre>`;
}