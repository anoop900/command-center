"""Tests for the no-AI morning/midday run: actions, reminders, board docs, idempotency. Fictional data."""
import datetime as dt
import os
import tempfile
import unittest

from cc import store
from cc.rules import job_key, reminders
from cc.runs import actions, board, morning

TODAY = dt.date(2026, 10, 12)  # a Monday
NOW = dt.datetime(2026, 10, 12, 7, 30)


def role(co, title, **kw):
    r = {"key": job_key.key(co, title), "date_found": "2026-10-08", "company": co, "title": title, "status": "build on request",
         "decision": "queued", "fit_pct": "", "pay": "", "location": "Toronto", "link": "https://example.com/j/1"}
    r.update(kw)
    return r


class Stage(unittest.TestCase):
    def test_blank_status_is_new_not_dropped(self):
        self.assertEqual(board.stage({"status": "", "decision": ""}), "new")
        self.assertEqual(board.stage({"status": "applied"}), "applied")
        self.assertIsNone(board.stage({"status": "something odd"}))

    def test_ready_without_a_kit_is_new(self):
        rows = [{"company": "A", "title": "x", "status": "ready to apply", "date_found": "2026-10-09"},
                {"company": "B", "title": "y", "status": "ready to apply", "date_found": "2026-10-09", "kit": "d\B_y"}]
        got = {r["company"]: r["stage"] for r in board.build_roles(rows, {}, dt.date(2026, 10, 10))}
        self.assertEqual(got, {"A": "new", "B": "ready"})


class Runs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.tmp.name, "t.db"))
        for r in (role("Acme", "Director of Platform"),
                  role("Beta", "Head of Cloud", status="applied", decision="", date_applied="2026-10-05", follow_up="2026-10-12"),
                  role("Gamma", "VP Infrastructure", status="interviewing (round 2)", decision=""),
                  role("Delta", "Old Role", date_found="2026-08-01"),
                  role("Eps", "Ready Role", status="ready to apply", decision="", fit_pct="85%", date_found="2026-10-09", kit="2026-10-09\Eps_Ready")):
            store.set_doc(self.conn, "roles", job_key.doc_id(r["key"]), r)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def doc(self, c, i):
        return store.get(self.conn, c, i)["data"]

    def test_reminders(self):
        rows = [d["data"] for d in store.list_docs(self.conn, "roles")]
        kinds = {x["type"] for x in reminders.reminders(rows, TODAY)}
        self.assertEqual(kinds, {"follow up", "interview check-in", "packet ready, not applied"})
        self.assertEqual(reminders.reminders(rows, TODAY)[0]["p"], 1)

    def test_morning_fills_board_docs(self):
        out = morning.run(self.conn, "morning", TODAY, NOW)
        roles = self.doc("board", "roles")["roles"]
        self.assertEqual(out["roles"], 4)  # stale new role dropped
        self.assertEqual({r["stage"] for r in roles}, {"new", "applied", "interviewing", "ready"})
        t = self.doc("board", "today")
        self.assertEqual(t["run"], "morning")
        self.assertNotIn("No AI", t["summary"])
        types = [x["type"] for x in t["todos"]]
        self.assertEqual(sorted(types), ["apply", "followup", "interview"])
        self.assertEqual(t["week"]["totals"]["applied"], 0)
        self.assertEqual(t["week"]["targets"]["applied"], 10)

    def test_second_run_writes_nothing(self):
        morning.run(self.conn, "morning", TODAY, NOW)
        v = [store.get(self.conn, "board", i)["version"] for i in ("roles", "today")]
        morning.run(self.conn, "morning", TODAY, NOW.replace(hour=9))
        self.assertEqual([store.get(self.conn, "board", i)["version"] for i in ("roles", "today")], v)

    def test_actions_status_followup_and_dismiss(self):
        k = job_key.key("Acme", "Director of Platform")
        for i, a in enumerate((
                {"key": k, "action": "applied", "status": "applied", "at": "2026-10-12"},
                {"key": job_key.key("Beta", "Head of Cloud"), "action": "followed_up", "at": "2026-10-12"},
                {"key": "todo:fu-x", "action": "done"},
                {"key": "no|such", "action": "applied", "status": "applied", "at": "2026-10-12"},
                {"key": k, "action": "build_now"})):
            store.set_doc(self.conn, "actions", f"a{i}", a)
        res = actions.apply_actions(self.conn, TODAY)
        acme = self.doc("roles", job_key.doc_id(k))
        self.assertEqual((acme["status"], acme["date_applied"], acme["follow_up"]), ("applied", "2026-10-12", "2026-10-19"))
        self.assertEqual(self.doc("roles", "beta-head-of-cloud")["follow_up"], "2026-10-19")
        self.assertEqual(res["dismissed"], ["fu-x"])
        self.assertEqual(len(res["notes"]), 1)  # unmatched press reported, not raised
        self.assertEqual([d["id"] for d in store.list_docs(self.conn, "actions")], ["a4"])  # build_now left for M2

    def test_dismissed_todo_stays_gone_all_day(self):
        morning.run(self.conn, "morning", TODAY, NOW)
        tid = next(x["id"] for x in self.doc("board", "today")["todos"] if x["type"] == "followup")
        store.set_doc(self.conn, "actions", "d1", {"key": "todo:" + tid, "action": "done"})
        morning.run(self.conn, "midday", TODAY, NOW.replace(hour=12))
        morning.run(self.conn, "midday", TODAY, NOW.replace(hour=13))
        self.assertNotIn(tid, [x["id"] for x in self.doc("board", "today")["todos"]])

    def test_notes_capped_and_link_sanitised(self):
        rows = [role("Z", "T", link="javascript:x")]
        r, t = board.build_docs(rows, {"notes": [{"text": str(i), "kind": "fyi"} for i in range(9)]}, TODAY, NOW)
        self.assertEqual((len(t["notes"]), r["roles"][0]["link"]), (6, ""))


class MaxRoleAge(unittest.TestCase):
    rows = [role("Old", "New Role", date_found="2026-09-20"),
            role("Old2", "Skipped Role", date_found="2026-09-20", status="skipped", decision=""),
            role("Old3", "Applied Role", date_found="2026-09-20", status="applied", decision="", date_applied="2026-09-25")]

    def names(self, **kw):
        return sorted(r["company"] for r in board.build_roles(self.rows, {}, TODAY, **kw))

    def test_setting_validated_default_21(self):
        got = [board.max_age(c) for c in (None, {}, {"max_role_age_days": 7}, {"max_role_age_days": 0},
                                          {"max_role_age_days": -3}, {"max_role_age_days": "9"}, {"max_role_age_days": True})]
        self.assertEqual(got, [21, 21, 7, 21, 21, 21, 21])

    def test_cutoff_edge_and_custom(self):
        # found 22 days before TODAY: hidden at 21, shown at 22; applied roles are never aged out
        self.assertEqual(self.names(), ["Old3"])
        self.assertEqual(self.names(age=22), ["Old", "Old2", "Old3"])
        self.assertEqual(self.names(age=60), ["Old", "Old2", "Old3"])

    def test_docs_carry_age_for_refresh(self):
        r, _ = board.build_docs(self.rows, {}, TODAY, NOW, age=30)
        self.assertEqual((r["max_age_days"], len(r["roles"])), (30, 3))


if __name__ == "__main__":
    unittest.main()
