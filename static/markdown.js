/**
 * Minimal, dependency-free Markdown to HTML (ES module, no DOM access, runs in Node too).
 *
 * API: renderMarkdown(source: string): string
 *
 * Contract for app.js (copy button): every fenced code block renders as
 *   <div class="md-code">
 *     <div class="md-code-head"><span class="md-lang">LANG</span>   (span only if a language is given)
 *       <button type="button" class="md-copy" data-copy>复制</button></div>
 *     <pre class="code"><code>ESCAPED CODE</code></pre>
 *   </div>
 * Blocks of 2..500 lines (a single trailing empty line ignored) are line-numbered; the numbers come from
 * a CSS counter, so they are not in the DOM text and copying never includes them:
 *   <pre class="code ln ln-N"><code><span class="ln-row">LINE\n</span>...<span class="ln-row">LAST</span></code></pre>
 * (N = digit count of the line total, min 2). textContent of <code> equals the original code exactly.
 * With event delegation: on click of `button.md-copy[data-copy]`, copy
 * `button.closest('.md-code').querySelector('code').textContent`.
 *
 * Security: input is untrusted. Code/math/link/URL fragments are rendered into a stash
 * (escaped there), all other text is escaped before any syntax is interpreted, so the
 * output only contains tags generated in this file. Links are http(s) only.
 */

export function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Placeholders: \u0001N\u0001 = inline fragment, \u0002N\u0002 = block fragment.
let stash = [];
const keepInline = (html) => `\u0001${stash.push(html) - 1}\u0001`;
const keepBlock = (html) => `\u0002${stash.push(html) - 1}\u0002`;
const resolve = (html) => html.replace(/[\u0001\u0002](\d+)[\u0001\u0002]/g, (_, n) => resolve(stash[Number(n)]));

const LIST_RE = /^( *)([-*+]|\d{1,9}[.)])( +)(.*)$/;
const HR_RE = /^ {0,3}([-*_])( *\1){2,}\s*$/;
const HEADING_RE = /^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const QUOTE_RE = /^ {0,3}&gt;\s?(.*)$/;
const BLOCK_PH_RE = /^\u0002\d+\u0002$/;
const DELIM_CELL_RE = /^:?-+:?$/;

export function renderMarkdown(source) {
  stash = [];
  const text = String(source ?? '').replace(/[\u0000-\u0002]/g, '').replace(/\r\n?/g, '\n').replace(/\t/g, '    ');
  const lines = extractBlocks(text.split('\n'));
  const html = resolve(parseBlocks(lines));
  stash = [];
  return html;
}

// ===== Pass 1: fenced code, math blocks, inline code/math (stashed), then escape =====

function extractBlocks(lines) {
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const fence = /^( *)(`{3,}|~{3,})(.*)$/.exec(lines[i]);
    const info = fence ? fence[3].trim() : '';
    if (fence && !(fence[2][0] === '`' && info.includes('`'))) {
      const [, indent, marker] = fence;
      const closeRe = new RegExp(`^ *\\${marker[0]}{${marker.length},}\\s*$`);
      const body = [];
      for (i++; i < lines.length && !closeRe.test(lines[i]); i++) body.push(lines[i].slice(Math.min(indent.length, lines[i].length - lines[i].trimStart().length)));
      out.push(indent + keepBlock(codeBlock(body.join('\n'), info)));
      continue;
    }
    const math = /^\s*\$\$(.*)$/.exec(lines[i]);
    if (math) {
      const body = [];
      let rest = math[1];
      let closed = rest.trimEnd().endsWith('$$');
      if (closed) rest = rest.trimEnd().slice(0, -2);
      if (rest.trim()) body.push(rest);
      while (!closed && ++i < lines.length) {
        const end = lines[i].indexOf('$$');
        closed = end >= 0;
        body.push(closed ? lines[i].slice(0, end) : lines[i]);
      }
      out.push(keepBlock(`<div class="md-math">${escapeHtml(body.join('\n').trim())}</div>`));
      continue;
    }
    out.push(escapeHtml(protectInline(lines[i])));
  }
  return out;
}

export const LINE_NUMBER_MIN_LINES = 2;
export const LINE_NUMBER_MAX_LINES = 500;

/**
 * Line-number markup for a code string. Returns null when the block should stay plain
 * (fewer than MIN or more than MAX lines), else { html, digits, lines }.
 * html is built from escapeHtml output only, safe for innerHTML of a <code> or <pre>.
 */
export function numberCode(code) {
  const parts = String(code).split('\n');
  const trailing = parts.length > 1 && parts[parts.length - 1] === '';
  if (trailing) parts.pop();
  if (parts.length < LINE_NUMBER_MIN_LINES || parts.length > LINE_NUMBER_MAX_LINES) return null;
  const last = parts.length - 1;
  const html = parts
    .map((line, i) => `<span class="ln-row">${escapeHtml(line)}${i < last || trailing ? '\n' : ''}</span>`)
    .join('');
  return { html, digits: Math.max(2, String(parts.length).length), lines: parts.length };
}

function codeBlock(code, info) {
  const lang = info.split(/\s+/)[0].replace(/[^\w+#.-]/g, '');
  const label = lang ? `<span class="md-lang">${lang}</span>` : '';
  return `<div class="md-code"><div class="md-code-head">${label}<button type="button" class="md-copy" data-copy>复制</button></div>`
    + `${codePre(code)}</div>`;
}

function codePre(code) {
  const n = numberCode(code);
  if (!n) return `<pre class="code"><code>${escapeHtml(code)}</code></pre>`;
  return `<pre class="code ln ln-${n.digits}"><code>${n.html}</code></pre>`;
}

/** Stash inline code and inline math so no other syntax touches their content. */
function protectInline(line) {
  return line
    .replace(/(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)/g, (_, __, code) => keepInline(`<code>${escapeHtml(code)}</code>`))
    // opening $ must be followed by non-space, closing $ preceded by non-space and not followed by a digit
    .replace(/(?<![\\$])\$([^\s$\u0001](?:[^$\n\u0001]*?[^\s$\\\u0001])?)\$(?![\d$])/g,
      (_, math) => keepInline(`<span class="md-math">${escapeHtml(math)}</span>`));
}

// ===== Pass 2: block parser (operates on escaped lines) =====

const indentOf = (line) => line.length - line.trimStart().length;

function startsBlock(lines, i) {
  const line = lines[i];
  return BLOCK_PH_RE.test(line.trim()) || HEADING_RE.test(line) || HR_RE.test(line)
    || QUOTE_RE.test(line) || LIST_RE.test(line) || isTableStart(lines, i);
}

function parseBlocks(lines) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    let m;
    if (!trimmed) { i++; continue; }
    if (BLOCK_PH_RE.test(trimmed)) { out.push(trimmed); i++; continue; }
    if ((m = HEADING_RE.exec(line))) {
      out.push(`<h${m[1].length}>${renderInline(m[2])}</h${m[1].length}>`);
      i++;
    } else if (HR_RE.test(line)) {
      out.push('<hr>');
      i++;
    } else if (QUOTE_RE.test(line)) {
      const inner = [];
      for (; i < lines.length && (m = QUOTE_RE.exec(lines[i])); i++) inner.push(m[1]);
      out.push(`<blockquote>${parseBlocks(inner)}</blockquote>`);
    } else if (isTableStart(lines, i)) {
      i = parseTable(lines, i, out);
    } else if (LIST_RE.test(line)) {
      i = parseList(lines, i, out);
    } else {
      const para = [trimmed];
      for (i++; i < lines.length && lines[i].trim() && !startsBlock(lines, i); i++) para.push(lines[i].trim());
      out.push(`<p>${para.map((l) => renderInline(l)).join('<br>')}</p>`);
    }
  }
  return out.join('');
}

// ----- tables -----

/** Split a table row on unescaped pipes; `\|` becomes a literal pipe. */
function splitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (/(?<!\\)\|$/.test(s)) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

function isTableStart(lines, i) {
  if (i + 1 >= lines.length || !lines[i].includes('|')) return false;
  const delim = splitRow(lines[i + 1]);
  return delim.every((c) => DELIM_CELL_RE.test(c)) && delim.length === splitRow(lines[i]).length;
}

function parseTable(lines, i, out) {
  const head = splitRow(lines[i]);
  const aligns = splitRow(lines[i + 1]).map((c) =>
    c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : c.startsWith(':') ? 'left' : '');
  const cell = (tag, text, k) =>
    `<${tag}${aligns[k] ? ` class="md-al-${aligns[k]}"` : ''}>${renderInline(text ?? '')}</${tag}>`;
  const rows = [];
  for (i += 2; i < lines.length && lines[i].trim() && !startsBlock(lines, i); i++) {
    const cells = splitRow(lines[i]);
    rows.push(`<tr>${head.map((_, k) => cell('td', cells[k], k)).join('')}</tr>`); // pad / truncate to header width
  }
  out.push(`<div class="md-table"><table><thead><tr>${head.map((t, k) => cell('th', t, k)).join('')}</tr></thead>`
    + `<tbody>${rows.join('')}</tbody></table></div>`);
  return i;
}

// ----- lists (nesting by indentation, task items) -----

function parseList(lines, i, out) {
  const first = LIST_RE.exec(lines[i]);
  const base = first[1].length;
  const ordered = /\d/.test(first[2]);
  const start = ordered ? parseInt(first[2], 10) : 1;
  const items = [];
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      const next = lines[j];
      const cont = next !== undefined && (indentOf(next) >= base + 2 || siblingOf(next, base, ordered));
      if (!cont) break;
      items.at(-1).lines.push('');
      i++;
      continue;
    }
    const m = LIST_RE.exec(line);
    if (m && siblingOf(line, base, ordered)) {
      items.push({ offset: m[1].length + m[2].length + m[3].length, lines: [m[4]] });
    } else if (items.length && indentOf(line) > base) {
      const item = items.at(-1);
      item.lines.push(line.slice(Math.min(indentOf(line), item.offset)));
    } else break;
    i++;
  }
  const html = items.map(renderItem).join('');
  out.push(ordered ? `<ol${start !== 1 ? ` start="${start}"` : ''}>${html}</ol>` : `<ul>${html}</ul>`);
  return i;
}

function siblingOf(line, base, ordered) {
  const m = LIST_RE.exec(line);
  return !!m && Math.abs(m[1].length - base) < 2 && /\d/.test(m[2]) === ordered;
}

function renderItem(item) {
  const task = /^\[( |x|X)\] (.*)$/.exec(item.lines[0]);
  if (task) item.lines[0] = task[2];
  const body = parseBlocks(item.lines).replace(/^<p>([\s\S]*?)<\/p>/, '$1'); // tight list: unwrap first paragraph
  if (!task) return `<li>${body}</li>`;
  return `<li class="task"><input type="checkbox" disabled${task[1] === ' ' ? '' : ' checked'}> ${body}</li>`;
}

// ===== Pass 3: inline parser (operates on escaped text) =====

const URL_RE = /https?:\/\/(?:(?!&quot;|&#39;|&lt;|&gt;)[^\s\u0001\u0002　-〿＀-￯一-鿿])+/g;

function anchor(url, labelHtml) {
  return keepInline(`<a href="${url}" target="_blank" rel="noopener noreferrer">${labelHtml}</a>`);
}

/** Cut trailing punctuation / unbalanced `)` / dangling entity off a bare URL; returns [url, tail]. */
function trimUrl(url) {
  let tail = '';
  for (;;) {
    const entity = /&(?:amp|lt|gt|quot|#39);$/.exec(url);
    const cut = entity ? entity[0].length
      : /[.,;:!?*_~'")\]]$/.test(url) && !(url.endsWith(')') && count(url, '(') >= count(url, ')')) ? 1 : 0;
    if (!cut) return [url, tail];
    tail = url.slice(-cut) + tail;
    url = url.slice(0, -cut);
  }
}
const count = (s, ch) => s.split(ch).length - 1;

function renderInline(text) {
  return text
    // [label](url): label may hold emphasis but is never auto-linked again
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)\u0001\u0002]+)\)/g, (_, label, url) => anchor(url, emphasis(label)))
    .replace(URL_RE, (raw) => { const [url, tail] = trimUrl(raw); return url ? anchor(url, url) + tail : raw; })
    .replace(/^.*$/s, emphasis);
}

function emphasis(text) {
  return text
    .replace(/\*\*(?=\S)([^\n]+?)(?<=\S)\*\*/g, '<strong>$1</strong>')
    .replace(/(?<![\w])__(?=\S)([^\n]+?)(?<=\S)__(?!\w)/g, '<strong>$1</strong>')
    .replace(/~~(?=\S)([^\n]+?)(?<=\S)~~/g, '<del>$1</del>')
    .replace(/(?<![*\w])\*([^\s*](?:[^*\n]*?[^\s*])?)\*(?![*\w])/g, '<em>$1</em>')
    .replace(/(?<![_\w])_([^\s_](?:[^_\n]*?[^\s_])?)_(?![_\w])/g, '<em>$1</em>');
}
