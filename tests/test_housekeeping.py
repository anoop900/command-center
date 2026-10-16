"""Housekeeping: history stays bounded, and a resume built from the profile follows the user's own voice rules."""
import json
import tempfile
import unittest
from pathlib import Path

from cc import profile, store
from cc.kits import content


class History(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = store.connect(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.conn.close)

    def count(self, doc="a"):
        return self.conn.execute("SELECT COUNT(*) FROM history WHERE id=?", (doc,)).fetchone()[0]

    def test_writes_keep_only_the_newest_copies(self):
        for i in range(store.HISTORY_KEEP + 15):
            store.set_doc(self.conn, "board", "a", {"n": i}, by="t", force=True)
        self.assertEqual(self.count(), store.HISTORY_KEEP)
        self.assertEqual(store.history(self.conn, "board", "a", 1)[0]["data"], {"n": store.HISTORY_KEEP + 13})

    def test_housekeeping_trims_old_bloat_and_leaves_other_records(self):
        for i in range(60):
            self.conn.execute("INSERT INTO history(collection,id,data,version,op,at,by) VALUES ('board','a',?,?,'set','x','t')", (f'{{"n":{i}}}', i))
        store.set_doc(self.conn, "board", "b", {"n": 1}, force=True)
        store.set_doc(self.conn, "board", "b", {"n": 2}, force=True)
        self.assertEqual(store.housekeeping(self.conn, keep=10), 50)
        self.assertEqual(self.count("a"), 10)
        self.assertEqual(self.count("b"), 1)
        self.assertEqual(store.housekeeping(self.conn, keep=10), 0)      # repeated runs change nothing


class Voice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        ok = lambda v: {"value": v, "ok": True}
        profile.save(self.home, {"fields": {"firstName": ok("Jordan"), "lastName": ok("Example"), "education": ok("BSc")},
                                 "jobs": [{"employer": "Example Co", "title": "Lead", "dates": "2020 - 2022", "ok": True,
                                           "facts": ["Built pipelines — standardizing patterns", "Leveraging AWS and leveraged Azure"]}],
                                 "skills": {"value": "Python", "ok": True}})

    def bullets(self):
        return json.loads((self.home / "content.json").read_text("utf-8"))["jobs"][0]["bullets"]

    def test_banned_em_dash_and_leverage_are_fixed_when_content_is_built(self):
        (self.home / "qc_rules.json").write_text(json.dumps({"banned_words": ["—", "leverag"]}), "utf-8")
        content.ensure(self.home)
        self.assertEqual(self.bullets(), ["Built pipelines, standardizing patterns", "Using AWS and used Azure"])

    def test_nothing_changes_when_the_user_has_no_such_rule(self):
        (self.home / "qc_rules.json").write_text(json.dumps({"banned_words": ["robust"]}), "utf-8")
        content.ensure(self.home)
        self.assertIn("—", self.bullets()[0])

    def test_tailored_text_is_tidied_too(self):
        (self.home / "qc_rules.json").write_text(json.dumps({"banned_words": ["—", "leverag"]}), "utf-8")
        c = {"headline": "Lead — platform", "profile": "I leverage cloud.", "skills": [["Cloud", "Leveraging AWS"]],
             "jobs": [{"bullets": ["A — b"]}]}
        content.tidy_content(c, self.home)
        self.assertEqual((c["headline"], c["profile"], c["skills"], c["jobs"][0]["bullets"]),
                         ("Lead, platform", "I use cloud.", [["Cloud", "Using AWS"]], ["A, b"]))

    def test_a_missing_or_broken_rules_file_is_fine(self):
        (self.home / "qc_rules.json").write_text("{not json", "utf-8")
        content.ensure(self.home)
        self.assertEqual(len(self.bullets()), 2)


class UnreadableDates(unittest.TestCase):
    def raw(self, dates):
        ok = lambda v: {"value": v, "ok": True}
        return {"fields": {"firstName": ok("Jordan"), "lastName": ok("Example"), "education": ok("BSc")},
                "jobs": [{"employer": "Example Co", "title": "Lead", "dates": dates, "facts": ["Ran it."], "ok": True}],
                "skills": {"value": "Python", "ok": True}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def test_save_asks_for_a_readable_format_but_still_saves(self):
        r = profile.save(self.home, self.raw("Summer 2019 - Fall 2021"))
        self.assertEqual(len(r["date_warnings"]), 1)
        self.assertIn('"Aug 2024 - Present"', r["date_warnings"][0])
        self.assertTrue((self.home / "profile.json").exists())

    def test_readable_or_empty_dates_give_no_warning(self):
        for d in ("2024 Aug - Present", "15.08.2024 to 01.03.2025", ""):
            self.assertEqual(profile.save(self.home, self.raw(d))["date_warnings"], [], d)

    def test_a_build_repeats_the_request(self):
        profile.save(self.home, self.raw("since 2019"))
        self.assertTrue(any("cannot be read" in n for n in content.ensure(self.home)))


if __name__ == "__main__":
    unittest.main()
