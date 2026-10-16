"""Unit tests for cc/rules/job_key.py and score.py. Run: python3 -m unittest discover tests"""
import copy
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
from cc.rules import job_key, score

CFG = score.load_config(os.path.join(HERE, "fixtures", "config.test.toml"))
GOOD = {"technical": 90, "experience": 85, "seniority": 80, "career": 85}


def legacy_key(company, title):  # the original make_tracker.key, kept verbatim for the regression test
    t = re.sub(r"[^a-z0-9& ]", " ", str(title).lower())
    t = re.sub(r"\b(sr|senior)\b", "senior", t)
    t = re.sub(r"\s+", " ", t).strip()
    return str(company).lower().strip() + "|" + t


def post(**kw):
    p = {"key": "k", "title": "Senior Cloud Architect", "channel": "LinkedIn", "scores": dict(GOOD),
         "pay": None, "location": {"type": "remote", "country": "CA"}, "work_auth": {"accepts": "silent"},
         "people_management": False}
    p.update(kw)
    return p


class JobKey(unittest.TestCase):
    def test_key_unchanged(self):
        for c, t in [("Acme Corp.", "Sr. Cloud Architect"), ("Fabrikam Advisory", "Senior Manager, Technology – Finance"),
                     ("  Tailspin Health ", "Principal Cloud Architect (Remote)"), ("R&D Co", "SRE / Ops")]:
            self.assertEqual(job_key.key(c, t), legacy_key(c, t))

    def test_suffix_duplicate(self):
        existing = [("acme corp.|senior cloud architect", "Acme Corp.", "Senior Cloud Architect", "")]
        self.assertIsNotNone(job_key.find_duplicate("Acme", "Sr Cloud Architect", "", existing))

    def test_url_duplicate_across_titles(self):
        u = "https://www.linkedin.com/jobs/view/4012345678/"
        existing = [("x|cloud lead", "X", "Cloud Lead", u)]
        self.assertIsNotNone(job_key.find_duplicate("X Inc", "Cloud Lead (Hybrid)", u + "?refId=1", existing))

    def test_different_roles_not_duplicates(self):
        existing = [("acme|cloud architect", "Acme", "Cloud Architect", "")]
        self.assertIsNone(job_key.find_duplicate("Acme", "Data Architect", "", existing))

    def test_dupe_groups(self):
        rows = [{"company": "Acme Ltd", "title": "SRE"}, {"company": "Acme", "title": "SRE"}, {"company": "B", "title": "SRE"}]
        self.assertEqual(job_key.dupe_groups(rows), [["acme ltd|sre", "acme|sre"]])


class JobKeyCli(unittest.TestCase):
    def test_check_against_folder(self):
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as t:
            json_path = os.path.join(t, "acme-corp-senior-cloud-architect.json")
            open(json_path, "w").write('{"company": "Acme Corp", "title": "Senior Cloud Architect"}')
            run = lambda *a: subprocess.run([sys.executable, "-m", "cc.rules.job_key", "check", t, *a],
                                            capture_output=True, text=True).stdout.strip()
            self.assertEqual(run("Acme", "Sr Cloud Architect"), "acme-corp-senior-cloud-architect")
            self.assertEqual(run("Acme", "Data Architect"), "NEW")


class TrackerPendingWrites(unittest.TestCase):
    def test_second_call_keeps_first_calls_change(self):
        import subprocess, tempfile
        if __import__("importlib").util.find_spec("openpyxl") is None:
            self.skipTest("openpyxl not installed")
        with tempfile.TemporaryDirectory() as t:
            roles = os.path.join(t, "roles"); os.makedirs(roles)
            for i, c in (("acme-sre", "Acme"), ("beta-ops", "Beta")):
                json.dump({"company": c, "title": i.split("-")[1].upper(), "status": "applied"},
                          open(os.path.join(roles, i + ".json"), "w"))
            run = lambda *a: subprocess.run([sys.executable, "-m", "cc.rules.make_tracker", *a], capture_output=True, text=True, check=True)
            run("--update", roles, "Acme", "SRE", "status=interviewing")
            new = os.path.join(t, "new.json"); json.dump([{"company": "Gamma", "title": "Lead", "status": "new"}], open(new, "w"))
            run("--add", roles, new)
            run("--update", roles, "Beta", "OPS", "status=rejected")
            writes = json.load(open(os.path.join(roles, "_writes.json")))
            self.assertEqual(sorted(w["doc_id"] for w in writes), ["acme-sre", "beta-ops", "gamma-lead"])
            self.assertEqual(json.load(open(os.path.join(roles, "_out", "acme-sre.json")))["status"], "interviewing")
            self.assertTrue([w for w in writes if w["doc_id"] == "gamma-lead"][0]["new"])
            self.assertFalse([w for w in writes if w["doc_id"] == "acme-sre"][0]["new"])


class Score(unittest.TestCase):
    def test_overall_and_band(self):
        self.assertEqual(score.overall(GOOD, CFG["scoring"]["weights"]), 86)
        self.assertEqual(score.band(75, CFG["scoring"]["bands"]), "Strong fit")
        self.assertEqual(score.band(74, CFG["scoring"]["bands"]), "Good fit")
        self.assertEqual(score.band(0, CFG["scoring"]["bands"]), "Poor fit")

    def test_missing_dimension_raises(self):
        with self.assertRaises(ValueError):
            score.overall({"technical": 90}, CFG["scoring"]["weights"])

    def test_pay_not_stated_passes(self):
        self.assertEqual(score.decide(post(), CFG)["gates"]["pay"]["verdict"], "PASS")

    def test_pay_usd_converted(self):
        # 90k USD * 1.37 = 123,300 CAD >= 120k floor
        d = score.decide(post(pay={"max": 90000, "currency": "USD", "basis": "year"}), CFG)
        self.assertEqual(d["gates"]["pay"]["verdict"], "PASS")
        d = score.decide(post(pay={"max": 80000, "currency": "USD", "basis": "year"}), CFG)
        self.assertEqual(d["gates"]["pay"]["verdict"], "FAIL")
        self.assertFalse(d["eligible"])

    def test_pay_hourly_and_unknown_currency(self):
        self.assertEqual(score.gate_pay({"max": 70, "basis": "hour"}, CFG["pay"])["verdict"], "FAIL")
        self.assertEqual(score.gate_pay({"min": 80, "basis": "hour"}, CFG["pay"])["verdict"], "PASS")
        self.assertEqual(score.gate_pay({"max": 9, "currency": "JPY"}, CFG["pay"])["verdict"], "FLAG")

    def test_pay_flag_band(self):
        self.assertEqual(score.gate_pay({"max": 116000}, CFG["pay"])["verdict"], "FLAG")   # within 5% of 120k
        self.assertEqual(score.gate_pay({"max": 113000}, CFG["pay"])["verdict"], "FAIL")

    def test_top_n_min(self):
        low = {k: 40 for k in GOOD}
        out = score.decide_batch([post(key="z", scores=low)], CFG)
        self.assertFalse(out[0]["build_kit"])                          # top 1 but below top_n_min 60

    def test_location(self):
        g = lambda loc, ch="LinkedIn": score.gate_location(loc, ch, CFG["location"])["verdict"]
        self.assertEqual(g({"type": "hybrid", "country": "CA", "region": "Greater Toronto Area"}), "PASS")
        self.assertEqual(g({"type": "onsite", "country": "CA", "region": "Calgary"}), "FAIL")
        self.assertEqual(g({"type": "remote", "country": "DE"}), "FAIL")
        self.assertEqual(g({"type": "onsite", "country": "IE", "region": "Dublin"}, "Intl relocation"), "PASS")
        self.assertEqual(g(None), "FLAG")

    def test_work_auth_routes(self):
        us = {"type": "remote", "country": "US"}
        v = lambda **kw: score.decide(post(location=us, **kw), CFG)["gates"]["work_auth"]
        self.assertEqual(v(work_auth={"accepts": "yes"})["verdict"], "PASS")
        self.assertEqual(v(work_auth={"accepts": "silent"})["verdict"], "FLAG")
        self.assertEqual(v(work_auth={"accepts": "excludes"})["verdict"], "FAIL")
        flag = v(work_auth={"accepts": "yes"}, title="Director, Cloud")
        self.assertEqual(flag["verdict"], "FLAG")
        self.assertIn("lawyer", flag["reason"])
        self.assertEqual(v(work_auth={"accepts": "yes"}, people_management=True)["verdict"], "FLAG")

    def test_onsite_abroad_via_route(self):
        d = score.decide(post(location={"type": "onsite", "country": "US", "region": "Plano"},
                              work_auth={"accepts": "yes"}), CFG)
        self.assertEqual(d["gates"]["location"]["verdict"], "PASS")
        self.assertTrue(d["eligible"])
        d = score.decide(post(location={"type": "onsite", "country": "US", "region": "Plano"},
                              work_auth={"accepts": "excludes"}), CFG)
        self.assertFalse(d["eligible"])                                   # TN gate still decides
        d = score.decide(post(location={"type": "onsite", "country": "FR", "region": "Paris"}), CFG)
        self.assertEqual(d["gates"]["location"]["verdict"], "FAIL")       # no route for France

    def test_work_auth_on_silent_fail_and_unknown_country(self):
        cfg = copy.deepcopy(CFG)
        cfg["work_authorization"][0]["on_silent"] = "fail"
        d = score.decide(post(location={"type": "remote", "country": "US"}), cfg)
        self.assertEqual(d["gates"]["work_auth"]["verdict"], "FAIL")
        d = score.decide(post(location={"type": "remote", "country": "FR"}), CFG)
        self.assertEqual(d["gates"]["work_auth"]["verdict"], "FAIL")

    def test_batch_kits(self):
        weak = {k: 65 for k in GOOD}
        posts = [post(key="a"), post(key="b", scores=weak), post(key="c", scores=weak),
                 post(key="d", scores=weak), post(key="e", scores=weak,
                                                  location={"type": "onsite", "country": "CA", "region": "Calgary"})]
        out = {d["key"]: d for d in score.decide_batch(posts, CFG)}
        self.assertTrue(out["a"]["build_kit"])                       # over threshold
        self.assertEqual(sum(d["build_kit"] for d in out.values()), 3)  # a + top-3 fill = a, b, c
        self.assertFalse(out["e"]["build_kit"])                      # FAIL gate never gets a kit
        self.assertNotIn("rank", out["e"])


if __name__ == "__main__":
    unittest.main()
