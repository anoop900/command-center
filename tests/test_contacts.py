"""Recruiter mail -> contacts (runbook 2c). Fixtures only; fictional people."""
import datetime as dt
import email
import os
import tempfile
import unittest
from email import policy
from pathlib import Path
from types import SimpleNamespace

from cc import store
from cc.ingest import imap, recruiter
from cc.runs import contacts
from tests.test_ingest import FakeConn, MAIL

FIX = Path(__file__).parent / "fixtures" / "mail"
TODAY = dt.date(2026, 10, 10)


def msg(n):
    return email.message_from_bytes((FIX / f"{n}.eml").read_bytes(), policy=policy.default)


class Classify(unittest.TestCase):
    def test_recruiter_company_from_domain(self):
        c = recruiter.classify(msg("recruiter_intro"))
        self.assertEqual((c["name"], c["email"], c["company"], c["kind"]), ("Sam Rivera", "sam@talentbridge.co.uk", "Talentbridge", "recruiter"))

    def test_freemail_has_no_company_and_hiring_kind(self):
        c = recruiter.classify(msg("recruiter_freemail"))
        self.assertEqual((c["company"], c["kind"]), ("", "hiring"))

    def test_bulk_ats_and_alerts_rejected(self):
        for n in ("promo_noreply", "known_contact_reply", "greenhouse_rejected", "linkedin_alert", "spoofed"):
            self.assertIsNone(recruiter.classify(msg(n)), n)

    def test_header_test(self):
        self.assertTrue(recruiter.looks_recruiter(msg("recruiter_intro")))
        self.assertFalse(recruiter.looks_recruiter(msg("promo_noreply")))
        self.assertFalse(recruiter.looks_recruiter(msg("known_contact_reply")))
        self.assertTrue(recruiter.looks_recruiter(msg("known_contact_reply"), ["Casey@TalentBridge.co.uk"]))


class Apply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def all(self):
        return {d["id"]: d["data"] for d in store.list_docs(self.conn, "contacts")}

    def test_new_recruiters_added_once_and_nothing_else(self):
        msgs = [msg("recruiter_intro"), msg("recruiter_freemail"), msg("promo_noreply")]
        self.assertEqual(contacts.apply(self.conn, msgs, TODAY), {"added": 2, "touched": 0})
        snap = {d["id"]: d["version"] for d in store.list_docs(self.conn, "contacts")}
        self.assertEqual(contacts.apply(self.conn, msgs, TODAY), {"added": 0, "touched": 0})
        self.assertEqual(snap, {d["id"]: d["version"] for d in store.list_docs(self.conn, "contacts")})
        c = self.all()["sam-rivera-talentbridge"]
        self.assertEqual((c["last_touch"], c["cadence"], c["channel"]), ("2026-10-09", 7, "email"))

    def test_known_contact_touch_moves_forward_only_and_keeps_nudge_off(self):
        store.set_doc(self.conn, "contacts", "casey", {"name": "Casey Ng", "email": "casey@talentbridge.co.uk",
                      "last_touch": "2026-10-01", "snooze_until": "2026-10-20", "nudge_off": True})
        self.assertEqual(contacts.apply(self.conn, [msg("known_contact_reply")], TODAY)["touched"], 1)
        c = self.all()["casey"]
        self.assertEqual((c["last_touch"], c.get("snooze_until"), c["nudge_off"]), ("2026-10-10", None, True))
        store.set_doc(self.conn, "contacts", "casey", {**c, "last_touch": "2026-10-15"}, if_version=store.get(self.conn, "contacts", "casey")["version"])
        contacts.apply(self.conn, [msg("known_contact_reply")], TODAY)
        self.assertEqual(self.all()["casey"]["last_touch"], "2026-10-15")

    def test_hostile_text_is_data_only(self):
        contacts.apply(self.conn, [msg("recruiter_hostile")], TODAY)
        self.assertNotIn("steal", str(self.all()))


class ImapExtra(unittest.TestCase):
    def test_extra_widens_reads_only_for_accepted_headers(self):
        conn = FakeConn([(FIX / f"{n}.eml").read_bytes() for n in ("recruiter_intro", "promo_noreply", "linkedin_alert")])
        out = imap.fetch(MAIL, "pw", dt.date(2026, 10, 1), opener=lambda h: conn, extra=recruiter.looks_recruiter)
        self.assertEqual(len(out), 2)  # recruiter + known alert; the promo body was never fetched
        bodies = [c for c in conn.calls if c[0] == "fetch" and "HEADER" not in c[1]]
        self.assertEqual(len(bodies), 2)
        self.assertTrue(all("PEEK" in c[1] for c in bodies))


if __name__ == "__main__":
    unittest.main()
