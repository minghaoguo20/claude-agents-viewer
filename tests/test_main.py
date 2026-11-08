import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from viewer.__main__ import main, port_arg


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name) / "home"
        home.mkdir()
        self.ini = Path(tmp.name) / "c.ini"
        self.ini.write_text(f"[server]\nhost = 127.0.0.1\nport = 1234\n[claude]\nhome = {home}\n")

    def test_port_overrides_config(self) -> None:
        with mock.patch("viewer.__main__.make_server") as make:
            make.return_value.server_address = ("127.0.0.1", 4321)
            make.return_value.serve_forever.side_effect = KeyboardInterrupt
            self.assertEqual(main(["--config", str(self.ini), "--port", "4321"]), 0)
        self.assertEqual(make.call_args.args[0].port, 4321)

    def test_config_port_used_without_flag(self) -> None:
        with mock.patch("viewer.__main__.make_server") as make:
            make.return_value.server_address = ("127.0.0.1", 1234)
            make.return_value.serve_forever.side_effect = KeyboardInterrupt
            self.assertEqual(main(["--config", str(self.ini)]), 0)
        self.assertEqual(make.call_args.args[0].port, 1234)

    def test_invalid_port_rejected(self) -> None:
        for bad in ("0", "65536", "-1", "abc"):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                main(["--config", str(self.ini), "--port", bad])
        self.assertEqual(port_arg("65535"), 65535)

    def test_port_in_use_returns_1(self) -> None:
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            with self.assertLogs("viewer", "ERROR") as logs:
                code = main(["--config", str(self.ini), "--port", str(port)])
        self.assertEqual(code, 1)
        self.assertIn("already in use", logs.output[0])

    def test_other_oserror_returns_1(self) -> None:
        with mock.patch("viewer.__main__.make_server", side_effect=PermissionError(13, "denied")):
            with self.assertLogs("viewer", "ERROR"):
                self.assertEqual(main(["--config", str(self.ini)]), 1)


if __name__ == "__main__":
    unittest.main()
