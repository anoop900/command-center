"""Where kits live: check a folder the user chose, move the existing kits into it, and name new kit folders.

Folders inside the kits folder are stored on roles and in kitindex as paths relative to it, so moving the whole tree
keeps every dashboard link working without touching the database."""
import hashlib
import os
import re
import shutil
from pathlib import Path

from . import config, store


class KitFolderError(ValueError):
    """A short, safe message for the user."""


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(path):
    """Create the folder if needed and prove it is writable. Returns the resolved Path or raises KitFolderError
    with what the user can do about it."""
    raw = str(path or "").strip()
    if not raw or len(raw) > 500 or any(ord(c) < 32 for c in raw):
        raise KitFolderError("Type the full path of a folder, for example D:/Documents/Resumes.")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise KitFolderError("Use the full path, starting with the drive letter (for example D:/Documents/Resumes).")
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / f".cc-write-test-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise KitFolderError(f"Command Center cannot write to {p} ({e.strerror or type(e).__name__}). Create the folder "
                             "yourself, give your Windows user permission to change it (folder Properties, Security), "
                             "then try again.") from None
    return p.resolve()


def _files(root):
    return [p for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()]


def relocate(old, new):
    """Move every file under old into new (same relative paths). Copies and verifies first, then removes the originals,
    so a failure leaves the old folder whole. Existing different files in new are never overwritten.
    Returns {"moved": n, "left": n} (left = originals that could not be removed, still safe in the old folder)."""
    old, new = Path(old).resolve(), Path(new).resolve()
    if old == new or not old.is_dir():
        return {"moved": 0, "left": 0}
    if new in old.parents or old in new.parents:
        raise KitFolderError("Choose a folder that is not inside the current kit folder, and not a parent of it.")
    files = _files(old)
    clash = [p.relative_to(old).as_posix() for p in files if (new / p.relative_to(old)).exists()]
    if clash:
        raise KitFolderError(f"{len(clash)} file(s) with the same name already exist in the new folder, for example "
                             f"{clash[0]}. Nothing was moved. Choose an empty folder or rename those files.")
    made = []
    try:
        for p in files:
            dest = new / p.relative_to(old)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            made.append(dest)
            if dest.stat().st_size != p.stat().st_size or _sha(dest) != _sha(p):
                raise OSError("copy did not match the original")
    except OSError as e:
        for d in made:
            d.unlink(missing_ok=True)
        raise KitFolderError(f"Could not copy the kits ({e.strerror or e}). Nothing was moved.") from None
    left = 0
    for p in files:
        try:
            p.unlink()
        except OSError:
            left += 1
    for d in sorted((x for x in old.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
        try:
            d.rmdir()
        except OSError:
            pass
    return {"moved": len(files) - left, "left": left}


def current(home):
    home = Path(home)
    default = home / "kits"
    now = config.kits_dir(home)
    return {"path": str(now), "default": str(default), "custom": now != default, "exists": now.is_dir(),
            "files": len(_files(now)) if now.is_dir() else 0}


def change(home, cfg_file, path):
    """Switch the kit folder ('' = back to the default) and move the kits. Refuses while a kit build is running."""
    home = Path(home)
    default = home / "kits"
    old = config.kits_dir(home)
    new = default if not str(path or "").strip() else check(path)
    if str(path or "").strip() and new == default.resolve():
        new = default
    conn = store.connect(home / "commandcenter.db")
    try:
        if any(d["data"].get("state") == "running" for d in store.list_docs(conn, "jobs")):
            raise KitFolderError("A kit is being built right now. Try again when it finishes.")
    finally:
        conn.close()
    if new == default:
        default.mkdir(parents=True, exist_ok=True)
    out = relocate(old, new)
    config.set_values(cfg_file, "paths", {"kits": "" if new == default else Path(new).as_posix()})
    return dict(out, path=str(new))


def folder_for(role, kits, today):
    """Relative folder for a new kit: <date>/<Company>_<Title>, unique inside the kits folder."""
    def part(s, n):
        return re.sub(r"[^A-Za-z0-9]+", "-", str(s or "")).strip("-")[:n] or "role"
    base = f"{part(role.get('company'), 30)}_{part(role.get('title'), 45)}"
    day = str(today)[:10]
    rel, n = f"{day}/{base}", 2
    while (kits / rel).exists():
        rel, n = f"{day}/{base}-{n}", n + 1
    return rel
