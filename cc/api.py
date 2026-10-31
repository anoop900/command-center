"""Everything the engine can do, as plain functions. The MCP server and the CLI are thin wrappers.

Collections follow the dashboard database: roles (one doc per role, fields as make_tracker.COLS),
contacts, targets, tasks, decisions, offers, references, admin, preps, actions, requests, runs.
"""
import datetime as dt
import re
import sys
from pathlib import Path

from . import config, learned, store

from .ingest import boards
from .rules import job_key, score

OPEN = ("new", "ready", "applied", "interviewing", "offer")
CLOSED_WORDS = ("rejected", "closed", "withdrew", "declined", "no response", "skipped", "not applying")


def db_path():
    """CC_HOME env var, else ~/.commandcenter. The data folder lives outside the repo."""
    return config.data_dir() / "commandcenter.db"


def role_id(company, title):
    """Same doc id the dashboard uses: make_tracker.doc_id(job_key.key(company, title))."""
    return re.sub(r"[^a-z0-9]+", "-", job_key.key(company, title).lower()).strip("-")[:120] or "x"


def _is_open(data):
    s = str(data.get("status", "")).lower()
    return not any(w in s for w in CLOSED_WORDS)


# ---- roles -------------------------------------------------------------------------------------
def roles_list(conn, status=None, open_only=False, company=None):
    out = []
    for d in store.list_docs(conn, "roles"):
        r = d["data"]
        if open_only and not _is_open(r):
            continue
        if status and status.lower() not in str(r.get("status", "")).lower():
            continue
        if company and company.lower() not in str(r.get("company", "")).lower():
            continue
        out.append({"id": d["id"], "version": d["version"], **r})
    return out


def role_get(conn, role_id_):
    d = store.get(conn, "roles", role_id_)
    return None if d is None else {"id": d["id"], "version": d["version"], **d["data"]}


def find_duplicate(conn, company, title, url=""):
    existing = [(d["id"], d["data"].get("company", ""), d["data"].get("title", ""), d["data"].get("link", ""))
                for d in store.list_docs(conn, "roles")]
    return job_key.find_duplicate(company, title, url, existing)


def role_upsert(conn, fields, if_version=None, by="claude"):
    """Create a role (no if_version) or merge fields into one (if_version required).
    Refuses to create a role that duplicates an existing one; returns the duplicate's id instead."""
    company, title = fields.get("company"), fields.get("title")
    rid = fields.get("id") or (role_id(company, title) if company and title else None)
    if not rid:
        raise ValueError("company and title are required to create a role")
    fields = {k: v for k, v in fields.items() if k not in ("id", "version")}
    if store.get(conn, "roles", rid) is None:
        dup = find_duplicate(conn, company or "", title or "", fields.get("link", ""))
        if dup:
            return {"created": False, "duplicate_of": dup}
        fields.setdefault("date_found", dt.date.today().isoformat())
        v = store.update_doc(conn, "roles", rid, fields, by=by)
        return {"created": True, "id": rid, "version": v}
    v = store.update_doc(conn, "roles", rid, fields, if_version=if_version, by=by)
    return {"created": False, "id": rid, "version": v}


def add_manual_role(conn, company, title, link="", posting="", by="dashboard", found_via=""):
    """A role the user adds by hand. Their own choice, so it skips the screening gates and waits for a kit build
    ('build on request', same as a kept gate-failed role). -> role_upsert's result."""
    company, title, link, posting = (v.strip() if isinstance(v, str) else v for v in (company, title, link, posting))
    if not all(isinstance(v, str) for v in (company, title, link, posting)):
        raise ValueError("company, title, link and posting must be text")
    if not company or not title or len(company) > 200 or len(title) > 200:
        raise ValueError("company and title are required (200 characters at most)")
    if link and (len(link) > 2000 or not re.match(r"https?://\S+$", link)):
        raise ValueError("the link must be a web address starting with http:// or https://")
    if not link and not posting:
        raise ValueError("add the job link, the job description, or both")
    dup = find_duplicate(conn, company, title, link) or (role_id(company, title) if store.get(conn, "roles", role_id(company, title)) else None)
    if dup:
        have = store.get(conn, "roles", dup)
        return {"created": False, "duplicate_of": dup, "was": (have["data"].get("status", "") if have else "")}
    why = learned.why_missed(conn, title)
    if not isinstance(found_via, str):
        raise ValueError("found_via must be text")
    fields = {"company": company, "title": title, "key": job_key.key(company, title), "source": "manual", "found_via": found_via.strip()[:30],
              "status": "build on request", "decision": "queued", "gate_review": "kept"}
    if link:
        fields["link"] = link
    if posting:
        fields["posting"] = posting[:200000]
    out = role_upsert(conn, fields, by=by)
    if out.get("created"):
        learned.record(conn, {"id": out["id"], "data": fields}, "added_manually")
        learned.record_missed(conn, out["id"], fields, found_via, why)
        learned.train(conn)
    return out


def boards_discover(company, urls=None):
    return boards.discover(company, urls=urls or [])


def boards_poll(targets, rules=None):
    return boards.poll(targets, rules or {})


def score_batch(postings, config_path):
    return score.decide_batch(postings, score.load_config(config_path))


# ---- tasks and contacts ------------------------------------------------------------------------
def tasks_due(conn, on=None):
    on = on or dt.date.today().isoformat()
    out = [{"id": d["id"], "version": d["version"], **d["data"]} for d in store.list_docs(conn, "tasks")
           if not d["data"].get("done") and d["data"].get("due") and d["data"]["due"] <= on]
    return sorted(out, key=lambda t: (t.get("due", ""), t.get("pri", 2)))


def task_add(conn, title, due=None, pri=2, about="", by="claude"):
    tid = re.sub(r"[^a-z0-9]+", "-", f"{title}-{dt.datetime.now():%Y%m%d%H%M%S}".lower()).strip("-")[:120]
    fields = {"title": title, "due": due, "pri": pri, "about": about, "at": dt.date.today().isoformat(), "by": by}
    store.update_doc(conn, "tasks", tid, {k: v for k, v in fields.items() if v not in (None, "")}, by=by)
    return tid


def contacts_due(conn, on=None):
    """Contacts whose nudge is due (last_touch + cadence, or snooze_until), oldest first."""
    on = dt.date.fromisoformat(on) if on else dt.date.today()
    cad = {"recruiter": 7, "hiring": 7, "interviewer": 21, "warm": 21}
    out = []
    for d in store.list_docs(conn, "contacts"):
        c = d["data"]
        if c.get("archived"):
            continue
        if not c.get("last_touch"):
            due = on
        else:
            due = dt.date.fromisoformat(c["last_touch"][:10]) + dt.timedelta(days=int(c.get("cadence") or cad.get(c.get("type"), 21)))
        if c.get("snooze_until") and dt.date.fromisoformat(c["snooze_until"][:10]) > due:
            due = dt.date.fromisoformat(c["snooze_until"][:10])
        if due <= on:
            out.append({"id": d["id"], "version": d["version"], "due": due.isoformat(), **c})
    return sorted(out, key=lambda c: c["due"])


def contact_touch(conn, contact_id, on=None, by="claude"):
    d = store.get(conn, "contacts", contact_id)
    if d is None:
        raise KeyError(contact_id)
    return store.update_doc(conn, "contacts", contact_id,
                            {"last_touch": on or dt.date.today().isoformat(), "snooze_until": None},
                            if_version=d["version"], by=by)


# ---- phone mirror: actions queue ---------------------------------------------------------------
STATUS_FOR = {"applied": "applied", "interview": "interviewing", "callback": "interviewing (call back)",
              "next_round": "interviewing (next round)", "offer": "offer", "rejected": "rejected",
              "no_response": "closed (no response)"}


def actions_apply(conn, actions, by="phone"):
    """Apply dashboard button taps ({id, key, action, status?, reason?, at}) to roles. Idempotent:
    each action id is recorded in collection actions_applied and never applied twice."""
    changes = []
    for a in actions:
        aid = a.get("id") or role_id(a.get("key", ""), a.get("action", "")) + "-" + str(a.get("at", ""))
        if store.get(conn, "actions_applied", aid):
            continue
        key = a.get("key", "")
        result = "ignored"
        if key and not key.startswith("todo:") and "|" in key:
            company, title = key.split("|", 1)
            rid = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:120]
            cur = store.get(conn, "roles", rid)
            status = a.get("status") or STATUS_FOR.get(a.get("action"))
            if a.get("action") in ("skipped", "withdrew") and not a.get("status"):
                status = f"{a['action']} ({a.get('reason') or 'no reason'})"
            if cur and status:
                fields = {"status": status}
                if a.get("action") == "applied":
                    fields["date_applied"] = a.get("at") or dt.date.today().isoformat()
                store.update_doc(conn, "roles", rid, fields, if_version=cur["version"], by=by)
                result = f"{rid}: status -> {status}"
            elif not cur:
                result = f"no role {rid}"
        store.set_doc(conn, "actions_applied", aid, {"action": a, "result": result, "at": store.now()}, by=by)
        changes.append(result)
    return changes


# ---- run heartbeat and exports -----------------------------------------------------------------
def run_heartbeat(conn, run, phase, info=None, on=None):
    """phase 'start', 'finish' or 'fail'. One doc per run per day: runs/<date>-<run>."""
    if phase not in ("start", "finish", "fail"):
        raise ValueError("phase must be 'start', 'finish' or 'fail'")
    on = on or dt.date.today().isoformat()
    doc = f"{on}-{run}"
    fields = {{"start": "started", "finish": "finished", "fail": "failed"}[phase]: store.now(), "run": run, "date": on}
    if phase == "start":
        fields["failed"] = ""                                # a retry the same day clears the earlier failure
    if info:
        fields["info"] = info
    cur = store.get(conn, "runs", doc)
    return store.update_doc(conn, "runs", doc, fields, if_version=cur["version"] if cur else None, by=run)


def last_finished_run(conn, run=None):
    rows = [d["data"] for d in store.list_docs(conn, "runs") if d["data"].get("finished") and (not run or d["data"].get("run") == run)]
    return max(rows, key=lambda r: r["finished"]) if rows else None


def export_markdown(conn, path):
    """Read-only Markdown copy of open roles, for the local disk. Overwritten every time."""
    rows = sorted((r for r in roles_list(conn, open_only=True)),
                  key=lambda r: (str(r.get("status", "")), str(r.get("company", "")).lower()))
    cell = lambda v: str(v or "").replace("|", "/").replace("\n", " ")
    lines = [f"# Pipeline export ({store.now()})", "",
             "Read-only copy written by the engine. Edits here are overwritten.", "",
             "| Company | Role | Status | Fit | Applied | Follow-up |", "|---|---|---|---|---|---|"]
    lines += [f"| {cell(r.get('company'))} | {cell(r.get('title'))} | {cell(r.get('status'))} | "
              f"{cell(r.get('fit_pct'))} | {cell(r.get('date_applied'))} | {cell(r.get('follow_up'))} |" for r in rows]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)
