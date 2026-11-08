import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderMarkdown as md } from '../../static/markdown.js';

const has = (html, part) => assert.ok(html.includes(part), `expected ${part} in ${html}`);
const lacks = (html, part) => assert.ok(!html.includes(part), `unexpected ${part} in ${html}`);

test('paragraphs, line breaks, headings, hr', () => {
  assert.equal(md('a\nb\n\nc'), '<p>a<br>b</p><p>c</p>');
  has(md('## Title'), '<h2>Title</h2>');
  has(md('text\n\n---\n\nmore'), '<hr>');
  assert.equal(md(''), '');
});

test('bold, italic, strikethrough', () => {
  has(md('**b** __c__'), '<strong>b</strong> <strong>c</strong>');
  has(md('*x* and _y_'), '<em>x</em> and <em>y</em>');
  has(md('~~gone~~'), '<del>gone</del>');
  has(md('中*文*字'), '<em>文</em>');
});

test('italic does not break snake_case or 2*3*4', () => {
  assert.equal(md('use snake_case_name here'), '<p>use snake_case_name here</p>');
  assert.equal(md('2*3*4'), '<p>2*3*4</p>');
});

test('inline code protects content', () => {
  const html = md('`**not bold** <b> _x_ $a$`');
  has(html, '<code>**not bold** &lt;b&gt; _x_ $a$</code>');
  lacks(html, '<strong>');
});

test('fenced code with language label and copy button', () => {
  const html = md('```python\nprint("<hi>")\n```');
  has(html, '<div class="md-code">');
  has(html, '<span class="md-lang">python</span>');
  has(html, '<button type="button" class="md-copy" data-copy>');
  has(html, '<pre class="code"><code>print(&quot;&lt;hi&gt;&quot;)</code></pre>');
});

test('tilde fence, no language, unclosed fence runs to end', () => {
  const t = md('~~~\n**x**\n~~~');
  has(t, '<code>**x**</code>');
  lacks(t, 'md-lang');
  const u = md('```js\nconst a = 1;\n\n# not heading');
  has(u, '# not heading</code>');
  lacks(u, '<h1>');
});

test('fence language is sanitized', () => {
  const html = md('```"><script>alert(1)</script>\ncode\n```');
  lacks(html, '<script');
  has(html, '<span class="md-lang">scriptalert1script</span>');
});

test('GFM table with alignment and inline syntax', () => {
  const html = md('| a | b | c |\n|:--|:-:|--:|\n| **x** | `y` | [l](https://e.com) |');
  has(html, '<div class="md-table"><table>');
  has(html, '<th class="md-al-center">b</th>');
  has(html, '<th class="md-al-right">c</th>');
  has(html, '<td class="md-al-left"><strong>x</strong></td>');
  has(html, '<td class="md-al-center"><code>y</code></td>');
  has(html, 'href="https://e.com"');
});

test('table: escaped pipe, short and long rows, no outer pipes', () => {
  const html = md('a | b\n--- | ---\nx \\| y | z\nonly\n1 | 2 | 3');
  has(html, '<td>x | y</td><td>z</td>');
  has(html, '<tr><td>only</td><td></td></tr>');
  has(html, '<tr><td>1</td><td>2</td></tr>');
  lacks(html, '<td>3</td>');
});

test('table needs matching delimiter row', () => {
  lacks(md('a | b\nnot | delimiter'), '<table');
  lacks(md('| a | b |\n|---|\n| 1 | 2 |'), '<table');
});

test('lists: unordered, ordered start, nested', () => {
  has(md('- a\n- b'), '<ul><li>a</li><li>b</li></ul>');
  has(md('3. a\n4. b'), '<ol start="3"><li>a</li><li>b</li></ol>');
  const html = md('- a\n  - b\n    - c\n- d');
  has(html, '<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li><li>d</li></ul>');
});

test('task lists', () => {
  const html = md('- [ ] todo\n- [x] done');
  has(html, '<li class="task"><input type="checkbox" disabled> todo</li>');
  has(html, '<input type="checkbox" disabled checked> done');
});

test('blockquote may contain lists', () => {
  const html = md('> intro\n> - a\n> - b');
  has(html, '<blockquote><p>intro</p><ul><li>a</li><li>b</li></ul></blockquote>');
});

test('links and bare URL autolink', () => {
  has(md('[x](https://a.com/p?q=1&r=2)'), 'href="https://a.com/p?q=1&amp;r=2" target="_blank" rel="noopener noreferrer">x</a>');
  const bare = md('see https://a.com/x.');
  has(bare, '<a href="https://a.com/x" target="_blank" rel="noopener noreferrer">https://a.com/x</a>.');
  has(md('(https://a.com/x)'), 'href="https://a.com/x"');
  has(md('见 https://a.com/x，然后'), '>https://a.com/x</a>，然后');
  has(md('https://en.wikipedia.org/wiki/A_(b)'), 'href="https://en.wikipedia.org/wiki/A_(b)"');
});

test('markdown link is not double-linked', () => {
  const html = md('[https://a.com](https://a.com)');
  assert.equal(html.match(/<a /g).length, 1);
});

test('URL with underscores is not italicised', () => {
  lacks(md('https://a.com/_x_/y'), '<em>');
});

test('math: block and inline, no price false positives', () => {
  has(md('$$\nE = mc^2 \\\\ a_b\n$$'), '<div class="md-math">E = mc^2 \\\\ a_b</div>');
  has(md('$$x^2$$'), '<div class="md-math">x^2</div>');
  has(md('let $a_b*c$ be'), '<span class="md-math">a_b*c</span>');
  lacks(md('costs $5 and $10 total'), 'md-math');
  lacks(md('between $5,$10'), 'md-math');
});

test('hostile: raw HTML is escaped', () => {
  const html = md('<script>alert(1)</script> <img src=x onerror=alert(1)>');
  lacks(html, '<script');
  lacks(html, '<img');
  has(html, '&lt;script&gt;');
});

test('hostile: non-http links are not linked', () => {
  const html = md('[x](javascript:alert(1)) [y](data:text/html,x) javascript:alert(1)');
  lacks(html, '<a ');
});

test('hostile: quotes in URLs cannot break out of href', () => {
  const html = md('[x](https://a.com/"onmouseover="alert(1)) https://b.com/"onclick="x');
  lacks(html, '"onmouseover');
  lacks(html, '"onclick');
  assert.ok(!/<a [^>]*\son\w+=/.test(html));
});

test('hostile: quotes in table cells and label text', () => {
  const html = md('| "a" | b" onclick="x |\n|---|---|\n| " | <b> |');
  lacks(html, '<b>');
  assert.ok(!/<[^>]*\sonclick=/.test(html));
  has(html, '&quot;a&quot;');
});

test('hostile: placeholder control characters in input are stripped', () => {
  const html = md('\u00010\u0001 \u00020\u0002 `x`');
  lacks(html, '\u0001');
  lacks(html, '\u0002');
  has(html, '<code>x</code>');
});

test('hostile: math and code content is escaped', () => {
  has(md('$<img src=x onerror=1>$'), '&lt;img src=x onerror=1&gt;');
  lacks(md('$$\n<script>\n$$'), '<script>');
});

test('realistic Chinese answer with table', () => {
  const src = [
    '## 结论',
    '',
    '改动涉及以下文件，详见 https://example.com/docs。',
    '',
    '| 文件 | 作用 |',
    '| --- | :---: |',
    '| `static/app.js` | 前端入口 |',
    '| `server.py` | **后端** 服务 |',
    '',
    '- [x] 完成渲染',
    '- [ ] 补充测试',
    '  - 嵌套项',
    '',
    '```bash',
    'node --test tests/js/',
    '```',
  ].join('\n');
  const html = md(src);
  has(html, '<h2>结论</h2>');
  has(html, '<th>文件</th><th class="md-al-center">作用</th>');
  has(html, '<td><code>static/app.js</code></td>');
  has(html, '<td class="md-al-center"><strong>后端</strong> 服务</td>');
  has(html, '>https://example.com/docs</a>。');
  has(html, '<li class="task"><input type="checkbox" disabled checked> 完成渲染</li>');
  has(html, '嵌套项</li></ul></li>');
  has(html, '<span class="md-lang">bash</span>');
});
