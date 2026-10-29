"""A role you applied to stays off the board until the cooldown passes or it is reposted. Offline, fictional data."""
import datetime as dt
import os
import tempfile
import unittest

from cc import store
from cc.rules import job_key
from cc.runs import intake
from tests.test_intake import CFG, PROFILE, posting

TODAY = dt.date(2026, 10, 29)
URL_OLD = "https://www.linkedin.com/jobs/view/4012345678"
URL_NEW = "https://www.linkedin.com/jobs/view/4099999999"


class Reapply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.tmp.name, "t.db"))
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def applied(self, days_ago, status="applied", url=URL_OLD, **extra):
        k = job_key.key("Delta", "Director of Platform")
        d = (TODAY - dt.timedelta(days=days_ago)).isoformat()
        cur = store.get(self.conn, "roles", job_key.doc_id(k))
        store.set_doc(self.conn, "roles", job_key.doc_id(k), dict(
            key=k, company="Delta", title="Director of Platform", link=url, status=status, date_applied=d, date_found=d, **extra),
            if_version=cur["version"] if cur else None)

    def run_intake(self, **kw):
        return intake.intake(self.conn, CFG, PROFILE, [posting(**kw)], TODAY)

    def roles(self):
        return [d["data"] for d in store.list_docs(self.conn, "roles")]

    def test_recent_application_stays_off_the_board_and_counts_the_sighting(self):
        self.applied(10)
        out = self.run_intake(url=URL_OLD)
        self.assertEqual((out["added"], out["held"]), ([], [job_key.key("Delta", "Director of Platform")]))
        r = self.roles()
        self.assertEqual((len(r), r[0]["seen_again"], r[0]["last_seen"]), (1, 1, TODAY.isoformat()))
        self.run_intake(url=URL_OLD)
        self.assertEqual(self.roles()[0]["seen_again"], 2)

    def test_new_posting_id_inside_the_minimum_gap_is_still_held(self):
        self.applied(10)
        self.assertEqual(len(self.run_intake(url=URL_NEW)["held"]), 1)

    def test_repost_with_a_new_id_after_three_weeks_comes_back_flagged(self):
        self.applied(25)
        out = self.run_intake(url=URL_NEW)
        self.assertEqual((len(out["added"]), len(out["reposts"])), (1, 1))
        new = [r for r in self.roles() if r.get("reposted_of")][0]
        self.assertIn("|repost-", new["key"])
        self.assertIn("applied to this role before", new["repost_note"])
        self.assertEqual(len(self.roles()), 2)                       # the applied role is untouched

    def test_same_id_is_not_a_repost_until_the_cooldown_ends(self):
        self.applied(60)
        self.assertEqual(len(self.run_intake(url=URL_OLD)["held"]), 1)
        self.applied(100)
        self.assertEqual(len(self.run_intake(url=URL_OLD)["reposts"]), 1)

    def test_cooldown_is_configurable(self):
        self.applied(40)
        cfg = dict(CFG, reapply_after_days=30)
        out = intake.intake(self.conn, cfg, PROFILE, [posting(url=URL_OLD)], TODAY)
        self.assertEqual(len(out["reposts"]), 1)
        for bad in (0, -5, "30", True, None):
            self.assertEqual(intake.reapply_days({"reapply_after_days": bad}), intake.DEFAULT_REAPPLY_DAYS)

    def test_a_repost_is_not_added_twice(self):
        self.applied(120)
        self.run_intake(url=URL_OLD)
        again = self.run_intake(url=URL_OLD)
        self.assertEqual((again["added"], again["reposts"], again["duplicates"]), ([], [], 1))
        self.assertEqual(len(self.roles()), 2)

    def test_skipped_and_open_roles_never_come_back(self):
        for status in ("skipped (Pay too low)", "not applying", "build on request", "ready"):
            with self.subTest(status=status):
                self.tmp2 = tempfile.TemporaryDirectory()
                conn = store.connect(os.path.join(self.tmp2.name, "t.db"))
                k = job_key.key("Delta", "Director of Platform")
                store.set_doc(conn, "roles", job_key.doc_id(k), dict(key=k, company="Delta", title="Director of Platform",
                                                                     link=URL_OLD, status=status, date_found="2025-01-01"))
                out = intake.intake(conn, CFG, PROFILE, [posting(url=URL_NEW)], TODAY)
                self.assertEqual((out["added"], out["held"], out["reposts"], out["duplicates"]), ([], [], [], 1))
                conn.close()
                self.tmp2.cleanup()

    def test_rejected_and_withdrawn_roles_follow_the_same_cooldown(self):
        for status in ("rejected", "withdrew", "interviewing", "no response"):
            with self.subTest(status=status):
                self.applied(5, status=status, url=URL_OLD)
                self.assertEqual(len(self.run_intake(url=URL_OLD)["held"]), 1)

    def test_hostile_dates_do_not_crash(self):
        k = job_key.key("Delta", "Director of Platform")
        store.set_doc(self.conn, "roles", job_key.doc_id(k), dict(key=k, company="Delta", title="Director of Platform",
                                                                 status="applied", date_applied="garbage", date_found="12345"))
        out = self.run_intake(url=URL_OLD)
        self.assertEqual((len(out["held"]), out["added"]), (1, []))


if __name__ == "__main__":
    unittest.main()
