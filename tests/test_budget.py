import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from cc import ai, store
from cc.ai import budget, runners


def cfg(run=2, day=3):
    return NS(max_calls_per_run=run, max_calls_per_day=day)


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "db.sqlite"
        self.conn = store.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_run_cap_then_fallback(self):
        calls = []
        b = budget.Budget(self.conn, cfg(run=2))
        r = b.wrap(lambda t, p: calls.append(1) or '{"answer": "x"}')
        for _ in range(3):
            res = ai.run_task("ask", {"question": "hi"}, "assisted", r)
        self.assertEqual(len(calls), 2)
        self.assertEqual(res.status, "unavailable")
        self.assertIn("per run", res.reason)
        self.assertEqual(budget.status(self.conn, cfg())["day_used"], 2)  # the refused call spent nothing

    def test_day_cap_spans_runs(self):
        c = cfg(run=5, day=3)
        for _ in range(2):
            budget.Budget(self.conn, c).charge()
        b = budget.Budget(self.conn, c)
        b.charge()
        with self.assertRaises(ai.RunnerError) as e:
            b.charge()
        self.assertIn("per day", str(e.exception))
        with self.assertRaises(ai.RunnerError):
            budget.Budget(self.conn, c).charge()

    def test_failing_runner_still_counts(self):
        def boom(t, p):
            raise ai.RunnerError("down")
        b = budget.Budget(self.conn, cfg())
        ai.run_task("ask", {"question": "hi"}, "assisted", b.wrap(boom))
        self.assertEqual(budget.status(self.conn, cfg())["day_used"], 1)

    def test_queued_and_none_runners_not_metered(self):
        class Cfg(NS):
            pass
        c = NS(mode=NS(name="chat"), budget=cfg(), ai=NS(runner="paste"))
        r = runners.make_runner(c, self.conn)
        ai.run_task("ask", {"question": "hi"}, "chat", r)
        self.assertEqual(budget.status(self.conn, cfg())["day_used"], 0)

    def test_day_rollover(self):
        c = cfg(day=1)
        budget.Budget(self.conn, c).charge()
        real = budget._today
        budget._today = lambda: "2999-01-01"
        try:
            budget.Budget(self.conn, c).charge()
            self.assertEqual(budget.status(self.conn, c)["day_used"], 1)
        finally:
            budget._today = real

    def test_corrupt_usage_record(self):
        for bad in ("lots", -5, 1.5, None, [1]):
            store.set_doc(self.conn, "usage", budget._today(), {"calls": bad}, force=True)
            self.assertEqual(budget.status(self.conn, cfg())["day_used"], 0)
            budget.Budget(self.conn, cfg(day=2)).charge()  # treated as zero, not a crash
        store.set_doc(self.conn, "usage", budget._today(), {"calls": 10 ** 9}, force=True)
        with self.assertRaises(ai.RunnerError):
            budget.Budget(self.conn, cfg()).charge()

    def test_concurrent_chargers_never_exceed_cap(self):
        ok = []

        def go():
            conn = store.connect(self.db)
            try:
                for _ in range(5):
                    try:
                        budget.Budget(conn, cfg(run=99, day=7)).charge()
                        ok.append(1)
                    except ai.RunnerError:
                        pass
            finally:
                conn.close()
        ts = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(ok), 7)
        self.assertEqual(budget.status(self.conn, cfg(day=7))["day_used"], 7)

    def test_old_days_pruned_recent_and_odd_ids_kept(self):
        for i in ("2000-01-01", "2020-06-30", "not-a-date", budget._today()):
            store.set_doc(self.conn, "usage", i, {"calls": 1})
        budget.Budget(self.conn, cfg()).charge()
        ids = [d["id"] for d in store.list_docs(self.conn, "usage")]
        self.assertEqual(ids, sorted(["not-a-date", budget._today()]))

    def test_prune_failure_does_not_block_a_call(self):
        real = store.delete_before
        store.delete_before = lambda *a: 1 / 0
        try:
            budget.Budget(self.conn, cfg()).charge()
            self.assertEqual(budget.status(self.conn, cfg())["day_used"], 1)
        finally:
            store.delete_before = real

    def test_no_history_rows_for_counter(self):
        for _ in range(3):
            budget.Budget(self.conn, cfg()).charge()
        self.assertEqual(store.history(self.conn, "usage", budget._today()), [])


if __name__ == "__main__":
    unittest.main()
