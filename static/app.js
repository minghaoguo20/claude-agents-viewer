// Claude Agents Viewer frontend: read-only chat-style transcript browser.
// All transcript content is untrusted: it is only inserted via textContent
// or through renderMarkdown(), whose output is the only trusted HTML string.

import { renderMarkdown } from './markdown.js';
import { agentHash, parseAgentParams, siblingTarget, siblingJump, branchLabel, hitLeaf } from './branch.js';

// ===== Utils =====

const $ = (selector) => document.querySelector(selector);

/** Build a DOM element. Attrs: class, text, on<event> handlers, anything else is an attribute. */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) el.append(child);
  }
  return el;
}

const ICON_PATHS = {
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  chevron: 'M9 6l6 6-6 6',
  up: 'M6 15l6-6 6 6',
  down: 'M6 9l6 6 6-6',
  bottom: 'M6 5l6 6 6-6M6 13l6 6 6-6',
  copy: 'M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1zM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2',
  list: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
};

/** Inline SVG icon built through the DOM API (stroke follows currentColor). */
function icon(name) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  const attrs = {
    viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2',
    'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: `icon icon-${name}`,
  };
  for (const [key, value] of Object.entries(attrs)) svg.setAttribute(key, value);
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', ICON_PATHS[name]);
  svg.append(path);
  return svg;
}

function firstLine(text) {
  return String(text).split('\n', 1)[0];
}

function clip(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Compact local time: "14:05" today, "3月3日 14:05" otherwise. */
function formatTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const clock = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
  if (date.toDateString() === new Date().toDateString()) return clock;
  return `${date.getMonth() + 1}月${date.getDate()}日 ${clock}`;
}

function relativeTime(ms) {
  if (!ms) return '';
  const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (seconds < 45) return '刚刚';
  const units = [['天', 86400], ['小时', 3600], ['分钟', 60]];
  for (const [label, size] of units) {
    if (seconds >= size) return `${Math.floor(seconds / size)} ${label}前`;
  }
  return '刚刚';
}

function shortPath(path) {
  return path.split('/').filter(Boolean).slice(-2).join('/') || path;
}

function formatCost(usd) {
  return usd == null ? '' : `$${usd.toFixed(usd < 1 ? 3 : 2)}`;
}

const BADGE_CLASSES = {
  busy: 'busy', working: 'busy', idle: 'idle', done: 'done',
  blocked: 'blocked', shell: 'shell', failed: 'failed', error: 'failed',
};

/** Map any status string (agent, job or timeline state) onto the shared badge palette. */
function badgeClass(status) {
  return BADGE_CLASSES[String(status || '').toLowerCase()] || 'unknown';
}

function statusBadge(status) {
  return h('span', { class: `badge ${badgeClass(status)}`, text: status || 'unknown' });
}

/** Epoch ms from an ISO string or an epoch number (ms, or seconds when small). */
function toMs(value) {
  if (typeof value === 'number') return value < 1e11 ? value * 1000 : value;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

const intFormat = new Intl.NumberFormat('en-US');
const usdFormat = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const formatInt = (n) => intFormat.format(n || 0);
const formatUsd = (n) => usdFormat.format(n || 0);

/** Split text around case-insensitive matches of needle: plain strings and <mark> elements (no innerHTML). */
function highlight(text, needle) {
  const pattern = new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
  return text.split(pattern).map((part, i) => (i % 2 ? h('mark', { text: part }) : part));
}

// ===== API client =====

const agentPath = (sid) => `/api/agents/${encodeURIComponent(sid)}`;

async function api(path) {
  const response = await fetch(path, { headers: { Accept: 'application/json' } });
  let body = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON body; fall through to the status check.
  }
  if (!response.ok) throw new Error(body?.error || `HTTP ${response.status}`);
  return body;
}

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value != null) search.set(key, value);
  }
  return `?${search}`;
}

// ===== Persistent UI state =====

const COLLAPSED_KEY = 'cav.collapsedGroups';

function loadCollapsed() {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY)) || []);
  } catch {
    return new Set(); // storage unavailable or corrupt
  }
}

function saveCollapsed(collapsed) {
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed]));
  } catch {
    // Persistence is best effort.
  }
}

// ===== State =====

const state = {
  config: { poll_interval_ms: 3000, page_size: 50 },
  agents: [],
  filter: '',
  collapsed: loadCollapsed(), // cwd keys of collapsed sidebar groups
  view: null, // the active view (conversation, search or stats), or null
  viewSeq: 0,
};

/** True while `view` is still the one on screen; async code must check this after every await. */
const isActive = (view) => state.view === view;

// ===== Router =====

/** Split "#/path?a=b" into the path and its URLSearchParams. */
function splitHash(hash) {
  const raw = hash.replace(/^#/, '');
  const cut = raw.indexOf('?');
  if (cut < 0) return { path: raw, params: new URLSearchParams() };
  return { path: raw.slice(0, cut), params: new URLSearchParams(raw.slice(cut + 1)) };
}

/** Routes: {page:'agent', sid, aid, at, b}, {page:'search', q}, {page:'stats'} or null. `b` = pinned branch leaf. */
function parseHash() {
  const { path, params } = splitHash(location.hash);
  if (path === '/stats') return { page: 'stats' };
  if (path === '/search') return { page: 'search', q: params.get('q') || '' };
  const match = path.match(/^\/agent\/([^/]+)(?:\/sub\/([^/]+))?\/?$/);
  if (!match) return null;
  return {
    page: 'agent',
    sid: decodeURIComponent(match[1]),
    aid: match[2] ? decodeURIComponent(match[2]) : null,
    ...parseAgentParams(params, Boolean(match[2])), // jump target `at` and pinned leaf `b` (main transcript only)
  };
}

function onRoute() {
  closeView();
  document.body.classList.remove('sidebar-open');
  const route = parseHash();
  if (route?.page !== 'agent' || route.aid) closeDrawers();
  if (route?.page === 'agent') openView(route);
  else if (route?.page === 'search') openSearch(route);
  else if (route?.page === 'stats') openStats();
  else showWelcome();
  $('#stats-link').classList.toggle('active', route?.page === 'stats');
  renderSidebar();
}

/** Leave the current view and show the blank home state; the URL is cleaned without a trailing "#". */
function goHome() {
  document.body.classList.remove('sidebar-open');
  if (location.hash) history.pushState(null, '', location.pathname + location.search);
  onRoute();
}

// ===== Sidebar =====

const UNKNOWN_DIR = '(未知目录)';

function matchesFilter(agent, needle) {
  if (!needle) return true;
  const haystack = [agent.name, agent.cwd, agent.intent, agent.short_id].join('\n').toLowerCase();
  return haystack.includes(needle);
}

/** Group agents by cwd: groups with pinned agents first, then by latest update; members keep API order. */
function groupByCwd(agents) {
  const groups = new Map();
  for (const agent of agents) {
    const key = agent.cwd || UNKNOWN_DIR;
    if (!groups.has(key)) groups.set(key, { cwd: key, agents: [], latest: 0, pinned: false });
    const group = groups.get(key);
    group.agents.push(agent);
    group.latest = Math.max(group.latest, agent.updated_at || 0);
    group.pinned = group.pinned || Boolean(agent.pinned);
  }
  return [...groups.values()].sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.latest - a.latest);
}

function renderAgentItem(agent) {
  const active = state.view?.sid === agent.session_id && !state.view.aid;
  return h('a', {
    class: `agent-item${active ? ' active' : ''}`,
    href: agentHash(agent.session_id),
    title: agent.detail || agent.intent || agent.name,
  },
  h('div', { class: 'agent-row' },
    agent.pinned ? h('span', { class: 'pin', title: '已置顶', text: '★' }) : null,
    h('span', { class: 'agent-name', text: agent.name }),
    statusBadge(agent.status)),
  h('div', { class: 'agent-sub' },
    h('span', { class: 'agent-intent', text: agent.intent || agent.short_id }),
    h('span', { text: relativeTime(agent.updated_at) })));
}

function toggleGroup(cwd) {
  if (!state.collapsed.delete(cwd)) state.collapsed.add(cwd);
  saveCollapsed(state.collapsed);
  renderSidebar();
}

/** Collapsible group; `forceOpen` (active filter) ignores the stored collapsed state. */
function renderGroup(group, forceOpen) {
  const collapsed = !forceOpen && state.collapsed.has(group.cwd);
  const name = group.cwd === UNKNOWN_DIR ? group.cwd : shortPath(group.cwd);
  const head = h('button', {
    class: 'group-head',
    type: 'button',
    title: group.cwd,
    'aria-expanded': String(!collapsed),
    onclick: () => toggleGroup(group.cwd),
  },
  icon('chevron'),
  icon('folder'),
  h('span', { class: 'group-name', text: name }),
  h('span', { class: 'count-badge', text: String(group.agents.length) }));
  return h('section', { class: `group${collapsed ? ' collapsed' : ''}` },
    head,
    collapsed ? null : h('div', { class: 'group-body' }, group.agents.map(renderAgentItem)));
}

function renderSidebar() {
  const list = $('#agent-list');
  const needle = state.filter.trim().toLowerCase();
  const visible = state.agents.filter((agent) => matchesFilter(agent, needle));
  if (!visible.length) {
    list.replaceChildren(h('div', { class: 'state', text: state.agents.length ? '没有匹配的 agent' : '没有找到 agent' }));
    return;
  }
  list.replaceChildren(...groupByCwd(visible).map((group) => renderGroup(group, Boolean(needle))));
}

async function refreshAgents() {
  try {
    state.agents = (await api('/api/agents')).agents;
    renderSidebar();
  } catch (error) {
    if (!state.agents.length) {
      $('#agent-list').replaceChildren(h('div', { class: 'state error', text: `加载 agent 列表失败：${error.message}` }));
    }
  }
  setTimeout(refreshAgents, state.config.poll_interval_ms);
}

/** One input: typing filters the sidebar, Enter runs the full-text search. */
function initSidebar() {
  const search = $('#search');
  search.addEventListener('input', () => {
    state.filter = search.value;
    renderSidebar();
  });
  search.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.isComposing) return;
    const q = search.value.trim();
    if (q) location.hash = `#/search${query({ q })}`;
  });
  $('#home-btn').addEventListener('click', goHome);
  $('#menu-btn').addEventListener('click', toggleSidebar);
  $('#backdrop').addEventListener('click', toggleSidebar);
}

// ===== Markdown code copy =====

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return legacyCopy(text);
  }
}

/** Fallback for insecure contexts where navigator.clipboard is unavailable. */
function legacyCopy(text) {
  // 16px font avoids iOS zoom; the element must be rendered (not display:none) to be selectable.
  const area = h('textarea', {
    readonly: true, 'aria-hidden': 'true', tabindex: '-1',
    style: 'position:fixed;opacity:0;top:0;left:0;width:1px;height:1px;font-size:16px',
  });
  area.value = text;
  const active = document.activeElement;
  document.body.append(area);
  try {
    area.focus({ preventScroll: true });
    area.select();
    area.setSelectionRange(0, text.length); // iOS ignores select() on readonly fields
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    area.remove();
    active?.focus?.({ preventScroll: true });
  }
}

/** Last resort: show the text in a pre-selected input so the user can copy it by hand. */
function showManualCopy(text, anchor) {
  anchor.parentElement?.querySelector('.manual-copy')?.remove();
  const input = h('input', {
    class: 'manual-copy mono', type: 'text', readonly: true, value: text,
    'aria-label': '手动复制路径', onblur: (event) => event.currentTarget.remove(),
  });
  anchor.after(input);
  input.focus();
  input.select();
}

/** Show `text` on the button briefly, then restore its original label. */
function flashLabel(button, text) {
  button.dataset.label ??= button.textContent;
  button.textContent = text;
  clearTimeout(Number(button.dataset.timer));
  button.dataset.timer = setTimeout(() => { button.textContent = button.dataset.label; }, 1500);
}

/** One delegated handler for every rendered Markdown code block's copy button. */
function initCopyButtons() {
  document.addEventListener('click', async (event) => {
    const button = event.target.closest?.('.md-copy');
    const code = button?.closest('.md-code')?.querySelector('code');
    if (!code) return;
    flashLabel(button, (await copyText(code.textContent)) ? '已复制' : '复制失败');
  });
}

// ===== Tool helpers =====

const SUMMARY_KEYS = {
  Bash: 'command',
  Read: 'file_path',
  Edit: 'file_path',
  MultiEdit: 'file_path',
  Write: 'file_path',
  NotebookEdit: 'file_path',
  Grep: 'pattern',
  Glob: 'pattern',
  Agent: 'description',
  Task: 'description',
  WebFetch: 'url',
  WebSearch: 'query',
};

/** Pull a string field out of a truncated JSON preview with a regex (the preview is not valid JSON). */
function extractFromPreview(preview, key) {
  const match = preview.match(new RegExp(`"${key}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)`));
  if (!match) return '';
  try {
    return JSON.parse(`"${match[1]}"`);
  } catch {
    return match[1];
  }
}

function toolSummary(block) {
  const key = SUMMARY_KEYS[block.name];
  let value = '';
  if (block.input) {
    value = key && typeof block.input[key] === 'string'
      ? block.input[key]
      : Object.values(block.input).find((v) => typeof v === 'string' && v && v.length <= 200) || '';
  } else if (block.input_preview) {
    value = key ? extractFromPreview(block.input_preview, key) : '';
  }
  return clip(firstLine(value), 160);
}

/** Text of a fully loaded block returned by the blocks endpoint. */
function fullBlockText(block) {
  if (typeof block.text === 'string') return block.text;
  return block.input !== undefined ? JSON.stringify(block.input, null, 2) : '';
}

// ===== Tool input rendering =====

const isString = (value) => typeof value === 'string';
const label = (text) => h('div', { class: 'label', text });

/** Lines of a text without the empty tail produced by a trailing newline. */
function textLines(text) {
  const lines = text.split('\n');
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

function diffLines(text, sign, cls) {
  return text ? textLines(text).map((line) => h('span', { class: `diff-line ${cls}`, text: `${sign} ${line}` })) : [];
}

/** Removed lines (red, "-") then added lines (green, "+"), monospace and horizontally scrollable. */
function renderDiff(oldText, newText) {
  return h('pre', { class: 'code diff' }, diffLines(oldText, '-', 'del'), diffLines(newText, '+', 'add'));
}

/** Labelled fields; string values are shown raw so multi-line commands stay readable. */
function fieldNodes(input, skip = []) {
  return Object.entries(input).filter(([key]) => !skip.includes(key)).flatMap(([key, value]) => [
    label(key),
    h('pre', { class: 'code', text: isString(value) ? value : JSON.stringify(value, null, 2) }),
  ]);
}

const isEdit = (edit) => isString(edit?.old_string) && isString(edit?.new_string);

/** Tool-specific layout (Edit/MultiEdit diff, Write code block), or null to use the generic fields. */
function renderSpecialInput(name, input) {
  if (name === 'Edit' && isEdit(input)) {
    return [...fieldNodes(input, ['old_string', 'new_string']), label('修改'), renderDiff(input.old_string, input.new_string)];
  }
  if (name === 'MultiEdit' && Array.isArray(input.edits) && input.edits.every(isEdit)) {
    return [...fieldNodes(input, ['edits']),
      ...input.edits.flatMap((edit, i) => [label(`修改 ${i + 1}`), renderDiff(edit.old_string, edit.new_string)])];
  }
  if (name === 'Write' && isString(input.content)) {
    return [...fieldNodes(input, ['content']), label('内容'), h('pre', { class: 'code nowrap', text: input.content })];
  }
  return null;
}

function renderInputFields(block) {
  if (block.input_preview != null) return [h('pre', { class: 'code', text: block.input_preview })];
  const input = block.input || {};
  if (!Object.keys(input).length) return [h('div', { class: 'muted', text: '（无输入）' })];
  return renderSpecialInput(block.name, input) || fieldNodes(input);
}

// ===== Renderers =====

/** Branch button to refocus after a switch reloads the view (keyboard users keep their place). */
let pendingFocus = null;

/** "Load full content" control for a truncated block; calls onFull(block) and removes itself on success. */
function truncationNote(view, ctx, onFull) {
  const message = h('span', { text: '内容已截断。' });
  const button = h('button', { class: 'link-btn', type: 'button', text: '加载完整内容' });
  const note = h('div', { class: 'trunc' }, message, button);
  button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = '加载中…';
    try {
      onFull(await api(`${view.base}/messages/${ctx.idx}/blocks/${ctx.bidx}`));
      note.remove();
    } catch (error) {
      message.textContent = `加载失败：${error.message}。`;
      button.disabled = false;
      button.textContent = '重试';
    }
  });
  return note;
}

function renderTextBlock(view, ctx, block) {
  const body = h('div', { class: 'md' });
  body.innerHTML = renderMarkdown(block.text || '');
  const wrap = h('div', {}, body);
  if (block.truncated) {
    wrap.append(truncationNote(view, ctx, (full) => { body.innerHTML = renderMarkdown(fullBlockText(full)); }));
  }
  return wrap;
}

/** Unformatted text (used by system rows, whose content is mostly tag-like markup). */
function renderPlainText(view, ctx, block) {
  const body = h('div', { class: 'sys-text', text: block.text || '' });
  const wrap = h('div', {}, body);
  if (block.truncated) {
    wrap.append(truncationNote(view, ctx, (full) => { body.textContent = fullBlockText(full); }));
  }
  return wrap;
}

function renderThinkingBlock(view, ctx, block) {
  const body = h('div', { class: 'thinking-body', text: block.text || '' });
  const details = h('details', { class: 'thinking' }, h('summary', { text: '思考' }), body);
  if (block.truncated) {
    body.after(truncationNote(view, ctx, (full) => { body.textContent = fullBlockText(full); }));
  }
  return details;
}

function renderToolInput(view, ctx, block) {
  const box = h('div', { class: 'tool-input' }, renderInputFields(block));
  if (block.truncated) {
    box.append(truncationNote(view, ctx, (full) => {
      const content = full.input !== undefined ? renderInputFields(full) : [h('pre', { class: 'code', text: fullBlockText(full) })];
      box.replaceChildren(...content);
    }));
  }
  return box;
}

function renderToolUse(view, ctx, block) {
  const slot = {
    resultHost: h('div', { class: 'tool-result' }, h('div', { class: 'muted', text: '等待结果…' })),
    status: h('span', { class: 'tool-status' }),
    linkHost: h('span'),
  };
  slot.card = h('details', { class: 'tool-card' },
    h('summary', {},
      h('span', { class: 'tool-name', text: block.name }),
      h('span', { class: 'tool-summary', text: toolSummary(block) }),
      slot.linkHost,
      slot.status),
    h('div', { class: 'tool-body' },
      renderToolInput(view, ctx, block),
      slot.resultHost));

  view.toolSlots.set(block.id, slot);
  const pending = view.pendingResults.get(block.id);
  if (pending) {
    view.pendingResults.delete(block.id);
    fillResult(view, slot, pending.ctx, pending.block);
  }
  linkSubagent(view, block.id, slot);
  return slot.card;
}

/** Add an "open subagent" link to a tool card if a subagent was spawned by this tool_use id. */
function linkSubagent(view, toolId, slot) {
  const subagent = view.subagentByToolUse.get(toolId);
  if (!subagent || slot.linkHost.firstChild) return;
  slot.linkHost.append(h('a', {
    class: 'sub-link',
    href: `#/agent/${encodeURIComponent(view.sid)}/sub/${encodeURIComponent(subagent.agent_id)}`,
    title: subagent.description || subagent.agent_type || '',
    text: '打开 subagent →',
  }));
}

function fillResult(view, slot, ctx, block) {
  const isError = Boolean(block.is_error);
  slot.card.classList.add(isError ? 'is-error' : 'done');
  slot.status.textContent = isError ? '出错' : '完成';

  const pre = h('pre', { class: `code${isError ? ' error' : ''}`, text: block.text || '（空结果）' });
  const parts = [label(isError ? '错误' : '结果'), pre];
  if (block.truncated) {
    parts.push(truncationNote(view, ctx, (full) => { pre.textContent = fullBlockText(full); }));
  }
  if (block.persisted_file) {
    parts.push(h('div', { class: 'result-links' }, h('a', {
      href: `${agentPath(view.sid)}/tool-results/${encodeURIComponent(block.persisted_file)}`,
      target: '_blank',
      rel: 'noopener noreferrer',
      text: '查看完整输出',
    })));
  }
  slot.resultHost.replaceChildren(...parts);
}

/** Pair a tool_result with its tool_use card, or park it until the card is rendered. */
function registerResult(view, ctx, block) {
  const slot = view.toolSlots.get(block.tool_use_id);
  if (slot) fillResult(view, slot, ctx, block);
  else view.pendingResults.set(block.tool_use_id, { ctx, block });
}

function renderBlock(view, ctx, block, plain) {
  switch (block.type) {
    case 'text': return plain ? renderPlainText(view, ctx, block) : renderTextBlock(view, ctx, block);
    case 'thinking': return renderThinkingBlock(view, ctx, block);
    case 'tool_use': return renderToolUse(view, ctx, block);
    case 'image': return h('span', { class: 'chip', text: `图片${block.media_type ? `（${block.media_type}）` : ''}` });
    default: return h('span', { class: 'chip', text: `不支持的内容块：${block.type}` });
  }
}

const SYSTEM_LABELS = {
  'task-notification': '任务通知',
  peer: '会话消息',
  'compact-summary': '上下文摘要',
  system: '系统',
};

/** Only user messages that did not come from a human are shown as system rows. */
const isSystemMessage = (msg) => msg.role !== 'assistant' && msg.origin !== 'human';

/** One-line preview of a system message: first text with markup tags stripped. */
function systemPreview(msg) {
  const text = msg.blocks.find((b) => b.type === 'text')?.text || '';
  return clip(text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(), 140);
}

/** Compact collapsed row: type label + preview, expands to the full content. */
function renderSystemRow(msg, visible) {
  const typeLabel = SYSTEM_LABELS[msg.origin] || SYSTEM_LABELS.system;
  return h('div', { class: 'msg system', 'data-idx': msg.idx },
    h('details', { class: 'sys-row' },
      h('summary', {},
        h('span', { class: 'sys-label', text: typeLabel }),
        h('span', { class: 'sys-preview', text: systemPreview(msg) }),
        h('span', { class: 'sys-time', text: formatTime(msg.timestamp) })),
      h('div', { class: 'msg-body sys-body' }, visible)));
}

function renderBubble(view, msg, visible) {
  const assistant = msg.role === 'assistant';
  const bare = msg.blocks.every((b) => ['tool_use', 'thinking', 'tool_result'].includes(b.type));
  const footer = [formatTime(msg.timestamp), assistant ? msg.model : null].filter(Boolean).join(' · ');
  const switcher = !assistant && msg.branch ? renderBranchSwitcher(view, msg) : null;
  return h('div', { class: `msg ${assistant ? 'assistant' : 'user human'}`, 'data-idx': msg.idx },
    h('div', { class: `msg-body bubble${bare ? ' bare' : ''}` }, visible),
    footer || switcher ? h('div', { class: 'msg-foot' }, footer, switcher) : null);
}

/** Compact "‹ 2/3 ›" control on a human message that has rewind siblings; switching reloads the view. */
function renderBranchSwitcher(view, msg) {
  const { branch } = msg;
  const step = (dir, text, name) => {
    const target = siblingTarget(branch, dir, view.detail?.leaf);
    return h('button', {
      class: 'branch-btn', type: 'button', text, 'aria-label': name, 'data-step': dir,
      disabled: !target,
      onclick: () => switchBranch(view, target, dir),
    });
  };
  return h('span', { class: 'branch-switch', role: 'group', title: '同一问题的回退分支', 'aria-label': '回退分支' },
    step(-1, '‹', '上一个分支'),
    h('span', { class: 'branch-label', 'aria-live': 'polite', text: branchLabel(branch) }),
    step(1, '›', '下一个分支'));
}

/** Reload the view on the sibling's branch, scrolled to that message; the hash makes it back/forward friendly. */
function switchBranch(view, target, dir) {
  if (!target) return;
  pendingFocus = { idx: target.idx, dir };
  location.hash = agentHash(view.sid, target.idx, target.leaf);
}

/** Render one message; returns null when it only carried tool results (shown inside tool cards). */
function renderMessage(view, msg, from = 0) {
  const system = isSystemMessage(msg);
  const visible = [];
  msg.blocks.forEach((block, bidx) => {
    if (bidx < from) return; // already rendered earlier
    const ctx = { idx: msg.idx, bidx };
    if (block.type === 'tool_result') registerResult(view, ctx, block);
    else visible.push(renderBlock(view, ctx, block, system));
  });
  if (!visible.length) return null;
  return system ? renderSystemRow(msg, visible) : renderBubble(view, msg, visible);
}

function renderMessages(view, messages) {
  const fragment = document.createDocumentFragment();
  for (const msg of messages) {
    const el = renderMessage(view, msg);
    if (el) fragment.append(el);
  }
  return fragment;
}

// ===== Conversation view =====

function createView({ sid, aid, at, b }) {
  const base = aid ? `${agentPath(sid)}/subagents/${encodeURIComponent(aid)}` : agentPath(sid);
  return {
    id: ++state.viewSeq,
    sid,
    aid,
    base,
    detail: null,
    subagents: [],
    subagentByToolUse: new Map(),
    minIdx: null,
    maxIdx: null,
    blockCounts: new Map(), // message idx -> number of blocks rendered
    hasMoreBefore: false,
    hasMoreAfter: false, // true while the view is a window in the middle (jump mode)
    loadingOlder: false,
    loadingAfter: false,
    summaryOpen: false, // end-of-conversation result summary expanded
    summaryKey: null, // rendered summary text, to skip no-op refreshes
    prompts: [], // human prompts from the prompts API
    promptsLoaded: false,
    timelineCount: 0, // events reported by the timeline API (drives the topbar button)
    openBranches: new Set(), // prompt idxs whose rewind versions are expanded in the drawer
    promptItems: new Map(), // prompt idx -> list element in the drawer
    currentPrompt: null, // idx of the prompt currently in view
    scrollFrame: 0,
    at: aid ? null : at,
    pinned: aid ? null : b, // pinned branch leaf, or null to follow the latest path
    wasLive: false,
    toolSlots: new Map(), // tool_use id -> card parts
    pendingResults: new Map(), // tool_use id -> result that arrived before its card
    timer: null,
    observer: null,
    bottomObserver: null,
    els: {},
  };
}

/** Minimal view object for search/stats pages, so isActive() works for them too. */
function createPageView(page) {
  return { id: ++state.viewSeq, page, timer: null, observer: null, bottomObserver: null, scrollFrame: 0, els: {} };
}

function closeView() {
  const view = state.view;
  if (!view) return;
  clearTimeout(view.timer);
  cancelAnimationFrame(view.scrollFrame);
  view.observer?.disconnect();
  view.bottomObserver?.disconnect();
  resetPromptsUi();
  resetTimelineUi();
  state.view = null;
}

/** Open/close the mobile sidebar drawer (shared by the topbar menu, backdrop and list fab). */
function toggleSidebar() {
  document.body.classList.toggle('sidebar-open');
}

function fabButton(title, iconName, onclick) {
  return h('button', { class: 'fab', type: 'button', title, 'aria-label': title, onclick }, icon(iconName));
}

/** Floating buttons at the stream's bottom-right; question buttons only exist for the main transcript. */
function buildFabs(view) {
  view.els.bottomBtn = fabButton('回到底部', 'bottom', () => jumpToBottom(view));
  const nodes = [view.els.bottomBtn];
  if (!view.aid) {
    view.els.prev = fabButton('上一个问题（[）', 'up', () => stepPrompt(view, -1));
    view.els.next = fabButton('下一个问题（]）', 'down', () => stepPrompt(view, 1));
    nodes.unshift(view.els.prev, view.els.next);
  }
  // Mobile-only (hidden by CSS on desktop); last so it sits nearest the thumb
  nodes.push(fabButton('打开 agent 列表', 'list', toggleSidebar));
  nodes[nodes.length - 1].classList.add('fab-list');
  return h('div', { class: 'fabs' }, nodes);
}

function buildViewDom(view) {
  const top = h('div', { class: 'top-status' });
  const bottom = h('div', { class: 'top-status' });
  const list = h('div', { class: 'stream' }, h('div', { class: 'state', text: '加载中…' }));
  const summary = h('div', { class: 'stream end-summary-slot' });
  const scroller = h('div', { class: 'scroller' }, top, list, summary, bottom);
  view.els = { top, bottom, list, summary, scroller };
  scroller.addEventListener('scroll', () => scheduleNavigatorUpdate(view), { passive: true });
  const stage = h('div', { class: 'stage' }, scroller, buildFabs(view));
  $('#view').replaceChildren(stage);
}

function showWelcome() {
  $('#header-content').replaceChildren();
  $('#view').replaceChildren(h('div', { class: 'state', text: '从左侧选择一个 agent 查看对话。' }));
}

function showViewError(view, error) {
  const retry = h('button', { type: 'button', text: '重试', onclick: onRoute });
  const latest = view.pinned
    ? h('button', { type: 'button', text: '回到最新分支', onclick: () => { location.hash = agentHash(view.sid); } })
    : null;
  view.els.list.replaceChildren(h('div', { class: 'state error' }, `加载失败：${error.message}`, h('div', {}, retry, ' ', latest)));
}

function renderSubagentHeader(view, name) {
  const sub = view.subagents.find((s) => s.agent_id === view.aid);
  return [
    h('a', { class: 'back', href: agentHash(view.sid), text: `← 返回 ${name}` }),
    h('div', { class: 'head-title' }, h('span', { class: 'text', text: sub?.description || `Subagent ${view.aid}` })),
    h('div', { class: 'head-meta' },
      h('span', { text: sub?.agent_type || 'subagent' }),
      sub?.model ? h('span', { class: 'mono', text: sub.model }) : null,
      sub ? h('span', { text: sub.finished ? '已结束' : '运行中' }) : null),
  ];
}

/** Folder icon + "路径" copy button for the working directory; the path itself is only exposed via the tooltip. */
function renderCwdCopy(cwd) {
  // The label doubles as the live region: it flashes "已复制" / "复制失败" in place of "路径"
  const label = h('span', { class: 'copy-label', 'aria-live': 'polite', text: '路径' });
  const button = h('button', {
    class: 'copy-btn', type: 'button', title: `复制工作目录路径：${cwd}`, 'aria-label': '复制工作目录路径',
    onclick: async () => {
      if (await copyText(cwd)) flashLabel(label, '已复制');
      else { flashLabel(label, '复制失败'); showManualCopy(cwd, button); }
      button.classList.add('flashing'); // mobile shows the label only while it flashes
      clearTimeout(Number(button.dataset.flash));
      button.dataset.flash = setTimeout(() => button.classList.remove('flashing'), 1500);
    },
  }, icon('folder'), label);
  return h('span', { class: 'cwd-group' }, button);
}

function renderHeader(view) {
  const box = $('#header-content');
  const detail = view.detail;
  if (!detail) {
    box.replaceChildren();
    return;
  }
  const name = detail.name || detail.short_id;
  if (view.aid) {
    box.replaceChildren(...renderSubagentHeader(view, name));
    return;
  }
  // Re-rendering would drop the copy feedback / manual-copy input, so keep the existing group while the path is unchanged.
  const prev = box.querySelector('.cwd-group');
  const keep = prev && prev.dataset.cwd === detail.cwd ? prev : null;
  const cwd = keep || (detail.cwd ? renderCwdCopy(detail.cwd) : null);
  if (cwd && !keep) cwd.dataset.cwd = detail.cwd;
  box.replaceChildren(
    h('div', { class: 'head-title' }, h('span', { class: 'text', text: name }), statusBadge(detail.status), cwd));
}

/** Result (fallback: detail) text for the end-of-conversation block, or '' when there is none. */
const resultText = (detail) => (detail?.result || detail?.detail || '').trim();

/** Short first line that expands to the full text as Markdown when the text is longer. */
function renderResultSummary(view, full) {
  const short = clip(firstLine(full), 160);
  const label = h('div', { class: 'end-summary-label', text: '结果摘要' });
  if (short === full) return h('div', { class: 'end-summary' }, label, h('div', { class: 'end-summary-text', text: full }));
  const body = h('div', { class: 'md summary-body' });
  body.innerHTML = renderMarkdown(full);
  return h('div', { class: 'end-summary' }, label, h('details', {
    open: view.summaryOpen,
    ontoggle: (event) => { view.summaryOpen = event.currentTarget.open; },
  }, h('summary', { text: short }), body));
}

/**
 * Keep the result summary as the last block of the scroll content. Shown only on the default path of a main view
 * once the window reaches the end; refreshed in place (no list re-render) when the text changes.
 */
function renderEndSummary(view) {
  const slot = view.els.summary;
  if (!slot) return;
  const full = view.aid || view.pinned || view.hasMoreAfter ? '' : resultText(view.detail);
  if (full === view.summaryKey) return;
  const { scroller } = view.els;
  const stick = isAtBottom(scroller);
  view.summaryKey = full;
  slot.replaceChildren(...(full ? [renderResultSummary(view, full)] : []));
  if (stick && !view.hasMoreAfter) scrollToBottom(scroller);
  updateNavigator(view);
}

function indexSubagents(view, subagents) {
  view.subagents = subagents;
  view.subagentByToolUse = new Map(subagents.filter((s) => s.tool_use_id).map((s) => [s.tool_use_id, s]));
  for (const [toolId, slot] of view.toolSlots) linkSubagent(view, toolId, slot);
}

async function fetchSubagents(view) {
  try {
    return (await api(`${agentPath(view.sid)}/subagents`)).subagents;
  } catch {
    return view.subagents; // subagent links are optional; keep what we have
  }
}

// --- scrolling ---

const isAtBottom = (scroller) => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
const scrollToBottom = (scroller) => { scroller.scrollTop = scroller.scrollHeight; };

function flash(el) {
  el.classList.add('flash');
  setTimeout(() => el.classList.remove('flash'), 2500);
}

// --- paging ---

function trackRange(view, messages) {
  for (const { idx } of messages) {
    if (view.minIdx === null || idx < view.minIdx) view.minIdx = idx;
    if (view.maxIdx === null || idx > view.maxIdx) view.maxIdx = idx;
  }
  for (const { idx, blocks } of messages) view.blockCounts.set(idx, blocks.length);
}

function setTopStatus(view, text) {
  view.els.top.replaceChildren(text);
}

function setBottomStatus(view, text) {
  view.els.bottom.replaceChildren(text);
}

/** Render the first page; jump mode (`view.at`) puts the target at the top instead of scrolling to the end. */
function applyInitialPage(view, page) {
  const { list, scroller } = view.els;
  list.replaceChildren();
  trackRange(view, page.messages);
  view.hasMoreBefore = page.has_more_before;
  view.hasMoreAfter = Boolean(page.has_more_after);
  if (page.messages.length) list.append(renderMessages(view, page.messages));
  else list.append(h('div', { class: 'state', text: '暂无消息。' }));
  setTopStatus(view, view.hasMoreBefore ? '' : '已到对话开头');
  if (view.at !== null) scrollToMessage(view, view.at);
  else scrollToBottom(scroller);
  restoreBranchFocus(view);
  if (view.hasMoreBefore) observeTop(view);
  if (view.hasMoreAfter) observeBottom(view);
  renderEndSummary(view);
  updateNavigator(view);
}

/** After a branch switch, focus the same direction button on the target message (or its sibling when disabled). */
function restoreBranchFocus(view) {
  const focus = pendingFocus;
  pendingFocus = null;
  if (!focus || focus.idx !== view.at) return;
  const buttons = [...(findMessage(view, focus.idx)?.querySelectorAll('.branch-btn') || [])];
  const same = buttons.find((b) => Number(b.dataset.step) === focus.dir && !b.disabled);
  (same || buttons.find((b) => !b.disabled))?.focus({ preventScroll: true });
}

const findMessage = (view, idx) => view.els.list.querySelector(`:scope > [data-idx="${idx}"]`);

/** Scroll to message `idx` (or the nearest rendered one after it) and flash it briefly. */
function scrollToMessage(view, idx) {
  const items = [...view.els.list.querySelectorAll(':scope > [data-idx]')];
  const target = items.find((el) => Number(el.dataset.idx) >= idx) || items[items.length - 1];
  if (!target) return;
  target.scrollIntoView({ block: 'start' });
  flash(target);
}

function observeTop(view) {
  view.observer = makeObserver(view, view.els.top, '200px 0px 0px 0px', loadOlder);
}

async function loadOlder(view) {
  if (view.loadingOlder || !view.hasMoreBefore || view.minIdx === null) return;
  view.loadingOlder = true;
  setTopStatus(view, '加载更早的消息…');
  try {
    const page = await api(`${view.base}/messages${query({ before: view.minIdx, limit: state.config.page_size, leaf: view.pinned })}`);
    if (!isActive(view)) return;
    trackRange(view, page.messages);
    view.hasMoreBefore = page.has_more_before;
    setTopStatus(view, view.hasMoreBefore ? '' : '已到对话开头');
    // Keep the viewport anchored to the same content after prepending.
    const { scroller, list } = view.els;
    const before = scroller.scrollHeight;
    list.prepend(renderMessages(view, page.messages));
    scroller.scrollTop += scroller.scrollHeight - before;
  } catch (error) {
    if (!isActive(view)) return;
    setTopStatus(view, h('span', {}, `加载更早的消息失败（${error.message}） `,
      h('button', { class: 'link-btn', type: 'button', text: '重试', onclick: () => loadOlder(view) })));
  } finally {
    view.loadingOlder = false;
    if (isActive(view)) {
      if (view.hasMoreBefore) {
        // Re-observe so the observer reports again if the sentinel is still visible.
        view.observer.unobserve(view.els.top);
        view.observer.observe(view.els.top);
      } else {
        view.observer.disconnect();
      }
    }
  }
}

/** Observe `target`; call `load` whenever it (re)enters the view. */
function makeObserver(view, target, margin, load) {
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) load(view);
  }, { root: view.els.scroller, rootMargin: margin });
  observer.observe(target);
  return observer;
}

function observeBottom(view) {
  view.bottomObserver = makeObserver(view, view.els.bottom, '0px 0px 200px 0px', loadAfter);
}

/**
 * Fetch one page after the last message we have and render it.
 * `overlap` re-requests the last message so blocks streamed into it are picked up.
 * Returns {page, fresh} or null when the view went away.
 */
async function fetchNewer(view, overlap) {
  const { page_size: limit } = state.config;
  const params = view.maxIdx === null ? { limit } : { after: view.maxIdx - (overlap ? 1 : 0), limit };
  const page = await api(`${view.base}/messages${query({ ...params, leaf: view.pinned })}`);
  if (!isActive(view)) return null;
  // Following the latest path: if our last message is gone from it, a rewind happened.
  if (overlap && !view.pinned && view.maxIdx !== null && !page.messages.some((m) => m.idx === view.maxIdx)) {
    return { rewound: true };
  }
  const grown = page.messages.filter((m) => view.blockCounts.has(m.idx)
    && m.blocks.length > view.blockCounts.get(m.idx));
  const fresh = page.messages.filter((m) => view.maxIdx === null || m.idx > view.maxIdx);
  if (grown.length || fresh.length) updateMessages(view, grown, fresh);
  return { page, fresh };
}

/** Live polling: pull everything newer than what we have. Only used once the view reached the end. Returns true on rewind. */
async function loadNewer(view) {
  for (;;) {
    const result = await fetchNewer(view, true);
    if (!result) return false;
    if (result.rewound) return true;
    if (!result.page.has_more_after || !result.fresh.length) return false;
  }
}

/** The latest path changed under us (rewind): reopen the view on the new path, dropping any jump target. */
function reloadLatest(view) {
  history.replaceState(null, '', agentHash(view.sid));
  onRoute();
}

/** Jump mode: lazily load the next page below the window until the end is reached. */
async function loadAfter(view) {
  if (view.loadingAfter || !view.hasMoreAfter) return;
  view.loadingAfter = true;
  setBottomStatus(view, '加载后续消息…');
  try {
    const result = await fetchNewer(view, false);
    if (!result) return;
    view.hasMoreAfter = result.page.has_more_after && result.fresh.length > 0;
    setBottomStatus(view, '');
  } catch (error) {
    if (!isActive(view)) return;
    setBottomStatus(view, h('span', {}, `加载后续消息失败（${error.message}） `,
      h('button', { class: 'link-btn', type: 'button', text: '重试', onclick: () => loadAfter(view) })));
  } finally {
    view.loadingAfter = false;
    if (isActive(view)) {
      renderEndSummary(view);
      if (view.hasMoreAfter) {
        view.bottomObserver.unobserve(view.els.bottom);
        view.bottomObserver.observe(view.els.bottom);
      } else {
        view.bottomObserver.disconnect();
      }
    }
  }
}

/** Append only the new blocks of already-rendered messages, then add fresh messages. */
function updateMessages(view, grown, fresh) {
  const { scroller, list } = view.els;
  const stick = !view.hasMoreAfter && isAtBottom(scroller);
  list.querySelector(':scope > .state')?.remove();
  for (const msg of grown) {
    const added = renderMessage(view, msg, view.blockCounts.get(msg.idx));
    view.blockCounts.set(msg.idx, msg.blocks.length);
    if (!added) continue;
    const body = findMessage(view, msg.idx)?.querySelector('.msg-body');
    if (!body) {
      list.append(added); // earlier blocks were all tool results, nothing was shown yet
      continue;
    }
    const extra = added.querySelector('.msg-body');
    if (!extra.classList.contains('bare')) body.classList.remove('bare');
    body.append(...extra.childNodes);
  }
  trackRange(view, fresh);
  list.append(renderMessages(view, fresh));
  if (stick) scrollToBottom(scroller);
  updateNavigator(view);
}

// --- polling ---

function scheduleTick(view) {
  view.timer = setTimeout(() => tick(view), state.config.poll_interval_ms);
}

/** Refresh header, subagents, prompts and timeline; poll for new messages only while live and at the end. */
async function tick(view) {
  try {
    const detail = await api(agentPath(view.sid));
    if (!isActive(view)) return;
    view.detail = detail;
    const live = Boolean(detail.live);
    const shouldPoll = live || view.wasLive; // one last fetch after going offline
    view.wasLive = live;
    if (shouldPoll) {
      const subagents = await fetchSubagents(view);
      if (!isActive(view)) return;
      indexSubagents(view, subagents);
      const rewound = !view.hasMoreAfter && await loadNewer(view);
      if (!isActive(view)) return;
      if (rewound) {
        reloadLatest(view);
        return;
      }
      if (isPromptsOpen()) await refreshPrompts(view);
      if (!isActive(view)) return;
      await refreshTimeline(view);
      if (!isActive(view)) return;
    }
    renderHeader(view);
    renderEndSummary(view);
  } catch {
    // Transient errors are ignored; the next tick retries.
  }
  if (isActive(view)) scheduleTick(view);
}

async function openView(route) {
  const view = createView(route);
  state.view = view;
  $('#header-content').replaceChildren();
  $('#prompts-btn').hidden = Boolean(view.aid);
  $('#timeline-btn').hidden = true; // shown once the timeline has events
  buildViewDom(view);
  try {
    const [detail, subagents] = await Promise.all([api(agentPath(view.sid)), fetchSubagents(view)]);
    if (!isActive(view)) return;
    view.detail = detail;
    view.wasLive = Boolean(detail.live);
    indexSubagents(view, subagents);
    renderHeader(view);

    const limit = state.config.page_size;
    const params = view.at !== null ? { around: view.at, limit } : { limit };
    const page = await api(`${view.base}/messages${query({ ...params, leaf: view.pinned })}`);
    if (!isActive(view)) return;
    applyInitialPage(view, page);
    refreshPrompts(view);
    refreshTimeline(view);
    scheduleTick(view);
  } catch (error) {
    if (isActive(view)) showViewError(view, error);
  }
}

// ===== Question navigator =====

// Side drawers (问题 / 时间线) share one open/close mechanism: a body class `<name>-open`
// drives the panel and backdrop CSS, and at most one drawer is open at a time.
const DRAWERS = ['prompts', 'timeline'];

const isDrawerOpen = (name) => document.body.classList.contains(`${name}-open`);

function setDrawerOpen(name, open) {
  if (open) for (const other of DRAWERS) if (other !== name) setDrawerOpen(other, false);
  document.body.classList.toggle(`${name}-open`, open);
  $(`#${name}-btn`).setAttribute('aria-expanded', String(open));
}

const closeDrawers = () => DRAWERS.forEach((name) => setDrawerOpen(name, false));
const anyDrawerOpen = () => DRAWERS.some(isDrawerOpen);
const isPromptsOpen = () => isDrawerOpen('prompts');
const setPromptsOpen = (open) => setDrawerOpen('prompts', open);

/** Clear the drawer and hide its header button; called when a view goes away. */
function resetPromptsUi() {
  $('#prompts-btn').hidden = true;
  $('#prompts-count').textContent = '';
  $('#prompts-list').replaceChildren();
}

/** Index in view.prompts of the last prompt at or above the top of the viewport, or -1. */
function currentPromptIndex(view) {
  const items = view.els.list.children;
  if (!view.prompts.length || !items.length || items[0].dataset.idx === undefined) return -1;
  const line = view.els.scroller.getBoundingClientRect().top + 24;
  let lo = 0;
  let hi = items.length - 1;
  while (lo < hi) { // first message whose bottom edge is below the reading line
    const mid = (lo + hi) >> 1;
    if (items[mid].getBoundingClientRect().bottom > line) hi = mid;
    else lo = mid + 1;
  }
  const ref = Number(items[lo].dataset.idx);
  let found = -1;
  while (found + 1 < view.prompts.length && view.prompts[found + 1].idx <= ref) found += 1;
  return found;
}

function highlightPrompt(view) {
  for (const [idx, el] of view.promptItems) el.classList.toggle('current', idx === view.currentPrompt);
  if (isPromptsOpen()) view.promptItems.get(view.currentPrompt)?.scrollIntoView({ block: 'nearest' });
}

/** Sync the highlighted question and the floating buttons with the scroll position. */
function updateNavigator(view) {
  const { scroller, bottomBtn, prev, next } = view.els;
  bottomBtn.hidden = !view.hasMoreAfter && isAtBottom(scroller);
  if (view.aid) return;
  const current = currentPromptIndex(view);
  prev.disabled = current <= 0;
  next.disabled = current >= view.prompts.length - 1;
  const idx = current >= 0 ? view.prompts[current].idx : null;
  if (idx !== view.currentPrompt) {
    view.currentPrompt = idx;
    highlightPrompt(view);
  }
}

function scheduleNavigatorUpdate(view) {
  if (view.scrollFrame) return;
  view.scrollFrame = requestAnimationFrame(() => {
    view.scrollFrame = 0;
    updateNavigator(view);
  });
}

/** Smooth-scroll when the prompt is loaded, otherwise reload the view around it (jump mode). */
function goToPrompt(view, idx) {
  const el = findMessage(view, idx);
  if (!el) {
    location.hash = agentHash(view.sid, idx, view.pinned);
    return;
  }
  el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  flash(el);
}

async function stepPrompt(view, direction) {
  if (view.detail?.live) await refreshPrompts(view); // new questions may have arrived
  if (!isActive(view)) return;
  const current = currentPromptIndex(view);
  const target = view.prompts[direction < 0 ? current - 1 : current + 1];
  if (target) goToPrompt(view, target.idx);
}

/** Jump mode holds only a window of the transcript, so reload the latest page instead of scrolling. */
function jumpToBottom(view) {
  if (view.hasMoreAfter) {
    history.replaceState(null, '', agentHash(view.sid, null, view.pinned));
    onRoute();
    return;
  }
  view.els.scroller.scrollTo({ top: view.els.scroller.scrollHeight, behavior: 'smooth' });
}

/** Collapsible list of every rewind version of a question; picking another one switches branch. */
function renderPromptBranches(view, prompt) {
  const { branch } = prompt;
  const open = view.openBranches.has(prompt.idx);
  const list = h('div', { class: 'prompt-alts', role: 'list', hidden: !open },
    branch.siblings.map((sib, i) => {
      const label = h('span', { class: 'prompt-alt-text', text: sib.text || '（空）' });
      const num = h('span', { class: 'prompt-alt-num', text: `${i + 1}.` });
      if (sib.current) {
        return h('div', { class: 'prompt-alt current', role: 'listitem', 'aria-current': 'true' }, num, label);
      }
      return h('button', {
        class: 'prompt-alt', type: 'button', role: 'listitem',
        onclick: () => {
          const target = siblingJump(sib, view.detail?.leaf);
          if (matchMedia('(max-width: 767px)').matches) setPromptsOpen(false);
          switchBranch(view, target, 0);
        },
      }, num, label);
    }));
  const toggle = h('button', {
    class: 'prompt-branch', type: 'button', 'aria-expanded': String(open),
    title: '同一问题的回退分支', text: `分支 ${branchLabel(branch)}`,
    onclick: () => {
      const now = !view.openBranches.has(prompt.idx);
      if (now) view.openBranches.add(prompt.idx);
      else view.openBranches.delete(prompt.idx);
      toggle.setAttribute('aria-expanded', String(now));
      list.hidden = !now;
    },
  });
  return [toggle, list];
}

function renderPromptItem(view, prompt, number) {
  const link = h('a', {
    class: 'prompt-item',
    href: agentHash(view.sid, prompt.idx, view.pinned),
    onclick: (event) => {
      event.preventDefault();
      goToPrompt(view, prompt.idx);
      if (matchMedia('(max-width: 767px)').matches) setPromptsOpen(false);
    },
  },
  h('div', { class: 'prompt-meta' },
    h('span', { class: 'prompt-num', text: `#${number}` }),
    h('span', { text: formatTime(prompt.timestamp) })),
  h('div', { class: 'prompt-text', text: prompt.text || '（空）' }));
  view.promptItems.set(prompt.idx, link);
  return h('div', { class: 'prompt-entry' }, link, prompt.branch ? renderPromptBranches(view, prompt) : null);
}

function renderPrompts(view) {
  const count = view.prompts.length;
  $('#prompts-count').textContent = count ? ` ${count}` : '';
  $('#prompts-title').textContent = `问题目录（${count}）`;
  view.promptItems = new Map();
  const items = view.prompts.map((prompt, i) => renderPromptItem(view, prompt, i + 1));
  $('#prompts-list').replaceChildren(...(items.length ? items : [h('div', { class: 'state', text: '暂无问题' })]));
  highlightPrompt(view);
}

/** Fetch the prompt list; re-render only when it changed so the drawer keeps its scroll position. */
async function refreshPrompts(view) {
  if (view.aid) return;
  try {
    const { prompts } = await api(`${agentPath(view.sid)}/prompts${query({ leaf: view.pinned })}`);
    if (!isActive(view)) return;
    if (!view.promptsLoaded || prompts.length !== view.prompts.length) {
      view.prompts = prompts;
      view.promptsLoaded = true;
      renderPrompts(view);
    }
    updateNavigator(view);
  } catch {
    // The navigator is optional; keep the current list.
  }
}

function initNavigator() {
  $('#prompts-btn').addEventListener('click', () => {
    const open = !isPromptsOpen();
    setPromptsOpen(open);
    if (open && state.view?.els.prev) refreshPrompts(state.view);
  });
  $('#timeline-btn').addEventListener('click', () => {
    const open = !isDrawerOpen('timeline');
    setDrawerOpen('timeline', open);
    if (open && state.view) refreshTimeline(state.view, true);
  });
  for (const name of DRAWERS) {
    $(`#${name}-close`).addEventListener('click', () => setDrawerOpen(name, false));
    $(`#${name}-backdrop`).addEventListener('click', () => setDrawerOpen(name, false));
  }
  document.addEventListener('keydown', onNavigatorKey);
}

/** "[" / "]" step through questions; Escape closes a drawer first, otherwise goes home. Ignored while typing. */
function onNavigatorKey(event) {
  if (event.key === 'Escape') {
    const sidebarOpen = document.body.classList.contains('sidebar-open');
    if (anyDrawerOpen() || sidebarOpen) {
      closeDrawers();
      document.body.classList.remove('sidebar-open');
      return;
    }
    if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.isComposing) return;
    if (event.target.closest?.('input, textarea, select, [contenteditable]')) return;
    goHome();
    return;
  }
  if (event.ctrlKey || event.metaKey || event.altKey || (event.key !== '[' && event.key !== ']')) return;
  if (event.target.closest?.('input, textarea, select, [contenteditable]')) return;
  const view = state.view;
  if (!view?.els.prev) return; // only the main conversation has questions
  event.preventDefault();
  stepPrompt(view, event.key === '[' ? -1 : 1);
}

// ===== Progress timeline =====

/** Hide the topbar button and clear the drawer; called when a view goes away. */
function resetTimelineUi() {
  $('#timeline-btn').hidden = true;
  $('#timeline-count').textContent = '';
  $('#timeline-title').textContent = '进度时间线';
  $('#timeline-list').replaceChildren();
}

function renderTimelineEvent(event, wasOpen) {
  const ms = toMs(event.at);
  const head = h('div', { class: 'tl-head' },
    statusBadge(event.state),
    h('span', { class: 'tl-time', title: ms ? new Date(ms).toLocaleString() : '', text: ms ? relativeTime(ms) : '' }),
    event.detail ? h('span', { class: 'tl-detail', text: event.detail }) : null);
  if (!event.text) return h('li', { class: 'tl-event' }, head);
  const body = h('div', { class: 'md tl-text' });
  body.innerHTML = renderMarkdown(event.text);
  return h('li', { class: 'tl-event' }, head,
    h('details', { open: wasOpen }, h('summary', { text: event.truncated ? '详情（已截断）' : '详情' }), body));
}

function renderTimeline(events) {
  const body = $('#timeline-list');
  const openIdx = new Set([...body.querySelectorAll('li > details[open]')].map((el) => Number(el.parentNode.dataset.i)));
  const stick = !body.firstChild || body.scrollHeight - body.scrollTop - body.clientHeight < 40;
  if (!events.length) {
    body.replaceChildren(h('div', { class: 'state', text: '暂无进度事件。' }));
    return;
  }
  const items = events.map((event, i) => {
    const li = renderTimelineEvent(event, openIdx.has(i));
    li.dataset.i = i;
    return li;
  });
  body.replaceChildren(h('ol', { class: 'tl-list' }, items));
  if (stick) body.scrollTop = body.scrollHeight;
}

/**
 * Fetch the timeline and update the topbar count; the list is rendered only while the drawer is open.
 * While closed it only polls when the button is still hidden (no events yet), so new events can surface it.
 */
async function refreshTimeline(view, force = false) {
  if (view.aid) return;
  const open = isDrawerOpen('timeline');
  if (!force && !open && view.timelineCount > 0) return;
  try {
    const data = await api(`${agentPath(view.sid)}/timeline${query({ limit: 200 })}`);
    if (!isActive(view)) return;
    const events = data.events || [];
    view.timelineCount = events.length;
    $('#timeline-btn').hidden = !events.length;
    $('#timeline-count').textContent = events.length ? ` ${events.length}` : '';
    $('#timeline-title').textContent = `进度时间线（${events.length}）`;
    if (isDrawerOpen('timeline')) renderTimeline(events);
  } catch (error) {
    const body = $('#timeline-list');
    if (isActive(view) && isDrawerOpen('timeline') && !body.firstChild) {
      body.replaceChildren(h('div', { class: 'state error', text: `加载时间线失败：${error.message}` }));
    }
  }
}

// ===== Page views (search, stats) =====

function setPageHeader(title, subtitle) {
  $('#header-content').replaceChildren(
    h('div', { class: 'head-title' }, h('span', { class: 'text', text: title })),
    subtitle ? h('div', { class: 'head-meta' }, h('span', { text: subtitle })) : null);
}

function showPage(...nodes) {
  $('#view').replaceChildren(h('div', { class: 'page' }, h('div', { class: 'page-inner' }, nodes)));
}

function showPageState(text, { error = false, retry = false } = {}) {
  showPage(h('div', { class: `state${error ? ' error' : ''}` }, text,
    retry ? h('div', {}, h('button', { type: 'button', text: '重试', onclick: onRoute })) : null));
}

// --- search ---

const ROLE_LABELS = { user: '用户', assistant: '助手' };

function renderHit(hit, needle) {
  const meta = [ROLE_LABELS[hit.role] || hit.role, hit.block_type, formatTime(hit.timestamp)].filter(Boolean).join(' · ');
  const rewound = hitLeaf(hit) !== null;
  return h('a', { class: 'hit', href: agentHash(hit.session_id, hit.idx, hitLeaf(hit)) },
    h('div', { class: 'hit-meta' }, meta, rewound ? h('span', { class: 'hit-branch', text: '已回退的分支' }) : null),
    h('div', { class: 'hit-snippet' }, highlight(hit.snippet || '', needle)));
}

function renderSearchResults(data, needle) {
  const groups = new Map(); // session_id -> {name, hits}, in API order
  for (const hit of data.results) {
    if (!groups.has(hit.session_id)) groups.set(hit.session_id, { name: hit.name, hits: [] });
    groups.get(hit.session_id).hits.push(hit);
  }
  const nodes = [...groups].map(([sid, group]) => h('section', { class: 'hit-group' },
    h('h2', {},
      h('a', { href: agentHash(sid), text: group.name || sid.slice(0, 8) }),
      h('span', { class: 'count', text: ` ${group.hits.length}` })),
    group.hits.map((hit) => renderHit(hit, needle))));
  if (data.truncated) nodes.push(h('div', { class: 'muted note', text: '还有更多匹配结果，请缩小搜索范围。' }));
  showPage(nodes);
}

async function openSearch({ q }) {
  const view = createPageView('search');
  state.view = view;
  const needle = q.trim();
  setPageHeader('全文搜索', needle ? `"${needle}"` : '');
  if (needle.length < 2) {
    showPageState('请输入至少 2 个字符。');
    return;
  }
  showPageState('搜索中…');
  try {
    const data = await api(`/api/search${query({ q: needle, limit: 50 })}`);
    if (!isActive(view)) return;
    if (data.results.length) renderSearchResults(data, needle);
    else showPageState(`没有找到 "${needle}" 的匹配结果。`);
  } catch (error) {
    if (isActive(view)) showPageState(`搜索失败：${error.message}`, { error: true, retry: true });
  }
}

// --- stats ---

const sumModels = (models, key) => Object.values(models || {}).reduce((total, m) => total + (m[key] || 0), 0);

const TOKEN_COLUMNS = [
  ['input_tokens', '输入'], ['output_tokens', '输出'], ['thinking_tokens', '思考'],
  ['cache_read_tokens', '缓存读取'], ['cache_creation_tokens', '缓存写入'],
];

/** Accessible table: scrollable wrapper, header cells scoped, numeric columns right-aligned. */
function dataTable(caption, columns, rows) {
  const head = h('tr', {}, columns.map((c) => h('th', { scope: 'col', class: c.num ? 'num' : null, text: c.label })));
  const body = rows.map((cells) => h('tr', {}, cells.map((cell, i) => h('td', { class: columns[i].num ? 'num' : null }, cell))));
  return h('div', { class: 'table-wrap', role: 'region', tabindex: '0', 'aria-label': caption },
    h('table', { class: 'data' }, h('caption', { text: caption }), h('thead', {}, head), h('tbody', {}, body)));
}

/** Cost number plus a thin bar showing the share of total cost (decorative; the number carries the data). */
function costCell(cost, total) {
  const share = total > 0 ? Math.min(100, (cost / total) * 100) : 0;
  const fill = h('i');
  fill.style.width = `${share.toFixed(1)}%`;
  return h('div', { class: 'cost-cell' },
    h('span', { text: formatUsd(cost) }),
    h('span', { class: 'bar', 'aria-hidden': 'true', title: `占总费用 ${share.toFixed(1)}%` }, fill));
}

function modelTable(models) {
  const rows = Object.entries(models || {}).sort((a, b) => (b[1].cost_usd || 0) - (a[1].cost_usd || 0));
  if (!rows.length) return h('div', { class: 'muted', text: '没有模型用量记录。' });
  const columns = [{ label: '模型' }, ...TOKEN_COLUMNS.map(([, text]) => ({ label: text, num: true })), { label: '费用', num: true }];
  return dataTable('按模型统计费用', columns, rows.map(([name, m]) => [
    h('span', { class: 'mono', text: name }),
    ...TOKEN_COLUMNS.map(([key]) => formatInt(m[key])),
    formatUsd(m.cost_usd),
  ]));
}

function agentTable(agents, totalCost) {
  const columns = [{ label: 'Agent' }, { label: '状态' }, { label: '消息数', num: true }, { label: '费用', num: true },
    ...TOKEN_COLUMNS.map(([, text]) => ({ label: text, num: true })),
    { label: '新增行', num: true }, { label: '删除行', num: true }];
  return dataTable('按 agent 统计费用', columns, agents.map((a) => [
    h('a', { href: agentHash(a.session_id), text: a.name || a.session_id.slice(0, 8) }),
    statusBadge(a.status),
    formatInt(a.messages),
    costCell(a.cost_usd || 0, totalCost),
    ...TOKEN_COLUMNS.map(([key]) => formatInt(sumModels(a.models, key))),
    formatInt(a.lines_added),
    formatInt(a.lines_removed),
  ]));
}

function renderStats(data) {
  const totalCost = data.totals?.cost_usd || 0;
  showPage(
    h('div', { class: 'stat-total' },
      label('总花费'),
      h('div', { class: 'stat-value', text: formatUsd(totalCost) })),
    modelTable(data.totals?.models),
    data.agents.length ? agentTable(data.agents, totalCost) : h('div', { class: 'muted', text: '暂无 agent。' }));
}

async function openStats() {
  const view = createPageView('stats');
  state.view = view;
  setPageHeader('花费统计');
  showPageState('加载中…');
  try {
    const data = await api('/api/stats');
    if (isActive(view)) renderStats(data);
  } catch (error) {
    if (isActive(view)) showPageState(`加载统计失败：${error.message}`, { error: true, retry: true });
  }
}

// ===== Startup =====

async function loadConfig() {
  try {
    Object.assign(state.config, await api('/api/config'));
  } catch {
    // Keep defaults when the config endpoint is unavailable.
  }
}

async function main() {
  await loadConfig();
  initSidebar();
  initNavigator();
  initCopyButtons();
  refreshAgents();
  window.addEventListener('hashchange', onRoute);
  onRoute();
}

main();
