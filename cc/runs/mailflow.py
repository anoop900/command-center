"""Parsed mail and calendar events -> role updates, intake postings, agenda, per-day counts.

Everything here is derived from untrusted text: it is matched and stored as data, never executed or fetched.
"""
import datetime as dt
import re
from email.utils import parseaddr, parsedate_to_datetime

from cc import store
from cc.ingest import alerts, ics, mailparse as mp, status
from cc.rules import job_key

ACTIVE = ("applied", "build on request", "ready", "interview")


def msg_date(msg, today):
    try:
        return parsedate_to_datetime(str(msg.get("Date"))).date()
    except Exception:
        return today


def _match(roles, company, title):
    """Role doc for (company, title); with no title, the single active role at that company, else None."""
    keys = [(d["data"].get("key") or d["id"], d["data"].get("company", ""), d["data"].get("title", ""), d["data"].get("link", ""))
            for d in roles]
    if title:
        k = job_key.find_duplicate(company, title, "", keys)
        return next((d for d in roles if (d["data"].get("key") or d["id"]) == k), None) if k else None
    cn = job_key.company_norm(company)
    hits = [d for d in roles if job_key.company_norm(d["data"].get("company", "")) == cn
            and any(a in d["data"].get("status", "").lower() for a in ACTIVE)]
    return hits[0] if len(hits) == 1 else None


def _note(text):
    return {"text": mp.clean(text, 160), "kind": "fyi", "key": ""}


def _set(conn, doc, fields):
    store.update_doc(conn, "roles", doc["id"], fields, if_version=store.get(conn, "roles", doc["id"])["version"], by="mail")


def _record(conn, day, mid, kind):
    """Per-day reply/interview counts, keyed by message id so a re-run counts nothing twice."""
    i = f"events-{day}"
    cur = store.get(conn, "runs", i)
    ids = dict(cur["data"].get("ids", {})) if cur else {}
    if ids.get(mid) != kind:
        ids[mid] = kind
        store.set_doc(conn, "runs", i, {"ids": ids}, if_version=cur["version"] if cur else None, by="mail")


LABEL = {"revised": "Revised offer", "signed": "Signed offer letter", "offer": "Offer"}


def _offer(conn, msg, st, day, out):
    """An offer email. ATS senders and known contacts are trusted to name a company; anyone else only raises a warning
    note (fake job offers are a common scam, so nothing is recorded for them). Amounts and dates are never read out of
    the text: the user fills them in. An existing offer record is only ever appended to, never overwritten."""
    name, addr = parseaddr(str(msg.get("From", "")))
    addr = addr.lower()
    company = st["company"]
    if not st.get("ats"):
        known = next((c for c in store.list_docs(conn, "contacts") if str(c["data"].get("email", "")).lower() == addr), None)
        if known is None:
            who = mp.clean(name, 40) or addr.rpartition("@")[2]
            out["notes"].append({"text": mp.clean(f"Possible offer email from {who}: open it. Nothing was recorded.", 160),
                                 "kind": "action", "key": ""})
            return
        company = company or mp.clean(str(known["data"].get("company", "")), 80)
    if not company:
        out["notes"].append({"text": "Offer email with no company name: open it and add the offer by hand.", "kind": "action", "key": ""})
        return
    label, title = LABEL[st["variant"]], st["title"]
    role = _match(store.list_docs(conn, "roles"), company, title)
    if role and role["data"].get("status", "").lower() != "offer":
        _set(conn, role, {"status": "offer"})
    cn = job_key.company_norm(company)
    existing = next((o for o in store.list_docs(conn, "offers")
                     if job_key.company_norm(o["data"].get("company", "")) == cn and not o["data"].get("archived")), None)
    line = f"{label} from email {day.isoformat()}"
    if existing is None:
        oid = re.sub(r"[^a-z0-9]+", "-", f"{company}-{title}".lower()).strip("-")[:120] or "offer"
        store.set_doc(conn, "offers", oid, {"company": company, "title": title, "type": "", "notes": line}, by="mail")
    elif line not in str(existing["data"].get("notes", "")):
        old = str(existing["data"].get("notes", ""))
        store.update_doc(conn, "offers", existing["id"], {"notes": (old + "; " if old else "") + line},
                         if_version=existing["version"], by="mail")
    slug = re.sub(r"[^a-z0-9]+", "-", f"{company}-{st['variant']}".lower()).strip("-")[:60]
    out["todos"].append({"id": "offer-" + slug, "type": "offer", "key": role["data"].get("key", "") if role else "", "priority": 1,
                         "title": f"{label}: {company}", "tag": "offer", "tagClass": "hot",
                         "detail": "Read it and decide. The numbers and the answer are yours; nothing was suggested or sent."})


def week_events(conn, today):
    mon = today - dt.timedelta(days=today.weekday())
    out = []
    for n in range(7):
        day = (mon + dt.timedelta(days=n)).isoformat()
        cur = store.get(conn, "runs", f"events-{day}")
        kinds = list(cur["data"].get("ids", {}).values()) if cur else []
        if kinds:
            out.append({"date": day, "replies": kinds.count("rejected"), "interviews": kinds.count("interview")})
    return out


def apply_messages(conn, msgs, today, max_age_days=14):
    """-> {"postings": [...], "notes": [...], "todos": [...]}. Status mail updates roles; alert mail yields postings,
    except alerts older than max_age_days (a re-delivered or forwarded old alert lists postings that are long gone)."""
    out = {"postings": [], "notes": [], "todos": []}
    for m in msgs:
        day, mid = msg_date(m, today), mp.message_id(m)
        if max_age_days is None or (today - day).days <= max_age_days:
            out["postings"] += alerts.parse_alert(m)
        st = status.parse_status(m) or status.parse_offer(m)
        if st and st["kind"] == "offer":
            _offer(conn, m, st, day, out)
            continue
        if not st or st["kind"] == "unknown" or not st["company"]:
            continue
        role = _match(store.list_docs(conn, "roles"), st["company"], st["title"])
        label = f"{st['company']}, {st['title']}".strip(", ")
        if st["kind"] == "received":
            if role is None and st["title"]:
                k = job_key.key(st["company"], st["title"])
                store.set_doc(conn, "roles", job_key.doc_id(k), {
                    "key": k, "date_found": day.isoformat(), "company": st["company"], "title": st["title"], "status": "applied",
                    "date_applied": day.isoformat(), "follow_up": (day + dt.timedelta(days=7)).isoformat(),
                    "source": "email", "channel": st["ats"], "decision": ""}, by="mail")
            elif role is None:
                out["notes"].append(_note(f"Application received at {st['company']}: add the role title."))
            elif not role["data"].get("date_applied") and "interview" not in role["data"].get("status", "").lower():
                _set(conn, role, {"status": "applied", "date_applied": day.isoformat(),
                                  "follow_up": (day + dt.timedelta(days=7)).isoformat()})
        elif role is None:
            out["notes"].append(_note(f"{st['kind'].title()} email from {label}: no matching role."))
        elif st["kind"] == "rejected":
            if "rejected" not in role["data"].get("status", ""):
                _set(conn, role, {"status": "rejected"})
            _record(conn, day, mid, "rejected")
        elif st["kind"] == "interview":
            if "interview" not in role["data"].get("status", "").lower():
                _set(conn, role, {"status": "interviewing (invite by email)"})
            _record(conn, day, mid, "interview")
            out["todos"].append({"id": "prep-" + re.sub(r"[^a-z0-9]+", "-", label.lower())[:60], "type": "prep", "key": "",
                                 "priority": 1, "title": f"Interview invite: {label}", "detail": "Reply and book a time.",
                                 "tag": "interview", "tagClass": "hot"})
    return out


def agenda(ics_text, today, days=7):
    """Interview and recruiter events in the next `days` days as agenda items. Event text is data only."""
    events, skipped = ics.parse_ics(ics_text, today, days)
    items = [{"title": mp.clean(e["summary"], 120), "when": e["start"], "detail": "", "type": "interview"}
             for e in ics.interviews(events)]
    return items, skipped
