"""Learning from choices: signals, training, relevance gate, forget/restore, optional AI. Offline, fictional data."""
import datetime as dt
import os
import tempfile
import unittest

from cc import ai, learned, relevance, store
from cc.rules import job_key
from cc.runs import actions

TODAY = dt.date(2026, 10, 13)


def role(conn, title, **kw):
    d = {"key": job_key.key("Acme", title), "company": "Acme", "title": title, "status": "build on request", "decision": "queued", **kw}
    store.set_doc(conn, "roles", job_key.doc_id(d["key"]), d)
    return d["key"]


class Learned(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def press(self, key, action, reason=""):
        store.set_doc(self.conn, "actions", job_key.doc_id(key + action), {"key": key, "action": action, "reason": reason,
                      "status": f"{action} ({reason})" if action == "skipped" else action})
        actions.settle(self.conn, TODAY)

    def cfg(self, **kw):
        return dict(relevance.settings(self.conn), **kw)

    def test_nothing_is_excluded_by_default(self):
        c = relevance.settings(self.conn)
        for t in ("Summer Intern, Finance", "Director of Platform", "Marine Biology Technician"):
            self.assertIsNone(relevance.check(t, c))

    def test_levels_senior_profile_flags_interns_student_keeps_them(self):
        senior, student = self.cfg(levels=["director"]), self.cfg(levels=["intern", "entry"])
        self.assertIn("level:", relevance.check("Summer Intern, Finance", senior))
        self.assertIn("level:", relevance.check("Junior Analyst", senior))
        self.assertIsNone(relevance.check("VP Operations", senior))                  # one step away is allowed
        self.assertIsNone(relevance.check("Marine Biology Technician", senior))      # no level word, unknown field: neutral
        self.assertIsNone(relevance.check("Summer Intern, Finance", student))
        self.assertIn("level:", relevance.check("Senior Director of Finance", student))

    def test_several_levels(self):
        c = self.cfg(levels=["entry", "senior"])
        self.assertIsNone(relevance.check("Junior Analyst", c))
        self.assertIsNone(relevance.check("Senior Analyst", c))
        self.assertIn("level:", relevance.check("Chief Executive", c))
        self.assertIsNone(relevance.check("Whatever Intern", c))                      # intern is one step from entry

    def test_suggested_levels_from_history(self):
        j = lambda t, d: {"title": t, "dates": d}
        self.assertEqual(relevance.suggest_levels([]), ["intern", "entry"])
        self.assertEqual(relevance.suggest_levels([j("Cashier", "2025 - Present")], dt.date(2026, 6, 1)), ["entry"])
        self.assertEqual(relevance.suggest_levels([j("Analyst", "2008 - 2012"), j("Director of Ops", "2012 - Present")], TODAY), ["director"])
        self.assertEqual(relevance.years_of([j("x", "Jan 2019 - Present")], TODAY), 7)
        self.assertIsNone(relevance.years_of([j("x", "")]))

    def test_rule_needs_two_consistent_skips_and_no_positive(self):
        for t in ("Oncology Nurse Lead", "Oncology Pharmacist"):
            self.press(role(self.conn, t), "skipped", "Not a match")
        m = learned.model(self.conn)
        self.assertEqual([r["term"] for r in m["rules"]], ["oncology"])
        self.assertIn("learned", relevance.check("Oncology Director", relevance.settings(self.conn), m))
        self.press(role(self.conn, "Oncology Analyst"), "applied")                 # a contradicting positive removes the rule
        self.assertEqual(learned.model(self.conn)["rules"], [])

    def test_outcomes_by_source_level_band_need_two_signals(self):
        for i, (src, act, why) in enumerate([("Board A", "applied", ""), ("Board A", "applied", ""), ("Board B", "skipped", "not a match"),
                                             ("Board B", "skipped", "not a match"), ("Board C", "applied", "")]):
            r = {"key": job_key.key("Co", f"Role {i}"), "title": f"Role {i}", "source": src, "level": "director", "label": "strong"}
            learned.record(self.conn, r, act, why, TODAY)
        got = {(o["dim"], o["value"]): (o["pos"], o["neg"]) for o in learned.train(self.conn, TODAY)["outcomes"]}
        self.assertEqual(got, {("source", "Board A"): (2, 0), ("source", "Board B"): (0, 2), ("level", "director"): (3, 2), ("band", "strong"): (3, 2)})
        self.assertEqual(learned.view(self.conn)["outcomes"], learned.model(self.conn)["outcomes"])
        self.assertEqual(learned.outcomes([{"kind": "positive", "source": 5}, {"kind": "", "source": "x"}, {}]), [])

    def test_source_hints_need_evidence_and_can_be_forgotten(self):
        for i, (src, act, why) in enumerate([("Bad", "skipped", "not a match")] * 3 + [("Good", "applied", "")] * 2 + [("Mixed", "applied", ""), ("Mixed", "skipped", "not a match")] * 3 + [("Few", "skipped", "not a match")] * 2):
            learned.record(self.conn, {"key": job_key.key("Co", f"R{i}"), "title": f"R{i}", "source": src}, act, why, TODAY)
        learned.train(self.conn, TODAY)
        h = {x["source"]: x["kind"] for x in learned.view(self.conn)["hints"]}
        self.assertEqual(h, {"Bad": "drop", "Good": "keep"})
        learned.forget(self.conn, "source:bad")
        self.assertEqual({x["source"]: x["forgotten"] for x in learned.view(self.conn)["hints"]}, {"Bad": True, "Good": False})
        learned.forget(self.conn, "source:bad", restore=True)
        self.assertFalse(any(x["forgotten"] for x in learned.view(self.conn)["hints"]))

    def test_timing_counts_hours_and_needs_a_clear_window(self):
        for bad in (None, "", "garbage", "2026-10-13T25:00:00", 5):
            learned.tick_hour(self.conn, bad)
        self.assertIsNone(store.get(self.conn, "learned", "timing"))
        for h in (18, 19, 19, 20, 18, 19, 20, 19, 18):
            learned.tick_hour(self.conn, f"2026-10-13T{h}:05:00")
        self.assertIsNone(learned.view(self.conn)["timing"])                       # 9 presses is too few
        learned.tick_hour(self.conn, "2026-10-14T07:00:00")
        t = learned.view(self.conn)["timing"]
        self.assertEqual((t["start"], t["end"], t["share"], t["n"], t["forgotten"]), (18, 21, 90, 10, False))
        learned.forget(self.conn, "timing:window")
        self.assertTrue(learned.view(self.conn)["timing"]["forgotten"])
        for h in range(24):                                                          # spread out: no pattern
            for _ in range(3):
                learned.tick_hour(self.conn, f"2026-10-15T{h:02d}:00:00")
        self.assertIsNone(learned.view(self.conn)["timing"])

    def test_settle_ticks_each_press_once(self):
        k = role(self.conn, "Director of Platform")
        self.press(k, "applied")
        self.press(k, "applied")
        hours = store.get(self.conn, "learned", "timing")["data"]["hours"]
        self.assertEqual(sum(hours), 2)

    def test_pay_and_place_skips_teach_nothing(self):
        for t in ("Ops Alpha", "Ops Beta", "Ops Gamma"):
            self.press(role(self.conn, t), "skipped", "Pay too low")
        self.assertEqual(learned.model(self.conn)["rules"], [])

    def test_repeat_press_is_idempotent_and_forget_restore(self):
        k = role(self.conn, "Oncology Nurse")
        self.press(k, "skipped", "Not a match")
        self.press(k, "skipped", "Not a match")
        self.press(role(self.conn, "Oncology Aide"), "skipped", "Not a match")
        self.assertEqual(len(store.list_docs(self.conn, "signals")), 2)
        learned.forget(self.conn, "oncology")
        self.assertIsNone(relevance.check("Oncology Lead", relevance.settings(self.conn), learned.model(self.conn)))
        learned.forget(self.conn, "oncology", restore=True)
        self.assertIsNotNone(relevance.check("Oncology Lead", relevance.settings(self.conn), learned.model(self.conn)))

    def test_include_term_beats_exclude(self):
        cfg = self.cfg(include=["intern"], exclude=["audit"], levels=["director"])
        self.assertIsNone(relevance.check("Internal Audit Intern", cfg))

    def test_seed_from_existing_roles(self):
        role(self.conn, "Oncology Nurse", status="skipped (Not a match)")
        role(self.conn, "Oncology Aide", status="skipped (Not a match)")
        role(self.conn, "Platform Lead", status="applied", date_applied="2026-10-01")
        self.assertEqual(learned.seed(self.conn), 3)
        self.assertEqual(learned.seed(self.conn), 3)
        self.assertEqual([r["term"] for r in learned.train(self.conn)["rules"]], ["oncology"])

    def test_ai_is_off_by_default_and_never_overrides_positives(self):
        raw = '{"avoid": ["oncology", "platform", "BAD WORD!"]}'
        runner = lambda task, prompt: raw
        for t in ("Oncology Nurse", "Oncology Aide", "Platform Lead", "Platform Chief"):
            self.press(role(self.conn, t), "skipped" if "Onc" in t else "applied", "Not a match")
        self.assertEqual(learned.suggest_ai(self.conn, ("autopilot", runner)), [])      # toggle off
        learned.set_ai(self.conn, True)
        self.assertEqual(learned.suggest_ai(self.conn, ("autopilot", runner)), ["oncology"])
        self.assertEqual(learned.suggest_ai(self.conn, None), [])

    def test_hostile_inputs(self):
        self.assertEqual(relevance.terms(None), [])
        self.assertEqual(relevance.terms("x" * 100000)[0][:1], "x")
        self.assertIsNone(relevance.check("", relevance.settings(self.conn), {"rules": []}))
        learned.forget(self.conn, "A" * 500)
        learned.record(self.conn, None, "skipped", "Not a match")


if __name__ == "__main__":
    unittest.main()


class RankOrder(unittest.TestCase):
    MODEL = {"rules": [{"term": "oncology", "neg": 2, "pos": 0, "why": {}}], "pos_terms": ["platform", "cloud"]}

    def test_adjust_directions_and_caps(self):
        self.assertEqual(learned.rank_adjust(self.MODEL, "Cloud Platform Architect")[0], 10)
        self.assertEqual(learned.rank_adjust(self.MODEL, "Oncology Architect")[0], -5)
        self.assertEqual(learned.rank_adjust(self.MODEL, "Payroll Clerk"), (0, ""))
        self.assertEqual(learned.rank_adjust(self.MODEL, "Cloud Platform Architect", forgotten=["cloud", "platform"])[0], 0)
        self.assertEqual(learned.rank_adjust({}, "Cloud Platform"), (0, ""))

    def test_todos_reorder_within_fit_and_unlearned_is_fit_order(self):
        from cc.runs import board
        rows = [{"key": job_key.key("A", t), "company": "A", "title": t, "status": "ready", "fit_pct": "70", "kit": "k",
                 "date_found": TODAY.isoformat()} for t in ("Oncology Lead", "Cloud Platform Lead")]
        rank = lambda t: learned.rank_adjust(self.MODEL, t)
        order = lambda rk: [t["title"] for t in board.build_todos(board.build_roles(rows, {}, TODAY, rk), rows, {}, TODAY) if t["type"] == "apply"]
        plain = order(None)
        self.assertEqual(len(plain), 2)
        self.assertIn("Cloud Platform", order(rank)[0])
