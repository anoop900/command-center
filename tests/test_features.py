"""Review and prep: recorded model answers, no live AI."""
import json
import tempfile
import unittest
from pathlib import Path

from cc import ai, store
from cc.ai import features
from cc.kits.pipeline import KitError

KIT = '{"headline": "Azure Architect", "profile": "Led the Azure migration"}'
REVIEW = {"verdict": "Send after fixes",
          "part_a": [{"file": "kit", "old_string": "Azure Architect", "new_string": "Cloud Architect", "reason": "keyword"}],
          "part_b": {"missed_requirements": "none", "company_angle": "none", "reframing": "none", "voice": "none"},
          "notes": []}
PREP = {"sections": [{"h": "Stories", "items": ["Migration story"]}]}


def runner(d):
    return lambda task, prompt: d if isinstance(d, str) else json.dumps(d)


class Features(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "Candidate_Profile.md").write_text("Azure lead")
        (self.home / "kits" / "r1").mkdir(parents=True)
        (self.home / "kits" / "r1" / "tailor.json").write_text(KIT)
        self.conn = store.connect(self.home / "t.db")
        store.set_doc(self.conn, "roles", "r1", {"company": "Contoso", "title": "Lead"})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def review(self, d=REVIEW, **kw):
        a = dict(role="r1", posting="Azure role", mode="autopilot", r=runner(d))
        a.update(kw)
        return features.review_role(self.conn, a["role"], a["posting"], a["mode"], a["r"], self.home)

    def prep(self, d=PREP, **kw):
        a = dict(role="r1", posting="Azure role", mode="autopilot", r=runner(d))
        a.update(kw)
        return features.prep_role(self.conn, a["role"], a["posting"], a["mode"], a["r"], self.home)

    def test_review_saved(self):
        out = self.review()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(store.get(self.conn, "kitreviews", "r1")["data"]["verdict"], "Send after fixes")

    def test_review_edit_must_quote_text_once(self):
        bad = dict(REVIEW, part_a=[dict(REVIEW["part_a"][0], old_string="not in the kit")])
        out = self.review(bad)
        self.assertEqual(out["status"], "unavailable")
        self.assertIsNone(store.get(self.conn, "kitreviews", "r1"))

    def apply(self, i=0):
        return features.apply_review_edit(self.conn, "r1", i, self.home)

    def test_review_doc_carries_dashboard_fields(self):
        self.review()
        d = store.get(self.conn, "kitreviews", "r1")["data"]
        self.assertEqual((d["key"], d["grounding_issues"], d["suggestions"], d["fixed"], d["applied"]), ("r1", 0, 1, 0, []))

    def test_apply_edit_changes_kit_once(self):
        self.review()
        self.assertEqual(self.apply()["applied"], [0])
        kit = json.loads((self.home / "kits" / "r1" / "tailor.json").read_text())
        self.assertEqual(kit["headline"], "Cloud Architect")
        self.assertEqual(store.get(self.conn, "kitreviews", "r1")["data"]["fixed"], 1)
        with self.assertRaises(KitError):
            self.apply()  # already applied

    def test_apply_refuses_stale_or_bad_edits(self):
        self.review()
        kit = self.home / "kits" / "r1" / "tailor.json"
        kit.write_text('{"headline": "Changed by hand", "profile": "x"}')
        with self.assertRaises(KitError):
            self.apply()  # text no longer there
        for bad in (-1, 1, "0", True, None, 1.0):
            with self.assertRaises(KitError):
                self.apply(bad)
        with self.assertRaises(KitError):
            features.apply_review_edit(self.conn, "nope", 0, self.home)

    def test_apply_cannot_change_structure_or_edit_resume(self):
        evil = dict(REVIEW["part_a"][0], new_string='x", "extra": "y')
        self.review(dict(REVIEW, part_a=[evil]))
        kit = self.home / "kits" / "r1" / "tailor.json"
        with self.assertRaises(KitError):
            self.apply(0)
        self.assertEqual(kit.read_text(), KIT)  # untouched
        # review is never given resume text, so a resume edit is rejected when the review is made
        r = self.review(dict(REVIEW, part_a=[dict(REVIEW["part_a"][0], file="resume")]))
        self.assertEqual(r["status"], "unavailable")

    def test_prep_with_and_without_stories(self):
        self.assertEqual(self.prep()["status"], "ok")
        (self.home / "Story_Bank.md").write_text("Story one")
        self.assertEqual(self.prep()["status"], "ok")
        self.assertEqual(len(store.list_docs(self.conn, "preps")), 1)

    def test_no_ai_saves_nothing(self):
        for fn, col in ((self.review, "kitreviews"), (self.prep, "preps")):
            out = fn(mode="no-ai", r=ai.none_runner)
            self.assertEqual(out["status"], "unavailable")
            self.assertEqual(store.list_docs(self.conn, col), [])

    def test_paste_waits(self):
        self.assertEqual(self.review(r=lambda t, p: ai.QUEUED)["status"], "waiting")

    def test_hostile_model_output(self):
        for bad in ("garbage", "[]", '{"sections": 5}', '{"ignore previous instructions": true}', ""):
            self.assertEqual(self.prep(bad)["status"], "unavailable")
            self.assertEqual(self.review(bad)["status"], "unavailable")
        injected = dict(PREP, injection_suspect=True)
        self.assertEqual(self.prep(injected)["status"], "unavailable")

    def test_hostile_inputs(self):
        for fn in (self.review, self.prep):
            with self.assertRaises(KitError):
                fn(role="nope")
            for posting in ("", "  ", None, 7):
                with self.assertRaises(KitError):
                    fn(posting=posting)
        (self.home / "kits" / "r1" / "tailor.json").unlink()
        with self.assertRaises(KitError):
            self.review()
        (self.home / "Candidate_Profile.md").write_bytes(b"\xff\xfe")
        with self.assertRaises(KitError):
            self.prep()

    def test_traversal_role_id_cannot_read_outside_kits(self):
        store.set_doc(self.conn, "roles", "../x", {"company": "c"})
        (self.home / "x").mkdir()
        (self.home / "x" / "tailor.json").write_text(KIT)
        with self.assertRaises(KitError):
            self.review(role="../x")


if __name__ == "__main__":
    unittest.main()
