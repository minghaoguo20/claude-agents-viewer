"""HTTP routes for the viewer (GET/HEAD only)."""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .config import PROJECT_ROOT, Config
from .prompts import list_prompts
from .search import search_agents
from .sources import SESSION_ID_RE, AgentInfo, find_agent, list_agents
from .stats import compute_stats
from .timeline import read_timeline
from .transcript import NAME_RE, TranscriptCache, list_subagents, page

log = logging.getLogger(__name__)

MAX_LIMIT = 200
MAX_TIMELINE_LIMIT = 1000
TIMELINE_DEFAULT = 200
SEARCH_DEFAULT_LIMIT = 50
QUERY_MIN, QUERY_MAX = 2, 200
AGENT_ROUTE = re.compile(r"^/api/agents/([^/]+)(/.*)?$")
BLOCK_RE = re.compile(r"^/messages/(\d+)/blocks/(\d+)$")
SUB_RE = re.compile(r"^/subagents/([^/]+)(/messages(?:/\d+/blocks/\d+)?)$")
TOOL_RESULT_RE = re.compile(r"^/tool-results/([^/]+)$")


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def inside(path: Path, base: Path) -> bool:
    """True if path resolves to a location inside base."""
    return path.resolve().is_relative_to(base.resolve())


def int_param(query: dict[str, list[str]], key: str) -> int | None:
    values = query.get(key)
    if not values or values[0] == "":
        return None
    try:
        return int(values[0])
    except ValueError:
        raise HttpError(400, f"invalid {key}") from None


def clamp_limit(value: int | None, default: int, maximum: int) -> int:
    """Default when absent/zero, otherwise clamp into [1, maximum]."""
    return max(1, min(value or default, maximum))


class Handler(BaseHTTPRequestHandler):
    server: "ViewerServer"
    server_version = "ClaudeAgentsViewer"
    protocol_version = "HTTP/1.1"  # keep-alive; every response must carry Content-Length
    disable_nagle_algorithm = True  # headers and body are separate writes; avoid 40ms delayed-ACK stalls
    timeout = 30  # close idle keep-alive sockets (one thread per connection)

    # ---- plumbing ----
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        log.debug("%s - %s", self.address_string(), format % args)

    def send_body(self, status: int, body: bytes, ctype: str,
                  extra: dict[str, str] | None = None, cache: str = "no-store") -> None:
        self.send_response(status)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, data: Any, status: int = 200,
                  extra: dict[str, str] | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_body(status, body, "application/json; charset=utf-8", extra)

    def handle_any(self) -> None:
        try:
            self.dispatch(urlsplit(self.path))
        except HttpError as err:
            self.send_json({"error": err.message}, err.status)
        except Exception:  # noqa: BLE001 - last-resort guard
            log.exception("unhandled error for %s", self.path)
            self.send_json({"error": "internal error"}, 500)

    def do_GET(self) -> None:  # noqa: N802
        self.handle_any()

    do_HEAD = do_GET

    def reject(self) -> None:
        self.send_json({"error": "method not allowed"}, 405, {"Allow": "GET, HEAD"})

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = reject

    # ---- routing ----
    def dispatch(self, url: Any) -> None:
        path, query = url.path, parse_qs(url.query)
        cfg = self.server.config
        if path == "/":
            return self.serve_static("index.html")
        if path.startswith("/static/"):
            return self.serve_static(path[len("/static/"):])
        if path == "/api/config":
            return self.send_json({"poll_interval_ms": cfg.poll_interval_ms,
                                   "page_size": cfg.page_size})
        if path == "/api/agents":
            return self.send_json({"agents": [a.to_dict() for a in list_agents(cfg.claude_home)]})
        if path == "/api/search":
            return self.search(query)
        if path == "/api/stats":
            return self.send_json(compute_stats(list_agents(cfg.claude_home), self.server.cache))
        match = AGENT_ROUTE.match(path)
        if match:
            return self.agent_route(match.group(1), match.group(2) or "", query)
        raise HttpError(404, "not found")

    def serve_static(self, name: str) -> None:
        base = self.server.static_dir
        if not NAME_RE.match(name):
            raise HttpError(404, "not found")
        target = base / name
        if not inside(target, base) or not target.is_file():
            raise HttpError(404, "not found")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        stat = target.stat()
        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            # 304 carries no body; Content-Length: 0 keeps the connection reusable.
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", "0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            return
        self.send_body(200, target.read_bytes(), ctype, {"ETag": etag}, cache="no-cache")

    def agent_route(self, sid: str, rest: str, query: dict[str, list[str]]) -> None:
        agent = self.require_agent(sid)
        if rest == "":
            return self.agent_detail(agent)
        if rest == "/messages":
            return self.messages(agent.transcript, query)
        if rest == "/prompts":
            return self.prompts(agent, query)
        if rest == "/timeline":
            return self.timeline(agent, query)
        if rest == "/tasks":
            if agent.transcript is None:
                raise HttpError(404, "no transcript")
            return self.send_json({"tasks": self.server.cache.get(agent.transcript).tasks()})
        if rest == "/subagents":
            return self.send_json({"subagents": list_subagents(self.session_dir(agent))})
        block = BLOCK_RE.match(rest)
        if block:
            return self.block(agent.transcript, int(block.group(1)), int(block.group(2)))
        sub = SUB_RE.match(rest)
        if sub:
            return self.subagent_route(agent, sub.group(1), sub.group(2), query)
        tool = TOOL_RESULT_RE.match(rest)
        if tool:
            return self.tool_result(agent, tool.group(1))
        raise HttpError(404, "not found")

    # ---- helpers ----
    def require_agent(self, sid: str) -> AgentInfo:
        if not SESSION_ID_RE.match(sid):
            raise HttpError(404, "unknown session")
        agent = find_agent(self.server.config.claude_home, sid)
        if agent is None:
            raise HttpError(404, "unknown session")
        return agent

    def session_dir(self, agent: AgentInfo) -> Path:
        if agent.transcript is None:
            raise HttpError(404, "no transcript")
        directory = agent.transcript.parent / agent.session_id
        if not inside(directory, agent.transcript.parent):
            raise HttpError(404, "not found")
        return directory

    def agent_detail(self, agent: AgentInfo) -> None:
        data = agent.to_dict()
        title = cost = None
        total = 0
        if agent.transcript is not None:
            tr = self.server.cache.get(agent.transcript)
            leaf, path = tr.view()
            title, cost, total = tr.title, tr.cost_usd, len(path)
        self.send_json({**data, "title": title, "cost_usd": cost, "total": total,
                        "leaf": leaf})

    def branch_view(self, transcript: Path, query: dict[str, list[str]]
                    ) -> tuple[str | None, list[dict[str, Any]]]:
        """(leaf, path messages) for the optional ?leaf= parameter; 400 if unknown."""
        values = query.get("leaf")
        try:
            leaf = values[0] if values and values[0] else None
            return self.server.cache.get(transcript).view(leaf)
        except KeyError:
            raise HttpError(400, "unknown leaf") from None

    def messages(self, transcript: Path | None, query: dict[str, list[str]]) -> None:
        if transcript is None:
            raise HttpError(404, "no transcript")
        cfg = self.server.config
        limit = clamp_limit(int_param(query, "limit"), cfg.page_size, MAX_LIMIT)
        before, after, around = (int_param(query, k) for k in ("before", "after", "around"))
        if sum(v is not None for v in (before, after, around)) > 1:
            raise HttpError(400, "before, after and around are mutually exclusive")
        leaf, msgs = self.branch_view(transcript, query)
        self.send_json({**page(msgs, before=before, after=after, around=around, limit=limit,
                               max_block_chars=cfg.max_block_chars), "leaf": leaf})

    def prompts(self, agent: AgentInfo, query: dict[str, list[str]]) -> None:
        if agent.transcript is None:
            raise HttpError(404, "no transcript")
        leaf, msgs = self.branch_view(agent.transcript, query)
        everything = self.server.cache.get(agent.transcript).messages
        self.send_json({"prompts": list_prompts(msgs, everything), "leaf": leaf})

    def timeline(self, agent: AgentInfo, query: dict[str, list[str]]) -> None:
        if not NAME_RE.match(agent.job_id):
            raise HttpError(404, "no timeline")
        base = self.server.config.claude_home / "jobs"
        target = base / agent.job_id / "timeline.jsonl"
        if not inside(target, base):
            raise HttpError(404, "no timeline")
        cfg = self.server.config
        limit = clamp_limit(int_param(query, "limit"), TIMELINE_DEFAULT, MAX_TIMELINE_LIMIT)
        self.send_json({"events": read_timeline(target, limit, cfg.max_block_chars)})

    def search(self, query: dict[str, list[str]]) -> None:
        q = (query.get("q") or [""])[0].strip()
        if not QUERY_MIN <= len(q) <= QUERY_MAX:
            raise HttpError(400, f"q must be {QUERY_MIN}-{QUERY_MAX} characters")
        limit = clamp_limit(int_param(query, "limit"), SEARCH_DEFAULT_LIMIT, MAX_LIMIT)
        agents = list_agents(self.server.config.claude_home)
        self.send_json(search_agents(agents, self.server.cache, q, limit))

    def block(self, transcript: Path | None, idx: int, bidx: int) -> None:
        if transcript is None:
            raise HttpError(404, "no transcript")
        msgs = self.server.cache.get(transcript).messages
        if idx >= len(msgs) or bidx >= len(msgs[idx]["blocks"]):
            raise HttpError(404, "no such block")
        self.send_json(msgs[idx]["blocks"][bidx])

    def subagent_route(self, agent: AgentInfo, aid: str, rest: str,
                       query: dict[str, list[str]]) -> None:
        if not NAME_RE.match(aid):
            raise HttpError(400, "invalid agent id")
        base = self.session_dir(agent) / "subagents"
        target = base / f"agent-{aid}.jsonl"
        if not inside(target, base) or not target.is_file():
            raise HttpError(404, "unknown subagent")
        if rest == "/messages":
            return self.messages(target, query)
        block = BLOCK_RE.match(rest)
        if block:
            return self.block(target, int(block.group(1)), int(block.group(2)))
        raise HttpError(404, "not found")

    def tool_result(self, agent: AgentInfo, name: str) -> None:
        if not NAME_RE.match(name):
            raise HttpError(400, "invalid name")
        base = self.session_dir(agent) / "tool-results"
        target = base / name
        if not inside(target, base) or not target.is_file():
            raise HttpError(404, "unknown tool result")
        self.send_body(200, target.read_bytes(), "text/plain; charset=utf-8")


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: Config, static_dir: Path) -> None:
        if ":" in config.host:
            self.address_family = socket.AF_INET6
        super().__init__((config.host, config.port), Handler)
        self.config = config
        self.static_dir = static_dir
        self.cache = TranscriptCache(config.cache_max_mb * 1024 * 1024)


def make_server(config: Config, static_dir: Path | None = None) -> ThreadingHTTPServer:
    """Create (but do not start) the server; port 0 picks a free port."""
    return ViewerServer(config, static_dir or PROJECT_ROOT / "static")
