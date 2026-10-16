"""Tests for cc/ingest mail and calendar parsers on synthetic fixtures. Run: python -m unittest discover tests"""
import datetime as dt
import email
import unittest
from email import policy
from pathlib import Path
from types import SimpleNamespace

from cc.ingest import alerts, ics, imap, mailparse, status

FIX = Path(__file__).parent / "fixtures" / "mail"


def load(name):
    return email.message_from_bytes((FIX / name).read_bytes(), policy=policy.default)


class AlertTests(unittest.TestCase):
    def test_each_source(self):
        for name, source, n, title in [("linkedin_alert.eml", "linkedin", 2, "Operations Manager"),
                                       ("indeed_alert.eml", "indeed", 1, "Delivery Manager"),
                                       ("glassdoor_alert.eml", "glassdoor", 1, "Project Manager"),
                                       ("ziprecruiter_alert.eml", "ziprecruiter", 1, "Analyst")]:
            with self.subTest(name):
                jobs = alerts.parse_alert(load(name))
                self.assertEqual(len(jobs), n)
                self.assertEqual((jobs[0]["source"], jobs[0]["title"]), (source, title))
                self.assertTrue(jobs[0]["company"])

    def test_fields_and_tracking_stripped(self):
        j = alerts.parse_alert(load("linkedin_alert.eml"))[1]
        self.assertEqual((j["company"], j["location"]), ("Sample Industries", "Austin, TX"))
        self.assertEqual(j["url"], "https://www.linkedin.com/comm/jobs/view/2222")
        self.assertEqual(alerts.parse_alert(load("indeed_alert.eml"))[0]["url"],
                         "https://www.indeed.com/rc/clk?jk=abc123")

    def test_spoofed_sender_ignored(self):
        self.assertEqual(alerts.parse_alert(load("spoofed.eml")), [])
        self.assertIsNone(status.parse_status(load("spoofed.eml")))

    def test_hostile_links_dropped_and_text_stays_data(self):
        jobs = alerts.parse_alert(load("hostile_alert.eml"))
        self.assertEqual([j["url"] for j in jobs], ["https://www.linkedin.com/jobs/view/4"])  # no js:, off-domain, http
        self.assertIn("Ignore previous", jobs[0]["title"])  # kept as inert data for the later prompt wrapper

    def test_garbage_never_raises(self):
        for raw in (b"", b"From: a@linkedin.com\n\n\x00\xff<a href=", b"Subject: x\n\n"):
            msg = email.message_from_bytes(raw, policy=policy.default)
            self.assertIsInstance(alerts.parse_alert(msg), list)
            status.parse_status(msg)


class StatusTests(unittest.TestCase):
    def test_kinds(self):
        for name, ats, kind in [("workday_received.eml", "workday", "received"),
                                ("greenhouse_rejected.eml", "greenhouse", "rejected"),
                                ("lever_interview.eml", "lever", "interview"),
                                ("successfactors_received.eml", "successfactors", "received")]:
            with self.subTest(name):
                r = status.parse_status(load(name))
                self.assertEqual((r["ats"], r["kind"]), (ats, kind))

    def test_company_and_title(self):
        r = status.parse_status(load("workday_received.eml"))
        self.assertEqual((r["company"], r["title"]), ("Example Corp", "Operations Manager"))

    def test_non_ats_is_none(self):
        self.assertIsNone(status.parse_status(load("linkedin_alert.eml")))

    def test_message_id(self):
        self.assertEqual(mailparse.message_id(load("lever_interview.eml")), "<lever_interview.eml@fixture>")
        m = email.message_from_string("From: a@b.c\nSubject: s\n\nx")
        self.assertTrue(mailparse.message_id(m).startswith("synthetic-"))


class IcsTests(unittest.TestCase):
    def setUp(self):
        text = (FIX / "calendar.ics").read_text(encoding="utf-8")
        self.events, self.skipped = ics.parse_ics(text, dt.date(2026, 10, 10))

    def test_recurrence_exdate_and_window(self):
        days = [e["start"][:10] for e in self.events if e["summary"] == "Standup"]
        self.assertEqual(days, ["2026-10-12", "2026-10-14", "2026-10-19", "2026-10-21"])  # 10-07 excluded, ends at 24th

    def test_all_day_and_skips(self):
        self.assertTrue(next(e for e in self.events if e["summary"] == "Holiday")["all_day"])
        self.assertEqual(self.skipped, 2)  # unsupported MONTHLY rule, unparseable DTSTART

    def test_interview_detection(self):
        iv = ics.interviews(self.events)
        self.assertEqual([e["summary"] for e in iv], ["Interview with Example Corp"])
        self.assertIn("hiring, manager", iv[0]["description"])

    def test_garbage(self):
        for t in ("", "BEGIN:VEVENT", "not a calendar\x00", None):
            self.assertEqual(ics.parse_ics(t, dt.date(2026, 10, 10))[0], [])


class FakeConn:
    def __init__(self, msgs):
        self.msgs, self.calls = msgs, []

    def login(self, u, p):
        self.calls.append("login")

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", readonly))
        return "OK", [b"1"]

    def search(self, charset, *crit):
        self.calls.append(("search", crit))
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.msgs)))]

    def fetch(self, i, what):
        self.calls.append(("fetch", what))
        raw = self.msgs[int(i) - 1]
        if "HEADER" in what:
            head = raw.split(b"\n\n")[0] + b"\n\n"
            return "OK", [(b"1 (RFC822.SIZE %d" % len(raw), head), b")"]
        return "OK", [(b"1 (BODY[]", raw), b")"]

    def logout(self):
        self.calls.append("logout")

    def store(self, *a):
        raise AssertionError("STORE must never be reachable")


MAIL = SimpleNamespace(host="imap.example.com", user="u@example.com", folder="INBOX")


class ImapTests(unittest.TestCase):
    def run_fetch(self, names, **kw):
        conn = FakeConn([(FIX / n).read_bytes() for n in names])
        out = imap.fetch(MAIL, "pw", dt.date(2026, 10, 1), opener=lambda host: conn, **kw)
        return conn, out

    def test_only_known_senders_and_peek_only(self):
        conn, out = self.run_fetch(["linkedin_alert.eml", "spoofed.eml", "lever_interview.eml"])
        self.assertEqual(len(out), 2)
        self.assertIn(("select", True), conn.calls)  # EXAMINE
        self.assertTrue(all("PEEK" in c[1] for c in conn.calls if c[0] == "fetch"))
        self.assertIn(("search", ("SINCE", "01-Oct-2026")), conn.calls)

    def test_write_commands_refused(self):
        ro = imap.ReadOnly(FakeConn([]))
        for cmd in ("store", "expunge", "copy", "append", "delete", "create"):
            with self.assertRaises(imap.IngestError):
                getattr(ro, cmd)

    def test_size_cap(self):
        _, out = self.run_fetch(["linkedin_alert.eml"], max_size=10)
        self.assertEqual(out, [])

    def test_failures_become_ingest_error(self):
        def boom(host):
            raise TimeoutError("timed out")
        with self.assertRaisesRegex(imap.IngestError, "mail unavailable"):
            imap.fetch(MAIL, "pw", dt.date(2026, 10, 1), opener=boom)
        with self.assertRaisesRegex(imap.IngestError, "not configured"):
            imap.fetch(MAIL, "", dt.date(2026, 10, 1), opener=boom)


if __name__ == "__main__":
    unittest.main()
