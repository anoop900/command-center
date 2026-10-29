"""Reasons for not applying teach the right thing; roles added by hand explain why they were missed. Offline, fictional data."""
import datetime as dt
import os
import re
import tempfile
import unittest

from cc import api, learned, reasons, store

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = dt.date(2026, 10, 29)


def role(i, title="Plant Manager", company="Delta", **kw):
    return {"id": f"r{i}", "data": dict({"key": f"{company}|{title}{i}", "title": title, "company": company, "source": "board",
                                         "location": "Toronto, ON"}, **kw)}


class Reasons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.tmp.name, "t.db"))
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def test_page_offers_exactly_the_reasons_the_code_classifies(self):
        html = open(os.path.join(HERE, "..", "web", "index.html"), encoding="utf-8").read()
        listed = re.findall(r"'([^']+)'", re.search(r"const SKIP = \[(.*?)\];", html).group(1))
        self.assertEqual(tuple(listed), reasons.LABELS)
        for label in reasons.LABELS:
            self.assertNotEqual(reasons.group_of(label), "other", label)

    def test_each_reason_teaches_the_right_kind_of_signal(self):
        kinds = {l: learned.kind_of("skipped", l) for l in reasons.LABELS}
        self.assertEqual({l for l, k in kinds.items() if k == "not_fit"},
                         {"Not a match", "Too technical / coding", "Level wrong", "Wrong industry", "Not interested"})
        self.assertEqual(kinds["Already applied elsewhere"], "")
        self.assertEqual(kinds["Posting expired or duplicate"], "")
        self.assertEqual(kinds["Salary not shown"], "pref")
        self.assertEqual(learned.kind_of("skipped", "my own words"), "")
        self.assertEqual(learned.kind_of("skipped", None), "")

    def test_repeated_pay_reasons_become_a_suggestion_not_a_rule(self):
        for i in range(2):
            learned.record(self.conn, role(i), "skipped", "Pay too low", TODAY)
        self.assertEqual(learned.view(self.conn)["prefs"], [])               # two is not a pattern yet
        learned.record(self.conn, role(3), "skipped", "Salary not shown", TODAY)
        prefs = learned.view(self.conn)["prefs"]
        self.assertEqual([(p["group"], p["n"]) for p in prefs], [("pay", 3)])
        self.assertEqual(learned.train(self.conn)["rules"], [])               # a pref never becomes a title rule
        learned.forget(self.conn, prefs[0]["id"])
        self.assertTrue(learned.view(self.conn)["prefs"][0]["forgotten"])

    def test_place_and_company_group_by_value(self):
        for i in range(3):
            learned.record(self.conn, role(i, location=f"Calgary, AB"), "skipped", "Location", TODAY)
        learned.record(self.conn, role(9, location="Toronto, ON"), "skipped", "Location", TODAY)
        for i in range(10, 12):
            learned.record(self.conn, role(i, company="Acme"), "skipped", "Company / culture", TODAY)
        got = {(p["group"], p["id"]) for p in learned.view(self.conn)["prefs"]}
        self.assertEqual(got, {("place", "pref:place:calgary"), ("company", "pref:company:acme")})

    def test_hostile_signal_values_do_not_crash(self):
        r = role(1, title=None, company=object(), location=["x"], source=7)
        learned.record(self.conn, r, "skipped", "x" * 5000, TODAY)
        learned.record(self.conn, r, "gate_skip", "Pay too low\x00" * 100, TODAY)
        learned.view(self.conn)

    def test_manual_add_records_how_it_was_found_and_why_it_was_missed(self):
        out = api.add_manual_role(self.conn, "Delta", "Director of Platform", link="https://x.example/j/1", found_via="LinkedIn")
        self.assertTrue(out["created"])
        sig = store.get(self.conn, "signals", out["id"] + "-missed")["data"]
        self.assertEqual((sig["via"], sig["why"]), ("linkedin", "never seen"))
        self.assertEqual(store.get(self.conn, "roles", out["id"])["data"]["found_via"], "LinkedIn")

    def test_title_the_rules_would_hide_is_reported_as_a_rule_problem(self):
        learned.set_list(self.conn, "exclude", ["intern"])
        out = api.add_manual_role(self.conn, "Delta", "Platform Intern", link="https://x.example/j/2")
        why = store.get(self.conn, "signals", out["id"] + "-missed")["data"]["why"]
        self.assertTrue(why.startswith("rules"), why)
        self.assertEqual(learned.view(self.conn)["missed"][0]["id"], "missed:rules")

    def test_two_roles_from_one_unseen_source_suggest_adding_that_source_and_keep_words(self):
        for i, t in enumerate(("Treasury Analyst", "Treasury Manager")):
            api.add_manual_role(self.conn, f"Co{i}", t, link=f"https://x.example/j/{i + 10}", found_via="Recruiter")
        m = {h["id"]: h for h in learned.view(self.conn)["missed"]}
        self.assertIn("missed:via:recruiter", m)
        self.assertEqual(m["missed:terms"]["add_include"], ["treasury"])

    def test_duplicate_manual_add_says_what_happened_to_the_role(self):
        store.set_doc(self.conn, "roles", "delta-director-of-platform",
                      {"company": "Delta", "title": "Director of Platform", "key": "delta|director of platform", "status": "skipped (Pay too low)"})
        out = api.add_manual_role(self.conn, "Delta", "Director of Platform", link="https://x.example/j/3")
        self.assertEqual((out["created"], out["was"]), (False, "skipped (Pay too low)"))

    def test_found_via_must_be_text(self):
        with self.assertRaises(ValueError):
            api.add_manual_role(self.conn, "Delta", "Director", link="https://x.example/j/4", found_via=["x"])


if __name__ == "__main__":
    unittest.main()


class Feeds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.tmp.name, "t.db"))
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def test_counts_add_up_across_runs_and_a_mostly_failing_feed_gets_a_hint(self):
        f = {"seen": 6, "added": 1, "dup": 0, "held": 0, "gated": 5, "gates": {"pay": 5}}
        learned.record_feeds(self.conn, {"LinkedIn": f})
        self.assertEqual(learned.feed_hints(self.conn), [])               # 6 is below the minimum
        learned.record_feeds(self.conn, {"LinkedIn": f})
        h = learned.feed_hints(self.conn)
        self.assertEqual((len(h), h[0]["n"]), (1, 10))
        self.assertIn("failed your pay rule", h[0]["text"])
        self.assertEqual(learned.view(self.conn)["feeds"][0]["seen"], 12)

    def test_hostile_feed_data_is_bounded(self):
        learned.record_feeds(self.conn, {f"s{i}": {"seen": 1, "added": "x", "gates": {"pay": 1}} for i in range(50)})
        self.assertLessEqual(len(learned.feed_totals(self.conn)), 20)
        learned.record_feeds(self.conn, None)
        learned.record_feeds(self.conn, {})
