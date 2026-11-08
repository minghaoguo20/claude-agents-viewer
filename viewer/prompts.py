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


def list_prompts(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return {idx, timestamp, text} for every human user message, by idx."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") != "user" or m.get("origin") != "human":
            continue
        first = next((b["text"] for b in m["blocks"] if b["type"] == "text"), "")
        out.append({"idx": m["idx"], "timestamp": m["timestamp"],
                    "text": prompt_preview(first)})
    return out
