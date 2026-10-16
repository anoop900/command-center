"""Morning run: fetch the job description for open roles that have none, so Build kit needs no paste (DESIGN section 10, 2026-10-14)."""
from cc import jobs, store
from cc.ingest import fetchposting

MAX_PER_RUN = 25
OPEN = ("build on request", "ready", "")


def run(conn, today, fetch=None):
    """Returns notes. One attempt per role per day; a role that cannot be read keeps its reason for the paste panel."""
    fetch = fetch or fetchposting.fetch
    todo = [d for d in store.list_docs(conn, "roles")
            if str(d["data"].get("status") or "").lower() in OPEN and not d["data"].get("kit")
            and not str(d["data"].get("posting") or "").strip() and d["data"].get("posting_try") != str(today)
            and not fetchposting.why_not(d["data"].get("link"))]
    todo.sort(key=lambda d: d["data"].get("date_found", ""), reverse=True)
    got = 0
    for d in todo[:MAX_PER_RUN]:
        text, why = fetch(d["data"]["link"])
        fields = {"posting_try": str(today), "posting_why": why}
        if text:
            fields["posting"], got = text[:jobs.MAX_POSTING], got + 1
        jobs._patch(conn, "roles", d["id"], fields)
    return [{"text": f"Job descriptions fetched for {got} of {min(len(todo), MAX_PER_RUN)} role(s).", "kind": "fyi", "key": ""}] if todo else []
