import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cc import cli, store, tracker
from cc.runs import morning

try:
    import openpyxl
except ImportError:
    openpyxl = None

ROLE = {"company": "Acme", "title": "Analyst", "date_found": "2026-10-01", "fit_pct": 80, "link": "https://example.com/j/1",
        "status": "new", "pay": None}


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = store.connect(self.dir / "t.db")
        store.set_doc(self.conn, "roles", "a", ROLE, force=True)
        store.set_doc(self.conn, "roles", "b", {**ROLE, "company": "Beta", "date_found": "2026-10-05"}, force=True)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_rows_are_strings_without_none(self):
        rows = tracker.rows_from_store(self.conn)
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["fit_pct"], rows[0]["pay"]), ("80", ""))

    def test_rejects_non_xlsx_path(self):
        with self.assertRaises(tracker.TrackerError):
            tracker.export(self.conn, self.dir / "x.csv")

    def test_missing_openpyxl_is_a_tracker_error(self):
        with mock.patch.dict("sys.modules", {"cc.rules.make_tracker": None}):
            with self.assertRaises(tracker.TrackerError) as cm:
                tracker.export(self.conn, self.dir / "x.xlsx")
        self.assertIn("openpyxl", str(cm.exception))

    def test_run_survives_missing_openpyxl_without_a_note(self):
        with mock.patch.dict("sys.modules", {"cc.rules.make_tracker": None}):
            morning.run(self.conn, "morning", tracker_path=self.dir / "x.xlsx")
        self.assertEqual(store.get(self.conn, "board", "today")["data"]["notes"], [])

    @unittest.skipUnless(openpyxl, "openpyxl not installed")
    def test_workbook_round_trip_and_default_path(self):
        with mock.patch.dict(os.environ, {"CC_HOME": str(self.dir)}):
            n, out = tracker.export(self.conn)
        self.assertEqual((n, out), (2, self.dir / "exports" / "Applications_Tracker.xlsx"))
        ws = openpyxl.load_workbook(out)["Applications"]
        self.assertEqual([c.value for c in ws[1]][:3], ["Date found", "Company", "Role"])
        self.assertEqual([ws.cell(r, 2).value for r in (2, 3)], ["Beta", "Acme"])  # newest first
        self.assertEqual(ws.cell(3, 16).hyperlink.target, "https://example.com/j/1")
        self.assertFalse(list(out.parent.glob("*.tmp")))

    @unittest.skipUnless(openpyxl, "openpyxl not installed")
    def test_locked_file_gives_a_clear_error_and_keeps_old_copy(self):
        out = self.dir / "x.xlsx"
        tracker.export(self.conn, out)
        before = out.read_bytes()
        with mock.patch("os.replace", side_effect=PermissionError):
            with self.assertRaises(tracker.TrackerError) as cm:
                tracker.export(self.conn, out)
        self.assertIn("close it in Excel", str(cm.exception))
        self.assertEqual(out.read_bytes(), before)
        self.assertFalse(list(self.dir.glob("*.tmp")))

    @unittest.skipUnless(openpyxl, "openpyxl not installed")
    def test_run_refreshes_the_file_and_notes_a_locked_one(self):
        out = self.dir / "x.xlsx"
        morning.run(self.conn, "morning", tracker_path=out)
        self.assertTrue(out.exists())
        with mock.patch("os.replace", side_effect=PermissionError):
            morning.run(self.conn, "morning", tracker_path=out)
        notes = store.get(self.conn, "board", "today")["data"]["notes"]
        self.assertTrue(any("Excel copy not updated" in n["text"] for n in notes))

    def test_cli_reports_failure_with_exit_1(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"CC_HOME": str(self.dir)}), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["tracker", str(self.dir / "x.txt")]), 1)
        self.assertIn("tracker:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
