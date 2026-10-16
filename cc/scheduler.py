"""Wizard step 5: install, remove and check the two OS tasks that run the morning and midday runs.
Per-user, no admin rights, no stored password. Install replaces the two tasks by name and touches nothing else.
All OS calls go through `runner(argv, input_text) -> (returncode, output)` so tests never touch a real scheduler."""
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from shlex import quote
from xml.sax.saxutils import escape

from . import config, modes

RUNS = ("morning", "midday")
HHMM = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
WIN_PREFIX = "CommandCenter-"
MAC_PREFIX = "com.commandcenter."
CRON_BEGIN, CRON_END = "# CommandCenter begin", "# CommandCenter end"
# One argument list for every OS: the data folder travels as an argument, never inside code text.
BOOT = "import os,sys;os.environ['CC_HOME']=sys.argv[1];from cc.cli import main;sys.exit(main(sys.argv[2:]))"


class SchedulerError(ValueError):
    pass


def check_times(times):
    out = {}
    for k in RUNS:
        v = times.get(k) if isinstance(times, dict) else None
        if not isinstance(v, str) or not HHMM.fullmatch(v):
            raise SchedulerError(f"{k}: time must look like 07:30 (24-hour)")
        out[k] = v
    if out["morning"] == out["midday"]:
        raise SchedulerError("morning and midday must be at different times")
    return out


def current_times(cfg):
    return {"morning": cfg.run.morning, "midday": cfg.run.midday}


def _python():
    exe = Path(sys.executable)
    if os.name == "nt" and exe.with_name("pythonw.exe").exists():
        return str(exe.with_name("pythonw.exe"))  # no console window flashing on every run
    return str(exe)


def command(run, mode, data_dir, catch_up=False):
    """argv for one run. Only autopilot may call an AI with nobody present; every other mode runs the no-AI path."""
    args = ["run", run] + ([] if modes.schedules_ai(mode) else ["--no-ai"]) + (["--catch-up"] if catch_up else [])
    return [_python(), "-c", BOOT, str(data_dir), *args]


def system_runner(argv, input_text=None):
    try:
        p = subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return p.returncode, (p.stdout + p.stderr).strip()


# ---- Windows ---------------------------------------------------------------------------------
def windows_xml(argv, hhmm):
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><CalendarTrigger><StartBoundary>2024-01-01T{hhmm}:00</StartBoundary><Enabled>true</Enabled>
    <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT30M</ExecutionTimeLimit><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>{escape(argv[0])}</Command>
    <Arguments>{escape(subprocess.list2cmdline(argv[1:]))}</Arguments></Exec></Actions>
</Task>
"""


def _win_install(jobs, runner):
    for name, hhmm, argv in jobs:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "task.xml"
            f.write_text(windows_xml(argv, hhmm), encoding="utf-16")
            rc, out = runner(["schtasks", "/Create", "/TN", WIN_PREFIX + name, "/XML", str(f), "/F"], None)
        if rc != 0:
            raise SchedulerError(f"Task Scheduler refused the {name} task: {out[:200]}")


# ---- macOS -----------------------------------------------------------------------------------
def launchd_plist(name, hhmm, argv):
    h, m = hhmm.split(":")
    return plistlib.dumps({"Label": MAC_PREFIX + name, "ProgramArguments": argv,
                           "StartCalendarInterval": {"Hour": int(h), "Minute": int(m)}, "RunAtLoad": False})


def _mac_dir():
    return Path.home() / "Library" / "LaunchAgents"


def _mac_install(jobs, runner):
    _mac_dir().mkdir(parents=True, exist_ok=True)
    for name, hhmm, argv in jobs:
        p = _mac_dir() / f"{MAC_PREFIX}{name}.plist"
        runner(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)], None)  # fine if it was not loaded
        p.write_bytes(launchd_plist(name, hhmm, argv))
        rc, out = runner(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(p)], None)
        if rc != 0:
            raise SchedulerError(f"launchd refused the {name} task: {out[:200]}")


# ---- Linux (cron) ----------------------------------------------------------------------------
def cron_lines(jobs, data_dir, mode):
    out = [CRON_BEGIN]
    for name, hhmm, argv in jobs:
        h, m = hhmm.split(":")
        out.append(f"{int(m)} {int(h)} * * * {' '.join(quote(a) for a in argv)}")
        # cron has no catch-up: also try once at boot; --catch-up skips when today's run is done or not yet due
        out.append("@reboot " + " ".join(quote(a) for a in command(name, mode, data_dir, catch_up=True)))
    return out + [CRON_END]


def _strip_cron(text):
    keep, skip = [], False
    for line in text.splitlines():
        if line.strip() == CRON_BEGIN:
            skip = True
        elif line.strip() == CRON_END:
            skip = False
        elif not skip:
            keep.append(line)
    return keep


def _cron_write(runner, lines):
    rc, out = runner(["crontab", "-"], "\n".join(lines).strip("\n") + "\n" if lines else "")
    if rc != 0:
        raise SchedulerError(f"crontab refused the change: {out[:200]}")


def _cron_read(runner):
    rc, out = runner(["crontab", "-l"], None)
    return out if rc == 0 else ""  # "no crontab for user" is an empty one


# ---- public ----------------------------------------------------------------------------------
def _kind(platform):
    return "windows" if platform.startswith("win") else "mac" if platform == "darwin" else "cron"


def install(cfg, times, runner=system_runner, platform=None, data_dir=None):
    """Validate, save the times to config.toml, then (re)create both tasks. Returns the status afterwards."""
    times = check_times(times)
    data_dir = data_dir or config.data_dir()
    mode = cfg.mode.name
    kind = _kind(platform or sys.platform)
    jobs = [(r, times[r], command(r, mode, data_dir)) for r in RUNS]
    if kind == "windows":
        _win_install(jobs, runner)
    elif kind == "mac":
        _mac_install(jobs, runner)
    else:
        kept = _strip_cron(_cron_read(runner))
        _cron_write(runner, kept + cron_lines(jobs, data_dir, mode))
    config.set_values(Path(data_dir) / "config.toml", "run", times)
    return status(runner, platform)


def remove(runner=system_runner, platform=None):
    kind = _kind(platform or sys.platform)
    if kind == "windows":
        for r in RUNS:
            if runner(["schtasks", "/Query", "/TN", WIN_PREFIX + r], None)[0] == 0:
                rc, out = runner(["schtasks", "/Delete", "/TN", WIN_PREFIX + r, "/F"], None)
                if rc != 0:
                    raise SchedulerError(f"could not remove the {r} task: {out[:200]}")
    elif kind == "mac":
        for r in RUNS:
            p = _mac_dir() / f"{MAC_PREFIX}{r}.plist"
            runner(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)], None)
            p.unlink(missing_ok=True)
    else:
        kept = _strip_cron(_cron_read(runner))
        _cron_write(runner, kept)
    return status(runner, platform)


def status(runner=system_runner, platform=None):
    kind = _kind(platform or sys.platform)
    if kind == "windows":
        found = {r: runner(["schtasks", "/Query", "/TN", WIN_PREFIX + r], None)[0] == 0 for r in RUNS}
    elif kind == "mac":
        found = {r: (_mac_dir() / f"{MAC_PREFIX}{r}.plist").exists() for r in RUNS}
    else:
        text = _cron_read(runner)
        found = {r: CRON_BEGIN in text and f" run {r}" in text for r in RUNS}
    return {"os": kind, "installed": found, "all_installed": all(found.values())}


# ---- always-on dashboard ---------------------------------------------------------------------
# One per-user task that starts `cc web` at login and keeps it up, so a bookmark always works. It never picks
# another port: if the port is taken `cc web` exits with a message in the log, and the bookmark stays honest.
SERVICE = "web"
SERVICE_PORT = 8791
SVC_BEGIN, SVC_END = "# CommandCenter web begin", "# CommandCenter web end"
SERVICE_FILE = "service.json"
# argv: data folder, log file, then the cc arguments. The log is trimmed at 1 MB so it cannot grow forever.
BOOT_WEB = ("import os,sys;os.environ['CC_HOME']=sys.argv[1];p=sys.argv[2];"
            "os.path.exists(p) and os.path.getsize(p)>1000000 and os.replace(p,p+'.1');"
            "sys.stdout=sys.stderr=open(p,'a',buffering=1,encoding='utf-8');"
            "from cc.cli import main;sys.exit(main(sys.argv[3:]))")


def check_port(port):
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise SchedulerError("port: a whole number from 1024 to 65535")
    return port


def service_command(data_dir, port, watch=True):
    log = Path(data_dir) / "logs" / "web.log"
    return [_python(), "-c", BOOT_WEB, str(data_dir), str(log), "web", "--port", str(check_port(port))] + (["--watch"] if watch else [])


def service_windows_xml(argv, user):
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>99</Count></RestartOnFailure><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>{escape(argv[0])}</Command>
    <Arguments>{escape(subprocess.list2cmdline(argv[1:]))}</Arguments></Exec></Actions>
</Task>
"""


def service_plist(argv):
    return plistlib.dumps({"Label": MAC_PREFIX + SERVICE, "ProgramArguments": argv, "RunAtLoad": True, "KeepAlive": True})


def _win_user():
    import getpass
    dom = os.environ.get("USERDOMAIN")
    return f"{dom}\\{getpass.getuser()}" if dom else getpass.getuser()


def _reachable(port):
    import urllib.request
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.5).close()
        return True
    except Exception:
        return False


def _saved_port(data_dir):
    import json
    try:
        return check_port(json.loads((Path(data_dir) / SERVICE_FILE).read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError, SchedulerError):
        return SERVICE_PORT


def _port_free(port):
    import socket
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def service_install(port=SERVICE_PORT, runner=system_runner, platform=None, data_dir=None):
    """(Re)create the login task and start it now. Replaces only its own task, plist or crontab block."""
    import json
    port = check_port(port)
    data_dir = Path(data_dir or config.data_dir())
    # A busy port is fine only when it is this service's own saved port (a reinstall); otherwise fail now, not in the log.
    own = (data_dir / SERVICE_FILE).exists() and _saved_port(data_dir) == port
    if not own and not _port_free(port):
        raise SchedulerError(f"port {port} is already in use on 127.0.0.1; pick another with --port N (1024-65535)")
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    argv = service_command(data_dir, port)
    kind = _kind(platform or sys.platform)
    if kind == "windows":
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "task.xml"
            f.write_text(service_windows_xml(argv, _win_user()), encoding="utf-16")
            rc, out = runner(["schtasks", "/Create", "/TN", WIN_PREFIX + SERVICE, "/XML", str(f), "/F"], None)
        if rc != 0:
            raise SchedulerError(f"Task Scheduler refused the web task: {out[:200]}")
        # Task Scheduler refuses /Run while an instance is alive, so a reinstall would keep serving the old code.
        runner(["schtasks", "/End", "/TN", WIN_PREFIX + SERVICE], None)
        for _ in range(20):  # up to 5 s for the old server to let go of its port
            if _port_free(port):
                break
            time.sleep(0.25)
        runner(["schtasks", "/Run", "/TN", WIN_PREFIX + SERVICE], None)  # start now; a taken port shows in the log
    elif kind == "mac":
        _mac_dir().mkdir(parents=True, exist_ok=True)
        p = _mac_dir() / f"{MAC_PREFIX}{SERVICE}.plist"
        runner(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)], None)
        p.write_bytes(service_plist(argv))
        rc, out = runner(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(p)], None)
        if rc != 0:
            raise SchedulerError(f"launchd refused the web task: {out[:200]}")
    else:
        kept = _strip_svc_cron(_cron_read(runner))
        _cron_write(runner, kept + [SVC_BEGIN, "@reboot " + " ".join(quote(a) for a in argv), SVC_END])
        runner(["sh", "-c", " ".join(quote(a) for a in argv) + " >/dev/null 2>&1 &"], None)
    (data_dir / SERVICE_FILE).write_text(json.dumps({"port": port}), encoding="utf-8")
    return service_status(runner, platform, data_dir)


def _strip_svc_cron(text):
    keep, skip = [], False
    for line in text.splitlines():
        if line.strip() == SVC_BEGIN:
            skip = True
        elif line.strip() == SVC_END:
            skip = False
        elif not skip:
            keep.append(line)
    return keep


def service_remove(runner=system_runner, platform=None, data_dir=None):
    kind = _kind(platform or sys.platform)
    if kind == "windows":
        name = WIN_PREFIX + SERVICE
        if runner(["schtasks", "/Query", "/TN", name], None)[0] == 0:
            runner(["schtasks", "/End", "/TN", name], None)  # stop the running server too
            rc, out = runner(["schtasks", "/Delete", "/TN", name, "/F"], None)
            if rc != 0:
                raise SchedulerError(f"could not remove the web task: {out[:200]}")
    elif kind == "mac":
        p = _mac_dir() / f"{MAC_PREFIX}{SERVICE}.plist"
        runner(["launchctl", "bootout", f"gui/{os.getuid()}", str(p)], None)
        p.unlink(missing_ok=True)
    else:
        _cron_write(runner, _strip_svc_cron(_cron_read(runner)))
    return service_status(runner, platform, data_dir)


def service_status(runner=system_runner, platform=None, data_dir=None):
    kind = _kind(platform or sys.platform)
    data_dir = Path(data_dir or config.data_dir())
    if kind == "windows":
        installed = runner(["schtasks", "/Query", "/TN", WIN_PREFIX + SERVICE], None)[0] == 0
    elif kind == "mac":
        installed = (_mac_dir() / f"{MAC_PREFIX}{SERVICE}.plist").exists()
    else:
        installed = SVC_BEGIN in _cron_read(runner)
    port = _saved_port(data_dir)
    return {"os": kind, "installed": installed, "port": port, "url": f"http://127.0.0.1:{port}/",
            "running": _reachable(port)}
