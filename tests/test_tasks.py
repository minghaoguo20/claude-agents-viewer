import json
import tempfile
import threading
import unittest
from pathlib import Path

from tests import fixtures as fx
from viewer.config import Config
from viewer.server import make_server
from viewer.tasks import MAX_TASKS, derive_tasks, parse_notification
from viewer.transcript import Transcript

SECRET = "sk-should-not-leak"


def use(uid, tid, name="Bash", **inp):
    return fx.msg("assistant", uid, [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
                  message={"id": "m" + uid})


def result(uid, tid, text):
    return fx.msg("user", uid, [{"type": "tool_result", "tool_use_id": tid, "content": text}])


def note(task, tid, status, summary):
    body = (f"<task-notification>\n<task-id>{task}</task-id>\n<tool-use-id>{tid}</tool-use-id>\n"
            f"<output-file>/tmp/{SECRET}.output</output-file>\n<status>{status}</status>\n"
            f"<summary>{summary}</summary>\n</task-notification>")
    return fx.rec("queue-operation", operation="enqueue", timestamp="2026-01-02T00:00:00Z", content=body)


def shell(n, desc="job"):
    return [use(f"a{n}", f"t{n}", command=f"sleep {n} # {SECRET}", description=desc, run_in_background=True),
            result(f"r{n}", f"t{n}", f"Command running in background with ID: bg{n}. Output is being written to: /tmp/x")]


LINES = (
    shell(1, "done job") + [note("bg1", "t1", "completed", 'Background command "x" completed (exit code 0)')]
    + shell(2, "bad job") + [note("bg2", "t2", "completed", 'Background command "x" completed (exit code 3)')]
    + shell(3, "stopped job") + [use("s3", "ts3", "TaskStop", task_id="bg3")]
    + shell(4, "mystery job")
    + [use("am", "tm", "Monitor", description="watch log", command="tail -f log", timeout_ms=60000),
       result("rm", "tm", "Monitor started (task mon1, expires in 60s)"),
       note("mon1", "tm", "killed", "Monitor ended")]
    + [use("fg", "tf", command="ls"), use("bad", "tb", "Bash", run_in_background="yes"),
       "not json", json.dumps([1]), fx.rec("queue-operation", content=None),
       fx.rec("queue-operation", content="<task-notification><status>x</status></task-notification>"),
       fx.msg("assistant", "w", [{"type": "tool_use", "id": "tw", "name": "Bash", "input": "str"}])]
)


def load(lines):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tr = Transcript(path)
        tr.refresh()
        return tr


class TasksTest(unittest.TestCase):
    def test_statuses(self) -> None:
        tasks = {t["description"]: t for t in load(LINES).tasks()}
        self.assertEqual(len(tasks), 5)
        self.assertEqual((tasks["done job"]["status"], tasks["done job"]["exit_code"]), ("done", 0))
        self.assertEqual((tasks["bad job"]["status"], tasks["bad job"]["exit_code"]), ("failed", 3))
        self.assertEqual(tasks["stopped job"]["status"], "stopped")
        self.assertEqual(tasks["mystery job"]["status"], "unknown")
        self.assertNotIn("exit_code", tasks["mystery job"])
        mon = tasks["watch log"]
        self.assertEqual((mon["kind"], mon["id"], mon["tool_use_id"], mon["status"]), ("monitor", "mon1", "tm", "stopped"))
        self.assertEqual(tasks["done job"]["id"], "bg1")
        self.assertEqual(tasks["done job"]["kind"], "shell")

    def test_auto_backgrounded_plain_bash(self) -> None:
        text = ("Command did not complete within its 120s timeout and was moved to the background "
                "(ID: bx1). Output is being written to: /tmp/x")
        lines = [use("a", "t", command="sleep 300", description="slow"), result("r", "t", text),
                 use("b", "t2", command="ls"), result("r2", "t2", "file1"),
                 note("bx1", "t", "completed", 'Background command "slow" completed (exit code 0)')]
        tasks = load(lines).tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual((tasks[0]["id"], tasks[0]["status"], tasks[0]["exit_code"]), ("bx1", "done", 0))

    def test_no_leak_and_whitelist(self) -> None:
        tasks = load(LINES).tasks()
        self.assertNotIn(SECRET, json.dumps(tasks))
        allowed = {"id", "kind", "tool_use_id", "description", "status", "exit_code", "at"}
        self.assertTrue(all(set(t) <= allowed for t in tasks))

    def test_description_fallback_and_truncation(self) -> None:
        lines = [use("a", "t", command="x" * 999 + "\nsecond", run_in_background=True)]
        desc = load(lines).tasks()[0]["description"]
        self.assertEqual(len(desc), 200)
        self.assertTrue(desc.endswith("…"))

    def test_notification_as_user_message(self) -> None:
        text = note("bg1", "t1", "failed", "boom")
        content = json.loads(text)["content"]
        lines = shell(1) + [fx.msg("user", "n", content, origin={"kind": "task-notification"})]
        self.assertEqual(load(lines).tasks()[0]["status"], "failed")

    def test_parse_and_garbage(self) -> None:
        for bad in (None, 5, "", "<task-notification></task-notification>"):
            self.assertIsNone(parse_notification(bad))
        self.assertEqual(derive_tasks([{"blocks": [None, 3, {"type": "tool_use", "input": []}]}, {}], {}), [])

    def test_bounded(self) -> None:
        lines = [x for n in range(MAX_TASKS + 20) for x in shell(n)]
        self.assertEqual(len(load(lines).tasks()), MAX_TASKS)

    def test_endpoint_and_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = fx.make_claude_home(root)
            (home / "projects" / fx.CWD_DIR / f"{fx.SID_B}.jsonl").write_text("\n".join(LINES) + "\n")
            static = root / "static"
            static.mkdir()
            server = make_server(Config("127.0.0.1", 0, home, 50, 100, 1234), static)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                import http.client
                def get(path, method="GET"):
                    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                    conn.request(method, path)
                    resp = conn.getresponse()
                    return resp.status, resp.read()
                code, body = get(f"/api/agents/{fx.SID_B}/tasks")
                self.assertEqual(code, 200)
                self.assertEqual(len(json.loads(body)["tasks"]), 5)
                self.assertNotIn(SECRET.encode(), body)
                self.assertEqual(get(f"/api/agents/{fx.SID_A}/tasks")[0], 200)
                self.assertEqual(get("/api/agents/00000000-0000-0000-0000-000000000000/tasks")[0], 404)
                self.assertNotEqual(get(f"/api/agents/{fx.SID_B}/tasks", "POST")[0], 200)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
