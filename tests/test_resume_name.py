import tempfile
import unittest
from pathlib import Path

from cc.kits.content import find_resume, resume_stem


class ResumeNameTests(unittest.TestCase):
    def test_first_and_last(self):
        self.assertEqual(resume_stem("Jordan Example"), "Jordan_Example_Resume")

    def test_one_name_only(self):
        self.assertEqual(resume_stem("Jordan"), "Jordan_Resume")

    def test_no_name(self):
        for v in (None, "", "   ", 5, "///"):
            self.assertEqual(resume_stem(v), "Resume" if v != 5 else "5_Resume")

    def test_unsafe_characters_are_stripped(self):
        s = resume_stem("../Jo/rdan  Van Der:Berg")
        self.assertEqual(s, "Jordan_Van_DerBerg_Resume")
        self.assertNotIn("/", s)

    def test_accents_are_kept(self):
        self.assertEqual(resume_stem("José Núñez"), "José_Núñez_Resume")

    def test_find_resume_new_and_old_names(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            self.assertIsNone(find_resume(d, "pdf"))
            (d / "resume.pdf").write_bytes(b"a")
            self.assertEqual(find_resume(d, "pdf").name, "resume.pdf")
            (d / "Jordan_Example_Resume.docx").write_bytes(b"a")
            (d / "notes.pdf").write_bytes(b"a")
            self.assertEqual(find_resume(d, "docx").name, "Jordan_Example_Resume.docx")


if __name__ == "__main__":
    unittest.main()
