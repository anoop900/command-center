"""cc/kitlink.py: imported resume paths become kit folders on this PC; hostile paths never leave the kits folder."""
import tempfile
import unittest
from pathlib import Path

from cc import kitlink, store


class KitLink(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "kits" / "2026-10-06" / "Northwind_DirCloudOps").mkdir(parents=True)
        (self.home / "kits" / "2026-10-05" / "Contoso_DirCloud").mkdir(parents=True)
        self.conn = store.connect(self.home / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def role(self, rid, resume_file, **kw):
        store.set_doc(self.conn, "roles", rid, dict({"key": rid, "resume_file": resume_file}, **kw))

    def kit(self, rid):
        return store.get(self.conn, "roles", rid)["data"].get("kit")

    def test_path_and_bare_filename_resolve_and_board_row_follows(self):
        self.role("a", "Daily\\2026-10-06\\Northwind_DirCloudOps\\Jordan_Resume.pdf")
        self.role("b", "Jordan_Example_Resume_Contoso_DirCloud.pdf")
        store.set_doc(self.conn, "board", "roles", {"roles": [{"key": "a", "kit": ""}, {"key": "b", "kit": ""}]})
        self.assertEqual(kitlink.link_all(self.conn, self.home), 2)
        self.assertIn("Northwind_DirCloudOps", self.kit("a"))
        self.assertIn("Contoso_DirCloud", self.kit("b"))
        rows = store.get(self.conn, "board", "roles")["data"]["roles"]
        self.assertTrue(all(r["kit"] for r in rows))

    def test_missing_folder_hostile_paths_and_repeat_run(self):
        self.role("c", "Daily\\2026-10-01\\Gone\\x.pdf")
        self.role("d", "Daily\\..\\..\\Windows\\x.pdf")
        self.role("e", "C:\\Windows\\System32\\x.pdf")
        self.role("f", "Jordan_Example_Resume_*.pdf")
        self.role("g", "Daily\\2026-10-06\\Northwind_DirCloudOps\\x.pdf", kit="mine")
        self.assertEqual(kitlink.link_all(self.conn, self.home), 0)
        self.assertEqual([self.kit(i) for i in "cdefg"], [None, None, None, None, "mine"])
        self.role("h", "Daily\\2026-10-06\\Northwind_DirCloudOps\\x.pdf")
        self.assertEqual((kitlink.link_all(self.conn, self.home), kitlink.link_all(self.conn, self.home)), (1, 0))

    def test_no_kits_folder_is_fine(self):
        self.assertEqual(kitlink.link_all(self.conn, self.home / "nowhere"), 0)


if __name__ == "__main__":
    unittest.main()
