"""Read a job's progress timeline (jobs/<jobId>/timeline.jsonl). Read-only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .transcript import truncate_text

FIELDS = ("at", "state", "detail", "text")


def parse_event(line: bytes, max_chars: int) -> dict[str, Any] | None:
    """Parse one line into an event with only the whitelisted fields."""
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    event = {k: rec.get(k) if isinstance(rec.get(k), str) else None for k in FIELDS}
    if event["text"] is not None:
        event["text"], cut = truncate_text(event["text"], max_chars)
        if cut:
            event["truncated"] = True
    return event


def read_timeline(path: Path, limit: int, max_chars: int) -> list[dict[str, Any]]:
    """Return the last `limit` events in ascending time order; [] if unreadable."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return []
    end = data.rfind(b"\n")
    lines = data[:end].split(b"\n") if end >= 0 else []  # drop a partial last line
    events = [e for e in (parse_event(ln, max_chars) for ln in lines if ln.strip()) if e]
    events.sort(key=lambda e: e["at"] or "")  # stable; ISO timestamps sort lexically
    return events[-limit:] if limit > 0 else []
