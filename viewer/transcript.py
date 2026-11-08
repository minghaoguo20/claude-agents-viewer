"""Incremental JSONL transcript parsing, normalization and paging. Read-only."""
from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path
from typing import Any

from .files import read_json

PERSISTED_RE = re.compile(r"tool-results/([\w.-]+)")
NAME_RE = re.compile(r"^[\w.-]+$")


def _tool_result_text(content: Any) -> str:
    """Flatten tool_result content (string or block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def normalize_block(block: Any) -> dict[str, Any] | None:
    """Convert one raw content block; return None if it should be dropped."""
    if isinstance(block, str):
        return {"type": "text", "text": block} if block else None
    if not isinstance(block, dict):
        return None
    kind = block.get("type")
    if kind == "text":
        text = block.get("text") or ""
        return {"type": "text", "text": text} if text else None
    if kind == "thinking":
        text = block.get("thinking") or ""
        return {"type": "thinking", "text": text} if text.strip() else None
    if kind == "tool_use":
        return {"type": "tool_use", "id": block.get("id"), "name": block.get("name"),
                "input": block.get("input", {})}
    if kind == "tool_result":
        text = _tool_result_text(block.get("content"))
        found = PERSISTED_RE.search(text)
        return {"type": "tool_result", "tool_use_id": block.get("tool_use_id"),
                "text": text, "is_error": bool(block.get("is_error")),
                "persisted_file": found.group(1) if found else None}
    if kind == "image":
        source = block.get("source")
        media = source.get("media_type") if isinstance(source, dict) else None
        return {"type": "image", "media_type": media}
    return None


def normalize_blocks(content: Any) -> list[dict[str, Any]]:
    items = content if isinstance(content, list) else [content]
    return [b for b in (normalize_block(i) for i in items) if b is not None]


MODEL_FIELDS = {
    "input_tokens": "inputTokens", "output_tokens": "outputTokens",
    "thinking_tokens": "thinkingTokens", "cache_read_tokens": "cacheReadInputTokens",
    "cache_creation_tokens": "cacheCreationInputTokens", "cost_usd": "costUSD",
}


def _num(value: Any) -> float | int:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    return value if ok else 0


def normalize_cost_state(rec: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw cost-state record (missing fields count as 0)."""
    usage = rec.get("modelUsage")
    models: dict[str, dict[str, Any]] = {}
    for model, raw in (usage.items() if isinstance(usage, dict) else []):
        if isinstance(raw, dict):
            models[str(model)] = {k: _num(raw.get(src)) for k, src in MODEL_FIELDS.items()}
            models[str(model)]["cost_usd"] = float(models[str(model)]["cost_usd"])
    return {"cost_usd": float(_num(rec.get("totalCostUSD"))),
            "lines_added": _num(rec.get("totalLinesAdded")),
            "lines_removed": _num(rec.get("totalLinesRemoved")),
            "api_duration_ms": _num(rec.get("totalAPIDuration")), "models": models}


def derive_origin(rec: dict[str, Any], role: str, blocks: list[dict[str, Any]]) -> str | None:
    """Message origin: human / task-notification / peer / compact-summary / system (None for assistant)."""
    if role != "user":
        return None
    if rec.get("isCompactSummary"):
        return "compact-summary"
    raw = rec.get("origin")
    kind = raw.get("kind") if isinstance(raw, dict) else raw
    if isinstance(kind, str) and kind:
        return kind
    if rec.get("isMeta"):
        return "system"
    # No text block (e.g. tool_result only) is not something a human typed.
    first = next((b["text"] for b in blocks if b["type"] == "text"), None)
    if first is None or first.lstrip().startswith(("<", "[")):
        return "system"
    return "human"


class Transcript:
    """Incrementally parsed JSONL transcript (append-only file)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.title: str | None = None
        self.cost_usd: float | None = None
        self.cost_state: dict[str, Any] | None = None
        self._messages: list[dict[str, Any]] = []
        self._msg_ids: list[str | None] = []  # message.id per normalized message
        self._offset = 0
        self._lock = threading.Lock()

    @property
    def messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._messages)

    def refresh(self) -> None:
        """Parse newly appended complete lines only."""
        with self._lock:
            for line in self._read_new_lines():
                self._ingest_line(line)

    def _reset(self) -> None:
        self.title = self.cost_usd = self.cost_state = None
        self._messages, self._msg_ids, self._offset = [], [], 0

    def _read_new_lines(self) -> list[bytes]:
        try:
            if self.path.stat().st_size < self._offset:
                self._reset()  # file was replaced or truncated
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                data = fh.read()
        except OSError:
            return []
        end = data.rfind(b"\n")
        if end < 0:
            return []
        self._offset += end + 1
        return data[:end].split(b"\n")

    def _ingest_line(self, line: bytes) -> None:
        try:
            rec = json.loads(line)
        except ValueError:
            return
        if not isinstance(rec, dict):
            return
        kind = rec.get("type")
        if kind in ("user", "assistant"):
            self._add_message(rec)
        elif kind in ("ai-title", "custom-title"):
            title = rec.get("aiTitle" if kind == "ai-title" else "customTitle")
            if isinstance(title, str) and title:
                self.title = title
        elif kind == "cost-state":
            cost = rec.get("totalCostUSD")
            if isinstance(cost, (int, float)):
                self.cost_usd = float(cost)
            self.cost_state = normalize_cost_state(rec)

    def _add_message(self, rec: dict[str, Any]) -> None:
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return
        blocks = normalize_blocks(msg.get("content"))
        if not blocks:
            return
        role = rec["type"]
        mid = msg.get("id") if role == "assistant" else None
        if mid and self._msg_ids and self._msg_ids[-1] == mid:
            last = self._messages[-1]
            self._messages[-1] = {**last, "blocks": last["blocks"] + blocks}
            return
        self._messages.append({
            "idx": len(self._messages), "uuid": rec.get("uuid"), "role": role,
            "timestamp": rec.get("timestamp"), "model": msg.get("model"),
            "meta": bool(rec.get("isMeta")), "origin": derive_origin(rec, role, blocks),
            "blocks": blocks,
        })
        self._msg_ids.append(mid)


class TranscriptCache:
    """Thread-safe cache of Transcript instances keyed by path."""

    def __init__(self) -> None:
        self._items: dict[Path, Transcript] = {}
        self._lock = threading.Lock()

    def get(self, path: Path) -> Transcript:
        with self._lock:
            tr = self._items.get(path)
            if tr is None:
                tr = self._items[path] = Transcript(path)
        tr.refresh()
        return tr


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    return (text[:limit], True) if len(text) > limit else (text, False)


def truncate_block(block: dict[str, Any], limit: int) -> dict[str, Any]:
    """Return a truncated copy of a block (the original is untouched)."""
    out = copy.copy(block)
    if block["type"] in ("text", "thinking", "tool_result"):
        out["text"], cut = truncate_text(block["text"], limit)
        if cut:
            out["truncated"] = True
    elif block["type"] == "tool_use":
        dumped = json.dumps(block.get("input"), ensure_ascii=False)
        if len(dumped) > limit:
            del out["input"]
            out["input_preview"] = dumped[:limit]
            out["truncated"] = True
    return out


def select_window(messages: list[dict[str, Any]], before: int | None,
                  after: int | None, around: int | None, limit: int) -> list[dict[str, Any]]:
    """Pick the message window; idx equals the list position."""
    if limit <= 0:
        return []
    if around is not None:
        start = max(0, around - limit // 2)
        return messages[start:start + limit]
    if after is not None:
        return [m for m in messages if m["idx"] > after][:limit]
    pool = messages if before is None else [m for m in messages if m["idx"] < before]
    return pool[-limit:]


def page(messages: list[dict[str, Any]], *, before: int | None = None,
         after: int | None = None, limit: int, max_block_chars: int,
         around: int | None = None) -> dict[str, Any]:
    """Select a window of messages and truncate large blocks."""
    if sum(v is not None for v in (before, after, around)) > 1:
        raise ValueError("before, after and around are mutually exclusive")
    window = select_window(messages, before, after, around, limit)
    has_before = bool(window) and window[0]["idx"] > 0
    has_after = bool(window) and window[-1]["idx"] < len(messages) - 1
    out = [{**m, "blocks": [truncate_block(b, max_block_chars) for b in m["blocks"]]}
           for m in window]
    return {"messages": out, "total": len(messages), "has_more_before": has_before,
            "has_more_after": has_after}


def list_subagents(session_dir: Path) -> list[dict[str, Any]]:
    """List subagent transcripts under <session_dir>/subagents, oldest first."""
    result: list[dict[str, Any]] = []
    for path in (session_dir / "subagents").glob("agent-*.jsonl"):
        agent_id = path.stem[len("agent-"):]
        meta = read_json(path.with_name(f"agent-{agent_id}.meta.json"))
        if not isinstance(meta, dict):
            meta = {}
        try:
            updated = int(path.stat().st_mtime * 1000)
        except OSError:
            continue
        result.append({
            "agent_id": agent_id, "agent_type": meta.get("agentType"),
            "description": meta.get("description"), "model": meta.get("model"),
            "tool_use_id": meta.get("toolUseId"), "finished": meta.get("finished"),
            "updated_at": updated,
        })
    result.sort(key=lambda s: s["updated_at"])
    return result
