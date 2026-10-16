#!/usr/bin/env python3
"""Build the dashboard's database documents from the tracker plus the run's notes.

Usage: python3 sync_db.py Applications_Tracker.xlsx run.json OUT_DIR YYYY-MM-DD
Writes OUT_DIR/roles.json (-> doc board/roles) and OUT_DIR/today.json (-> doc board/today).
Then: ArtifactData set board/roles file_path=OUT_DIR/roles.json (if_version from a get), same for board/today.

run.json (written by Claude during the run; every key optional):
  run: "morning" | "midday" | "build"
  summary: one line for the header
  notes: ["problem 1", ...]                       -> red banner
  inbox: [{from, subject, why, link}]              -> "Inbox needs you" (non-job triage, max 8)
  agenda: [{title, when, detail, type, prep, cal}] -> This week (interviews, calls)
  loops: [{id, title, due, detail}]                -> Open loops
  todos: [{id, type, title, detail, priority, key, link, prep, cal, tag, tagClass}]  -> extra to-dos
  roles: {"<tracker key>": {why, warm, posted, kit}} -> extra fields per role
  week_events: [{date, replies, interviews, touches}] -> replies/interviews/touches per day this week
  minutes: number (optional estimate of the user's time per day)
cal objects: {title, start: "2026-10-08T11:00", end: "...", body}
"""
import datetime as dt, json, os, re, subprocess, sys

trk, runp, out = sys.argv[1:4]
today = dt.date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else dt.date.today()
here = os.path.dirname(os.path.abspath(__file__))
mt = ["-m", "cc.rules.make_tracker"]
rows = json.loads(subprocess.run([sys.executable, *mt, "--show", trk], capture_output=True, text=True, check=True).stdout)
rem = json.loads(subprocess.run([sys.executable, *mt, "--reminders", trk, today.isoformat()], capture_output=True, text=True, check=True).stdout)
run = json.load(open(runp, encoding="utf-8")) if runp != "-" and os.path.exists(runp) else {}
os.makedirs(out, exist_ok=True)


def d(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


from cc.rules.job_key import key  # one home for role identity


def stage(r):
    s = r.get("status", "").lower(); dec = r.get("decision", "").lower()
    if "offer" in s: return "interviewing"
    if "interview" in s: return "interviewing"
    if any(k in s for k in ("rejected", "closed", "withdrew", "declined", "no response", "not applying", "not applied", "deadline passed")): return "closed"
    if "skip" in s or dec == "skip": return "skipped"
    if "applied" in s: return "applied"
    if s.startswith("ready"): return "ready"
    if "build on request" in s or dec == "queued": return "new"
    return None


extra = run.get("roles", {})
roles = []
for r in rows:
    st = stage(r)
    if not st:
        continue
    found = d(r.get("date_found"))
    if st in ("new", "skipped") and found and (today - found).days > 21:
        continue  # stale, keep the board short
    k = key(r.get("company", ""), r.get("title", ""))
    rf = r.get("resume_file", "")
    kit = rf.replace("/", "\\").rsplit("\\", 1)[0] if rf.lower().endswith(".pdf") and "\\" in rf.replace("/", "\\") else ""
    x = {"key": k, "company": r.get("company", ""), "title": r.get("title", ""), "status": r.get("status", ""), "stage": st,
         "fit": r.get("fit_pct", ""), "pay": r.get("pay", ""), "location": r.get("location", ""), "mode": r.get("work_mode", ""),
         "link": r.get("link", "") if str(r.get("link", "")).startswith("http") else "", "gaps": r.get("reason_or_gaps", ""),
         "found": r.get("date_found", ""), "applied": r.get("date_applied", ""), "follow_up": r.get("follow_up", ""),
         "source": r.get("source", ""), "channel": r.get("channel", ""), "kit": kit, "why": "", "warm": "", "posted": ""}
    x.update({kk: vv for kk, vv in extra.get(k, {}).items() if kk in ("why", "warm", "posted", "kit")})
    roles.append(x)
byk = {r["key"]: r for r in roles}

# ---- to-dos ------------------------------------------------------------------
todos = []
for r in sorted([r for r in roles if r["stage"] == "ready" and d(r["found"]) and (today - d(r["found"])).days <= 10], key=lambda r: -(int(re.search(r"\d+", r["fit"] or "0").group()) if re.search(r"\d+", r["fit"] or "") else 0)):
    hot = (re.search(r"\d+", r["fit"] or "") and int(re.search(r"\d+", r["fit"]).group()) >= 80)
    todos.append({"id": "apply-" + re.sub(r"[^a-z0-9]+", "-", r["key"])[:80], "type": "apply", "key": r["key"], "priority": 1 if hot else 2,
                  "title": f"Apply: {r['company']}, {r['title']}", "tag": ("80%+ " if hot else "") + "kit ready", "tagClass": "hot" if hot else "ready",
                  "detail": " · ".join(filter(None, [r["fit"] and "Fit " + r["fit"], r["pay"], r["warm"] and "Warm: " + r["warm"], r["kit"] and "Kit: " + r["kit"]]))})
for x in rem:
    comp, _, title = x["role"].partition(", ")
    k = key(comp, title)
    if x["type"].startswith(("follow", "no reply")):
        todos.append({"id": "fu-" + re.sub(r"[^a-z0-9]+", "-", k)[:80] + "-" + today.isoformat(), "type": "followup", "key": k if k in byk else "",
                      "priority": 2, "title": f"Follow up: {x['role']}", "detail": f"{x['type']} ({x['note']})", "tag": "follow-up"})
    elif x["type"].startswith("interview"):
        todos.append({"id": "iv-" + re.sub(r"[^a-z0-9]+", "-", k)[:80] + "-" + today.isoformat(), "type": "interview", "key": "", "priority": 1,
                      "title": f"Interview check-in: {x['role']}", "detail": x.get("note", ""), "tag": "interview", "tagClass": "hot"})
todos += run.get("todos", [])

# ---- week --------------------------------------------------------------------
mon = today - dt.timedelta(days=today.weekday())
dd = []
ev = {}
for e in run.get("week_events", []):
    ev.setdefault(e.get("date", ""), []).append(e)
for i in range(7):
    day = mon + dt.timedelta(days=i)
    applied = sum(1 for r in rows if d(r.get("date_applied")) == day)
    es = ev.get(day.isoformat(), [])
    dd.append({"d": day.strftime("%a %d"), "applied": applied, "replies": sum(e.get("replies", 0) for e in es),
               "interviews": sum(e.get("interviews", 0) for e in es), "touches": sum(e.get("touches", 0) for e in es)})
tot = {k: sum(x[k] for x in dd) for k in ("applied", "replies", "interviews", "touches")}
week = {"label": f"Week of {mon.strftime('%b %d')}", "days": dd, "totals": tot,
        "targets": {"applied": 10, "touches": 5, "replies": 2, "interviews": 1}, "upcoming": run.get("agenda", [])}
if run.get("minutes"):
    week["minutes"] = run["minutes"]

today_doc = {"date": today.isoformat(), "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "run": run.get("run", "morning"),
             "summary": run.get("summary", ""), "notes": run.get("notes", []), "todos": todos, "inbox": run.get("inbox", [])[:8],
             "agenda": run.get("agenda", []), "loops": run.get("loops", []), "week": week}
json.dump({"updated": today_doc["generated"], "roles": roles}, open(os.path.join(out, "roles.json"), "w"), ensure_ascii=False)
json.dump(today_doc, open(os.path.join(out, "today.json"), "w"), ensure_ascii=False)
print(json.dumps({"roles": len(roles), "todos": len(todos), "bytes_roles": os.path.getsize(os.path.join(out, "roles.json")),
                  "bytes_today": os.path.getsize(os.path.join(out, "today.json"))}))
