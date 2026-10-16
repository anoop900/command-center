"""Friday weekly review -> reviews/<date> and reviews/latest (the dashboard reads latest). The numbers come from the
store; an AI, when one runs, only writes what worked, what to stop and the three priorities from those numbers."""
import datetime as dt

from cc import ai as ai_layer, store
from cc.runs import mailflow

FRIDAY = 4


def due(today):
    return today.weekday() == FRIDAY


def first_friday(today):
    return due(today) and today.day <= 7


def _iso(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def gather(conn, today):
    """-> (numbers dict, role summaries). The window is the seven days ending today."""
    since = today - dt.timedelta(days=6)
    roles = [d["data"] for d in store.list_docs(conn, "roles")]
    inwin = lambda r, k: (_iso(r.get(k)) or dt.date.min) >= since
    ev = [e for e in mailflow.week_events(conn, today) if _iso(e["date"]) >= since]
    n = {"found": sum(inwin(r, "date_found") for r in roles),
         "applied": sum(inwin(r, "date_applied") for r in roles),
         "interviews": sum(e["interviews"] for e in ev), "rejections": sum(e["replies"] for e in ev),
         "overdue": sum(1 for r in roles if r.get("status") == "applied" and (_iso(r.get("follow_up")) or dt.date.max) < today),
         "kits_waiting": sum(1 for r in roles if r.get("status") == "build on request")}
    return n, [f"{r.get('company', '')[:40]} | {r.get('title', '')[:60]} | {r.get('status', '')[:30]}" for r in roles[:30]]


def _lines(n):
    return [f"{n['found']} new roles found", f"{n['applied']} applications sent",
            f"{n['interviews']} interview invites", f"{n['rejections']} rejections",
            f"{n['overdue']} follow-ups overdue"]


def _priorities(n):
    out = []
    if n["overdue"]:
        out.append(f"Follow up on {n['overdue']} overdue application{'s' * (n['overdue'] != 1)}.")
    if n["kits_waiting"]:
        out.append(f"Build kits for {n['kits_waiting']} role{'s' * (n['kits_waiting'] != 1)} that are waiting.")
    if n["interviews"]:
        out.append("Prepare for this week's interviews.")
    return out[:3]


def run(conn, today, ai=None, links=""):
    """Write the review for `today` if it is a Friday; idempotent (a second run the same day rewrites the same docs).
    ai: (mode, runner) or None. links: the user's own website/LinkedIn text for the monthly brand check.
    -> {"written": bool, "note": str}"""
    if not due(today):
        return {"written": False, "note": ""}
    n, roles = gather(conn, today)
    mon = today - dt.timedelta(days=today.weekday())
    doc = {"week": f"Week of {mon.isoformat()}", "date": today.isoformat(), "numbers": _lines(n),
           "worked": [], "stop": [], "priorities": _priorities(n)}
    note = ""
    if ai:
        mode, runner = ai
        r = ai_layer.run_task("week_review", {"week": "\n".join(_lines(n) + roles)}, mode, runner)
        if r.status == "ok" and r.source == "ai":
            doc.update(worked=r.data["worked"], stop=r.data["stop"], priorities=r.data["priorities"] or doc["priorities"])
        if first_friday(today):
            if not links.strip():
                note = "Brand check skipped: add your website or LinkedIn address to the profile."
            else:
                b = ai_layer.run_task("brand_check", {"profile": _profile_text(), "links": links}, mode, runner)
                if b.status == "ok" and b.source == "ai":
                    doc.update(brand=b.data["brand"], post_idea=b.data["post_idea"])
                else:
                    note = "Brand check needs an AI that can browse (Autopilot mode)."
    for i in (today.isoformat(), "latest"):
        cur = store.get(conn, "reviews", i)
        store.set_doc(conn, "reviews", i, doc, if_version=cur["version"] if cur else None, by="run")
    return {"written": True, "note": note}


def _profile_text():
    from cc import config
    p = config.data_dir() / "Candidate_Profile.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""
