"""Wizard step 0: state, demo load/clear, and the HTTP endpoints under hostile input."""
import http.client
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cc import config, modes, store, web, wizard


class WizardState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(Path(self.tmp.name) / "c.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_fresh_then_resume(self):
        self.assertTrue(wizard.state(self.conn)["fresh"])
        wizard.go(self.conn, 2, complete=True)
        s = wizard.state(self.conn)
        self.assertEqual((s["step"], s["done"], s["fresh"]), (2, [0], False))
        wizard.go(self.conn, 0)  # re-run an earlier step
        self.assertEqual(wizard.state(self.conn)["step"], 0)

    def test_bad_steps(self):
        for bad in (-1, 8, "1", None, True, 1.5):
            with self.assertRaises(ValueError):
                wizard.go(self.conn, bad)

    def test_corrupt_state_doc_falls_back(self):
        store.set_doc(self.conn, "admin", "wizard", {"step": "x", "done": "y"}, force=True)
        s = wizard.state(self.conn)
        self.assertEqual((s["step"], s["done"]), (0, []))

    def test_demo_idempotent_and_clear(self):
        wizard.load_demo(self.conn)
        before = {(c, r["id"]): r["version"] for c in wizard.USER_DATA for r in store.list_docs(self.conn, c)}
        self.assertTrue(before and wizard.state(self.conn)["demo"])
        wizard.load_demo(self.conn)
        after = {(c, r["id"]): r["version"] for c in wizard.USER_DATA for r in store.list_docs(self.conn, c)}
        self.assertEqual(before, after)
        wizard.clear_demo(self.conn)
        self.assertEqual(sum(len(store.list_docs(self.conn, c)) for c in wizard.USER_DATA), 0)

    def test_demo_refused_over_real_data_and_clear_keeps_real(self):
        store.set_doc(self.conn, "tasks", "mine", {"title": "real"}, force=True)
        with self.assertRaises(ValueError):
            wizard.load_demo(self.conn)
        wizard.clear_demo(self.conn)
        self.assertIsNotNone(store.get(self.conn, "tasks", "mine"))

    def test_complete_profile_switches_the_demo_off(self):
        d = Path(self.tmp.name)
        wizard.load_demo(self.conn, data_dir=d)
        self.assertEqual(wizard.state(self.conn, d)["demo_locked"], False)
        with mock.patch("cc.wizard.profile.completeness", return_value={"percent": 100}):
            self.assertTrue(wizard.state(self.conn, d)["demo_locked"])
            wizard.settle(self.conn, d)  # a loaded demo is cleared the moment the profile completes
            self.assertFalse(wizard.state(self.conn, d)["demo"])
            with self.assertRaises(ValueError) as cm:
                wizard.load_demo(self.conn, data_dir=d)
            self.assertIn("complete", str(cm.exception))
        wizard.load_demo(self.conn, data_dir=d)  # an incomplete profile may still look around
        self.assertTrue(wizard.state(self.conn, d)["demo"])

    def test_settle_keeps_the_demo_while_the_profile_is_incomplete(self):
        d = Path(self.tmp.name)
        wizard.load_demo(self.conn, data_dir=d)
        wizard.settle(self.conn, d)
        self.assertTrue(wizard.state(self.conn, d)["demo"])

    def test_missing_demo_folder(self):
        with self.assertRaises(ValueError):
            wizard.load_demo(self.conn, Path(self.tmp.name) / "nope")


class ModeChoice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.toml"
        self.conn = store.connect(Path(self.tmp.name) / "c.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_set_mode_keeps_rest_of_file(self):
        self.cfg.write_text(config.STARTER.replace("# Command", "# Mine\n# Command"), encoding="utf-8")
        config.set_mode(self.cfg, "chat")
        t = self.cfg.read_text(encoding="utf-8")
        self.assertIn("# Mine", t)
        self.assertIn('host = "imap.example.com"', t)
        self.assertEqual(config.load(self.cfg).mode.name, "chat")

    def test_set_mode_without_file_or_section(self):
        config.set_mode(self.cfg, "assisted")
        self.assertEqual(config.load(self.cfg).mode.name, "assisted")
        self.cfg.write_text('[run]\nmorning = "08:00"\n', encoding="utf-8")
        config.set_mode(self.cfg, "autopilot")
        c = config.load(self.cfg)
        self.assertEqual((c.mode.name, c.run.morning), ("autopilot", "08:00"))

    def test_bad_names_and_corrupt_config_leave_file_alone(self):
        self.cfg.write_text(config.STARTER, encoding="utf-8")
        for bad in ("turbo", "", None, 3, ["chat"], 'x"\n[mail]'):
            with self.assertRaises(ValueError):
                wizard.choose_mode(self.conn, self.cfg, bad)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), config.STARTER)
        self.cfg.write_text("[mode\nname=", encoding="utf-8")
        with self.assertRaises(ValueError):
            config.set_mode(self.cfg, "chat")
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), "[mode\nname=")

    def test_choose_mode_advances_and_data_survives(self):
        wizard.load_demo(self.conn)
        n = len(store.list_docs(self.conn, "roles"))
        wizard.go(self.conn, 1)
        wizard.choose_mode(self.conn, self.cfg, "chat")
        s = wizard.state(self.conn)
        self.assertEqual((s["step"], 1 in s["done"]), (2, True))
        self.assertEqual(len(store.list_docs(self.conn, "roles")), n)

    def test_table_covers_every_mode_and_feature(self):
        t = modes.table()
        self.assertEqual({m["name"] for m in t["modes"]}, set(modes.MODES))
        self.assertEqual([f["key"] for f in t["features"]], list(modes.FEATURES))


class WizardHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.srv = web.make_server(self.tmp.name, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body) if body is not None else "{}")
        for attempt in (1, 2):  # Windows loopback occasionally resets a fresh connection; retry once
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            try:
                c.request(method, path, data, headers or {})
                r = c.getresponse()
                out = r.read()
                break
            except ConnectionError:
                c.close()
                if attempt == 2:
                    raise
        c.close()
        try:
            return r.status, json.loads(out)
        except ValueError:
            return r.status, out

    def test_flow(self):
        self.assertTrue(self.call("GET", "/wizard")[1]["fresh"])
        st, j = self.call("POST", "/wizard/demo")
        self.assertEqual((st, j["demo"]), (200, True))
        self.assertEqual(self.call("POST", "/wizard/demo")[0], 200)
        st, j = self.call("POST", "/wizard/step", {"step": 3, "complete": True})
        self.assertEqual((st, j["step"], j["done"]), (200, 3, [0]))
        st, j = self.call("POST", "/wizard/demo/clear")
        self.assertEqual((st, j["demo"]), (200, False))

    def test_hostile(self):
        for body in ({"step": "1; drop"}, {"step": 99}, {"step": None}, {}, {"step": [1]}):
            self.assertEqual(self.call("POST", "/wizard/step", body)[0], 400)
        for raw in ("not json", "[1]", "null", "\x00\xff"):
            self.assertEqual(self.call("POST", "/wizard/step", raw=raw)[0], 400)
        self.assertEqual(self.call("POST", "/wizard/nope")[0], 404)
        self.assertEqual(self.call("POST", "/wizard/step", headers={"Origin": "http://evil.example"}, body={"step": 1})[0], 403)
        self.assertEqual(self.call("POST", "/wizard/step", headers={"Host": "evil.example"}, body={"step": 1})[0], 403)
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sk:  # no body sent, so no reset race
            req = f"POST /wizard/step HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nContent-Length: abc\r\n\r\n"
            sk.sendall(req.encode())
            self.assertIn(b" 400 ", sk.recv(200).split(b"\r\n")[0])

    def test_mode_endpoints(self):
        st, j = self.call("GET", "/wizard/modes")
        self.assertEqual((st, j["current"]), (200, "no-ai"))
        self.assertEqual(self.call("POST", "/wizard/mode", {"name": "bogus"})[0], 400)
        self.assertEqual(self.call("POST", "/wizard/mode", {"name": "chat"}, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/wizard/mode", {"name": "chat"})[0], 200)
        self.assertEqual(self.call("GET", "/wizard/modes")[1]["current"], "chat")
        (Path(self.tmp.name) / "config.toml").write_text("[[[", encoding="utf-8")
        self.assertEqual(self.call("POST", "/wizard/mode", {"name": "no-ai"})[0], 400)
        self.assertIn("error", self.call("GET", "/wizard/modes")[1])

    def test_demo_refused_with_real_data(self):
        self.call("PUT", "/api/tasks/mine", {"title": "x"}, {"If-Match": "*"})
        st, j = self.call("POST", "/wizard/demo")
        self.assertEqual(st, 400)
        self.assertIn("own data", j["error"])

    def test_demo_refused_and_hidden_once_the_profile_is_complete(self):
        with mock.patch("cc.wizard.profile.completeness", return_value={"percent": 100}):
            st, j = self.call("POST", "/wizard/demo")
            self.assertEqual(st, 400)
            self.assertIn("complete", j["error"])
            self.assertTrue(self.call("GET", "/wizard")[1]["demo_locked"])
        self.assertFalse(self.call("GET", "/wizard")[1]["demo_locked"])


if __name__ == "__main__":
    unittest.main()
