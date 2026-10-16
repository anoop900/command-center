"""A dashboard button press takes effect at once (actions.settle), not at the next morning run. Fictional data."""
import datetime as dt
import http.client
import json
import os
import tempfile
import threading
import unittest

from cc import store, web
from cc.rules import job_key
from cc.runs import actions, morning

TODAY = dt.date.today()


def role(co, title, **kw):
    r = {"key": job_key.key(co, title), "date_found": TODAY.isoformat(), "company": co, "title": title, "status": "ready to apply",
         "decision": "", "fit_pct": "85%", "location": "Toronto", "link": "https://example.com/j/1", "kit": "d\\" + co}
    r.update(kw)
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        self.conn = store.connect(self.db)
        self.a, self.b = role("Acme", "Director"), role("Beta", "Head")
        for r in (self.a, self.b):
            store.set_doc(self.conn, "roles", job_key.doc_id(r["key"]), r)
        morning.run(self.conn, "morning", TODAY)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def stages(self):
        return {r["company"]: r["stage"] for r in store.get(self.conn, "board", "roles")["data"]["roles"]}

    def todo_ids(self):
        return [t["id"] for t in store.get(self.conn, "board", "today")["data"]["todos"]]

    def press(self, doc_id, **d):
        store.set_doc(self.conn, "actions", doc_id, dict(d, at=TODAY.isoformat()))


class Settle(Base):
    def test_mark_applied_moves_the_role_and_clears_its_apply_todo(self):
        self.assertEqual(self.stages(), {"Acme": "ready", "Beta": "ready"})
        self.assertTrue(any(i.startswith("apply-acme") for i in self.todo_ids()))
        self.press("acme-applied", key=self.a["key"], action="applied", status="applied")
        actions.settle(self.conn, TODAY)
        self.assertEqual(self.stages(), {"Acme": "applied", "Beta": "ready"})
        self.assertFalse(any(i.startswith("apply-acme") for i in self.todo_ids()))
        self.assertTrue(any(i.startswith("apply-beta") for i in self.todo_ids()))
        self.assertEqual(store.list_docs(self.conn, "actions"), [])
        self.assertEqual(store.get(self.conn, "roles", job_key.doc_id(self.a["key"]))["data"]["date_applied"], TODAY.isoformat())
        week = store.get(self.conn, "board", "today")["data"]["week"]
        self.assertEqual(week["totals"]["applied"], 1)
        self.assertEqual(sum(d["applied"] for d in week["days"]), 1)

    def test_week_keeps_mail_counts_when_applications_are_recounted(self):
        doc = store.get(self.conn, "board", "today")
        d = doc["data"]
        d["week"]["days"][TODAY.weekday()]["replies"] = 2
        store.set_doc(self.conn, "board", "today", d, if_version=doc["version"])
        self.press("acme-applied", key=self.a["key"], action="applied", status="applied")
        actions.settle(self.conn, TODAY)
        week = store.get(self.conn, "board", "today")["data"]["week"]
        self.assertEqual((week["totals"]["applied"], week["totals"]["replies"]), (1, 2))

    def test_stale_build_now_does_not_block_or_get_lost(self):
        self.press("acme-build-now", key=self.a["key"], action="build_now")
        self.press("acme-applied", key=self.a["key"], action="applied", status="applied")
        actions.settle(self.conn, TODAY)
        self.assertEqual(self.stages()["Acme"], "applied")
        self.assertEqual([d["id"] for d in store.list_docs(self.conn, "actions")], ["acme-build-now"])  # AI step keeps it

    def test_skip_and_todo_done_and_repeat_are_safe(self):
        self.press("beta-skipped", key=self.b["key"], action="skipped", status="skipped (not relevant)")
        tid = [i for i in self.todo_ids() if i.startswith("apply-acme")][0]
        self.press("todo-x", key="todo:" + tid, action="done")
        actions.settle(self.conn, TODAY)
        actions.settle(self.conn, TODAY)
        self.assertEqual(self.stages(), {"Acme": "ready", "Beta": "skipped"})
        self.assertNotIn(tid, self.todo_ids())
        morning.run(self.conn, "morning", TODAY)  # the next run keeps the result
        self.assertEqual(self.stages(), {"Acme": "ready", "Beta": "skipped"})
        self.assertNotIn(tid, self.todo_ids())

    def test_unmatched_key_and_no_board_do_not_crash(self):
        self.press("ghost", key="nobody|nothing", action="applied", status="applied")
        actions.settle(self.conn, TODAY)
        self.assertEqual(self.stages(), {"Acme": "ready", "Beta": "ready"})
        c2 = store.connect(os.path.join(self.tmp.name, "empty.db"))
        actions.settle(c2, TODAY)  # no board yet: nothing to refresh
        c2.close()


class Http(unittest.TestCase):
    def test_a_dashboard_press_applies_immediately(self):
        with tempfile.TemporaryDirectory() as home:
            conn = store.connect(os.path.join(home, "commandcenter.db"))
            r = role("Acme", "Director")
            store.set_doc(conn, "roles", job_key.doc_id(r["key"]), r)
            morning.run(conn, "morning", TODAY)
            srv = web.make_server(home, port=0)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
                body = json.dumps({"key": r["key"], "action": "applied", "status": "applied", "at": TODAY.isoformat()})
                c.request("PUT", "/api/actions/acme-applied", body, {"Content-Type": "application/json", "If-Match": "*"})
                self.assertEqual(c.getresponse().status, 200)
                c.close()
                got = store.get(conn, "board", "roles")["data"]["roles"][0]
                self.assertEqual(got["stage"], "applied")
                self.assertEqual(store.list_docs(conn, "actions"), [])
            finally:
                srv.shutdown()
                srv.server_close()
                conn.close()


if __name__ == "__main__":
    unittest.main()
