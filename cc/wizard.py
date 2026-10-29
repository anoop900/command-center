"""Setup wizard state and step 0 (welcome + demo). Later steps add to STEPS handling here."""
import datetime as dt
import json
import re
import shutil
import threading
from pathlib import Path

from . import assets, config, modes, profile, scheduler, store

STEPS = 8  # steps 0..7, DESIGN section 7
DEMO_DIR = assets.find("demo")
# Collections whose non-demo content means the user already has real data.
USER_DATA = ("roles", "contacts", "targets", "tasks", "offers")
DOC = ("admin", "wizard")


def _tagged(row):
    return row["data"].get("demo") is True


def has_real_data(conn):
    return any(not _tagged(r) for c in USER_DATA for r in store.list_docs(conn, c))


def profile_complete(data_dir):
    return data_dir is not None and profile.completeness(data_dir)["percent"] == 100


def state(conn, data_dir=None):
    cur = store.get(conn, *DOC)
    d = cur["data"] if cur else {}
    step = d.get("step") if isinstance(d.get("step"), int) and 0 <= d["step"] < STEPS else 0
    done = sorted({s for s in d.get("done", []) if isinstance(s, int) and 0 <= s < STEPS})
    demo = any(_tagged(r) for c in USER_DATA for r in store.list_docs(conn, c))
    return {"step": step, "done": done, "demo": demo, "fresh": cur is None and not has_real_data(conn),
            "demo_locked": profile_complete(data_dir)}


def _save(conn, step, done):
    store.set_doc(conn, *DOC, {"step": step, "done": sorted(done)}, by="wizard", force=True)


def go(conn, step, complete=False):
    """Move to a step (re-running an earlier one is allowed). complete=True also marks the current step done."""
    if isinstance(step, bool) or not isinstance(step, int) or not 0 <= step < STEPS:
        raise ValueError(f"step must be a whole number from 0 to {STEPS - 1}")
    s = state(conn)
    done = set(s["done"])
    if complete:
        done.add(s["step"])
    _save(conn, step, done)
    return state(conn)


DEMO_DAY = dt.date(2026, 10, 12)        # the day the seed's dates were written for
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NOT_DATES = {"kit", "resume_file"}      # kit folders keep their names


def _shift(v, days, key=""):
    """Move every date in a seed record by `days`, so the demo looks current whenever it is loaded."""
    if isinstance(v, dict):
        return {k: _shift(x, days, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_shift(x, days, key) for x in v]
    if isinstance(v, str) and key not in _NOT_DATES and _ISO.match(v):
        try:
            return (dt.date.fromisoformat(v) + dt.timedelta(days=days)).isoformat()
        except (ValueError, OverflowError):
            return v
    return v


def load_demo(conn, root=None, data_dir=None, today=None):
    """Tag-and-import the fictional seed. Refuses when real data exists or the profile is complete; same input twice changes nothing."""
    if profile_complete(data_dir):
        raise ValueError("Your profile is complete, so the demo is switched off to keep it apart from your own data.")
    if has_real_data(conn):
        raise ValueError("You already have your own data, so the demo was not loaded.")
    root = Path(root) if root else DEMO_DIR
    if not root.is_dir():
        raise ValueError("The demo files are not installed.")
    docs = []
    days = ((today or dt.date.today()) - DEMO_DAY).days
    for f in sorted(root.glob("*/*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        if f.parent.name in USER_DATA and isinstance(data, dict):
            docs.append((f.parent.name, f.stem, {**_shift(data, days), "demo": True}))
    for col, doc_id, data in docs:
        cur = store.get(conn, col, doc_id)
        if cur is None or cur["data"] != data:
            store.set_doc(conn, col, doc_id, data, by="wizard", force=True)
    _refresh_board(conn)
    return {"loaded": len(docs)}


def clear_demo(conn):
    """Delete only records tagged demo; anything the user added or edited into non-demo stays."""
    n = 0
    for c in USER_DATA:
        for r in store.list_docs(conn, c):
            if _tagged(r) and store.delete_doc(conn, c, r["id"], r["version"], by="wizard"):
                n += 1
    _refresh_board(conn)
    return {"cleared": n}


def settle(conn, data_dir):
    """After anything that can change profile completeness: a complete profile switches the demo off (clearing it if
    loaded), and today's coaching tasks are brought in line with what is true now."""
    from . import coach
    if profile_complete(data_dir) and any(_tagged(r) for c in USER_DATA for r in store.list_docs(conn, c)):
        clear_demo(conn)
    coach.refresh(conn, data_dir)


def _refresh_board(conn):
    from .runs import morning
    morning.run(conn, "morning")


def choose_mode(conn, cfg_file, name):
    """Step 1: save the chosen mode to config.toml and move on. Data is untouched, so this is also the upgrade path."""
    if not isinstance(name, str):
        raise ValueError("choose one of: " + ", ".join(modes.MODES))
    try:
        config.set_mode(cfg_file, name)
    except modes.ModeError as e:
        raise ValueError(str(e)) from None
    s = state(conn)
    done = set(s["done"]) | {1}
    _save(conn, max(s["step"], 2) if s["step"] <= 1 else s["step"], done)
    return {"mode": name}


def change_mode(conn, cfg_file, name, runner=None, platform=None):
    """Switch mode any time. Data is untouched. Installed scheduled tasks bake in whether runs may use AI, so when that
    flips they are reinstalled with the saved times; if that fails the mode stays changed and the reply says so."""
    old = config.load(cfg_file).mode.name
    out = choose_mode(conn, cfg_file, name)
    out.update(schedule="none", message="")
    if modes.schedules_ai(old) == modes.schedules_ai(name):
        installed = scheduler.status(runner or scheduler.system_runner, platform, Path(cfg_file).parent)["installed"]
        out["schedule"] = "unchanged" if any(installed.values()) else "none"
        return out
    runner = runner or scheduler.system_runner
    if not any(scheduler.status(runner, platform, Path(cfg_file).parent)["installed"].values()):
        return out
    cfg = config.load(cfg_file)
    try:
        scheduler.install(cfg, scheduler.current_times(cfg), runner, platform, Path(cfg_file).parent)
        out.update(schedule="updated", message="Your scheduled runs now follow the new mode.")
    except (scheduler.SchedulerError, ValueError, OSError) as e:
        out.update(schedule="failed", message=f"Mode changed, but the schedule could not be updated: {str(e)[:150]}. "
                   "Open Setup > Schedule and press Update schedule.")
    return out


# Step 2: email and calendar. Each live test reads only (login + select, or one GET) and never stores anything.
HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$")
PROVIDERS = {
    "gmail": {"label": "Gmail", "host": "imap.gmail.com", "steps": [
        "Turn on 2-step verification for your Google account.",
        "Open your Google account's App passwords page and create one named Command Center.",
        "Paste the 16-letter app password below, not your normal password."]},
    "icloud": {"label": "iCloud Mail", "host": "imap.mail.me.com", "steps": [
        "Sign in at appleid.apple.com and open Sign-In and Security.",
        "Create an app-specific password named Command Center.",
        "Paste it below. Use your full iCloud address as the user."]},
    "yahoo": {"label": "Yahoo Mail", "host": "imap.mail.yahoo.com", "steps": [
        "Open Account Security in your Yahoo account and turn on 2-step verification.",
        "Generate an app password named Command Center and paste it below."]},
    "outlook": {"label": "Outlook / Hotmail", "host": "outlook.office365.com", "steps": [
        "Microsoft has been closing password sign-in to personal mailboxes, so this may not work for you.",
        "If the test fails, skip mail: the app still runs, and you can forward job alerts to a Gmail address instead."]},
    "other": {"label": "Another provider", "host": "", "steps": [
        "Find your provider's IMAP server name and whether it needs an app password.",
        "Type the server name below. It must use a secure (SSL) connection."]},
}


def _clean_text(v, what, limit=254):
    if not isinstance(v, str) or not v.strip() or len(v) > limit or not v.isprintable() or any(c in v for c in ' "\\'):
        raise ValueError(f"{what} is missing or has characters that are not allowed")
    return v.strip()


def _mail_args(host, user, folder, password):
    host = _clean_text(host, "Server name").lower()
    if not HOST_RE.match(host):
        raise ValueError("Server name should look like imap.example.com")
    user = _clean_text(user, "User")
    folder = _clean_text(folder or "INBOX", "Folder", 100)
    if not isinstance(password, str) or not password or len(password) > 256 or not password.isprintable():
        raise ValueError("Password is missing or has characters that are not allowed")
    return host, user, folder, password


def _scrub(e, *secret):
    t = str(e)
    for sv in secret:
        t = t.replace(sv, "***")
    return t[:120]


_MAIL_HINTS = (
    ("application-specific password", "Use an app password, not your normal one: with 2-step verification on, create it at myaccount.google.com/apppasswords and paste the 16 characters without spaces."),
    ("app password", "Use an app password, not your normal one: create it in your account's security settings."),
    ("authenticationfailed", "Check the address and the app password."),
    ("invalid credentials", "Check the address and the app password."),
    ("imap", "If IMAP is switched off, turn it on in your mail settings."),
)


def _mail_hint(text):
    low = str(text).lower()
    return next((" " + h for k, h in _MAIL_HINTS if k in low), "")


def check_mail(host, user, folder, password, opener=None):
    """Log in read-only and open the folder. Returns {"ok": True}; raises ValueError with a short reason."""
    from .ingest import imap
    host, user, folder, password = _mail_args(host, user, folder, password)
    try:
        c = imap.ReadOnly((opener or imap._open)(host))
        c.login(user, password)
        typ, data = c.select(folder)
        if typ != "OK":
            raise ValueError(f"Signed in, but the folder {folder} could not be opened")
        try:
            c.logout()
        except Exception:
            pass
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("Mail test failed: " + _scrub(e, password, user) + _mail_hint(e)) from None
    return {"ok": True, "folder": folder}


def save_mail(cfg_file, host, user, folder, password, opener=None):
    """Only a mailbox that passed the live test is saved: server and user to config.toml, password to the keyring."""
    from . import secrets
    r = check_mail(host, user, folder, password, opener)
    host, user, folder, password = _mail_args(host, user, folder, password)
    config.set_values(cfg_file, "mail", {"host": host, "user": user, "folder": folder})
    r["stored_in"] = secrets.set("mail_password", password)
    return r


def _cal_hint(text):
    t = str(text)
    if "404" in t or "403" in t:
        return (" The feed address was not accepted. In Google Calendar open Settings, pick the calendar, and copy "
                "Secret address in iCal format (not the public address). If it was reset, copy the new one.")
    return ""


def check_calendar(url, fetcher=None):
    from .ingest import ics
    from .runs import sources
    import datetime as dt
    if not isinstance(url, str) or len(url) > 2000 or not url.isprintable() or not url.lower().startswith("https://"):
        raise ValueError("The calendar link must start with https://")
    try:
        text = (fetcher or sources.fetch_ics)(url.strip())
        events, _ = ics.parse_ics(text, dt.date.today())
    except Exception as e:
        raise ValueError("Calendar test failed: " + _scrub(e, url) + _cal_hint(e)) from None
    if "BEGIN:VCALENDAR" not in text.upper():
        raise ValueError("That link did not return a calendar feed")
    return {"ok": True, "events_next_14_days": len(events)}


def save_calendar(cfg_file, url, fetcher=None):
    from . import secrets
    r = check_calendar(url, fetcher)
    config.set_values(cfg_file, "calendar", {"ics_configured": True})
    r["stored_in"] = secrets.set("calendar_url", url.strip())
    return r


def skip_step(conn, step):
    """Email and calendar are optional; skipping marks the step done so the wizard can move on."""
    if isinstance(step, bool) or not isinstance(step, int) or not 0 <= step < STEPS - 1:
        raise ValueError(f"step must be a whole number from 0 to {STEPS - 2}")
    return go(conn, step + 1, complete=True)


EXT_SRC = assets.find("extension")
EXT_SKIP = shutil.ignore_patterns("test", "tools", "profile.example.json", "profile.json", "__pycache__")
EXT_NEEDED = ("firstName", "lastName", "email")


def extension_status(data_dir, src=None):
    """Read-only checks of what the server can see. Whether Chrome has the extension loaded it cannot see."""
    d, src = Path(data_dir), Path(src or EXT_SRC)
    out = []
    ext = d / "extension"
    out.append({"check": "extension files", "ok": (ext / "manifest.json").is_file(),
                "detail": f"Copied to {ext}" if (ext / "manifest.json").is_file() else "Not copied yet. Press Prepare."})
    pj, prob = ext / "profile.json", None
    try:
        facts = json.loads(pj.read_text(encoding="utf-8"))
        if not isinstance(facts, dict):
            raise ValueError
        missing = [k for k in EXT_NEEDED if not facts.get(k)]
        if missing:
            prob = "Missing " + ", ".join(missing) + ". Confirm them in step 3."
    except FileNotFoundError:
        prob = "No profile.json yet. Press Prepare after finishing step 3."
    except (ValueError, OSError):
        prob = "profile.json is damaged. Press Prepare to rewrite it."
    out.append({"check": "profile", "ok": prob is None, "detail": prob or "Your confirmed facts are in place."})
    kits = config.kits_dir(d)
    out.append({"check": "kits folder", "ok": kits.is_dir(),
                "detail": f"Choose {kits} in the extension Options." if kits.is_dir() else "No kits folder yet; it appears after the first kit."})
    out.append({"check": "extension source", "ok": src.is_dir(), "detail": "Found." if src.is_dir() else f"Not found at {src}."})
    return {"checks": out, "ready": all(c["ok"] for c in out if c["check"] != "kits folder"),
            "folder": str(ext), "try": "Open any application page, press Alt+J, press Fill this page, then tick the box below."}


def prepare_extension(data_dir, src=None):
    """Copy the extension into the data folder and give it the step 3 profile. Never touches a profile.json it did not write."""
    d, src = Path(data_dir), Path(src or EXT_SRC)
    if not src.is_dir():
        raise ValueError("The extension files were not found next to this install.")
    facts = d / "profile.json"
    if not facts.is_file():
        raise ValueError("Finish step 3 (profile) first: there are no confirmed facts to give the extension.")
    try:
        data = json.loads(facts.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise ValueError("profile.json in your data folder is damaged. Save step 3 again.") from None
    if not isinstance(data, dict):
        raise ValueError("profile.json in your data folder is damaged. Save step 3 again.")
    dest = d / "extension"
    if not profile._ours(dest / "profile.json"):
        raise ValueError("extension/profile.json already exists and was not written by the wizard, so it was left alone.")
    shutil.copytree(src, dest, ignore=EXT_SKIP, dirs_exist_ok=True)
    profile._write(dest / "profile.json", json.dumps(data, indent=1))
    config.kits_dir(d).mkdir(parents=True, exist_ok=True)
    return extension_status(d, src)


_RUNNING = threading.Lock()


def first_run(conn, cfg_file, gather=None, tracker_path=None):
    """One morning run from the wizard, no AI. Fetch problems become notes; demo data blocks it so the two never mix."""
    from . import api, tracker
    from .runs import morning, sources
    if state(conn)["demo"]:
        raise ValueError("Remove the demo first, so your own data is not mixed with it.")
    cfg = config.load(cfg_file)
    if not _RUNNING.acquire(blocking=False):
        raise ValueError("A run is already in progress. Wait for it to finish.")
    try:
        emails = [d["data"].get("email", "") for d in store.list_docs(conn, "contacts")]
        kw = (gather or sources.gather)(cfg, "morning", contact_emails=emails)
        api.run_heartbeat(conn, "morning", "start")
        try:
            result = morning.run(conn, "morning", tracker_path=tracker_path or tracker.default_path(), **kw)
        except (ValueError, OSError, KeyError, TypeError) as e:
            raise ValueError(f"The run stopped: {str(e)[:150]}") from None
        api.run_heartbeat(conn, "morning", "finish", info={k: result[k] for k in ("roles", "todos", "notes") if k in result})
    finally:
        _RUNNING.release()
    return {"roles": result.get("roles"), "todos": result.get("todos"), "notes": result.get("notes"),
            "errors": [str(e)[:200] for e in (kw.get("errors") or [])[:5]],
            "mail": kw.get("msgs") is not None, "calendar": bool(kw.get("ics_text"))}


def finish(conn):
    """Mark the last step done; the wizard is then complete."""
    _save(conn, STEPS - 1, set(state(conn)["done"]) | {STEPS - 1})
    return state(conn)
