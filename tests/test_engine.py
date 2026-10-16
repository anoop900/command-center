"""Engine tests. Fictional data only. Run: python3 -m unittest discover tests"""
import asyncio
import json
import multiprocessing as mp
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
from cc import api, cli, importer, store

ROLE = {"company": "Acme Corp", "title": "Senior Cloud Architect", "status": "applied", "fit_pct": "85",
        "date_found": "2026-10-01", "link": "https://www.linkedin.com/jobs/view/4000000001/"}


def make_export(root):
    root = Path(root)
    rid = api.role_id(ROLE["company"], ROLE["title"])
    files = {f"roles/{rid}.json": ROLE,
             "roles/globex-sre-lead.json": {"company": "Globex", "title": "SRE Lead", "status": "rejected"},
             "contacts/c-alex.json": {"name": "Pat Doe", "type": "recruiter", "last_touch": "2026-09-01"},
             "tasks/t1.json": {"title": "Send thank-you", "due": "2026-10-01", "pri": 1},
             "board/roles.json": {"roles": [{"key": "acme corp|senior cloud architect"}]},
             "backups/page-latest.json": {"html": "<big page>"},
             "_writes.json": {"ignored": True}}
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    return rid


def _hammer(path, worker, n):
    conn = store.connect(path)
    for i in range(n):
        store.set_doc(conn, "load", f"w{worker}-{i}", {"i": i})
        while True:  # read-modify-write on one shared counter, retried on conflict
            cur = store.get(conn, "load", "counter")
            try:
                store.set_doc(conn, "load", "counter", {"n": cur["data"]["n"] + 1}, if_version=cur["version"])
                break
            except store.Conflict:
                continue


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.db"
        self.conn = store.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_versions_and_conflicts(self):
        self.assertEqual(store.set_doc(self.conn, "c", "a", {"x": 1}), 1)
        with self.assertRaises(store.Conflict):
            store.set_doc(self.conn, "c", "a", {"x": 2})            # no if_version on existing record
        with self.assertRaises(store.Conflict):
            store.set_doc(self.conn, "c", "a", {"x": 2}, if_version=9)
        self.assertEqual(store.set_doc(self.conn, "c", "a", {"x": 2}, if_version=1), 2)
        self.assertEqual(store.set_doc(self.conn, "c", "a", {"x": 2}, if_version=2), 2)  # unchanged: no bump
        self.assertEqual(store.update_doc(self.conn, "c", "a", {"y": 3, "x": None}, if_version=2), 3)
        self.assertEqual(store.get(self.conn, "c", "a")["data"], {"y": 3})

    def test_history_keeps_every_old_copy(self):
        store.set_doc(self.conn, "c", "a", {"x": 1})
        store.set_doc(self.conn, "c", "a", {"x": 2}, if_version=1)
        store.delete_doc(self.conn, "c", "a", if_version=2)
        h = store.history(self.conn, "c", "a")
        self.assertEqual([x["op"] for x in h], ["delete", "set"])
        self.assertEqual(h[1]["data"], {"x": 1})
        self.assertIsNone(store.get(self.conn, "c", "a"))

    def test_concurrent_writers_lose_nothing(self):
        store.set_doc(self.conn, "load", "counter", {"n": 0})
        ps = [mp.get_context("spawn").Process(target=_hammer, args=(str(self.path), w, 100)) for w in (1, 2)]
        [p.start() for p in ps]
        [p.join(120) for p in ps]
        self.assertTrue(all(p.exitcode == 0 for p in ps))
        self.assertEqual(store.get(self.conn, "load", "counter")["data"]["n"], 200)
        self.assertEqual(store.collections(self.conn)["load"], 201)

    def test_backup_is_readable_and_rotated(self):
        store.set_doc(self.conn, "c", "a", {"x": 1})
        dest = Path(self.tmp.name) / "bk"
        (dest).mkdir()
        for old in ("2026-01-01", "2026-01-02"):
            (dest / f"commandcenter-{old}.db").write_bytes(b"")
        b = store.backup(self.conn, dest, keep=2)
        bk = sqlite3.connect(b)
        try:
            self.assertEqual(bk.execute("SELECT COUNT(*) FROM docs").fetchone()[0], 1)
        finally:
            bk.close()
        self.assertEqual(len(list(dest.glob("*.db"))), 2)


class ImportAndApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.export = Path(self.tmp.name) / "export"
        self.rid = make_export(self.export)
        self.conn = store.connect(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_import_is_idempotent_and_diff_clean(self):
        r = importer.import_folder(self.conn, self.export)
        self.assertEqual(r["roles"]["added"], 2)
        self.assertNotIn("backups", r)                      # skipped by default
        self.assertEqual(importer.diff(self.conn, self.export), [])
        r2 = importer.import_folder(self.conn, self.export)
        self.assertEqual(r2["roles"], {"added": 0, "changed": 0, "same": 2})
        self.assertEqual(store.get(self.conn, "roles", self.rid)["version"], 1)

    def test_diff_reports_field_changes_and_missing(self):
        importer.import_folder(self.conn, self.export)
        cur = store.get(self.conn, "roles", self.rid)
        store.update_doc(self.conn, "roles", self.rid, {"status": "interviewing"}, if_version=cur["version"])
        store.set_doc(self.conn, "tasks", "only-here", {"title": "x"})
        d = importer.diff(self.conn, self.export)
        self.assertIn({"collection": "roles", "id": self.rid, "field": "status",
                       "export": "applied", "engine": "interviewing"}, d)
        self.assertIn({"collection": "tasks", "id": "only-here", "issue": "missing in export"}, d)

    def test_export_round_trip(self):
        importer.import_folder(self.conn, self.export)
        out = Path(self.tmp.name) / "out"
        importer.export_folder(self.conn, out)
        self.assertEqual(importer.diff(self.conn, out), [])

    def test_role_upsert_and_duplicates(self):
        importer.import_folder(self.conn, self.export)
        r = api.role_upsert(self.conn, {"company": "Acme", "title": "Sr Cloud Architect"})
        self.assertEqual(r, {"created": False, "duplicate_of": self.rid})
        r = api.role_upsert(self.conn, {"company": "Initech", "title": "Platform Lead"})
        self.assertTrue(r["created"])
        with self.assertRaises(store.Conflict):
            api.role_upsert(self.conn, {"id": r["id"], "status": "applied"})
        r2 = api.role_upsert(self.conn, {"id": r["id"], "status": "applied"}, if_version=r["version"])
        self.assertEqual(r2["version"], 2)
        self.assertEqual([x["company"] for x in api.roles_list(self.conn, open_only=True)], ["Acme Corp", "Initech"])

    def test_actions_apply_once(self):
        importer.import_folder(self.conn, self.export)
        acts = [{"id": "a1", "key": "acme corp|senior cloud architect", "action": "interview", "at": "2026-10-08"},
                {"id": "a2", "key": "todo:x", "action": "done"}]
        self.assertEqual(api.actions_apply(self.conn, acts)[0], f"{self.rid}: status -> interviewing")
        self.assertEqual(api.actions_apply(self.conn, acts), [])          # already applied
        self.assertEqual(api.role_get(self.conn, self.rid)["status"], "interviewing")

    def test_tasks_contacts_heartbeat_markdown(self):
        importer.import_folder(self.conn, self.export)
        self.assertEqual([t["id"] for t in api.tasks_due(self.conn, "2026-10-08")], ["t1"])
        self.assertEqual([c["id"] for c in api.contacts_due(self.conn, "2026-10-08")], ["c-alex"])
        api.contact_touch(self.conn, "c-alex", "2026-10-08")
        self.assertEqual(api.contacts_due(self.conn, "2026-10-08"), [])
        api.run_heartbeat(self.conn, "morning", "start", on="2026-10-08")
        self.assertIsNone(api.last_finished_run(self.conn))
        api.run_heartbeat(self.conn, "morning", "finish", {"new_roles": 3}, on="2026-10-08")
        self.assertEqual(api.last_finished_run(self.conn)["info"], {"new_roles": 3})
        md = Path(self.tmp.name) / "Pipeline_Export.md"
        self.assertEqual(api.export_markdown(self.conn, md), 1)        # rejected role excluded
        self.assertIn("| Acme Corp | Senior Cloud Architect | applied |", md.read_text())


class Cli(unittest.TestCase):
    def test_cli_import_diff_status(self):
        with tempfile.TemporaryDirectory() as t:
            os.environ["CC_HOME"] = t
            try:
                make_export(Path(t) / "export")
                self.assertEqual(cli.main(["import", str(Path(t) / "export")]), 0)
                self.assertEqual(cli.main(["diff", str(Path(t) / "export")]), 0)
                self.assertEqual(cli.main(["status"]), 0)
            finally:
                del os.environ["CC_HOME"]


@unittest.skipUnless(__import__("importlib").util.find_spec("mcp"), "mcp library not installed")
class Mcp(unittest.TestCase):
    def test_tools_through_server(self):
        from cc import mcp_server
        with tempfile.TemporaryDirectory() as t:
            os.environ["CC_HOME"] = t
            try:
                make_export(Path(t) / "export")
                importer.import_folder(mcp_server.conn(), Path(t) / "export")
                names = {x.name for x in asyncio.run(mcp_server.server.list_tools())}
                self.assertTrue({"roles_list", "role_upsert", "find_duplicate", "score_batch",
                                 "actions_apply", "run_heartbeat", "boards_poll", "boards_discover"} <= names)
                res = asyncio.run(mcp_server.server.call_tool("find_duplicate",
                                  {"company": "Acme", "title": "Sr. Cloud Architect"}))
                self.assertIn("acme-corp-senior-cloud-architect", json.dumps(res.model_dump()))
                res = asyncio.run(mcp_server.server.call_tool("role_upsert",
                                  {"fields": {"id": "acme-corp-senior-cloud-architect", "status": "x"}, "if_version": 7}))
                self.assertIn("conflict", json.dumps(res.model_dump()))
                res = asyncio.run(mcp_server.server.call_tool("boards_poll",
                                  {"targets": [{"company": "X", "ats": "nope", "token": "x"}]}))
                self.assertIn("missing or invalid", json.dumps(res.model_dump()))  # no network needed
            finally:
                mcp_server.close_all()  # Windows cannot delete a database that is still open
                del os.environ["CC_HOME"]


if __name__ == "__main__":
    unittest.main()
