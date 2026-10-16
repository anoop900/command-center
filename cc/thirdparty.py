"""Import from other job-search tools: CSV/XLSX trackers -> roles, JSON Resume -> profile draft.

Every input is untrusted. preview() reads and writes nothing; apply() re-reads the same file (no server-side state to
trust), validates every row first, then writes. Re-applying a file adds nothing (job_key de-duplication).
Fixtures are synthetic: no real Huntr/Teal/Simplify export was available, so there are no named presets, only
generic header matching.
"""
import csv
import datetime as dt
import io
import json
import re
import zipfile
from pathlib import Path

from . import drafts, profile, store
from .rules import job_key

MAX_BYTES = 5 << 20
MAX_UNPACKED = 20 << 20
MAX_ZIP_ENTRIES = 200
MAX_ROWS = 5000
SHOW = 50
CELL = {"company": 200, "title": 200, "status": 80, "location": 200, "link": 500, "applied": 40, "notes": 1000}

HEADERS = {
    "company": ("company", "company name", "employer", "organization", "organisation"),
    "title": ("title", "job title", "position", "role", "job", "position title"),
    "status": ("status", "stage", "application status"),
    "link": ("url", "link", "job url", "posting url", "job link", "application url"),
    "applied": ("applied date", "date applied", "applied", "applied on", "application date"),
    "notes": ("notes", "note", "comments", "comment"),
    "location": ("location", "city", "job location"),
}
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y")


class ImportError_(ValueError):
    """A file that cannot be imported at all; the message is for the user."""


def _read(path):
    p = Path(path)
    if not p.is_file():
        raise ImportError_("that file was not found")
    if p.stat().st_size > MAX_BYTES:
        raise ImportError_(f"that file is larger than {MAX_BYTES >> 20} MB; export a smaller one")
    return p.read_bytes()


def _decode(raw):
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", "replace")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    if b"\x00" in raw:
        raise ImportError_("that file looks binary, not a text export")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def _cell(v, field):
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        v = v.date().isoformat() if isinstance(v, dt.datetime) else v.isoformat()
    elif not isinstance(v, str):
        v = str(v)
    return _CTRL.sub(" ", v).strip()[:CELL[field]]


def _date(s):
    for f in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s[:40], f).date().isoformat()
        except ValueError:
            continue
    return ""


def _csv_rows(text):
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    csv.field_size_limit(1 << 16)
    try:
        return [r for r in csv.reader(io.StringIO(text, newline=""), dialect)][:MAX_ROWS + 2]
    except csv.Error as e:
        raise ImportError_(f"that CSV could not be read ({str(e)[:80]})")


def _xlsx_rows(path):
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNPACKED:
                raise ImportError_("that spreadsheet is too large when unpacked")
    except zipfile.BadZipFile:
        raise ImportError_("that is not a valid .xlsx file")
    try:
        import openpyxl
    except ImportError:
        raise ImportError_("reading .xlsx needs openpyxl: pip install openpyxl (or save the sheet as CSV)")
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0] if wb.worksheets else None
        rows = [list(r) for _, r in zip(range(MAX_ROWS + 2), ws.iter_rows(values_only=True))] if ws else []
        wb.close()
        return rows
    except Exception as e:  # openpyxl raises many types on damaged files
        raise ImportError_(f"that spreadsheet could not be read ({type(e).__name__})")


def _map_headers(header):
    names = [_cell(h, "title").lower() for h in header]
    mapped = {}
    for field, aliases in HEADERS.items():
        for a in aliases:
            if a in names and field not in mapped:
                mapped[field] = names.index(a)
                break
    used = set(mapped.values())
    return mapped, [header[i] for i in range(len(header)) if i not in used and names[i]]


def _tracker(rows):
    rows = [r for r in rows if any(str(c or "").strip() for c in r)]
    if not rows:
        raise ImportError_("that file has no rows")
    mapped, unmapped = _map_headers(rows[0])
    if "company" not in mapped or "title" not in mapped:
        raise ImportError_("could not find a Company column and a Job title/Position column in the first row")
    if len(rows) - 1 > MAX_ROWS:
        raise ImportError_(f"more than {MAX_ROWS} rows; split the file")
    out, skipped = [], []
    for n, r in enumerate(rows[1:], start=2):
        rec = {f: _cell(r[i], f) if i < len(r) else "" for f, i in mapped.items()}
        if not (rec.get("company") and rec.get("title")):
            skipped.append({"row": n, "reason": "no company or no job title"})
            continue
        if rec.get("link") and not re.match(r"https?://", rec["link"], re.I):
            rec["link"] = ""
        rec["applied"] = _date(rec.get("applied", ""))
        out.append(rec)
    return out, skipped, {f: str(rows[0][i])[:60] for f, i in mapped.items()}, [str(u)[:60] for u in unmapped[:20]]


def _s(v, n=200):
    return _CTRL.sub(" ", v).strip()[:n] if isinstance(v, str) else ""


def _resume_draft(doc, existing):
    """JSON Resume -> a profile form state with every fact unconfirmed (ok=False)."""
    if not isinstance(doc, dict):
        raise ImportError_("that JSON is not a JSON Resume (expected an object)")
    b = doc.get("basics") if isinstance(doc.get("basics"), dict) else {}
    loc = b.get("location") if isinstance(b.get("location"), dict) else {}
    fields = {}
    name = _s(b.get("name")).split()
    if name:
        fields["firstName"] = name[0]
        if len(name) > 1:
            fields["lastName"] = " ".join(name[1:])
    for key, val in (("email", b.get("email")), ("phone", b.get("phone")), ("website", b.get("url")),
                     ("city", loc.get("city")), ("province", loc.get("region")), ("postalCode", loc.get("postalCode")),
                     ("country", loc.get("countryCode")), ("address1", loc.get("address"))):
        if _s(val):
            fields[key] = _s(val)
    for pr in (b.get("profiles") if isinstance(b.get("profiles"), list) else [])[:20]:
        if isinstance(pr, dict) and _s(pr.get("network")).lower() == "linkedin" and _s(pr.get("url"), 500):
            fields["linkedin"] = _s(pr.get("url"), 500)
    jobs = []
    for w in (doc.get("work") if isinstance(doc.get("work"), list) else [])[:profile.MAX_JOBS]:
        if not isinstance(w, dict):
            continue
        emp, title = _s(w.get("name"), 120), _s(w.get("position"), 120)
        if not (emp and title):
            continue
        dates = " to ".join(x for x in (_s(w.get("startDate"), 20), _s(w.get("endDate"), 20) or "present") if x)
        hl = w.get("highlights") if isinstance(w.get("highlights"), list) else []
        jobs.append({"employer": emp, "title": title, "dates": dates[:120], "ok": False,
                     "facts": [f for f in (_s(h, 400) for h in hl[:profile.MAX_FACTS]) if f]})
    kw = []
    for s in (doc.get("skills") if isinstance(doc.get("skills"), list) else [])[:50]:
        if isinstance(s, dict):
            kw += [_s(k, 60) for k in (s.get("keywords") if isinstance(s.get("keywords"), list) else [])[:50]]
            if _s(s.get("name"), 60) and not s.get("keywords"):
                kw.append(_s(s.get("name"), 60))
    # Merge under what the user already typed: a draft field or job list that has content is never replaced.
    base = existing or profile.empty()
    state = {"fields": dict(base["fields"]), "jobs": list(base["jobs"]), "skills": dict(base["skills"])}
    added = []
    for k, v in fields.items():
        if not (state["fields"].get(k) or {}).get("value"):
            state["fields"][k] = {"value": v, "ok": False}
            added.append(k)
    if jobs and not state["jobs"]:
        state["jobs"] = jobs
        added.append(f"{len(jobs)} job(s)")
    skills = ", ".join(dict.fromkeys(k for k in kw if k))[:600]
    if skills and not state["skills"].get("value"):
        state["skills"] = {"value": skills, "ok": False}
        added.append("skills")
    return profile.validate(state), added


def _load(path):
    """-> (kind, parsed) where kind is 'tracker' or 'resume'."""
    ext = Path(path).suffix.lower()
    if ext == ".xlsx":
        _read(path)
        return "tracker", _tracker(_xlsx_rows(path))
    raw = _read(path)
    text = _decode(raw)
    if ext == ".json" or text.lstrip()[:1] in ("{", "["):
        try:
            return "resume", json.loads(text)
        except (ValueError, RecursionError):
            raise ImportError_("that JSON could not be read (damaged or nested too deeply)")
    if ext in (".csv", ".tsv", ".txt", ""):
        return "tracker", _tracker(_csv_rows(text))
    raise ImportError_("use a .csv, .xlsx or JSON Resume .json file")


def _existing(conn):
    return [(d["data"].get("key") or d["id"], d["data"].get("company", ""), d["data"].get("title", ""),
             d["data"].get("link", "")) for d in store.list_docs(conn, "roles")]


def _plan(conn, data_dir, path):
    kind, parsed = _load(path)
    if kind == "resume":
        state, added = _resume_draft(parsed, drafts.read(data_dir, "profile"))
        return {"kind": "resume", "state": state, "added": added}
    recs, skipped, mapped, unmapped = parsed
    existing = _existing(conn)
    new, dups = [], 0
    for r in recs:
        if job_key.find_duplicate(r["company"], r["title"], r.get("link", ""), existing):
            dups += 1
            continue
        k = job_key.key(r["company"], r["title"])
        existing.append((k, r["company"], r["title"], r.get("link", "")))
        new.append((k, r))
    return {"kind": "tracker", "new": new, "dups": dups, "skipped": skipped, "mapped": mapped, "unmapped": unmapped}


def preview(conn, data_dir, path):
    """What apply() would do. Writes nothing."""
    p = _plan(conn, data_dir, path)
    if p["kind"] == "resume":
        return {"kind": "resume", "draft_adds": p["added"], "roles": 0,
                "notes": ["Facts go into your profile DRAFT unconfirmed; you tick each one before it counts.",
                          "Anything you already typed in the draft is kept."]}
    return {"kind": "tracker", "roles": len(p["new"]), "duplicates": p["dups"], "skipped_count": len(p["skipped"]),
            "skipped": p["skipped"][:SHOW], "mapped": p["mapped"], "unmapped_headers": p["unmapped"],
            "sample": [{"company": r["company"], "title": r["title"], "status": r.get("status", ""),
                        "applied": r.get("applied", "")} for _, r in p["new"][:SHOW]],
            "notes": ["Imported roles are stored as 'imported' and are not scored or shown as new until you review them.",
                      "Salary is not imported."]}


def apply(conn, data_dir, path, by="import-other", today=None):
    p = _plan(conn, data_dir, path)
    if p["kind"] == "resume":
        drafts.write(data_dir, "profile", p["state"])
        return {"kind": "resume", "draft_adds": p["added"], "roles": 0}
    today = (today or dt.date.today()).isoformat()
    for k, r in p["new"]:
        data = {"key": k, "date_found": today, "company": r["company"], "title": r["title"],
                "location": r.get("location", ""), "link": r.get("link", ""), "source": "import",
                "channel": "import-other", "status": "imported", "decision": "", "pay": "", "fit_pct": "",
                "label": "", "source_status": r.get("status", ""), "applied_date": r.get("applied", ""),
                "reason_or_gaps": r.get("notes", "")}
        store.set_doc(conn, "roles", job_key.doc_id(k), data, by=by)
    return {"kind": "tracker", "roles": len(p["new"]), "duplicates": p["dups"], "skipped_count": len(p["skipped"])}
