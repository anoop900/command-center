"""Lets the always-on dashboard notice that its own code was replaced (an upgrade) and restart on the new code."""
import hashlib
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KINDS = {".py", ".html", ".js", ".md", ".css"}
EXIT_RESTART = 3  # non-zero, so Task Scheduler's RestartOnFailure restarts it; launchd KeepAlive restarts any exit


def fingerprint(root=None):
    """Hash of names, sizes and mtimes of the code files, or None when they cannot be read (an upgrade is mid-copy)."""
    root = Path(root or ROOT)
    h = hashlib.sha256()
    if not root.is_dir():
        return None
    try:
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix in KINDS and "__pycache__" not in p.parts:
                st = p.stat()
                h.update(f"{p.relative_to(root).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    except OSError:
        return None
    return h.hexdigest()


def watch(on_change, interval=60, settle=5, root=None, stop=None):
    """Poll in a daemon thread. A change must hold still for `settle` seconds before on_change() runs, once."""
    stop = stop or threading.Event()
    start = fingerprint(root)

    def loop():
        while not stop.wait(interval):
            now = fingerprint(root)
            if now is None or start is None or now == start:
                continue
            if stop.wait(settle):
                return
            if fingerprint(root) == now:
                on_change()
                return

    t = threading.Thread(target=loop, daemon=True, name="selfwatch")
    t.start()
    return stop


def respawn_if_needed(argv):
    """cron has no restart policy: start the replacement ourselves. macOS restarts us via the exit code. Windows runs the
    replacement as a child and waits, because Task Scheduler's RestartOnFailure did not bring the dashboard back."""
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.call(argv, stdin=subprocess.DEVNULL, creationflags=flags) or 0
        except OSError:
            return EXIT_RESTART  # could not start it: fall back to the scheduler's restart
    if sys.platform.startswith("darwin"):
        return EXIT_RESTART
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return 0
