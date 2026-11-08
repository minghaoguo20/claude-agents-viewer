import builtins
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests import fixtures as fx
from viewer.config import Config
from viewer.server import make_server
from viewer.sources import list_agents
from viewer.transcript import Transcript

VIEWER_DIR = Path(__file__).resolve().parent.parent / "viewer"
FORBIDDEN = [
    (re.compile(r"""\bopen\([^)]*,\s*(?:mode\s*=\s*)?["'][^"']*[wax+][^"']*["']"""), "open() write mode"),
    (re.compile(r"\.open\(\s*(?:mode\s*=\s*)?[\"'][^\"']*[wax+]"), "Path.open write mode"),
    (re.compile(r"\.write_text\b"), "write_text"),
    (re.compile(r"\.write_bytes\b"), "write_bytes"),
    (re.compile(r"\.unlink\b"), "unlink"),
    (re.compile(r"\.rmdir\b"), "rmdir"),
    (re.compile(r"\.mkdir\b"), "mkdir"),
    (re.compile(r"\.rename\b"), "rename"),
    (re.compile(r"(?<!dataclasses\.)\breplace\("), "replace("),
    (re.compile(r"\bshutil\."), "shutil"),
    (re.compile(r"\bos\.(?:remove|unlink|rename|replace|rmdir|mkdir|makedirs|chmod)\b"), "os write"),
]


class StaticScanTest(unittest.TestCase):
    def test_no_write_operations(self) -> None:
        problems = []
        for src in sorted(VIEWER_DIR.glob("*.py")):
            for no, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                problems += [f"{src.name}:{no}: {label}" for rx, label in FORBIDDEN
                             if rx.search(code)]
        self.assertEqual(problems, [])

    def test_no_absolute_paths(self) -> None:
        rx = re.compile(r"""["']/(?:home|root|mnt|etc|usr|tmp|var)/""")
        for src in VIEWER_DIR.glob("*.py"):
            self.assertIsNone(rx.search(src.read_text(encoding="utf-8")), src.name)


class KeyFileTest(unittest.TestCase):
    def test_key_files_never_opened(self) -> None:
        opened: list[str] = []
        real_open = builtins.open

        def spy(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            home = fx.make_claude_home(Path(tmp))
            cfg = Config("127.0.0.1", 0, home, 50, 100, 1000)
            server = make_server(cfg, Path(tmp))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with mock.patch("builtins.open", spy), \
                        mock.patch.object(Path, "open", autospec=True,
                                          side_effect=lambda self, *a, **k: opened.append(str(self)) or real_open(self, *a, **k)):
                    list_agents(home)
                    Transcript(home / "projects" / fx.CWD_DIR / f"{fx.SID_A}.jsonl").refresh()
                    import http.client
                    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
                    for route in ("/api/agents", "/api/stats", "/api/search?q=hello",
                                  f"/api/agents/{fx.SID_A}/timeline"):
                        conn.request("GET", route)
                        conn.getresponse().read()
                    conn.close()
            finally:
                server.shutdown()
                server.server_close()
        self.assertTrue(opened)
        self.assertEqual([p for p in opened if p.endswith(".key")], [])
        self.assertEqual([p for p in opened if "daemon" in Path(p).parts], [])

    def test_unreadable_key_does_not_matter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = fx.make_claude_home(Path(tmp))
            for key in (home / "sessions").glob("*.key"):
                key.chmod(0)
            self.assertEqual(len(list_agents(home)), 3)
            for key in (home / "sessions").glob("*.key"):
                key.chmod(0o600)


if __name__ == "__main__":
    unittest.main()
