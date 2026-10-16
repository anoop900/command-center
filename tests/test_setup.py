"""`cc setup`: idempotent, falls back to a free port, survives a browser that will not open; the asset lookup
prefers the packaged copy; a built wheel carries the page, extension and demo seed (and no tests or profile)."""
import contextlib
import io
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from cc import assets, cli, config, web

PKG_ROOT = Path(__file__).resolve().parent.parent


def quiet_server(real):
    def make(port=0, **kw):
        srv = real(port=port, **kw)
        srv.serve_forever = lambda: None
        return srv
    return make


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"CC_HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_setup(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(web, "make_server", quiet_server(web.make_server)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["setup", *args])
        return code, out.getvalue(), err.getvalue()

    def test_creates_data_folder_and_is_idempotent(self):
        self.assertEqual(self.run_setup("--no-open", "--port", "0")[0], 0)
        cfg = Path(self.tmp.name) / "config.toml"
        self.assertTrue(cfg.exists())
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n# mine\n", encoding="utf-8")
        self.assertEqual(self.run_setup("--no-open", "--port", "0")[0], 0)
        self.assertIn("# mine", cfg.read_text(encoding="utf-8"))

    def test_busy_port_falls_forward(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        try:
            code, out, _ = self.run_setup("--no-open", "--port", str(busy))
        finally:
            s.close()
        self.assertEqual(code, 0)
        self.assertNotIn(f":{busy}/", out)
        self.assertIn("setup wizard at http://127.0.0.1:", out)

    def test_browser_failure_is_not_fatal(self):
        with mock.patch("webbrowser.open", side_effect=RuntimeError("no browser")):
            self.assertEqual(self.run_setup("--port", "0")[0], 0)

    def test_no_free_port_is_a_clear_error(self):
        with mock.patch.object(web, "make_server", side_effect=OSError("busy")), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["setup", "--no-open", "--port", "9000"]), 1)
        self.assertIn("no free port", err.getvalue())

    def test_bad_port_is_a_message_not_a_traceback(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["setup", "--port", "abc"]), 2)
        self.assertIn("error:", err.getvalue())


class Assets(unittest.TestCase):
    def test_checkout_paths_resolve(self):
        self.assertTrue((assets.find("web") / "index.html").is_file())
        self.assertTrue((assets.find("extension") / "manifest.json").is_file())
        self.assertTrue(assets.find("demo").is_dir())

    def test_packaged_copy_wins_and_unknown_names_fail(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "_assets" / "web").mkdir(parents=True)
            with mock.patch.object(assets, "HERE", Path(t)):
                self.assertEqual(assets.find("web"), Path(t) / "_assets" / "web")
        with self.assertRaises(KeyError):
            assets.find("../etc")


class Wheel(unittest.TestCase):
    def test_wheel_carries_assets_and_no_tests_or_profile(self):
        try:
            import setuptools  # noqa: F401  needed because the build runs without isolation (no network)
        except ImportError:
            self.skipTest("setuptools is not installed in this environment")
        with tempfile.TemporaryDirectory() as t:
            r = subprocess.run([sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", "--no-build-isolation",
                                "-w", t, str(PKG_ROOT)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            names = zipfile.ZipFile(next(Path(t).glob("*.whl"))).namelist()
        for need in ("cc/_assets/web/index.html", "cc/_assets/extension/manifest.json", "cc/_assets/demo/roles"):
            self.assertTrue(any(n.startswith(need) for n in names), need)
        self.assertFalse([n for n in names if "/test/" in n or n.endswith("profile.json") or "/tools/" in n])


if __name__ == "__main__":
    unittest.main()
