"""Learning from the user's choices (DESIGN section 10). Offline statistical core: every button press is recorded as a
signal (title terms, reason, outcome; never posting text or notes), and train() turns repeated, consistent signals into
explainable rules. The user can forget any rule. Optional AI suggestions are separate (suggest_ai) and off by default."""
import datetime as dt
import re
from collections import Counter

from cc import store
from cc.rules import job_key
from cc import kitprefs, reasons, relevance

MIN_SIGNALS = 2                      # a rule needs this many consistent skips and no contradicting positive
NOT_FIT = reasons.GROUPS["title"]
POSITIVE = {"applied", "interview", "interviewing", "callback", "next_round", "offer", "gate_apply", "added_manually"}  # adding a role by hand says you want roles like it


def _put(conn, coll, doc_id, data, by="learned"):
    cur = store.get(conn, coll, doc_id)
    store.set_doc(conn, coll, doc_id, data, if_version=cur["version"] if cur else None, by=by)


def kind_of(action, reason=""):
    """'positive', 'not_fit' or '' (a skip about pay, place or timing says nothing about the title)."""
    if action in POSITIVE:
        return "positive"
    if action in ("skipped", "gate_skip"):
        g = reasons.group_of(reason)
        return "not_fit" if g == "title" else "pref" if g in reasons.PREF else ""      # pref: shown as suggestions, never a rule
    return ""


def record(conn, role, action, reason="", today=None):
    """One signal per (role, action); safe to repeat. No-op when the press carries no learning value."""
    kind = kind_of(action, reason)
    if not kind or not role:
        return
    r = role["data"] if "data" in role else role
    sid = f"{role.get('id') or job_key.doc_id(r.get('key', ''))}-{action}"
    _put(conn, "signals", sid, {"kind": kind, "action": action, "reason": str(reason)[:80], "terms": relevance.terms(r.get("title")),
                                "source": r.get("source", ""), "level": r.get("level", ""), "band": r.get("label", ""),
                                "group": reasons.group_of(reason), "company": str(r.get("company", ""))[:60],
                                "place": _place(r.get("location")),
                                "at": (today or dt.date.today()).isoformat()})


def _place(loc):
    """The city part of a location, lowercase, so 'Toronto, ON' and 'toronto' count together."""
    return re.sub(r"\s+", " ", str(loc or "").split(",")[0].strip().lower())[:40]


def seed(conn):
    """Signals from roles the user already decided on, so learning starts with history. Idempotent."""
    n = 0
    for d in store.list_docs(conn, "roles"):
        r = d["data"]
        st = str(r.get("status", "")).lower()
        if st.startswith("skipped (") and "gate" not in st.split("(")[1][:4]:
            record(conn, d, "skipped", st[st.index("(") + 1:].rstrip(")"))
            n += 1
        elif r.get("date_applied") or st.split(" ")[0] in ("applied", "interviewing", "interview", "offer"):
            record(conn, d, "applied")
            n += 1
    return n


OUTCOME_DIMS = ("source", "level", "band")


def outcomes(signals):
    """Counts of applied-style (pos) and not-a-fit (neg) signals per source, level and fit band. Only groups with at least
    MIN_SIGNALS signals are kept, so one press never shows as a pattern."""
    tally = {}
    for d in signals:
        k = d.get("kind")
        if k not in ("positive", "not_fit"):
            continue
        for dim in OUTCOME_DIMS:
            v = str(d.get(dim) or "").strip()[:40]
            if v:
                c = tally.setdefault((dim, v), {"pos": 0, "neg": 0})
                c["pos" if k == "positive" else "neg"] += 1
    rows = [{"dim": dim, "value": v, **c} for (dim, v), c in tally.items() if c["pos"] + c["neg"] >= MIN_SIGNALS]
    return sorted(rows, key=lambda r: (r["dim"], -(r["pos"] + r["neg"]), r["value"]))[:60]


DROP_AT = 3                          # a source needs this many not-a-match choices and no application to be suggested for dropping


def source_hints(rows):
    """Suggestions only (never changes the sources): drop a source that gave only not-a-match roles, favour one that gave
    only applications. `id` is the key used by Forget/Restore."""
    out = []
    for o in rows:
        if o["dim"] != "source" or o["value"] == "manual":   # "manual" is you, not a feed to keep or drop
            continue
        if o["neg"] >= DROP_AT and not o["pos"]:
            out.append({"id": ("source:" + o["value"]).lower()[:40], "source": o["value"], "kind": "drop", "pos": 0, "neg": o["neg"]})
        elif o["pos"] >= MIN_SIGNALS and not o["neg"]:
            out.append({"id": ("source:" + o["value"]).lower()[:40], "source": o["value"], "kind": "keep", "pos": o["pos"], "neg": 0})
    return out


PREF_AT = {"pay": 3, "level": 3, "place": 3, "contract": 3, "auth": 2, "company": 2, "agency": 2}
PREF_TEXT = {
    "pay": ("You passed on {n} roles because of pay.", "Raise your pay floor, or send roles that show no pay to review.", "Setup > Search rules"),
    "level": ("You passed on {n} roles as the wrong level (too junior or too senior).", "Check the levels you saved.", "Learning > Levels"),
    "place": ("You passed on {n} roles in {key} because of the place.", "Remove it from your places, or require remote.", "Setup > Search rules"),
    "contract": ("You passed on {n} roles because of contract versus permanent.", "Set the work type you want.", "Setup > Search rules"),
    "auth": ("You passed on {n} roles because of work authorization.", "Check the authorization routes in your rules.", "Setup > Search rules"),
    "company": ("You passed on {key} {n} times for company or culture.", "Add it to the companies to skip.", "Learning > words"),
    "agency": ("You passed on {n} recruiter or agency postings (latest: {key}).", "Consider dropping the source that sends them.", "Learning > sources"),
}


def pref_hints(sigs):
    """Suggestions from reasons that are not title words (pay, place, level, ...). Repeated reasons only; never changes a setting."""
    by = {}
    for d in sigs:
        g = d.get("group")
        if d.get("kind") != "pref" or g not in PREF_AT:
            continue
        key = str(d.get("place") if g == "place" else d.get("company") if g in ("company", "agency") else "")
        by.setdefault((g, key if g in ("place", "company") else ""), []).append(d)
    out = []
    for (g, key), items in sorted(by.items()):
        if len(items) < PREF_AT[g]:
            continue
        head, tip, where = PREF_TEXT[g]
        last = str(items[-1].get("company", "")) if g == "agency" else key
        out.append({"id": f"pref:{g}:{key}".lower()[:40], "group": g, "n": len(items), "where": where,
                    "text": head.format(n=len(items), key=last or "this") + " " + tip})
    return out


FEED_MIN, FEED_SHARE = 10, 0.8


def _n(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def record_feeds(conn, feeds):
    """Add one run's per-source counts (seen, added, dup, held, gated, and which gate failed) to the running totals."""
    if not feeds:
        return
    tot = _feeds(store.get(conn, "learned", "feeds"))
    for src, f in feeds.items():
        if not isinstance(f, dict):
            continue
        t = dict(tot.get(src) or {"seen": 0, "added": 0, "dup": 0, "held": 0, "gated": 0, "gates": {}})
        for k in ("seen", "added", "dup", "held", "gated"):
            t[k] = _n(t.get(k)) + _n(f.get(k))
        gates = dict(t.get("gates") or {})
        for g, n in (f.get("gates") or {}).items():
            gates[str(g)[:20]] = _n(gates.get(str(g)[:20])) + _n(n)
        t["gates"] = gates
        tot[src] = t
    keep = dict(sorted(tot.items(), key=lambda kv: -kv[1]["seen"])[:20])
    try:
        _put(conn, "learned", "feeds", {"sources": keep})
    except store.Conflict:
        pass                                          # a lost count changes nothing that matters


def feed_hints(conn):
    """A source whose roles mostly fail the same rule: tighten the alert there or change the rule. Suggestion only."""
    cur = store.get(conn, "learned", "feeds")
    out = []
    for src, t in sorted(_feeds(cur).items()):
        if t["seen"] < FEED_MIN or not t["gates"]:
            continue
        gate, n = max(t["gates"].items(), key=lambda kv: kv[1])
        if t["gated"] / t["seen"] >= FEED_SHARE:
            out.append({"id": f"feed:{src}".lower()[:40], "n": t["gated"], "where": "Setup > Mail and boards",
                        "text": f"{t['gated']} of the {t['seen']} roles from {src} failed your {gate} rule. Narrow the alert or search at the source, or change the rule."})
    return out


def _feeds(cur):
    """The saved per-source counts, cleaned so a corrupt record cannot break the page."""
    src = cur["data"].get("sources") if cur else None
    out = {}
    for s, t in (src.items() if isinstance(src, dict) else ()):
        if isinstance(t, dict):
            g = t.get("gates") if isinstance(t.get("gates"), dict) else {}
            out[str(s)[:30]] = {**{k: _n(t.get(k)) for k in ("seen", "added", "dup", "held", "gated")},
                                "gates": {str(a)[:20]: _n(b) for a, b in g.items()}}
    return out


def feed_totals(conn):
    cur = store.get(conn, "learned", "feeds")
    return [dict(t, source=s) for s, t in sorted(_feeds(cur).items(), key=lambda kv: -kv[1]["seen"])]


def record_missed(conn, role_id, fields, found_via, why, today=None):
    """A role the user added by hand: remember how they found it and why it was not already on the board. Counts only; no posting text."""
    _put(conn, "signals", f"{role_id}-missed", {"kind": "missed", "action": "added_manually", "terms": relevance.terms(fields.get("title")),
                                               "via": str(found_via or "")[:30].lower(), "why": str(why)[:120],
                                               "at": (today or dt.date.today()).isoformat()})


def why_missed(conn, title):
    """'rules: <why>' when the user's own rules would have hidden this title, else 'never seen' (no feed showed it)."""
    try:
        why = relevance.check(title, relevance.settings(conn), model(conn))
    except Exception:
        why = ""
    return f"rules: {why}" if why else "never seen"


MISSED_VIA_AT, MISSED_TERM_AT = 2, 2


def missed_hints(sigs, include=()):
    """What manually added roles say about the search: titles my rules would hide, sources I do not read, words to keep."""
    ms = [dict(d, why=str(d.get("why") or ""), via=str(d.get("via") or ""),
               terms=[t for t in d.get("terms") or [] if isinstance(t, str)] if isinstance(d.get("terms"), list) else [])
          for d in sigs if d.get("kind") == "missed"]
    out = []
    rules = [d for d in ms if str(d.get("why", "")).startswith("rules")]
    if rules:
        out.append({"id": "missed:rules", "n": len(rules), "where": "Learning > words",
                    "text": f"{len(rules)} role(s) you added by hand would have been hidden by your own rules ({rules[-1]['why'][7:60]}). Check the words that send a title to review."})
    via = Counter(d.get("via") or "other" for d in ms if d.get("why") == "never seen")
    for v, n in sorted(via.items()):
        if n >= MISSED_VIA_AT:
            out.append({"id": f"missed:via:{v}"[:40], "n": n, "where": "Setup > Mail and boards",
                        "text": f"{n} roles you added by hand came from '{v}' and none of my sources showed them. Forward that site's job alert emails, or add the company's job board."})
    terms = Counter(t for d in ms for t in set(d.get("terms", [])))
    new = sorted(t for t, n in terms.items() if n >= MISSED_TERM_AT and t not in set(include))
    if new:
        out.append({"id": "missed:terms", "n": len(new), "where": "Learning > words", "add_include": new[:6],
                    "text": "Words in the roles you added by hand: " + ", ".join(new[:6]) + ". Add them to the words that always keep a title?"})
    return out


def train(conn, today=None):
    """Rebuild the learned model from all signals. A term becomes a rule with >=2 not-fit roles and no positive role."""
    neg, pos, why = Counter(), Counter(), {}
    sigs = [s["data"] for s in store.list_docs(conn, "signals")]
    for d in sigs:
        for t in d.get("terms", []):
            if d.get("kind") == "positive":
                pos[t] += 1
            elif d.get("kind") == "not_fit":
                neg[t] += 1
                why.setdefault(t, Counter())[d.get("reason", "")] += 1
    rules = [{"term": t, "neg": n, "pos": pos[t], "why": dict(why[t])} for t, n in neg.most_common()
             if n >= MIN_SIGNALS and not pos[t]]
    model = {"rules": rules[:200], "pos_terms": sorted(pos)[:300], "outcomes": outcomes(sigs),
             "trained": (today or dt.date.today()).isoformat(), "signals": sum(neg.values()) + sum(pos.values())}
    _put(conn, "learned", "model", model)
    return model


def model(conn):
    """Trained rules plus AI-suggested ones (kept apart so retraining never drops or trusts them)."""
    d, a = store.get(conn, "learned", "model"), store.get(conn, "learned", "ai")
    rules = list(d["data"]["rules"]) if d else []
    have = {r["term"] for r in rules}
    return {"rules": rules + [{"term": t, "neg": 0, "pos": 0, "why": {}, "ai": True} for t in (a["data"]["terms"] if a else []) if t not in have],
            "pos_terms": list(d["data"].get("pos_terms", [])) if d else [],
            "outcomes": list(d["data"].get("outcomes", [])) if d else [],
            "trained": d["data"].get("trained", "") if d else "", "signals": d["data"].get("signals", 0) if d else 0}


RANK_CAP = 15
RANK_STEP = 5


def rank_adjust(mdl, title, forgotten=()):
    """Fit-point adjustment for ordering only: +5 per term seen in a role you applied to, -5 per learned not-a-match term, each
    side capped at 15. Returns (points, short reason). Never hides or re-gates a role."""
    ts = set(relevance.terms(title)) - set(forgotten)
    if not mdl or not ts:
        return 0, ""
    bad = ts & {r["term"] for r in mdl.get("rules", []) if not r.get("ai")}
    good = (ts & set(mdl.get("pos_terms", []))) - bad
    adj = min(RANK_CAP, RANK_STEP * len(good)) - min(RANK_CAP, RANK_STEP * len(bad))
    why = "like roles you applied to" if adj > 0 else "like roles you rejected" if adj < 0 else ""
    return adj, why


def forget(conn, term, restore=False):
    cfg = relevance.settings(conn)
    gone = {t for t in cfg["forgotten"] if t != term} | (set() if restore else {str(term).lower()[:40]})
    cur = store.get(conn, "config", "relevance")
    data = dict(cur["data"]) if cur else {}
    data["forgotten"] = sorted(gone)
    _put(conn, "config", "relevance", data, by="owner")


def set_ai(conn, on):
    cur = store.get(conn, "config", "relevance")
    data = dict(cur["data"]) if cur else {}
    data["ai"] = bool(on)
    _put(conn, "config", "relevance", data, by="owner")


def suggest_ai(conn, ai):
    """Optional: an AI reads recent titles and the user's reasons (no names, posting text or notes) and proposes extra
    title terms. They are added as rules flagged 'ai' and can be forgotten like any other. Needs ai=(mode, runner)."""
    from cc import ai as ai_layer
    cfg = relevance.settings(conn)
    if not (ai and cfg.get("ai")):
        return []
    lines = [f"{d['data']['kind']} ({d['data'].get('reason', '')}): {' '.join(d['data'].get('terms', []))}"
             for d in store.list_docs(conn, "signals")][-80:]
    if len(lines) < 4:
        return []
    mode, runner = ai
    r = ai_layer.run_task("learning_suggest", {"signals": lines}, mode, runner)
    if r.status != "ok" or r.source != "ai":
        return []
    liked = {t for s in store.list_docs(conn, "signals") if s["data"].get("kind") == "positive" for t in s["data"].get("terms", [])}
    cur = store.get(conn, "learned", "ai")
    terms = sorted({t.lower() for t in r.data["avoid"] if re.fullmatch(r"[a-z][a-z+#.-]{2,30}", t.lower()) and t.lower() not in liked}
                   | set(cur["data"]["terms"] if cur else []))
    _put(conn, "learned", "ai", {"terms": terms[:100]})
    return terms


def tick_hour(conn, stamp):
    """Count one button press in the hour-of-day tally (local time, from the action's saved time). Counts only; no role data."""
    m = re.match(r"\d{4}-\d\d-\d\dT(\d\d):", str(stamp))
    if not m or int(m.group(1)) > 23:
        return
    cur = store.get(conn, "learned", "timing")
    hours = list(cur["data"].get("hours", [])) if cur else []
    hours = (hours + [0] * 24)[:24]
    hours[int(m.group(1))] += 1
    try:
        _put(conn, "learned", "timing", {"hours": hours})
    except store.Conflict:
        pass                                  # a lost tick changes nothing that matters


TIMING_MIN = 10                       # presses needed before a pattern is shown
TIMING_SHARE = 0.6                    # share of presses that must fall in the busiest 3-hour window


def timing_hint(hours):
    """The busiest 3-hour window when it holds most presses. A suggestion only (nudges and the run can be set to fit it)."""
    hours = (list(hours or []) + [0] * 24)[:24]
    n = sum(hours)
    if n < TIMING_MIN:
        return None
    start = max(range(22), key=lambda h: sum(hours[h:h + 3]))
    share = sum(hours[start:start + 3]) / n
    if share < TIMING_SHARE:
        return None
    return {"id": "timing:window", "start": start, "end": start + 3, "share": round(share * 100), "n": n}


def _timing(conn, gone):
    d = store.get(conn, "learned", "timing")
    h = timing_hint(d["data"].get("hours") if d else None)
    return dict(h, forgotten=h["id"] in gone) if h else None


def _kit_phrases(conn):
    d = store.get(conn, "learned", "kitprefs")
    return list(d["data"].get("phrases", [])) if d else []


def _sigs(conn):
    return [s["data"] for s in store.list_docs(conn, "signals")]


def view(conn, jobs=None):
    """What the Learning page shows: rules with their evidence, the editable lists, target levels and the AI toggle.
    `suggested` is what the work history implies; it is only a suggestion until the user saves levels."""
    cfg = relevance.settings(conn)
    gone, m = set(cfg["forgotten"]), model(conn)
    return {"rules": [dict(r, forgotten=r["term"] in gone) for r in m["rules"]], "forgotten": sorted(gone),
            "exclude": cfg["exclude"], "include": cfg["include"], "targets": cfg["targets"], "levels": cfg["levels"],
            "ladder": [{"id": k, "label": relevance.LEVEL_LABEL[k]} for k in relevance.LADDER],
            "suggested": relevance.suggest_levels(jobs or []), "years": relevance.years_of(jobs or []),
            "hints": [dict(h, forgotten=h["id"] in gone) for h in source_hints(m["outcomes"])], "kitprefs": [dict(p, id=kitprefs.key(p["phrase"]), forgotten=kitprefs.key(p["phrase"]) in gone) for p in _kit_phrases(conn)],
            "prefs": [dict(h, forgotten=h["id"] in gone) for h in pref_hints(_sigs(conn))],
            "missed": [dict(h, forgotten=h["id"] in gone) for h in missed_hints(_sigs(conn), cfg["include"]) + feed_hints(conn)],
            "feeds": feed_totals(conn),
            "timing": _timing(conn, gone), "ai": bool(cfg["ai"]), "enabled": bool(cfg["enabled"]), "outcomes": m["outcomes"], "trained": m["trained"], "signals": m["signals"]}


def set_list(conn, field, items):
    """User-edited word lists (short lowercase words or phrases, at most 100); `targets` keeps the typed case, at most 10."""
    if field not in ("exclude", "include", "targets"):
        raise ValueError("unknown list")
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        raise ValueError("list of words expected")
    keep_case = field == "targets"
    clean = sorted({re.sub(r"\s+", " ", x.strip() if keep_case else x.strip().lower())[:60 if keep_case else 40] for x in items if x.strip()})[:10 if keep_case else 100]
    cur = store.get(conn, "config", "relevance")
    data = dict(cur["data"]) if cur else {}
    data[field] = clean
    _put(conn, "config", "relevance", data, by="owner")
    return clean


def set_levels(conn, levels):
    """Target levels from relevance.LADDER (several allowed; empty = no level gating)."""
    if not isinstance(levels, list) or not all(isinstance(x, str) and x in relevance.LADDER for x in levels):
        raise ValueError("levels: pick from the list")
    cur = store.get(conn, "config", "relevance")
    data = dict(cur["data"]) if cur else {}
    data["levels"] = [k for k in relevance.LADDER if k in levels]
    _put(conn, "config", "relevance", data, by="owner")
