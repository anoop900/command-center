"""Title relevance gate. Nothing ships excluded: the user's target levels, their own word lists and what learned.py
derived from their choices decide. Level words (intern ... executive) are the only built-in knowledge, and they mean the
same in every field. A title with no level word, or in a field we know nothing about, is neutral and passes.

A failure only routes the role to the Failed your rules list (never a silent delete). Pay, never-submit and work
authorization rules are separate gates that nothing here changes.
"""
import datetime as dt
import re

LADDER = ("intern", "entry", "mid", "senior", "lead", "manager", "director", "executive")
LEVEL_LABEL = {"intern": "Internship / co-op", "entry": "Entry / junior", "mid": "Mid level", "senior": "Senior",
               "lead": "Lead / principal", "manager": "Manager", "director": "Director / head", "executive": "VP / executive"}
# "mid" has no title words; a title without a level word is neutral.
LEVEL_WORDS = (
    ("intern", ("intern", "internship", "co-op", "coop", "student", "trainee", "apprentice", "apprenticeship", "summer")),
    ("entry", ("junior", "jr", "entry level", "entry-level", "graduate", "new grad")),
    ("executive", ("vp", "vice president", "chief", "cto", "cio", "cfo", "ceo", "coo", "president", "evp", "svp")),
    ("director", ("director", "head of")),
    ("manager", ("manager", "supervisor")),
    ("lead", ("lead", "principal", "staff", "team lead")),
    ("senior", ("senior", "sr")),
)
STOP = {"the", "and", "for", "with", "senior", "sr", "lead", "head", "director", "manager", "vp", "chief", "of", "to", "in", "at",
        "a", "an", "global", "remote", "ii", "iii", "iv", "associate", "principal", "staff", "new", "team", "group", "services",
        "intern", "internship", "junior", "graduate", "student"}


def terms(title):
    """Lowercase word terms of a title (no stop or level words, 3+ letters)."""
    return sorted({w for w in re.findall(r"[a-z][a-z+#.-]{2,}", str(title or "").lower()) if w not in STOP})


def settings(conn):
    from cc import store
    d = store.get(conn, "config", "relevance")
    s = {"enabled": True, "exclude": [], "include": [], "forgotten": [], "ai": False, "targets": [], "levels": []}
    s.update(d["data"] if d else {})
    return s


def _has(title, term):
    return re.search(r"(?<![a-z])" + re.escape(term.lower()) + r"(?![a-z])", str(title or "").lower()) is not None


def level_of(title):
    """Index on LADDER named by the title, or None when it names no level."""
    for name, words in LEVEL_WORDS:
        if any(_has(title, w) for w in words):
            return LADDER.index(name)
    return None


def years_of(jobs, today=None):
    """Years between the earliest and latest year mentioned in the work-history dates ('Present' counts as this year)."""
    now = (today or dt.date.today()).year
    yrs = []
    for j in jobs or []:
        d = str(j.get("dates", ""))
        yrs += [int(y) for y in re.findall(r"\b(19[89]\d|20\d\d)\b", d)]
        if re.search(r"present|current|now|today", d, re.I):
            yrs.append(now)
    return max(yrs) - min(yrs) if yrs else None


def suggest_levels(jobs, today=None):
    """Levels to offer the user, from work history: years of experience and the levels named by their past titles.
    No history -> internship and entry. The user confirms; nothing is applied from this alone."""
    past = [i for i in (level_of(j.get("title", "")) for j in jobs or []) if i is not None]
    yrs = years_of(jobs, today)
    if yrs is None and not past:
        return ["intern", "entry"]
    by_years = 0 if yrs is None else (1 if yrs < 3 else 2 if yrs < 6 else 3 if yrs < 10 else 4)
    top = max(past + [by_years])
    return [LADDER[max(top, 1)]]


def check(title, cfg, model=None):
    """None when the title is relevant, else the reason it was routed to review."""
    if not cfg.get("enabled", True):
        return None
    keep = [t.lower() for t in cfg.get("include", [])]
    if any(_has(title, t) for t in keep):
        return None
    want = [LADDER.index(x) for x in cfg.get("levels", []) if x in LADDER]
    lv = level_of(title)
    if want and lv is not None and min(abs(lv - w) for w in want) > 1:
        return f"level: the title says {LEVEL_LABEL[LADDER[lv]].lower()}, you want " + ", ".join(LEVEL_LABEL[LADDER[w]].lower() for w in sorted(want))
    for t in cfg.get("exclude", []):
        if _has(title, t):
            return f"title says '{t}'"
    gone = {t.lower() for t in cfg.get("forgotten", [])}
    for r in (model or {}).get("rules", []):
        if r["term"] not in gone and _has(title, r["term"]):
            return (f"AI suggested avoiding '{r['term']}'" if r.get("ai")
                    else f"learned: you marked {r['neg']} roles with '{r['term']}' as not a match")
    return None
