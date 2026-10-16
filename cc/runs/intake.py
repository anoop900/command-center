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


def fetch_boards(conn, config, opener=None, today=None):
    """Poll every board in the targets collection with the [boards] rules from the scoring config."""
    targets = [d["data"] for d in store.list_docs(conn, "targets") if d["data"].get("token")]
    return boards.poll(targets, config.get("boards", {}), opener, today)


def intake(conn, config, profile_text, postings, today=None):
    """Write new postings as roles. Idempotent: a posting already in roles (by job_key or URL id) is skipped."""
    today = (today or dt.date.today()).isoformat()
    existing = [(d["data"].get("key") or d["id"], d["data"].get("company", ""), d["data"].get("title", ""), d["data"].get("link", ""))
                for d in store.list_docs(conn, "roles")]
    rel = (relevance.settings(conn), learned.model(conn))
    terms = terms_of(profile_text) | set(relevance.terms(" ".join(rel[0]["targets"])))
    out = {"added": [], "skipped": [], "duplicates": 0}
    for p in postings:
        co, title, url = p.get("company", ""), p.get("title", ""), p.get("url", "")
        if not co or not title:
            continue
        if job_key.find_duplicate(co, title, url, existing):
            out["duplicates"] += 1
            continue
        k = job_key.key(co, title)
        gates, fails, flags = gate(p, config, rel)
        url = url or p.get("apply_url", "")
        link = url if str(url).startswith("http") else ""
        data = {"key": k, "date_found": today, "company": co, "title": title, "location": p.get("location", ""),
                "work_mode": "remote" if p.get("remote") else "", "pay": p.get("pay_text", ""), "fit_pct": "",
                "decision": "", "source": "board", "channel": CHANNEL, "link": link, "ats": p.get("ats", ""),
                "label": keyword_label(p, terms), "label_mode": "no-ai",
                "gates": {n: g["verdict"] for n, g in gates.items()}, "flags": flags,
                "reason_or_gaps": "; ".join(fails or flags)}
        if str(p.get("text") or "").strip():
            data["posting"] = str(p["text"])[:200_000]      # kept so Build now does not ask for a paste
        if fails:
            data.update(status="skipped (gate)", decision="skip", gate_review="pending")
            out["skipped"].append(k)
        else:
            data.update(status="build on request", decision="queued")
            out["added"].append(k)
        store.set_doc(conn, "roles", job_key.doc_id(k), data, by="intake")
        existing.append((k, co, title, link))
    return out
