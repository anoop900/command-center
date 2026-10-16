"""Full backup and restore of the data folder as one `.ccbackup` file (a zip with a manifest and a SHA-256 per file).

What goes in is an allow-list, so logs, exports, the extension copy and anything unknown stay out. Passwords and API keys
never go in: they live in the OS keyring (or secrets.json, which is not on the list). Restore verifies first, saves the
CURRENT state to <data>/backups/before-restore-*.ccbackup, then replaces; if applying fails it puts the old state back.
"""
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from . import __version__, config, store

FORMAT = 1
DB = "commandcenter.db"
FILES = ("config.toml", "profile.json", "profile.previous.json", "profile_form.json", "Candidate_Profile.md",
         "qc_rules.json", "scoring.toml", "example_content.json")
DIRS = ("drafts", "resumes", "kits")
BACKUPS = "backups"
MANIFEST = "manifest.json"
README = "README.txt"
EXT = ".ccbackup"
MAX_FILE = 200 << 20       # one file inside a backup
MAX_TOTAL = 2 << 30        # everything unpacked
MAX_ENTRIES = 20000
NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,120}\.ccbackup$")
KEEP_BEFORE_RESTORE = 5

SECRETS_NOTICE = ("Passwords and API keys are NOT in this backup. Your email password, calendar link and AI key stay in "
                  "this computer's secure store. After a restore on another computer, open Setup and enter them again.")
FILE_NOTICE = ("This backup holds your personal data (profile, resumes, applications). Keep it somewhere private and "
               "do not share it. It is not encrypted.")


class BackupError(ValueError):
    pass


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _items(d):
    """(archive name, path) for every allow-listed file that exists, excluding the database."""
    d = Path(d)
    for name in FILES:
        if (d / name).is_file():
            yield name, d / name
    for sub in DIRS:
        base = config.kits_dir(d) if sub == "kits" else d / sub     # the kit folder may be one the user chose
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file() and not p.is_symlink() and not p.name.startswith(".cc-write-test"):
                    yield f"{sub}/{p.relative_to(base).as_posix()}", p


def _stamp(now=None):
    return (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")


def has_data(data_dir):
    d = Path(data_dir)
    for name, p in _items(d):
        if name == "config.toml":  # the untouched starter file is not anybody's data
            try:
                if p.read_text(encoding="utf-8") == config.STARTER:
                    continue
            except (OSError, UnicodeDecodeError):
                pass
        return True
    if (d / DB).exists():
        try:
            conn = store.connect(d / DB)
            try:
                return any(store.list_docs(conn, c) for c in store.COLLECTIONS)
            finally:
                conn.close()
        except (sqlite3.Error, OSError, ValueError):
            return True  # an unreadable database is still somebody's data
    return False


def create(data_dir, dest, now=None):
    """Write one backup file. Atomic: the destination appears only when complete. Returns {path, files, bytes}."""
    d, dest = Path(data_dir), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    files = {}
    try:
        with tempfile.TemporaryDirectory(dir=dest.parent) as work:
            snap = Path(work) / DB
            if (d / DB).exists():
                src = sqlite3.connect(f"file:{(d / DB).as_posix()}?mode=ro", uri=True)
                try:
                    out = sqlite3.connect(snap)
                    with out:
                        src.backup(out)
                    out.close()
                finally:
                    src.close()
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                entries = ([(DB, snap)] if snap.exists() else []) + list(_items(d))
                for name, p in entries:
                    if p.stat().st_size > MAX_FILE:
                        raise BackupError(f"{name} is larger than {MAX_FILE >> 20} MB, so it was not backed up")
                    files[name] = {"sha256": _sha(p), "size": p.stat().st_size}
                    z.write(p, name)
                manifest = {"format": FORMAT, "app_version": __version__,
                            "created": (now or dt.datetime.now()).isoformat(timespec="seconds"), "files": files,
                            "excluded": ["passwords and API keys (OS secure store)", "logs", "exports", "backups"]}
                z.writestr(MANIFEST, json.dumps(manifest, indent=1))
                z.writestr(README, "Command Center backup.\n\n" + SECRETS_NOTICE + "\n\n" + FILE_NOTICE + "\n\n"
                           "To restore: Backup & restore page in the dashboard, or  cc backup restore FILE\n")
        verify(tmp)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return {"path": str(dest), "files": len(files), "bytes": dest.stat().st_size}


def _safe_name(name):
    parts = name.split("/")
    if (not name or name.startswith(("/", "\\")) or "\\" in name or ":" in name or any(c < " " for c in name)
            or any(p in ("", ".", "..") for p in parts)):
        return False
    return name in (DB, MANIFEST, README) or name in FILES or parts[0] in DIRS and len(parts) > 1


def verify(path):
    """Raise BackupError unless the file is a complete, undamaged backup this version can restore. -> manifest."""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise BackupError(f"not a readable backup file ({str(e)[:80]})") from None
    with z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise BackupError("too many files inside")
        if sum(i.file_size for i in infos) > MAX_TOTAL or any(i.file_size > MAX_FILE for i in infos):
            raise BackupError("the backup is larger than the allowed size")
        for i in infos:
            if i.flag_bits & 0x1:
                raise BackupError("encrypted entries are not supported")
            if i.is_dir():
                continue
            if not _safe_name(i.filename) or ((i.external_attr >> 16) & 0o170000) == 0o120000:
                raise BackupError(f"unsafe or unexpected entry {i.filename[:60]!r}")
        try:
            manifest = json.loads(z.read(MANIFEST))
        except (KeyError, ValueError, RecursionError):
            raise BackupError("manifest.json is missing or damaged, so this is not a Command Center backup") from None
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
            raise BackupError("manifest.json is damaged")
        fmt = manifest.get("format")
        if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt < 1:
            raise BackupError("manifest.json has no valid format number")
        if fmt > FORMAT:
            raise BackupError(f"this backup was made by a newer version (format {fmt}); update Command Center first")
        have = {i.filename for i in infos if not i.is_dir()}
        for name, meta in manifest["files"].items():
            if name not in have or not _safe_name(name) or not isinstance(meta, dict):
                raise BackupError(f"{str(name)[:60]!r} is listed but missing or not allowed")
            h = hashlib.sha256()
            with z.open(name) as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != meta.get("sha256"):
                raise BackupError(f"{name} is damaged (checksum does not match)")
        extra = have - set(manifest["files"]) - {MANIFEST, README}
        if extra:
            raise BackupError(f"unlisted entry {sorted(extra)[0][:60]!r}")
    return manifest


def _check_db(path):
    try:
        c = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("the database inside the backup is damaged")
        finally:
            c.close()
    except sqlite3.Error:
        raise BackupError("the database inside the backup cannot be read") from None


def planned_safety_path(data_dir, now=None):
    """A name that does not exist yet, so a safety copy can never overwrite the file being restored."""
    base = Path(data_dir) / BACKUPS
    p = base / f"before-restore-{_stamp(now)}{EXT}"
    n = 1
    while p.exists():
        n += 1
        p = base / f"before-restore-{_stamp(now)}-{n}{EXT}"
    return p


def _apply(src_dir, data_dir):
    """Make the data folder match an unpacked backup (allow-listed names only)."""
    src, d = Path(src_dir), Path(data_dir)
    for name in FILES:
        if (src / name).is_file():
            shutil.copy2(src / name, d / name)
        else:
            (d / name).unlink(missing_ok=True)
    notes = []
    for sub in DIRS:
        old = d / sub
        if sub == "kits" and config.kits_dir(d) != old:
            # A folder the user chose may hold their other files: add the backup's kits, delete nothing.
            target = config.kits_dir(d)
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError:
                notes.append(f"Could not create {target}, so the kits went to {old}. Choose the kit folder again after restoring.")
                config.set_values(d / "config.toml", "paths", {"kits": ""})
                target = old
            if (src / sub).is_dir():
                shutil.copytree(src / sub, target, dirs_exist_ok=True)
            continue
        if old.is_dir():
            shutil.rmtree(old)
        if (src / sub).is_dir():
            shutil.copytree(src / sub, old)
    if (src / DB).is_file():
        # Through SQLite's backup API, so a running dashboard keeps a valid handle on the same file.
        s = sqlite3.connect(f"file:{(src / DB).as_posix()}?mode=ro", uri=True)
        try:
            t = sqlite3.connect(d / DB, timeout=30)
            try:
                s.backup(t)
            finally:
                t.close()
        finally:
            s.close()
    return notes


def _unpack(path, dest):
    with zipfile.ZipFile(path) as z:
        for i in z.infolist():
            if i.is_dir() or i.filename in (MANIFEST, README):
                continue
            target = Path(dest) / i.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as f, open(target, "wb") as o:
                shutil.copyfileobj(f, o)


def restore(path, data_dir, replace=False, now=None):
    """Verify, save the current state, then replace the data folder's contents with the backup's.
    -> {restored, safety_backup, notice}. Without replace=True a folder that already holds data is refused."""
    d = Path(data_dir)
    manifest = verify(path)
    d.mkdir(parents=True, exist_ok=True)
    safety = None
    if has_data(d):
        if not replace:
            raise BackupError("this data folder already has data. Restoring replaces it; the current data is saved first "
                              f"to {planned_safety_path(d, now).parent}. Confirm to continue (--replace).")
        safety = Path(create(d, planned_safety_path(d, now), now)["path"])
    with tempfile.TemporaryDirectory(dir=d) as work:
        _unpack(path, work)
        if (Path(work) / DB).exists():
            _check_db(Path(work) / DB)
        try:
            notes = _apply(work, d)
        except BaseException as e:
            if safety is not None:
                with tempfile.TemporaryDirectory(dir=d) as back:
                    _unpack(safety, back)
                    try:
                        _apply(back, d)
                    except Exception:
                        raise BackupError(f"restore failed ({e}) and the old state could not be put back. "
                                          f"Your previous data is in {safety}") from None
                raise BackupError(f"restore failed ({e}); your previous data was put back. A copy is in {safety}") from None
            raise
    if safety is not None:
        olds = sorted((d / BACKUPS).glob("before-restore-*" + EXT))
        for old in olds[:-KEEP_BEFORE_RESTORE]:
            old.unlink(missing_ok=True)
    return {"restored": len(manifest["files"]), "safety_backup": str(safety) if safety else None,
            "created": manifest.get("created"), "notice": " ".join([SECRETS_NOTICE] + notes)}


def prune(data_dir, keep=7, keep_manual=20):
    """Housekeeping: keep the newest `keep` automatic and `keep_manual` manual backups. before-restore files have their own limit."""
    base = Path(data_dir) / BACKUPS
    for prefix, n in (("auto-", keep), ("manual-", keep_manual)):
        olds = sorted(base.glob(prefix + "*" + EXT))
        for old in olds[:-n] if n else []:
            old.unlink(missing_ok=True)


def auto(data_dir, keep=7, extra_dir="", now=None, keep_manual=20):
    """One backup per day into <data>/backups (and a copy in extra_dir when set). -> notes (empty = nothing to say)."""
    d = Path(data_dir)
    day = (now or dt.datetime.now()).strftime("%Y-%m-%d")
    target = d / BACKUPS / f"auto-{day}{EXT}"
    notes = []
    if not has_data(d):
        return notes
    if not target.exists():
        create(d, target, now)
    prune(d, keep, keep_manual)
    if extra_dir:
        try:
            ex = Path(extra_dir)
            ex.mkdir(parents=True, exist_ok=True)
            copy = ex / target.name
            if not copy.exists():
                shutil.copyfile(target, copy)
            olds = sorted(ex.glob("auto-*" + EXT))
            for old in olds[:-keep] if keep else []:
                old.unlink(missing_ok=True)
        except OSError as e:
            notes.append(f"Could not copy the backup to {extra_dir}: {str(e)[:100]}")
    return notes


def _scan(base, where):
    out = []
    for p in sorted(base.glob("*" + EXT), reverse=True) if base.is_dir() else []:
        kind = "before-restore" if p.name.startswith("before-restore") else "auto" if p.name.startswith("auto") else "manual"
        out.append({"name": p.name, "kind": kind, "where": where, "bytes": p.stat().st_size,
                    "modified": dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")})
    return out


def listing(data_dir, extra_dir=""):
    """Backups in <data>/backups (where = "data"), then the copies in extra_dir (where = "copy"), newest first in each."""
    out = _scan(Path(data_dir) / BACKUPS, "data")
    if extra_dir:
        try:
            out += _scan(Path(extra_dir), "copy")
        except OSError:
            pass
    return out


def folders(data_dir, extra_dir="", kits_dir=None):
    """Where things live, for the Backup page: every path is absolute text."""
    out = {"backups": str(Path(data_dir) / BACKUPS), "extra": str(extra_dir) if extra_dir else ""}
    if kits_dir:
        out["kits"] = str(kits_dir)
    return out


def local_path(data_dir, name, extra_dir="", where="data"):
    """A file in <data>/backups (or the extra copy folder when where="copy") by name, or BackupError.
    Nothing outside those two folders can be named."""
    if not isinstance(name, str) or not NAME_OK.fullmatch(name):
        raise BackupError("bad backup name")
    if where == "copy" and not extra_dir:
        raise BackupError("no copy folder is set")
    base = Path(extra_dir) if where == "copy" else Path(data_dir) / BACKUPS
    p = base / name
    if not p.is_file():
        raise BackupError("no such backup")
    return p


def reentry_hints(cfg):
    """What the user must enter again after a restore, from the restored settings."""
    out = []
    if cfg.mail.host and cfg.mail.host != "imap.example.com":
        out.append("email password")
    if cfg.calendar.ics_configured:
        out.append("calendar link")
    if cfg.ai.runner == "api":
        out.append("AI key")
    return out
