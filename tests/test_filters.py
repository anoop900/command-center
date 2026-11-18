"""Filters data layer: reason grouping, Not a Match hiding, and the single read/write entry point."""
import os
import tempfile
import unittest

from cc import filters, store
from cc.runs import board

TODAY = __import__("datetime").date(2026, 6, 1)


def role(i, status, reason="", **kw):
    d = {"company": f"Co{i}", "title": f"Role {i}", "key": f"co{i}|role {i}", "status": status, "date_found": "2026-05-30",
         "reason_or_gaps": reason, "gate_review": "pending" if status == "skipped (gate)" else ""}
    d.update(kw)
    return d


class Reasons(unittest.TestCase):
    def test_splits_gates_and_learned_word(self):
        r = filters.reasons_of("location: onsite in Dallas, TX; relevance: learned: you marked 3 roles with 'java' as not a match")
        self.assertEqual([(x["kind"], x.get("term")) for x in r], [("location", None), ("learned", "java")])
        self.assertEqual(r[1]["count"], 3)

    def test_kinds_and_odd_text(self):
        self.assertEqual(filters.reasons_of("relevance: level: the title says director, you want senior")[0]["kind"], "level")
        self.assertEqual(filters.reasons_of("relevance: title says 'sales'")[0]["kind"], "excluded_word")
        self.assertEqual(filters.reasons_of("pay: below floor")[0]["kind"], "pay")
        for junk in ("", None, 5, "just words", ";;;", "x" * 100000):
            self.assertTrue(filters.reasons_of(junk))


class Hidden(unittest.TestCase):
    def test_is_hidden_matches_any_case_only_for_skipped(self):
        self.assertTrue(board.is_hidden({"status": "skipped (Not a Match)"}))
        self.assertTrue(board.is_hidden({"status": "Skipped (not a match)"}))
        self.assertFalse(board.is_hidden({"status": "skipped (Location)"}))
        self.assertFalse(board.is_hidden({"status": "applied"}))
        self.assertFalse(board.is_hidden({}))

    def test_hidden_roles_leave_the_board(self):
        rows = [role(1, "skipped (Not a Match)"), role(2, "skipped (gate)", "location: x"), role(3, "build on request", decision="queued")]
        got = [r["company"] for r in board.build_roles(rows, {}, TODAY)]
        self.assertEqual(sorted(got), ["Co2", "Co3"])


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.tmp.name, "t.db"))
        for i, (st, why) in enumerate([("skipped (gate)", "location: onsite in Dallas, TX"),
                                       ("skipped (gate)", "location: onsite in Plano, TX; relevance: learned: you marked 3 roles with 'java' as not a match"),
                                       ("skipped (gate)", "relevance: learned: you marked 3 roles with 'java' as not a match"),
                                       ("skipped (Not a Match)", ""), ("applied", "")]):
            store.set_doc(self.conn, "roles", f"r{i}", role(i, st, why))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def view(self):
        return filters.view(self.conn, self.tmp.name)

    def test_groups_count_and_exclude_hidden(self):
        v = self.view()
        self.assertEqual(v["failed"]["total"], 3)
        g = {x["id"]: x["count"] for x in v["failed"]["groups"]}
        self.assertEqual(g, {"location": 2, "learned:java": 2})
        self.assertEqual([h["id"] for h in v["hidden"]], ["r3"])

    def test_forget_and_rules_validation(self):
        out = filters.apply(self.conn, self.tmp.name, {"action": "forget", "term": "java"})
        self.assertEqual(out["relevance"]["forgotten"], ["java"])
        for bad in ({}, {"action": "x"}, {"action": "forget", "term": 5}, {"action": "levels", "items": ["wizard"]},
                    {"action": "exclude", "items": "a"}, {"action": "rules", "rules": "nope"}, {"action": "unhide", "id": "r4"},
                    {"action": "unhide", "id": 7}, {"action": "unhide", "id": "nope"}):
            with self.assertRaises(ValueError, msg=bad):
                filters.apply(self.conn, self.tmp.name, bad)

    def test_unhide_moves_role_to_normal_skipped_and_is_idempotent(self):
        filters.apply(self.conn, self.tmp.name, {"action": "unhide", "id": "r3"})
        self.assertEqual(store.get(self.conn, "roles", "r3")["data"]["status"], "skipped (unhidden)")
        self.assertEqual(self.view()["hidden"], [])
        with self.assertRaises(ValueError):
            filters.apply(self.conn, self.tmp.name, {"action": "unhide", "id": "r3"})


if __name__ == "__main__":
    unittest.main()
