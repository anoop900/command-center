"""Learning from the user's choices (DESIGN section 10). Offline statistical core: every button press is recorded as a
signal (title terms, reason, outcome; never posting text or notes), and train() turns repeated, consistent signals into
explainable rules. The user can forget any rule. Optional AI suggestions are separate (suggest_ai) and off by default."""
import datetime as dt
import re
from collections import Counter

from cc import store
from cc.rules import job_key
from cc import kitprefs, relevance

MIN_SIGNALS = 2                      # a rule needs this many consistent skips and no contradicting positive
NOT_FIT = ("not a match", "level wrong", "too technical", "not interested", "irrelevant")
POSITIVE = {"applied", "interview", "interviewing", "callback", "next_round", "offer", "gate_apply", "added_manually"}  # adding a role by hand says you want roles like it


def _put(conn, coll, doc_id, data, by="learned"):
    cur = store.get(conn, coll, doc_id)
    store.set_doc(conn, coll, doc_id, data, if_version=cur["version"] if cur else None, by=by)


def kind_of(action, reason=""):
    """'positive', 'not_fit' or '' (a skip about pay, place or timing says nothing about the title)."""
    if action in POSITIVE:
        return "positive"
    if action in ("skipped", "gate_skip") and any(n in str(reason).lower() for n in NOT_FIT):
        return "not_fit"
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
                                "at": (today or dt.date.today()).isoformat()})


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
