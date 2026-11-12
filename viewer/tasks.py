"""Derive background tasks (Bash run_in_background, Monitor) from a transcript. Whitelist only; never raises."""
from __future__ import annotations

import re
from typing import Any


MAX_TASKS = 200
MAX_NOTES = 1000
DESC_MAX = 200
SHELL_ID_RE = re.compile(r"Command running in background with ID:\s*([\w-]+)")
MONITOR_ID_RE = re.compile(r"Monitor started \(task\s+([\w-]+)")
MOVED_RE = re.compile(r"moved to the background \(ID:\s*([\w-]+)")  # plain Bash auto-backgrounded on timeout
EXIT_RE = re.compile(r"exit code\s+(-?\d+)", re.I)
TAG_RE = {name: re.compile(rf"<{name}>(.*?)</{name}>", re.S)
          for name in ("task-id", "tool-use-id", "status", "summary")}
STOP_TOOLS = {"TaskStop": "task_id", "KillShell": "shell_id"}


def parse_notification(text: Any) -> dict[str, Any] | None:
    """Fields of a `<task-notification>` text (task_id, tool_use_id, status, summary), else None."""
    if not isinstance(text, str) or "<task-notification>" not in text:
        return None
    found = {k: (m.group(1).strip() if (m := rx.search(text)) else "") for k, rx in TAG_RE.items()}
    if not found["task-id"] and not found["tool-use-id"]:
        return None
    return {"task_id": found["task-id"], "tool_use_id": found["tool-use-id"],
            "status": found["status"], "summary": found["summary"][:DESC_MAX * 2]}


def remember(notes: dict[str, dict[str, Any]], rec: dict[str, Any], text: Any) -> None:
    """Store a notification (keyed by task id, else tool_use id); later ones win; size is bounded."""
    note = parse_notification(text)
    if note is None:
        return
    note["at"] = rec.get("timestamp") if isinstance(rec.get("timestamp"), str) else None
    key = note["task_id"] or note["tool_use_id"]
    notes.pop(key, None)
    notes[key] = note
    while len(notes) > MAX_NOTES:
        del notes[next(iter(notes))]


def _status(note: dict[str, Any] | None, stopped: bool) -> tuple[str, int | None]:
    """Terminal status from a notification (or a TaskStop); no evidence -> unknown."""
    if note is None:
        return ("stopped", None) if stopped else ("unknown", None)
    code = EXIT_RE.search(note["summary"])
    exit_code = int(code.group(1)) if code else None
    status = note["status"].lower()
    if status in ("killed", "stopped", "cancelled") or stopped:
        return "stopped", exit_code
    if status == "failed" or (exit_code not in (None, 0)):
        return "failed", exit_code
    return ("done", exit_code) if status in ("completed", "done", "timeout", "timed_out") else ("unknown", exit_code)


def _description(name: str, inp: dict[str, Any]) -> str:
    text = inp.get("description") if isinstance(inp.get("description"), str) else ""
    text = text.strip() or str(inp.get("command") or "").strip() or name
    text = " ".join(text.split())
    return text if len(text) <= DESC_MAX else text[:DESC_MAX - 1] + "…"


def derive_tasks(messages: list[dict[str, Any]], notes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Tasks in start order: {id, kind, tool_use_id, description, status, exit_code?, at?}."""
    started: dict[str, dict[str, Any]] = {}  # tool_use id -> task
    stops: set[str] = set()
    plain: set[str] = set()
    notes = dict(notes)  # also pick up notifications delivered as user messages
    for msg in messages:
        for block in msg.get("blocks") or []:
            try:
                kind = block.get("type")
                if kind == "tool_use":
                    name, inp, tid = block.get("name"), block.get("input"), block.get("id")
                    if not isinstance(inp, dict) or not isinstance(tid, str):
                        continue
                    if name == "Bash" and inp.get("run_in_background") is True:
                        task_kind = "shell"
                    elif name == "Bash":
                        task_kind = "shell"
                        plain.add(tid)  # becomes a task only if its result says it was backgrounded
                    elif name == "Monitor":
                        task_kind = "monitor"
                    elif name in STOP_TOOLS and isinstance(inp.get(STOP_TOOLS[name]), str):
                        stops.add(inp[STOP_TOOLS[name]])
                        continue
                    else:
                        continue
                    started[tid] = {"id": tid, "kind": task_kind, "tool_use_id": tid,
                                    "description": _description(name, inp), "at": msg.get("timestamp")}
                elif kind == "text" and msg.get("role") == "user":
                    note = parse_notification(block.get("text"))
                    if note:
                        note["at"] = msg.get("timestamp")
                        notes.setdefault(note["task_id"] or note["tool_use_id"], note)
                elif kind == "tool_result" and block.get("tool_use_id") in started:
                    tid = block["tool_use_id"]
                    task = started[tid]
                    text = block.get("text") or ""
                    found = MONITOR_ID_RE.search(text) if task["kind"] == "monitor" else (
                        MOVED_RE.search(text) if tid in plain else SHELL_ID_RE.search(text))
                    if found:
                        task["id"] = found.group(1)
                        plain.discard(tid)
            except (AttributeError, TypeError):
                continue
    tasks = []
    for task in started.values():
        if task["tool_use_id"] in plain:
            continue
        note = notes.get(task["id"]) or notes.get(task["tool_use_id"])
        task["status"], code = _status(note, task["id"] in stops or task["tool_use_id"] in stops)
        if code is not None:
            task["exit_code"] = code
        tasks.append({k: v for k, v in task.items() if v is not None})
    return tasks[-MAX_TASKS:]
