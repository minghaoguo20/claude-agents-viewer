"""Prompt-preview extraction for the question index. Pure functions, read-only."""
from __future__ import annotations

import re
from typing import Any

PREVIEW_MAX = 200
COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
PASTED_OPEN_RE = re.compile(r"^\s*<pasted_content\b[^>]*>")
PASTED_CLOSE_RE = re.compile(r"</pasted_content\b[^>]*>")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def prompt_preview(text: str, limit: int = PREVIEW_MAX) -> str:
    """Build a one-line preview of a human prompt (wrappers stripped, capped)."""
    name = COMMAND_NAME_RE.search(text)
    if name:
        args = COMMAND_ARGS_RE.search(text)
        text = f"{name.group(1).strip()} {args.group(1) if args else ''}"
    elif PASTED_OPEN_RE.match(text):
        text = PASTED_CLOSE_RE.sub(" ", PASTED_OPEN_RE.sub("", text, count=1))
    return _collapse(text)[:limit]


def _first_text(m: dict[str, Any]) -> str:
    return next((b["text"] for b in m["blocks"] if b["type"] == "text"), "")


def list_prompts(messages: list[dict[str, Any]],
                 all_messages: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return {idx, timestamp, text[, branch]} for every human user message, by idx.

    A message with `branch` gets a `branch` object whose siblings carry their own preview
    `text` and `current` (True for the one on the displayed path). Sibling messages are
    looked up by global idx in `all_messages` (default: `messages`).
    """
    pool = messages if all_messages is None else all_messages
    by_idx = {m["idx"]: m for m in pool} if all_messages is None else None
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") != "user" or m.get("origin") != "human":
            continue
        item: dict[str, Any] = {"idx": m["idx"], "timestamp": m["timestamp"],
                                "text": prompt_preview(_first_text(m))}
        branch = m.get("branch")
        if branch:
            siblings = []
            for s in branch["siblings"]:
                if by_idx is not None:
                    sm = by_idx.get(s["idx"])
                else:
                    # idx is the position in the full transcript
                    sm = pool[s["idx"]] if 0 <= s["idx"] < len(pool) else None
                text = prompt_preview(_first_text(sm)) if sm else ""
                siblings.append({**s, "text": text, "current": s["idx"] == m["idx"]})
            item["branch"] = {"index": branch["index"], "count": branch["count"],
                              "siblings": siblings}
        out.append(item)
    return out
