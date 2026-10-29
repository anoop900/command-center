"""What the browser extension reads from the running dashboard, and the one thing it may write.

Everything here is read-only except mark_applied, which the extension calls only when the person clicks
"Mark applied". Role rows and kit folders are data; names are checked before any file is opened.
"""
import datetime as dt

from . import api, config, store
from .kits import content
from .rules import job_key
from .runs import actions

MAX_ROLES = 200


def _kit_dir(kits, rel):
    """The kit folder inside the kits folder, or None. `rel` comes from a stored role, so it is checked anyway."""
    try:
        root = kits.resolve()
        f = (root / rel).resolve()
        return f if rel and root in f.parents and f.is_dir() else None
    except (ValueError, OSError):
        return None


def roles(conn, kits):
    """Open roles that have a kit with a resume PDF, newest kit first."""
    out = []
    for d in store.list_docs(conn, "roles"):
        r = d["data"]
        folder = _kit_dir(kits, str(r.get("kit") or ""))
        pdf = content.find_resume(folder, "pdf") if folder else None
        if not pdf or not api._is_open(r):
            continue
        company = str(r.get("company") or "")
        out.append({"key": d["id"], "company": company, "title": str(r.get("title") or ""),
                    "link": str(r.get("link") or ""), "folder": str(r["kit"]), "resume": pdf.name,
                    "job_id": job_key.url_id(r.get("link")).split(":", 1)[-1],
                    "aliases": [a for a in {job_key.company_norm(company)} if a and a != company.lower()],
                    "applied": str(r.get("status", "")).lower().startswith("applied") or bool(r.get("date_applied"))})
    out.sort(key=lambda x: x["folder"], reverse=True)       # folders start with the date
    return out[:MAX_ROLES]


def resume_file(conn, kits, key):
    """The resume PDF of this role's kit, or None."""
    d = store.get(conn, "roles", key) if isinstance(key, str) and 0 < len(key) <= 200 else None
    folder = _kit_dir(kits, str(d["data"].get("kit") or "")) if d else None
    return content.find_resume(folder, "pdf") if folder else None


def mark_applied(conn, key, today=None):
    """Same effect as the Mark applied button: queue the action and let it settle. Repeating it changes nothing."""
    today = today or dt.date.today()
    d = store.get(conn, "roles", key) if isinstance(key, str) and 0 < len(key) <= 200 else None
    if d is None:
        return None
    if str(d["data"].get("status", "")).lower().startswith("applied"):
        return {"key": key, "already": True}
    store.set_doc(conn, "actions", f"ext-applied-{key}"[:120],
                  {"action": "applied", "status": "applied", "key": d["data"].get("key") or key, "at": today.isoformat()},
                  by="extension")
    actions.settle(conn, today)
    return {"key": key, "already": False}
