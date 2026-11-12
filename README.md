English | [中文](README-zh.md)

# Claude Agents Viewer (page display name: CC-Viewer)

A local web page for browsing the session transcripts of Claude Code background agents (the ones listed by `claude agents`). It is strictly read-only and has zero dependencies: just the Python standard library and vanilla JS.

## Features

- Agent list matching `claude agents`, grouped by working directory, with live status
- Chat-style transcript: Markdown (including tables), tool calls paired with their results, Edit diffs, subagent conversations
- Lazy loading of older messages and live updates for running agents
- Rewind branches: switch between alternatives with a ‹ 2/3 › control
- Question navigator, with `[` / `]` to jump between your questions
- Blocked agents are highlighted (sorted first, tab title count, optional browser notification) and show their pending questions read-only
- Job progress timeline (working, done, ... plus the result summary)
- Background tasks block (Bash `run_in_background` / Monitor) below the result summary
- Full-text search across all agents, with jump-to-message
- Cost and token statistics per agent and per model
- Mobile-friendly

## Requirements

- Python >= 3.10 (nothing to install)
- Node >= 18, only for running the JS tests

## Quick start

```bash
python3 -m viewer                       # foreground, Ctrl-C to stop
python3 -m viewer --port 8765          # override the port from config.ini
python3 -m viewer --config <path>       # or set CAV_CONFIG=<path>
```

Run it in tmux:

```bash
tmux new -d -s cav 'cd <project-dir> && python3 -m viewer --port 8765'   # start
tmux attach -t cav                                                        # view logs (Ctrl-b d to detach)
tmux kill-session -t cav                                                  # stop
```

The server only listens on loopback. To reach it from your own machine, run this locally (the ports after `-L` must match the server port):

```bash
ssh -N -L 8765:127.0.0.1:8765 <server>
```

Then open http://localhost:8765.

<details>
<summary>Troubleshooting</summary>

**"Address already in use"**: the port is taken (common on shared servers). Find the owner and pick another port, via `[server] port` in `config.ini` or `--port`, and adjust the SSH tunnel accordingly.

```bash
ss -ltnp | grep :<port>
```

</details>

## Configuration

Defaults ship in `config.ini.example`. For local changes, copy it to `config.ini` (git-ignored):

```bash
cp config.ini.example config.ini
```

Lookup order: `--config` / `CAV_CONFIG` → `config.ini` → `config.ini.example`.

| Key | Description | Default |
|---|---|---|
| `[server] host` | Bind address; loopback only (`127.0.0.1`, `localhost`, `::1`) | `127.0.0.1` |
| `[server] port` | Listen port (`--port` overrides it) | `8765` |
| `[claude] home` | Claude data directory | `~/.claude` |
| `[viewer] page_size` | Messages per page | `50` |
| `[viewer] max_block_chars` | Max characters per content block before "expand" | `4000` |
| `[viewer] poll_interval_ms` | Frontend polling interval in ms | `3000` |
| `[viewer] cache_max_mb` | Memory budget in MB for parsed transcripts (LRU eviction) | `512` |

<details>
<summary>Data sources</summary>

Only these paths under the Claude data directory are read:

- `jobs/*/state.json`: agent list (name, status, intent, result)
- `jobs/*/timeline.jsonl`: progress timeline
- `jobs/pins.json`: pinned agents
- `sessions/*.json`: liveness and idle/busy status only
- `projects/*/<sid>.jsonl`: transcripts, plus the sibling `subagents/`, `tool-results/` and `custom-title.json`

Only background jobs (with a `state.json` containing `sessionId`) are listed, as in `claude agents`.

</details>

## Security

- Strictly read-only; nothing under the Claude data directory is ever written (enforced by `tests/test_readonly.py`)
- Loopback-only bind, enforced at startup
- Never reads `sessions/*.key` or `daemon/` (which holds auth tokens)
- Path-traversal protection: file names are whitelisted and resolved paths must stay in the expected directory
- All transcript content is escaped; no HTML or scripts from it are executed
- Only GET/HEAD are accepted

## Development

```bash
python3 -m unittest discover -s tests -t . -v   # backend
node --test tests/js/*.mjs                       # Markdown renderer (Node >= 18)
```

```
config.ini.example  default configuration
viewer/         Python backend (stdlib only)
static/         frontend (vanilla JS/CSS)
tests/          unit tests (Python + JS)
docs/design.md  design spec (in Chinese)
```

See [docs/design.md](docs/design.md) for the design spec (written in Chinese).

## License

[MIT](LICENSE)
