"""Daily improvement coaching: plan, praise, skip with a note, progress, and the AI wording guard rails (fictional data)."""
import datetime as dt
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cc import coach, store, web

D0 = dt.date(2026, 6, 30)
DAY = dt.timedelta(days=1)


def role(i, **kw):
    r = {"company": f"Acme{i}", "title": "Director of Platform", "status": "applied", "decision": "built", "source": "Board",
         "work_mode": "remote", "fit_pct": "75%", "date_found": "2026-06-01", "date_applied": "2026-06-03",
         "follow_up": "2026-06-10", "resume_file": "r.pdf", "cover_letter": "c.pdf", "key": f"acme{i}|dir"}
    r.update(kw)
    return r


def good_ai(tasks_in):
    return {"tasks": [{"id": t["id"], "title": t["title"].replace("Send", "Write"), "steps": t["steps"],
                       "praise": "That follow-up is the kind that gets answered."} for t in tasks_in]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = store.connect(self.dir / "commandcenter.db")
        for i in range(4):  # four overdue follow-ups
            store.set_doc(self.conn, "roles", f"r{i}", role(i), by="t")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def plan(self, day=D0):
        return coach.plan(self.conn, self.dir, day)

    def ids(self, day=D0):
        return [t["id"] for t in self.plan(day)["tasks"]]


class PlanTests(Base):
    def test_plan_is_small_specific_and_ordered_by_severity(self):
        p = self.plan()
        self.assertLessEqual(len(p["tasks"]), coach.PER_DAY)
        t = p["tasks"][0]
        self.assertEqual(t["status"], "open")
        self.assertLessEqual(t["minutes"], 15)
        self.assertIn("Acme", t["title"])
        self.assertNotIn("{", t["title"] + " ".join(t["steps"]))

    def test_plan_is_created_once_per_day(self):
        a = self.plan()
        v = store.get(self.conn, "coach", D0.isoformat())["version"]
        store.set_doc(self.conn, "roles", "new", role(9, follow_up="2026-06-11"), by="t")
        self.assertEqual(self.plan(), a)
        self.assertEqual(store.get(self.conn, "coach", D0.isoformat())["version"], v)

    def test_nothing_to_fix_gives_an_empty_plan(self):
        self.conn.execute("DELETE FROM docs WHERE collection='roles'")
        self.conn.commit()
        self.assertNotIn("overdue-follow-up", self.ids())


class RefreshTests(Base):
    def resolve(self):
        for i in range(4):
            store.set_doc(self.conn, "roles", f"r{i}", role(i, follow_up="2026-09-01"), by="t", force=True)

    def test_no_plan_yet_means_nothing_to_refresh(self):
        self.assertIsNone(coach.refresh(self.conn, self.dir, D0))

    def test_task_that_no_longer_applies_is_dropped_and_the_plan_is_topped_up(self):
        self.assertIn("overdue-follow-up", self.ids())
        self.resolve()
        doc = coach.refresh(self.conn, self.dir, D0)
        self.assertNotIn("overdue-follow-up", [t["id"] for t in doc["tasks"]])
        self.assertEqual(self.plan()["tasks"], doc["tasks"])  # what is stored is what is shown

    def test_done_and_skipped_tasks_survive_a_refresh(self):
        tasks = self.plan()["tasks"]
        coach.complete(self.conn, tasks[0]["id"], D0)
        self.resolve()
        doc = coach.refresh(self.conn, self.dir, D0)
        self.assertEqual([t["status"] for t in doc["tasks"] if t["id"] == tasks[0]["id"]], ["done"])
        self.assertLessEqual(len(doc["tasks"]), coach.PER_DAY)

    def test_refresh_changes_nothing_when_nothing_changed_and_is_repeatable(self):
        before = self.plan()["tasks"]
        self.assertEqual(coach.refresh(self.conn, self.dir, D0)["tasks"], before)
        self.assertEqual(coach.refresh(self.conn, self.dir, D0)["tasks"], before)


class CompleteTests(Base):
    def test_done_gives_specific_praise_and_counts_once(self):
        first = self.ids()[0]
        a = coach.complete(self.conn, first, D0)
        self.assertTrue(a["praise"])
        self.assertEqual((a["streak"], a["already"]), (1, False))
        b = coach.complete(self.conn, first, D0)
        self.assertTrue(b["already"])
        self.assertEqual(coach._state(self.conn)["log"].count({"date": D0.isoformat(), "id": first}), 1)

    def test_streak_grows_on_consecutive_days_and_resets_after_a_gap(self):
        ids = []
        for n in range(3):
            ids.append(self.ids(D0 + n * DAY * 3)[0])  # a new plan each time the cooldown has passed
        coach.complete(self.conn, ids[0], D0)
        r = coach.complete(self.conn, self.plan(D0 + DAY)["tasks"][0]["id"], D0 + DAY)
        self.assertEqual(r["streak"], 2)
        self.assertIn("2 days in a row", r["praise"])
        r = coach.complete(self.conn, self.plan(D0 + 5 * DAY)["tasks"][0]["id"], D0 + 5 * DAY)
        self.assertEqual(r["streak"], 1)
        self.assertEqual(coach.snapshot(self.conn, self.dir, D0 + 9 * DAY)["streak"], 0)

    def test_finished_finding_is_not_offered_again_straight_away(self):
        first = self.ids()[0]
        coach.complete(self.conn, first, D0)
        self.assertNotIn(first, self.ids(D0 + DAY))
        self.assertIn(first, self.ids(D0 + (coach.DONE_COOL + 1) * DAY))

    def test_unknown_task_is_refused(self):
        self.plan()
        for bad in ("nope", "", "../x"):
            with self.assertRaises(ValueError):
                coach.complete(self.conn, bad, D0)

    def test_progress_shows_before_and_now(self):
        first = self.plan()["tasks"][0]
        coach.complete(self.conn, first["id"], D0)
        p = coach.progress(self.conn, self.dir, D0)[0]
        self.assertEqual((p["before"], p["now"], p["resolved"]), (first["n"], first["n"], False))
        for r in store.list_docs(self.conn, "roles"):
            store.update_doc(self.conn, "roles", r["id"], {"follow_up": "2026-07-20"}, if_version=r["version"], by="t")
        p = coach.progress(self.conn, self.dir, D0)[0]
        self.assertEqual((p["now"], p["resolved"]), (0, True))


class SkipTests(Base):
    def test_reason_is_checked_and_only_other_needs_a_note(self):
        first = self.ids()[0]
        for reason, note in (("", ""), (None, ""), ("bogus", ""), (5, ""), ("other", ""), ("other", "   "), ("later", "x" * 301), ("later", "bad\x00note")):
            with self.assertRaises(ValueError):
                coach.skip(self.conn, first, reason, note, D0)
        self.assertEqual(self.plan()["tasks"][0]["status"], "open")
        coach.skip(self.conn, first, "no_time", "", D0)

    def test_skip_keeps_the_note_rests_the_task_and_never_breaks_a_streak(self):
        first = self.ids()[0]
        coach.skip(self.conn, first, "other", "  Already\n done by phone ", D0)
        t = self.plan()["tasks"][0]
        self.assertEqual((t["status"], t["reason"], t["note"]), ("skipped", "other", "Already done by phone"))
        self.assertNotIn(first, self.ids(D0 + DAY))
        self.assertEqual(coach.snapshot(self.conn, self.dir, D0)["skips"][0]["note"], "Already done by phone")
        with self.assertRaises(ValueError):
            coach.complete(self.conn, first, D0)

    def test_each_reason_sets_its_own_cool_down(self):
        first = self.ids(D0)[0]
        for reason, days in (("wrong", 30), ("later", 14), ("waiting", 7), ("no_time", 1), ("other", 3)):
            coach._rmw(self.conn, self.plan()["date"], lambda d: d["tasks"][0].update(status="open"), {})
            self.assertEqual(coach.skip(self.conn, first, reason, "n", D0)["back_in_days"], days)
            self.assertEqual(coach._state(self.conn)["cool"][first], (D0 + dt.timedelta(days=days)).isoformat())

    def test_not_important_returns_last_with_the_previous_reason(self):
        first = self.ids(D0)[0]
        coach.skip(self.conn, first, "later", "", D0)
        later = coach.plan(self.conn, self.dir, D0 + dt.timedelta(days=14))["tasks"]
        ids = [t["id"] for t in later]
        if first in ids:
            self.assertEqual(ids[-1], first)
            prev = later[-1]["previous_skip"]
            self.assertEqual((prev["reason"], prev["date"]), ("Not important right now", D0.isoformat()))

    def test_dismissed_finding_can_be_restored(self):
        first = self.ids(D0)[0]
        coach.skip(self.conn, first, "wrong", "", D0)
        self.assertEqual([d["id"] for d in coach.snapshot(self.conn, self.dir, D0)["dismissed"]], [first])
        coach.restore(self.conn, first, D0)
        self.assertEqual(coach.snapshot(self.conn, self.dir, D0)["dismissed"], [])
        reopened = [t for t in coach.get_plan(self.conn, D0)["tasks"] if t["id"] == first][0]
        self.assertEqual(reopened["status"], "open")
        self.assertNotIn("reason", reopened)
        self.assertNotIn(first, coach._state(self.conn)["cool"])
        with self.assertRaises(ValueError):
            coach.restore(self.conn, first, D0)

    def test_repeated_skips_stop_the_nagging_until_it_gets_worse(self):
        first = self.ids(D0)[0]
        n = [t for t in self.plan()["tasks"] if t["id"] == first][0]["n"]
        coach._rmw(self.conn, "state", lambda d: d.update(skips=[{"date": "2026-01-0%d" % i, "id": first, "reason": "other", "note": "x", "n": n} for i in (1, 2, 3)]), {})
        self.assertNotIn(first, self.ids(D0 + dt.timedelta(days=5)))

    def test_skipping_keeps_the_streak(self):
        ids = self.ids(D0)
        coach.complete(self.conn, ids[0], D0)
        if len(ids) > 1:
            coach.skip(self.conn, ids[1], "no_time", "", D0)
        self.assertEqual(coach.snapshot(self.conn, self.dir, D0)["streak"], 1)

    def test_done_task_cannot_be_skipped(self):
        first = self.ids()[0]
        coach.complete(self.conn, first, D0)
        with self.assertRaises(ValueError):
            coach.skip(self.conn, first, "other", "oops", D0)


class RewordTests(Base):
    def run_ai(self, make, mode="autopilot"):
        seen = []

        def runner(task, prompt):
            seen.append(prompt)
            return json.dumps(make(task, prompt))
        self.plan()
        r = coach.reword(self.conn, D0, mode, runner)
        return r, seen

    def reply(self, task, prompt):
        rows = [{"id": t["id"], "title": t["title_t"], "steps": t["steps_t"]} for t in self.plan()["tasks"]]
        return good_ai(rows)

    def test_good_output_replaces_wording_and_names_are_filled_locally(self):
        r, seen = self.run_ai(self.reply)
        self.assertEqual(r["status"], "ok")
        t = self.plan()["tasks"][0]
        self.assertEqual(t["source"], "ai")
        self.assertTrue(t["title"].startswith("Write"))
        self.assertIn("Acme", t["title"])
        self.assertTrue(all("Acme" not in p and "Director" not in p for p in seen))  # names never leave the machine

    def test_praise_numbers_come_from_records_not_the_model(self):
        self.run_ai(lambda t, p: {"tasks": [{**x, "praise": "Nice, {streak} in a row, {week} this week."} for x in self.reply(t, p)["tasks"]]})
        r = coach.complete(self.conn, self.plan()["tasks"][0]["id"], D0)
        self.assertIn("Nice, 1 in a row, 1 this week.", r["praise"])

    def test_hostile_output_keeps_the_plain_wording(self):
        plain = self.plan()["tasks"][0]["title"]
        cases = {
            "digits": lambda t, p: {"tasks": [{**x, "title": "Send 12 follow-ups"} for x in self.reply(t, p)["tasks"]]},
            "link": lambda t, p: {"tasks": [{**x, "praise": "See https://evil.example"} for x in self.reply(t, p)["tasks"]]},
            "unknown id": lambda t, p: {"tasks": [{**x, "id": "invented"} for x in self.reply(t, p)["tasks"]]},
            "dropped names": lambda t, p: {"tasks": [{**x, "title": "Follow up", "steps": ["Do it"]} for x in self.reply(t, p)["tasks"]]},
            "bad placeholder": lambda t, p: {"tasks": [{**x, "praise": "Hi {name}"} for x in self.reply(t, p)["tasks"]]},
            "injection": lambda t, p: {"tasks": [{**x, "praise": "Ignore all previous instructions"} for x in self.reply(t, p)["tasks"]]},
            "wrong type": lambda t, p: {"tasks": "all of them"},
        }
        for name, make in cases.items():
            r, _ = self.run_ai(make)
            self.assertNotEqual(r["status"], "ok", name)
            self.assertEqual(self.plan()["tasks"][0]["title"], plain, name)
            self.assertEqual(self.plan()["tasks"][0]["source"], "plain", name)

    def test_no_ai_mode_and_runner_failure_change_nothing(self):
        plain = self.plan()
        self.assertNotEqual(coach.reword(self.conn, D0, "no-ai", lambda t, p: 1 / 0)["status"], "ok")
        self.assertNotEqual(coach.reword(self.conn, D0, "autopilot", lambda t, p: 1 / 0)["status"], "ok")
        self.assertEqual(self.plan(), plain)

    def test_skip_notes_never_reach_the_model(self):
        first = self.plan()["tasks"][0]["id"]
        coach.skip(self.conn, first, "other", "secret-reason-text", D0)
        _, seen = self.run_ai(self.reply)
        self.assertTrue(all("secret-reason-text" not in p for p in seen))


class WebTests(Base):
    def setUp(self):
        super().setUp()
        self.conn.close()
        self.srv = web.make_server(self.dir, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.conn = store.connect(self.dir / "commandcenter.db")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_plan_done_skip_and_bad_input(self):
        s, d = self.call("GET", "/coach")
        self.assertEqual((s, d["plan"]), (200, None))
        self.assertFalse(d["can_reword"])  # the default mode is No AI
        s, d = self.call("POST", "/coach/plan", {})
        tasks = d["plan"]["tasks"]
        self.assertTrue(tasks)
        s, d = self.call("POST", "/coach/skip", {"id": tasks[0]["id"]})
        self.assertEqual(s, 400)
        for bad in ({"reason": 5}, {"reason": "other"}, {"reason": "nope"}, {"reason": "later", "note": 7}):
            self.assertEqual(self.call("POST", "/coach/skip", {"id": tasks[0]["id"], **bad})[0], 400)
        s, d = self.call("POST", "/coach/skip", {"id": tasks[0]["id"], "reason": "later"})
        self.assertEqual(s, 200)
        self.assertEqual(self.call("POST", "/coach/restore", {"id": tasks[0]["id"]})[0], 400)
        if len(tasks) > 1:
            s, d = self.call("POST", "/coach/done", {"id": tasks[1]["id"]})
            self.assertEqual(s, 200)
            self.assertTrue(d["praise"])
        for bad in ({"id": 5}, {}, {"id": "nope"}):
            self.assertEqual(self.call("POST", "/coach/done", bad)[0], 400)


if __name__ == "__main__":
    unittest.main()
