"""Non-job mail the user should read -> today.inbox (max 8) and LinkedIn job search (agent mode only).
Mail text is untrusted: it reaches an AI only fenced, and only sender, subject and a short snippet are kept."""
from email.utils import parseaddr

from cc import ai as ai_layer, store
from cc.ingest import alerts, mailparse as mp, status

MAX = 8


def _candidates(msgs):
    """Mail that is not a job alert and not application-status mail (those are handled elsewhere)."""
    out = []
    for m in msgs:
        if alerts.source_of(m) or status.parse_status(m) or status.parse_offer(m):
            continue
        out.append(m)
    return out


def inbox(conn, msgs, ai=None):
    """-> list of {from, subject, why, link}, at most MAX. Without an AI only mail from known contacts is listed."""
    known = {str(d["data"].get("email", "")).lower() for d in store.list_docs(conn, "contacts")} - {""}
    cands = _candidates(msgs or [])[:40]
    if not cands:
        return []
    items = []
    for m in cands:
        name, addr = parseaddr(str(m.get("From", "")))
        items.append({"from": mp.clean(name or addr, 80), "subject": mp.clean(str(m.get("Subject", "")), 120),
                      "snippet": mp.clean(mp.text_of(m)[:300], 200), "known": addr.lower() in known})
    mode, runner = ai or ("no-ai", ai_layer.none_runner)
    r = ai_layer.run_task("inbox_triage", {"mail": items}, mode, runner)
    if r.status != "ok":
        return []
    return [{"from": items[x["n"] - 1]["from"], "subject": items[x["n"] - 1]["subject"], "why": mp.clean(x["why"], 200),
             "link": ""} for x in r.data["items"][:MAX]]


def linkedin(search, ai):
    """LinkedIn search results as intake postings. Needs a browsing AI; otherwise [] (alert emails still work)."""
    if not ai or not str(search).strip():
        return []
    mode, runner = ai
    r = ai_layer.run_task("linkedin_jobs", {"search": str(search)}, mode, runner)
    if r.status != "ok" or r.source != "ai":
        return []
    out = []
    for p in r.data["postings"]:
        url = p["url"] if p["url"].startswith("https://") else "https://" + p["url"]
        out.append({"source": "linkedin", "title": p["title"], "company": p["company"],
                    "location": p.get("location", ""), "url": url})
    return out
