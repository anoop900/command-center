"""Custom kit folder: check, verified move that keeps every dashboard link, backup/restore of it, and the HTTP routes."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cc import backup, config, kitfolder, store, web


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir()
        self.cfg = self.home / "config.toml"
        self.cfg.write_text(config.STARTER, encoding="utf-8")
        self.dest = Path(self.tmp.name) / "OneDrive" / "Resumes"
        old = self.home / "kits"
        for rel, text in (("2026-10-10/Fabrikam_VP/resume.pdf", "pdf-a"), ("2026-10-10/Fabrikam_VP/kit.md", "notes"),
                          ("litware-dir/resume.pdf", "pdf-b")):
            (old / rel).parent.mkdir(parents=True, exist_ok=True)
            (old / rel).write_text(text, encoding="utf-8")
        self.conn = store.connect(self.home / "commandcenter.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class Change(Base):
    def test_creates_missing_folder_moves_everything_and_keeps_relative_paths(self):
        out = kitfolder.change(self.home, self.cfg, str(self.dest))
        self.assertEqual((out["moved"], out["left"]), (3, 0))
        self.assertEqual((self.dest / "2026-10-10/Fabrikam_VP/resume.pdf").read_text(), "pdf-a")
        self.assertEqual((self.dest / "litware-dir/resume.pdf").read_text(), "pdf-b")
        self.assertEqual(list((self.home / "kits").rglob("*.pdf")), [])
        self.assertEqual(config.kits_dir(self.home), self.dest.resolve())
        self.assertTrue(kitfolder.current(self.home)["custom"])

    def test_back_to_default_moves_kits_home(self):
        kitfolder.change(self.home, self.cfg, str(self.dest))
        out = kitfolder.change(self.home, self.cfg, "")
        self.assertEqual(out["moved"], 3)
        self.assertTrue((self.home / "kits/litware-dir/resume.pdf").is_file())
        self.assertFalse(kitfolder.current(self.home)["custom"])

    def test_clash_refuses_and_changes_nothing(self):
        (self.dest / "litware-dir").mkdir(parents=True)
        (self.dest / "litware-dir/resume.pdf").write_text("someone else's file")
        with self.assertRaises(kitfolder.KitFolderError):
            kitfolder.change(self.home, self.cfg, str(self.dest))
        self.assertEqual((self.dest / "litware-dir/resume.pdf").read_text(), "someone else's file")
        self.assertTrue((self.home / "kits/litware-dir/resume.pdf").is_file())
        self.assertEqual(config.kits_dir(self.home), self.home / "kits")

    def test_existing_files_in_the_new_folder_are_kept(self):
        self.dest.mkdir(parents=True)
        (self.dest / "My old resume.docx").write_text("mine")
        kitfolder.change(self.home, self.cfg, str(self.dest))
        self.assertEqual((self.dest / "My old resume.docx").read_text(), "mine")

    def test_bad_input_gives_a_message_not_a_crash(self):
        for bad in ("relative/folder", "   ", "x" * 600, "bad\x00name", "C:\\Windows\\System32\\config\\x\x01"):
            with self.assertRaises(kitfolder.KitFolderError, msg=bad[:20]):
                kitfolder.check(bad)
        blocker = Path(self.tmp.name) / "afile"
        blocker.write_text("x")
        with self.assertRaises(kitfolder.KitFolderError) as c:       # a folder cannot be created under a file
            kitfolder.check(str(blocker / "sub"))
        self.assertIn("permission", str(c.exception))

    def test_nested_choice_refused(self):
        with self.assertRaises(kitfolder.KitFolderError):
            kitfolder.change(self.home, self.cfg, str(self.home / "kits" / "inside"))
        self.assertTrue((self.home / "kits/litware-dir/resume.pdf").is_file())

    def test_refused_while_a_build_runs(self):
        store.set_doc(self.conn, "jobs", "kit-x", {"kind": "kit", "key": "x", "state": "running"})
        with self.assertRaises(kitfolder.KitFolderError):
            kitfolder.change(self.home, self.cfg, str(self.dest))

    def test_same_folder_twice_is_a_no_op(self):
        kitfolder.change(self.home, self.cfg, str(self.dest))
        self.assertEqual(kitfolder.change(self.home, self.cfg, str(self.dest))["moved"], 0)

    def test_folder_names_are_safe_and_unique(self):
        kits = Path(self.tmp.name) / "k"
        a = kitfolder.folder_for({"company": "Re:Search/Co <>", "title": "VP, AI & Data!"}, kits, "2026-10-13")
        self.assertRegex(a, r"^2026-10-13/[A-Za-z0-9_-]+$")
        (kits / a).mkdir(parents=True)
        self.assertTrue(kitfolder.folder_for({"company": "Re:Search/Co <>", "title": "VP, AI & Data!"}, kits, "2026-10-13").endswith("-2"))
        self.assertTrue(kitfolder.folder_for({}, kits, "2026-10-13").endswith("role_role"))


class BackupCustom(Base):
    def test_backup_includes_custom_folder_and_restore_keeps_other_files(self):
        kitfolder.change(self.home, self.cfg, str(self.dest))
        (self.dest / "keep-me.docx").write_text("unrelated")
        out = backup.create(self.home, self.home / "backups" / "t.ccbackup")
        names = backup.verify(out["path"])["files"]
        self.assertIn("kits/2026-10-10/Fabrikam_VP/resume.pdf", names)
        (self.dest / "litware-dir/resume.pdf").unlink()
        backup.restore(out["path"], self.home, replace=True)
        self.assertEqual((self.dest / "litware-dir/resume.pdf").read_text(), "pdf-b")
        self.assertEqual((self.dest / "keep-me.docx").read_text(), "unrelated")

    def test_restore_on_a_pc_without_that_folder_falls_back_to_default(self):
        src = Path(self.tmp.name) / "unpacked"
        (src / "kits/2026-10-10/Fabrikam_VP").mkdir(parents=True)
        (src / "kits/2026-10-10/Fabrikam_VP/resume.pdf").write_text("pdf-a")
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("x")                              # a folder cannot be created under a file
        (src / "config.toml").write_text(f'[paths]\nkits = "{(blocker / "Resumes").as_posix()}"\n', encoding="utf-8")
        other = Path(self.tmp.name) / "newpc"
        other.mkdir()
        notes = backup._apply(src, other)
        self.assertEqual((other / "kits/2026-10-10/Fabrikam_VP/resume.pdf").read_text(), "pdf-a")
        self.assertEqual(config.kits_dir(other), other / "kits")
        self.assertIn("Could not create", notes[0])


class Http(Base):
    def setUp(self):
        super().setUp()
        self.conn.close()
        self.srv = web.make_server(self.home, port=0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.conn = store.connect(self.home / "commandcenter.db")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_get_post_and_served_files_follow_the_new_folder(self):
        st, raw = self.call("GET", "/kits-folder")
        self.assertEqual((st, json.loads(raw)["custom"]), (200, False))
        st, raw = self.call("POST", "/kits-folder", {"path": str(self.dest)})
        self.assertEqual((st, json.loads(raw)["moved"]), (200, 3))
        st, raw = self.call("GET", "/kits/litware-dir/resume.pdf")
        self.assertEqual((st, raw), (200, b"pdf-b"))
        st, raw = self.call("GET", "/kits/2026-10-10/Fabrikam_VP/kit.md")
        self.assertEqual(st, 200)
        self.assertEqual(self.call("GET", "/kits/../config.toml")[0], 404)

    def test_bad_requests_are_400_not_500(self):
        for body in ({}, {"path": 5}, {"path": "relative"}, [], "x"):
            st, _ = self.call("POST", "/kits-folder", body)
            self.assertIn(st, (400,), str(body))
        self.assertEqual(config.kits_dir(self.home), self.home / "kits")

    def test_open_folder_stays_inside_the_kit_folder(self):
        from unittest import mock
        with mock.patch.object(web, "_open_folder") as op:
            self.assertEqual(self.call("POST", "/kits-folder/open", {"sub": ""})[0], 200)
            self.assertEqual(op.call_args[0][0], (self.home / "kits").resolve())
            for sub, want in (("litware-dir", 200), ("..", 400), ("../..", 400), ("nope", 404), (5, 400)):
                self.assertEqual(self.call("POST", "/kits-folder/open", {"sub": sub})[0], want, str(sub))
            self.assertEqual(op.call_count, 2)


if __name__ == "__main__":
    unittest.main()
