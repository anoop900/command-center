"""Kit pipeline: tailor -> build -> qc -> ats -> trace. PDF steps are mocked; no live AI, no LibreOffice needed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cc import ai, store
from cc.kits import ats_check, build_resume, pipeline, qc_check

BASE = {"name": "Jordan Example", "contact": "Toronto | j@example.com", "headline": "Base head", "profile": "Base profile",
        "skills": [["Cloud", "Azure"]], "jobs": [{"company": "Contoso", "dates": "2020-2024", "title": "Lead",
                                                   "bullets": ["Led the migration of 40 servers"]}],
        "education": ["BSc"]}
RULES = {"home_city": "Toronto", "pages": 2, "min_last_page_fill": 0.75, "banned_words": ["robust", "genuinely"]}
PROFILE = "Azure lead. Migrated 40 servers. Toronto."
TAILOR = {"headline": "Azure Architect", "profile": "Azure lead", "skills": [{"label": "Cloud", "text": "Azure"}],
          "bullet_changes": [{"old": "Led the migration of 40 servers", "new": "Led the Azure migration of 40 servers"},
                             {"old": "text that is not there", "new": "x"}]}
PDF = {"ok": True, "pdf": "x.pdf", "pages": 2, "page2_fill": 0.9, "note": ""}
GOOD_TEXT = ("Toronto | j@example.com Azure Architect Azure lead " * 3, 2, 0.9)


def runner_for(tailor=TAILOR, trace=None):
    def run(task, prompt):
        d = tailor if task.name == "tailor" else (trace if trace is not None else {"unsupported": []})
        return d if isinstance(d, str) else json.dumps(d)
    return run


class Kits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "content.json").write_text(json.dumps(BASE))
        (self.home / "qc_rules.json").write_text(json.dumps(RULES))
        (self.home / "Candidate_Profile.md").write_text(PROFILE)
        self.conn = store.connect(self.home / "t.db")
        store.set_doc(self.conn, "roles", "r1", {"company": "Contoso", "title": "Lead"})
        self.built = []

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def kit(self, role="r1", posting="Azure migration role", mode="autopilot", runner=None, pdf=PDF, text=GOOD_TEXT):
        def bp(c, outdir, pages=2):
            self.built.append(c)
            Path(outdir, c["file_stem"] + ".pdf").write_bytes(b"%PDF")
            Path(outdir, c["file_stem"] + ".docx").write_bytes(b"x")
            return dict(pdf)
        with mock.patch.object(build_resume, "build_pdf", bp), mock.patch.object(qc_check, "read_pdf", lambda p: text):
            return pipeline.build_kit(self.conn, role, posting, mode, runner or runner_for(), self.home)

    def test_happy_path_merges_tailor_and_writes_files(self):
        k = self.kit()
        self.assertEqual(k["status"], "ok")
        self.assertEqual(k["tailor"]["source"], "ai")
        c = self.built[0]
        self.assertEqual(c["headline"], "Azure Architect")
        self.assertEqual(c["jobs"][0]["bullets"], ["Led the Azure migration of 40 servers"])
        self.assertEqual(BASE["jobs"][0]["bullets"], ["Led the migration of 40 servers"])
        self.assertTrue(any("dropped" in n for n in k["notes"]))
        d = self.kdir("r1")
        self.assertTrue((d / "kit.json").exists() and (d / "Jordan_Example_Resume.pdf").exists())
        self.assertEqual(store.get(self.conn, "kitindex", "r1")["data"]["status"], "ok")

    def test_none_runner_falls_back_to_base_resume(self):
        k = self.kit(mode="no-ai", runner=ai.none_runner)
        self.assertEqual(self.built[0]["headline"], "Base head")
        self.assertEqual(k["tailor"]["source"], "fallback")
        self.assertIn("skipped", k["trace"])

    def test_bad_tailor_output_falls_back(self):
        for bad in ("not json", {"headline": 5}, {"headline": "h", "profile": "p", "skills": "x"}):
            self.kit(runner=runner_for(tailor=bad))
            self.assertEqual(self.built[-1]["headline"], "Base head")

    def test_bad_qc_rules_file_is_a_clear_error(self):
        for bad in ('{"banned": {"(": "x"}}', "[]", "not json", '{"pages": "two"}'):
            (self.home / "qc_rules.json").write_text(bad)
            with self.assertRaises(pipeline.KitError) as cm:
                self.kit()
            self.assertIn("qc", str(cm.exception).lower())

    def test_qc_failure_blocks_and_skips_trace(self):
        k = self.kit(text=("Toronto genuinely robust " * 5, 2, 0.9))
        self.assertEqual(k["status"], "blocked")
        self.assertTrue(k["qc"]["issues"])
        self.assertNotIn("trace", k)

    def test_two_page_failure_blocks(self):
        self.assertEqual(self.kit(pdf={"ok": False, "pdf": "x.pdf", "pages": 3, "page2_fill": 0.9})["status"], "blocked")
        with mock.patch.object(build_resume, "build_pdf", lambda c, o, p=2: {"ok": False, "pages": 3, "reason": "too long"}):
            k = pipeline.build_kit(self.conn, "r1", "p", "autopilot", runner_for(), self.home)
        self.assertEqual(k["status"], "blocked")

    def test_rebuild_keeps_the_cover_letter(self):
        self.kit()
        (self.kdir("r1") / "Jordan_Example_Cover_Letter.pdf").write_bytes(b"%PDF")
        self.kit()
        self.assertTrue((self.kdir("r1") / "Jordan_Example_Cover_Letter.pdf").exists())

    def test_rebuild_archives_only_the_latest_previous_kit(self):
        self.kit()
        self.assertFalse((self.kdir("r1") / "previous").exists())
        first = json.loads((self.kdir("r1") / "kit.json").read_text("utf-8"))["built"]
        self.kit()
        self.kit()
        prev = self.kdir("r1") / "previous"
        self.assertTrue((prev / "kit.json").is_file())
        self.assertFalse((prev / "previous").exists())     # no nesting: one level of undo
        self.assertTrue(json.loads((prev / "kit.json").read_text("utf-8"))["built"] >= first)

    def _ladder_pick(self, fills):
        """Run build_pdf with fake page measurements (one per ladder rung; None = three pages) and return the rung it kept."""
        seen = []
        def fake_measure(pdf):
            f = fills[build_resume.LADDER.index(seen[-1])]       # the rung built last, also on the final re-measure
            return (3, 1.0) if f is None else (2, f)
        with mock.patch.object(build_resume, "build", lambda c, cfg, path: seen.append(cfg)),              mock.patch.object(build_resume, "to_pdf", lambda d, o: "x.pdf"), mock.patch.object(build_resume, "measure", fake_measure):
            r = build_resume.build_pdf({k: "x" for k in build_resume.REQUIRED}, tempfile.mkdtemp(), 2)
        return build_resume.LADDER.index(seen[-1]), r

    def test_resume_keeps_room_on_the_last_page_for_word(self):
        rung, r = self._ladder_pick([0.5, 0.7, 0.8, 0.9, 0.97, 0.98, None])
        self.assertEqual((rung, r["ok"]), (3, True))          # 0.9 beats the larger 0.97 and 0.98

    def test_resume_falls_back_to_the_largest_fit_when_no_rung_leaves_room(self):
        rung, r = self._ladder_pick([0.96, 0.98, None])
        self.assertEqual((rung, r["ok"]), (1, True))

    def test_missing_tools_is_unavailable_not_a_crash(self):
        with mock.patch.object(build_resume, "build_pdf", side_effect=RuntimeError("LibreOffice (soffice) is not installed")):
            k = pipeline.build_kit(self.conn, "r1", "p", "autopilot", runner_for(), self.home)
        self.assertEqual(k["status"], "unavailable")
        self.assertTrue((self.kdir("r1") / "tailor.json").exists())

    def test_unsupported_claims_flag_review(self):
        k = self.kit(runner=runner_for(trace={"unsupported": [{"claim": "ran Kafka", "reason": "not in profile"}]}))
        self.assertEqual(k["status"], "review")
        self.assertEqual(k["trace"]["unsupported"][0]["claim"], "ran Kafka")

    def kdir(self, rid):
        return self.home / "kits" / store.get(self.conn, "kitindex", rid)["data"]["folder"]

    def test_paste_mode_waits_and_stores_nothing(self):
        k = self.kit(runner=lambda t, p: ai.QUEUED)
        self.assertEqual(k["status"], "waiting")
        self.assertFalse((self.home / "kits").exists() and any((self.home / "kits").iterdir()))
        self.assertIsNone(store.get(self.conn, "kitindex", "r1"))

    def test_rerun_is_idempotent_and_leaves_no_temp(self):
        self.kit()
        self.kit()
        days = list((self.home / "kits").iterdir())
        self.assertEqual(len(days), 1)
        self.assertEqual(len(list(days[0].iterdir())), 1)       # the second run reused the first folder
        self.assertEqual(len(store.list_docs(self.conn, "kitindex")), 1)

    def test_hostile_inputs(self):
        with self.assertRaises(pipeline.KitError):
            self.kit(role="nope")
        for posting in ("", "   ", None, 5):
            with self.assertRaises(pipeline.KitError):
                self.kit(posting=posting)
        (self.home / "content.json").write_text("[1]")
        with self.assertRaises(pipeline.KitError):
            self.kit()
        (self.home / "content.json").write_bytes(b"\xff\xfe\x00")
        with self.assertRaises(pipeline.KitError):
            self.kit()
        (self.home / "content.json").unlink()
        with self.assertRaises(pipeline.KitError) as cm:
            self.kit()
        self.assertIn("content.json", str(cm.exception))

    def test_traversal_role_id_stays_inside_kits(self):
        store.set_doc(self.conn, "roles", "../../evil", {"company": "x"})
        self.kit(role="../../evil")
        self.assertTrue(self.kdir("../../evil").is_relative_to(self.home / "kits"))
        self.assertFalse((self.home.parent / "evil").exists())

    def test_locked_folder_keeps_previous_kit(self):
        self.kit()
        real = pipeline.os.replace

        def flaky(a, b):
            if Path(a).name.startswith(".tmp-"):
                raise PermissionError(13, "locked")
            return real(a, b)
        with mock.patch.object(pipeline.os, "replace", flaky), self.assertRaises(pipeline.KitError):
            self.kit()
        self.assertTrue((self.kdir("r1") / "kit.json").exists())
        self.assertFalse(any(p.name.startswith(".tmp-") for p in (self.home / "kits").iterdir()))


class SofficeLookup(unittest.TestCase):
    def test_missing_libreoffice_says_how_to_fix(self):
        with mock.patch("shutil.which", return_value=None), mock.patch("os.path.isfile", return_value=False):
            with self.assertRaises(RuntimeError) as cm:
                build_resume.soffice_path()
        self.assertIn("libreoffice.org", str(cm.exception))

    def test_finds_default_windows_install_off_path(self):
        found = r"C:\Program Files\LibreOffice\program\soffice.exe"
        with mock.patch("shutil.which", return_value=None),              mock.patch.dict("os.environ", {"ProgramFiles": r"C:\Program Files"}),              mock.patch("os.path.isfile", side_effect=lambda f: f.replace("\\", "/").endswith("LibreOffice/program/soffice.exe")):
            self.assertTrue(build_resume.soffice_path().replace("\\", "/").endswith("LibreOffice/program/soffice.exe"))


class PdfConversion(unittest.TestCase):
    def test_failed_conversion_is_a_plain_message(self):
        import subprocess
        with mock.patch.object(build_resume, "soffice_path", return_value="soffice"),              mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "soffice")):
            with self.assertRaises(RuntimeError) as cm:
                build_resume.to_pdf("x.docx", ".")
        self.assertIn("LibreOffice could not make the PDF", str(cm.exception))


class PageTarget(unittest.TestCase):
    def docx(self, d, name, pages):
        import zipfile
        with zipfile.ZipFile(Path(d) / "resumes" / name, "w") as z:
            z.writestr("docProps/app.xml", f"<Properties><Pages>{pages}</Pages></Properties>")

    def test_setting_beats_upload_beats_default(self):
        from cc import resume
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(resume.page_target(d, {}), 2)
            (Path(d) / "resumes").mkdir()
            self.docx(d, "a.docx", 1)
            self.assertEqual(resume.page_target(d, {}), 1)
            self.assertEqual(resume.page_target(d, {"pages": 3}), 3)
            self.docx(d, "b.docx", 7)
            self.assertEqual(resume.page_target(d, {}), 3)   # auto is capped at 3

    def test_unreadable_upload_falls_back_to_two(self):
        from cc import resume
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "resumes").mkdir()
            (Path(d) / "resumes" / "x.docx").write_bytes(b"not a zip")
            (Path(d) / "resumes" / "y.txt").write_text("hi")
            self.assertEqual(resume.page_target(d, {}), 2)


class FitLoop(unittest.TestCase):
    CONTENT = {k: "x" for k in build_resume.REQUIRED}

    def run_fit(self, pages, counts):
        seq = iter(counts)
        with mock.patch.object(build_resume, "build"), mock.patch.object(build_resume, "to_pdf", lambda d, o: "x.pdf"),              mock.patch.object(build_resume, "measure", lambda p: (next(seq), 0.9)), mock.patch.dict("sys.modules", {"docx": mock.Mock(), "pdfplumber": mock.Mock()}):
            return build_resume.build_pdf(dict(self.CONTENT, file_stem="r"), tempfile.mkdtemp(), pages)

    def test_one_page_target_fits_when_the_content_does(self):
        self.assertTrue(self.run_fit(1, [1, 1, 2, 1])["ok"])

    def test_three_page_target(self):
        self.assertEqual(self.run_fit(3, [2, 2, 3, 3, 4, 3])["pages"], 3)

    def test_too_long_for_one_page(self):
        r = self.run_fit(1, [2])
        self.assertFalse(r["ok"]); self.assertIn("1 page ", r["reason"] + " ")

    def test_too_short_for_target_is_reported_not_crashed(self):
        r = self.run_fit(3, [2] * 8)
        self.assertFalse(r["ok"]); self.assertIn("only 2 pages", r["note"])


class Checks(unittest.TestCase):
    def test_qc_pure(self):
        r = qc_check.check("Toronto 40 servers Azure", 2, 0.9, PROFILE, RULES)
        self.assertTrue(r["pass"], r)
        r = qc_check.check("Toronto 99 servers Kafka", 3, 0.5, PROFILE, RULES)
        joined = " ".join(r["issues"])
        for needle in ("3 pages", "50%", "number not in profile: 99", "kafka"):
            self.assertIn(needle, joined)

    def test_rules_default_to_off(self):
        r = qc_check.check("Anything goes 99 here", 7, 0.1, "", {})
        self.assertTrue(r["pass"] or all(i.startswith("number") for i in r["issues"]), r)
        self.assertFalse(any(i.startswith(("layout", "contact", "voice")) for i in r["issues"]))

    def test_voice_rules_are_opt_in(self):
        t = "We don't leverage robust things"
        self.assertFalse(any("voice" in i for i in qc_check.check(t, 2, 0.9, "", {})["issues"]))
        r = qc_check.check(t, 2, 0.9, "", {"no_contractions": True, "banned_words": ["robust"]})
        joined = " ".join(r["issues"])
        self.assertIn("contraction 'don't'", joined)
        self.assertIn("banned word 'robust'", joined)
        self.assertNotIn("leverag", joined)

    def test_watch_terms_configurable(self):
        t = "Built on Kafka and Foobar"
        self.assertIn("kafka", " ".join(qc_check.check(t, 2, 0.9, "", {})["issues"]))
        r = qc_check.check(t, 2, 0.9, "", {"watch_terms": [], "watch_terms_add": ["foobar"]})
        self.assertEqual([i for i in r["issues"] if "not in profile" in i and "tool" in i], ["tool/credential not in profile: foobar"])

    def test_bad_rules_fail_with_a_message(self):
        for bad in ([], {"banned": {"(": "x"}}, {"pages": 0}, {"pages": True}, {"min_last_page_fill": 2},
                    {"no_contractions": "yes"}, {"banned_words": "robust"}, {"home_city": "x" * 400},
                    {"watch_terms": ["a"] * 500}):
            with self.assertRaises(ValueError, msg=bad):
                qc_check.normalize(bad)

    def test_ats_pure(self):
        r = ats_check.check("", "azure kubernetes", "azure")
        self.assertFalse(r["pass"])
        self.assertIn("azure", r["add_these_exact_wording"])

    def test_build_pdf_rejects_bad_content(self):
        with self.assertRaises(RuntimeError):
            build_resume.build_pdf({"name": "x"}, tempfile.gettempdir())


if __name__ == "__main__":
    unittest.main()
