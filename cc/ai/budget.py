"""AI call budget: caps per run (one process, or one /ask request) and per local calendar day (DESIGN section 5)."""
import datetime as dt

from .. import store
from . import RunnerError

COLLECTION = "usage"
KEEP_DAYS = 60


def _today():
    return dt.date.today().isoformat()


class Budget:
    def __init__(self, conn, cfg):
        self.conn, self.max_run, self.max_day = conn, cfg.max_calls_per_run, cfg.max_calls_per_day
        self.run_used = 0

    def charge(self):
        """Count one call or raise RunnerError. The run cap is checked first so a capped run spends no day budget."""
        if self.run_used >= self.max_run:
            raise RunnerError(f"budget: {self.max_run} AI calls per run reached")
        try:
            n = store.bump(self.conn, COLLECTION, _today(), "calls", self.max_day)
        except Exception:
            raise RunnerError("budget: usage record unavailable")
        if n is None:
            raise RunnerError(f"budget: {self.max_day} AI calls per day reached")
        if self.run_used == 0:
            self._prune()
        self.run_used += 1

    def _prune(self):
        """Old days are never read again; drop them once per run. A failure here must not block a call."""
        try:
            store.delete_before(self.conn, COLLECTION, (dt.date.fromisoformat(_today()) - dt.timedelta(days=KEEP_DAYS)).isoformat())
        except Exception:
            pass

    def wrap(self, runner):
        def budgeted(task, prompt):
            self.charge()
            return runner(task, prompt)
        return budgeted


def status(conn, cfg):
    try:
        doc = store.get(conn, COLLECTION, _today())
    except Exception:
        doc = None
    used = doc["data"].get("calls") if doc else 0
    used = used if isinstance(used, int) and not isinstance(used, bool) and used >= 0 else 0
    return {"day_used": used, "day_cap": cfg.max_calls_per_day, "run_cap": cfg.max_calls_per_run}
