"""Run with mail, calendar and boards wired in (fixtures only, no network)."""
import datetime as dt
import email
import os
import tempfile
import unittest
from email import policy
from pathlib import Path

from cc import store
from cc.rules import job_key, score
from cc.runs import morning
from tests.test_boards import GH, LEVER, ASHBY, RULES, opener

HERE = Path(__file__).parent
FIX = HERE / "fixtures" / "mail"
TODAY = dt.date(2026, 10, 9)
NOW = dt.datetime(2026, 10, 9, 7, 30)
CFG = score.load_config(str(HERE / "fixtures" / "config.test.toml"))
CFG["boards"] = RULES
ROUTES = {"boards/acme/jobs": GH, "postings/beta": LEVER, "job-board/gamma": ASHBY}
PROFILE = (HERE.parent / "demo" / "Demo_Profile.md").read_text(encoding="utf-8")


def msg(name):
    return email.message_from_bytes((FIX / name).read_bytes(), policy=policy.default)


class RunIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_(self, mode="morning", **kw):
        return morning.run(self.conn, mode, TODAY, NOW, **kw)

    def roles(self):
        return {d["id"]: d["data"] for d in store.list_docs(self.conn, "roles")}

    def today(self):
        return store.get(self.conn, "board", "today")["data"]

    def test_status_mail_updates_roles_and_counts(self):
        store.set_doc(self.conn, "roles", "sample-industries-program-lead", {
            "key": job_key.key("Sample Industries", "Program Lead"), "company": "Sample Industries", "title": "Program Lead",
            "status": "applied", "date_applied": "2026-10-01", "date_found": "2026-09-30"})
        store.set_doc(self.conn, "roles", "example-corp-delivery-manager", {
            "key": job_key.key("Example Corp", "Delivery Manager"), "company": "Example Corp", "title": "Delivery Manager",
            "status": "applied", "date_applied": "2026-10-02", "date_found": "2026-09-30"})
        msgs = [msg("workday_received.eml"), msg("greenhouse_rejected.eml"), msg("lever_interview.eml")]
        self.run_(msgs=msgs)
        r = self.roles()
        self.assertEqual(r["sample-industries-program-lead"]["status"], "rejected")
        self.assertEqual(r["example-corp-operations-manager"]["date_applied"], "2026-10-09")
        self.assertIn("Interview invite", " ".join(t["title"] for t in self.today()["todos"]))
        self.assertEqual(r["example-corp-delivery-manager"]["status"], "interviewing (invite by email)")
        tot = self.today()["week"]["totals"]
        self.assertEqual((tot["replies"], tot["interviews"]), (1, 1))

    def test_unmatched_status_mail_becomes_a_note(self):
        self.run_(msgs=[msg("lever_interview.eml")])
        self.assertTrue(any("no matching role" in n["text"] for n in self.today()["notes"]))
        self.assertEqual(self.today()["week"]["totals"]["interviews"], 0)

    def test_rerun_is_idempotent(self):
        msgs = [msg("workday_received.eml"), msg("greenhouse_rejected.eml")]
        self.run_(msgs=msgs)
        snap = {d["id"]: d["version"] for c in ("roles", "runs", "board") for d in store.list_docs(self.conn, c)}
        self.run_(msgs=msgs)
        self.assertEqual(snap, {d["id"]: d["version"] for c in ("roles", "runs", "board") for d in store.list_docs(self.conn, c)})

    def test_alerts_and_boards_go_through_gates(self):
        for co, ats in (("Acme", "greenhouse"), ("Beta", "lever"), ("Gamma", "ashby")):
            store.set_doc(self.conn, "targets", co.lower(), {"company": co, "ats": ats, "token": co.lower()})
        out = self.run_(msgs=[msg("linkedin_alert.eml")], score_cfg=CFG, profile=PROFILE, opener=opener(ROUTES))
        self.assertGreaterEqual(out["added"], 3)
        self.assertIn("example-corp-operations-manager", self.roles())
        self.assertEqual(self.roles()["example-corp-operations-manager"]["status"], "build on request")

    def test_midday_skips_board_search(self):
        store.set_doc(self.conn, "targets", "acme", {"company": "Acme", "ats": "greenhouse", "token": "acme"})
        self.run_("midday", score_cfg=CFG, profile=PROFILE, opener=opener(ROUTES))
        self.assertEqual(self.roles(), {})

    def test_alerts_without_scoring_config_are_not_added(self):
        self.run_(msgs=[msg("linkedin_alert.eml")])
        self.assertEqual(self.roles(), {})
        self.assertTrue(any(n["kind"] == "action" for n in self.today()["notes"]))

    def test_calendar_interviews_become_agenda_and_failures_become_notes(self):
        self.run_(ics_text=(FIX / "calendar.ics").read_text(), errors=["Mail fetch failed: timeout"])
        t = self.today()
        self.assertEqual([a["title"] for a in t["agenda"]], ["Interview with Example Corp"])
        self.assertTrue(any("Mail fetch failed" in n["text"] for n in t["notes"]))

    def test_hostile_alert_adds_no_unsafe_link(self):
        self.run_(msgs=[msg("hostile_alert.eml")], score_cfg=CFG, profile=PROFILE, opener=opener({}))
        for r in self.roles().values():
            self.assertTrue(r["link"] == "" or r["link"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()


class Demo(unittest.TestCase):
    def test_demo_seed_fills_dashboard_without_ai(self):
        from cc import importer
        with tempfile.TemporaryDirectory() as tmp:
            conn = store.connect(os.path.join(tmp, "d.db"))
            importer.import_folder(conn, HERE.parent / "demo" / "seed")
            out = morning.run(conn, "morning", dt.date.today())
            doc = store.get(conn, "board", "today")["data"]
            self.assertEqual(out["roles"], 5)
            self.assertTrue(doc["todos"])
            self.assertEqual(len(store.get(conn, "board", "roles")["data"]["roles"]), 5)
            for col in ("contacts", "tasks", "offers"):  # the People, Tasks and Offers tabs need demo rows too
                self.assertTrue(store.list_docs(conn, col), col)
            conn.close()


class Sources(unittest.TestCase):
    def test_failures_become_notes_and_data_dir_files_are_read(self):
        from types import SimpleNamespace as NS
        from cc.runs import sources
        cfg = NS(mail=NS(host="h", user="u", folder="INBOX"), calendar=NS(ics_configured=True))
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.update(CC_HOME=tmp, CC_SECRET_MAIL_PASSWORD="x", CC_SECRET_CALENDAR_URL="https://example.com/a.ics")
            try:
                (Path(tmp) / "Candidate_Profile.md").write_text("Kubernetes", encoding="utf-8")
                boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
                got = sources.gather(cfg, "morning", TODAY, imap_fetch=boom, ics_fetch=boom)
                self.assertEqual(len(got["errors"]), 2)
                self.assertEqual((got["msgs"], got["ics_text"], got["score_cfg"], got["profile"]), (None, None, None, "Kubernetes"))
                with self.assertRaises(ValueError):
                    sources.fetch_ics("http://example.com/a.ics")
            finally:
                for k in ("CC_HOME", "CC_SECRET_MAIL_PASSWORD", "CC_SECRET_CALENDAR_URL"):
                    os.environ.pop(k, None)
