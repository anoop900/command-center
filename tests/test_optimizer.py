"""ATS optimizer: what-if, Have / Adjacent / No answers, and ticked keywords reaching the next kit build (D7/D8)."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cc import optimizer, skills, store
from cc.kits import ats_check, build_resume, pipeline, qc_check

from tests.test_kits import BASE, PDF, PROFILE, RULES

POST = "Platform Lead at Fabrikam\nRequirements:\n- Experience with Azure, Kubernetes and Terraform\n- Splunk and PMP preferred"
ATS = {"score": 60, "parts": {"keywords": [18, 45], "title": [15, 15], "quantified": [7, 10], "years": [10, 10],
                              "education": [10, 10], "format": [0, 10]},
       "matched": ["azure"], "add": ["kubernetes"], "gaps": ["terraform", "splunk", "pmp"], "kw": [2, 5]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "Candidate_Profile.md").write_text(PROFILE + " Kubernetes.")
        self.conn = store.connect(self.home / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def kit(self, rid="r1", ats=ATS, title="Platform Lead"):
        store.set_doc(self.conn, "roles", rid, {"company": "Fabrikam", "title": title, "posting": POST}, force=True)
        store.set_doc(self.conn, "kitindex", rid, {"role": rid, "key": rid, "ats": ats}, force=True)

    def row(self, v, term, rid="r1"):
        k = next(k for k in v["kits"] if k["role"] == rid)
        return next(x for x in k["missing"] if x["term"] == term)


class WhatIf(Base):
    def test_no_extra_is_the_score_and_each_keyword_adds(self):
        self.assertEqual(optimizer.whatif(ATS, 0), 60)
        self.assertEqual(optimizer.whatif(ATS, 1), 69)
        self.assertEqual(optimizer.whatif(ATS, 99), optimizer.whatif(ATS, 3))     # capped at every keyword matched
        self.assertEqual(optimizer.whatif(ATS, -5), 60)

    def test_old_scores_without_counts_use_the_lists(self):
        old = {k: v for k, v in ATS.items() if k != "kw"}
        self.assertEqual(optimizer.counts(old), [1, 5])
        for bad in ({"kw": "x"}, {"kw": [3, 1]}, {"kw": [-1, 2]}, {"kw": [1, 2, 3]}, {}):
            self.assertIsInstance(optimizer.counts(bad), list)
        self.assertEqual(optimizer.whatif({"score": 50, "kw": [0, 0]}, 3), 50)

    def test_score_reports_counts_and_summary_keeps_them(self):
        r = ats_check.score("Azure Kubernetes", POST, PROFILE)
        self.assertEqual(r["kw"][1], len(ats_check.posting_terms(POST)))
        self.assertEqual(ats_check.summary(r)["kw"], r["kw"])


class Answers(Base):
    def test_states_lowest_first_and_projection(self):
        self.kit("r1")
        self.kit("r2", dict(ATS, score=75))
        v = optimizer.view(self.conn, self.home)
        self.assertEqual([k["role"] for k in v["kits"]], ["r1", "r2"])
        self.assertEqual(self.row(v, "kubernetes")["state"], "backed")
        self.assertEqual(self.row(v, "terraform")["state"], "open")
        self.assertEqual(v["kits"][0]["projected"], optimizer.whatif(ATS, 1))
        v = optimizer.apply(self.conn, self.home, {"action": "have", "term": "Terraform", "evidence": "Built modules at Contoso"})
        v = optimizer.apply(self.conn, self.home, {"action": "adjacent", "term": "splunk", "evidence": "Ran Azure Monitor alerting",
                                                   "confirm": True})
        v = optimizer.apply(self.conn, self.home, {"action": "no", "term": "pmp"})
        self.assertEqual([self.row(v, t)["state"] for t in ("terraform", "splunk", "pmp")], ["have", "adjacent", "no"])
        self.assertEqual(v["kits"][0]["projected"], optimizer.whatif(ATS, 3))
        self.assertIn("splunk (related experience): Ran Azure Monitor", skills.backed_text(self.conn))
        self.assertNotIn("pmp", skills.backed_text(self.conn))

    def test_adjacent_needs_an_explicit_true_and_evidence(self):
        self.kit()
        for bad in ({"action": "adjacent", "term": "splunk", "evidence": "x"},
                    {"action": "adjacent", "term": "splunk", "evidence": "x", "confirm": "true"},
                    {"action": "adjacent", "term": "splunk", "confirm": True},
                    {"action": "have", "term": "splunk"}, {"action": "have", "term": "splunk", "evidence": "   "}):
            with self.assertRaises(ValueError):
                optimizer.apply(self.conn, self.home, bad)
        self.assertEqual(skills.inventory(self.conn), [])
        self.assertEqual(skills.backed_text(self.conn), "")

    def test_undo_both_ways_leaves_no_stray_proposal(self):
        self.kit()
        optimizer.apply(self.conn, self.home, {"action": "no", "term": "pmp"})
        with self.assertRaises(ValueError):
            optimizer.apply(self.conn, self.home, {"action": "no", "term": "pmp"} | {"action": "have"})      # no evidence
        v = optimizer.apply(self.conn, self.home, {"action": "undo", "term": "pmp"})
        self.assertEqual(self.row(v, "pmp")["state"], "open")
        self.assertEqual(v["skills"]["proposals"] + v["skills"]["dismissed"], [])
        optimizer.apply(self.conn, self.home, {"action": "have", "term": "pmp", "evidence": "Certified 2019"})
        with self.assertRaises(ValueError):
            optimizer.apply(self.conn, self.home, {"action": "no", "term": "pmp"})      # in the inventory: remove first
        v = optimizer.apply(self.conn, self.home, {"action": "undo", "term": "pmp"})
        self.assertEqual((self.row(v, "pmp")["state"], skills.inventory(self.conn)), ("open", []))
        with self.assertRaises(ValueError):
            optimizer.apply(self.conn, self.home, {"action": "undo", "term": "pmp"})

    def test_a_mined_proposal_declined_then_undone_returns_to_the_list(self):
        store.set_doc(self.conn, skills.PROPOSALS, "splunk", {"term": "splunk", "status": "new", "files": 2, "sources": []})
        optimizer.apply(self.conn, self.home, {"action": "no", "term": "splunk"})
        self.assertEqual([p["term"] for p in skills.view(self.conn)["dismissed"]], ["splunk"])
        optimizer.apply(self.conn, self.home, {"action": "undo", "term": "splunk"})
        self.assertEqual([p["term"] for p in skills.view(self.conn)["proposals"]], ["splunk"])

    def test_hostile_input(self):
        self.kit()
        for bad in (None, [], "x", {}, {"action": "zap", "term": "x"}, {"action": "no"}, {"action": "no", "term": 5},
                    {"action": "no", "term": "x" * 500}, {"action": "no", "term": "  "}, {"action": "undo", "term": "never"}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                optimizer.apply(self.conn, self.home, bad)

    def test_odd_stored_scores_do_not_break_the_page(self):
        store.set_doc(self.conn, "kitindex", "a", {"ats": "x"}, force=True)
        store.set_doc(self.conn, "kitindex", "b", {"ats": {"score": "high"}}, force=True)
        store.set_doc(self.conn, "kitindex", "c", {"ats": {"score": 40, "add": [5, None, "azure"], "gaps": "oops"}}, force=True)
        (self.home / "Candidate_Profile.md").write_bytes(b"\xff\xfe junk")
        v = optimizer.view(self.conn, self.home)
        self.assertEqual([k["role"] for k in v["kits"]], ["c"])
        self.assertEqual([x["term"] for x in v["kits"][0]["missing"]], ["azure"])


class FeedsTailoring(Base):
    """Done-when for F1.5: a kit regenerated after ticking keywords scores higher, and no score is in the kit files."""
    def setUp(self):
        super().setUp()
        (self.home / "content.json").write_text(json.dumps(BASE))
        (self.home / "qc_rules.json").write_text(json.dumps(RULES))
        store.set_doc(self.conn, "roles", "r1", {"company": "Fabrikam", "title": "Platform Lead", "posting": POST})
        self.prompts = []

    def runner(self, task, prompt):
        """A stand-in tailor that, like the real one, may only use skills the profile it was given backs."""
        if task.name != "tailor":
            return json.dumps({"unsupported": []})
        self.prompts.append(prompt)
        inv = re.findall(r"^- ([a-z0-9 .+#/-]+?)(?: \(related experience\))?: ", prompt, flags=re.M)
        head = " | ".join(["Platform Lead"] + inv)
        return json.dumps({"headline": head, "profile": "Azure lead", "skills": [{"label": "Cloud", "text": "Azure"}]})

    def build(self):
        def bp(c, outdir, pages=2):
            Path(outdir, c["file_stem"] + ".pdf").write_text(c["headline"])
            Path(outdir, c["file_stem"] + ".docx").write_bytes(b"x")
            return dict(PDF, pdf=str(Path(outdir, c["file_stem"] + ".pdf")))
        read = lambda p: ("Toronto | j@example.com " + Path(p).read_text() + " Azure lead", 2, 0.9)
        with mock.patch.object(build_resume, "build_pdf", bp), mock.patch.object(qc_check, "read_pdf", read):
            return pipeline.build_kit(self.conn, "r1", POST, "autopilot", self.runner, self.home)

    def test_ticked_keywords_raise_the_rebuilt_score(self):
        first = self.build()["ats"]
        self.assertIn("terraform", first["gaps"])
        v = optimizer.view(self.conn, self.home)
        self.assertEqual(self.row(v, "terraform")["state"], "open")
        optimizer.apply(self.conn, self.home, {"action": "have", "term": "terraform", "evidence": "Built modules at Contoso"})
        optimizer.apply(self.conn, self.home, {"action": "adjacent", "term": "splunk", "evidence": "Azure Monitor alerting",
                                               "confirm": True})
        optimizer.apply(self.conn, self.home, {"action": "no", "term": "pmp"})
        second = self.build()["ats"]
        self.assertGreater(second["score"], first["score"])
        self.assertIn("terraform", second["matched"])
        self.assertNotIn("pmp", second["matched"])                       # "No" never reaches the kit
        self.assertIn("terraform: Built modules at Contoso", self.prompts[-1])
        inv = self.prompts[-1].split("Skill inventory")[-1].split("PROFILE-")[0]
        self.assertNotIn("pmp", inv)
        kdir = self.home / "kits" / store.get(self.conn, "kitindex", "r1")["data"]["folder"]
        for f in kdir.rglob("*"):
            if f.is_file() and f.suffix in (".json", ".md", ".txt"):
                body = json.loads(f.read_text()) if f.suffix == ".json" else {}
                self.assertFalse(isinstance(body, dict) and ("ats" in body or "ats_retry" in body), f)
        self.assertEqual(store.get(self.conn, "kitindex", "r1")["data"]["ats"]["score"], second["score"])

    def test_unticked_adjacent_never_reaches_the_build(self):
        with self.assertRaises(ValueError):
            optimizer.apply(self.conn, self.home, {"action": "adjacent", "term": "splunk", "evidence": "x"})
        self.build()
        self.assertNotIn("Skill inventory", self.prompts[-1])


class PostingTermsJunk(unittest.TestCase):
    def test_verbs_and_filler_are_not_keywords(self):
        got = ats_check.posting_terms("Acts as lead. Graduate degree. Manages Leads. Extensive knowledge of "
                                      "Kubernetes and Okta. Familiar with Terraform.")
        self.assertEqual(got, {"kubernetes", "okta", "terraform"})


if __name__ == "__main__":
    unittest.main()
