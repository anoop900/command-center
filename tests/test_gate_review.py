"""The "Failed your rules" list: gate-failed roles wait for the user; no AI and no history decide anything."""
import datetime as dt
import os
import tempfile
import unittest

from cc import store
from cc.rules import job_key
from cc.runs import actions, board, intake
from tests.test_intake import CFG, PROFILE, posting
from tests.test_boards import TODAY


class GateReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.tmp.name, "t.db"))
        out = intake.intake(self.conn, CFG, PROFILE, [posting(pay_text="$50K - $60K CAD")], TODAY)
        self.key = out["skipped"][0]
        self.id = job_key.doc_id(self.key)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def role(self):
        return store.get(self.conn, "roles", self.id)["data"]

    def press(self, act, key=None):
        store.set_doc(self.conn, "actions", "a-" + act, {"key": key or self.key, "action": act, "at": TODAY.isoformat()})
        return actions.apply_actions(self.conn, TODAY)

    def stage(self, today=TODAY):
        rows = [d["data"] for d in store.list_docs(self.conn, "roles")]
        return [r["stage"] for r in board.build_roles(rows, {}, today)]

    def test_failed_role_is_pending_and_in_its_own_stage(self):
        self.assertEqual(self.role()["gate_review"], "pending")
        self.assertEqual(self.stage(), ["gate_failed"])

    def test_pending_role_does_not_expire(self):
        self.assertEqual(self.stage(TODAY + dt.timedelta(days=90)), ["gate_failed"])

    def test_apply_moves_it_out_of_every_skipped_list(self):
        self.press("gate_apply")
        r = self.role()
        self.assertEqual((r["status"], r["decision"], r["gate_review"]), ("build on request", "queued", "kept"))
        self.assertIn("below floor", r["reason_or_gaps"])
        self.assertEqual(self.stage(), ["new"])

    def test_later_stays_listed_but_is_marked(self):
        self.press("gate_later")
        self.assertEqual((self.role()["gate_review"], self.stage()), ("later", ["gate_failed"]))

    def test_skip_goes_to_not_applying(self):
        self.press("gate_skip")
        self.assertEqual((self.role()["gate_review"], self.stage()), ("dismissed", ["skipped"]))

    def test_repeat_and_late_presses_change_nothing(self):
        self.press("gate_apply")
        before = store.get(self.conn, "roles", self.id)["version"]
        self.press("gate_skip")  # already reviewed: a stale second tap must not undo the user's choice
        self.press("gate_apply")
        self.assertEqual(store.get(self.conn, "roles", self.id)["version"], before)
        self.assertEqual(self.role()["gate_review"], "kept")
        self.assertEqual(store.list_docs(self.conn, "actions"), [])

    def test_unknown_key_is_a_note_not_a_crash(self):
        res = self.press("gate_apply", key="nobody|nothing")
        self.assertEqual(len(res["notes"]), 1)
        self.assertEqual(self.role()["gate_review"], "pending")

    def test_hostile_presses_never_crash_or_change_other_roles(self):
        store.set_doc(self.conn, "roles", "other", {"key": "a|b", "company": "A", "title": "B", "status": "build on request",
                                                    "decision": "queued"})
        for key in ("", None, 12, "x" * 5000, "../../etc", "a|b", self.key + "\x00"):
            store.set_doc(self.conn, "actions", "h", {"key": key, "action": "gate_skip"}, force=True)
            actions.apply_actions(self.conn, TODAY)  # must not raise
        self.assertEqual(store.get(self.conn, "roles", "other")["data"]["status"], "build on request")
        self.assertEqual(store.list_docs(self.conn, "actions"), [])

    def test_old_gate_failed_records_without_the_flag_count_as_unreviewed(self):
        data = {k: v for k, v in self.role().items() if k != "gate_review"}
        store.set_doc(self.conn, "roles", self.id, data, force=True)
        self.assertEqual(self.stage(), ["gate_failed"])
        self.press("gate_apply")  # and the buttons work on them
        self.assertEqual(self.stage(), ["new"])


if __name__ == "__main__":
    unittest.main()
