# Claude Agents Viewer 设计文档

本地只读网页，用于浏览 `claude agents` 中可见的 session 历史。纯 Python 3.10 标准库 + 原生 HTML/JS/CSS，无第三方依赖、无 CDN。

## 1. 硬性约束

1. **严格只读**：对 `~/.claude` 只做读操作（`open(..., "r"/"rb")`、`stat`、`glob`、`os.path.exists`）。禁止任何写、删、改名、mkdir。服务端只接受 `GET`/`HEAD`，其余方法返回 405。
2. **永不读取** `~/.claude/sessions/*.key`。
3. **只监听回环地址**：`host` 不是 `127.0.0.1` / `localhost` / `::1` 时，启动直接报错退出。
4. 代码中不得出现绝对路径；Claude home 默认 `~/.claude`，由配置文件指定。
5. 代码注释英文；类型注解完整；函数短小、单一职责。
6. 所有来自 transcript 的内容都是不可信数据：前端渲染必须先 HTML 转义（防 XSS）。

## 2. 目录结构

```
config.ini.example      # 默认配置（本地修改复制为 config.ini，已 gitignore）
viewer/
  __init__.py
  __main__.py           # python -m viewer [--config PATH]
  config.py             # load_config()
  sources.py            # 发现 agent 列表
  transcript.py         # JSONL 增量解析、归一化、分页、subagent
  server.py             # HTTP 路由
static/
  index.html  app.js  style.css
tests/
  fixtures.py           # make_claude_home(tmp) 构造假的 ~/.claude
  test_config.py  test_sources.py  test_transcript.py  test_server.py  test_readonly.py
docs/design.md
README.md
```

测试使用 `unittest`：`python3 -m unittest discover -s tests -t .`

## 3. 数据源（~/.claude 下）

| 路径 | 说明 |
|---|---|
| `jobs/<shortId>/state.json` | 后台 agent。字段：`state`(working/done/...)、`detail`、`intent`、`name`、`sessionId`、`tempo` |
| `jobs/pins.json` | JSON 数组，元素为被置顶的 id（短 id 或完整 sessionId，都要兼容） |
| `sessions/<pid>.json` | 运行中的进程。字段：`pid`、`sessionId`、`cwd`、`kind`(bg/interactive)、`name`、`status`(idle/busy)、`updatedAt`(ms)、`startedAt`(ms) |
| `projects/<encodedCwd>/<sessionId>.jsonl` | 主 transcript |
| `projects/<encodedCwd>/<sessionId>/custom-title.json` | `{"customTitle": "..."}` |
| `projects/<encodedCwd>/<sessionId>/subagents/agent-<agentId>.jsonl` | subagent transcript（记录 `isSidechain: true`） |
| `projects/<encodedCwd>/<sessionId>/subagents/agent-<agentId>.meta.json` | `agentType`、`description`、`toolUseId`、`model`、`finished` |
| `projects/<encodedCwd>/<sessionId>/tool-results/<name>.txt` | 过大的工具输出，tool_result 文本中以 `<persisted-output>... Full output saved to: <path>` 引用 |

**Agent 列表 = jobs（有 state.json 的）∪ sessions/*.json**，按 sessionId 合并去重。没有 state.json 且不在 live sessions 中的 job 目录忽略。

### JSONL 记录

每行一条 JSON，`type` 字段区分。只关心：
- `user` / `assistant`：`message.content` 为字符串或 block 数组。block 类型：`text`、`thinking`（字段 `thinking`）、`tool_use`（`id/name/input`）、`tool_result`（`tool_use_id`、`content` 为字符串或 `[{type:text,text}|{type:image}]`、`is_error`）、`image`。通用字段：`uuid`、`timestamp`、`isMeta`、`message.model`、`message.id`。
- `ai-title`（`aiTitle`）、`custom-title`（`customTitle`，如有）：标题，取最后一次出现。
- `cost-state`（`totalCostUSD`）：取最后一次出现。
- 其他类型忽略。最后一行可能写到一半：只解析以 `\n` 结尾的完整行，JSON 解析失败的行跳过。

## 4. 后端模块接口

### config.py
```python
@dataclass(frozen=True)
class Config:
    host: str
    port: int
    claude_home: Path        # expanded
    page_size: int
    max_block_chars: int
    poll_interval_ms: int
    cache_max_mb: int        # 默认 512，必须为正整数

def load_config(path: Path | None = None) -> Config
```
查找顺序：`--config` / 环境变量 `CAV_CONFIG` → 项目根目录 `config.ini` → `config.ini.example`。非回环 host 抛 `ValueError`。

`config.ini.example`：
```ini
[server]
host = 127.0.0.1
port = 8765

[claude]
home = ~/.claude

[viewer]
page_size = 50
max_block_chars = 4000
poll_interval_ms = 3000
cache_max_mb = 512
```

### sources.py
```python
@dataclass
class AgentInfo:
    session_id: str
    short_id: str            # session_id[:8]
    name: str                # job name > live name > custom-title.json > short_id
    kind: str                # "bg" | "interactive"
    status: str              # live status (idle/busy) if alive, else job state, else "unknown"
    live: bool               # live session json exists and /proc/<pid> exists
    cwd: str | None
    intent: str | None
    detail: str | None
    updated_at: int | None   # epoch ms; max(live updatedAt, transcript mtime)
    pinned: bool
    transcript: Path | None
    def to_dict(self) -> dict   # JSON-safe; replaces transcript with has_transcript: bool

def list_agents(claude_home: Path) -> list[AgentInfo]   # pinned first, then updated_at desc
def find_agent(claude_home: Path, session_id: str) -> AgentInfo | None
```

### transcript.py
```python
class Transcript:
    """Incrementally parsed JSONL transcript (append-only file)."""
    def __init__(self, path: Path) -> None
    def refresh(self) -> None            # parse newly appended complete lines only (track byte offset)
    @property
    def messages(self) -> list[dict]     # normalized, full (untruncated) content
    title: str | None
    cost_usd: float | None

class TranscriptCache:
    def get(self, path: Path) -> Transcript   # thread-safe; reuses instance and calls refresh()

def page(messages: list[dict], *, before: int | None, after: int | None,
         limit: int, max_block_chars: int) -> dict
def list_subagents(session_dir: Path) -> list[dict]
```

归一化消息（`messages` 中的元素，`idx` 为其在列表中的下标，单调稳定）：
```json
{"idx": 12, "uuid": "...", "role": "user|assistant", "timestamp": "ISO8601",
 "model": "claude-...|null", "meta": false,
 "blocks": [
   {"type": "text", "text": "..."},
   {"type": "thinking", "text": "..."},
   {"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": {...}},
   {"type": "tool_result", "tool_use_id": "toolu_x", "text": "...", "is_error": false,
    "persisted_file": "b0v5i8967.txt|null"},
   {"type": "image", "media_type": "image/png"}
 ]}
```
规则：
- 连续的 assistant 记录若 `message.id` 相同，合并为一条消息（流式拆分的 block 拼接）。
- 空 thinking 丢弃；image 不保留 base64 数据。
- `meta` = 记录的 `isMeta` 为真。
- `persisted_file`：从 tool_result 文本中匹配 `tool-results/<name>`，只保留文件名。

`page()` 输出时截断（不修改原数据）：
- `text` / `thinking` / `tool_result.text` 超过 `max_block_chars` 截断，并加 `"truncated": true`。
- `tool_use.input`：`json.dumps` 后超过上限时，替换为 `"input_preview": "<截断的 json 字符串>"` 并加 `"truncated": true`，删除 `input`。
- 无 `before`/`after`：返回最后 `limit` 条。`before=N`：返回 idx < N 的最后 `limit` 条。`after=N`：返回 idx > N 的前 `limit` 条。
- 返回 `{"messages": [...], "total": int, "has_more_before": bool}`。

`list_subagents` 返回 `[{"agent_id", "agent_type", "description", "model", "tool_use_id", "finished", "updated_at"}]`，按 updated_at 升序。

## 5. HTTP API（全部 GET，JSON，UTF-8）

| 路由 | 返回 |
|---|---|
| `/` | `static/index.html` |
| `/static/<file>` | 静态文件（只允许 static 目录内，文件名 `^[\w.-]+$`） |
| `/api/config` | `{"poll_interval_ms", "page_size"}` |
| `/api/agents` | `{"agents": [AgentInfo.to_dict()...]}` |
| `/api/agents/<sid>` | `AgentInfo.to_dict()` + `{"title", "cost_usd", "total"}` |
| `/api/agents/<sid>/messages?before=&after=&limit=` | `page()` 结果 |
| `/api/agents/<sid>/messages/<idx>/blocks/<bidx>` | 未截断的单个 block |
| `/api/agents/<sid>/subagents` | `{"subagents": [...]}` |
| `/api/agents/<sid>/subagents/<aid>/messages?before=&after=&limit=` | subagent transcript 的 `page()` |
| `/api/agents/<sid>/subagents/<aid>/messages/<idx>/blocks/<bidx>` | 同上，subagent |
| `/api/agents/<sid>/tool-results/<name>` | `text/plain` 文件内容 |

- `sid` 必须匹配 `^[0-9a-f-]{8,36}$` 且存在于 `list_agents()` 中（只暴露 `claude agents` 可见的 session），否则 404。
- `aid`、`name` 必须匹配 `^[\w.-]+$`，并校验解析后的路径确实位于预期目录内（防路径穿越）。
- `limit` 上限 200，默认 `page_size`。
- 错误统一返回 `{"error": "..."}` 及相应状态码（400/404/405/500）。
- 响应头加 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。

## 6. 前端

原生 JS（ES2020），无构建、无外部依赖。hash 路由：`#/agent/<sid>`、`#/agent/<sid>/sub/<aid>`。

- **左侧边栏**：搜索框（按 name/cwd/intent/结果摘要 过滤）；agent 按 cwd 分组，组内顺序沿用 API；每项显示名称、结果摘要（`result`||`detail` 首行，截断；无则回退 intent、short_id）、状态徽标（busy 绿色脉冲 / idle / done / unknown）、相对时间、置顶标记。每 `poll_interval_ms` 刷新列表。
- **主区域头部**：名称、cwd、intent、花费、状态。
- **消息流**：仿聊天 App。user 气泡靠右，assistant 靠左；`meta` 消息淡化折叠。
  - text：简易 Markdown 渲染（先整体 HTML 转义，再处理代码块、行内代码、标题、粗体、列表、链接仅允许 http/https）。
  - thinking：`<details>` 默认折叠。
  - tool_use：卡片默认折叠，摘要行显示工具名 + 关键参数（Bash 的 command、Read/Edit/Write 的 file_path、Grep 的 pattern、Agent 的 description）；展开显示 input 与配对的 tool_result（按 `tool_use_id` 配对，结果可能在下一页/后续轮询到达，需要后补）。若 `tool_use.id` 匹配某个 subagent 的 `tool_use_id`，显示“打开 subagent”链接。
  - `truncated` 的 block 显示“加载完整内容”按钮（调用 blocks 接口）；`persisted_file` 显示“查看完整输出”链接（tool-results 接口，新标签页打开）。
- **懒加载**：首次加载最后一页；滚动到顶部（IntersectionObserver）用 `before=<最小idx>` 加载更早消息，并保持滚动位置。
- **实时更新**：当前 agent 为 live 时，每 `poll_interval_ms` 以 `after=<最大idx>` 轮询；用户位于底部时自动滚到底。
- **主题**：CSS 变量，跟随 `prefers-color-scheme`；窄屏（<768px）侧边栏可收起。

## 7. v0.2：与 `claude agents` 对齐 + 进度时间线

### 7.1 Agent 列表规则（修正 v0.1 的 bug）
`claude agents` 只显示后台 job，因此：
- **Agent 列表 = `jobs/<jobId>/state.json`（含 `sessionId`）**。`sessions/*.json` 只用于补充运行时信息（`live`、`status`、`updatedAt`），不再单独产生 agent。
  - 被排除的情况：interactive 进程（含 `parkedJobId`，与 job 重复）、`spare: true` 的预热进程、没有 state.json 的 job 目录。
- **永不读取 `~/.claude/daemon/`**（`roster.json` 含认证 token）。
- `cwd` 优先级：state.json `cwd` > live session `cwd` > transcript `cwd`。
- `AgentInfo` 新增字段：`job_id`（jobs 下的目录名）、`tempo`、`result`（`state.output.result`，字符串或 null）、`tokens`（int 或 null）。state.json 的其他字段（如 `providerEnv`、`sessionPermissionRules`）**不得暴露**。

### 7.2 新 API
| 路由 | 返回 |
|---|---|
| `/api/agents/<sid>/timeline?limit=` | `{"events": [{"at", "state", "detail", "text"}]}`，读 `jobs/<job_id>/timeline.jsonl`，按时间升序取最后 `limit` 条（默认 200，上限 1000），只保留这 4 个字段，`text` 按 `max_block_chars` 截断并标 `truncated` |
| `/api/agents/<sid>/messages?around=N&limit=` | 以 idx=N 为中心的窗口：从 `max(0, N - limit//2)` 起取 `limit` 条 |

`page()` 返回值新增 `has_more_after: bool`（窗口最后一条之后是否还有消息），所有模式都返回。

### 7.3 前端
- 头部显示 `result`（无则 `detail`）摘要，可展开查看全文（Markdown）。
- “进度时间线”抽屉：头部“时间线 N”按钮（与“问题”并列，无事件时隐藏，仅主视图）打开右侧抽屉（移动端底部抽屉），每条事件显示状态徽标、相对时间、detail；`text` 可展开（Markdown），刷新时保留展开状态。与问题抽屉共用开关逻辑（`DRAWERS`，body 类 `<name>-open`），同时只开一个；Esc/点击遮罩关闭。首次进入视图取一次事件数，打开后内容随轮询刷新。
- 状态徽标覆盖：`working`/`busy`（绿色脉冲）、`idle`、`done`、`blocked`（橙）、`shell`、`failed`/`error`（红）、其他/unknown（灰）。

## 8. v0.3：全文搜索 + 花费统计

### 8.1 Transcript 扩展
`Transcript` 保存最后一次 `cost-state` 的归一化结果 `cost_state`：
```json
{"cost_usd": 1.23, "lines_added": 0, "lines_removed": 0, "api_duration_ms": 0,
 "models": {"claude-opus-5-5": {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
            "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.0}}}
```
（对应原字段 `totalCostUSD`、`totalLinesAdded`、`totalLinesRemoved`、`totalAPIDuration`、`modelUsage.*.{inputTokens,outputTokens,thinkingTokens,cacheReadInputTokens,cacheCreationInputTokens,costUSD}`；缺失按 0。）

### 8.2 新 API
| 路由 | 返回 |
|---|---|
| `/api/search?q=&limit=` | 在所有可见 agent 的**主 transcript** 中做大小写不敏感的子串搜索。搜索 text、thinking、tool_use input（json 字符串）、tool_result text。`q` 去首尾空白后长度 2–200，否则 400。`limit` 默认 50、上限 200。每条消息最多 1 个命中。agent 按列表顺序，消息按 idx 倒序（新的在前）。返回 `{"results": [{"session_id", "name", "idx", "role", "timestamp", "block_type", "snippet"}], "truncated": bool}`，snippet 为命中位置前后各约 80 字符 |
| `/api/stats` | `{"agents": [{"session_id", "name", "status", "messages", **cost_state}], "totals": {"cost_usd", "models": {...同上结构，按模型求和}}}`，agents 按 cost_usd 降序；无 transcript/无 cost-state 的 agent 计 0 |

### 8.3 前端
- **全局搜索**：侧边栏顶部的搜索框保留“按名称过滤”；新增“全文搜索”入口（回车或按钮），路由 `#/search?q=...`，结果按 agent 分组，显示角色、时间、snippet（命中词高亮，高亮必须基于转义后的文本构建 DOM，禁止 innerHTML 拼接未转义内容）。
- **跳转定位**：点击结果进入 `#/agent/<sid>?at=<idx>`，用 `around` 加载，滚动到该消息并短暂高亮。此时视图不在底部，需支持**向下懒加载**（底部 sentinel，`after=<最大idx>`，直到 `has_more_after` 为 false），之后才恢复 live 轮询/自动滚底逻辑。
- **统计页** `#/stats`：总花费、按模型汇总表（tokens、费用），按 agent 的表格（名称可点击跳转、状态、消息数、费用、各类 token、代码增删行），费用列附细条形占比。数字用千分位，费用保留 2 位小数。侧边栏底部放入口。

## 9. v0.4：渲染完善 + 问题导航 + UI 改版

### 9.1 消息来源（后端）
归一化消息新增 `origin` 字段（仅 user 消息有意义，assistant 为 null）：
- 记录带 `isCompactSummary` → `"compact-summary"`（上下文压缩摘要）。
- 否则取记录的 `origin.kind`（`origin` 可能是 dict 或字符串）：`human` / `task-notification` / `peer` / 其他原样返回。
- 没有 `origin` 时：`meta` 为真 → `"system"`；首个 text 以 `<` 或 `[` 开头 → `"system"`；否则 `"human"`。
- 前端：`origin == "human"` 的才渲染为用户气泡；其余 user 消息渲染为紧凑的系统行（默认折叠，显示类型标签 + 首行）。

### 9.2 问题目录 API
`/api/agents/<sid>/prompts` → `{"prompts": [{"idx", "timestamp", "text"}]}`，按 idx 升序，只包含 `origin == "human"` 的 user 消息。`text`：
- 若包含 `<command-name>X</command-name>` → 显示 `X` 加上 `<command-args>` 内容（如有）；
- 若以 `<pasted_content` 开头 → 去掉标签，取内容；
- 其余取首个 text block；空白折叠为单个空格，截断到 200 字符。

### 9.3 Markdown 渲染（`static/markdown.js`，ES module）
导出 `renderMarkdown(source: string): string`，安全约束不变（先整体转义，只输出自己生成的标签）。支持：
- 已有：标题、段落、粗体、行内代码、代码块、引用、分隔线、http/https 链接、有序/无序列表。
- 新增：**GFM 表格**（含对齐 `:---:`，单元格内支持行内语法，`\|` 转义）；斜体 `*x*` / `_x_`（不误伤 `snake_case`）；删除线 `~~x~~`；任务列表 `- [ ]` / `- [x]`（禁用的 checkbox）；嵌套列表（按缩进）；裸 URL 自动链接；代码块语言标签 + “复制”按钮（按钮用 `data-copy` 标记，由 app.js 事件委托处理）；数学公式 `$$...$$`（块）与 `$...$`（行内）原样以等宽样式显示（不引入 KaTeX）。
- 代码块与 Write 工具内容框：2–500 行时显示行号（每行一个 `span.ln-row`，CSS counter 生成，不进入 DOM 文本、不可选中，复制不含行号；单行或超 500 行不显示）。
- 样式放 `static/markdown.css`。测试：`tests/js/markdown.test.mjs`，用 `node --test tests/js/*.mjs` 运行（node ≥18，无依赖）。

### 9.4 UI 改版
- **问题导航**：头部“问题”按钮打开问题目录面板（桌面右侧抽屉，移动端底部抽屉），列出序号、时间、首行；点击跳到 `?at=<idx>`；当前所在问题高亮。消息流右下角浮动按钮：上一个问题 / 下一个问题 / 回到底部。快捷键 `[` / `]` 上一个/下一个问题（输入框聚焦时不触发）。
- **侧边栏分组**：组头更醒目（文件夹图标、路径最后两段 + 完整路径 tooltip、数量徽标、可折叠且记住状态于 localStorage），组间有明显分隔；组按组内最近更新时间排序（置顶 agent 所在组优先）。
- **搜索合一**：一个输入框——输入即过滤侧边栏；回车执行全文搜索。
- **界面中文化**：所有界面文字改为中文。
- **工具卡片增强**：Edit 的 old_string/new_string 以 diff 样式（删除红、新增绿）显示；Write 的 content 以代码块显示。
- **移动端**：使用 `100dvh`；触控目标 ≥ 40px；输入框字号 ≥16px（避免 iOS 缩放）；`env(safe-area-inset-*)`；表格与代码块在气泡内横向滚动；
- **精简头部**：主视图头部只保留 agent 名称、状态徽标和一个“文件夹图标 + 路径”复制按钮（移动端仅图标；完整路径仅在 tooltip 中，不直接显示；点击后按钮文字短暂变为“已复制/复制失败”；http 下仍可复制，失败时回退为手动复制框）。意图、标题、花费、记录数不再显示，原“详情/收起”区域已移除。
- **结果摘要置底**：`result`（缺省回退 `detail`）作为消息流最后一块内容显示（带“结果摘要”标签，随内容滚动，非吸附）；首行（截断 160 字）可展开为 Markdown 全文。仅在默认路径（未固定分支）、已加载到路径末尾（`has_more_after` 为假）的主视图显示；轮询刷新 detail 时原位更新，文本未变则不动；子 agent 视图不显示。

## 10. 回溯分支（rewind branches）

Claude Code 的 transcript 是树（每条记录有 `uuid` / `parentUuid`）。用户回溯重问时，新的人类消息与旧消息拥有相同的 `parentUuid`，旧分支仍留在文件里。默认只显示一条路径（根 → 叶），可切换到兄弟分支。

### 10.1 数据模型（`viewer/branches.py` + `Transcript`）
- **缓存内存上界**：`TranscriptCache` 是 LRU，估算占用 = 各 Transcript 已解析的文件字节数（`parsed_bytes`）之和；超过 `cache_max_mb` 时从最久未用者开始淘汰，最近使用的一项永不淘汰（即使它单独超预算）。实测常驻内存约为文件大小的 0.2–0.4 倍（20 MB 文件约 6.5 MB），按 1 倍估算留有余量。淘汰只丢弃缓存引用，正在处理请求的线程仍持有有效对象；下次访问重新解析，结果不变。搜索/统计这类批量扫描用 `get(path, keep=False)`：已缓存的直接用，未缓存的临时解析、用完即弃，因此不会挤掉热点会话。
- `Transcript._nodes`：`uuid -> Node(parent, msg, human)`，**所有**带 `uuid` 的记录都入表（attachment、system 等也在链上），在 `_ingest_line` 中增量维护；有新行时丢弃派生缓存 `BranchIndex`。
- `parent` = `parentUuid`；为空且 `logicalParentUuid` 是已知 uuid 时取后者（上下文压缩后路径不被截断）。
- 同一 `message.id` 被合并的多条 assistant 记录，每个 uuid 都映射到同一个消息 idx（下一条的 `parentUuid` 指向最后一片）。
- **分支点**仅指：同一 `parentUuid` 下 ≥2 条 `origin == "human"` 的 user 消息。并行工具调用产生的同父兄弟不是分支，路径 = 叶的祖先链，自然正确。
- 默认叶 = 文件中最后一条 user/assistant 消息的 uuid。叶的“最新叶”（`latest_leaf_under`）= 其子树内文件序最后的消息，一次反向扫描 O(n) 求得并缓存。
- 路径回溯带环/缺父保护。完全没有 `parentUuid` 的（线性/旧）文件：路径 = 全部消息，无 `branch` 字段，行为同旧版。
- 消息 `idx` 仍是全局文件序，路径上 idx 可能有空洞；`page()` 的 `before/after/around/has_more_*` 全部按 idx 比较（`around` 取 idx ≥ 目标的第一条）。

### 10.2 API 变更
- `GET /api/agents/<sid>/messages?leaf=<uuid>&before|after|around=&limit=`（subagent 的 messages 同样支持）：`leaf` 省略 = 默认叶；未知 uuid → 400。返回在原字段（`messages`、`total` = 路径消息数、`has_more_*`）之外加 `"leaf"`（实际使用的叶 uuid）。
- 路径上有 ≥2 个兄弟的人类消息带 `"branch": {"index": 1起, "count": n, "siblings": [{"uuid", "idx", "leaf"}...]}`（兄弟按文件序，含自己；`leaf` 为该兄弟子树的最新叶，切换时作为 `?leaf=`）。其他消息无 `branch` 键。
- `GET /api/agents/<sid>/prompts?leaf=<uuid>`：只含所选路径上的问题；返回加 `"leaf"`；未知 → 400。带分支的问题另有 `"branch": {"index", "count", "siblings": [{"uuid", "idx", "leaf", "text", "current"}]}`，`text` 为该兄弟自己的问题预览，`current` 标记当前路径上的那个；无分支的问题没有 `branch` 键。
- `GET /api/agents/<sid>`：`total` 为默认路径长度，新增 `"leaf"`（默认叶 uuid），前端轮询时据此发现回溯。
- `GET /api/search`：每条命中新增 `"leaf"`（命中消息子树的最新叶，用于打开含该命中的分支）与 `"on_default_path"`。搜索仍扫描文件的全部消息（含被回溯的分支）。
- 统计 / 花费接口不变；`/messages/<idx>/blocks/<bidx>` 仍用全局 idx。

### 10.3 前端
- 纯逻辑在 `static/branch.js`（hash 构造/解析、兄弟选择，带 `tests/js/branch.test.mjs`）。
- 路由 `#/agent/<sid>?at=<idx>&b=<leaf>`：`b` 为固定的分支叶，缺省 = 跟随最新路径；subagent 视图忽略 `at`/`b`。所有 messages / prompts 请求都带 `leaf=b`。
- 带 `branch` 的人类消息在时间旁显示 `‹ 2/3 ›`（两端禁用，不循环）。点击即改 hash 为 `at=<兄弟 idx>&b=<兄弟 leaf>`（leaf 等于默认叶时省略 `b`），由 `onRoute` 整体重载视图，因此前进/后退/分享都可用，旧视图的在途请求会被 `isActive` 丢弃。
- 轮询：未固定分支时，以 `after=<maxIdx-1>` 拉取；若响应中已不含当前最后一条消息，说明发生了回溯，丢弃 `at` 并重载最新路径。已固定的分支不会自动解除固定。
- 问题目录：带 `branch` 的问题在行下显示“分支 2/3”开关（`aria-expanded`），展开后列出所有版本的预览（当前版本高亮），点击其他版本与消息级切换器同样改 hash（`at=<兄弟 idx>&b=<兄弟 leaf>`，等于默认叶则省略 `b`，用 `siblingJump`）。展开状态保存在 view 内，`[` `]` 仍只在当前路径的问题间移动。
- 搜索：`on_default_path === false` 的命中带 `b=<hit.leaf>` 跳转，并显示“已回退的分支”标记。
