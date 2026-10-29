"""30 simulated days of coaching on fictional data: several kinds of user, checked day by day against an independent
count of streaks, weeks, cool-downs and the repeat-skip rule. No clocks, no network, no AI."""
import datetime as dt
import json
import random
import tempfile
import unittest
from pathlib import Path

from cc import coach, store, wizard

START = dt.date(2026, 10, 12)
DAYS = 30
REASON_CODES = [c for c in coach.REASONS if c != "other"]
ONE = dt.timedelta(days=1)


def run_user(conn, data_dir, behave, world=None, days=DAYS):
    """behave(day_index, task) -> ("done",) | ("skip", reason[, note]) | None (leave it). Returns the history."""
    hist = []
    for d in range(days):
        today = START + dt.timedelta(days=d)
        if world:
            world(conn, today)
        before = coach._state(conn)
        plan = coach.plan(conn, data_dir, today)
        assert coach.plan(conn, data_dir, today) == plan, "a second plan call changed the day"
        acted = {}
        for t in plan["tasks"]:
            act = behave(d, t)
            if act is None:
                continue
            if act[0] == "done":
                coach.complete(conn, t["id"], today)
                assert coach.complete(conn, t["id"], today)["already"], "done twice must count once"
                acted[t["id"]] = "done"
            else:
                coach.skip(conn, t["id"], act[1], act[2] if len(act) > 2 else "", today)
                acted[t["id"]] = "skip:" + act[1]
        snap = coach.snapshot(conn, data_dir, today)
        json.dumps(snap)    # the page needs this to serialise
        hist.append({"day": today, "offered": [t["id"] for t in plan["tasks"]], "acted": acted, "snap": snap,
                     "cool": before["cool"]})
    return hist


def check_invariants(tc, hist):
    """What the coaching promises, recomputed from the history instead of read back from the code."""
    done_days, done_log = set(), []
    for h in hist:
        day, ids = h["day"], h["offered"]
        tc.assertLessEqual(len(ids), coach.PER_DAY)
        tc.assertEqual(len(ids), len(set(ids)), "a finding was offered twice in one day")
        for i in ids:
            tc.assertLessEqual(h["cool"].get(i, ""), day.isoformat(), f"{i} offered inside its cool-down on {day}")
        for i, a in h["acted"].items():
            if a == "done":
                done_days.add(day)
                done_log.append((day, i))
        s = h["snap"]
        run, d = 0, day if day in done_days else day - ONE
        while d in done_days:
            run += 1
            d -= ONE
        tc.assertEqual(s["streak"], run, f"streak on {day}")
        tc.assertEqual(s["week"], sum(1 for d0, _ in done_log if day - 6 * ONE <= d0 <= day), f"week on {day}")
        tc.assertGreaterEqual(s["best"], s["streak"])
        for p in s["progress"]:
            tc.assertGreaterEqual(p["before"], 0)
            tc.assertGreaterEqual(p["now"], 0)


class Sim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = store.connect(self.dir / "commandcenter.db")
        wizard.load_demo(self.conn, today=START)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_diligent_user_gets_a_real_streak(self):
        hist = run_user(self.conn, self.dir, lambda d, t: ("done",))
        check_invariants(self, hist)
        self.assertTrue(hist[0]["offered"], "day one offered nothing on the demo data")
        self.assertGreaterEqual(max(h["snap"]["streak"] for h in hist), 5)
        for a, b in zip(hist, hist[1:]):
            self.assertFalse(set(a["acted"]) & set(b["offered"]), "a finished finding came back the next day")

    def test_user_who_skips_everything_is_not_nagged_into_the_ground(self):
        rng = random.Random(7)
        hist = run_user(self.conn, self.dir, lambda d, t: ("skip", rng.choice(REASON_CODES)))
        check_invariants(self, hist)
        counts = {}
        for h in hist:
            for i in h["acted"]:
                counts[i] = counts.get(i, 0) + 1
        for i, n in counts.items():
            self.assertLessEqual(n, coach.REPEAT, f"{i} offered {n} times to someone who always skips it")
        self.assertEqual(hist[-1]["snap"]["streak"], 0)
        self.assertLessEqual(len(coach._state(self.conn)["skips"]), coach.KEEP)
        quiet = [h for h in hist if not h["offered"]]
        self.assertTrue(quiet, "expected the list to go quiet for someone who skips everything")
        for h in quiet:    # an empty card must not claim there is nothing to fix while findings are only resting or dismissed
            self.assertGreater(h["snap"]["resting"] + len(h["snap"]["dismissed"]), 0, h["day"])

    def test_mixed_users_over_many_seeds_keep_every_invariant(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                tmp = tempfile.TemporaryDirectory()
                conn = store.connect(Path(tmp.name) / "commandcenter.db")
                try:
                    wizard.load_demo(conn, today=START)
                    rng = random.Random(seed)

                    def behave(d, t):
                        r = rng.random()
                        if r < 0.45:
                            return ("done",)
                        if r < 0.8:
                            return ("skip", rng.choice(REASON_CODES))
                        if r < 0.9:
                            return ("skip", "other", "not now " + str(d))
                        return None
                    check_invariants(self, run_user(conn, Path(tmp.name), behave))
                    st = coach._state(conn)
                    self.assertLessEqual(len(st["log"]), coach.KEEP)
                    self.assertLessEqual(len(st["skips"]), coach.KEEP)
                finally:
                    conn.close()
                    tmp.cleanup()

    def test_user_who_vanishes_loses_the_streak_but_the_plan_still_builds(self):
        hist = run_user(self.conn, self.dir, lambda d, t: ("done",) if d < 5 or d >= 12 else None)
        check_invariants(self, hist)
        self.assertEqual(hist[4]["snap"]["streak"], 5)
        self.assertEqual(hist[11]["snap"]["streak"], 0)
        self.assertEqual(hist[-1]["snap"]["best"], max(h["snap"]["streak"] for h in hist))

    def test_dismissed_as_irrelevant_stays_away_for_a_month_and_can_be_brought_back(self):
        hist = run_user(self.conn, self.dir, lambda d, t: ("skip", "wrong") if d == 0 else None, days=29)
        gone = list(hist[0]["acted"])
        self.assertTrue(gone)
        for h in hist[1:]:
            self.assertFalse(set(gone) & set(h["offered"]), f"a dismissed finding came back on {h['day']}")
        for i in gone:
            coach.restore(self.conn, i, START + 29 * ONE)
        self.assertEqual(coach._state(self.conn)["dismissed"], {})
        self.assertTrue(coach.plan(self.conn, self.dir, START + 40 * ONE)["tasks"])

    def test_clock_going_backwards_or_a_huge_jump_does_not_crash(self):
        coach.plan(self.conn, self.dir, START)
        for d in (START - 400 * ONE, START + 4000 * ONE, START):
            for t in coach.plan(self.conn, self.dir, d)["tasks"]:
                coach.complete(self.conn, t["id"], d)
            json.dumps(coach.snapshot(self.conn, self.dir, d))
        self.assertGreaterEqual(coach.snapshot(self.conn, self.dir, START)["best"], 1)

    def test_recurring_follow_ups_come_back_about_weekly(self):
        for r in store.list_docs(self.conn, "roles"):
            store.set_doc(self.conn, "roles", r["id"], {**r["data"], "status": "rejected"}, by="sim", force=True)
        for i in range(3):
            store.set_doc(self.conn, "roles", f"sim{i}", {
                "company": f"Sim{i}", "title": "Lead", "status": "applied", "decision": "built", "source": "Board",
                "work_mode": "remote", "fit_pct": "70%", "date_found": "2026-09-01", "date_applied": "2026-09-03",
                "follow_up": "2026-09-10", "resume_file": "r.pdf", "cover_letter": "c.pdf", "key": f"sim{i}|lead"},
                by="t")

        def behave(d, t):
            if t["id"] == "overdue-follow-up":    # doing the work moves the dates a week out
                for r in store.list_docs(self.conn, "roles"):
                    if r["id"].startswith("sim"):
                        store.set_doc(self.conn, "roles", r["id"], {**r["data"], "follow_up": (START + (d + 7) * ONE).isoformat()},
                                      by="sim", force=True)
            return ("done",)
        hist = run_user(self.conn, self.dir, behave)
        check_invariants(self, hist)
        days = [h["day"] for h in hist if "overdue-follow-up" in h["acted"]]
        self.assertGreaterEqual(len(days), 3, days)
        self.assertTrue(all((b - a).days >= 6 for a, b in zip(days, days[1:])), days)


if __name__ == "__main__":
    unittest.main()
