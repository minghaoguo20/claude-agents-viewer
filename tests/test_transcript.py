import tempfile
import threading
import unittest
from pathlib import Path

from tests import fixtures as fx
from viewer.transcript import Transcript, TranscriptCache, list_subagents, page


class TranscriptTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = fx.make_claude_home(Path(tmp.name))
        self.proj = self.home / "projects" / fx.CWD_DIR
        self.path = self.proj / f"{fx.SID_A}.jsonl"
        self.tr = Transcript(self.path)
        self.tr.refresh()

    def test_message_count_and_idx(self) -> None:
        msgs = self.tr.messages
        self.assertEqual([m["idx"] for m in msgs], list(range(len(msgs))))
        self.assertEqual(len(msgs), 7)  # u1, meta, merged a, u2, a4, u3, u4

    def test_merge_by_message_id(self) -> None:
        merged = self.tr.messages[2]
        self.assertEqual([b["type"] for b in merged["blocks"]], ["thinking", "text", "tool_use"])
        self.assertEqual(merged["model"], "claude-x")

    def test_empty_thinking_dropped(self) -> None:
        kinds = [b["type"] for m in self.tr.messages for b in m["blocks"]]
        self.assertEqual(kinds.count("thinking"), 1)

    def test_meta_flag(self) -> None:
        self.assertTrue(self.tr.messages[1]["meta"])
        self.assertFalse(self.tr.messages[0]["meta"])

    def test_string_content_and_tool_result(self) -> None:
        self.assertEqual(self.tr.messages[0]["blocks"], [{"type": "text", "text": "hello there"}])
        result = self.tr.messages[3]["blocks"][0]
        self.assertEqual(result["text"], "file1")
        self.assertFalse(result["is_error"])
        self.assertIsNone(result["persisted_file"])

    def test_persisted_file(self) -> None:
        result = self.tr.messages[5]["blocks"][0]
        self.assertEqual(result["persisted_file"], fx.PERSISTED)
        self.assertTrue(result["is_error"])

    def test_image_data_stripped(self) -> None:
        image = self.tr.messages[6]["blocks"][0]
        self.assertEqual(image, {"type": "image", "media_type": "image/png"})
        self.assertNotIn("iVBOR", str(self.tr.messages))

    def test_cost_state(self) -> None:
        state = self.tr.cost_state
        self.assertEqual(state["cost_usd"], 1.25)
        self.assertEqual((state["lines_added"], state["lines_removed"]), (3, 0))
        self.assertEqual(state["api_duration_ms"], 99)
        self.assertEqual(state["models"]["claude-x"],
                         {"input_tokens": 10, "output_tokens": 20, "thinking_tokens": 0,
                          "cache_read_tokens": 5, "cache_creation_tokens": 0, "cost_usd": 1.25})

    def test_title_and_cost_last_wins(self) -> None:
        self.assertEqual(self.tr.title, "Demo title")
        self.assertEqual(self.tr.cost_usd, 1.25)

    def test_partial_last_line_then_append(self) -> None:
        before = len(self.tr.messages)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write('ssage": {"content": "finished line"}, "uuid": "u9"}\n')
        self.tr.refresh()  # the partial line was invalid JSON once completed -> skipped
        self.assertEqual(len(self.tr.messages), before)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(fx.msg("user", "u10", "later") + "\n")
        self.tr.refresh()
        self.assertEqual(self.tr.messages[-1]["blocks"][0]["text"], "later")

    def test_partial_line_picked_up_when_completed(self) -> None:
        path = self.proj / "partial.jsonl"
        full = fx.msg("user", "p1", "complete me")
        path.write_text(full[:20], encoding="utf-8")
        tr = Transcript(path)
        tr.refresh()
        self.assertEqual(tr.messages, [])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(full[20:] + "\n")
        tr.refresh()
        self.assertEqual(tr.messages[0]["blocks"][0]["text"], "complete me")

    def test_incremental_does_not_reparse(self) -> None:
        first = self.tr.messages[0]
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write("\n" + fx.msg("user", "u11", "more") + "\n")  # ends the partial line
        self.tr.refresh()
        self.assertEqual(self.tr.messages[0], first)
        self.assertEqual(len(self.tr.messages), 8)

    def test_append_continues_streamed_message(self) -> None:
        # message.id merge only across consecutive records, also across refreshes
        path = self.proj / "stream.jsonl"
        a = fx.msg("assistant", "x1", [{"type": "text", "text": "A"}], message={"id": "m"})
        b = fx.msg("assistant", "x2", [{"type": "text", "text": "B"}], message={"id": "m"})
        path.write_text(a + "\n", encoding="utf-8")
        tr = Transcript(path)
        tr.refresh()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(b + "\n")
        tr.refresh()
        self.assertEqual(len(tr.messages), 1)
        self.assertEqual(len(tr.messages[0]["blocks"]), 2)

    def test_cache_reuses_instance_and_threadsafe(self) -> None:
        cache = TranscriptCache()
        results = []
        threads = [threading.Thread(target=lambda: results.append(cache.get(self.path)))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len({id(r) for r in results}), 1)
        self.assertEqual(len(results[0].messages), 7)

    def test_missing_file(self) -> None:
        tr = Transcript(self.proj / "nope.jsonl")
        tr.refresh()
        self.assertEqual(tr.messages, [])


class PageAroundTest(unittest.TestCase):
    msgs = [{"idx": i, "blocks": [{"type": "text", "text": str(i)}]} for i in range(10)]

    def test_around_window_and_flags(self) -> None:
        out = page(self.msgs, around=5, limit=4, max_block_chars=10)
        self.assertEqual([m["idx"] for m in out["messages"]], [3, 4, 5, 6])
        self.assertTrue(out["has_more_before"] and out["has_more_after"])
        out = page(self.msgs, around=0, limit=4, max_block_chars=10)
        self.assertEqual((out["messages"][0]["idx"], out["has_more_before"]), (0, False))

    def test_tail_has_no_more_after(self) -> None:
        self.assertFalse(page(self.msgs, limit=3, max_block_chars=10)["has_more_after"])
        self.assertTrue(page(self.msgs, after=2, limit=3, max_block_chars=10)["has_more_after"])

    def test_conflicting_modes_rejected(self) -> None:
        with self.assertRaises(ValueError):
            page(self.msgs, before=3, around=2, limit=3, max_block_chars=10)


class PageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.msgs = [{"idx": i, "blocks": [{"type": "text", "text": str(i)}]} for i in range(10)]

    def idxs(self, **kw) -> list[int]:
        result = page(self.msgs, before=kw.get("before"), after=kw.get("after"),
                      limit=kw.get("limit", 3), max_block_chars=100)
        return [m["idx"] for m in result["messages"]]

    def test_default_last(self) -> None:
        self.assertEqual(self.idxs(), [7, 8, 9])
        out = page(self.msgs, before=None, after=None, limit=3, max_block_chars=100)
        self.assertEqual((out["total"], out["has_more_before"]), (10, True))

    def test_before(self) -> None:
        self.assertEqual(self.idxs(before=5), [2, 3, 4])
        self.assertEqual(self.idxs(before=2), [0, 1])
        out = page(self.msgs, before=2, after=None, limit=3, max_block_chars=100)
        self.assertFalse(out["has_more_before"])

    def test_after(self) -> None:
        self.assertEqual(self.idxs(after=3), [4, 5, 6])
        self.assertEqual(self.idxs(after=9), [])

    def test_all_fit(self) -> None:
        out = page(self.msgs, before=None, after=None, limit=50, max_block_chars=100)
        self.assertFalse(out["has_more_before"])
        self.assertEqual(len(out["messages"]), 10)

    def test_truncation_does_not_mutate(self) -> None:
        msgs = [{"idx": 0, "blocks": [
            {"type": "text", "text": "a" * 20},
            {"type": "tool_result", "text": "b" * 20},
            {"type": "tool_use", "id": "t", "name": "X", "input": {"k": "c" * 50}},
            {"type": "tool_use", "id": "u", "name": "X", "input": {"k": 1}},
        ]}]
        out = page(msgs, before=None, after=None, limit=5, max_block_chars=10)
        blocks = out["messages"][0]["blocks"]
        self.assertEqual((blocks[0]["text"], blocks[0]["truncated"]), ("a" * 10, True))
        self.assertTrue(blocks[1]["truncated"])
        self.assertNotIn("input", blocks[2])
        self.assertEqual(len(blocks[2]["input_preview"]), 10)
        self.assertTrue(blocks[2]["truncated"])
        self.assertEqual(blocks[3]["input"], {"k": 1})
        self.assertNotIn("truncated", blocks[3])
        self.assertEqual(len(msgs[0]["blocks"][0]["text"]), 20)
        self.assertIn("input", msgs[0]["blocks"][2])


class CacheLruTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = []
        for name in "abc":
            path = Path(tmp.name) / f"{name}.jsonl"
            path.write_text("\n".join(fx.main_transcript_lines()) + "\n")
            self.paths.append(path)
        self.size = self.paths[0].stat().st_size

    def keys(self, cache: TranscriptCache) -> list[str]:
        return [p.stem for p in cache._items]

    def test_evicts_least_recently_used(self) -> None:
        a, b, c = self.paths
        cache = TranscriptCache(self.size * 2 + 1)
        cache.get(a), cache.get(b)
        cache.get(a)  # touch: b is now the oldest
        cache.get(c)
        self.assertEqual(self.keys(cache), ["a", "c"])

    def test_get_updates_order(self) -> None:
        a, b, _ = self.paths
        cache = TranscriptCache(self.size * 10)
        cache.get(a), cache.get(b), cache.get(a)
        self.assertEqual(self.keys(cache), ["b", "a"])

    def test_mru_survives_tiny_budget(self) -> None:
        a, b, _ = self.paths
        cache = TranscriptCache(1)
        cache.get(a), cache.get(b)
        self.assertEqual(self.keys(cache), ["b"])

    def test_evicted_reparses_identically(self) -> None:
        a, b, _ = self.paths
        cache = TranscriptCache(1)
        first = cache.get(a)
        before = first.messages
        cache.get(b)
        self.assertNotIn(a, cache._items)
        self.assertEqual(first.messages, before)  # old reference stays valid
        again = cache.get(a)
        self.assertIsNot(again, first)
        self.assertEqual(again.messages, before)

    def test_scan_does_not_pollute_cache(self) -> None:
        a, b, c = self.paths
        cache = TranscriptCache(self.size * 10)
        hot = cache.get(a)
        self.assertEqual(len(cache.get(b, keep=False).messages), 7)
        cache.get(c, keep=False)
        self.assertEqual(self.keys(cache), ["a"])
        self.assertIs(cache.get(a, keep=False), hot)


class SubagentTest(unittest.TestCase):
    def test_list_subagents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = fx.make_claude_home(Path(tmp))
            subs = list_subagents(home / "projects" / fx.CWD_DIR / fx.SID_A)
            self.assertEqual(len(subs), 1)
            self.assertEqual(subs[0]["agent_id"], fx.AGENT_ID)
            self.assertEqual(subs[0]["agent_type"], "Explore")
            self.assertEqual(subs[0]["tool_use_id"], "toolu_1")
            self.assertTrue(subs[0]["finished"])
            self.assertEqual(list_subagents(Path(tmp) / "none"), [])


if __name__ == "__main__":
    unittest.main()
