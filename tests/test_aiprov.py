"""AI provider step: presets, validation, key handling, local no-key servers, and setup error messages."""
import http.client
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from cc import ai, aiprov, cli, config, secrets, web
from cc.ai import runners


class Provider(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cfg = self.d / "config.toml"
        config.set_mode(self.cfg, "assisted")
        self.store = {}
        for name, fn in (("get", lambda n: self.store.get(n)), ("set", lambda n, v: self.store.update({n: v}) or "keyring")):
            p = mock.patch.object(secrets, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_preset_saves_a_config_that_loads(self):
        for pid in aiprov.PRESETS:
            body = {"preset": pid, "api_model": "m1", "api_base": "https://example.org/v1", "key": ""}
            out = aiprov.save(self.cfg, body)
            self.assertEqual(out["current"], pid, pid)
            config.load(self.cfg)

    def test_key_goes_to_the_secrets_store_and_never_into_config_or_the_reply(self):
        out = aiprov.save(self.cfg, {"preset": "gemini", "api_model": "m", "key": "sk-secret-123"})
        self.assertEqual(self.store[runners.KEY_NAME], "sk-secret-123")
        self.assertNotIn("sk-secret-123", self.cfg.read_text() + json.dumps(out))
        self.assertTrue(out["key_set"])
        self.assertEqual(out["base"], "https://generativelanguage.googleapis.com/v1beta/openai")

    def test_missing_key_is_a_warning_not_a_failure(self):
        self.assertIn("No API key", aiprov.save(self.cfg, {"preset": "openrouter", "api_model": "a/b"})["warning"])
        self.assertNotIn("warning", aiprov.save(self.cfg, {"preset": "ollama", "api_model": "llama"}))

    def test_bad_input_is_refused_and_leaves_config_alone(self):
        before = self.cfg.read_text()
        for bad in (None, [], "x", {}, {"preset": "nope"}, {"preset": 5}, {"preset": "gemini"}, {"preset": "gemini", "api_model": 5},
                    {"preset": "gemini", "api_model": "has space"}, {"preset": "other", "api_model": "m"},
                    {"preset": "other", "api_model": "m", "api_base": "http://example.org"},
                    {"preset": "other", "api_model": "m", "api_base": "https://u:p@example.org"},
                    {"preset": "other", "api_model": "m", "api_base": "https://example.org?x=1"},
                    {"preset": "gemini", "api_model": "m", "key": "a b"}, {"preset": "gemini", "api_model": "m", "key": "k" * 501},
                    {"preset": "gemini", "api_model": "m", "key": 5}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                aiprov.save(self.cfg, bad)
        self.assertEqual(self.cfg.read_text(), before)
        self.assertEqual(self.store, {})

    def test_cli_presets_report_whether_the_program_exists(self):
        with mock.patch("shutil.which", side_effect=lambda n: "/bin/claude" if n == "claude" else None):
            by_id = {p["id"]: p for p in aiprov.options(self.cfg)["presets"]}
        self.assertEqual((by_id["claude-cli"]["installed"], by_id["codex-cli"]["installed"], by_id["gemini"]["installed"]),
                         (True, False, None))

    def test_local_server_needs_no_key_but_a_remote_one_does(self):
        seen = {}

        def send(req, timeout):
            seen["auth"] = req.get_header("Authorization")
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode())

        local = runners.api_runner("openai", "http://localhost:11434/v1", "m", 5, get_secret=lambda n: None, send=send)
        self.assertEqual(local(None, "p"), "hi")
        self.assertIsNone(seen["auth"])
        remote = runners.api_runner("openai", "https://example.org/v1", "m", 5, get_secret=lambda n: None, send=send)
        with self.assertRaises(ai.RunnerError):
            remote(None, "p")
        anthropic_local = runners.api_runner("anthropic", "http://localhost:1/x", "m", 5, get_secret=lambda n: None, send=send)
        with self.assertRaises(ai.RunnerError):
            anthropic_local(None, "p")

    def test_test_call_reports_provider_failures_as_a_message(self):
        conn = sqlite3.connect(":memory:")

        def boom(task, prompt):
            raise ai.RunnerError("HTTP 401")
        out = aiprov.test(self.cfg, conn, boom)
        self.assertEqual((out["ok"], "401" in out["message"]), (False, True))
        self.assertTrue(aiprov.test(self.cfg, conn, lambda t, p: '{"answer": "ok"}')["ok"])
        config.set_mode(self.cfg, "no-ai")
        self.assertFalse(aiprov.test(self.cfg, conn, lambda t, p: '{"answer": "ok"}')["ok"])


class Endpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = web.make_server(Path(self.tmp.name), 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        p = mock.patch.object(secrets, "get", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.srv.RequestHandlerClass.ai_runner = None
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, raw if raw is not None else json.dumps(body if body is not None else {}),
                  {"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse()
        out = r.read()
        c.close()
        return r.status, json.loads(out)

    def test_round_trip_and_hostile_bodies(self):
        st, o = self.call("GET", "/wizard/ai")
        self.assertEqual(st, 200)
        self.assertIn("ollama", [p["id"] for p in o["presets"]])
        st, o = self.call("POST", "/wizard/ai", {"preset": "ollama", "api_model": "llama3"})
        self.assertEqual((st, o["current"], o["model"]), (200, "ollama", "llama3"))
        for bad in ({}, {"preset": "nope"}, [], "x", None, {"preset": "other", "api_model": "m", "api_base": "ftp://x"}):
            st, o = self.call("POST", "/wizard/ai", bad)
            self.assertEqual(st, 400, bad)
            self.assertIn("error", o)

    def test_test_endpoint(self):
        self.assertFalse(self.call("POST", "/wizard/ai/test")[1]["ok"])     # No AI mode: says so, no network call
        config.set_mode(Path(self.tmp.name) / "config.toml", "autopilot")
        self.srv.RequestHandlerClass.ai_runner = staticmethod(lambda t, p: '{"answer": "ok"}')
        self.assertTrue(self.call("POST", "/wizard/ai/test")[1]["ok"])


class SetupErrors(unittest.TestCase):
    def run_cc(self, home, *args):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"CC_HOME": str(home)}), redirect_stderr(err):
            rc = cli.main(list(args))
        return rc, err.getvalue()

    def test_data_folder_that_is_a_file_is_a_short_message_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "afile"
            f.write_text("x")
            for args in (("setup", "--no-open"), ("web",), ("status",), ("init",)):
                rc, err = self.run_cc(f, *args)
                self.assertEqual(rc, 2, args)
                self.assertIn("CC_HOME", err)
                self.assertNotIn("Traceback", err)

    def test_web_creates_the_data_folder_like_setup(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t) / "new"
            with mock.patch("cc.web.make_server", side_effect=OSError("busy")):
                rc, _ = self.run_cc(home, "web")
            self.assertEqual(rc, 1)
            self.assertTrue((home / "config.toml").exists())


if __name__ == "__main__":
    unittest.main()
