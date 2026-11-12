"""Sanitize the pending AskUserQuestion of a blocked job. Whitelist only; never raises."""
from __future__ import annotations

from typing import Any

MAX_QUESTIONS = 4
MAX_OPTIONS = 8
HEADER_MAX = 60
QUESTION_MAX = 1000
LABEL_MAX = 200
DESCRIPTION_MAX = 500


def _clip(value: Any, limit: int) -> str | None:
    """Non-empty string clipped to `limit` characters (with an ellipsis), else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _option(raw: Any) -> dict[str, str] | None:
    if isinstance(raw, str):
        raw = {"label": raw}
    if not isinstance(raw, dict):
        return None
    label = _clip(raw.get("label"), LABEL_MAX)
    if label is None:
        return None
    out = {"label": label}
    description = _clip(raw.get("description"), DESCRIPTION_MAX)
    if description:
        out["description"] = description
    return out


def _question(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    text = _clip(raw.get("question"), QUESTION_MAX)
    if text is None:
        return None
    out: dict[str, Any] = {"question": text}
    header = _clip(raw.get("header"), HEADER_MAX)
    if header:
        out["header"] = header
    if raw.get("multiSelect") is True:
        out["multiSelect"] = True
    options = raw.get("options")
    options = options if isinstance(options, list) else []
    out["options"] = [o for o in map(_option, options[:MAX_OPTIONS]) if o]
    return out


def sanitize_questions(job: Any) -> list[dict[str, Any]]:
    """Return `job["block"]["questions"]` reduced to whitelisted, clipped fields; [] on any odd shape."""
    block = job.get("block") if isinstance(job, dict) else None
    questions = block.get("questions") if isinstance(block, dict) else None
    if not isinstance(questions, list):
        return []
    return [q for q in map(_question, questions[:MAX_QUESTIONS]) if q]
