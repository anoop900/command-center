"""Build kit reads the job description itself from known job sites; anything else asks for a paste, with a reason."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cc import jobs, store
from cc.ingest import fetchposting as fp
from cc.runs import backfill
from tests import test_jobs
from tests.test_jobs import runner

DESC = "Lead the Azure platform team. " * 12
LINKEDIN = f'<html><div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5">{DESC}</div></html>'


def opener_for(pages):
    calls = []

    def op(url):
        calls.append(url)
        for k, v in pages.items():
            if k in url:
                return v if isinstance(v, tuple) else (200, v)
        return 404, ""
    op.calls = calls
    return op


class FetchTests(unittest.TestCase):
    def test_known_hosts_only(self):
        for ok in ("https://www.linkedin.com/comm/jobs/view/4478343830/", "https://ca.indeed.com/viewjob?jk=1",
                   "https://acme.wd3.myworkdayjobs.com/en-US/Ext/job/Toronto/Lead_JR1"):
            self.assertTrue(fp.known(ok), ok)
        for bad in ("http://www.linkedin.com/jobs/view/1", "https://evil.example/linkedin.com", "https://linkedin.com.evil.example/x",
                    "https://localhost/x", "file:///etc/passwd", "", None, "https://127.0.0.1/x"):
            self.assertFalse(fp.known(bad), bad)

    def test_unknown_host_is_never_requested(self):
        op = opener_for({})
        text, why = fp.fetch("https://careers.example.com/job/1", op)
        self.assertEqual((text, op.calls), ("", []))
        self.assertIn("not on a site", why)

    def test_linkedin_alert_link_reads_the_guest_description(self):
        op = opener_for({"jobs-guest/jobs/api/jobPosting/4478343830": LINKEDIN})
        text, why = fp.fetch("https://www.linkedin.com/comm/jobs/view/4478343830/?trackingId=x", op)
        self.assertEqual(why, "")
        self.assertIn("Azure platform team", text)

    def test_workday_link_uses_the_detail_api(self):
        body = json.dumps({"jobPostingInfo": {"jobDescription": f"<p>{DESC}</p>"}})
        op = opener_for({"/wday/cxs/acme/Ext/job/Toronto/Lead_JR1": body})
        text, why = fp.fetch("https://acme.wd3.myworkdayjobs.com/en-US/Ext/job/Toronto/Lead_JR1", op)
        self.assertEqual(why, "")
        self.assertIn("Azure platform team", text)

    def test_json_ld_page(self):
        page = '<script type="application/ld+json">' + json.dumps({"@type": "JobPosting", "description": DESC}) + "</script>"
        text, _ = fp.fetch("https://www.builtin.com/job/lead/1", opener_for({"builtin.com": page}))
        self.assertIn("Azure platform team", text)

    def test_blocked_gone_and_sign_in_walls_give_a_reason(self):
        for status, words in ((403, "refused"), (401, "refused"), (410, "gone")):
            text, why = fp.fetch("https://ca.indeed.com/viewjob?jk=1", opener_for({"indeed": (status, "")}))
            self.assertEqual(text, "")
            self.assertIn(words, why)
        text, why = fp.fetch("https://ca.indeed.com/viewjob?jk=1", opener_for({"indeed": "<html><body>Sign in to continue</body></html>"}))
        self.assertEqual(text, "")
        self.assertIn("sign-in", why)

    def test_hostile_input_never_raises(self):
        for link in (None, 5, "", "\x00", "https://", "https:///x", "https://www.linkedin.com/jobs/view/" + "9" * 5000):
            text, why = fp.fetch(link, opener_for({"linkedin": "<<<>>>"}))
            self.assertEqual(text, "")
            self.assertTrue(why)

        def down(url):
            raise OSError("down")
        text, why = fp.fetch("https://www.linkedin.com/jobs/view/1234567", down)
        self.assertEqual(text, "")
        self.assertIn("Could not read", why)

    def test_internal_addresses_refused_when_not_injected(self):
        with mock.patch.object(fp.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 0))]):
            self.assertEqual(fp.fetch("https://www.linkedin.com/jobs/view/1234567")[0], "")


class JobFetchTests(test_jobs.JobTests):
    """Roles shaped like the real ones: an alert-mail role with a link and no text."""

    def role(self, link, **kw):
        store.set_doc(self.conn, "roles", "alert-role", dict({"key": "Contoso|Alert", "company": "Contoso", "title": "Alert",
                                                              "status": "build on request", "link": link}, **kw), force=True)

    def start_alert(self, posting=""):
        return jobs.start(self.db, self.cfg, "kit", "alert-role", posting, runner)

    def test_alert_role_builds_without_a_paste(self):
        self.role("https://www.linkedin.com/comm/jobs/view/4478343830/")
        with mock.patch.object(jobs.fetchposting, "fetch", return_value=(DESC, "")):
            self.assertEqual(self.start_alert()["state"], "running")
            j = self.state(rid="alert-role")
        self.assertNotEqual(j["state"], "needs_posting")
        self.assertIn("Azure platform team", store.get(self.conn, "roles", "alert-role")["data"]["posting"])

    def test_blocked_fetch_asks_for_a_paste_with_the_reason_and_can_be_retried(self):
        self.role("https://ca.indeed.com/viewjob?jk=1")
        with mock.patch.object(jobs.fetchposting, "fetch", return_value=("", "The job site refused the automatic read (HTTP 403).")):
            self.start_alert()
            j = self.state(rid="alert-role")
            self.assertEqual(j["state"], "needs_posting")
            self.assertIn("403", j["reason"])
            self.start_alert()          # pressing Build kit again is never refused
            self.assertEqual(self.state(rid="alert-role")["state"], "needs_posting")
        self.assertEqual(self.start_alert("Pasted description of the role")["state"], "running")
        self.state(rid="alert-role")        # let the worker finish before the folder is removed

    def test_unreadable_link_asks_at_once_with_a_reason(self):
        for link in ("", "https://careers.example.com/j/1"):
            self.role(link)
            out = self.start_alert()
            self.assertEqual(out["state"], "needs_posting")
            self.assertTrue(out["reason"])


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(Path(self.tmp.name) / "c.db")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)
        rows = [("https://www.linkedin.com/jobs/view/1234567", {}), ("https://ca.indeed.com/viewjob?jk=1", {}),
                ("https://careers.example.com/j", {}), ("https://www.linkedin.com/jobs/view/7654321", {"posting": "have it"}),
                ("https://www.linkedin.com/jobs/view/1111111", {"status": "applied"})]
        for i, (link, extra) in enumerate(rows):
            store.set_doc(self.conn, "roles", f"r{i}", dict({"company": "Contoso", "title": f"T{i}", "status": "build on request", "link": link}, **extra))

    def test_fetches_only_open_roles_without_text_on_known_sites_once_a_day(self):
        calls = []

        def fetch(link):
            calls.append(link)
            return (DESC, "") if "linkedin" in link else ("", "refused")
        backfill.run(self.conn, "2026-10-14", fetch)
        self.assertEqual(sorted(calls), ["https://ca.indeed.com/viewjob?jk=1", "https://www.linkedin.com/jobs/view/1234567"])
        self.assertIn("Azure platform", store.get(self.conn, "roles", "r0")["data"]["posting"])
        self.assertEqual(store.get(self.conn, "roles", "r1")["data"]["posting_why"], "refused")
        self.assertEqual(store.get(self.conn, "roles", "r3")["data"]["posting"], "have it")
        backfill.run(self.conn, "2026-10-14", fetch)        # same day: no repeat
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
