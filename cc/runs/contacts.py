"""Recruiter and hiring-manager mail -> contacts (runbook 2c). Never messages anyone; nudge_off is never touched."""
import datetime as dt
import re

from cc import store
from cc.ingest import mailparse as mp, recruiter
from cc.rules import job_key
from cc.runs import mailflow


def _id(name, company):
    return job_key.doc_id(f"{name} {company}".strip())


def _find(contacts, email, cid):
    return next((c for c in contacts if str(c["data"].get("email", "")).lower() == email or c["id"] == cid), None)


def _touch(conn, c, day):
    d = dict(c["data"])
    old = str(d.get("last_touch", ""))[:10]
    if old and old >= day and "snooze_until" not in d:
        return False
    d["last_touch"] = max(old, day)
    d.pop("snooze_until", None)
    store.set_doc(conn, "contacts", c["id"], d, if_version=c["version"], by="mail")
    return True


def apply(conn, msgs, today):
    out = {"added": 0, "touched": 0}
    for m in msgs:
        day = mailflow.msg_date(m, today).isoformat()
        contacts = store.list_docs(conn, "contacts")
        addr = re.sub(r"^.*<|>.*$", "", str(m.get("From", ""))).strip().lower()
        c = _find(contacts, addr, "")
        info = None if c else recruiter.classify(m)
        if c is None and info:
            cid = _id(info["name"], info["company"])
            c = _find(contacts, info["email"], cid)
            if c is None:
                store.set_doc(conn, "contacts", cid, {
                    "name": info["name"], "company": info["company"], "role": "", "type": info["kind"], "channel": "email",
                    "email": info["email"], "last_touch": day, "cadence": 7, "notes": f"First email {day}."}, by="mail")
                out["added"] += 1
                continue
        if c is not None and _touch(conn, c, day):
            out["touched"] += 1
    return out
