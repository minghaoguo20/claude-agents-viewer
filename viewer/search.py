"""Full-text search over normalized transcript messages. Read-only, pure helpers."""
from __future__ import annotations

import json
import re
from typing import Any

from .sources import AgentInfo
from .transcript import TranscriptCache

SNIPPET_CONTEXT = 80
_WS_RE = re.compile(r"\s+")


def block_text(block: dict[str, Any]) -> str:
    """Searchable text of a block ('' if it has none)."""
    kind = block.get("type")
    if kind in ("text", "thinking", "tool_result"):
        return block.get("text") or ""
    if kind == "tool_use":
        return json.dumps(block.get("input"), ensure_ascii=False)
    return ""


def make_snippet(text: str, start: int, length: int) -> str:
    """Cut ~80 chars around a match and collapse whitespace runs to one space."""
    lo = max(0, start - SNIPPET_CONTEXT)
    hi = start + length + SNIPPET_CONTEXT
    return _WS_RE.sub(" ", text[lo:hi]).strip()


def search_message(msg: dict[str, Any], needle: str) -> tuple[str, str] | None:
    """Return (block_type, snippet) of the first matching block, if any."""
    for block in msg["blocks"]:
        text = block_text(block)
        if len(text) < len(needle):
            continue
        pos = text.lower().find(needle)
        if pos >= 0:
            return block["type"], make_snippet(text, pos, len(needle))
    return None


def search_agents(agents: list[AgentInfo], cache: TranscriptCache, query: str,
                  limit: int) -> dict[str, Any]:
    """Search main transcripts; newest messages first per agent. One hit per message."""
    needle = query.lower()
    results: list[dict[str, Any]] = []
    for agent in agents:
        if agent.transcript is None:
            continue
        tr = cache.get(agent.transcript, keep=False)
        for msg in reversed(tr.messages):
            hit = search_message(msg, needle)
            if hit is None:
                continue
            if len(results) >= limit:
                return {"results": results, "truncated": True}
            leaf, on_default = tr.locate(msg["uuid"])
            results.append({"session_id": agent.session_id, "name": agent.name,
                            "idx": msg["idx"], "role": msg["role"],
                            "timestamp": msg["timestamp"], "block_type": hit[0],
                            "snippet": hit[1], "leaf": leaf,
                            "on_default_path": on_default})
    return {"results": results, "truncated": False}
