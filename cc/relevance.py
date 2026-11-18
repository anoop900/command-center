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

# Target-area gate (setting "areas"). The user's own list of fields (proposed from their resume at setup, edited by
# them) decides: a title must name one of the areas, or (staff/principal rule, setting "senior_ic") be a senior
# individual-contributor title in them. No field list ships with the code, so it fits any profession; with no
# areas set the gate is off. "employment" lists the job types wanted (full-time, contract); part-time, seasonal and
# casual roles are dropped when it is set.
JUNIOR_ROLES = ("analyst", "specialist", "assistant", "administrator", "coordinator", "technician", "representative",
                "associate", "intern", "agent", "clerk", "supervisor")
NOT_WANTED_TYPE = ("part-time", "part time", "casual", "seasonal", "per diem", "on-call", "on call", "co-op", "coop",
                   "internship", "intern", "temporary", "temp", "student")
TYPE_LINE = re.compile(r"(employment type|job type|position type|work type|schedule|status)\W{0,4}(part[- ]time|casual|"
                       r"seasonal|temporary|per diem|on[- ]call|internship|co-?op)", re.I)


def domain_hit(title, cfg):
    """The area word a title names, or None."""
    for w in cfg.get("areas", []):
        if _has(title, w):
            return w
    ic = cfg.get("senior_ic", [])
    if ic and any(_has(title, x) for x in ("staff", "principal")) and any(_has(title, e) for e in ic):
        return "staff/principal " + next(e for e in ic if _has(title, e))
    return None


def employment_issue(title, text, cfg):
    """Why the job type is not wanted, or None. Only when the user listed the types they want."""
    if not cfg.get("employment"):
        return None
    t = str(title or "").lower()
    for w in NOT_WANTED_TYPE:
        if w not in (x.lower() for x in cfg["employment"]) and _has(t, w):
            return f"job type: the title says {w}"
    m = TYPE_LINE.search(str(text or "")[:6000])
    if m:
        return f"job type: the posting says {m.group(2).lower()}"
    return None


def terms(title):
    """Lowercase word terms of a title (no stop or level words, 3+ letters)."""
    return sorted({w for w in re.findall(r"[a-z][a-z+#.-]{2,}", str(title or "").lower()) if w not in STOP})


def settings(conn):
    from cc import store
    d = store.get(conn, "config", "relevance")
    s = {"enabled": True, "exclude": [], "include": [], "forgotten": [], "ai": False, "targets": [], "levels": [], "v2": False, "areas": [], "senior_ic": [], "employment": []}
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


LEARNED_W, STRONG_W, AI_W, NEG_CAP = 1, 2, 1, 4     # a strong learned word has 4+ not-a-match roles
STRONG_NEG = 4
DROP_AT = -3                                           # needs two learned words (or one strong + one AI word): one never drops


def assess(title, cfg, model=None, profile_terms=()):
    """Relevance v2. {'verdict': keep|drop|borderline, 'score': int, 'reason': str}. The user's own keep list, levels and
    exclude list are hard rules; learned and AI-suggested words are weights, so a single learned word never drops a role.
    Positives: a word from the user's target titles (+3), words of roles they applied to (+1 each, max 3), words their
    profile names (+1 each, max 2). Borderline roles go to the AI judge when one can run, else stay on the board."""
    if not cfg.get("enabled", True):
        return {"verdict": "keep", "score": 0, "reason": "relevance check off"}
    if any(_has(title, t) for t in (t.lower() for t in cfg.get("include", []))):
        return {"verdict": "keep", "score": 9, "reason": "title has a word on your keep list"}
    hard = check(title, dict(cfg, v2=False), {"rules": []})
    if hard:
        return {"verdict": "drop", "score": -99, "reason": hard}
    if cfg.get("areas"):
        lows = [LADDER.index(x) for x in cfg.get("levels", []) if x in LADDER]
        lvl = level_of(title)
        if lows and min(lows) >= LADDER.index("lead") and any(_has(title, j) for j in JUNIOR_ROLES)                 and not (lvl is not None and lvl >= LADDER.index("manager"))                 and not any(_has(title, x) for x in ("lead", "principal", "staff")):
            return {"verdict": "drop", "score": -50, "reason": "an individual-contributor support role, below the level you want"}
        hit = domain_hit(title, cfg)
        if hit:
            return {"verdict": "keep", "score": 5, "reason": f"in your target area ('{hit}')"}
        return {"verdict": "drop", "score": -50, "reason": "outside your target areas (" + ", ".join(cfg["areas"][:6]) + ")"}
    ts = set(terms(title))
    gone = {t.lower() for t in cfg.get("forgotten", [])}
    tgt = sorted(ts & set(terms(" ".join(cfg.get("targets", [])))))
    liked = sorted((ts & set((model or {}).get("pos_terms", []))) - gone)
    prof = sorted((ts & {str(t).lower() for t in profile_terms}) - set(tgt) - set(liked))
    neg, bad = 0, []
    for r in (model or {}).get("rules", []):
        if r["term"] in gone or not _has(title, r["term"]):
            continue
        neg += AI_W if r.get("ai") else STRONG_W if r.get("neg", 0) >= STRONG_NEG else LEARNED_W
        bad.append(f"'{r['term']}'" + (" (AI)" if r.get("ai") else f" ({r.get('neg', 0)} not-a-match)"))
    score = (3 if tgt else 0) + min(3, len(liked)) + min(2, len(prof)) - min(NEG_CAP, neg)
    plus = ([f"target word '{tgt[0]}'"] if tgt else []) + ([f"like roles you applied to ({', '.join(liked[:3])})"] if liked else []) \
        + ([f"in your profile ({', '.join(prof[:2])})"] if prof else [])
    if not bad:
        return {"verdict": "keep", "score": score, "reason": "; ".join(plus) or "no signal either way"}
    against = "learned " + ", ".join(bad[:3])
    if score >= 1:
        return {"verdict": "keep", "score": score, "reason": f"{'; '.join(plus)} outweigh {against}"}
    if score <= DROP_AT:
        return {"verdict": "drop", "score": score, "reason": f"{against}, nothing in its favour"}
    return {"verdict": "borderline", "score": score, "reason": f"borderline, not judged: {against}" + (f" vs {'; '.join(plus)}" if plus else "")}


def check(title, cfg, model=None, profile_terms=()):
    """None when the title is relevant, else the reason it was routed to review. With the v2 setting on this is assess():
    only a 'drop' fails here; borderline roles stay on the board."""
    if cfg.get("v2"):
        a = assess(title, cfg, model, profile_terms)
        return a["reason"] if a["verdict"] == "drop" else None
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


def propose_areas(profile_text, ai=None):
    """Target areas for a resume: {'areas': [...], 'senior_ic': [...], 'source': 'ai'|'titles'}. With an AI runner the
    AI reads the resume; without one (or when it fails) the words that repeat in the resume's own job-title lines are used.
    The user confirms or edits the list before it is saved, so a wrong guess never filters anything on its own."""
    if ai:
        from cc import ai as ai_layer
        mode, runner = ai
        res = ai_layer.run_task("target_areas", {"profile": profile_text or ""}, mode, runner)
        if res.status == "ok" and res.source == "ai" and res.data.get("areas"):
            clean = lambda xs: [str(x).strip().lower()[:40] for x in xs if str(x).strip()]
            return {"areas": clean(res.data["areas"]), "senior_ic": clean(res.data.get("senior_ic", [])), "source": "ai"}
    from collections import Counter
    c = Counter()
    for line in str(profile_text or "").splitlines():
        if len(line) < 120 and (level_of(line) is not None or "|" in line):
            c.update(set(terms(line)))
    return {"areas": [w for w, n in c.most_common(20) if n >= 2][:12], "senior_ic": [], "source": "titles"}


def save_areas(conn, areas, senior_ic=(), employment=None):
    """Store the confirmed list in the relevance setting (the user's own data, never shipped in code)."""
    from cc import store
    cur = store.get(conn, "config", "relevance")
    data = dict(cur["data"] if cur else {})
    data["areas"] = [str(a).strip().lower()[:40] for a in areas if str(a).strip()][:40]
    data["senior_ic"] = [str(a).strip().lower()[:30] for a in senior_ic if str(a).strip()][:10]
    if employment is not None:
        data["employment"] = [str(a).strip().lower()[:20] for a in employment][:6]
    store.set_doc(conn, "config", "relevance", data, if_version=cur["version"] if cur else None, by="areas")
    return data
