"""AI second opinion on borderline titles (relevance v2, decision D5). Only roles on the board whose relevance gate
flagged them are sent, newest first, at most MAX_PER_RUN; the call budget still applies. A 'no' moves the role to
Failed your rules with the AI's reason (recoverable there); a 'yes' clears the flag. Unjudged roles stay on the board."""
from cc import ai as ai_layer
from cc import relevance, store

MAX_PER_RUN = 20


def _decisions(conn):
    return [f"{d['data']['kind']} ({d['data'].get('reason', '')}): {' '.join(d['data'].get('terms', []))}"
            for d in store.list_docs(conn, "signals") if d["data"].get("kind") in ("positive", "not_fit")][-60:]


def todo(conn):
    out = [d for d in store.list_docs(conn, "roles")
           if (d["data"].get("gates") or {}).get("relevance") == "FLAG" and not d["data"].get("relevance_judged")
           and d["data"].get("status") == "build on request" and d["data"].get("decision") == "queued"]
    return sorted(out, key=lambda d: str(d["data"].get("date_found", "")), reverse=True)


def run(conn, ai, profile_text, today):
    """Returns notes for the board. ai=(mode, runner); does nothing without it or with v2 off."""
    if not ai or not relevance.settings(conn).get("v2"):
        return []
    mode, runner = ai
    roles, dec, n_yes, n_no, stop = todo(conn), _decisions(conn), 0, 0, ""
    for d in roles[:MAX_PER_RUN]:
        r = d["data"]
        res = ai_layer.run_task("relevance_judge", {"title": str(r.get("title", "")), "posting": str(r.get("posting") or ""),
                                                    "profile": profile_text or "", "decisions": dec}, mode, runner)
        if res.status != "ok" or res.source != "ai":
            stop = res.reason or res.status        # budget reached, CLI failed or AI off: the rest stay on the board
            break
        why = str(res.data.get("why", ""))[:200]
        r = dict(r, relevance_judged=str(today), gates=dict(r.get("gates") or {}))
        r["flags"] = [f for f in r.get("flags", []) if not f.startswith("relevance:")]
        if res.data["fit"] == "no":
            r["gates"]["relevance"] = "FAIL"
            r.update(status="skipped (gate)", decision="skip", gate_review="pending",
                     relevance_why=f"AI judge: {why}", reason_or_gaps=f"relevance: AI judge: {why}")
            n_no += 1
        else:
            r["gates"]["relevance"] = "PASS"
            r.update(relevance_why=f"AI judge: fits. {why}", reason_or_gaps="; ".join(r["flags"]))
            n_yes += 1
        try:
            store.set_doc(conn, "roles", d["id"], r, if_version=d["version"], by="judge")
        except store.Conflict:
            continue                               # the user touched it meanwhile; their action wins
    if not roles:
        return []
    left = len(roles) - n_yes - n_no
    text = f"AI checked {n_yes + n_no} borderline role(s): {n_yes} kept, {n_no} moved to Failed your rules."
    if left:
        text += f" {left} not judged ({stop or 'run limit'}); they stay on the board."
    return [{"text": text[:160], "kind": "fyi", "key": ""}]


def dry_run(conn, profile_text, today, days=7):
    """Relevance v1 against v2 on roles found in the last `days` days, read-only. Effective change counts only roles
    no other gate (pay, location, work authorization) fails, since those stay out either way."""
    from cc.runs import intake
    cfg, mdl = relevance.settings(conn), None
    from cc import learned
    mdl = learned.model(conn)
    since = (today - __import__("datetime").timedelta(days=days - 1)).isoformat()
    prof = intake.terms_of(profile_text)
    rows, table = [], {}
    for d in store.list_docs(conn, "roles"):
        r = d["data"]
        if str(r.get("date_found", "")) < since:
            continue
        old = "fail" if relevance.check(r.get("title", ""), dict(cfg, v2=False), mdl) else "pass"
        new = relevance.assess(r.get("title", ""), cfg, mdl, prof)
        other = [n for n, v in (r.get("gates") or {}).items() if n != "relevance" and v == "FAIL"]
        table[(old, new["verdict"])] = table.get((old, new["verdict"]), 0) + 1
        rows.append({"title": r.get("title", ""), "company": r.get("company", ""), "date": r.get("date_found", ""),
                     "old": old, "new": new["verdict"], "score": new["score"], "reason": new["reason"], "other_fail": other,
                     "user": r.get("decision", "") if r.get("decision") in ("applied", "skip") and not r.get("gate_review") else ""})
    return {"since": since, "total": len(rows), "table": {f"{a}->{b}": n for (a, b), n in sorted(table.items())},
            "changed": [x for x in rows if (x["old"] == "fail") != (x["new"] == "drop")],
            "effective": [x for x in rows if (x["old"] == "fail") != (x["new"] == "drop") and not x["other_fail"]]}
