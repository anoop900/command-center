"""Full backup / restore, unsaved-form drafts, and their HTTP endpoints. Nothing here touches the real data folder,
Task Scheduler or keyring: every case works in a temp folder."""
import datetime as dt
import http.client
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from cc import backup, config, drafts, profile, store, web


def make_data(root, name="data"):
    d = Path(root) / name
    d.mkdir(parents=True)
    conn = store.connect(d / backup.DB)
    store.set_doc(conn, "roles", "r1", {"company": "Acme", "title": "Engineer"})
    store.set_doc(conn, "tasks", "t1", {"title": "call"})
    conn.close()
    (d / "config.toml").write_text('[mode]\nname = "no-ai"\n\n[mail]\nhost = "imap.real.test"\nuser = "me@real.test"\n', encoding="utf-8")
    (d / "profile.json").write_text('{"firstName": "Ann"}', encoding="utf-8")
    (d / "Candidate_Profile.md").write_text("# Ann\n", encoding="utf-8")
    (d / "resumes").mkdir()
    (d / "resumes" / "cv.txt").write_text("my resume", encoding="utf-8")
    (d / "kits" / "acme" / "x").mkdir(parents=True)
    (d / "kits" / "acme" / "x" / "resume.txt").write_text("kit", encoding="utf-8")
    (d / "drafts").mkdir()
    (d / "drafts" / "profile.json").write_text("{}", encoding="utf-8")
    # things that must never travel
    (d / "secrets.json").write_text('{"imap": "hunter2"}', encoding="utf-8")
    (d / "logs").mkdir()
    (d / "logs" / "web.log").write_text("log", encoding="utf-8")
    (d / "exports").mkdir()
    (d / "exports" / "t.xlsx").write_text("x", encoding="utf-8")
    return d


def snapshot(d):
    """Allow-listed files by name and content, plus the database documents."""
    files = {n: p.read_bytes() for n, p in backup._items(d)}
    conn = store.connect(Path(d) / backup.DB)
    try:
        docs = {c: sorted((r["id"], json.dumps(r["data"], sort_keys=True)) for r in store.list_docs(conn, c)) for c in store.COLLECTIONS}
    finally:
        conn.close()
    return files, docs


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.d = make_data(self.root)


class CreateVerify(Base):
    def test_round_trip_into_an_empty_folder_is_identical(self):
        out = backup.create(self.d, self.root / "b.ccbackup")
        self.assertTrue(Path(out["path"]).exists())
        fresh = self.root / "fresh"
        res = backup.restore(self.root / "b.ccbackup", fresh)
        self.assertIsNone(res["safety_backup"])
        self.assertEqual(snapshot(fresh), snapshot(self.d))

    def test_secrets_logs_exports_and_old_backups_stay_out(self):
        backup.create(self.d, self.d / "backups" / "m.ccbackup")
        backup.create(self.d, self.root / "b.ccbackup")
        with zipfile.ZipFile(self.root / "b.ccbackup") as z:
            names = set(z.namelist())
            self.assertFalse({n for n in names if n.startswith(("logs", "exports", "backups")) or n == "secrets.json"})
            self.assertIn(backup.README, names)
            self.assertIn("NOT in this backup", z.read(backup.README).decode())
            self.assertNotIn(b"hunter2", b"".join(z.read(n) for n in names))

    def test_file_is_owner_only_where_supported_and_atomic(self):
        dest = self.root / "b.ccbackup"
        backup.create(self.d, dest)
        self.assertFalse(dest.with_name(dest.name + ".part").exists())
        if os.name != "nt":
            self.assertEqual(dest.stat().st_mode & 0o077, 0)

    def test_failed_create_leaves_no_partial_file(self):
        dest = self.root / "b.ccbackup"
        with mock.patch.object(backup, "verify", side_effect=backup.BackupError("boom")):
            with self.assertRaises(backup.BackupError):
                backup.create(self.d, dest)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name(dest.name + ".part").exists())

    def test_oversize_file_is_refused_by_name(self):
        with mock.patch.object(backup, "MAX_FILE", 5):
            with self.assertRaises(backup.BackupError) as cm:
                backup.create(self.d, self.root / "b.ccbackup")
        self.assertIn("larger", str(cm.exception))

    def test_backup_taken_while_another_process_writes_is_consistent(self):
        stop = threading.Event()

        def writer():
            c = store.connect(self.d / backup.DB)
            try:
                i = 0
                while not stop.is_set():
                    store.set_doc(c, "tasks", f"w{i % 50}", {"title": f"n{i}"}, force=True)
                    i += 1
            finally:
                c.close()
        t = threading.Thread(target=writer)
        t.start()
        try:
            for n in range(3):
                backup.create(self.d, self.root / f"b{n}.ccbackup")
        finally:
            stop.set()
            t.join()
        fresh = self.root / "fresh"
        backup.restore(self.root / "b2.ccbackup", fresh)
        c = sqlite3.connect(fresh / backup.DB)
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.close()

    def test_empty_data_folder_still_backs_up(self):
        empty = self.root / "empty"
        empty.mkdir()
        out = backup.create(empty, self.root / "e.ccbackup")
        self.assertEqual(out["files"], 0)
        backup.verify(self.root / "e.ccbackup")


def rewrite(src, dst, edit):
    """Copy a backup, letting `edit(zin, zout)` change entries."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        edit(zin, zout)


class Hostile(Base):
    def setUp(self):
        super().setUp()
        self.good = self.root / "good.ccbackup"
        backup.create(self.d, self.good)

    def bad(self, edit, msg=None):
        out = self.root / "bad.ccbackup"
        rewrite(self.good, out, edit)
        with self.assertRaises(backup.BackupError) as cm:
            backup.verify(out)
        if msg:
            self.assertIn(msg, str(cm.exception))
        return out

    def test_not_a_zip_and_missing_and_empty_files(self):
        for name, content in (("a.ccbackup", b"not a zip"), ("b.ccbackup", b""), ("c.ccbackup", b"PK\x03\x04" + b"x" * 40)):
            (self.root / name).write_bytes(content)
            with self.assertRaises(backup.BackupError):
                backup.verify(self.root / name)
        with self.assertRaises(backup.BackupError):
            backup.verify(self.root / "nope.ccbackup")

    def test_flipped_byte_is_caught_by_the_checksum(self):
        def edit(zin, zout):
            for i in zin.infolist():
                data = zin.read(i.filename)
                if i.filename == "profile.json":
                    data = data.replace(b"Ann", b"Bob")
                zout.writestr(i.filename, data)
        self.bad(edit, "checksum")

    def test_truncated_file(self):
        data = self.good.read_bytes()
        (self.root / "t.ccbackup").write_bytes(data[: len(data) // 2])
        with self.assertRaises(backup.BackupError):
            backup.verify(self.root / "t.ccbackup")

    def test_missing_or_damaged_manifest(self):
        self.bad(lambda zin, zout: [zout.writestr(i.filename, zin.read(i.filename)) for i in zin.infolist() if i.filename != "manifest.json"], "manifest")
        self.bad(lambda zin, zout: [zout.writestr(i.filename, b"{" if i.filename == "manifest.json" else zin.read(i.filename)) for i in zin.infolist()], "manifest")
        self.bad(lambda zin, zout: [zout.writestr(i.filename, b"[1]" if i.filename == "manifest.json" else zin.read(i.filename)) for i in zin.infolist()], "damaged")

    def test_deeply_nested_manifest(self):
        deep = b"[" * 100000 + b"]" * 100000
        self.bad(lambda zin, zout: [zout.writestr(i.filename, deep if i.filename == "manifest.json" else zin.read(i.filename)) for i in zin.infolist()])

    def test_newer_format_is_refused_with_advice(self):
        def edit(zin, zout):
            for i in zin.infolist():
                data = zin.read(i.filename)
                if i.filename == "manifest.json":
                    m = json.loads(data)
                    m["format"] = backup.FORMAT + 1
                    data = json.dumps(m).encode()
                zout.writestr(i.filename, data)
        self.bad(edit, "newer version")

    def test_bad_format_values(self):
        for v in (0, -1, "1", None, True, 1.5):
            def edit(zin, zout, v=v):
                for i in zin.infolist():
                    data = zin.read(i.filename)
                    if i.filename == "manifest.json":
                        m = json.loads(data)
                        m["format"] = v
                        data = json.dumps(m).encode()
                    zout.writestr(i.filename, data)
            self.bad(edit)

    def test_path_traversal_absolute_drive_and_odd_names(self):
        for evil in ("../evil.txt", "kits/../../evil.txt", "/abs.txt", "C:/win.txt", "kits\\..\\x", "resumes/./a", "kits//a",
                     "logs/web.log", "secrets.json", "other.txt", "kits"):
            def edit(zin, zout, evil=evil):
                for i in zin.infolist():
                    zout.writestr(i.filename, zin.read(i.filename))
                zout.writestr(evil, b"x")
            out = self.root / "evil.ccbackup"
            rewrite(self.good, out, edit)
            with self.assertRaises(backup.BackupError, msg=evil):
                backup.verify(out)
            fresh = self.root / "f"
            with self.assertRaises(backup.BackupError):
                backup.restore(out, fresh)
            self.assertFalse((self.root / "evil.txt").exists())

    def test_listed_but_missing_and_unlisted_entries(self):
        def drop(zin, zout):
            for i in zin.infolist():
                if i.filename != "profile.json":
                    zout.writestr(i.filename, zin.read(i.filename))
        self.bad(drop, "missing")
        def add(zin, zout):
            for i in zin.infolist():
                zout.writestr(i.filename, zin.read(i.filename))
            zout.writestr("resumes/extra.txt", b"x")
        self.bad(add, "unlisted")

    def test_symlink_entry(self):
        def edit(zin, zout):
            for i in zin.infolist():
                zout.writestr(i.filename, zin.read(i.filename))
            zi = zipfile.ZipInfo("kits/link")
            zi.external_attr = (0o120777 << 16)
            zout.writestr(zi, b"/etc/passwd")
        self.bad(edit)

    def test_zip_bomb_limits(self):
        with mock.patch.object(backup, "MAX_TOTAL", 10):
            with self.assertRaises(backup.BackupError):
                backup.verify(self.good)
        with mock.patch.object(backup, "MAX_ENTRIES", 2):
            with self.assertRaises(backup.BackupError):
                backup.verify(self.good)

    def test_damaged_database_inside_a_valid_looking_backup_is_refused_before_anything_changes(self):
        junk = b"SQLite format 3\x00" + b"\x00" * 200
        def edit(zin, zout):
            m = json.loads(zin.read("manifest.json"))
            import hashlib
            for i in zin.infolist():
                data = zin.read(i.filename)
                if i.filename == backup.DB:
                    data = junk
                    m["files"][backup.DB] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                if i.filename != "manifest.json":
                    zout.writestr(i.filename, data)
            zout.writestr("manifest.json", json.dumps(m))
        out = self.root / "baddb.ccbackup"
        rewrite(self.good, out, edit)
        before = snapshot(self.d)
        with self.assertRaises(backup.BackupError):
            backup.restore(out, self.d, replace=True)
        self.assertEqual(snapshot(self.d), before)


class RestoreSafety(Base):
    def setUp(self):
        super().setUp()
        self.good = self.root / "good.ccbackup"
        backup.create(self.d, self.good)
        c = store.connect(self.d / backup.DB)
        store.set_doc(c, "roles", "r2", {"company": "NewCo"})
        c.close()
        (self.d / "profile.json").write_text('{"firstName": "Changed"}', encoding="utf-8")
        self.changed = snapshot(self.d)

    def test_non_empty_folder_is_refused_without_replace_and_nothing_changes(self):
        with self.assertRaises(backup.BackupError) as cm:
            backup.restore(self.good, self.d)
        self.assertIn("--replace", str(cm.exception))
        self.assertIn("backups", str(cm.exception))
        self.assertEqual(snapshot(self.d), self.changed)

    def test_replace_saves_current_state_first_and_going_back_restores_it_exactly(self):
        res = backup.restore(self.good, self.d, replace=True)
        safety = Path(res["safety_backup"])
        self.assertTrue(safety.exists())
        self.assertEqual(safety.parent, self.d / "backups")
        self.assertTrue(safety.name.startswith("before-restore-"))
        self.assertNotEqual(snapshot(self.d), self.changed)
        self.assertEqual(json.loads((self.d / "profile.json").read_text())["firstName"], "Ann")
        backup.restore(safety, self.d, replace=True)  # fail back
        self.assertEqual(snapshot(self.d), self.changed)

    def test_restore_removes_files_the_backup_does_not_have(self):
        (self.d / "qc_rules.json").write_text("{}", encoding="utf-8")
        (self.d / "resumes" / "extra.txt").write_text("e", encoding="utf-8")
        backup.restore(self.good, self.d, replace=True)
        self.assertFalse((self.d / "qc_rules.json").exists())
        self.assertFalse((self.d / "resumes" / "extra.txt").exists())

    def test_secrets_logs_and_other_backups_are_left_alone_by_restore(self):
        backup.restore(self.good, self.d, replace=True)
        self.assertEqual((self.d / "secrets.json").read_text(), '{"imap": "hunter2"}')
        self.assertTrue((self.d / "logs" / "web.log").exists())

    def test_failure_while_applying_puts_the_old_state_back(self):
        real = backup._apply
        calls = []

        def flaky(src, dest):
            calls.append(1)
            if len(calls) == 1:
                (Path(dest) / "profile.json").write_text("half-written", encoding="utf-8")
                raise OSError("disk full")
            return real(src, dest)
        with mock.patch.object(backup, "_apply", flaky):
            with self.assertRaises(backup.BackupError) as cm:
                backup.restore(self.good, self.d, replace=True)
        self.assertIn("put back", str(cm.exception))
        self.assertEqual(snapshot(self.d), self.changed)

    def test_failure_and_failed_rollback_still_names_the_safety_file(self):
        with mock.patch.object(backup, "_apply", side_effect=OSError("disk full")):
            with self.assertRaises(backup.BackupError) as cm:
                backup.restore(self.good, self.d, replace=True)
        self.assertIn("before-restore-", str(cm.exception))

    def test_restore_is_idempotent(self):
        backup.restore(self.good, self.d, replace=True, now=dt.datetime(2026, 1, 1, 10, 0, 0))
        a = snapshot(self.d)
        backup.restore(self.good, self.d, replace=True, now=dt.datetime(2026, 1, 1, 10, 0, 1))
        self.assertEqual(snapshot(self.d), a)

    def test_only_the_newest_safety_backups_are_kept(self):
        for n in range(backup.KEEP_BEFORE_RESTORE + 3):
            backup.restore(self.good, self.d, replace=True, now=dt.datetime(2026, 1, 1, 10, 0, n))
        kept = list((self.d / "backups").glob("before-restore-*.ccbackup"))
        self.assertEqual(len(kept), backup.KEEP_BEFORE_RESTORE)

    def test_fresh_install_starter_config_is_not_treated_as_data(self):
        fresh = self.root / "fresh"
        fresh.mkdir()
        (fresh / "config.toml").write_text(config.STARTER, encoding="utf-8")
        self.assertFalse(backup.has_data(fresh))
        res = backup.restore(self.good, fresh)
        self.assertIsNone(res["safety_backup"])

    def test_unreadable_database_counts_as_data_so_it_is_saved_first(self):
        fresh = self.root / "fresh"
        fresh.mkdir()
        (fresh / backup.DB).write_bytes(b"garbage" * 50)
        self.assertTrue(backup.has_data(fresh))

    def test_reentry_hints_follow_the_restored_settings(self):
        cfg = config.load(self.d / "config.toml")
        self.assertEqual(backup.reentry_hints(cfg), ["email password"])


class AutoBackup(Base):
    def test_one_per_day_pruned_and_copied_to_the_extra_folder(self):
        extra = self.root / "usb"
        for day in range(1, 11):
            backup.auto(self.d, keep=3, extra_dir=str(extra), now=dt.datetime(2026, 3, day, 8, 0))
            backup.auto(self.d, keep=3, extra_dir=str(extra), now=dt.datetime(2026, 3, day, 9, 0))  # same day: no second file
        mine = sorted(p.name for p in (self.d / "backups").glob("auto-*"))
        self.assertEqual(mine, ["auto-2026-03-08.ccbackup", "auto-2026-03-09.ccbackup", "auto-2026-03-10.ccbackup"])
        self.assertEqual(len(list(extra.glob("auto-*"))), 3)
        backup.verify(extra / "auto-2026-03-10.ccbackup")

    def test_unwritable_extra_folder_is_a_note_not_a_crash(self):
        blocker = self.root / "file"
        blocker.write_text("x")
        notes = backup.auto(self.d, extra_dir=str(blocker / "sub"), now=dt.datetime(2026, 3, 1))
        self.assertTrue(notes and "Could not copy" in notes[0])
        self.assertTrue((self.d / "backups" / "auto-2026-03-01.ccbackup").exists())

    def test_manual_backups_are_pruned_to_their_own_limit(self):
        for i in range(1, 6):
            backup.create(self.d, self.d / "backups" / f"manual-2026030{i}.ccbackup")
        backup.prune(self.d, keep=7, keep_manual=2)
        self.assertEqual(sorted(p.name for p in (self.d / "backups").glob("manual-*")), ["manual-20260304.ccbackup", "manual-20260305.ccbackup"])

    def test_listing_marks_where_each_backup_is_and_restore_can_read_the_copy_folder(self):
        extra = self.root / "usb"
        backup.auto(self.d, extra_dir=str(extra), now=dt.datetime(2026, 3, 1))
        where = {(b["name"], b["where"]) for b in backup.listing(self.d, str(extra))}
        self.assertEqual(where, {("auto-2026-03-01.ccbackup", "data"), ("auto-2026-03-01.ccbackup", "copy")})
        self.assertEqual(backup.local_path(self.d, "auto-2026-03-01.ccbackup", str(extra), "copy").parent, extra)
        with self.assertRaises(backup.BackupError):
            backup.local_path(self.d, "auto-2026-03-01.ccbackup", "", "copy")

    def test_nothing_to_back_up_means_no_file(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(backup.auto(empty), [])
        self.assertFalse((empty / "backups").exists())

    def test_names_are_validated(self):
        backup.create(self.d, self.d / "backups" / "manual-1.ccbackup")
        self.assertTrue(backup.local_path(self.d, "manual-1.ccbackup").is_file())
        for evil in ("../config.toml", "..\\x.ccbackup", "a/b.ccbackup", "", None, 5, "x.zip", "manual-1.ccbackup/..", "nul.ccbackup"):
            with self.assertRaises(backup.BackupError, msg=repr(evil)):
                backup.local_path(self.d, evil)


class Config(unittest.TestCase):
    def test_backup_section(self):
        c = config.validate({"backup": {"keep": 5, "extra_dir": "D:/b"}})
        self.assertEqual((c.backup.keep, c.backup.extra_dir), (5, "D:/b"))
        for bad in ({"keep": 0}, {"keep": 61}, {"extra_dir": "a\nb"}, {"extra_dir": "x" * 501}, {"keep": "7"}, {"api_key": "x"}):
            with self.assertRaises(config.ConfigError):
                config.validate({"backup": bad})


class Drafts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        self.raw = {"fields": {"firstName": {"value": "Ann", "ok": False}}, "jobs": [{"employer": "Acme", "title": "Dev", "dates": "2020", "facts": [], "ok": False}],
                    "skills": {"value": "python", "ok": False}}

    def test_write_read_clear(self):
        self.assertIsNone(drafts.read(self.d, "profile"))
        drafts.write(self.d, "profile", self.raw)
        st = drafts.read(self.d, "profile")
        self.assertEqual(st["fields"]["firstName"]["value"], "Ann")
        drafts.clear(self.d, "profile")
        self.assertIsNone(drafts.read(self.d, "profile"))
        drafts.clear(self.d, "profile")  # twice is fine

    def test_hostile_input(self):
        for bad in (None, [], "x", {"fields": []}, {"fields": {"nope": {}}}, {"fields": {"firstName": "x"}}, {"jobs": "x"}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                drafts.write(self.d, "profile", bad)
        with self.assertRaises(ValueError):
            drafts.write(self.d, "../x", self.raw)
        self.assertFalse((self.d / "drafts").exists() and list((self.d / "drafts").glob("*.json")))

    def test_damaged_draft_is_ignored(self):
        (self.d / "drafts").mkdir()
        for content in (b"{", b"\xff\xfe\x00", b"[]", b"[" * 100000, b'{"fields": {"zzz": 1}}'):
            (self.d / "drafts" / "profile.json").write_bytes(content)
            self.assertIsNone(drafts.read(self.d, "profile"))

    def test_saving_the_profile_for_real_removes_the_draft(self):
        drafts.write(self.d, "profile", self.raw)
        profile.save(self.d, self.raw)
        self.assertIsNone(drafts.read(self.d, "profile"))
        self.assertTrue((self.d / "profile.json").exists())

    def test_a_draft_never_writes_the_profile_files(self):
        drafts.write(self.d, "profile", self.raw)
        self.assertFalse((self.d / "profile.json").exists())
        self.assertFalse((self.d / "Candidate_Profile.md").exists())


class Http(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = make_data(self.tmp.name)
        (self.d / "profile.json").unlink()  # hand-written files are never overwritten by the wizard
        (self.d / "Candidate_Profile.md").unlink()
        self.srv = web.make_server(self.d, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.srv.shutdown(), self.srv.server_close()))

    def call(self, method, path, body=None, raw=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = dict(headers or {})
        data = raw if raw is not None else (None if body is None and method == "GET" else json.dumps(body if body is not None else {}))
        c.request(method, path, data, h)
        r = c.getresponse()
        out = r.read()
        c.close()
        try:
            return r.status, json.loads(out)
        except ValueError:
            return r.status, out

    def test_draft_then_reload_shows_the_draft_and_saving_clears_it(self):
        raw = {"fields": {"firstName": {"value": "Draft Ann", "ok": False}}, "jobs": [], "skills": {"value": "", "ok": False}}
        self.assertEqual(self.call("POST", "/wizard/draft", {"form": "profile", "data": raw})[0], 200)
        st, j = self.call("GET", "/wizard/profile")
        self.assertTrue(j["draft"])
        self.assertEqual(j["state"]["fields"]["firstName"]["value"], "Draft Ann")
        self.assertEqual(self.call("POST", "/wizard/profile", raw)[0], 200)
        st, j = self.call("GET", "/wizard/profile")
        self.assertFalse(j["draft"])
        self.assertEqual(j["state"]["fields"]["firstName"]["value"], "Draft Ann")

    def test_draft_endpoint_rejects_bad_bodies(self):
        for body in ({}, {"form": "rules", "data": {}}, {"form": "profile"}, {"form": "profile", "data": []},
                     {"form": "profile", "data": {"fields": {"zzz": {}}}}, {"form": ["profile"], "data": {}}):
            self.assertEqual(self.call("POST", "/wizard/draft", body)[0], 400, body)
        self.assertEqual(self.call("POST", "/wizard/draft", raw="not json")[0], 400)
        self.assertEqual(self.call("POST", "/wizard/draft", body={"form": "profile", "data": {}}, headers={"Origin": "http://evil.example"})[0], 403)

    def test_create_list_download_and_the_notices(self):
        st, j = self.call("POST", "/backup/create")
        self.assertEqual(st, 200)
        self.assertIn("NOT in this backup", j["notice"])
        self.assertTrue(j["name"].startswith("manual-"))
        st, lst = self.call("GET", "/backup/list")
        self.assertEqual([b["name"] for b in lst["backups"]], [j["name"]])
        self.assertIn("before-restore-", lst["next_safety"])
        self.assertIn("API keys", lst["notice"])
        st, blob = self.call("GET", "/backup/download/" + j["name"])
        self.assertEqual(st, 200)
        self.assertEqual(blob[:2], b"PK")

    def test_list_reports_folders_and_settings_and_settings_save_and_prune(self):
        st, lst = self.call("GET", "/backup/list")
        self.assertEqual((lst["settings"]["keep"], lst["settings"]["keep_manual"]), (7, 20))
        self.assertEqual(lst["folders"]["backups"], str(self.d / "backups"))
        copy = Path(self.tmp.name) / "usb"
        for i in range(1, 5):
            backup.create(self.d, self.d / "backups" / f"manual-2026030{i}.ccbackup")
        ok = {"keep": 3, "keep_manual": 2, "extra_dir": str(copy).replace("/", "\\")}
        self.assertEqual(self.call("POST", "/backup/settings", ok)[0], 200)
        st, lst = self.call("GET", "/backup/list")
        self.assertEqual((lst["settings"]["keep"], lst["settings"]["keep_manual"]), (3, 2))
        self.assertTrue(copy.is_dir())
        self.assertEqual(sum(b["kind"] == "manual" for b in lst["backups"]), 2)
        self.assertEqual(lst["folders"]["extra"], str(copy).replace("\\", "/"))

    def test_settings_reject_bad_input_and_change_nothing(self):
        for body in ({}, {"keep": 0, "keep_manual": 5}, {"keep": 61, "keep_manual": 5}, {"keep": 5, "keep_manual": 201},
                     {"keep": "7", "keep_manual": 5}, {"keep": True, "keep_manual": 5}, {"keep": 5, "keep_manual": 5, "extra_dir": 7},
                     {"keep": 5, "keep_manual": 5, "extra_dir": "relative/dir"}, {"keep": 5, "keep_manual": 5, "extra_dir": 'C:/a"b'},
                     {"keep": 5, "keep_manual": 5, "extra_dir": "C:/x" + "y" * 600}):
            self.assertEqual(self.call("POST", "/backup/settings", body)[0], 400, body)
        self.assertEqual(self.call("POST", "/backup/settings", raw="nope")[0], 400)
        self.assertEqual(self.call("GET", "/backup/list")[1]["settings"]["keep"], 7)

    def test_open_folder_only_opens_known_folders(self):
        opened = []
        web._Handler.folder_opener = staticmethod(opened.append)
        self.addCleanup(setattr, web._Handler, "folder_opener", None)
        self.call("POST", "/backup/create")
        self.assertEqual(self.call("POST", "/backup/open", {"which": "backups"})[0], 200)
        self.assertEqual(opened, [self.d / "backups"])
        for which in ("extra", "../..", "", None, 5, ["backups"]):
            self.assertEqual(self.call("POST", "/backup/open", {"which": which})[0], 404, which)

    def test_download_names_cannot_escape_the_backups_folder(self):
        for p in ("/backup/download/..%2Fconfig.toml", "/backup/download/..%5Cconfig.toml", "/backup/download/", "/backup/download/x.ccbackup",
                  "/backup/download/%2e%2e/config.toml"):
            self.assertEqual(self.call("GET", p)[0], 404, p)

    def test_restore_from_a_listed_backup_needs_replace_and_reports_the_safety_file(self):
        (self.d / "profile.json").write_text('{"firstName": "Ann"}', encoding="utf-8")
        name = self.call("POST", "/backup/create")[1]["name"]
        (self.d / "profile.json").write_text('{"firstName": "Changed"}', encoding="utf-8")
        st, j = self.call("POST", "/backup/restore-local", {"name": name})
        self.assertEqual(st, 400)
        self.assertIn("--replace", j["error"])
        st, j = self.call("POST", "/backup/restore-local", {"name": name, "replace": True})
        self.assertEqual(st, 200, j)
        self.assertTrue(Path(j["safety_backup"]).exists())
        self.assertIn("email password", j["reenter"])
        self.assertEqual(json.loads((self.d / "profile.json").read_text())["firstName"], "Ann")
        # fail back through the API as well
        st, j2 = self.call("POST", "/backup/restore-local", {"name": Path(j["safety_backup"]).name, "replace": True})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads((self.d / "profile.json").read_text())["firstName"], "Changed")

    def test_upload_restore_and_its_limits(self):
        (self.d / "profile.json").write_text('{"firstName": "Ann"}', encoding="utf-8")
        name = self.call("POST", "/backup/create")[1]["name"]
        blob = (self.d / "backups" / name).read_bytes()
        (self.d / "profile.json").write_text('{"firstName": "Changed"}', encoding="utf-8")
        st, j = self.call("POST", "/backup/restore?replace=1", raw=blob, headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(st, 200, j)
        self.assertEqual(json.loads((self.d / "profile.json").read_text())["firstName"], "Ann")
        before = (self.d / "profile.json").read_bytes()
        for payload in (b"", b"not a backup", blob[:100], b"PK" + b"\0" * 50):
            st, j = self.call("POST", "/backup/restore?replace=1", raw=payload, headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(st, 400, payload[:10])
        self.assertEqual((self.d / "profile.json").read_bytes(), before)
        with mock.patch.object(web, "MAX_BACKUP_UPLOAD", 10):
            self.assertEqual(self.call("POST", "/backup/restore?replace=1", raw=blob)[0], 413)
        st, j = self.call("POST", "/backup/restore", raw=blob)  # no replace on a folder with data
        self.assertEqual(st, 400)
        self.assertEqual(self.call("POST", "/backup/restore?replace=1", raw=blob, headers={"Origin": "http://evil.example"})[0], 403)

    def test_restore_local_bad_bodies(self):
        for body in ({}, {"name": "../x.ccbackup"}, {"name": 5}, {"name": "missing.ccbackup", "replace": True}):
            self.assertEqual(self.call("POST", "/backup/restore-local", body)[0], 400, body)


class Cli(Base):
    def run_cli(self, *args):
        import contextlib
        import io
        from cc import cli
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"CC_HOME": str(self.d)}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_create_verify_restore_and_the_messages_users_need(self):
        f = str(self.root / "c.ccbackup")
        code, out, _ = self.run_cli("backup", "create", f)
        self.assertEqual(code, 0)
        self.assertIn("NOT in this backup", out)
        self.assertIn("not encrypted", out)
        self.assertEqual(self.run_cli("backup", "verify", f)[0], 0)
        (self.d / "profile.json").write_text('{"firstName": "Changed"}', encoding="utf-8")
        code, _, err = self.run_cli("backup", "restore", f)
        self.assertEqual(code, 1)
        self.assertIn("--replace", err)
        code, out, _ = self.run_cli("backup", "restore", f, "--replace")
        self.assertEqual(code, 0)
        self.assertIn("Your CURRENT data will be saved first to", out)
        self.assertIn("Your previous data was saved to:", out)
        self.assertIn("To go back to it", out)
        self.assertIn("Enter again in Setup: email password", out)

    def test_bad_inputs_are_short_messages_not_tracebacks(self):
        bad = self.root / "bad.ccbackup"
        bad.write_bytes(b"junk")
        for args in (("backup", "verify", str(bad)), ("backup", "restore", str(bad), "--replace"), ("backup", "verify", str(self.root / "none")),
                     ("backup", "verify"), ("backup", "restore")):
            code, _, err = self.run_cli(*args)
            self.assertIn(code, (1, 2), args)
            self.assertNotIn("Traceback", err)
        self.assertEqual(self.run_cli("backup", "list")[0], 0)

    def test_old_database_only_backup_command_still_works(self):
        code, out, _ = self.run_cli("backup", str(self.root / "dbonly"))
        self.assertEqual(code, 0)
        self.assertTrue(list((self.root / "dbonly").glob("commandcenter-*.db")))


if __name__ == "__main__":
    unittest.main()
