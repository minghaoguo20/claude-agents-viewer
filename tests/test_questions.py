import json
import tempfile
import unittest
from pathlib import Path

from tests import fixtures as fx
from viewer.questions import DESCRIPTION_MAX, MAX_OPTIONS, QUESTION_MAX, sanitize_questions
from viewer.sources import list_agents


class QuestionsTest(unittest.TestCase):
    def test_whitelist_and_shape(self) -> None:
        job = {"block": {"questions": [{"question": "Q?", "header": "H", "multiSelect": True, "extra": 1,
                                        "options": [{"label": "A", "description": "d", "x": 1}, {"label": "B"}]}]}}
        self.assertEqual(sanitize_questions(job), [
            {"question": "Q?", "header": "H", "multiSelect": True,
             "options": [{"label": "A", "description": "d"}, {"label": "B"}]}])

    def test_tolerates_odd_shapes(self) -> None:
        for job in (None, {}, {"block": None}, {"block": {"questions": "x"}}, {"block": {"questions": [1, None, {}]}},
                    {"block": {"questions": [{"question": 5}]}}):
            self.assertEqual(sanitize_questions(job), [])
        odd = {"block": {"questions": [{"question": "Q", "options": [3, {"label": ""}, "plain"]}]}}
        self.assertEqual(sanitize_questions(odd), [{"question": "Q", "options": [{"label": "plain"}]}])

    def test_clipping(self) -> None:
        opts = [{"label": "L", "description": "d" * 9999}] * (MAX_OPTIONS + 3)
        out = sanitize_questions({"block": {"questions": [{"question": "q" * 9999, "options": opts}]}})[0]
        self.assertEqual(len(out["question"]), QUESTION_MAX)
        self.assertEqual(len(out["options"]), MAX_OPTIONS)
        self.assertEqual(len(out["options"][0]["description"]), DESCRIPTION_MAX)

    def test_agent_exposes_only_when_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = fx.make_claude_home(Path(tmp))
            agents = {a.session_id: a for a in list_agents(home)}
            self.assertEqual(agents[fx.SID_C].questions[0]["question"], "Pick one?")
            self.assertEqual(agents[fx.SID_A].questions, [])
            self.assertNotIn(fx.SECRET_ENV, json.dumps([a.to_dict() for a in agents.values()]))
            state = home / "jobs" / "cccccccc" / "state.json"
            data = json.loads(state.read_text())
            data["state"] = "done"
            state.write_text(json.dumps(data))
            self.assertEqual({a.session_id: a for a in list_agents(home)}[fx.SID_C].questions, [])
