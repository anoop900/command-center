"""Profile completeness (landing gate) and the Learning & Improvement engine, on fictional data."""
import base64
import datetime as dt
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from cc import learning, profile, resume, store, web

TODAY = dt.date(2026, 6, 30)


def full_form():
    f = {k: {"value": "Yes" if profile.KINDS[k] == "yesno" else "x", "ok": True} for k in profile.CRITICAL}
    f["email"] = {"value": "jordan@example.com", "ok": True}
    return {"fields": f, "jobs": [{"employer": "Northwind", "title": "Director", "dates": "", "facts": [], "ok": True}],
            "skills": {"value": "AWS", "ok": True}}


def make_complete(d):
    profile.save(d, full_form())
    (Path(d) / "scoring.toml").write_text("[scoring]\n", encoding="utf-8")
    resume.store(d, "Jordan Example Resume.pdf", b"%PDF-")


def role(i, status="applied", **kw):
    r = {"company": f"Co{i}", "title": "Director of Platform", "status": status, "decision": "built", "source": "LinkedIn",
         "work_mode": "remote", "fit_pct": "75%", "date_found": "2026-06-01", "date_applied": "2026-06-03",
         "follow_up": "2026-07-10", "resume_file": "r.pdf", "cover_letter": "c.pdf", "key": f"co{i}|dir"}
    r.update(kw)
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = store.connect(self.dir / "commandcenter.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def put(self, col, id_, data):
        store.set_doc(self.conn, col, id_, data, by="test", force=True)

    def run_(self):
        return learning.analyze(self.conn, self.dir, TODAY)

    def ids(self):
        return {a["id"] for a in self.run_()["areas"]}


class CompletenessTests(Base):
    def test_empty_profile_is_zero_and_lists_every_item(self):
        c = profile.completeness(self.dir)
        self.assertEqual(c["percent"], 0)
        self.assertEqual(len(c["missing"]), c["total"])

    def test_only_confirmed_facts_count(self):
        f = full_form()
        f["fields"]["email"]["ok"] = False
        profile.save(self.dir, f)
        self.assertIn("email", [m["key"] for m in profile.completeness(self.dir)["missing"]])

    def test_hundred_only_when_everything_including_resume_and_rules(self):
        profile.save(self.dir, full_form())
        c = profile.completeness(self.dir)
        self.assertLess(c["percent"], 100)
        self.assertEqual({m["key"] for m in c["missing"]}, {"resume", "rules"})
        (self.dir / "scoring.toml").write_text("[scoring]\n", encoding="utf-8")
        self.assertEqual({m["key"] for m in profile.completeness(self.dir)["missing"]}, {"resume"})
        resume.store(self.dir, "cv.docx", b"PK")
        self.assertEqual(profile.completeness(self.dir)["percent"], 100)

    def test_a_kit_resume_counts_as_a_resume(self):
        k = self.dir / "kits" / "2026-06-01" / "Role_A"
        k.mkdir(parents=True)
        (k / "Jordan_Resume_RoleA.pdf").write_bytes(b"%PDF-")
        self.assertTrue(profile.has_resume(self.dir))

    def test_resume_store_sanitises_the_name(self):
        name = resume.store(self.dir, "../../etc/pass wd\x00.docx", b"x")
        self.assertNotIn("/", name)
        self.assertTrue((self.dir / "resumes" / name).is_file())
        self.assertEqual([p.parent.name for p in self.dir.rglob("*.docx")], ["resumes"])

    def test_damaged_draft_is_zero_not_a_crash(self):
        (self.dir / profile.FORM_FILE).write_text("{not json", encoding="utf-8")
        self.assertEqual(profile.completeness(self.dir)["percent"], 0)


class LearningTests(Base):
    def test_empty_database_runs_and_says_early_days(self):
        d = self.run_()
        self.assertFalse(d["enough_data"])
        self.assertIn("data-volume", {a["id"] for a in d["areas"]})
        self.assertEqual(d["summary"]["applied"], 0)

    def test_profile_gap_is_the_first_finding(self):
        d = self.run_()
        self.assertEqual(d["areas"][0]["id"], "profile")
        self.assertEqual(d["areas"][0]["severity"], "high")

    def test_complete_profile_has_no_profile_finding(self):
        make_complete(self.dir)
        self.assertNotIn("profile", self.ids())

    def test_low_reply_rate_is_reported_with_evidence(self):
        for i in range(12):
            self.put("roles", f"r{i}", role(i))
        a = {x["id"]: x for x in self.run_()["areas"]}["response-rate"]
        self.assertIn("0 of 12", a["evidence"][0])
        self.assertTrue(a["action"])

    def test_group_comparison_needs_min_group_and_a_real_gap(self):
        for i in range(6):
            self.put("roles", f"a{i}", role(i, "interviewing (round 1)", source="Referral"))
        for i in range(6, 12):
            self.put("roles", f"b{i}", role(i, "applied", source="Board"))
        d = self.run_()
        self.assertIn("outcome-source", {a["id"] for a in d["areas"]})
        self.put("roles", "x", role(99, "applied", source="Solo"))
        self.assertEqual(len(self.run_()["breakdown"]["source"]), 3)  # shown, but never compared below MIN_GROUP

    def test_overdue_follow_up_and_missing_follow_up(self):
        self.put("roles", "a", role(1, follow_up="2026-06-01"))
        self.put("roles", "b", role(2, follow_up=""))
        ids = self.ids()
        self.assertTrue({"overdue-follow-up", "no-follow-up"} <= ids)

    def test_stale_ready_roles_and_slow_apply(self):
        for i in range(3):
            self.put("roles", f"s{i}", role(i, "ready to apply", date_found="2026-06-01", date_applied=""))
            self.put("roles", f"p{i}", role(10 + i, date_found="2026-06-01", date_applied="2026-06-20"))
        self.assertTrue({"stale-ready", "slow-apply"} <= self.ids())

    def test_skip_reasons_gate_review_and_recurring_skill_terms(self):
        for i in range(6):
            self.put("roles", f"k{i}", role(i, "skipped (gate)", decision="skip", reason_or_gaps="location: onsite in 1 connection; needs Kubernetes",
                                            gates={"location": "FAIL"}, gate_review="pending"))
        d = {a["id"]: a for a in self.run_()["areas"]}
        self.assertIn("location", d["skip-reason"]["title"])
        self.assertIn("gate-review", d)
        self.assertIn("Kubernetes", d["skill-gaps"]["evidence"][0])

    def test_own_name_and_months_are_not_skill_terms(self):
        f = full_form()
        f["fields"]["firstName"]["value"] = "Jordan"
        profile.save(self.dir, f)
        for i in range(4):
            self.put("roles", f"k{i}", role(i, "skipped", reason_or_gaps="Jordan said no on Oct 5; needs Terraform"))
        terms = [e for a in self.run_()["areas"] for e in a["evidence"] if e.startswith("Recurring")]
        self.assertTrue(terms)
        self.assertIn("Terraform", terms[0])
        self.assertNotIn("Jordan", terms[0])
        self.assertNotIn("Oct", terms[0])

    def test_contacts_targets_tasks_kits_and_ats(self):
        self.put("contacts", "c1", {"name": "Sam", "company": "Co1", "cadence": 7, "last_touch": "2026-05-01"})
        self.put("tasks", "t1", {"title": "Call", "due": "2026-06-01", "done": False})
        for i in range(4):
            self.put("targets", f"t{i}", {"company": f"Target{i}", "status": "active"})
        self.put("kitreviews", "k", {"grounding_issues": 2})
        self.put("roles", "a", role(1, ats=0.5, resume_file="", cover_letter=""))
        ids = self.ids()
        self.assertTrue({"contacts-due", "overdue-tasks", "targets-untouched", "grounding", "low-ats", "no-resume-file"} <= ids)

    def test_warm_intro_effect(self):
        for i in range(4):
            self.put("contacts", f"c{i}", {"name": f"P{i}", "company": f"Co{i}", "cadence": 30, "last_touch": "2026-06-25"})
            self.put("roles", f"w{i}", role(i, "interviewing (screen)"))
        for i in range(10, 16):
            self.put("roles", f"x{i}", role(i, "applied"))
        self.assertIn("warm-intro", self.ids())

    def test_pace_drop_uses_weekly_buckets(self):
        for i in range(5):
            self.put("roles", f"o{i}", role(i, date_applied="2026-06-10"))
        d = self.run_()
        self.assertEqual(len(d["trend"]["applied"]), learning.WEEKS)
        self.assertIn("pace-drop", {a["id"] for a in d["areas"]})

    def test_stage_classification(self):
        for text, want in (("interviewing (2 rounds)", "interviewing"), ("rejected", "rejected"), ("closed (no response)", "noresp"),
                           ("applied (per LinkedIn, confirm date)", "applied"), ("skipped (gate)", "skipped"),
                           ("Not applying", "skipped"), ("build on request", "ready"), ("", "new"), ("offer received", "offer")):
            self.assertEqual(learning.stage({"status": text}), want, text)

    def test_hostile_values_do_not_crash(self):
        """The store refuses bad types, so this is a hand-edited or corrupted database."""
        rows = [("roles", "a", {"status": 5, "fit_pct": ["x"], "date_found": {"a": 1}, "date_applied": "not a date", "gates": "FAIL",
                                "flags": None, "title": None, "company": 7, "reason_or_gaps": None, "ats": True}),
                ("roles", "b", {}), ("contacts", "c", {"cadence": "weekly", "last_touch": 3, "company": None}),
                ("tasks", "t", {"due": "never", "done": "no"}), ("reviews", "r", {"stop": "not a list"}),
                ("kitreviews", "k", {"grounding_issues": "lots"}), ("targets", "g", {"company": None, "status": "active"})]
        for col, id_, data in rows:
            self.conn.execute("INSERT INTO docs VALUES (?,?,?,1,'2026-06-30','test')", (col, id_, json.dumps(data)))
        self.conn.commit()
        self.assertIn("areas", self.run_())

    def test_idempotent_and_read_only(self):
        for i in range(12):
            self.put("roles", f"r{i}", role(i))
        before = self.conn.execute("select count(*) from history").fetchone()[0]
        self.assertEqual(json.dumps(self.run_(), sort_keys=True), json.dumps(self.run_(), sort_keys=True))
        self.assertEqual(self.conn.execute("select count(*) from history").fetchone()[0], before)


class WebRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.srv = web.make_server(self.dir, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                  headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data)

    def test_status_and_learning_endpoints(self):
        s, d = self.call("GET", "/profile/status")
        self.assertEqual((s, d["percent"]), (200, 0))
        s, d = self.call("GET", "/learning")
        self.assertEqual(s, 200)
        self.assertIn("areas", d)

    def test_uploading_a_resume_stores_it_and_moves_the_percentage(self):
        txt = base64.b64encode(b"Jordan Example\njordan@example.com\n").decode()
        s, _ = self.call("POST", "/wizard/resume", {"name": "cv.txt", "data": txt})
        self.assertEqual(s, 200)
        self.assertTrue(profile.has_resume(self.dir))
        s, bad = self.call("POST", "/wizard/resume", {"name": "x.exe", "data": txt})
        self.assertEqual(s, 400)
        self.assertEqual(len(list((self.dir / "resumes").glob("*"))), 1)  # a rejected upload stores nothing


if __name__ == "__main__":
    unittest.main()
