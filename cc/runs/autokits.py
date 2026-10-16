"""Autopilot only: fit-score new board roles with the AI, then build kits for the best ones (DESIGN section 4)."""
from cc import ai as aimod, jobs, modes, store
from cc.rules import score

MAX_SCORE = 15      # postings scored per run: keeps one morning run's AI use bounded
MAX_BUILD = 5       # kits built per run


def _note(text):
    return {"text": text[:160], "kind": "fyi", "key": ""}


def run(conn, score_cfg, profile, ai, db_file, cfg_file):
    """Returns notes. Does nothing unless the mode lets the scheduler call an AI. Roles keep their posting text from intake."""
    if not ai or not score_cfg or not modes.schedules_ai(ai[0]):
        return []
    mode, runner = ai
    dims = list(score_cfg["scoring"]["weights"])
    todo = [d for d in store.list_docs(conn, "roles")
            if d["data"].get("status") == "build on request" and not d["data"].get("fit_pct")
            and not d["data"].get("kit") and str(d["data"].get("posting") or "").strip()]
    todo.sort(key=lambda d: d["data"].get("date_found", ""), reverse=True)
    posts, by_key = [], {}
    for d in todo[:MAX_SCORE]:
        r = aimod.run_task("score", {"posting": d["data"]["posting"], "dims": dims, "profile": profile}, mode, runner)
        if r.status != "ok" or r.source != "ai":
            continue        # a keyword guess is not a fit score; leave the role for the user
        p = dict(r.data, key=d["id"], title=d["data"].get("title", ""), channel=d["data"].get("channel", ""))
        try:
            score.decide(p, score_cfg)
        except (ValueError, KeyError, TypeError):
            continue
        posts.append(p)
        by_key[d["id"]] = d
    if not posts:
        return [_note(f"Fit scoring: {len(todo)} role(s) waiting, none scored.")] if todo else []
    out, built, notes = score.decide_batch(posts, score_cfg), 0, []
    for dec in sorted(out, key=lambda x: -x["overall"]):
        d = by_key[dec["key"]]
        jobs._patch(conn, "roles", d["id"], {"fit_pct": str(dec["overall"]), "label_mode": "ai"})
        if dec["build_kit"] and built < MAX_BUILD:
            built += 1
            if jobs.run_now(db_file, cfg_file, "kit", d["id"], runner).get("state") != "done":
                notes.append(_note(f"Kit for {d['data'].get('company', '')} did not finish. Open the role and try again."))
    notes.append(_note(f"Fit scored {len(posts)} role(s); built {built} kit(s)."))
    return notes
