"""In-portal kit builds and reviews (cc/jobs.py + POST /job): no AI window, real state, failures shown."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cc import jobs, store, web
from cc.kits import build_resume, qc_check

BASE = {"name": "Jordan Example", "contact": "Toronto | j@example.com", "headline": "Base head", "profile": "Base profile",
        "skills": [["Cloud", "Azure"]], "jobs": [{"company": "Contoso", "dates": "2020-2024", "title": "Lead",
                                                   "bullets": ["Led the migration of 40 servers"]}], "education": ["BSc"]}
RULES = {"home_city": "Toronto", "pages": 2, "min_last_page_fill": 0.75, "banned_words": []}
PDF = {"ok": True, "pdf": "x.pdf", "pages": 2, "page2_fill": 0.9, "note": ""}
TEXT = ("Toronto | j@example.com Azure Architect Azure lead " * 3, 2, 0.9)
TAILOR = {"headline": "Azure Architect", "profile": "Azure lead", "skills": [{"label": "Cloud", "text": "Azure"}],
          "bullet_changes": []}


def runner(task, prompt):
    return json.dumps(TAILOR if task.name == "tailor" else {"unsupported": []})


def boom(task, prompt):
    raise RuntimeError("model exploded")


def fake_pdf(c, outdir, pages=2):
    Path(outdir, c["file_stem"] + ".pdf").write_bytes(b"%PDF")
    Path(outdir, c["file_stem"] + ".docx").write_bytes(b"x")
    return dict(PDF)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "config.toml").write_text('[mode]\nname = "autopilot"\n', encoding="utf-8")
        (self.home / "content.json").write_text(json.dumps(BASE))
        (self.home / "qc_rules.json").write_text(json.dumps(RULES))
        (self.home / "Candidate_Profile.md").write_text("Azure lead. Migrated 40 servers. Toronto.")
        self.db = self.home / "commandcenter.db"
        self.cfg = self.home / "config.toml"
        self.conn = store.connect(self.db)
        store.set_doc(self.conn, "roles", "contoso-lead", {"key": "Contoso|Lead", "company": "Contoso", "title": "Lead",
                                                          "status": "build on request", "decision": "queued"})
        store.set_doc(self.conn, "board", "roles", {"roles": [{"key": "Contoso|Lead", "stage": "new", "status": "build on request", "kit": ""}]})
        patches = [mock.patch.object(build_resume, "build_pdf", fake_pdf), mock.patch.object(qc_check, "read_pdf", lambda p: TEXT)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def state(self, kind="kit", rid="contoso-lead", wait=True):
        end = time.time() + 15
        while True:
            d = store.get(self.conn, "jobs", f"{kind}-{rid}")
            if d and (d["data"]["state"] != "running" or not wait or time.time() > end):
                return d["data"]
            time.sleep(0.05)

    def start(self, kind="kit", posting="Azure migration role", rid="contoso-lead", run=runner):
        return jobs.start(self.db, self.cfg, kind, rid, posting, run)

    def test_no_posting_asks_for_it_and_builds_nothing(self):
        out = self.start(posting="")
        self.assertEqual(out["state"], "needs_posting")
        self.assertFalse((self.home / "kits").exists())

    def test_statements_the_trace_could_not_support_are_saved_for_the_page(self):
        claims = [{"claim": "Led a team of 40", "reason": "profile says 12"}, {"claim": "Won an award", "reason": "not in profile"}]

        def traced(task, prompt):
            return json.dumps(TAILOR if task.name == "tailor" else {"unsupported": claims[:2]})
        self.start(run=traced)
        j = self.state()
        self.assertIn("2 statement(s) could not be traced", j["reason"])
        self.assertIn("Details", j["reason"])
        rows = store.get(self.conn, "kitindex", "contoso-lead")["data"]["unsupported"]
        self.assertEqual(rows[0], {"claim": "Led a team of 40", "reason": "profile says 12"})
        self.assertEqual(rows[1]["claim"], "Won an award")

    def test_unsupported_rows_survive_hostile_trace_data(self):
        for bad in (None, "x", [None, "junk", {"claim": 5}, {"claim": "  "}], {"a": 1}):
            self.assertEqual(jobs._unsupported({"trace": {"unsupported": bad}}), [])
        self.assertEqual(len(jobs._unsupported({"trace": {"unsupported": [{"claim": "c"}] * 80}})), 50)
        row = jobs._unsupported({"trace": {"unsupported": [{"claim": "x" * 900, "reason": None}]}})[0]
        self.assertEqual((len(row["claim"]), row["reason"]), (400, ""))

    def test_kit_build_publishes_ready_state_files_and_board_row(self):
        self.assertEqual(self.start()["state"], "running")
        j = self.state()
        self.assertEqual((j["state"], j["reason"]), ("done", ""))
        idx = store.get(self.conn, "kitindex", "contoso-lead")["data"]
        self.assertEqual(idx["key"], "Contoso|Lead")
        rel = idx["folder"]
        self.assertRegex(rel, r"^\d{4}-\d\d-\d\d/Contoso_Lead$")        # <date>/<Company>_<Title>
        self.assertIn(f"/kits/{rel}/Jordan_Example_Resume.pdf", [f["url"] for f in idx["files"]])
        self.assertTrue((self.home / "kits" / rel / "Jordan_Example_Resume.pdf").is_file())
        role = store.get(self.conn, "roles", "contoso-lead")["data"]
        self.assertEqual((role["status"], role["kit"]), ("ready", rel))
        self.assertEqual(role["posting"], "Azure migration role")      # kept, so a retry or review does not ask again
        row = store.get(self.conn, "board", "roles")["data"]["roles"][0]
        self.assertEqual((row["stage"], row["kit"]), ("ready", rel))

    def test_rebuild_reuses_its_own_folder_and_custom_folder_is_used(self):
        custom = self.home / "My Resumes"
        (self.home / "config.toml").write_text(f'[mode]\nname = "autopilot"\n[paths]\nkits = "{custom.as_posix()}"\n', encoding="utf-8")
        self.start()
        self.state()
        rel = store.get(self.conn, "roles", "contoso-lead")["data"]["kit"]
        self.assertTrue((custom / rel / "Jordan_Example_Resume.pdf").is_file())
        self.assertFalse((self.home / "kits").exists())
        jobs.run_now(self.db, self.cfg, "kit", "contoso-lead", runner)
        self.assertEqual(store.get(self.conn, "roles", "contoso-lead")["data"]["kit"], rel)
        self.assertEqual(len(list(custom.iterdir())), 1)

    def test_blocked_kit_says_why(self):
        short = dict(PDF, ok=False, pages=1, note="content fills only 1 page, not 2")
        with mock.patch.object(build_resume, "build_pdf", lambda c, o, pages=2: (fake_pdf(c, o), short)[1]):
            self.start()
            j = self.state()
        self.assertEqual(j["state"], "failed")
        self.assertIn("content fills only 1 page, not 2", j["reason"])
        self.assertEqual(j["code"], "qc_failed")
        self.assertIn("Rebuild kit", j["fix"])
        self.assertEqual(j["action"], "profile")

    def test_failed_job_for_banned_words_has_plain_reason_and_fix(self):
        out = {"status": "blocked", "resume": {}, "qc": {"issues": ["voice: banned word 'leverag'", "voice: banned word '—'"]}}
        state, reason = jobs._outcome("kit", out)
        p = jobs._problem("kit", out, reason)
        self.assertEqual(state, "failed")
        self.assertIn("ban", p["message"])
        self.assertIn("leverag", p["message"])
        self.assertNotIn("voice:", p["message"])
        self.assertTrue(p["fix"])

    def test_saved_posting_is_reused(self):
        self.start()
        self.state()
        self.assertEqual(self.start(posting="")["state"], "running")
        self.assertEqual(self.state()["state"], "done")

    def test_no_profile_and_no_content_says_what_to_do(self):
        (self.home / "content.json").unlink()
        self.start()
        j = self.state()
        self.assertEqual(j["state"], "failed")
        self.assertIn("Profile step", j["reason"])

    def test_content_is_built_from_confirmed_profile_facts_only(self):
        (self.home / "content.json").unlink()
        (self.home / "qc_rules.json").unlink()
        st = {"fields": {"firstName": {"value": "Jordan", "ok": True}, "lastName": {"value": "Example", "ok": True},
                         "email": {"value": "j@example.com", "ok": True}, "phone": {"value": "555", "ok": False},
                         "city": {"value": "Toronto", "ok": True}, "school": {"value": "Example University", "ok": True}},
              "jobs": [{"employer": "Contoso", "title": "Lead", "dates": "2020-2024", "facts": ["Led 40 servers"], "ok": True},
                       {"employer": "Hidden", "title": "Intern", "dates": "2019", "facts": ["x"], "ok": False}],
              "skills": {"value": "Azure", "ok": True}}
        (self.home / "profile_form.json").write_text(json.dumps(st))
        self.start()
        j = self.state()
        self.assertEqual(j["state"], "done")
        self.assertIn("confirmed in your profile", j["reason"])
        c = json.loads((self.home / "content.json").read_text())
        self.assertEqual((c["name"], c["contact"]), ("Jordan Example", "Toronto | j@example.com"))   # unconfirmed phone left out
        self.assertEqual([x["company"] for x in c["jobs"]], ["Contoso"])                             # unconfirmed job left out
        self.assertEqual(c["jobs"][0]["bullets"], ["Led 40 servers"])

    def test_ai_failure_still_builds_from_the_base_resume_and_says_so(self):
        self.start(run=boom)
        j = self.state()
        self.assertEqual(j["state"], "done")
        self.assertIn("base resume", j["reason"])

    def test_unexpected_exception_in_worker_still_ends_the_job(self):
        with mock.patch.object(jobs.pipeline, "build_kit", side_effect=ValueError("bad thing")):
            self.start()
            j = self.state()
        self.assertEqual(j["state"], "failed")
        self.assertIn("bad thing", j["reason"].lower())

    def test_restart_marks_stuck_builds_failed(self):
        store.set_doc(self.conn, "jobs", "kit-contoso-lead", {"kind": "kit", "key": "contoso-lead", "state": "running", "ts": store.now()})
        jobs.recover(self.conn)
        j = self.state(wait=False)
        self.assertEqual(j["state"], "failed")
        self.assertIn("closed", j["reason"])

    def test_double_start_does_not_run_twice(self):
        calls = []
        gate = threading.Event()

        def slow(c, rid, text, mode, run, home):
            calls.append(rid)
            gate.wait(5)
            return {"status": "ok", "tailor": {"source": "none"}, "built": "now"}
        with mock.patch.object(jobs.pipeline, "build_kit", slow), mock.patch.object(jobs, "_publish_kit", lambda *a: ""):
            self.start()
            self.assertEqual(self.start()["state"], "running")
            gate.set()
            self.state()
        self.assertEqual(len(calls), 1)

    def test_review_without_a_kit_fails_clearly(self):
        self.start(kind="review")
        j = self.state("review")
        self.assertEqual(j["state"], "failed")
        self.assertIn("kit", j["reason"])

    def test_chat_mode_waits_for_a_pasted_answer(self):
        self.cfg.write_text('[mode]\nname = "chat"\n', encoding="utf-8")
        self.start(run=None)
        j = self.state()
        self.assertEqual(j["state"], "waiting")

    def test_hostile_input_is_rejected(self):
        for kind, rid in (("rm -rf", "contoso-lead"), ("kit", "nope"), ("kit", "../x")):
            with self.assertRaises(ValueError):
                jobs.start(self.db, self.cfg, kind, rid, "x", runner)


class AutoKits(unittest.TestCase):
    """Autopilot: fit-score new roles that kept their posting, build kits for the best, leave the rest."""
    setUp, tearDown = JobTests.setUp, JobTests.tearDown
    SCORE = {"scores": {"technical": 90, "experience": 90, "seniority": 90, "career": 90}, "basis": "strong"}
    CFG = {"scoring": {"weights": {"technical": .3, "experience": .25, "seniority": .15, "career": .3}, "bands": [[80, "strong"], [0, "weak"]],
                       "kit_threshold": 80, "top_n": 0, "top_n_min": 0},
           "pay": {"currency": "CAD", "floor": 0}, "location": {"home": "Toronto"}}

    def run_auto(self, run, cfg=None):
        from cc.runs import autokits
        with mock.patch.object(autokits.score, "decide_batch", lambda posts, c: [
                {"key": p["key"], "overall": 90 if p["key"] == "contoso-lead" else 40, "build_kit": p["key"] == "contoso-lead"} for p in posts]),              mock.patch.object(autokits.score, "decide", lambda p, c: {}):
            return autokits.run(self.conn, cfg or self.CFG, "Azure lead", ("autopilot", run), self.db, self.cfg)

    def test_scores_then_builds_only_the_best_with_saved_posting(self):
        jobs._patch(self.conn, "roles", "contoso-lead", {"posting": "Azure migration role"})
        store.set_doc(self.conn, "roles", "other-co", {"key": "Other|Co", "company": "Other", "title": "Co", "status": "build on request", "posting": "Another role"})
        score_then_kit = lambda task, prompt: json.dumps(self.SCORE) if task.name == "score" else runner(task, prompt)
        self.run_auto(score_then_kit)
        a, b = (store.get(self.conn, "roles", i)["data"] for i in ("contoso-lead", "other-co"))
        self.assertEqual((a["fit_pct"], a["status"], bool(a["kit"])), ("90", "ready", True))
        self.assertEqual((b["fit_pct"], b["status"], bool(b.get("kit"))), ("40", "build on request", False))
        self.assertTrue((self.home / "kits" / a["kit"] / "Jordan_Example_Resume.pdf").is_file())

    def test_no_posting_or_not_autopilot_does_nothing(self):
        self.assertEqual(self.run_auto(runner), [])
        from cc.runs import autokits
        jobs._patch(self.conn, "roles", "contoso-lead", {"posting": "x"})
        self.assertEqual(autokits.run(self.conn, self.CFG, "p", ("assisted", runner), self.db, self.cfg), [])
        self.assertIsNone(store.get(self.conn, "roles", "contoso-lead")["data"].get("fit_pct"))

    def test_ai_failure_leaves_role_unscored(self):
        jobs._patch(self.conn, "roles", "contoso-lead", {"posting": "Azure"})
        self.run_auto(boom)
        self.assertIsNone(store.get(self.conn, "roles", "contoso-lead")["data"].get("fit_pct"))


class JobHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.srv = web.make_server(self.dir, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        conn = store.connect(self.dir / "commandcenter.db")
        store.set_doc(conn, "roles", "r1", {"key": "A|B", "company": "A", "title": "B"})
        conn.close()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def post(self, body, host=None, raw=None, origin=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if origin:
            h["Origin"] = origin
        c.request("POST", "/job", body=raw if raw is not None else json.dumps(body).encode(), headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data)

    def test_needs_posting_over_http(self):
        code, out = self.post({"kind": "kit", "key": "r1"})
        self.assertEqual((code, out["state"]), (200, "needs_posting"))

    def test_bad_requests_get_clean_errors(self):
        for body in ({"kind": "kit"}, {"kind": "zip", "key": "r1"}, {"kind": "kit", "key": "nope"}, {"kind": "kit", "key": "a/b"},
                     {"kind": "kit", "key": "r1", "posting": 5}, {"kind": "kit", "key": "x" * 500}, [1], "x"):
            code, out = self.post(body)
            self.assertEqual(code, 400, body)
            self.assertIn("error", out)
        self.assertEqual(self.post(None, raw=b"{not json")[0], 400)

    def test_cross_site_and_wrong_host_refused(self):
        self.assertEqual(self.post({"kind": "kit", "key": "r1"}, origin="https://evil.example")[0], 403)
        self.assertEqual(self.post({"kind": "kit", "key": "r1"}, host="evil.example")[0], 403)

    def test_oversized_body_refused(self):
        self.assertEqual(self.post(None, raw=b'{"kind":"kit","key":"r1","posting":"' + b"x" * (300 << 10) + b'"}')[0], 413)


if __name__ == "__main__":
    unittest.main()
