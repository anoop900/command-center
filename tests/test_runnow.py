"""Run now: starts the scheduler's own command, one at a time, and says plainly how it went. Offline: no real process is started."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cc import api, runnow, scheduler, store, web


class FakeProc:
    def __init__(self, argv, rc=None):
        self.argv, self.returncode = argv, rc

    def poll(self):
        return self.returncode


class RunNow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.launched = []
        self.rc = None

        def popen(argv, **kw):
            self.launched.append((argv, kw))
            self.proc = FakeProc(argv, self.rc)
            return self.proc

        handler_attrs = {"run_popen": staticmethod(popen)}
        self.srv = web.make_server(self.dir, 0)
        for k, v in handler_attrs.items():
            setattr(self.srv.RequestHandlerClass, k, v)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        runnow._procs.clear()

    def call(self, method, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, "/run-now", json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read() or b"null")
        c.close()
        return r.status, data

    def test_starts_the_schedulers_own_command_for_this_data_folder(self):
        st, out = self.call("POST", {})
        self.assertEqual((st, out["state"], out["run"]), (200, "running", "morning"))
        argv = self.launched[0][0]
        self.assertEqual(argv[argv.index("run") + 1], "morning")
        self.assertIn(str(self.dir), argv)
        self.assertTrue((self.dir / "logs" / "run-now.log").exists())

    def test_second_press_while_running_is_a_plain_400_and_starts_nothing(self):
        self.call("POST", {})
        st, out = self.call("POST", {})
        self.assertEqual(st, 400)
        self.assertIn("already going", out["error"])
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.call("GET")[1]["state"], "running")

    def test_a_failed_run_says_what_to_do(self):
        self.call("POST", {})
        self.proc.returncode = 1
        st, out = self.call("GET")
        self.assertEqual(out["state"], "failed")
        self.assertIn("Press Run now", out["fix"])
        self.assertNotIn("Traceback", json.dumps(out))

    def test_a_finished_run_reports_ok_and_can_be_started_again(self):
        self.call("POST", {})
        self.proc.returncode = 0
        self.assertEqual(self.call("GET")[1]["state"], "ok")
        self.assertEqual(self.call("POST", {"run": "midday"})[0], 200)

    def test_hostile_bodies_are_clean_errors(self):
        for body in ({"run": "evening"}, {"run": ["morning"]}, {"run": None}, [], "x", 5):
            with self.subTest(body=str(body)):
                self.assertIn(self.call("POST", body)[0], (400, 413))
        self.assertEqual(self.launched, [])

    def test_a_run_started_by_the_scheduler_counts_as_running(self):
        conn = store.connect(self.dir / "commandcenter.db")
        api.run_heartbeat(conn, "morning", "start")
        conn.close()
        self.assertEqual(self.call("GET")[1]["state"], "running")
        self.assertEqual(self.call("POST", {})[0], 400)

    def test_a_dead_start_without_a_finish_is_not_running_forever(self):
        conn = store.connect(self.dir / "commandcenter.db")
        api.run_heartbeat(conn, "morning", "start")
        d = store.get(conn, "runs", store.list_docs(conn, "runs")[0]["id"])
        data = dict(d["data"], started="2020-01-01T00:00:00+00:00")
        store.set_doc(conn, "runs", d["id"], data, if_version=d["version"])
        conn.close()
        self.assertNotEqual(self.call("GET")[1]["state"], "running")


if __name__ == "__main__":
    unittest.main()
