import json
import tempfile
import unittest
from pathlib import Path

from tests import fixtures as fx
from viewer.sources import CWD_PREFIX_BYTES, find_agent, list_agents, transcript_cwd


class SourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = fx.make_claude_home(Path(tmp.name))

    def by_sid(self) -> dict:
        return {a.session_id: a for a in list_agents(self.home)}

    def test_union_and_dedupe(self) -> None:
        agents = list_agents(self.home)
        self.assertEqual(sorted(a.session_id for a in agents), sorted([fx.SID_A, fx.SID_B, fx.SID_C]))

    def test_orphan_job_ignored(self) -> None:
        self.assertNotIn(fx.SID_ORPHAN, self.by_sid())

    def test_only_background_jobs(self) -> None:
        sids = self.by_sid()
        self.assertNotIn(fx.SID_INTER, sids)  # interactive, parkedJobId
        self.assertNotIn(fx.SID_SPARE, sids)  # spare session

    def test_job_fields_and_no_leaks(self) -> None:
        a = self.by_sid()[fx.SID_A]
        self.assertEqual((a.job_id, a.tempo, a.result, a.tokens),
                         ("aaaaaaaa", "active", "all done", 4242))
        c = self.by_sid()[fx.SID_C]
        self.assertEqual((c.tempo, c.result, c.tokens), (None, None, None))
        self.assertNotIn(fx.SECRET_ENV, json.dumps([x.to_dict() for x in list_agents(self.home)]))

    def test_pinned_first(self) -> None:
        agents = list_agents(self.home)
        self.assertEqual(agents[0].session_id, fx.SID_B)
        self.assertTrue(agents[0].pinned)
        self.assertEqual(agents[1].session_id, fx.SID_A)  # newest updatedAt among the rest

    def test_name_precedence(self) -> None:
        agents = self.by_sid()
        self.assertEqual(agents[fx.SID_A].name, "Job A")  # job beats live and custom title
        self.assertEqual(agents[fx.SID_C].name, fx.SID_C[:8])

    def test_custom_title_and_live_name(self) -> None:
        state = self.home / "jobs" / "aaaaaaaa" / "state.json"
        state.write_text(json.dumps({"state": "working", "sessionId": fx.SID_A}))
        self.assertEqual(self.by_sid()[fx.SID_A].name, "Live A")
        (self.home / "sessions" / f"{__import__('os').getpid()}.json").unlink()
        self.assertEqual(self.by_sid()[fx.SID_A].name, "Custom A")

    def test_live_status_and_fields(self) -> None:
        a = self.by_sid()
        self.assertTrue(a[fx.SID_A].live)
        self.assertEqual(a[fx.SID_A].status, "busy")
        self.assertEqual(a[fx.SID_A].cwd, "/work/a")
        self.assertFalse(a[fx.SID_C].live)  # dead pid
        self.assertEqual(a[fx.SID_C].status, "blocked")  # dead pid: job state
        self.assertEqual(a[fx.SID_B].status, "done")
        self.assertEqual(a[fx.SID_A].intent, "build")

    def test_to_dict_json_safe(self) -> None:
        data = self.by_sid()[fx.SID_A].to_dict()
        json.dumps(data)
        self.assertTrue(data["has_transcript"])
        self.assertNotIn("transcript", data)
        self.assertFalse(self.by_sid()[fx.SID_C].to_dict()["has_transcript"])

    def test_find_agent(self) -> None:
        self.assertEqual(find_agent(self.home, fx.SID_B).name, "Job B")
        self.assertIsNone(find_agent(self.home, "eeeeeeee-0000"))


if __name__ == "__main__":
    unittest.main()


class TranscriptCwdTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "t.jsonl"

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def test_first_cwd_found(self) -> None:
        self.write('{"type": "x"}\nbad json\n{"cwd": "/w/one"}\n{"cwd": "/w/two"}\n')
        self.assertEqual(transcript_cwd(self.path), "/w/one")

    def test_missing_and_none(self) -> None:
        self.assertIsNone(transcript_cwd(None))
        self.assertIsNone(transcript_cwd(self.path))

    def test_partial_last_line_ignored(self) -> None:
        self.write('{"type": "x"}\n{"cwd": "/w/partial"}')
        self.assertIsNone(transcript_cwd(self.path))

    def test_prefix_is_bounded(self) -> None:
        filler = json.dumps({"type": "x", "pad": "a" * 1000}) + "\n"
        body = filler * (CWD_PREFIX_BYTES // len(filler) + 2) + '{"cwd": "/w/late"}\n'
        self.write(body)
        self.assertIsNone(transcript_cwd(self.path))

    def test_agent_fallback_priority(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = fx.make_claude_home(Path(tmp.name))
        proj = next((home / "projects").iterdir())
        for sid in (fx.SID_A, fx.SID_B):
            path = proj / f"{sid}.jsonl"
            path.write_text('{"cwd": "/from/transcript"}\n' + path.read_text(encoding="utf-8"),
                            encoding="utf-8")
        agents = {a.session_id: a for a in list_agents(home)}
        self.assertEqual(agents[fx.SID_A].cwd, "/work/a")  # no state cwd: live wins
        self.assertEqual(agents[fx.SID_B].cwd, "/from/transcript")
        self.assertEqual(agents[fx.SID_C].cwd, "/job/c")  # state.json beats live /work/c
