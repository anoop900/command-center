"""Build the dashboard documents board/roles and board/today from the roles collection (ported from sync_db.py)."""
import datetime as dt
import re

from cc import learned, relevance, store
from cc.rules import job_key, reminders

GATE_OPEN = ("pending", "later")  # gate_review values that keep a role in the "Failed your rules" list
TARGETS = {"applied": 10, "touches": 5, "replies": 2, "interviews": 1}
CLOSED = ("rejected", "closed", "withdrew", "declined", "no response", "not applying", "not applied", "deadline passed")


def _d(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def gate_open(r):
    """True for a gate-failed role the user has not decided on (also ones saved before the review list existed)."""
    if "gate_review" not in r:
        return str(r.get("status", "")).lower() == "skipped (gate)"
    return r["gate_review"] in GATE_OPEN


def stage(r):
    s, dec = r.get("status", "").lower(), r.get("decision", "").lower()
    if "offer" in s or "interview" in s:
        return "interviewing"
    if gate_open(r) and "skip" in s:
        return "gate_failed"  # failed the user's own rules, waiting for the user to look
    if any(k in s for k in CLOSED):
        return "closed"
    if "skip" in s or dec == "skip":
        return "skipped"
    if "applied" in s:
        return "applied"
    if s.startswith("ready"):
        return "ready"
    if "build on request" in s or dec == "queued":
        return "new"
    return "new" if not s.strip() else None     # found but never triaged: show it, do not drop it


def _num(s):
    m = re.search(r"\d+", str(s or ""))
    return int(m.group()) if m else 0


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s)[:80]


DEFAULT_MAX_AGE = 21


def max_age(cfg):
    """Days a new or skipped role stays on the board; a missing, non-numeric or non-positive setting means the default."""
    v = (cfg or {}).get("max_role_age_days")
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else DEFAULT_MAX_AGE


def build_roles(rows, extra, today, rank=None, age=DEFAULT_MAX_AGE):
    roles = []
    for r in rows:
        st = stage(r)
        if not st:
            continue
        found = _d(r.get("date_found"))
        if st in ("new", "skipped") and found and (today - found).days > age and r.get("source") != "manual":
            continue  # stale, keep the board short
        k = r.get("key") or job_key.key(r.get("company", ""), r.get("title", ""))
        link = r.get("link", "")
        x = {"key": k, "company": r.get("company", ""), "title": r.get("title", ""), "status": r.get("status", ""), "stage": st,
             "fit": r.get("fit_pct", ""), "pay": r.get("pay", ""), "location": r.get("location", ""), "mode": r.get("work_mode", ""),
             "link": link if str(link).startswith("http") else "", "gaps": r.get("reason_or_gaps", ""),
             "found": r.get("date_found", ""), "applied": r.get("date_applied", ""), "follow_up": r.get("follow_up", ""),
             "source": r.get("source", ""), "channel": r.get("channel", ""), "kit": r.get("kit", ""), "why": "", "warm": "", "posted": "",
             "label": r.get("label", ""), "label_mode": r.get("label_mode", ""), "gate_review": r.get("gate_review", ""),
             "repost": r.get("repost_note", ""), "seen_again": r.get("seen_again", 0)}
        x["likelihood"], x["rank_why"] = rank(r.get("title", "")) if rank else (0, "")
        x.update({a: b for a, b in extra.get(k, {}).items() if a in ("why", "warm", "posted", "kit")})
        if x["stage"] == "ready" and not x["kit"]:
            x["stage"] = "new"      # "Kits ready" only lists roles whose kit exists; the others still need building
        roles.append(x)
    return roles


def ranker(conn):
    """title -> (points, reason) from the learned model; (0, '') when nothing is learned or it cannot be read."""
    try:
        mdl = learned.model(conn)
        forgotten = relevance.settings(conn).get("forgotten", [])
    except Exception:
        return lambda title: (0, "")
    return lambda title: learned.rank_adjust(mdl, title, forgotten)


def build_todos(roles, rows, run, today):
    todos = []
    ready = [r for r in roles if r["stage"] == "ready" and _d(r["found"]) and (today - _d(r["found"])).days <= 10]
    for r in sorted(ready, key=lambda r: -(_num(r["fit"]) + r.get("likelihood", 0))):
        hot = _num(r["fit"]) >= 80
        todos.append({"id": "apply-" + _slug(r["key"]), "type": "apply", "key": r["key"], "priority": 1 if hot else 2,
                      "title": f"Apply: {r['company']}, {r['title']}", "tag": ("80%+ " if hot else "") + "kit ready",
                      "tagClass": "hot" if hot else "ready",
                      "detail": " · ".join(filter(None, [r["fit"] and "Fit " + str(r["fit"]), r["pay"], r["warm"] and "Warm: " + r["warm"],
                                                    r.get("rank_why") if r.get("likelihood", 0) > 0 else ""]))})
    byk = {r["key"] for r in roles}
    for x in reminders.reminders(rows, today):
        comp, _, title = x["role"].partition(", ")
        k = job_key.key(comp, title)
        if x["type"].startswith(("follow", "no reply")):
            todos.append({"id": f"fu-{_slug(k)}-{today}", "type": "followup", "key": k if k in byk else "", "priority": 2,
                          "title": f"Follow up: {x['role']}", "detail": f"{x['type']} ({x['note']})", "tag": "follow-up"})
        elif x["type"].startswith("interview"):
            todos.append({"id": f"iv-{_slug(k)}-{today}", "type": "interview", "key": "", "priority": 1,
                          "title": f"Interview check-in: {x['role']}", "detail": x.get("note", ""), "tag": "interview", "tagClass": "hot"})
    return todos + run.get("todos", [])


def build_week(rows, run, today):
    mon = today - dt.timedelta(days=today.weekday())
    ev = {}
    for e in run.get("week_events", []):
        ev.setdefault(e.get("date", ""), []).append(e)
    days = []
    for i in range(7):
        day = mon + dt.timedelta(days=i)
        es = ev.get(day.isoformat(), [])
        days.append({"d": day.strftime("%a %d"), "applied": sum(1 for r in rows if _d(r.get("date_applied")) == day),
                     "replies": sum(e.get("replies", 0) for e in es), "interviews": sum(e.get("interviews", 0) for e in es),
                     "touches": sum(e.get("touches", 0) for e in es)})
    totals = {k: sum(x[k] for x in days) for k in TARGETS}
    return {"label": f"Week of {mon.strftime('%b %d')}", "days": days, "totals": totals,
            "targets": dict(TARGETS), "upcoming": run.get("agenda", [])}


def build_docs(rows, run, today, now=None, rank=None, age=DEFAULT_MAX_AGE):
    """rows: role data dicts. run: {run, summary, notes, todos, inbox, agenda, loops, week_events, roles, dismissed}."""
    gen = (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
    roles = build_roles(rows, run.get("roles", {}), today, rank, age)
    gone = set(run.get("dismissed", []))
    todos = [t for t in build_todos(roles, rows, run, today) if t["id"] not in gone]
    notes = [n for n in run.get("notes", []) if n.get("id", n.get("key", "")) not in gone][:6]
    t = {"date": today.isoformat(), "generated": gen, "run": run.get("run", "morning"), "summary": run.get("summary", ""),
         "notes": notes, "todos": todos, "inbox": run.get("inbox", [])[:8], "agenda": run.get("agenda", []),
         "loops": [x for x in run.get("loops", []) if x.get("id") not in gone], "week": build_week(rows, run, today)}
    return {"updated": gen, "roles": roles, "max_age_days": age}, t


def write_docs(conn, roles_doc, today_doc):
    """Set board/roles and board/today; skip the write when only the timestamps differ (idempotent runs)."""
    def strip(d):
        return {k: v for k, v in d.items() if k not in ("updated", "generated")}
    for did, doc in (("roles", roles_doc), ("today", today_doc)):
        cur = store.get(conn, "board", did)
        if cur and strip(cur["data"]) == strip(doc):
            continue
        store.set_doc(conn, "board", did, doc, if_version=cur["version"] if cur else None, by="run")


DERIVED_TODO = ("apply-", "fu-", "iv-")


def refresh(conn, today, gone=(), now=None):
    """Rebuild the board's role list and the role-derived to-dos after a button press, keeping everything else the run wrote."""
    cur_r, cur_t = store.get(conn, "board", "roles"), store.get(conn, "board", "today")
    if not cur_r or not cur_t:
        return
    rows = [d["data"] for d in store.list_docs(conn, "roles")]
    age = cur_r["data"].get("max_age_days", DEFAULT_MAX_AGE)
    roles = build_roles(rows, {}, today, ranker(conn), age)
    keep = [t for t in cur_t["data"].get("todos", []) if not str(t.get("id", "")).startswith(DERIVED_TODO)]
    todos = [t for t in build_todos(roles, rows, {}, today) + keep if t["id"] not in set(gone)]
    open_n = sum(1 for x in roles if x["stage"] in ("new", "ready", "applied", "interviewing"))
    gen = (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
    write_docs(conn, {"updated": gen, "roles": roles, "max_age_days": age},
               dict(cur_t["data"], todos=todos, generated=gen, summary=f"{open_n} open roles, {len(todos)} to do.",
                    week=_refresh_week(cur_t["data"].get("week") or {}, rows, today)))


def ensure(conn, today):
    """Show a role added between runs: refresh the board, or build it from the roles when no run has written one yet."""
    if store.get(conn, "board", "roles") and store.get(conn, "board", "today"):
        return refresh(conn, today)
    rows = [d["data"] for d in store.list_docs(conn, "roles")]
    write_docs(conn, *build_docs(rows, {}, today, None, ranker(conn)))


def _refresh_week(old, rows, today):
    """Recount applications per day from the roles; replies, interviews and touches come from mail and stay as the run wrote them."""
    new = build_week(rows, {"agenda": old.get("upcoming", [])}, today)
    if old.get("label") == new["label"]:
        prev = {d.get("d"): d for d in old.get("days", [])}
        for d in new["days"]:
            for k in ("replies", "interviews", "touches"):
                d[k] = prev.get(d["d"], {}).get(k, 0)
        new["totals"] = {k: sum(x[k] for x in new["days"]) for k in TARGETS}
    return new
