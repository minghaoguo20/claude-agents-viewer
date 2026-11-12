import http.client
import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from tests import fixtures as fx
from viewer.config import Config
from viewer.server import make_server


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        home = fx.make_claude_home(root)
        static = root / "static"
        static.mkdir()
        (static / "index.html").write_text("<h1>hi</h1>")
        (static / "app.js").write_text("var a=1;")
        (root / "secret.txt").write_text("top secret")
        cfg = Config("127.0.0.1", 0, home, 50, 100, 1234)
        cls.server = make_server(cfg, static)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, path: str, method: str = "GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read()
        headers = dict(resp.getheaders())
        conn.close()
        return resp.status, headers, body

    def get_json(self, path: str, status: int = 200):
        code, headers, body = self.request(path)
        self.assertEqual(code, status, body[:200])
        return json.loads(body)

    base = f"/api/agents/{fx.SID_A}"

    def test_config(self) -> None:
        self.assertEqual(self.get_json("/api/config"), {"poll_interval_ms": 1234, "page_size": 50})

    def test_security_headers(self) -> None:
        _, headers, _ = self.request("/api/config")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_static_validators(self) -> None:
        code, headers, body = self.request("/static/app.js")
        self.assertEqual((code, body), (200, b"var a=1;"))
        self.assertEqual(headers["Cache-Control"], "no-cache")
        etag = headers["ETag"]
        for path in ("/static/app.js", "/"):
            _, h, _ = self.request(path)
            self.assertEqual(h["Cache-Control"], "no-cache")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/static/app.js", headers={"If-None-Match": etag})
        resp = conn.getresponse()
        self.assertEqual((resp.status, resp.read()), (304, b""))
        self.assertEqual(resp.getheader("ETag"), etag)
        self.assertEqual(resp.getheader("Cache-Control"), "no-cache")
        conn.request("GET", "/static/app.js", headers={"If-None-Match": '"nope"'})  # same connection
        resp = conn.getresponse()
        self.assertEqual((resp.status, resp.read()), (200, b"var a=1;"))
        conn.close()

    def test_keep_alive(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        for path, status in (("/api/config", 200), ("/api/nope", 404), ("/api/agents", 200),
                             ("/static/../secret.txt", 404), ("/static/app.js", 200)):
            conn.request("GET", path)
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, status)
            self.assertEqual(resp.version, 11)
        conn.request("HEAD", "/api/config")
        resp = conn.getresponse()
        self.assertEqual(resp.read(), b"")
        conn.request("POST", "/api/config")
        resp = conn.getresponse()
        self.assertEqual((resp.status, resp.getheader("Allow")), (405, "GET, HEAD"))
        resp.read()
        conn.request("GET", "/api/config")
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()

    def test_agents_list(self) -> None:
        agents = self.get_json("/api/agents")["agents"]
        self.assertEqual(len(agents), 3)
        self.assertEqual(agents[0]["session_id"], fx.SID_B)

    def test_agents_no_leaks(self) -> None:
        _, _, body = self.request("/api/agents")
        self.assertNotIn(fx.SECRET_ENV.encode(), body)
        self.assertNotIn(fx.ROSTER_TOKEN.encode(), body)
        first = next(a for a in json.loads(body)["agents"] if a["session_id"] == fx.SID_A)
        self.assertEqual((first["job_id"], first["result"], first["tokens"]),
                         ("aaaaaaaa", "all done", 4242))
        self.assertEqual(first["pid"], os.getpid())  # live session exposes its pid
        dead = next(a for a in json.loads(body)["agents"] if a["session_id"] == fx.SID_C)
        self.assertIsNone(dead["pid"])  # dead pid is hidden
        info = first["session"]
        self.assertEqual((info["pid"], info["sessionId"], info["cwd"], info["kind"], info["status"], info["alive"]),
                         (os.getpid(), fx.SID_A, "/work/a", "bg", "busy", True))
        self.assertEqual(set(info) - {"entrypoint", "version"},
                         {"pid", "sessionId", "cwd", "startedAt", "kind", "status", "alive"})
        self.assertFalse(dead["session"]["alive"])

    def test_prompts(self) -> None:
        prompts = self.get_json(f"/api/agents/{fx.SID_B}/prompts")["prompts"]
        self.assertEqual([p["idx"] for p in prompts], [0, 1, 4, 5])
        self.assertEqual(prompts[0]["text"], "hi b")
        self.assertEqual(prompts[1]["text"], "/review the PR")
        self.assertEqual(prompts[2]["text"], "line one line two")
        self.assertEqual(len(prompts[3]["text"]), 200)
        self.assertEqual(set(prompts[0]), {"idx", "timestamp", "text"})

    def test_prompts_unknown_sid(self) -> None:
        self.get_json("/api/agents/00000000-0000-0000-0000-000000000000/prompts", 404)

    def test_message_origin(self) -> None:
        msgs = self.get_json(f"{self.base}/messages?limit=100")["messages"]
        self.assertEqual([m["origin"] for m in msgs[:3]], ["human", "system", None])

    def test_timeline(self) -> None:
        events = self.get_json(f"{self.base}/timeline")["events"]
        self.assertEqual([e["state"] for e in events], ["working", "done"])
        self.assertEqual(set(events[1]), {"at", "state", "detail", "text", "truncated"})
        self.assertEqual(len(events[1]["text"]), 100)
        self.assertNotIn("leak", json.dumps(events))
        events = self.get_json(f"{self.base}/timeline?limit=1")["events"]
        self.assertEqual([e["state"] for e in events], ["done"])
        self.assertEqual(self.get_json(f"/api/agents/{fx.SID_B}/timeline")["events"], [])
        self.get_json(f"{self.base}/timeline?limit=x", 400)

    def test_around_and_has_more_after(self) -> None:
        data = self.get_json(f"{self.base}/messages?around=3&limit=4")
        self.assertEqual([m["idx"] for m in data["messages"]], [1, 2, 3, 4])
        self.assertTrue(data["has_more_before"] and data["has_more_after"])
        data = self.get_json(f"{self.base}/messages?around=6&limit=4")
        self.assertEqual([m["idx"] for m in data["messages"]], [4, 5, 6])
        self.assertFalse(data["has_more_after"])
        self.assertFalse(self.get_json(f"{self.base}/messages")["has_more_after"])
        self.get_json(f"{self.base}/messages?around=3&before=2", 400)
        self.get_json(f"{self.base}/messages?after=1&before=4", 400)
        sub = self.get_json(f"{self.base}/subagents/{fx.AGENT_ID}/messages?around=0&limit=1")
        self.assertTrue(sub["has_more_after"])

    def test_search(self) -> None:
        data = self.get_json("/api/search?q=HELLO")
        self.assertEqual([(r["session_id"], r["idx"], r["block_type"]) for r in data["results"]],
                         [(fx.SID_A, 0, "text")])
        self.assertFalse(data["truncated"])
        tool = self.get_json("/api/search?q=file_path")["results"]
        self.assertEqual(tool[0]["block_type"], "tool_use")
        self.assertEqual(self.get_json("/api/search?q=%20%20ls%20%20")["results"][0]["idx"], 2)
        data = self.get_json("/api/search?q=sub%20task")  # subagents are not searched
        self.assertEqual(data["results"], [])

    def test_search_limit_and_validation(self) -> None:
        data = self.get_json("/api/search?q=xx&limit=1")
        self.assertEqual((len(data["results"]), data["truncated"]), (1, True))
        self.assertEqual(data["results"][0]["idx"], 6)  # newest first
        self.get_json("/api/search?q=a", 400)
        self.get_json("/api/search", 400)
        self.get_json("/api/search?q=" + "a" * 201, 400)
        self.get_json("/api/search?q=aa&limit=zz", 400)

    def test_snippet_whitespace_and_context(self) -> None:
        snippet = self.get_json("/api/search?q=pondering")["results"][0]["snippet"]
        self.assertEqual(snippet, "pondering")
        long = self.get_json("/api/search?q=xxxx&limit=1")["results"][0]["snippet"]
        self.assertLessEqual(len(long), 4 + 160)

    def test_stats(self) -> None:
        data = self.get_json("/api/stats")
        rows = {r["session_id"]: r for r in data["agents"]}
        self.assertEqual(data["agents"][0]["session_id"], fx.SID_A)
        self.assertEqual(rows[fx.SID_A]["cost_usd"], 1.25)
        self.assertEqual(rows[fx.SID_A]["messages"], 7)
        self.assertEqual(rows[fx.SID_A]["models"]["claude-x"]["input_tokens"], 10)
        self.assertEqual(rows[fx.SID_B]["cost_usd"], 0)
        self.assertEqual(rows[fx.SID_B]["models"], {})
        self.assertEqual(data["totals"]["cost_usd"], 1.25)
        self.assertEqual(data["totals"]["models"]["claude-x"]["output_tokens"], 20)

    def test_agent_detail(self) -> None:
        data = self.get_json(self.base)
        self.assertEqual((data["title"], data["cost_usd"], data["total"]),
                         ("Demo title", 1.25, 7))
        self.assertEqual(data["name"], "Job A")

    def test_unknown_and_invalid_sid(self) -> None:
        self.get_json("/api/agents/eeeeeeee-1111", 404)
        self.get_json("/api/agents/..", 404)
        self.get_json("/api/agents/ZZZZZZZZ", 404)
        self.get_json(f"/api/agents/{fx.SID_ORPHAN}", 404)

    def test_messages_paging(self) -> None:
        data = self.get_json(f"{self.base}/messages?limit=3")
        self.assertEqual([m["idx"] for m in data["messages"]], [4, 5, 6])
        self.assertTrue(data["has_more_before"])
        data = self.get_json(f"{self.base}/messages?limit=3&before=4")
        self.assertEqual([m["idx"] for m in data["messages"]], [1, 2, 3])
        data = self.get_json(f"{self.base}/messages?after=5")
        self.assertEqual([m["idx"] for m in data["messages"]], [6])
        self.get_json(f"{self.base}/messages?limit=abc", 400)

    def test_messages_truncated_and_limit_cap(self) -> None:
        data = self.get_json(f"{self.base}/messages?limit=100000")
        self.assertEqual(len(data["messages"]), 7)
        big = data["messages"][6]["blocks"][1]
        self.assertTrue(big["truncated"])
        self.assertEqual(len(big["text"]), 100)

    def test_block_untruncated(self) -> None:
        block = self.get_json(f"{self.base}/messages/6/blocks/1")
        self.assertEqual(block["text"], fx.BIG_TEXT)
        self.assertNotIn("truncated", block)
        use = self.get_json(f"{self.base}/messages/4/blocks/0")
        self.assertEqual(len(use["input"]["file_path"]), 6000)
        self.get_json(f"{self.base}/messages/99/blocks/0", 404)
        self.get_json(f"{self.base}/messages/6/blocks/9", 404)

    def test_subagents(self) -> None:
        subs = self.get_json(f"{self.base}/subagents")["subagents"]
        self.assertEqual(subs[0]["agent_id"], fx.AGENT_ID)
        data = self.get_json(f"{self.base}/subagents/{fx.AGENT_ID}/messages")
        self.assertEqual(data["total"], 2)
        block = self.get_json(f"{self.base}/subagents/{fx.AGENT_ID}/messages/1/blocks/0")
        self.assertEqual(block["text"], "sub done")
        self.get_json(f"{self.base}/subagents/nope/messages", 404)
        self.assertEqual(self.get_json(f"/api/agents/{fx.SID_B}/subagents")["subagents"], [])

    def test_tool_results(self) -> None:
        code, headers, body = self.request(f"{self.base}/tool-results/{fx.PERSISTED}")
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertEqual(body.decode(), fx.PERSISTED_BODY)
        self.get_json(f"{self.base}/tool-results/missing.txt", 404)

    def test_path_traversal(self) -> None:
        attempts = [
            f"{self.base}/tool-results/..%2F..%2Fsecret.txt",
            f"{self.base}/tool-results/..",
            f"{self.base}/tool-results/%2e%2e%2f%2e%2e%2fsecret.txt",
            f"{self.base}/subagents/..%2F..%2F{fx.SID_A}/messages",
            f"{self.base}/subagents/../messages",
            "/static/../config.ini",
            "/static/..%2Fconfig.ini",
            "/static/%2e%2e/config.ini",
            f"/api/agents/..%2F..%2Fetc/messages",
        ]
        for path in attempts:
            code, _, body = self.request(path)
            self.assertIn(code, (400, 404), path)
            self.assertNotIn(b"top secret", body)
            self.assertNotIn(b"outside", body)

    def test_symlink_escape_blocked(self) -> None:
        link = Path(self.tmp.name) / ".claude" / "projects" / fx.CWD_DIR / fx.SID_A \
            / "tool-results" / "link.txt"
        link.symlink_to(Path(self.tmp.name) / "secret.txt")
        self.get_json(f"{self.base}/tool-results/link.txt", 404)

    def test_methods(self) -> None:
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            code, headers, _ = self.request("/api/agents", method)
            self.assertEqual(code, 405, method)
            self.assertIn("GET", headers["Allow"])
        code, _, body = self.request("/api/config", "HEAD")
        self.assertEqual((code, body), (200, b""))

    def test_static(self) -> None:
        code, headers, body = self.request("/")
        self.assertEqual((code, body), (200, b"<h1>hi</h1>"))
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        code, headers, body = self.request("/static/app.js")
        self.assertEqual((code, body), (200, b"var a=1;"))
        self.assertIn("javascript", headers["Content-Type"])
        self.assertEqual(self.request("/static/missing.css")[0], 404)
        self.assertEqual(self.request("/nowhere")[0], 404)


class NonLoopbackTest(unittest.TestCase):
    def test_make_server_port_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(Config("127.0.0.1", 0, Path(tmp), 1, 1, 1))
            server = make_server(cfg, Path(tmp))
            self.assertGreater(server.server_address[1], 0)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
