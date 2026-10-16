"""Learning & Improvement: what the job-search history says needs work, and what to do about it.

Deterministic and offline: it only reads documents already in the store (roles, contacts, tasks, targets,
kitindex, kitreviews, reviews, runs) and the saved profile, so it works in every mode and nothing is guessed.
Every finding carries its evidence (counts and dates) and one concrete next step. A comparison needs
MIN_GROUP applications in each group before it is reported, so a handful of rows never looks like a pattern."""
import datetime as dt
import re
from collections import Counter

from . import profile, store

MIN_APPLIED = 10      # below this many applications there is no outcome signal yet
MIN_GROUP = 3         # applications in a group before comparing it with the rest
STALE_DAYS = 7        # a ready-to-apply role older than this is going cold
SLOW_APPLY_DAYS = 5   # median days from finding a role to applying
GHOST_DAYS = 21       # an application with no reply this long is treated as unanswered
WEEKS = 8

POSITIVE = ("interviewing", "offer")
RESOLVED = POSITIVE + ("rejected", "noresp")
APPLIED = ("applied",) + RESOLVED
SKIP_REASONS = (("location", ("location",)), ("work authorization", ("work_auth", "visa", "sponsor", "permit")),
                ("pay", ("pay", "salary", "compensation")), ("fit below threshold", ("below ", "not in profile")),
                ("people management", ("people management",)), ("seniority or title", ("seniority", "junior", "title")))
NOISE = {"The", "Not", "Role", "Below", "Skip", "Mostly", "Posting", "Strong", "Weak", "Deep", "Senior", "Lead",
         "Manager", "Director", "Engineering", "Software", "Architect", "Remote", "Hybrid", "Canada", "Toronto",
         "Ontario", "And", "For", "With", "Years", "Experience", "Does", "Mention", "Category", "May", "Fit", "Actively",
         "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec"}


def stage(r):
    s = str(r.get("status", "")).lower().strip()
    if "interview" in s:
        return "interviewing"
    for key, name in (("offer", "offer"), ("rejected", "rejected"), ("no response", "noresp"), ("closed", "noresp"),
                      ("skipped", "skipped"), ("not appl", "skipped"), ("deadline", "missed")):
        if key in s:
            return name
    if s.startswith("applied"):
        return "applied"
    if s.startswith("ready") or "build on request" in s:
        return "ready"
    return "skipped" if str(r.get("decision", "")).lower() in ("skip", "skipped") else "new"


def _date(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _pct(r):
    m = re.match(r"\s*(\d{1,3})", str(r.get("fit_pct", "")))
    return int(m.group(1)) if m else None


def _mode(r):
    t = str(r.get("work_mode", "")).lower()
    return "remote" if "remote" in t else "hybrid" if "hybrid" in t else "on-site" if re.search(r"on-?site|office", t) else "unstated"


def _level(r):
    t = str(r.get("title", "")).lower()
    for name, words in (("executive", ("chief", "cto", "vp", "vice president", "head of")), ("director", ("director",)),
                        ("manager", ("manager", "lead")), ("architect", ("architect",)), ("engineer", ("engineer", "developer"))):
        if any(re.search(rf"\b{w}\b", t) for w in words):
            return name
    return "other"


def _band(r):
    p = _pct(r)
    return "unscored" if p is None else "80+" if p >= 80 else "70-79" if p >= 70 else "60-69" if p >= 60 else "under 60"


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _median(xs):
    xs = sorted(xs)
    return None if not xs else xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2


def _rate(n, d):
    return round(100 * n / d) if d else None


def _group(applied, key):
    """-> {group: {applied, positive, responded, ghosted}} for roles that were applied to."""
    out = {}
    for r in applied:
        g = out.setdefault(key(r), {"applied": 0, "positive": 0, "responded": 0, "ghosted": 0})
        g["applied"] += 1
        g["positive"] += r["_s"] in POSITIVE
        g["responded"] += r["_s"] in POSITIVE + ("rejected",)
        g["ghosted"] += r["_s"] == "noresp"
    return out


def _weeks(rows, field, today):
    """Counts per week for the last WEEKS weeks, oldest first, by the date in `field`."""
    start = today - dt.timedelta(days=today.weekday())
    out = []
    for i in range(WEEKS - 1, -1, -1):
        a = start - dt.timedelta(weeks=i)
        out.append({"week": a.isoformat(), "n": sum(1 for r in rows if (d := _date(r.get(field))) and a <= d < a + dt.timedelta(days=7))})
    return out


class _Out:
    def __init__(self):
        self.areas, self.working, self.points = [], [], []

    def area(self, id_, group, severity, title, evidence, action, n=None, items=()):
        """n: how many things are wrong (what a daily task can bring down); items: names to act on, kept local."""
        self.areas.append({"id": id_, "group": group, "severity": severity, "title": title,
                           "evidence": [e for e in evidence if e], "action": action, "n": n, "items": [str(i) for i in items]})

    def win(self, title, evidence):
        self.working.append({"title": title, "evidence": evidence})

    def point(self, name, n):
        self.points.append({"name": name, "count": n})


def _outcomes(o, applied, overall):
    """Which kind of application gets answered, and which does not."""
    for dim, label, key in (("source", "Source", lambda r: r.get("source") or "unstated"), ("work mode", "Work mode", _mode),
                            ("fit score band", "Fit score", _band), ("seniority", "Level", _level),
                            ("channel", "Channel", lambda r: str(r.get("channel") or "unstated").lower()),
                            ("match label", "Match label", lambda r: r.get("label") or "unlabelled")):
        groups = {k: g for k, g in _group(applied, key).items() if g["applied"] >= MIN_GROUP}
        o.point(f"{dim}: outcome by group", sum(g["applied"] for g in groups.values()))
        if len(groups) < 2:
            continue
        rates = {k: _rate(g["positive"], g["applied"]) for k, g in groups.items()}
        best, worst = max(rates, key=rates.get), min(rates, key=rates.get)
        if rates[best] - rates[worst] < 20:
            continue
        gb, gw = groups[best], groups[worst]
        o.area(f"outcome-{dim.replace(' ', '-')}", "Where you apply", "medium", f"{label}: {best} outperforms {worst}",
               [f"{best}: {gb['positive']} of {gb['applied']} reached interview ({rates[best]}%).",
                f"{worst}: {gw['positive']} of {gw['applied']} reached interview ({rates[worst]}%)."],
               f"Weight your next applications toward {best} and stop spending kits on {worst} unless the role is a strong fit.")
        o.win(f"{label}: {best}", f"{rates[best]}% reached interview ({gb['positive']} of {gb['applied']}).")


def _funnel(o, rows, applied, today):
    n = len(applied)
    pos = sum(r["_s"] in POSITIVE for r in applied)
    resp = sum(r["_s"] in POSITIVE + ("rejected",) for r in applied)
    ghost = sum(bool(r["_s"] == "noresp" or (r["_s"] == "applied" and (d := _date(r.get("date_applied"))) and (today - d).days >= GHOST_DAYS))
                for r in applied)
    o.point("applications", n)
    if n < MIN_APPLIED:
        o.area("data-volume", "Data", "low", "Too few applications to learn from yet",
               [f"{n} applications so far; outcome comparisons start at {MIN_APPLIED}."],
               f"Apply to {MIN_APPLIED - n} more roles; the outcome sections fill in by themselves.")
        return
    if _rate(resp, n) < 20:
        o.area("response-rate", "Conversion", "high", "Few applications get any reply",
               [f"{resp} of {n} applications got a reply ({_rate(resp, n)}%).", f"{ghost} have been silent {GHOST_DAYS}+ days."],
               "Tighten the fit threshold, tailor the top third of the resume to each posting, and apply through a contact where you have one.")
    elif _rate(pos, n) < 10:
        o.area("interview-rate", "Conversion", "high", "Replies are not turning into interviews",
               [f"{pos} interviews from {n} applications ({_rate(pos, n)}%); {resp} replies."],
               "Review the rejected roles for a common gap and move it up the resume; ask recruiters for feedback on the next decline.")
    else:
        o.win("Conversion", f"{pos} interviews from {n} applications ({_rate(pos, n)}%).")
    if ghost >= 5 and _rate(ghost, n) >= 30:
        o.area("ghosting", "Follow-up", "medium", "Many applications are never answered",
               [f"{ghost} of {n} unanswered after {GHOST_DAYS}+ days."],
               "Send one follow-up to a named person at day 7 and close roles that stay silent after day 21.", ghost)


def _hygiene(o, rows, applied, today):
    gaps = []
    ready = [r for r in rows if r["_s"] in ("ready", "new") and (d := _date(r.get("date_found"))) and (today - d).days > STALE_DAYS
             and str(r.get("decision", "")).lower() not in ("skip", "skipped")]
    o.point("roles waiting to be applied", len(ready))
    if len(ready) >= 3:
        o.area("stale-ready", "Pace", "medium", "Good roles are going cold",
               [f"{len(ready)} roles found over {STALE_DAYS} days ago are still not applied to or dropped."],
               "Apply to or drop each one this week; roles lose value fast after the first few days.", len(ready),
               [r.get("company", "?") for r in ready])
    days = [(da - df).days for r in applied if (df := _date(r.get("date_found"))) and (da := _date(r.get("date_applied"))) and da >= df]
    o.point("days from found to applied", len(days))
    if len(days) >= MIN_GROUP and _median(days) > SLOW_APPLY_DAYS:
        o.area("slow-apply", "Pace", "medium", "It takes too long to go from found to applied",
               [f"Median {_median(days):g} days across {len(days)} applications."],
               "Apply the same or next day for roles scoring 75+ and batch the rest once a week.")
    live = [r for r in applied if r["_s"] == "applied"]
    nofu = [r for r in live if not r.get("follow_up")]
    over = [r for r in live if (d := _date(r.get("follow_up"))) and d < today]
    o.point("applications with a follow-up date", len(live) - len(nofu))
    if nofu:
        o.area("no-follow-up", "Follow-up", "medium", "Applications with no follow-up date",
               [f"{len(nofu)} of {len(live)} open applications have no follow-up date."],
               "Set a follow-up date on each one; the Tasks page then reminds you.", len(nofu), [r.get("company", "?") for r in nofu])
    if over:
        o.area("overdue-follow-up", "Follow-up", "high", "Follow-ups are overdue",
               [f"{len(over)} follow-up dates have passed: " + ", ".join(str(r.get('company', '?')) for r in over[:5])],
               "Send those follow-ups today, then reset each date.", len(over), [r.get("company", "?") for r in over])
    unsure = [r for r in rows if "confirm" in str(r.get("status", "")).lower() or "unknown" in str(r.get("status", "")).lower()]
    blank = [r for r in rows if r["_s"] == "new" and not r.get("status")]
    if unsure:
        o.area("unconfirmed-status", "Data quality", "low", "Some roles have an unconfirmed status",
               [f"{len(unsure)} roles say 'confirm' or 'status unknown'."], "Open each and set the real status so the numbers here are right.",
               len(unsure), [r.get("company", "?") for r in unsure])
    o.point("roles with no status", len(blank))


def _kits(o, conn, rows, applied):
    no_kit = [r for r in applied if not r.get("resume_file")]
    no_cover = [r for r in applied if not r.get("cover_letter")]
    o.point("applications with a tailored resume", len(applied) - len(no_kit))
    if no_kit and applied:
        o.area("no-resume-file", "Application quality", "medium", "Applications without a tailored resume on record",
               [f"{len(no_kit)} of {len(applied)} applications have no resume file recorded."],
               "Build a kit for every application you send so you can see later which version worked.", len(no_kit),
               [r.get("company", "?") for r in no_kit])
    o.point("applications with a cover letter", len(applied) - len(no_cover))
    scored = [(a, r) for r in applied if isinstance(a := r.get("ats"), (int, float)) and not isinstance(a, bool)]
    low = [r for a, r in scored if a < 0.8]
    o.point("applications with an ATS score", len(scored))
    if low:
        o.area("low-ats", "Application quality", "medium", "Resumes scoring low on the ATS check",
               [f"{len(low)} of {len(scored)} scored below 0.80."], "Add the missing keywords the ATS check lists, where they are true.", len(low),
               [r.get("company", "?") for r in low])
    revs = store.list_docs(conn, "kitreviews")
    issues = sum(_int(x["data"].get("grounding_issues")) for x in revs)
    o.point("kit reviews", len(revs))
    if issues:
        o.area("grounding", "Application quality", "high", "Reviews found claims not backed by your profile",
               [f"{issues} grounding issues across {len(revs)} reviewed kits."], "Fix those lines in your profile facts or remove them from the resume.", issues)
    elif revs:
        o.win("Kit honesty", f"{len(revs)} reviewed kits, no ungrounded claims.")
    built = {x["data"].get("role_id") or x["id"] for x in store.list_docs(conn, "kitindex")}
    idle = [r for r in rows if r["_s"] not in APPLIED + ("skipped",) and r.get("key") in built]
    if idle:
        o.area("unused-kits", "Pace", "low", "Kits built for roles you have not applied to",
               [f"{len(idle)} kits are ready but unsent."], "Send them or mark the roles skipped.", len(idle), [r.get("company", "?") for r in idle])


def _skips(o, rows, data_dir):
    skipped = [r for r in rows if r["_s"] == "skipped"]
    o.point("skipped roles", len(skipped))
    if len(skipped) < MIN_GROUP:
        return
    why = Counter()
    for r in skipped:
        text = str(r.get("reason_or_gaps", "")).lower()
        for name, words in SKIP_REASONS:
            if any(w in text for w in words):
                why[name] += 1
    top = why.most_common(2)
    if top and top[0][1] >= MIN_GROUP and top[0][1] * 3 >= len(skipped):
        name, cnt = top[0]
        action = {"location": "Search remote-first or widen the commutable area in Rules, or stop opening roles outside it.",
                  "work authorization": "Add the work routes you hold to Rules, or filter searches to countries where you can work.",
                  "pay": "Raise the minimum pay in your search filters or lower the floor in Rules if it is too tight.",
                  "fit below threshold": "Narrow search titles to what your profile supports so fewer weak roles reach you.",
                  "people management": "Add the leadership scope you really have to your profile, or exclude pure people-management titles.",
                  "seniority or title": "Adjust target titles in your searches."}[name]
        o.area("skip-reason", "Search targeting", "medium", f"Most skipped roles fail on {name}",
               [f"{cnt} of {len(skipped)} skipped roles ({_rate(cnt, len(skipped))}%): " + "; ".join(f"{n} {c}" for n, c in top)], action)
    gate = Counter(g for r in rows if isinstance(r.get("gates"), dict) for g, v in r["gates"].items() if v == "FAIL")
    o.point("roles with a failed gate", sum(gate.values()))
    pend = [r for r in rows if r.get("gate_review") == "pending"]
    if pend:
        o.area("gate-review", "Search targeting", "medium", "Roles were gated out and never looked at",
               [f"{len(pend)} gate decisions are pending your review; failing gates: " + ", ".join(f"{g} {c}" for g, c in gate.most_common(3))],
               "Review them once: if some should have passed, adjust that rule so it stops hiding good roles.", len(pend),
               [r.get("company", "?") for r in pend])
    terms = Counter()
    own = {str(c.get("value", "")).lower() for k, c in profile.load(data_dir)["fields"].items() if k in ("firstName", "lastName")}
    for r in skipped:
        terms.update({t for t in re.findall(r"\b[A-Z][A-Za-z+#.]{2,}\b|\b[A-Z]{2,}\b", str(r.get("reason_or_gaps", ""))) if t not in NOISE and t.lower() not in own})
    recurring = [t for t, c in terms.most_common(5) if c >= MIN_GROUP]
    o.point("skill terms in skip reasons", sum(terms.values()))
    if recurring:
        o.area("skill-gaps", "Skills", "medium", "Skills that keep appearing in skipped roles",
               ["Recurring in skip reasons: " + ", ".join(recurring) + "."],
               "If you have real experience with any, add it to your profile facts; if not, decide whether to learn one or stop targeting those roles.",
               len(recurring), recurring)


def _network(o, conn, rows, applied, today):
    contacts = [c["data"] for c in store.list_docs(conn, "contacts")]
    o.point("contacts", len(contacts))
    due = []
    for c in contacts:
        last, cad = _date(c.get("last_touch")), c.get("cadence")
        if isinstance(cad, int) and (last is None or (today - last).days > cad):
            due.append(c.get("name", "?"))
    if due:
        o.area("contacts-due", "Networking", "medium", "Contacts overdue for a touch",
               [f"{len(due)} of {len(contacts)} past their cadence: " + ", ".join(due[:5])], "Send each a short, specific message this week.", len(due), due)
    names = {str(c.get("company", "")).lower() for c in contacts if c.get("company")}
    warm = [r for r in applied if str(r.get("company", "")).lower() in names]
    cold = [r for r in applied if str(r.get("company", "")).lower() not in names]
    o.point("applications at companies with a contact", len(warm))
    if len(warm) >= MIN_GROUP and len(cold) >= MIN_GROUP:
        w, c = _rate(sum(r["_s"] in POSITIVE for r in warm), len(warm)), _rate(sum(r["_s"] in POSITIVE for r in cold), len(cold))
        if w - c >= 15:
            o.area("warm-intro", "Networking", "medium", "Applications through a contact do better",
                   [f"With a contact: {w}% reached interview ({len(warm)} applied). Without: {c}% ({len(cold)} applied)."],
                   "For each role you want most, ask a contact there for an intro before you apply.")
    targets = [t["data"] for t in store.list_docs(conn, "targets") if t["data"].get("status") == "active"]
    applied_co = {str(r.get("company", "")).lower() for r in applied}
    cold_targets = [t for t in targets if str(t.get("company", "")).lower() not in applied_co]
    o.point("active target companies", len(targets))
    if len(cold_targets) >= 3:
        o.area("targets-untouched", "Search targeting", "low", "Target companies with no application yet",
               [f"{len(cold_targets)} of {len(targets)} active targets: " + ", ".join(str(t.get('company')) for t in cold_targets[:5])],
               "Check each company's openings this week or move it to paused.", len(cold_targets),
               [t.get("company", "?") for t in cold_targets])


def _habits(o, conn, rows, today):
    runs = [r["data"] for r in store.list_docs(conn, "runs") if (d := _date(r["data"].get("date"))) and (today - d).days < 7]
    o.point("runs in the last 7 days", len(runs))
    tasks = [t["data"] for t in store.list_docs(conn, "tasks")]
    late = [t for t in tasks if not t.get("done") and (d := _date(t.get("due"))) and d < today]
    o.point("tasks", len(tasks))
    if late:
        o.area("overdue-tasks", "Follow-up", "medium", "Tasks past their due date",
               [f"{len(late)} of {len(tasks)} tasks overdue: " + ", ".join(str(t.get('title'))[:40] for t in late[:3])], "Do or reschedule them on the Tasks page.", len(late), [t.get("title", "?") for t in late])
    found, applied = _weeks(rows, "date_found", today), _weeks([r for r in rows if r["_s"] in APPLIED], "date_applied", today)
    this, prev = sum(w["n"] for w in applied[-2:]), sum(w["n"] for w in applied[-4:-2])
    o.point("weeks of activity", sum(1 for w in found + applied if w["n"]))
    if prev >= 4 and this <= prev // 2:
        o.area("pace-drop", "Pace", "high", "Application pace has dropped",
               [f"Last 2 weeks: {this} applications; the 2 weeks before: {prev}."], "Block fixed apply sessions in your calendar and apply to the best-scored roles first.")
    elif prev and this > prev:
        o.win("Pace", f"Applications are up: {this} in the last 2 weeks versus {prev} before.")
    lessons = []
    for rv in sorted(store.list_docs(conn, "reviews"), key=lambda x: x["id"])[-1:]:
        stop = rv["data"].get("stop")
        lessons = [str(x) for x in stop][:3] if isinstance(stop, list) else []
    if lessons:
        o.area("weekly-review", "Habits", "low", "Things your last weekly review said to stop",
               lessons, "Check each is actually stopped; remove it from your routine if not.")
    return found, applied


def analyze(conn, data_dir, today=None):
    """-> {summary, trend, breakdown, areas, working, data_points, enough_data}."""
    today = today or dt.date.today()
    rows = [dict(r["data"], _id=r["id"]) for r in store.list_docs(conn, "roles")]
    for r in rows:
        r["_s"] = stage(r)
    applied = [r for r in rows if r["_s"] in APPLIED]
    o = _Out()
    comp = profile.completeness(data_dir)
    o.point("profile items", comp["total"])
    if comp["percent"] < 100:
        o.area("profile", "Profile", "high", f"Profile is {comp['percent']}% complete",
               [f"{comp['done']} of {comp['total']} items done; missing: " + ", ".join(m["label"] for m in comp["missing"][:4]) + "."],
               "Open Profile and answer the missing items; scoring and kits are only as good as these facts.",
               len(comp["missing"]), [m["label"] for m in comp["missing"]])
    o.point("roles", len(rows))
    _funnel(o, rows, applied, today)
    if len(applied) >= MIN_APPLIED:
        _outcomes(o, applied, None)
    _hygiene(o, rows, applied, today)
    _kits(o, conn, rows, applied)
    _skips(o, rows, data_dir)
    _network(o, conn, rows, applied, today)
    found, weekly = _habits(o, conn, rows, today)
    n = len(applied)
    count = lambda *s: sum(r["_s"] in s for r in rows)
    order = {"high": 0, "medium": 1, "low": 2}
    o.areas.sort(key=lambda a: order[a["severity"]])
    return {"summary": {"found": len(rows), "skipped": count("skipped"), "applied": n, "interviewing": count("interviewing"),
                        "offers": count("offer"), "rejected": count("rejected"), "no_response": count("noresp"),
                        "interview_rate": _rate(sum(r["_s"] in POSITIVE for r in applied), n),
                        "response_rate": _rate(sum(r["_s"] in POSITIVE + ("rejected",) for r in applied), n)},
            "trend": {"found": found, "applied": weekly},
            "breakdown": {k: _group(applied, f) for k, f in (("source", lambda r: r.get("source") or "unstated"), ("work_mode", _mode),
                                                              ("fit_band", _band), ("level", _level))},
            "profile": comp, "areas": o.areas, "working": o.working, "data_points": o.points,
            "enough_data": n >= MIN_APPLIED}
