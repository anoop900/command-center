"""The resume content follows the profile: a changed job date reaches the resume on a rebuild, and hand edits are not lost silently."""
import json
import tempfile
import unittest
from pathlib import Path

from cc import profile
from cc.kits import content


def raw(dates="Aug 2024 - Present", skills="Python"):
    ok = lambda v: {"value": v, "ok": True}
    return {"fields": {"firstName": ok("Jordan"), "lastName": ok("Example"), "education": ok("BSc")},
            "jobs": [{"employer": "Example Co", "title": "Lead", "dates": dates, "facts": ["Ran the thing."], "ok": True}],
            "skills": {"value": skills, "ok": bool(skills)}}


class Refresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        profile.save(self.home, raw())
        content.ensure(self.home)

    def dates(self):
        return json.loads((self.home / "content.json").read_text("utf-8"))["jobs"][0]["dates"]

    def test_an_untouched_content_file_follows_the_profile(self):
        profile.save(self.home, raw("Aug 2024 - June 2026"))
        notes = content.ensure(self.home)
        self.assertEqual(self.dates(), "Aug 2024 - June 2026")
        self.assertTrue(any("refreshed" in n for n in notes))
        self.assertIn("Present", (self.home / "content.previous.json").read_text("utf-8"))
        self.assertEqual(content.ensure(self.home), [])      # nothing changed: no note, no churn

    def test_hand_edits_survive_an_automatic_build_but_not_a_requested_rebuild(self):
        edited = json.loads((self.home / "content.json").read_text("utf-8"))
        edited["headline"] = "My own headline"
        (self.home / "content.json").write_text(json.dumps(edited), "utf-8")
        profile.save(self.home, raw("Aug 2024 - June 2026"))
        content.ensure(self.home)
        self.assertEqual(self.dates(), "Aug 2024 - Present")
        content.ensure(self.home, force=True)
        self.assertEqual(self.dates(), "Aug 2024 - June 2026")
        self.assertIn("My own headline", (self.home / "content.previous.json").read_text("utf-8"))

    def test_a_file_from_before_this_fix_is_only_refreshed_on_a_requested_rebuild(self):
        (self.home / "content.sha").unlink()
        profile.save(self.home, raw("Aug 2024 - June 2026"))
        content.ensure(self.home)
        self.assertEqual(self.dates(), "Aug 2024 - Present")
        content.ensure(self.home, force=True)
        self.assertEqual(self.dates(), "Aug 2024 - June 2026")

    def test_a_requested_rebuild_with_an_incomplete_profile_keeps_the_file_and_says_why(self):
        profile.save(self.home, raw(skills=""))
        notes = content.ensure(self.home, force=True)
        self.assertTrue(any("not refreshed" in n and "skills" in n for n in notes))
        self.assertEqual(self.dates(), "Aug 2024 - Present")

    def test_first_build_without_a_usable_profile_still_says_what_to_do(self):
        empty = self.home / "fresh"
        empty.mkdir()
        with self.assertRaises(ValueError) as e:
            content.ensure(empty)
        self.assertIn("Profile step", str(e.exception))

    def test_corrupt_content_file_is_left_for_the_build_to_report(self):
        (self.home / "content.json").write_text("{not json", "utf-8")
        self.assertEqual(content.ensure(self.home), [])
        self.assertEqual((self.home / "content.json").read_text("utf-8"), "{not json")


if __name__ == "__main__":
    unittest.main()
