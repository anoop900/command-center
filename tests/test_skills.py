"""Skill inventory and the resume miner: proposals only, nothing confirmed without the user's tick."""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

from cc import skills, store

DAY = dt.date(2026, 6, 1)
RESUME = """Jordan Example
Led Kubernetes platform rollout across three regions.
Built Terraform modules and GitHub Actions pipelines.
Ran incident management for the service desk.
PMP certified.
"""


class Miner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = store.connect(os.path.join(self.tmp.name, "t.db"))
        kit = self.home / "kits" / "Example Co - Platform Lead"
        kit.mkdir(parents=True)
        (kit / "Resume.md").write_text(RESUME, encoding="utf-8")
        (kit / "kit.json").write_text('{"terraform": 1}', encoding="utf-8")            # not a resume file type: ignored
        work = self.home / "kits" / ".tmp-x"
        work.mkdir()
        (work / "Resume.md").write_text("splunk", encoding="utf-8")                     # half-built kit: ignored
        (self.home / "resumes").mkdir()
        (self.home / "resumes" / "old.txt").write_text("Owned Splunk dashboards", encoding="utf-8")
        (self.home / "resumes" / "broken.pdf").write_bytes(b"not a pdf")
        (self.home / "Candidate_Profile.md").write_text("## 4. Confirmed skills and tools\nKubernetes\n", encoding="utf-8")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def terms(self, key="proposals"):
        return sorted(p["term"] for p in skills.view(self.conn)[key])

    def test_terms_in_finds_whole_words_and_certs(self):
        got = skills.terms_in(RESUME)
        self.assertIn("terraform", got)
        self.assertIn("pmp", got)
        self.assertIn("Terraform modules", got["terraform"])
        self.assertNotIn("aks", skills.terms_in("Asked around"))                        # no partial-word hits

    def test_mine_proposes_only_unbacked_terms(self):
        out = skills.mine(self.conn, self.home, DAY)
        self.assertEqual((out["read"], out["failed"]), (2, ["broken.pdf"]))
        got = self.terms()
        self.assertIn("terraform", got)
        self.assertIn("splunk", got)
        self.assertNotIn("kubernetes", got)                                              # already in the profile
        self.assertEqual(skills.inventory(self.conn), [])                                # proposals only
        self.assertEqual(skills.backed_text(self.conn), "")

    def test_tick_moves_to_inventory_with_evidence(self):
        skills.mine(self.conn, self.home, DAY)
        skills.accept(self.conn, "Terraform", today=DAY)
        inv = skills.inventory(self.conn)
        self.assertEqual(inv[0]["term"], "terraform")
        self.assertIn("Terraform modules", inv[0]["evidence"])
        self.assertTrue(inv[0]["source"].endswith("Resume.md"))
        self.assertNotIn("terraform", self.terms())
        self.assertIn("terraform", skills.backed_text(self.conn))
        skills.mine(self.conn, self.home, DAY)                                           # re-mining leaves it alone
        self.assertNotIn("terraform", self.terms())

    def test_dismiss_survives_remine_and_can_be_undone(self):
        skills.mine(self.conn, self.home, DAY)
        skills.dismiss(self.conn, "splunk")
        skills.mine(self.conn, self.home, DAY)
        self.assertNotIn("splunk", self.terms())
        self.assertIn("splunk", self.terms("dismissed"))
        skills.dismiss(self.conn, "splunk", restore=True)
        self.assertIn("splunk", self.terms())

    def test_remove_returns_proposal(self):
        skills.mine(self.conn, self.home, DAY)
        skills.accept(self.conn, "terraform", today=DAY)
        skills.remove(self.conn, "terraform")
        self.assertEqual(skills.inventory(self.conn), [])
        self.assertIn("terraform", self.terms())

    def test_manual_add_needs_evidence_and_valid_kind(self):
        with self.assertRaises(ValueError):
            skills.accept(self.conn, "ansible")
        with self.assertRaises(ValueError):
            skills.accept(self.conn, "ansible", "Wrote playbooks", kind="maybe")
        for bad in (None, "", 5, "x" * 61):
            with self.assertRaises(ValueError):
                skills.accept(self.conn, bad, "e")
        skills.accept(self.conn, "Ansible", "Wrote playbooks", kind="adjacent", today=DAY)
        self.assertEqual(skills.inventory(self.conn)[0]["kind"], "adjacent")

    def test_apply_dispatch(self):
        v = skills.apply(self.conn, self.home, {"action": "mine"}, DAY)
        self.assertEqual(v["mined"]["read"], 2)
        for bad in ({}, {"action": "nope"}, "x"):
            with self.assertRaises(ValueError):
                skills.apply(self.conn, self.home, bad)


if __name__ == "__main__":
    unittest.main()
