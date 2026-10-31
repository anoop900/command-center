"""Run now: start the same run the scheduler would (morning or midday) from the dashboard, in the background, and report
how it went. One at a time; the scheduler's own command is reused, so there is no second code path to keep in step."""
import datetime as dt
import subprocess
import sys
import threading
from pathlib import Path

from . import config, problems, scheduler, store

STALE_MIN = 45                      # a run with a start and no finish this old is treated as dead, not running
_lock = threading.Lock()
_procs = {}                         # data folder -> (Popen, run, started)


def _today():
    return dt.date.today().isoformat()


def _heartbeat(conn, run):
    d = store.get(conn, "runs", f"{_today()}-{run}")
    return d["data"] if d else {}


def _age_min(stamp):
    """Minutes since a store timestamp (local time, no zone). Unreadable stamps count as very old."""
    try:
        t = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if t.tzinfo:
            t = t.astimezone().replace(tzinfo=None)
        return (dt.datetime.now() - t).total_seconds() / 60
    except (ValueError, TypeError):
        return 10**6


def status(conn, data_dir):
    """{state: idle|running|failed|ok, run, finished, message, fix, due}. due: the morning run should have finished by now and did not."""
    key = str(data_dir)
    with _lock:
        entry = _procs.get(key)
    if entry and entry[0].poll() is None:
        return {"state": "running", "run": entry[1], "started": entry[2]}
    out = {"state": "idle", "run": "", "due": False}
    if entry:                                            # finished while this server was up
        rc, run = entry[0].returncode, entry[1]
        out.update(run=run, state="ok" if rc == 0 else "failed")
        if rc != 0:
            out.update(problems.describe(f"run failed with exit code {rc}"), message="The run stopped before it finished.",
                       fix="Press Run now to try again. If it stops again, open the log: logs/run-now.log in your data folder.")
    for run in ("morning", "midday"):
        hb = _heartbeat(conn, run)
        if hb.get("started") and not hb.get("finished") and not hb.get("failed") and _age_min(hb["started"]) < STALE_MIN and not entry:
            return {"state": "running", "run": run, "started": hb["started"]}     # started by the scheduler or by an earlier server
    hb = _heartbeat(conn, "morning")
    out["finished"] = hb.get("finished", "")
    try:
        times = scheduler.current_times(config.load(Path(data_dir) / "config.toml"))
        due_at = dt.datetime.combine(dt.date.today(), dt.datetime.strptime(times["morning"], "%H:%M").time()) + dt.timedelta(minutes=30)
        out["due"] = not hb.get("finished") and dt.datetime.now() > due_at
    except Exception:
        pass                                             # an unreadable schedule only hides the hint
    return out


def start(conn, data_dir, run="morning", popen=subprocess.Popen):
    """Launch the run. Returns the status; raises ValueError (plain text) when a run is already going or `run` is unknown."""
    if run not in scheduler.RUNS:
        raise ValueError("run must be morning or midday")
    st = status(conn, data_dir)
    if st["state"] == "running":
        raise ValueError(f"The {st['run']} run is already going. Wait for it to finish.")
    cfg = config.load(Path(data_dir) / "config.toml")
    argv = scheduler.command(run, cfg.mode.name, data_dir)
    logs = Path(data_dir) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = open(logs / "run-now.log", "a", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
    p = popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    log.close()                                          # the child holds its own handle
    with _lock:
        _procs[str(data_dir)] = (p, run, dt.datetime.now().isoformat(timespec="seconds"))
    return {"state": "running", "run": run}
