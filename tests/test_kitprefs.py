"""Kit-edit learning: phrases removed by hand from 2+ kits become avoid phrases; offline, fictional text."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docx import Document

from cc import config, kitprefs, learned, store
from cc.kits import build_resume, pipeline, qc_check


def write_docx(path, lines):
    d = Document()
    for ln in lines:
        d.add_paragraph(ln)
    d.save(str(path))


class KitPrefs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.conn = store.connect(self.home / "t.db")
        self.kits = config.kits_dir(self.home)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def kit(self, name, generated, edited=None):
        f = self.kits / name
        f.mkdir(parents=True)
        write_docx(f / "resume.docx", generated)
        kitprefs.save_baseline(f)
        if edited is not None:
            write_docx(f / "resume.docx", edited)
        return f

    def test_docx_text_roundtrip_and_bad_files(self):
        f = self.kit("a", ["Led the Azure migration", "Ran a team"])
        self.assertIn("Led the Azure migration", kitprefs.docx_text(f / "resume.docx"))
        (self.home / "bad.docx").write_bytes(b"not a zip")
        self.assertEqual((kitprefs.docx_text(self.home / "bad.docx"), kitprefs.docx_text(self.home / "none.docx")), ("", ""))

    def test_phrase_needs_two_kits_and_absence_everywhere(self):
        line = "Spearheaded synergy across platforms"
        keep = ["Ran the Azure migration"]
        self.kit("k1", [line] + keep, keep)
        self.assertEqual(kitprefs.train(self.conn, self.home), [])        # one kit is not a pattern
        self.kit("k2", [line, "Built dashboards"], ["Built dashboards"])
        got = {p["phrase"] for p in kitprefs.train(self.conn, self.home)}
        self.assertIn("spearheaded synergy", got)
        self.assertNotIn("ran the azure", got)
        self.kit("k3", ["Other"], ["Other", "Spearheaded synergy across platforms"])    # kept in a current kit
        self.assertNotIn("spearheaded synergy", {p["phrase"] for p in kitprefs.train(self.conn, self.home)})

    def test_unedited_corrupt_and_missing_kits_teach_nothing(self):
        self.kit("same", ["Plain text here today"])
        f = self.kit("broken", ["Some wording to remove"])
        (f / "resume.docx").write_bytes(b"junk")
        (self.kits / "stray").mkdir()
        (self.kits / "file.txt").write_text("x")
        self.assertEqual(kitprefs.train(self.conn, self.home), [])
        self.assertEqual(kitprefs.train(self.conn, self.home / "nowhere"), [])

    def test_forgotten_phrase_is_not_sent_and_view_lists_it(self):
        for n in ("a", "b"):
            self.kit(n, ["Spearheaded synergy across platforms", n * 5], [n * 5])
        kitprefs.train(self.conn, self.home)
        self.assertIn("spearheaded synergy", kitprefs.phrases(self.conn))
        learned.forget(self.conn, kitprefs.key("spearheaded synergy"))
        gone = learned.view(self.conn)
        self.assertTrue(any(p["forgotten"] for p in gone["kitprefs"]))
        from cc import relevance
        self.assertNotIn("spearheaded synergy", kitprefs.phrases(self.conn, relevance.settings(self.conn)["forgotten"]))

    def test_tailor_gets_learned_phrases_as_banned(self):
        (self.home / "content.json").write_text(json.dumps({"name": "J", "headline": "h", "profile": "p", "skills": [["a", "b"]],
                                                            "jobs": [], "education": []}))
        (self.home / "qc_rules.json").write_text(json.dumps({"pages": 2}))
        (self.home / "Candidate_Profile.md").write_text("Azure lead.")
        store.set_doc(self.conn, "roles", "r1", {"company": "Contoso", "title": "Lead"})
        store.set_doc(self.conn, "learned", "kitprefs", {"phrases": [{"phrase": "spearheaded synergy", "kits": 2}]})
        seen = []

        def runner(task, prompt):
            seen.append((task.name, prompt))
            return json.dumps({"unsupported": []})

        def bp(c, outdir, pages=2):
            Path(outdir, c["file_stem"] + ".pdf").write_bytes(b"%PDF")
            write_docx(Path(outdir, c["file_stem"] + ".docx"),["Generated line for baseline"])
            return {"ok": True, "pdf": "x.pdf", "pages": 2, "page2_fill": 0.9, "note": ""}
        with mock.patch.object(build_resume, "build_pdf", bp), mock.patch.object(qc_check, "read_pdf", lambda p: ("Azure " * 30, 2, 0.9)):
            pipeline.build_kit(self.conn, "r1", "Azure role", "autopilot", runner, self.home)
        self.assertTrue(any("spearheaded synergy" in p for n, p in seen if n == "tailor"))
        self.assertEqual(len(list(self.kits.rglob(kitprefs.BASELINE))), 1)


if __name__ == "__main__":
    unittest.main()
