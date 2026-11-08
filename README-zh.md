[English](README.md) | 中文

# Claude Agents Viewer

一个本地网页，用来浏览 Claude Code 后台 agent（`claude agents` 中可见的那些）的 session 对话历史。严格只读、零依赖：仅使用 Python 标准库和原生 JS。

## 功能

- agent 列表与 `claude agents` 一致，按工作目录分组，显示实时状态
- 聊天式对话视图：Markdown（含表格）、工具调用与结果配对、Edit diff、子 agent 对话
- 向上滚动懒加载更早消息，运行中的 agent 实时刷新
- 回溯分支：用 ‹ 2/3 › 控件在同一问题的不同分支间切换
- 问题导航，用 `[` / `]` 在你的提问之间跳转
- 任务进度时间线（working、done 等状态变化与结果摘要）
- 跨所有 agent 的全文搜索，点击结果定位到对应消息
- 按 agent、按模型统计花费与 token 用量
- 支持移动端浏览

## 依赖

- Python >= 3.10（无需安装任何包）
- Node >= 18，仅用于运行 JS 测试

## 快速开始

```bash
python3 -m viewer                       # 前台运行，Ctrl-C 停止
python3 -m viewer --port 8765          # 覆盖 config.ini 中的端口
python3 -m viewer --config <路径>       # 或设置环境变量 CAV_CONFIG=<路径>
```

在 tmux 中常驻运行：

```bash
tmux new -d -s cav 'cd <项目目录> && python3 -m viewer --port 8765'   # 启动
tmux attach -t cav                                                     # 查看日志（Ctrl-b d 退出查看）
tmux kill-session -t cav                                               # 关闭
```

服务只监听回环地址。从本地电脑访问时，在本地执行下面的命令（`-L` 后的端口须与服务端口一致）：

```bash
ssh -N -L 8765:127.0.0.1:8765 <服务器>
```

然后浏览器打开 http://localhost:8765 。

<details>
<summary>常见问题</summary>

**"Address already in use"**：端口被占用（共享服务器上常见）。查出占用者，换一个空闲端口（修改 `config.ini` 的 `[server] port` 或加 `--port`），SSH 转发的端口同步修改。

```bash
ss -ltnp | grep :<端口>
```

</details>

## 配置

默认配置在 `config.ini.example`。如需本地修改，复制为 `config.ini`（已被 git 忽略）：

```bash
cp config.ini.example config.ini
```

查找顺序：`--config` / `CAV_CONFIG` → `config.ini` → `config.ini.example`。

| 字段 | 说明 | 默认值 |
|---|---|---|
| `[server] host` | 监听地址，只允许回环地址（`127.0.0.1`、`localhost`、`::1`） | `127.0.0.1` |
| `[server] port` | 监听端口（可被 `--port` 覆盖） | `8765` |
| `[claude] home` | Claude 数据目录 | `~/.claude` |
| `[viewer] page_size` | 每页消息数 | `50` |
| `[viewer] max_block_chars` | 单个内容块最多返回的字符数，超出部分点击展开 | `4000` |
| `[viewer] poll_interval_ms` | 前端轮询间隔（毫秒） | `3000` |
| `[viewer] cache_max_mb` | 已解析会话的内存预算（MB），超出按 LRU 淘汰 | `512` |

<details>
<summary>数据来源</summary>

只读取 Claude 数据目录下的以下内容：

- `jobs/*/state.json`：agent 列表（名称、状态、意图、结果）
- `jobs/*/timeline.jsonl`：进度时间线
- `jobs/pins.json`：置顶列表
- `sessions/*.json`：仅用于补充运行状态（是否存活、idle/busy）
- `projects/*/<sid>.jsonl`：对话记录，以及同名子目录中的 `subagents/`、`tool-results/`、`custom-title.json`

与 `claude agents` 一致，只展示后台 job（有 `state.json` 且含 `sessionId`）。

</details>

## 安全说明

- 严格只读：不对 Claude 数据目录做任何写操作（`tests/test_readonly.py` 强制检查）
- 仅监听回环地址，启动时强制校验
- 绝不读取 `sessions/*.key` 和 `daemon/`（其中含认证 token）
- 路径穿越防护：文件名经白名单校验，且解析后必须位于预期目录内
- 对话内容全部转义，不执行其中的 HTML/脚本
- 只允许 GET/HEAD

## 开发

```bash
python3 -m unittest discover -s tests -t . -v   # 后端
node --test tests/js/*.mjs                       # Markdown 渲染器（Node >= 18）
```

```
config.ini.example  默认配置
viewer/         Python 后端（仅标准库）
static/         前端（原生 JS/CSS）
tests/          单元测试（Python + JS）
docs/design.md  设计文档
```

设计规范见 [docs/design.md](docs/design.md)。

## 许可证

[MIT](LICENSE)
