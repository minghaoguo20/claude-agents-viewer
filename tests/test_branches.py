import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from tests import fixtures as fx
from viewer.branches import Node, latest_leaves, path_for
from viewer.config import Config
from viewer.server import make_server
from viewer.transcript import Transcript, page

SID_BR, SID_FAN, SID_LIN = ("1" * 8 + "-0000-0000-0000-000000000001",
                            "2" * 8 + "-0000-0000-0000-000000000002",
                            "3" * 8 + "-0000-0000-0000-000000000003")


def write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def uuids(msgs: list[dict]) -> list[str]:
    return [m["uuid"] for m in msgs]


class TranscriptBranchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def load(self, lines: list[str]) -> Transcript:
        tr = Transcript(write(self.dir / "t.jsonl", lines))
        tr.refresh()
        return tr

    def test_linear_unchanged(self) -> None:
        tr = self.load(fx.main_transcript_lines())
        leaf, path = tr.view()
        self.assertEqual(path, tr.messages)
        self.assertTrue(all("branch" not in m for m in path))

    def test_linear_tree_has_no_branch(self) -> None:
        tr = self.load(fx.fanout_lines()[:1] + [fx.tmsg("assistant", "x", "f1", fx.text_block("r"))])
        self.assertEqual(uuids(tr.view()[1]), ["f1", "x"])

    def test_default_path_is_latest_branch(self) -> None:
        tr = self.load(fx.branch_lines())
        leaf, path = tr.view()
        self.assertEqual(leaf, "a2c")
        self.assertEqual(uuids(path), ["h1", "a1", "h2c", "a2c"])
        self.assertEqual([m["idx"] for m in path], [0, 1, 6, 7])  # gaps are kept
        self.assertEqual(len(tr.messages), 8)

    def test_branch_info(self) -> None:
        _, path = self.load(fx.branch_lines()).view()
        branch = path[2]["branch"]
        self.assertEqual((branch["index"], branch["count"]), (3, 3))
        self.assertEqual(branch["siblings"], [
            {"uuid": "h2", "idx": 2, "leaf": "a2"},
            {"uuid": "h2b", "idx": 4, "leaf": "a2b"},
            {"uuid": "h2c", "idx": 6, "leaf": "a2c"}])
        self.assertTrue(all("branch" not in m for i, m in enumerate(path) if i != 2))

    def test_switch_leaf(self) -> None:
        tr = self.load(fx.branch_lines())
        leaf, path = tr.view("a2")
        self.assertEqual((leaf, uuids(path)), ("a2", ["h1", "a1", "h2", "a2"]))
        self.assertEqual(path[2]["branch"]["index"], 1)
        with self.assertRaises(KeyError):
            tr.view("nope")

    def test_fanout_not_a_branch_and_merge_keeps_chain(self) -> None:
        tr = self.load(fx.fanout_lines())
        leaf, path = tr.view()
        self.assertEqual(leaf, "f6")
        self.assertEqual(uuids(path), ["f1", "f2", "f4", "f5", "f6"])
        self.assertTrue(all("branch" not in m for m in path))
        self.assertEqual(len(path[1]["blocks"]), 2)  # merged pieces

    def test_compaction_logical_parent(self) -> None:
        tr = self.load(fx.compaction_lines())
        self.assertEqual(uuids(tr.view()[1]), ["c1", "c2", "c4", "c5"])

    def test_incremental_rewind(self) -> None:
        lines = fx.branch_lines()[:5]
        path = write(self.dir / "t.jsonl", lines)
        tr = Transcript(path)
        tr.refresh()
        self.assertEqual(uuids(tr.view()[1]), ["h1", "a1", "h2", "a2"])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(fx.tmsg("user", "h3", "a1", "rewound") + "\n")
        tr.refresh()
        leaf, view = tr.view()
        self.assertEqual((leaf, uuids(view)), ("h3", ["h1", "a1", "h3"]))
        self.assertEqual(view[2]["branch"]["count"], 2)

    def test_locate(self) -> None:
        tr = self.load(fx.branch_lines())
        self.assertEqual(tr.locate("h2"), ("a2", False))
        self.assertEqual(tr.locate("h2c"), ("a2c", True))

    def test_pure_helpers_cycle_and_missing_parent(self) -> None:
        nodes = {"a": Node("b", 0), "b": Node("a", 1), "c": Node("zzz", 2)}
        self.assertEqual(path_for(nodes, "a"), [1, 0])
        self.assertEqual(path_for(nodes, "c"), [2])
        self.assertEqual(latest_leaves({"r": Node(None, 0), "x": Node("r", 1)})["r"], "x")


class PagingGapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.msgs = [{"idx": i, "blocks": []} for i in (0, 1, 6, 7, 12, 13)]

    def idxs(self, **kw: int) -> tuple[list[int], bool, bool]:
        out = page(self.msgs, limit=2, max_block_chars=10, **kw)
        return [m["idx"] for m in out["messages"]], out["has_more_before"], out["has_more_after"]

    def test_default_and_before_after(self) -> None:
        self.assertEqual(self.idxs(), ([12, 13], True, False))
        self.assertEqual(self.idxs(before=12), ([6, 7], True, True))
        self.assertEqual(self.idxs(after=1), ([6, 7], True, True))
        self.assertEqual(self.idxs(before=1), ([0], False, True))

    def test_around_with_gaps(self) -> None:
        self.assertEqual(self.idxs(around=8), ([7, 12], True, True))  # 8 is not on the path
        self.assertEqual(self.idxs(around=0), ([0, 1], False, True))
        self.assertEqual(self.idxs(around=13)[1:], (True, False))


class BranchServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        home = fx.make_claude_home(root)
        proj = home / "projects" / fx.CWD_DIR
        write(proj / f"{SID_BR}.jsonl", fx.branch_lines())
        write(proj / f"{SID_FAN}.jsonl", fx.fanout_lines())
        write(proj / f"{SID_LIN}.jsonl", fx.prompt_transcript_lines())
        sub = proj / SID_BR / "subagents"
        sub.mkdir(parents=True)
        write(sub / "agent-br1.jsonl", fx.branch_lines())
        for sid in (SID_BR, SID_FAN, SID_LIN):
            fx.write_json(home / "jobs" / sid[:8] / "state.json",
                          {"state": "done", "sessionId": sid})
        static = root / "static"
        static.mkdir()
        cls.server = make_server(Config("127.0.0.1", 0, home, 50, 100, 1234), static)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.proj, cls.home = proj, home

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path: str, status: int = 200):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        self.assertEqual(resp.status, status, body[:200])
        return json.loads(body)

    base = f"/api/agents/{SID_BR}"

    def test_messages_default_and_leaf(self) -> None:
        data = self.get(f"{self.base}/messages")
        self.assertEqual((data["leaf"], data["total"]), ("a2c", 4))
        self.assertEqual(uuids(data["messages"]), ["h1", "a1", "h2c", "a2c"])
        self.assertEqual(data["messages"][2]["branch"]["count"], 3)
        data = self.get(f"{self.base}/messages?leaf=a2b")
        self.assertEqual(uuids(data["messages"]), ["h1", "a1", "h2b", "a2b"])
        self.assertEqual(data["messages"][2]["branch"]["index"], 2)

    def test_messages_paging_with_gaps(self) -> None:
        data = self.get(f"{self.base}/messages?limit=2")
        self.assertEqual([m["idx"] for m in data["messages"]], [6, 7])
        self.assertTrue(data["has_more_before"] and not data["has_more_after"])
        data = self.get(f"{self.base}/messages?leaf=a2&around=2&limit=2")
        self.assertEqual(uuids(data["messages"]), ["a1", "h2"])

    def test_unknown_leaf(self) -> None:
        self.assertIn("error", self.get(f"{self.base}/messages?leaf=nope", 400))
        self.get(f"{self.base}/prompts?leaf=nope", 400)

    def test_prompts_branch_field(self) -> None:
        data = self.get(f"{self.base}/prompts")
        self.assertEqual([p["idx"] for p in data["prompts"]], [0, 6])
        self.assertNotIn("branch", data["prompts"][0])
        br = data["prompts"][1]["branch"]
        self.assertEqual((br["index"], br["count"]), (3, 3))
        self.assertEqual([(s["idx"], s["text"], s["current"]) for s in br["siblings"]],
                         [(2, "old question one", False), (4, "old question two", False),
                          (6, "newest question", True)])
        old = self.get(f"{self.base}/prompts?leaf=a2")["prompts"][1]["branch"]
        self.assertEqual([s["current"] for s in old["siblings"]], [True, False, False])

    def test_fanout_route(self) -> None:
        data = self.get(f"/api/agents/{SID_FAN}/messages")
        self.assertEqual(data["total"], 5)
        self.assertTrue(all("branch" not in m for m in data["messages"]))

    def test_linear_route_unchanged(self) -> None:
        data = self.get(f"/api/agents/{SID_LIN}/messages")
        self.assertEqual(data["total"], 6)
        self.assertTrue(all("branch" not in m for m in data["messages"]))

    def test_prompts_leaf(self) -> None:
        data = self.get(f"{self.base}/prompts")
        self.assertEqual(([p["idx"] for p in data["prompts"]], data["leaf"]), ([0, 6], "a2c"))
        data = self.get(f"{self.base}/prompts?leaf=a2")
        self.assertEqual(([p["idx"] for p in data["prompts"]], data["leaf"]), ([0, 2], "a2"))

    def test_detail(self) -> None:
        data = self.get(self.base)
        self.assertEqual((data["total"], data["leaf"]), (4, "a2c"))

    def test_search_hits(self) -> None:
        hits = {r["idx"]: r for r in self.get("/api/search?q=question")["results"]
                if r["session_id"] == SID_BR}
        self.assertEqual((hits[2]["leaf"], hits[2]["on_default_path"]), ("a2", False))
        self.assertEqual((hits[6]["leaf"], hits[6]["on_default_path"]), ("a2c", True))
        self.assertEqual(len(hits), 4)

    def test_subagent_branches(self) -> None:
        data = self.get(f"{self.base}/subagents/br1/messages")
        self.assertEqual(uuids(data["messages"]), ["h1", "a1", "h2c", "a2c"])
        self.get(f"{self.base}/subagents/br1/messages?leaf=bad", 400)


if __name__ == "__main__":
    unittest.main()
