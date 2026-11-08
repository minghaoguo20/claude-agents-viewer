import unittest

from viewer.prompts import list_prompts, prompt_preview
from viewer.transcript import derive_origin


def text(s: str) -> list[dict[str, str]]:
    return [{"type": "text", "text": s}]


class OriginTest(unittest.TestCase):
    def test_dict_and_string(self) -> None:
        self.assertEqual(derive_origin({"origin": {"kind": "peer"}}, "user", text("x")), "peer")
        self.assertEqual(derive_origin({"isCompactSummary": True, "origin": {"kind": "human"}},
                                       "user", text("This session is being continued")),
                         "compact-summary")
        self.assertEqual(derive_origin({"origin": "task-notification"}, "user", text("<a>")),
                         "task-notification")

    def test_missing_origin(self) -> None:
        self.assertEqual(derive_origin({"isMeta": True}, "user", text("hi")), "system")
        self.assertEqual(derive_origin({}, "user", text("<x>y</x>")), "system")
        self.assertEqual(derive_origin({}, "user", text("[Request interrupted]")), "system")
        self.assertEqual(derive_origin({}, "user", text("hello")), "human")

    def test_non_text_user_is_system(self) -> None:
        blocks = [{"type": "tool_result", "text": "ok"}]
        self.assertEqual(derive_origin({}, "user", blocks), "system")

    def test_malformed_origin_falls_back(self) -> None:
        self.assertEqual(derive_origin({"origin": {}}, "user", text("hi")), "human")

    def test_assistant_is_none(self) -> None:
        self.assertIsNone(derive_origin({"origin": "human"}, "assistant", text("hi")))


class PreviewTest(unittest.TestCase):
    def test_plain_collapses_whitespace(self) -> None:
        self.assertEqual(prompt_preview("  a \n\n b\t c "), "a b c")

    def test_command_with_and_without_args(self) -> None:
        raw = "<command-name>/x</command-name><command-args> a  b </command-args>"
        self.assertEqual(prompt_preview(raw), "/x a b")
        self.assertEqual(prompt_preview("<command-name>/x</command-name>"), "/x")

    def test_pasted_content_closing_tag_with_attrs(self) -> None:
        raw = "<pasted_content id=1 n=2>\nfoo\nbar</pasted_content id=1 n=2>"
        self.assertEqual(prompt_preview(raw), "foo bar")
        self.assertEqual(prompt_preview("<pasted_content>hi</pasted_content>"), "hi")

    def test_cap(self) -> None:
        self.assertEqual(len(prompt_preview("y" * 500)), 200)

    def test_list_prompts_filters_and_orders(self) -> None:
        def m(idx: int, role: str, origin: str | None) -> dict:
            return {"idx": idx, "role": role, "origin": origin, "timestamp": "t",
                    "blocks": text(f"m{idx}")}
        out = list_prompts([m(0, "user", "human"), m(1, "assistant", None),
                            m(2, "user", "system"), m(3, "user", "human")])
        self.assertEqual([p["idx"] for p in out], [0, 3])
        self.assertEqual(out[1], {"idx": 3, "timestamp": "t", "text": "m3"})

    def test_list_prompts_branch_siblings(self) -> None:
        def m(idx: int, text_: str, **extra: object) -> dict:
            return {"idx": idx, "role": "user", "origin": "human", "timestamp": "t",
                    "blocks": text(text_), **extra}
        full = [m(0, "old"), m(1, "new")]
        sib = [{"uuid": "u0", "idx": 0, "leaf": "l0"}, {"uuid": "u1", "idx": 1, "leaf": "l1"}]
        shown = {**full[1], "branch": {"index": 2, "count": 2, "siblings": sib}}
        out = list_prompts([shown], full)
        self.assertEqual(out[0]["branch"], {"index": 2, "count": 2, "siblings": [
            {"uuid": "u0", "idx": 0, "leaf": "l0", "text": "old", "current": False},
            {"uuid": "u1", "idx": 1, "leaf": "l1", "text": "new", "current": True}]})


if __name__ == "__main__":
    unittest.main()
