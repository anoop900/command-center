"""Daily improvement coaching: turn the Learning findings into a few small tasks a day, praise what gets done, and
later show whether the thing moved.

The plan is deterministic and works in every mode. AI (modes table row "coach") only rewords tasks and writes one
praise line each; it never chooses tasks and never receives names, postings, mail or the user's skip notes. Praise
numbers (streak, tasks this week) are filled in here from real records, never written by a model."""
import datetime as dt
import re
import zlib

from . import ai as ai_layer
from . import learning, store

COLLECTION = "coach"
PER_DAY = 3
DONE_COOL = 2                        # days before a finished finding is offered again
# reason code -> (label, days before it is offered again, drops to the end of the list when it returns).
# Only "other" needs a typed note. Tasks are suggestions: a skip with a reason is never a failure.
REASONS = {"wrong": ("Not relevant to me", 30, False), "later": ("Not important right now", 14, True),
           "waiting": ("Waiting on someone or something", 7, False), "no_time": ("No time today", 1, False),
           "other": ("Other", 3, False)}
REPEAT = 3                           # after this many skips a finding returns only if it has got worse
NOTE_MAX = 300
KEEP = 100                           # log and skip entries kept in the state document
# id -> (title, minutes, steps, how many names to show). {names} and {n} are filled from the finding.
TEMPLATES = {
    "overdue-follow-up": ("Send a short follow-up to {names}", 15, ["Open each application", "Write two lines asking for an update", "Set the next follow-up date"], 3),
    "profile": ("Answer {n} missing profile questions: {names}", 10, ["Open Profile", "Fill in the items listed", "Tick each one you know is true"], 3),
    "contacts-due": ("Message {names}", 15, ["Write one short, specific note to each", "Mark each contact as touched"], 2),
    "stale-ready": ("Apply to or drop {names}", 15, ["Open each role", "Apply today, or mark it skipped"], 3),
    "gate-review": ("Review gated roles: {names}", 15, ["Check why each was gated", "Mark any that should have passed"], 3),
    "no-follow-up": ("Set a follow-up date for {names}", 5, ["Open each role", "Pick a date a week after you applied"], 5),
    "overdue-tasks": ("Finish or reschedule: {names}", 10, ["Do it now if it takes under 10 minutes", "Otherwise set a new date"], 3),
    "unconfirmed-status": ("Confirm the real status of {names}", 10, ["Open each role", "Set the status you know to be true"], 4),
    "grounding": ("Fix {n} claims a review flagged", 15, ["Open the latest kit review", "Correct or remove each flagged line"], 0),
    "low-ats": ("Raise the ATS score for {names}", 15, ["Open the ATS check", "Add the missing keywords that are true for you"], 2),
    "no-resume-file": ("Record the resume you sent to {names}", 10, ["Open each role", "Attach the resume file you used"], 3),
    "targets-untouched": ("Check openings at {names}", 15, ["Look at each careers page", "Add roles worth a look, or pause the company"], 2),
    "ghosting": ("Close or chase the oldest silent applications ({n})", 15, ["Sort the applications by date", "Follow up on the oldest, close the rest"], 0),
    "skill-gaps": ("Decide about {names}", 10, ["If you have real experience, add it to your profile facts", "If not, note that you skip roles that need it"], 2),
    "unused-kits": ("Send or skip the kits waiting for {names}", 10, ["Open each role", "Apply, or mark it skipped"], 3),
    "skip-reason": ("Change one search filter that causes most skips", 10, ["Open Rules", "Adjust the one setting behind the top skip reason"], 0),
}
ORDER = {k: i for i, k in enumerate(TEMPLATES)}
SEVERITY = {"high": 0, "medium": 1, "low": 2}
PRAISE = ("Done. That one was worth doing.", "Good. One less thing slowing the search down.",
          "Well done. Small, steady steps are how this works.", "That is finished, and it matters more than it feels.",
          "Nicely done. The search is a little healthier than it was this morning.")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _join(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _fill(text, items, n):
    names = _join(items) if items else "these"
    return text.replace("{names}", names).replace("{n}", str(n if n is not None else len(items)))


def _n(area):
    return area["n"] if area["n"] is not None else len(area["items"])


def _task(area, last_skip=None):
    title, minutes, steps, show = TEMPLATES[area["id"]]
    items = [i.split(" · ")[0].strip() for i in area["items"][:show]] if show else []
    n = _n(area)
    prev = {"date": last_skip["date"], "reason": REASONS.get(last_skip.get("reason"), REASONS["other"])[0],
            "note": last_skip.get("note", "")} if last_skip else None
    return {"previous_skip": prev, "id": area["id"], "title_t": title, "steps_t": list(steps), "title": _fill(title, items, n),
            "steps": [_fill(s, items, n) for s in steps], "minutes": minutes, "items": items, "why": (area["evidence"] or [""])[0],
            "n": n, "status": "open", "source": "plain", "praise_t": ""}


def _rmw(conn, doc_id, change, default):
    """Read-modify-write one coach document, retrying if another writer got there first."""
    for _ in range(4):
        row = store.get(conn, COLLECTION, doc_id)
        data = dict(row["data"]) if row else dict(default)
        out = change(data)
        try:
            store.set_doc(conn, COLLECTION, doc_id, data, if_version=row["version"] if row else None, by="coach")
            return out
        except store.Conflict:
            continue
    raise ValueError("could not save; try again")


def _state_default():
    return {"streak": 0, "best": 0, "last_done": "", "log": [], "cool": {}, "baselines": {}, "skips": [], "low": {}, "dismissed": {}}


def _state(conn):
    row = store.get(conn, COLLECTION, "state")
    return {**_state_default(), **(row["data"] if row else {})}


def _streak(state, today):
    last = state.get("last_done", "")
    yday = (today - dt.timedelta(days=1)).isoformat()
    return state["streak"] if last in (today.isoformat(), yday) else 0


def _week(state, today):
    since = (today - dt.timedelta(days=6)).isoformat()
    return sum(1 for e in state["log"] if e["date"] >= since)


def get_plan(conn, today):
    row = store.get(conn, COLLECTION, today.isoformat())
    return row["data"] if row else None


def _candidates(conn, data_dir, today):
    """Findings worth a task today, most useful first; skips cooling down or repeated without getting worse are left out."""
    st = _state(conn)
    cool, low = st["cool"], st["low"]
    past = {}
    for e in st["skips"]:
        past.setdefault(e["id"], []).append(e)

    def offer(a):
        if a["id"] not in TEMPLATES or cool.get(a["id"], "") > today.isoformat():
            return False
        hist = past.get(a["id"], [])
        return not (len(hist) >= REPEAT and _n(a) <= hist[-1].get("n", 0))
    areas = [a for a in learning.analyze(conn, data_dir, today)["areas"] if offer(a)]
    areas.sort(key=lambda a: (a["id"] in low, SEVERITY[a["severity"]], ORDER[a["id"]]))
    return areas, past


def plan(conn, data_dir, today=None):
    """Today's tasks, created once per day (a second call returns the same plan)."""
    today = today or dt.date.today()
    have = get_plan(conn, today)
    if have is not None:
        return have
    areas, past = _candidates(conn, data_dir, today)
    doc = {"date": today.isoformat(), "tasks": [_task(a, (past.get(a["id"]) or [None])[-1]) for a in areas[:PER_DAY]]}
    try:
        store.set_doc(conn, COLLECTION, today.isoformat(), doc, by="coach")
    except store.Conflict:
        return get_plan(conn, today)
    return doc


def refresh(conn, data_dir, today=None):
    """After the profile, rules or demo data change: drop today's open tasks that no longer apply, update the
    names in the ones that still do, and top the plan back up. Done and skipped tasks are never touched."""
    today = today or dt.date.today()
    if get_plan(conn, today) is None:
        return None
    areas, past = _candidates(conn, data_dir, today)
    by_id = {a["id"]: a for a in areas}

    def change(doc):
        kept = []
        for t in doc["tasks"]:
            a = by_id.get(t["id"])
            if t["status"] != "open":
                kept.append(t)
            elif a is not None:
                fresh = _task(a, (past.get(a["id"]) or [None])[-1])
                kept.append(t if (t["n"], t["items"]) == (fresh["n"], fresh["items"]) else fresh)
        have = {t["id"] for t in kept}
        for a in areas:
            if len(kept) >= PER_DAY:
                break
            if a["id"] not in have and a["id"] not in {t["id"] for t in doc["tasks"]}:
                kept.append(_task(a, (past.get(a["id"]) or [None])[-1]))
        doc["tasks"] = kept
        return doc
    return _rmw(conn, today.isoformat(), change, {"date": today.isoformat(), "tasks": []})


def reword(conn, today, mode, runner):
    """Let the AI reword today's still-plain open tasks and add a praise line each. Any failure leaves the plain text."""
    doc = get_plan(conn, today)
    todo = [t for t in (doc or {}).get("tasks", []) if t["status"] == "open" and t["source"] == "plain"]
    if not todo:
        return {"status": "nothing", "reworded": 0}
    r = ai_layer.run_task("coach_wording", {"tasks": [{"id": t["id"], "title": t["title_t"], "steps": t["steps_t"]} for t in todo]},
                          mode, runner)
    if r.status != "ok" or r.source != "ai":
        return {"status": r.status, "reason": r.reason, "reworded": 0}
    new = {t["id"]: t for t in r.data["tasks"]}

    def change(d):
        for t in d["tasks"]:
            w = new.get(t["id"])
            if w and t["status"] == "open" and t["source"] == "plain":
                t.update(title_t=w["title"], steps_t=w["steps"], praise_t=w["praise"], source="ai",
                         title=_fill(w["title"], t["items"], t["n"]), steps=[_fill(s, t["items"], t["n"]) for s in w["steps"]])
        return len(new)
    return {"status": "ok", "reworded": _rmw(conn, today.isoformat(), change, {"tasks": []})}


def _pick(doc, id_):
    for t in doc["tasks"]:
        if t["id"] == id_:
            return t
    raise ValueError("that task is not in today's plan")


def _praise(task, state, today, doc):
    streak, week = _streak(state, today), _week(state, today)
    lead = task["praise_t"].replace("{streak}", str(streak)).replace("{week}", str(week)) if task.get("praise_t") else \
        PRAISE[zlib.crc32((task["id"] + today.isoformat()).encode()) % len(PRAISE)]
    facts = []
    if streak >= 2:
        facts.append(f"That is {streak} days in a row.")
    if week >= 2:
        facts.append(f"{week} tasks done this week.")
    if all(t["status"] != "open" for t in doc["tasks"]):
        facts.append("That clears today's list.")
    return " ".join([lead] + facts)


def complete(conn, id_, today=None):
    """Mark a task done. -> {praise, streak, week, already}. Done twice counts once."""
    today = today or dt.date.today()
    got = {}

    def mark(doc):
        t = _pick(doc, id_)
        got["already"] = t["status"] == "done"
        if t["status"] == "skipped":
            raise ValueError("this task was skipped; it comes back in a few days")
        if not got["already"]:
            t.update(status="done", done_at=store.now())
        got["task"], got["doc"] = dict(t), {"tasks": [dict(x) for x in doc["tasks"]]}
    _rmw(conn, today.isoformat(), mark, {"tasks": []})

    def bump(s):
        if not got["already"]:
            s["streak"] = _streak(s, today) + 1 if s["last_done"] != today.isoformat() else s["streak"]
            s["best"] = max(s["best"], s["streak"])
            s["last_done"] = today.isoformat()
            s["low"].pop(id_, None)
            s["log"] = (s["log"] + [{"date": today.isoformat(), "id": id_}])[-KEEP:]
            s["cool"][id_] = (today + dt.timedelta(days=DONE_COOL)).isoformat()
            s["baselines"].setdefault(id_, {"n": got["task"]["n"], "date": today.isoformat(), "label": got["task"]["title"]})
        return dict(s)
    s = _rmw(conn, "state", bump, _state_default())
    return {"praise": _praise(got["task"], s, today, got["doc"]), "streak": _streak(s, today), "week": _week(s, today),
            "already": got["already"]}


def skip(conn, id_, reason, note="", today=None):
    """Skip a task for now with a preset reason (see REASONS). The reason decides when it returns and how hard it is
    pushed; the note is kept for the user and never sent to an AI. Only "other" needs a note."""
    today = today or dt.date.today()
    if reason not in REASONS:
        raise ValueError("Pick a reason for skipping it.")
    note = " ".join(str(note or "").split())
    if len(note) > NOTE_MAX or CONTROL.search(note):
        raise ValueError(f"Keep the note under {NOTE_MAX} characters.")
    if reason == "other" and not note:
        raise ValueError(f"Say briefly why you are skipping it (up to {NOTE_MAX} characters).")
    label, days, low = REASONS[reason]
    got = {}

    def mark(doc):
        t = _pick(doc, id_)
        if t["status"] == "done":
            raise ValueError("this task is already done")
        got["again"] = t["status"] == "skipped"     # a second click or tab must not count as another skip
        if not got["again"]:
            t.update(status="skipped", reason=reason, note=note)
        got["n"], got["title"] = t["n"], t["title"]

    def bump(s):
        s["cool"][id_] = (today + dt.timedelta(days=days)).isoformat()
        if low:
            s["low"][id_] = True
        if reason == "wrong":
            s["dismissed"][id_] = got["title"]
        s["skips"] = (s["skips"] + [{"date": today.isoformat(), "id": id_, "reason": reason, "note": note, "n": got["n"]}])[-KEEP:]
    _rmw(conn, today.isoformat(), mark, {"tasks": []})
    if not got["again"]:
        _rmw(conn, "state", bump, _state_default())
    return {"skipped": id_, "back_in_days": days}


def restore(conn, id_, today=None):
    """Bring back a finding the user dismissed as not relevant; today's skipped task for it opens again."""
    today = today or dt.date.today()

    def change(s):
        if id_ not in s["dismissed"]:
            raise ValueError("that finding was not dismissed")
        s["dismissed"].pop(id_)
        s["cool"].pop(id_, None)
        s["skips"] = [e for e in s["skips"] if not (e["id"] == id_ and e.get("reason") == "wrong")]
    def reopen(doc):
        for t in doc["tasks"]:
            if t["id"] == id_ and t["status"] == "skipped" and t.get("reason") == "wrong":
                t["status"] = "open"
                t.pop("reason", None)
                t.pop("note", None)
    _rmw(conn, "state", change, _state_default())
    if get_plan(conn, today) is not None:
        _rmw(conn, today.isoformat(), reopen, {"tasks": []})
    return {"restored": id_}


def progress(conn, data_dir, today=None):
    """For every finding the user has worked on: the count when they first finished a task on it, and now."""
    today = today or dt.date.today()
    base = _state(conn)["baselines"]
    if not base:
        return []
    now = {a["id"]: a for a in learning.analyze(conn, data_dir, today)["areas"]}
    out = []
    for id_, b in sorted(base.items()):
        a = now.get(id_)
        out.append({"id": id_, "title": b.get("label", id_), "before": b["n"],
                    "now": 0 if a is None else a["n"], "since": b["date"], "resolved": a is None})
    return out


def resting(conn, data_dir, today, plan):
    """Findings that exist but are not in today's plan because the user skipped or finished them recently."""
    st = _state(conn)
    in_plan = {t["id"] for t in plan["tasks"]} if plan else set()
    return sorted(a["id"] for a in learning.analyze(conn, data_dir, today)["areas"]
                  if a["id"] in TEMPLATES and a["id"] not in in_plan and a["id"] not in st["dismissed"])


def snapshot(conn, data_dir, today=None):
    today = today or dt.date.today()
    s = _state(conn)
    plan = get_plan(conn, today)
    return {"plan": plan, "resting": len(resting(conn, data_dir, today, plan)) if plan else 0, "streak": _streak(s, today), "best": s["best"], "week": _week(s, today),
            "progress": progress(conn, data_dir, today), "skips": [{**e, "label": REASONS.get(e.get("reason"), REASONS["other"])[0]} for e in s["skips"][-5:][::-1]],
            "reasons": [{"code": k, "label": v[0]} for k, v in REASONS.items()],
            "dismissed": [{"id": k, "title": v} for k, v in sorted(s["dismissed"].items())]}
