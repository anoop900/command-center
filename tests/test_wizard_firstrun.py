"""Wizard step 7: one no-AI morning run from the wizard. Demo data blocks it, fetch problems become notes,
a second run at the same time is refused, and bad input never gives a 5xx."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cc import store, web, wizard


def fake(errors=(), msgs=None):
    return lambda cfg, mode, contact_emails=(): {"msgs": msgs, "ics_text": None, "score_cfg": None, "profile": "",
                                                 "errors": list(errors)}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cfg = self.d / "config.toml"
        self.conn = store.connect(self.d / "commandcenter.db")
        self.tr = self.d / "tracker.xlsx"

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class FirstRun(Base):
    def test_runs_and_reports_counts(self):
        r = wizard.first_run(self.conn, self.cfg, fake(), self.tr)
        self.assertEqual((r["roles"], r["errors"], r["mail"], r["calendar"]), (0, [], False, False))
        self.assertIsNotNone(store.get(self.conn, "runs", next(d["id"] for d in store.list_docs(self.conn, "runs")))["data"].get("finished"))

    def test_fetch_errors_are_reported_and_run_still_finishes(self):
        r = wizard.first_run(self.conn, self.cfg, fake(["Mail fetch failed: timeout"]), self.tr)
        self.assertEqual(r["errors"], ["Mail fetch failed: timeout"])

    def test_repeat_run_is_fine(self):
        wizard.first_run(self.conn, self.cfg, fake(), self.tr)
        wizard.first_run(self.conn, self.cfg, fake(), self.tr)

    def test_demo_data_blocks_the_run_until_cleared(self):
        wizard.load_demo(self.conn)
        with self.assertRaisesRegex(ValueError, "Remove the demo"):
            wizard.first_run(self.conn, self.cfg, fake(), self.tr)
        wizard.clear_demo(self.conn)
        wizard.first_run(self.conn, self.cfg, fake(), self.tr)

    def test_second_run_while_one_is_going_is_refused(self):
        wizard._RUNNING.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "already in progress"):
                wizard.first_run(self.conn, self.cfg, fake(), self.tr)
        finally:
            wizard._RUNNING.release()
        wizard.first_run(self.conn, self.cfg, fake(), self.tr)  # the lock was not left held

    def test_a_crashing_source_releases_the_lock(self):
        def boom(cfg, mode, contact_emails=()):
            raise RuntimeError("x")
        with self.assertRaises(RuntimeError):
            wizard.first_run(self.conn, self.cfg, boom, self.tr)
        wizard.first_run(self.conn, self.cfg, fake(), self.tr)

    def test_corrupt_config_is_a_clear_error(self):
        self.cfg.write_text("[[[not toml", encoding="utf-8")
        with self.assertRaises(Exception) as cm:
            wizard.first_run(self.conn, self.cfg, fake(), self.tr)
        self.assertEqual(type(cm.exception).__name__, "ConfigError")

    def test_finish_marks_last_step_done(self):
        s = wizard.finish(self.conn)
        self.assertIn(wizard.STEPS - 1, s["done"])
        self.assertEqual(wizard.finish(self.conn)["done"], s["done"])


class Http(Base):
    def setUp(self):
        super().setUp()
        self.srv = web.make_server(self.d, 0)
        self.srv.RequestHandlerClass.gather = staticmethod(fake(["Calendar fetch failed: x"]))
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, path, body=b"{}", headers=None):
        for attempt in (1, 2):  # Windows loopback occasionally resets a fresh connection
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
            try:
                c.request("POST", path, body, headers or {})
                r = c.getresponse()
                out = r.read()
                break
            except ConnectionError:
                c.close()
                if attempt == 2:
                    raise
        c.close()
        return r.status, json.loads(out)

    def test_run_finish_and_guards(self):
        st, j = self.call("/wizard/firstrun")
        self.assertEqual((st, j["errors"]), (200, ["Calendar fetch failed: x"]))
        self.assertEqual(self.call("/wizard/firstrun", b"[1]")[0], 400)
        self.assertEqual(self.call("/wizard/firstrun", b"\xff\x00")[0], 400)
        self.assertEqual(self.call("/wizard/firstrun", headers={"Origin": "http://evil.example"})[0], 403)
        st, j = self.call("/wizard/finish")
        self.assertIn(7, j["done"])

    def test_demo_blocks_over_http(self):
        self.call("/wizard/demo")
        st, j = self.call("/wizard/firstrun")
        self.assertEqual(st, 400)
        self.assertIn("demo", j["error"])


if __name__ == "__main__":
    unittest.main()
