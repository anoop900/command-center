"""Board postings -> roles: dedup by job_key, gate with score.py, keyword label when no AI is configured.

Posting text is untrusted data: it is matched against keywords and never executed, and URLs in it are not fetched.
"""
import datetime as dt
import re

from cc import learned, relevance, store
from cc.ingest import boards
from cc.ingest.alerts import is_badge
from cc.rules import job_key, score

CHANNEL = "Company board"
STOP = {"with", "from", "that", "this", "have", "will", "your", "team", "years", "work", "experience", "lead",
        "also", "the", "and", "for", "our", "you", "are", "all", "over", "into", "their", "about", "building", "built", "led"}
COUNTRIES = (("canada", "CA"), ("united states", "US"), ("usa", "US"), ("u.s.", "US"),
             ("united kingdom", "GB"), ("germany", "DE"), ("ireland", "IE"))
FX_CODES = ("CAD", "USD", "EUR", "GBP")


def _country(text):
    t = (text or "").lower()
    return next((c for k, c in COUNTRIES if k in t), "")


def location_of(p, cfg_loc):
    """Posting location text + remote flag -> the dict the location gate reads."""
    text = p.get("location", "")
    country = _country(text)
    if p.get("remote") or "remote" in text.lower():
        return {"type": "remote", "country": country}
    if not text or is_badge(text):
        return None
    region = next((r for r in cfg_loc.get("onsite_regions", []) if r.lower() in text.lower()), "")
    if not region and any(w in text.lower() for w in ("toronto", "mississauga")):
        region = cfg_loc.get("region_name", "")
    return {"type": "onsite", "country": country or (cfg_loc["home_country"] if region else ""), "region": region or text}


def pay_of(text):
    """'$200K - $250K', 'CAD 180000-220000 per-year-salary' -> {min, max, currency, basis} or None."""
    nums = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?", text or ""):
        v = float(m.group(1).replace(",", ""))
        nums.append(v * 1000 if m.group(2) else v)
    nums = [n for n in nums if n >= 15]
    if not nums:
        return None
    cur = next((c for c in FX_CODES if c in (text or "").upper()), None)
    hour = bool(re.search(r"hour|/\s*hr|per-hour", text or "", re.I))
    return {"min": min(nums), "max": max(nums), "currency": cur, "basis": "hour" if hour else "year"}


def terms_of(profile_text):
    return {w for w in re.findall(r"[a-z][a-z0-9+#.]{2,}", (profile_text or "").lower()) if w not in STOP}


def keyword_label(p, terms, strong=6, possible=3):
    """No-AI fit label from profile keyword overlap. Not a score; the label says so."""
    hay = set(re.findall(r"[a-z][a-z0-9+#.]{2,}", f"{p.get('title', '')} {p.get('text', '')}".lower()))
    hits = len(terms & hay)
    return "strong" if hits >= strong else "possible" if hits >= possible else "weak"


def gate(p, config, rel=None):
    loc = location_of(p, config["location"])
    post = {"title": p.get("title"), "channel": CHANNEL, "location": loc, "work_auth": {"accepts": "silent"},
            "people_management": bool(re.search(r"director|head of|vp|manager", p.get("title", ""), re.I))}
    routes = config.get("work_authorization", [])
    gates = {"pay": score.gate_pay(pay_of(p.get("pay_text")), config["pay"]),
             "location": score.gate_location(loc, CHANNEL, config["location"], routes),
             "work_auth": score.gate_work_auth(post, config["location"], routes)}
    if rel is not None:
        why = relevance.check(p.get("title", ""), *rel)
        gates["relevance"] = {"verdict": "FAIL" if why else "PASS", "reason": why or "title relevant"}
    fails = [f"{n}: {g['reason']}" for n, g in gates.items() if g["verdict"] == "FAIL"]
    flags = [f"{n}: {g['reason']}" for n, g in gates.items() if g["verdict"] == "FLAG"]
    return gates, fails, flags


def regate(conn, config):
    """Re-run the gates on New roles and on roles waiting in Failed your rules (never on roles the user already decided).
    A New role that now fails goes to review with the reason; a waiting role that no longer fails returns to New."""
    rel = (relevance.settings(conn), learned.model(conn))
    to_review, to_new = [], []
    for d in store.list_docs(conn, "roles"):
        r = d["data"]
        waiting = r.get("gate_review") == "pending"
        fresh = r.get("status") == "build on request" and r.get("decision") == "queued" and not r.get("gate_review")             and not r.get("date_applied") and not r.get("kit")
        if not (waiting or fresh):
            continue
        p = {"title": r.get("title"), "location": r.get("location", ""), "remote": r.get("work_mode") == "remote",
             "pay_text": r.get("pay", "")}
        gates, fails, flags = gate(p, config, rel)
        if fails and fresh:
            r.update(status="skipped (gate)", decision="skip", gate_review="pending", flags=flags, reason_or_gaps="; ".join(fails))
            to_review.append(r.get("key"))
        elif not fails and waiting:
            r.update(status="build on request", decision="queued", gate_review="", flags=flags, reason_or_gaps="; ".join(flags))
            to_new.append(r.get("key"))
        else:
            continue
        r["gates"] = {n: g["verdict"] for n, g in gates.items()}
        store.set_doc(conn, "roles", d["id"], r, if_version=d["version"], by="regate")
    return {"to_review": to_review, "to_new": to_new}


DEFAULT_REAPPLY_DAYS = 90      # an applied role may return as a new role only after this many days...
REPOST_MIN_DAYS = 21           # ...or, once this many days have passed, when the posting carries a new id (a real repost)
_APPLIED_WORDS = ("appl", "interview", "offer", "reject", "withdr", "no response", "declin", "closed")


def reapply_days(cfg):
    v = (cfg or {}).get("reapply_after_days")
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else DEFAULT_REAPPLY_DAYS


def _was_applied(r):
    st = str(r.get("status", "")).lower()
    if "skipped" in st or "not appl" in st:
        return False
    return bool(r.get("date_applied")) or any(w in st for w in _APPLIED_WORDS)


def _day(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def judge_repeat(matches, url, today, days=DEFAULT_REAPPLY_DAYS):
    """A posting matches roles already on file. matches: list of role dicts. Returns ('dup', None) when it is still the
    same open role (or a role you chose to skip), ('hold', role) when you applied recently and it is not a repost, or
    ('repost', role) when enough time has passed or the posting has a new id after at least REPOST_MIN_DAYS."""
    if any(not _was_applied(r) for r in matches):
        return "dup", None
    last = max(matches, key=lambda r: str(r.get("date_applied") or r.get("date_found") or ""))
    since = (today - d).days if (d := _day(last.get("date_applied") or last.get("date_found"))) else 0
    if since >= days:
        return "repost", last
    new_id, old_ids = job_key.url_id(url), {job_key.url_id(r.get("link", "")) for r in matches} - {""}
    if since >= REPOST_MIN_DAYS and new_id and old_ids and new_id not in old_ids:
        return "repost", last
    return "hold", last


def fetch_boards(conn, config, opener=None, today=None):
    """Poll every board in the targets collection with the [boards] rules from the scoring config."""
    targets = [d["data"] for d in store.list_docs(conn, "targets") if d["data"].get("token")]
    return boards.poll(targets, config.get("boards", {}), opener, today)


def intake(conn, config, profile_text, postings, today=None):
    """Write new postings as roles. Idempotent: a posting already in roles (by job_key or URL id) is skipped."""
    today = (today or dt.date.today()).isoformat()
    docs = store.list_docs(conn, "roles")
    existing = [(d["data"].get("key") or d["id"], d["data"].get("company", ""), d["data"].get("title", ""), d["data"].get("link", ""))
                for d in docs]
    by_key = {(d["data"].get("key") or d["id"]): d for d in docs}
    days, now = reapply_days(config), dt.date.fromisoformat(today)
    rel = (relevance.settings(conn), learned.model(conn))
    terms = terms_of(profile_text) | set(relevance.terms(" ".join(rel[0]["targets"])))
    out = {"added": [], "skipped": [], "duplicates": 0, "held": [], "reposts": []}
    feeds = {}                                         # per source: what happened to what it sent (learned.record_feeds)
    def tally(p, what, gate_name=""):
        f = feeds.setdefault(str(p.get("source") or "board")[:30], {"seen": 0, "added": 0, "dup": 0, "held": 0, "gated": 0, "gates": {}})
        f["seen"] += 1
        f[what] += 1
        if gate_name:
            f["gates"][gate_name] = f["gates"].get(gate_name, 0) + 1
    for p in postings:
        co, title, url = p.get("company", ""), p.get("title", ""), p.get("url", "")
        if not co or not title:
            continue
        mine, hits = job_key.match_keys(co, title, url), []
        for ek, ec, et, eu in existing:
            if mine & job_key.match_keys(ec, et, eu):
                d = by_key.get(ek) or store.get(conn, "roles", job_key.doc_id(ek))     # the second is a role added earlier in this batch
                if d:
                    hits.append(d)
        repost_of = None
        if hits:
            verdict, last = judge_repeat([h["data"] for h in hits], url, now, days)
            if verdict == "dup":
                out["duplicates"] += 1
                tally(p, "dup")
                continue
            if verdict == "hold":
                # Applied recently and not a repost: stay off the board, but remember it was seen again.
                h = next(h for h in hits if h["data"] is last)
                data = dict(h["data"], seen_again=int(h["data"].get("seen_again") or 0) + 1, last_seen=today)
                store.set_doc(conn, "roles", h["id"], data, if_version=h["version"], by="intake")
                out["held"].append(data.get("key") or h["id"])
                tally(p, "held")
                continue
            repost_of = last.get("key") or ""
        k = job_key.key(co, title)
        if repost_of:
            k = f"{k}|repost-{today}"
        gates, fails, flags = gate(p, config, rel)
        url = url or p.get("apply_url", "")
        link = url if str(url).startswith("http") else ""
        data = {"key": k, "date_found": today, "company": co, "title": title, "location": p.get("location", ""),
                "work_mode": "remote" if p.get("remote") else "", "pay": p.get("pay_text", ""), "fit_pct": "",
                "decision": "", "source": str(p.get("source") or "board")[:30], "channel": CHANNEL, "link": link, "ats": p.get("ats", ""),
                "label": keyword_label(p, terms), "label_mode": "no-ai",
                "gates": {n: g["verdict"] for n, g in gates.items()}, "flags": flags,
                "reason_or_gaps": "; ".join(fails or flags)}
        if repost_of:
            data.update(reposted_of=repost_of, repost_note=f"You applied to this role before ({repost_of.split('|')[0]}); it looks reposted.")
            out["reposts"].append(k)
        if str(p.get("text") or "").strip():
            data["posting"] = str(p["text"])[:200_000]      # kept so Build now does not ask for a paste
        if fails:
            data.update(status="skipped (gate)", decision="skip", gate_review="pending")
            out["skipped"].append(k)
            tally(p, "gated", fails[0].split(":")[0])
        else:
            data.update(status="build on request", decision="queued")
            out["added"].append(k)
            tally(p, "added")
        store.set_doc(conn, "roles", job_key.doc_id(k), data, by="intake")
        existing.append((k, co, title, link))
    learned.record_feeds(conn, feeds)
    out["feeds"] = feeds
    return out
