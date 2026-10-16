"""cc run morning|midday without AI: buttons -> mail/calendar -> roles -> board/roles + board/today."""
import datetime as dt

from cc import coach, kitlink, kitprefs, learned, store, tracker
from cc.ingest import fetchposting
from cc.runs import actions, autokits, backfill, board, contacts, intake, mailflow, reviews, triage

NO_SCORING = "Gates are not set up (scoring.toml missing), so new postings were not added."


def _note(text, kind="fyi"):
    return {"text": text[:160], "kind": kind, "key": ""}


def run(conn, mode="morning", today=None, now=None, msgs=None, ics_text=None, score_cfg=None, profile="", opener=None,
        errors=(), tracker_path=None, ai=None, links="", coach_dir=None, files=None):
    """msgs: parsed email messages; ics_text: calendar feed; both optional and already fetched (errors: fetch notes).
    Midday skips the board search. ai: (mode, runner) for the optional AI steps (inbox triage, LinkedIn search, Friday
    review); None runs without AI. links: the user's own website/LinkedIn text for the monthly brand check."""
    today = today or dt.date.today()
    a = actions.apply_actions(conn, today)
    notes = list(a["notes"]) + [_note(e) for e in errors]
    todos, agenda_items, postings, inbox = [], [], [], []
    age = ((score_cfg or {}).get("boards") or {}).get("max_age_days", 14)
    if msgs:
        m = mailflow.apply_messages(conn, msgs, today, max_age_days=age)
        notes += m["notes"]
        todos += m["todos"]
        postings += m["postings"]
        contacts.apply(conn, msgs, today)
        inbox = triage.inbox(conn, msgs, ai)
    if ics_text:
        agenda_items, _ = mailflow.agenda(ics_text, today)
    added = 0
    if score_cfg:
        if mode == "morning":
            res = intake.fetch_boards(conn, score_cfg, opener, today)
            postings += res["postings"]
            postings += triage.linkedin(((score_cfg.get("linkedin") or {}).get("search", "")), ai)
            notes += [_note(f"Board {b['company']} failed: {b['error']}") for b in res["boards"] if b.get("error")]
        learned.seed(conn)
        learned.train(conn, today)
        try:
            learned.suggest_ai(conn, ai)            # only when the user turned AI learning on
        except Exception as e:                       # a bonus step; it must not stop the board
            notes.append(_note(f"AI learning suggestions failed: {type(e).__name__}"))
        added = len(intake.intake(conn, score_cfg, profile, postings, today)["added"])
        added += len(intake.regate(conn, score_cfg)["to_new"])    # roles whose only failure was junk text or a forgotten rule
        if mode == "morning":
            try:
                notes += backfill.run(conn, today, (lambda u: fetchposting.fetch(u, lambda x: opener(x, None))) if opener else None)
            except Exception as e:  # a bonus step; it must not stop the board
                notes.append(_note(f"Fetching job descriptions failed: {type(e).__name__}"))
        if mode == "morning" and ai and files:
            try:
                notes += autokits.run(conn, score_cfg, profile, ai, *files)
            except Exception as e:  # kits are a bonus; they must not stop the board from being written
                notes.append(_note(f"Automatic kits failed: {type(e).__name__}"))
    elif postings:
        notes.append(_note(NO_SCORING, "action"))
    dismissed = actions.remember_dismissed(conn, today, a["dismissed"])
    if coach_dir:
        try:
            kitlink.link_all(conn, coach_dir)
        except (store.Conflict, OSError):
            pass
    rows = [d["data"] for d in store.list_docs(conn, "roles")]
    if tracker_path:
        try:
            if today.weekday() == 0:  # Monday: keep last week's file as the newest of four copies
                tracker.rotate_copies(tracker_path)
            tracker.export_rows(tracker.rows_from_store(conn), tracker_path)
        except tracker.TrackerError as e:
            if "openpyxl" not in str(e):  # without openpyxl the Excel copy is simply not offered
                notes.append(_note(f"Excel copy not updated: {e}"))
    if mode == "morning":
        try:
            rv = reviews.run(conn, today, ai, links)
            if rv["note"]:
                notes.append(_note(rv["note"]))
        except Exception as e:  # the review is a bonus; it must not stop the board from being written
            notes.append(_note(f"Weekly review failed: {type(e).__name__}"))
    if mode == "morning" and coach_dir:
        try:
            coach.plan(conn, coach_dir, today)
            if ai:
                coach.reword(conn, today, *ai)
        except Exception as e:  # coaching is a bonus; it must not stop the board from being written
            notes.append(_note(f"Daily improvement plan failed: {type(e).__name__}"))
    try:
        kitprefs.train(conn)                        # words you took out of kits by hand; offline
    except Exception as e:                           # a bonus step; it must not stop the board
        notes.append(_note(f"Kit edit learning failed: {type(e).__name__}"))
    run_doc = {"run": mode, "inbox": inbox, "notes": notes, "dismissed": dismissed, "todos": todos, "agenda": agenda_items,
               "week_events": mailflow.week_events(conn, today)}
    roles_doc, today_doc = board.build_docs(rows, run_doc, today, now, board.ranker(conn), board.max_age(score_cfg))
    open_n = sum(1 for x in roles_doc["roles"] if x["stage"] in ("new", "ready", "applied", "interviewing"))
    today_doc["summary"] = f"{open_n} open roles, {len(today_doc['todos'])} to do."
    board.write_docs(conn, roles_doc, today_doc)
    return {"roles": len(roles_doc["roles"]), "todos": len(today_doc["todos"]), "notes": len(today_doc["notes"]), "added": added}
