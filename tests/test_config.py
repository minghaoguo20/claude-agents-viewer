import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from viewer.config import PROJECT_ROOT, default_config_path, load_config


def write_ini(directory: Path, host: str = "127.0.0.1") -> Path:
    path = directory / "c.ini"
    path.write_text(f"[server]\nhost = {host}\nport = 1234\n[claude]\nhome = ~/.claude\n"
                    "[viewer]\npage_size = 10\nmax_block_chars = 100\npoll_interval_ms = 500\n")
    return path


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_values_and_home_expansion(self) -> None:
        cfg = load_config(write_ini(self.dir))
        self.assertEqual((cfg.host, cfg.port, cfg.page_size), ("127.0.0.1", 1234, 10))
        self.assertEqual(cfg.claude_home, Path("~/.claude").expanduser())
        self.assertEqual((cfg.max_block_chars, cfg.poll_interval_ms), (100, 500))

    def test_loopback_hosts_accepted(self) -> None:
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertEqual(load_config(write_ini(self.dir, host)).host, host)

    def test_non_loopback_rejected(self) -> None:
        for host in ("0.0.0.0", "192.168.1.5", "example.com", ""):
            with self.assertRaises(ValueError):
                load_config(write_ini(self.dir, host))

    def test_env_override(self) -> None:
        path = write_ini(self.dir)
        with mock.patch.dict(os.environ, {"CAV_CONFIG": str(path)}):
            self.assertEqual(load_config().port, 1234)

    def test_default_config_file_loads(self) -> None:
        config = load_config(PROJECT_ROOT / "config.ini.example")
        self.assertEqual((config.host, config.port), ("127.0.0.1", 8765))

    def test_default_path_falls_back_to_example(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CAV_CONFIG", None)
            with mock.patch.object(Path, "is_file", return_value=False):
                self.assertEqual(default_config_path(), PROJECT_ROOT / "config.ini.example")
            with mock.patch.object(Path, "is_file", return_value=True):
                self.assertEqual(default_config_path(), PROJECT_ROOT / "config.ini")


if __name__ == "__main__":
    unittest.main()
