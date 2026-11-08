"""Build a fake ~/.claude directory for tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SID_A = "aaaaaaaa-1111-2222-3333-444444444444"  # job + live (this process)
SID_B = "bbbbbbbb-1111-2222-3333-555555555555"  # job only, pinned by short id
SID_C = "cccccccc-1111-2222-3333-666666666666"  # job (state cwd) + dead-pid live session
SID_INTER = "eeeeeeee-1111-2222-3333-888888888888"  # interactive session, parked on job A
SID_SPARE = "ffffffff-1111-2222-3333-999999999999"  # spare pre-warmed session
SID_ORPHAN = "dddddddd-1111-2222-3333-777777777777"  # job dir without state.json
SECRET_ENV = "PROVIDER-ENV-SECRET"
ROSTER_TOKEN = "ROSTER-TOKEN-DO-NOT-READ"
AGENT_ID = "abc123"
PERSISTED = "b0v5i8967.txt"
KEY_CONTENT = "SECRET-KEY-DO-NOT-READ"
CWD_DIR = "-proj-demo"
BIG_TEXT = "x" * 10000
PERSISTED_BODY = "full persisted output\n" * 5


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def rec(kind: str, **fields: Any) -> str:
    return json.dumps({"type": kind, **fields})


def msg(kind: str, uuid: str, content: Any, **extra: Any) -> str:
    message = {"role": kind, "content": content, **extra.pop("message", {})}
    return rec(kind, uuid=uuid, timestamp="2026-01-01T00:00:00Z", message=message, **extra)


def main_transcript_lines() -> list[str]:
    return [
        msg("user", "u1", "hello there"),
        msg("user", "u0", "<system caveat>", isMeta=True),
        msg("assistant", "a1", [{"type": "thinking", "thinking": "pondering"}],
            message={"id": "msg_1", "model": "claude-x"}),
        msg("assistant", "a2", [{"type": "thinking", "thinking": ""},
                                {"type": "text", "text": "Let me look."}],
            message={"id": "msg_1", "model": "claude-x"}),
        msg("assistant", "a3", [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
                                 "input": {"command": "ls"}}],
            message={"id": "msg_1", "model": "claude-x"}),
        msg("user", "u2", [{"type": "tool_result", "tool_use_id": "toolu_1",
                            "content": [{"type": "text", "text": "file1"},
                                        {"type": "image", "source": {"data": "AAAA"}}]}]),
        msg("assistant", "a4", [{"type": "tool_use", "id": "toolu_2", "name": "Read",
                                 "input": {"file_path": "x" * 6000}}],
            message={"id": "msg_2", "model": "claude-x"}),
        msg("user", "u3", [{"type": "tool_result", "tool_use_id": "toolu_2", "is_error": True,
                            "content": f"<persisted-output>Full output saved to: "
                                       f"/x/tool-results/{PERSISTED}</persisted-output>"}]),
        msg("user", "u4", [{"type": "image", "source": {"type": "base64",
                                                        "media_type": "image/png",
                                                        "data": "iVBORw0KGgo"}},
                           {"type": "text", "text": BIG_TEXT}]),
        rec("ai-title", aiTitle="First title"),
        rec("ai-title", aiTitle="Demo title"),
        rec("cost-state", totalCostUSD=0.5),
        rec("cost-state", totalCostUSD=1.25, totalLinesAdded=3, totalAPIDuration=99,
            modelUsage={"claude-x": {"inputTokens": 10, "outputTokens": 20,
                                     "cacheReadInputTokens": 5, "costUSD": 1.25}}),
        rec("file-history-snapshot", x=1),
        "not json at all",
    ]


def prompt_transcript_lines() -> list[str]:
    """Transcript B: mixed origins for the prompts route."""
    return [
        msg("user", "b1", "hi b"),
        msg("user", "b2", "<command-name>/review</command-name>\n"
                          "<command-args>  the   PR </command-args>", origin={"kind": "human"}),
        msg("assistant", "b3", [{"type": "text", "text": "ok"}], message={"id": "msg_b"}),
        msg("user", "b4", "<task-notification>done</task-notification>",
            origin="task-notification"),
        msg("user", "b5", "<pasted_content id=1>\nline  one\nline two</pasted_content id=1>",
            origin={"kind": "human"}),
        msg("user", "b6", "z" * 500),
    ]


def timeline_text() -> str:
    """Timeline fixture: out-of-order lines, extra fields, junk and a partial last line."""
    events = [
        {"at": "2026-01-01T00:00:02Z", "state": "done", "detail": "d2", "text": "t" * 300,
         "secret": "leak"},
        {"at": "2026-01-01T00:00:01Z", "state": "working", "detail": "d1"},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\nnot json\n[1]\n" + '{"at": "2026'


def make_claude_home(root: Path) -> Path:
    """Create a realistic fake Claude home under root and return it."""
    home = root / ".claude"
    proj = home / "projects" / CWD_DIR
    proj.mkdir(parents=True)
    write_json(home / "jobs" / "aaaaaaaa" / "state.json",
               {"state": "working", "detail": "doing", "intent": "build", "name": "Job A",
                "sessionId": SID_A, "tempo": "active", "tokens": 4242,
                "output": {"result": "all done"}, "providerEnv": {"TOKEN": SECRET_ENV},
                "sessionPermissionRules": [SECRET_ENV]})
    write_json(home / "jobs" / "cccccccc" / "state.json",
               {"state": "blocked", "sessionId": SID_C, "cwd": "/job/c", "output": None})
    write_json(home / "jobs" / "bbbbbbbb" / "state.json",
               {"state": "done", "name": "Job B", "sessionId": SID_B})
    (home / "jobs" / "dddddddd").mkdir(parents=True)
    (home / "jobs" / "aaaaaaaa" / "timeline.jsonl").write_text(
        timeline_text(), encoding="utf-8")
    write_json(home / "daemon" / "roster.json", {"token": ROSTER_TOKEN})
    write_json(home / "jobs" / "pins.json", ["bbbbbbbb"])
    write_json(home / "sessions" / f"{os.getpid()}.json",
               {"pid": os.getpid(), "sessionId": SID_A, "cwd": "/work/a", "kind": "bg",
                "name": "Live A", "status": "busy", "updatedAt": 4102444800000,
                "startedAt": 1})
    write_json(home / "sessions" / "999999999.json",
               {"pid": 999999999, "sessionId": SID_C, "cwd": "/work/c", "kind": "interactive",
                "status": "idle", "updatedAt": 1000, "startedAt": 1})
    write_json(home / "sessions" / "888888888.json",
               {"pid": 888888888, "sessionId": SID_INTER, "cwd": "/work/i",
                "kind": "interactive", "parkedJobId": "aaaaaaaa", "updatedAt": 1})
    write_json(home / "sessions" / "777777777.json",
               {"pid": 777777777, "sessionId": SID_SPARE, "cwd": "/work/s", "kind": "bg",
                "spare": True, "updatedAt": 1})
    (home / "sessions" / f"{os.getpid()}.key").write_text(KEY_CONTENT, encoding="utf-8")
    body = "\n".join(main_transcript_lines()) + "\n" + '{"type": "user", "mess'  # truncated
    (proj / f"{SID_A}.jsonl").write_text(body, encoding="utf-8")
    (proj / f"{SID_B}.jsonl").write_text(
        "\n".join(prompt_transcript_lines()) + "\n", encoding="utf-8")
    sdir = proj / SID_A
    write_json(sdir / "custom-title.json", {"customTitle": "Custom A"})
    (sdir / "tool-results").mkdir(parents=True)
    (sdir / "tool-results" / PERSISTED).write_text(PERSISTED_BODY, encoding="utf-8")
    (sdir / "subagents").mkdir()
    (sdir / "subagents" / f"agent-{AGENT_ID}.jsonl").write_text(
        msg("user", "s1", "sub task", isSidechain=True) + "\n"
        + msg("assistant", "s2", [{"type": "text", "text": "sub done"}],
              message={"id": "msg_s"}, isSidechain=True) + "\n", encoding="utf-8")
    write_json(sdir / "subagents" / f"agent-{AGENT_ID}.meta.json",
               {"agentType": "Explore", "description": "look", "toolUseId": "toolu_1",
                "model": "claude-x", "finished": True})
    (proj / "secret.txt").write_text("outside", encoding="utf-8")
    return home
