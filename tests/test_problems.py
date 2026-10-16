import json
import re
import unittest

from cc import problems


BAD = re.compile(r"Traceback|\w+Error\b|Exception|errno|\bline \d+", re.I)


class DescribeTests(unittest.TestCase):
    def test_every_catalogue_entry_has_message_fix_and_known_action(self):
        codes = set()
        for code, pat, message, fix, action in problems._CATALOGUE:
            self.assertNotIn(code, codes)
            codes.add(code)
            re.compile(pat)
            self.assertTrue(message.endswith(".") and fix.endswith("."), code)
            self.assertIn(action, ("", "posting", "profile", "qc", "ai", "modes", "backup", "kits"), code)
            self.assertFalse(BAD.search(message + fix), code)

    def test_unknown_and_foreign_exceptions_get_the_generic_message(self):
        for e in (RuntimeError("boom"), KeyError("secret_key"), TypeError("x"), ZeroDivisionError()):
            d = problems.describe(e)
            self.assertEqual(d["code"], "unknown")
            self.assertEqual(d["message"], "Something went wrong.")
            self.assertTrue(d["fix"])
            self.assertNotIn("boom", d["message"])

    def test_our_own_plain_sentences_are_kept(self):
        d = problems.describe(ValueError("the posting text is empty"))
        self.assertEqual(d["message"], "The posting text is empty.")
        self.assertEqual(d["code"], "no_posting")

    def test_technical_text_is_replaced_and_kept_as_detail(self):
        d = problems.describe(PermissionError(13, "Access is denied"))
        self.assertEqual(d["code"], "disk")
        self.assertNotIn("Errno", d["message"])
        self.assertIn("Access is denied", d["detail"])

    def test_hostile_input_never_raises(self):
        for x in (None, "", b"\xff", "\x00" * 10, "A" * 100000, 5, {"a": 1}, ["x"], "<script>alert(1)</script>"):
            d = problems.describe(x)
            self.assertTrue(d["message"] and d["fix"])
            self.assertLessEqual(len(d["detail"]), 400)

    def test_qc_issues_are_grouped_and_have_a_next_step(self):
        d = problems.describe_qc(["voice: banned word 'leverag'", "voice: banned word '—'", "date range not in profile: ana 2013 - 2014"])
        self.assertIn("ban", d["message"])
        self.assertIn("Rebuild kit", d["fix"])
        self.assertFalse(BAD.search(d["message"]))
        self.assertIn("leverag", d["message"])
        self.assertEqual(problems.describe_qc([])["action"], "qc")
        self.assertIn("page", problems.describe_qc(["layout: 1 pages, must be exactly 2"])["message"])
        self.assertIn("not confirm", problems.describe_qc(["number not in profile: 40%"])["message"])

    def test_for_body_adds_fix_and_keeps_error_key(self):
        b = problems.for_body({"error": "database busy"})
        self.assertEqual(b["code"], "db_busy")
        self.assertTrue(b["fix"] and b["error"])
        self.assertEqual(problems.for_body({"ok": True}), {"ok": True})
        self.assertEqual(problems.for_body({"error": "x", "fix": "mine"})["fix"], "mine")
        self.assertEqual(problems.for_body(b"raw"), b"raw")
        json.dumps(problems.for_body({"error": "x" * 5000}))


if __name__ == "__main__":
    unittest.main()
