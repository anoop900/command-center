#!/usr/bin/env python3
"""Role tracker. Master record = the dashboard database collection `roles` (one doc per role).
PATH may be a folder of role docs (ArtifactData list out_dir .../roles) or, for legacy use, an .xlsx file.
Folder mode never touches the database itself: changed or new docs are written to PATH/_out/ and listed in
PATH/_writes.json for Claude to send as ONE ArtifactData batch (if_version from the list result; omit for new docs).
Extra commands:
  --to-db  Applications_Tracker.xlsx OUTDIR    one-time migration: xlsx rows -> OUTDIR/<doc_id>.json + _writes.json
  --export ROLESDIR Applications_Tracker.xlsx  rebuild the read-only Excel copy from the database docs
Existing rows are never overwritten, except by an explicit --update.
"""
import json, os, re, sys, datetime as dt
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

COLS = ["date_found", "company", "title", "location", "work_mode", "pay", "fit_pct", "decision", "status",
        "date_applied", "follow_up", "reason_or_gaps", "resume_file", "cover_letter", "source", "link", "channel"]
HEADS = ["Date found", "Company", "Role", "Location", "Mode", "Pay", "Fit %", "Decision", "Status",
         "Date applied", "Follow-up", "Gaps / reason", "Resume file", "Cover letter", "Source", "Link", "Channel"]
WIDTHS = [11, 22, 38, 16, 11, 16, 7, 12, 22, 12, 11, 40, 44, 8, 12, 8, 11]
HEAD_TO_COL = dict(zip(HEADS, COLS))


def _d(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


from .job_key import key, doc_id  # one home for role identity


ORIG = {}


def read(path):
    if os.path.isdir(path):
        def load(fp):
            r = json.load(open(fp, encoding="utf-8"))
            return {k: ("" if v is None else str(v)) for k, v in r.items() if k in COLS}
        rows = {}
        for f in sorted(os.listdir(path)):
            if f.endswith(".json") and not f.startswith("_"):
                r = load(os.path.join(path, f))
                r["_id"] = f[:-5]
                ORIG[r["_id"]] = dict(r)
                rows[r["_id"]] = r
        # Pending changes from earlier calls in the same run live in _out/: start from them, so every call's
        # _writes.json holds ALL changes since the list (not just its own) and one batch commits them all.
        pend = os.path.join(path, "_out")
        if os.path.isdir(pend):
            for f in sorted(os.listdir(pend)):
                if f.endswith(".json"):
                    r = load(os.path.join(pend, f))
                    r["_id"] = f[:-5]
                    rows[r["_id"]] = r
        return [], list(rows.values())
    ws = load_workbook(path).active
    heads = [c.value for c in ws[1]]
    extra = [h for h in heads if h and h not in HEAD_TO_COL]
    rows = []
    for row in ws.iter_rows(min_row=2):
        r = {}
        for h, c in zip(heads, row):
            if not h:
                continue
            name = HEAD_TO_COL.get(h, h)
            v = c.value
            if name == "link" and c.hyperlink is not None and c.hyperlink.target:
                v = c.hyperlink.target
            r[name] = "" if v is None else (v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v))
        if any(r.values()):
            rows.append(r)
    return extra, rows


def write(path, extra, rows):
    if os.path.isdir(path):
        out = os.path.join(path, "_out"); os.makedirs(out, exist_ok=True)
        writes = []
        for r in rows:
            i = r.get("_id") or doc_id(key(r.get("company", ""), r.get("title", "")))
            doc = {c: r.get(c, "") for c in COLS}
            doc["key"] = key(r.get("company", ""), r.get("title", ""))
            old = ORIG.get(i)
            if old is not None and all(old.get(c, "") == doc[c] for c in COLS):
                continue
            fp = os.path.abspath(os.path.join(out, i + ".json"))
            json.dump(doc, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
            writes.append({"op": "set", "collection": "roles", "doc_id": i, "file_path": fp, "new": old is None})
        json.dump(writes, open(os.path.join(path, "_writes.json"), "w"), indent=0)
        print(f"WRITES {len(writes)} ({sum(w['new'] for w in writes)} new) -> {os.path.join(path, '_writes.json')}")
        return
    wb = Workbook(); ws = wb.active; ws.title = "Applications"
    ws.append(HEADS + extra)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in sorted(rows, key=lambda r: r.get("date_found", ""), reverse=True):
        ws.append([r.get(k, "") for k in COLS] + [r.get(h, "") for h in extra])
        for c in ws[ws.max_row]:
            if isinstance(c.value, str) and c.value[:1] == "=":
                c.data_type = "s"  # posting text is untrusted: never let it become a live formula
        link = r.get("link", "")
        if link.startswith("http"):
            cell = ws.cell(row=ws.max_row, column=COLS.index("link") + 1)
            cell.hyperlink = link; cell.value = "open"; cell.font = Font(color="0563C1", underline="single")
    for i, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "C2"; ws.auto_filter.ref = ws.dimensions
    about = wb.create_sheet("About")
    about["A1"] = "Read-only copy. The master record is the dashboard database; this file is rebuilt after every run."
    about["A2"] = "Edits made here are overwritten. Change roles from the dashboard or ask Claude."
    about["A3"] = "Exported " + dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    wb.save(path)


def main():
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "--to-db":
        out = sys.argv[3]; os.makedirs(out, exist_ok=True)
        _, rows = read(path); writes = []; seen = set()
        for r in rows:
            k = key(r.get("company", ""), r.get("title", "")); i = doc_id(k)
            if i in seen:
                print("DUPLICATE key skipped:", k); continue
            seen.add(i)
            doc = {c: r.get(c, "") for c in COLS}; doc["key"] = k
            fp = os.path.abspath(os.path.join(out, i + ".json"))
            json.dump(doc, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
            writes.append({"op": "set", "collection": "roles", "doc_id": i, "file_path": fp})
        json.dump(writes, open(os.path.join(out, "_writes.json"), "w"), indent=0)
        print(f"{len(writes)} docs -> {out}"); sys.exit(0)
    if cmd == "--export":
        _, rows = read(path)
        for r in rows: r.pop("_id", None)
        write(sys.argv[3], [], rows); print(f"exported {len(rows)} rows -> {sys.argv[3]}"); sys.exit(0)
    extra, rows = read(path)
    keys = {key(r.get("company", ""), r.get("title", "")) for r in rows}

    if cmd == "--is-dup":
        k = key(sys.argv[3], sys.argv[4])
        print("DUP" if k in keys else "NEW", k)
    elif cmd == "--show":
        print(json.dumps(rows, indent=1))
    elif cmd == "--add":
        added = skipped = 0
        for n in json.load(open(sys.argv[3], encoding="utf-8")):
            k = key(n.get("company", ""), n.get("title", ""))
            if k in keys:
                skipped += 1; continue
            rows.append({c: str(n.get(c, "") or "") for c in COLS}); keys.add(k); added += 1
        write(path, extra, rows); print(f"added {added}, skipped {skipped} duplicates, total {len(rows)}")
    elif cmd == "--update":
        k = key(sys.argv[3], sys.argv[4])
        hits = [r for r in rows if key(r.get("company", ""), r.get("title", "")) == k]
        if len(hits) != 1:
            sys.exit(f"expected 1 matching row, found {len(hits)}")
        for kv in sys.argv[5:]:
            f, v = kv.split("=", 1)
            if f not in COLS and f not in extra:
                sys.exit("unknown field " + f)
            hits[0][f] = v
        r = hits[0]
        if "applied" in r.get("status", "").lower() and "not" not in r.get("status", "").lower() and not r.get("follow_up"):
            base = _d(r.get("date_applied")) or dt.date.today()
            r["follow_up"] = (base + dt.timedelta(days=7)).isoformat()
        write(path, extra, rows); print("updated", k, "follow_up", r.get("follow_up", ""))
    elif cmd == "--reminders":
        today = _d(sys.argv[3]) or dt.date.today()
        monday = today.weekday() == 0
        out = []
        for r in rows:
            st = r.get("status", "").lower(); who = f'{r.get("company","")}, {r.get("title","")}'
            if not st or "not " in st or "rejected" in st or "closed" in st:
                continue
            fu = _d(r.get("follow_up")); ap = _d(r.get("date_applied")); found = _d(r.get("date_found"))
            if "interview" in st:
                if (fu and fu == today) or (fu is None and monday):
                    out.append({"p": 1, "type": "interview check-in", "role": who, "note": r.get("status")})
            elif "applied" in st:
                due = fu or ((ap + dt.timedelta(days=7)) if ap else None)
                if due and due <= today and (today - due).days % 7 == 0:
                    days = (today - ap).days if ap else None
                    kind = "no reply 3+ weeks: ask for status or close" if days and days >= 21 else "follow up"
                    out.append({"p": 2, "type": kind, "role": who, "note": f"applied {days} days ago" if days is not None else "applied"})
            elif st == "ready to apply" and found and (today - found).days in (3, 7):
                out.append({"p": 3, "type": "packet ready, not applied", "role": who, "note": f"built {(today - found).days} days ago"})
        out.sort(key=lambda x: x["p"])
        print(json.dumps(out, indent=1))
    elif cmd == "--insights":
        today = _d(sys.argv[3]) or dt.date.today()
        def lvl(t):
            t = t.lower()
            if re.search(r"\b(vp|vice president|head of|chief)\b", t): return "VP / Head"
            if "director" in t: return "Director"
            if re.search(r"manager|\bem\b|lead", t): return "Manager"
            return "IC / Architect"
        def band(f):
            m = re.search(r"\d+", str(f)); n = int(m.group()) if m else None
            return "unscored" if n is None else "70%+" if n >= 70 else "60-69%" if n >= 60 else "<60%"
        apps = []
        for r in rows:
            st = r.get("status", "").lower()
            if not st or "not " in st or st.startswith("ready"): continue
            if not any(k in st for k in ("applied", "interview", "rejected", "closed", "offer")): continue
            ap = _d(r.get("date_applied")) or _d(r.get("date_found"))
            if r.get("date_found", "")[:7] and not ap:
                ap = _d(r["date_found"][:7] + "-15")
            g = (r.get("reason_or_gaps", "") + " " + r.get("title", "")).lower()
            apps.append({"age": (today - ap).days if ap else None,
                         "resp": any(k in st for k in ("interview", "rejected", "closed", "offer")),
                         "intv": "interview" in st or "offer" in st,
                         "channel": (r.get("channel") or "cold").lower(), "level": lvl(r.get("title", "")),
                         "fit": band(r.get("fit_pct")), "degree": "bachelor/degree asked" if re.search(r"degree|bachelor", g) else "no degree flag"})
        mature = [a for a in apps if a["age"] is not None and a["age"] >= 21]
        def cut(key):
            out = {}
            for a in mature:
                o = out.setdefault(a[key], {"sent": 0, "responses": 0, "interviews": 0}); o["sent"] += 1
                o["responses"] += a["resp"]; o["interviews"] += a["intv"]
            return out
        res = {"as_of": today.isoformat(), "total_tracked": len(apps), "too_new_under_21_days": len(apps) - len(mature),
               "mature": len(mature), "mature_responses": sum(a["resp"] for a in mature),
               "mature_interviews": sum(a["intv"] for a in mature),
               "by_channel": cut("channel"), "by_fit": cut("fit"), "by_level": cut("level"), "by_degree": cut("degree"),
               "note": "Rates use only applications 21+ days old; newer ones have not had time to get a reply."}
        print(json.dumps(res, indent=1))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
