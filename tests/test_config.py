"""Tests for cc/config.py. Run: python -m unittest discover tests"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cc import cli, config

BAD = [
    ("[nope]\na = 1", "unknown section"),
    ("[mail]\nbogus = \"x\"", "unknown key"),
    ("[mail]\npassword = \"x\"", "keyring"),
    ("[calendar]\nics_url = \"http://x\"", "keyring"),
    ("[budget]\nmax_calls_per_run = \"3\"", "expected int"),
    ("[budget]\nmax_calls_per_run = 0", "1 or more"),
    ("[run]\nmorning = \"25:00\"", "HH:MM"),
    ("[mail", "config.toml"),
]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "h"
        p = mock.patch.dict(os.environ, {"CC_HOME": str(self.home)})
        p.start()
        self.addCleanup(p.stop)

    def write(self, text):
        self.home.mkdir(exist_ok=True)
        (self.home / "config.toml").write_text(text)

    def test_data_dir(self):
        self.assertEqual(config.data_dir(), self.home)
        with mock.patch.dict(os.environ):
            del os.environ["CC_HOME"]
            self.assertEqual(config.data_dir().name, ".commandcenter")

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load(), config.Config())

    def test_init_idempotent_and_keeps_config(self):
        config.init_data_dir()
        self.assertTrue(all((self.home / s).is_dir() for s in config.SUBDIRS))
        self.write('[mode]\nname = "chat"\n')
        config.init_data_dir()
        self.assertEqual(config.load().mode.name, "chat")

    def test_starter_is_valid(self):
        config.init_data_dir()
        self.assertEqual(config.load().budget.max_calls_per_day, 100)

    def test_invalid(self):
        for text, msg in BAD:
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaisesRegex(config.ConfigError, msg):
                    config.load()

    def test_cli_bad_config_exits_2(self):
        self.write("[nope]\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["status"]), 2)
        self.assertIn("config error", err.getvalue())

    def test_doctor_mcp_is_optional(self):
        import importlib.metadata as md
        real = md.version

        def version(name):
            if name == "mcp":
                raise md.PackageNotFoundError(name)
            return real(name)

        out = io.StringIO()
        with mock.patch.object(md, "version", version), contextlib.redirect_stdout(out):
            cli.main(["doctor"])
        mcp_lines = [l for l in out.getvalue().splitlines() if "mcp library" in l]
        self.assertEqual(len(mcp_lines), 1)
        self.assertTrue(mcp_lines[0].startswith("info"))
        self.assertIn("optional", mcp_lines[0])


if __name__ == "__main__":
    unittest.main()
