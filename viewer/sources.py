"""Discover background-job agents under the Claude home directory. Read-only."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .files import read_json

CWD_PREFIX_BYTES = 64 * 1024
SESSION_ID_RE = re.compile(r"^[0-9a-f-]{8,36}$")


@dataclass
class AgentInfo:
    session_id: str
    short_id: str
    name: str
    kind: str
    status: str
    live: bool
    cwd: str | None
    intent: str | None
    detail: str | None
    updated_at: int | None
    pinned: bool
    transcript: Path | None
    job_id: str = ""
    tempo: str | None = None
    result: str | None = None
    tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {k: v for k, v in self.__dict__.items() if k != "transcript"}
        data["has_transcript"] = self.transcript is not None
        return data


def read_jobs(home: Path) -> dict[str, dict[str, Any]]:
    """Map job short id -> state.json dict for jobs having a state file."""
    jobs: dict[str, dict[str, Any]] = {}
    for state in sorted((home / "jobs").glob("*/state.json")):
        data = read_json(state)
        if isinstance(data, dict):
            jobs[state.parent.name] = data
    return jobs


def pid_alive(pid: int) -> bool:
    return Path("/proc", str(pid)).exists()


def read_live_sessions(home: Path) -> dict[str, dict[str, Any]]:
    """Map sessionId -> session json for sessions/<pid>.json (never *.key)."""
    live: dict[str, dict[str, Any]] = {}
    for path in sorted((home / "sessions").glob("*.json")):
        data = read_json(path)
        if isinstance(data, dict) and isinstance(data.get("sessionId"), str):
            data["_alive"] = isinstance(data.get("pid"), int) and pid_alive(data["pid"])
            live[data["sessionId"]] = data
    return live


def read_pins(home: Path) -> set[str]:
    data = read_json(home / "jobs" / "pins.json")
    return {p for p in data if isinstance(p, str)} if isinstance(data, list) else set()


def find_transcript(home: Path, session_id: str) -> Path | None:
    """Locate projects/*/<sessionId>.jsonl."""
    if not SESSION_ID_RE.match(session_id):
        return None
    for path in (home / "projects").glob(f"*/{session_id}.jsonl"):
        return path
    return None


def custom_title(transcript: Path | None) -> str | None:
    if transcript is None:
        return None
    data = read_json(transcript.parent / transcript.stem / "custom-title.json")
    title = data.get("customTitle") if isinstance(data, dict) else None
    return title if isinstance(title, str) and title else None


def transcript_cwd(transcript: Path | None) -> str | None:
    """Read cwd from the first records of a transcript (bounded prefix, complete lines only)."""
    if transcript is None:
        return None
    try:
        with open(transcript, "rb") as fh:
            prefix = fh.read(CWD_PREFIX_BYTES)
    except OSError:
        return None
    lines = prefix.split(b"\n")[:-1]  # drop the last, possibly partial, line
    for line in lines:
        try:
            cwd = json.loads(line).get("cwd")
        except (ValueError, AttributeError):
            continue
        if isinstance(cwd, str) and cwd:
            return cwd
    return None


def mtime_ms(path: Path | None) -> int | None:
    try:
        return int(path.stat().st_mtime * 1000) if path else None
    except OSError:
        return None


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _job_result(job: dict[str, Any]) -> str | None:
    output = job.get("output")
    return _opt_str(output.get("result")) if isinstance(output, dict) else None


def _job_tokens(job: dict[str, Any]) -> int | None:
    tokens = job.get("tokens")
    ok = isinstance(tokens, (int, float)) and not isinstance(tokens, bool)
    return int(tokens) if ok else None


def build_agent(home: Path, job_id: str, job: dict[str, Any],
                live: dict[str, Any] | None, pins: set[str]) -> AgentInfo:
    """Build an agent from a job state; live session data only enriches it."""
    sid = str(job["sessionId"])
    alive_data = live or {}
    alive = bool(alive_data.get("_alive"))
    transcript = find_transcript(home, sid)
    short = sid[:8]
    name = (job.get("name") or alive_data.get("name") or custom_title(transcript) or short)
    status = (alive_data.get("status") if alive else None) or job.get("state") or "unknown"
    stamps = [t for t in (alive_data.get("updatedAt"), mtime_ms(transcript))
              if isinstance(t, (int, float))]
    cwd = (_opt_str(job.get("cwd")) or _opt_str(alive_data.get("cwd"))
           or transcript_cwd(transcript))
    return AgentInfo(
        session_id=sid, short_id=short, name=str(name),
        kind=str(alive_data.get("kind") or "bg"), status=str(status), live=alive,
        cwd=cwd, intent=job.get("intent"), detail=job.get("detail"),
        updated_at=int(max(stamps)) if stamps else None,
        pinned=sid in pins or short in pins or job_id in pins, transcript=transcript,
        job_id=job_id, tempo=_opt_str(job.get("tempo")), result=_job_result(job),
        tokens=_job_tokens(job),
    )


def list_agents(claude_home: Path) -> list[AgentInfo]:
    """Return jobs having state.json with a sessionId; pinned first, newest first."""
    pins = read_pins(claude_home)
    live = read_live_sessions(claude_home)
    seen: set[str] = set()
    agents: list[AgentInfo] = []
    for job_id, state in read_jobs(claude_home).items():
        sid = state.get("sessionId")
        if not isinstance(sid, str) or not sid or sid in seen:
            continue
        seen.add(sid)
        agents.append(build_agent(claude_home, job_id, state, live.get(sid), pins))
    agents.sort(key=lambda a: (not a.pinned, -(a.updated_at or 0)))
    return agents


def find_agent(claude_home: Path, session_id: str) -> AgentInfo | None:
    return next((a for a in list_agents(claude_home) if a.session_id == session_id), None)
