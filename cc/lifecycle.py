"""Install-time and everyday entry points a non-technical user reaches: `cc start`, `cc open`, `cc uninstall`.
OS calls go through scheduler's runner so tests never touch a real scheduler."""
import os
import shutil
import sys
import time
import webbrowser
from pathlib import Path

from . import config, scheduler

FIRST_PORT, PORT_TRIES = scheduler.SERVICE_PORT, 20
SHORTCUT = "Command Center.lnk"


def shortcut_path():
    base = os.environ.get("APPDATA")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / SHORTCUT if base else None


def _usable_port(data_dir):
    """The saved port when the service already owns it, else the first free port from 8791."""
    saved = scheduler._saved_port(data_dir)
    if (Path(data_dir) / scheduler.SERVICE_FILE).exists() and (scheduler._reachable(saved) or scheduler._port_free(saved)):
        return saved
    for p in range(FIRST_PORT, FIRST_PORT + PORT_TRIES):
        if scheduler._port_free(p):
            return p
    return None


def _wait(port, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if scheduler._reachable(port):
            return True
        time.sleep(0.5)
    return scheduler._reachable(port)


def start(rest, runner=None, wait=15, foreground=None, opener=None, platform=None):
    """Install (or reuse) the always-on dashboard, wait until it answers, open the browser, and return. If the OS refuses
    the background task, fall back to the foreground wizard (`foreground(rest)`), whose window must stay open."""
    runner = runner or scheduler.system_runner
    opener = opener or webbrowser.open
    config.init_data_dir()
    config.load()
    d = config.data_dir()
    st = scheduler.service_status(runner, platform, d)
    if st["installed"] and st["running"] and "--reinstall" not in rest:
        port = st["port"]
    else:
        port = _usable_port(d)
        if port is None:
            print(f"No free port from {FIRST_PORT} to {FIRST_PORT + PORT_TRIES - 1} on this computer. "
                  "Close other programs that use local ports, then run: cc start", file=sys.stderr)
            return 1
        try:
            scheduler.service_install(port, runner, platform, d)
        except (scheduler.SchedulerError, OSError) as e:
            print(f"Could not set Command Center to run in the background ({str(e)[:150]}).")
            if "--no-foreground" in rest or not foreground:
                return 1                      # the installer must end with a message, never wait in a window
            print("Falling back: it will run in this window instead. Keep this window open while you use it.")
            return foreground(rest)
    url = f"http://127.0.0.1:{port}/"
    if not _wait(port, wait):
        print(f"Command Center was installed but is not answering yet at {url}", file=sys.stderr)
        print(f"Look at the end of this file for the reason: {Path(d) / 'logs' / 'web.log'}", file=sys.stderr)
        return 1
    if "--no-open" not in rest:
        try:
            opener(url)
        except Exception:
            pass
    print(f"Command Center is running in the background at {url}")
    print("It starts by itself when you log in. You can close this window now.")
    return 0


def data_dir_problem(d):
    """Why this folder must not be deleted, or "" when it is safe: only a folder that looks like a Command Center data
    folder, and never a drive root, the home folder or one of its parents."""
    try:
        d = Path(d).resolve()
    except OSError:
        return "the folder cannot be resolved"
    home = Path.home().resolve()
    if d == d.parent or d == home or d in home.parents:
        return "it is a drive root or your home folder"
    if d.is_symlink() or not d.is_dir():
        return "it is not an ordinary folder"
    if not any((d / m).exists() for m in ("config.toml", "commandcenter.db")):
        return "it does not look like a Command Center data folder"
    return ""


def uninstall(rest, runner=None, platform=None):
    """Remove the background dashboard, the scheduled runs and the Start menu shortcut. The data folder is deleted only
    with --delete-data (typed yes, or --yes). bootstrap/uninstall.ps1 removes the program and what the installer added."""
    runner = runner or scheduler.system_runner
    d = config.data_dir()
    problems = []
    for what, call in (("dashboard task", lambda: scheduler.service_remove(runner, platform, d)),
                       ("scheduled runs", lambda: scheduler.remove(runner, platform))):
        try:
            call()
        except (scheduler.SchedulerError, OSError) as e:
            problems.append(f"{what}: {str(e)[:150]}")
    sc = shortcut_path()
    if (platform or sys.platform).startswith("win") and sc:
        try:
            sc.unlink(missing_ok=True)
        except OSError as e:
            problems.append(f"Start menu shortcut: {e}")
    for p in problems:
        print(f"Could not remove the {p}", file=sys.stderr)
    print("Removed the background dashboard and the scheduled runs." if not problems else "Some parts were not removed (above).")
    deleted = False
    if "--delete-data" in rest and Path(d).exists():
        why = data_dir_problem(d)
        if why:
            print(f"Your data folder was NOT deleted ({why}): {d}", file=sys.stderr)
            problems.append("data folder")
        elif "--yes" in rest or input(f"Delete ALL your data in {d}? This cannot be undone. Type yes: ").strip().lower() == "yes":
            for _ in range(3):      # the stopped server can hold a log file for a moment
                shutil.rmtree(d, ignore_errors=True)
                if not Path(d).exists():
                    break
                time.sleep(1)
            deleted = not Path(d).exists()
            if deleted:
                from . import secrets
                from .ai import runners
                for name in (runners.KEY_NAME, "mail_password", "calendar_url"):
                    secrets.delete(name)    # saved passwords and keys live in the OS keyring, outside the data folder
                print(f"Deleted your data folder and the passwords saved with it: {d}")
            else:
                print(f"Could not delete everything in {d}. Close programs using it and delete it by hand.", file=sys.stderr)
                problems.append("data folder")
    if not deleted:
        print(f"Your data was NOT deleted. It is in: {d}")
    print("To finish removing the program itself, run: pipx uninstall cc")
    return 1 if problems else 0
