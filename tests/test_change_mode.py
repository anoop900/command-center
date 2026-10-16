"""Changing mode any time: data is untouched, and an installed schedule is reinstalled only when whether scheduled
runs may use AI flips (only autopilot may). A failed reinstall keeps the mode and says so."""
import http.client
import itertools
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cc import config, modes, scheduler, store, web, wizard
from tests.test_scheduler import FakeOS

TIMES = {"morning": "07:30", "midday": "12:30"}


def snapshot(conn):
    out = {c: [(d["id"], d["version"], json.dumps(d["data"], sort_keys=True)) for d in store.list_docs(conn, c)
              if (c, d["id"]) != wizard.DOC]  # the wizard's own position doc is allowed to move
           for c in store.collections(conn) if c != "runs"}
    return {c: rows for c, rows in out.items() if rows}


class CreateFails(FakeOS):
    """Queries and deletes work, creating a task is refused (the realistic way a reinstall breaks)."""
    def __call__(self, argv, input_text=None):
        if argv[:2] == ["schtasks", "/Create"] and self.fail:
            return 1, "access denied"
        return super().__call__(argv, input_text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cfg = self.d / "config.toml"
        self.conn = store.connect(self.d / "commandcenter.db")
        wizard.load_demo(self.conn)
        self.os = FakeOS()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def start(self, mode, install=True):
        config.set_values(self.cfg, "mode", {"name": mode})
        if install:
            scheduler.install(config.load(self.cfg), TIMES, self.os, "windows", self.d)

    def task_args(self):
        return " ".join(self.os.tasks.values())


class Pairs(Base):
    def test_every_pair_keeps_data_and_syncs_schedule_only_when_ai_flips(self):
        for old, new in itertools.product(modes.MODES, modes.MODES):
            with self.subTest(old=old, new=new):
                self.start(old)
                before = snapshot(self.conn)
                r = wizard.change_mode(self.conn, self.cfg, new, self.os, "windows")
                self.assertEqual(snapshot(self.conn), before)
                self.assertEqual(config.load(self.cfg).mode.name, new)
                flips = modes.schedules_ai(old) != modes.schedules_ai(new)
                self.assertEqual(r["schedule"], "updated" if flips else "unchanged")
                self.assertEqual("--no-ai" in self.task_args(), not modes.schedules_ai(new))


class Cases(Base):
    def test_no_schedule_installed_just_changes_mode(self):
        self.start("autopilot", install=False)
        r = wizard.change_mode(self.conn, self.cfg, "no-ai", self.os, "windows")
        self.assertEqual((r["mode"], r["schedule"]), ("no-ai", "none"))
        self.assertEqual(self.os.tasks, {})

    def test_times_are_kept_on_reinstall(self):
        self.start("autopilot")
        scheduler.install(config.load(self.cfg), {"morning": "06:15", "midday": "13:45"}, self.os, "windows", self.d)
        wizard.change_mode(self.conn, self.cfg, "no-ai", self.os, "windows")
        self.assertEqual(config.load(self.cfg).run.morning, "06:15")
        self.assertIn("06:15", self.task_args())

    def test_failed_reinstall_keeps_mode_and_says_so(self):
        self.start("autopilot")
        self.os.__class__ = CreateFails
        self.os.fail = True
        r = wizard.change_mode(self.conn, self.cfg, "no-ai", self.os, "windows")
        self.assertEqual((r["mode"], r["schedule"]), ("no-ai", "failed"))
        self.assertIn("Setup > Schedule", r["message"])
        self.assertEqual(config.load(self.cfg).mode.name, "no-ai")

    def test_same_mode_twice_is_a_noop(self):
        self.start("chat")
        a = wizard.change_mode(self.conn, self.cfg, "chat", self.os, "windows")
        b = wizard.change_mode(self.conn, self.cfg, "chat", self.os, "windows")
        self.assertEqual((a["schedule"], b["schedule"]), ("unchanged", "unchanged"))

    def test_bad_names_change_nothing(self):
        self.start("chat")
        for bad in (None, 3, "", "AUTOPILOT", "chat\n", ["chat"], "../x"):
            with self.assertRaises(ValueError):
                wizard.change_mode(self.conn, self.cfg, bad, self.os, "windows")
        self.assertEqual(config.load(self.cfg).mode.name, "chat")

    def test_corrupt_config_is_a_config_error(self):
        self.cfg.write_text("[[[nope", encoding="utf-8")
        with self.assertRaises(config.ConfigError):
            wizard.change_mode(self.conn, self.cfg, "chat", self.os, "windows")


class Http(Base):
    def setUp(self):
        super().setUp()
        self.conn.close()
        self.start("autopilot", install=False)
        self.srv = web.make_server(self.d, 0)
        H = self.srv.RequestHandlerClass
        H.sched_runner, H.sched_platform = self.os, "windows"
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.conn = store.connect(self.d / "commandcenter.db")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def post(self, body, headers=None):
        for attempt in (1, 2):  # Windows loopback occasionally resets a fresh connection
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
            try:
                c.request("POST", "/wizard/mode", body, headers or {})
                r = c.getresponse()
                out = r.read()
                break
            except ConnectionError:
                c.close()
                if attempt == 2:
                    raise
        c.close()
        return r.status, json.loads(out)

    def test_switch_over_http_and_guards(self):
        scheduler.install(config.load(self.cfg), TIMES, self.os, "windows", self.d)
        st, j = self.post(b'{"name": "no-ai"}')
        self.assertEqual((st, j["schedule"]), (200, "updated"))
        self.assertEqual(self.post(b'{"name": "nope"}')[0], 400)
        self.assertEqual(self.post(b"[1]")[0], 400)
        self.assertEqual(self.post(b"\xff\x00")[0], 400)
        self.assertEqual(self.post(b'{"name": "chat"}', {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(config.load(self.cfg).mode.name, "no-ai")


if __name__ == "__main__":
    unittest.main()
