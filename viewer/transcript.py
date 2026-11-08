"""Incremental JSONL transcript parsing, normalization and paging. Read-only."""
from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path
from typing import Any

from .branches import BranchIndex, Node
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
        self._nodes: dict[str, Node] = {}  # every record with a uuid, file order
        self._index: BranchIndex | None = None  # derived cache, dropped on ingest
        self._offset = 0
        self._lock = threading.Lock()

    @property
    def parsed_bytes(self) -> int:
        """File bytes consumed so far; the cheap basis of the memory estimate."""
        return self._offset

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
        self._nodes, self._index = {}, None

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
            self._add_node(rec, self._add_message(rec))
        elif kind in ("ai-title", "custom-title"):
            title = rec.get("aiTitle" if kind == "ai-title" else "customTitle")
            if isinstance(title, str) and title:
                self.title = title
        elif kind == "cost-state":
            cost = rec.get("totalCostUSD")
            if isinstance(cost, (int, float)):
                self.cost_usd = float(cost)
            self.cost_state = normalize_cost_state(rec)
        if kind not in ("user", "assistant"):
            self._add_node(rec, None)

    def _add_node(self, rec: dict[str, Any], idx: int | None) -> None:
        """Register a record in the parent/child tree (idx = its message, if any)."""
        uuid = rec.get("uuid")
        if not isinstance(uuid, str) or not uuid or uuid in self._nodes:
            return
        parent = rec.get("parentUuid")
        if not isinstance(parent, str) or not parent:
            parent = rec.get("logicalParentUuid")  # compaction keeps the chain here
            if parent not in self._nodes:
                parent = None
        human = idx is not None and self._messages[idx]["origin"] == "human"
        self._nodes[uuid] = Node(parent, idx, human)
        self._index = None

    def _add_message(self, rec: dict[str, Any]) -> int | None:
        """Append (or merge) a message; return its global idx, None if dropped."""
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return None
        blocks = normalize_blocks(msg.get("content"))
        if not blocks:
            return None
        role = rec["type"]
        mid = msg.get("id") if role == "assistant" else None
        if mid and self._msg_ids and self._msg_ids[-1] == mid:
            last = self._messages[-1]
            self._messages[-1] = {**last, "blocks": last["blocks"] + blocks}
            return last["idx"]
        self._messages.append({
            "idx": len(self._messages), "uuid": rec.get("uuid"), "role": role,
            "timestamp": rec.get("timestamp"), "model": msg.get("model"),
            "meta": bool(rec.get("isMeta")), "origin": derive_origin(rec, role, blocks),
            "blocks": blocks,
        })
        self._msg_ids.append(mid)
        return len(self._messages) - 1

    def _branch_index(self) -> BranchIndex:
        if self._index is None:
            self._index = BranchIndex(self._nodes)
        return self._index

    def view(self, leaf: str | None = None) -> tuple[str | None, list[dict[str, Any]]]:
        """Messages on the path to `leaf` (default leaf if None) with `branch` info.

        Returns (leaf uuid used, messages). Raises KeyError for an unknown leaf.
        A transcript without any parent links is returned whole, in file order.
        """
        with self._lock:
            index = self._branch_index()
            if leaf is not None and leaf not in self._nodes:
                raise KeyError(leaf)
            used = leaf or index.default_leaf
            if not index.tree:
                return used, list(self._messages)
            out = []
            for idx in index.path(used):
                m = self._messages[idx]
                siblings = index.siblings_for(m["uuid"])
                if siblings:
                    pos = next(i for i, s in enumerate(siblings) if s["uuid"] == m["uuid"])
                    m = {**m, "branch": {"index": pos + 1, "count": len(siblings),
                                         "siblings": siblings}}
                out.append(m)
            return used, out

    def locate(self, uuid: str | None) -> tuple[str | None, bool]:
        """(latest leaf under uuid, whether the message is on the default path)."""
        with self._lock:
            index = self._branch_index()
            node = self._nodes.get(uuid) if uuid else None
            if not index.tree:
                return index.latest_leaf_under(uuid), True
            on_default = node is not None and node.msg in index.default_idxs()
            return index.latest_leaf_under(uuid), on_default


class TranscriptCache:
    """Thread-safe LRU cache of Transcript instances keyed by path.

    Memory is bounded by an estimate: parsed bytes of each transcript times
    PARSED_COST_FACTOR (measured retained memory is 0.2-0.4x the file size,
    so 1x is conservative). The most recently used entry is never evicted.
    Eviction only drops the cache reference; a request still holding the
    Transcript keeps a valid object, and the next access re-parses.
    """

    PARSED_COST_FACTOR = 1

    def __init__(self, max_bytes: int = 512 * 1024 * 1024) -> None:
        self._max_bytes = max_bytes
        self._items: dict[Path, Transcript] = {}  # insertion order = LRU order
        self._lock = threading.Lock()

    def get(self, path: Path, *, keep: bool = True) -> Transcript:
        """Return the refreshed transcript, marking it most recently used.

        keep=False is for bulk scans (search, stats): an uncached transcript
        is parsed but not stored, so a scan cannot evict the hot entries.
        """
        with self._lock:
            tr = self._items.pop(path, None)
            if tr is not None:
                self._items[path] = tr  # touch: move to the MRU end
            elif keep:
                tr = self._items[path] = Transcript(path)
            else:
                tr = Transcript(path)
        tr.refresh()
        if keep:
            self._evict()
        return tr

    def _evict(self) -> None:
        with self._lock:
            total = sum(t.parsed_bytes for t in self._items.values()) * self.PARSED_COST_FACTOR
            for key in list(self._items)[:-1]:  # never the MRU entry
                if total <= self._max_bytes:
                    break
                total -= self._items.pop(key).parsed_bytes * self.PARSED_COST_FACTOR


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
    """Pick the message window by idx (idx may have gaps on a branch path)."""
    if limit <= 0:
        return []
    if around is not None:
        pos = next((i for i, m in enumerate(messages) if m["idx"] >= around), len(messages))
        start = max(0, pos - limit // 2)
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
    has_before = bool(window) and window[0]["idx"] > messages[0]["idx"]
    has_after = bool(window) and window[-1]["idx"] < messages[-1]["idx"]
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
