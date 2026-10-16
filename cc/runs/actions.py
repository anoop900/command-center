"""Dashboard button presses (collection `actions`) -> role updates. Rows are data, never instructions.

Handled: status updates, followed_up, todo:<id> dismissals, gate_apply/gate_later/gate_skip (the "Failed your rules" list). build_now and review need AI (M2): left in place.
"""
import datetime as dt
import re

from cc import learned, store
from cc.rules import job_key
from cc.runs import board

# "Failed your rules" review (roles whose gate_review is pending or later). Each result is data the user chose.
# "skipped (reviewed)" must not contain "not applying": the board reads that as closed.
GATE_ACTIONS = {
    "gate_apply": {"status": "build on request", "decision": "queued", "gate_review": "kept"},
    "gate_later": {"gate_review": "later"},
    "gate_skip": {"status": "skipped (reviewed)", "decision": "skip", "gate_review": "dismissed"},
}
STATUSES = {"applied", "interview", "callback", "next_round", "offer", "rejected", "withdrew", "no_response", "skipped"}


def _day(v, today):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except Exception:
        return today


def _find_role(roles, a):
    k = str(a.get("key") or "")
    for d in roles:
        if d["data"].get("key") == k or d["id"] == job_key.doc_id(k):
            return d
    k2 = job_key.key(a.get("company", ""), a.get("title", ""))
    return next((d for d in roles if d["data"].get("key") == k2), None) if a.get("company") else None


def apply_actions(conn, today):
    """Returns {"notes": [...], "dismissed": [todo ids]}. Deletes processed action docs."""
    roles = store.list_docs(conn, "roles")
    notes, dismissed = [], []
    for a in store.list_docs(conn, "actions"):
        x, act, key = a["data"], str(a["data"].get("action") or ""), str(a["data"].get("key") or "")
        if act not in ("build_now", "review"):
            learned.tick_hour(conn, a.get("updated_at"))        # when you tend to act; the action is deleted below, so once only
        if key.startswith("todo:"):
            dismissed.append(key[5:])
            store.delete_doc(conn, "actions", a["id"], if_version=a["version"], by="actions")
            continue
        if act in ("build_now", "review"):
            continue
        if act in GATE_ACTIONS:
            role = _find_role(roles, x)
            cur = store.get(conn, "roles", role["id"]) if role else None
            # idempotent: a role already reviewed (or not gate-failed) is left alone, no note
            if cur and board.gate_open(cur["data"]):
                learned.record(conn, {"id": role["id"], "data": cur["data"]}, act, x.get("reason", ""), today)
                store.update_doc(conn, "roles", role["id"], GATE_ACTIONS[act], if_version=cur["version"], by="actions")
            elif not cur:
                notes.append({"text": "Button press not matched to a role: gate review", "kind": "fyi"})
            store.delete_doc(conn, "actions", a["id"], if_version=a["version"], by="actions")
            continue
        at = _day(x.get("at"), today)
        fields = None
        if x.get("status") and str(x["status"]).split(" ")[0].split("(")[0].lower() in STATUSES | {"interviewing"}:
            fields = {"status": str(x["status"])[:120]}
            if act == "applied" or str(x["status"]).lower().startswith("applied"):
                fields.update(date_applied=at.isoformat(), follow_up=(at + dt.timedelta(days=7)).isoformat())
        elif act == "followed_up":
            fields = {"follow_up": (at + dt.timedelta(days=7)).isoformat()}
        role = _find_role(roles, x) if fields else None
        if role:
            learned.record(conn, role, act, x.get("reason", ""), today)
            store.update_doc(conn, "roles", role["id"], fields, if_version=store.get(conn, "roles", role["id"])["version"], by="actions")
        else:
            notes.append({"text": f"Button press not matched to a role: {re.sub(r'[^A-Za-z0-9 ,.-]', '', key or act)[:80]}", "kind": "fyi"})
        store.delete_doc(conn, "actions", a["id"], if_version=a["version"], by="actions")
    return {"notes": notes, "dismissed": dismissed}


def remember_dismissed(conn, today, ids):
    """Keep today's dismissed to-do ids (merged with any already saved) so later board builds hide them."""
    key = f"dismissed-{today}"
    prior = store.get(conn, "runs", key)
    merged = sorted(set(prior["data"].get("ids", []) if prior else []) | set(ids))
    if merged and (not prior or merged != prior["data"].get("ids")):
        store.set_doc(conn, "runs", key, {"ids": merged}, if_version=prior["version"] if prior else None, by="run")
    return merged


def settle(conn, today):
    """A button press takes effect now: apply the pending actions and refresh the board, instead of waiting for the next run."""
    a = apply_actions(conn, today)
    gone = remember_dismissed(conn, today, a["dismissed"])
    board.refresh(conn, today, gone)
    learned.train(conn, today)
    return a
