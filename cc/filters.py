"""Data behind the Filters page: failed roles grouped by the reason they failed, roles hidden as Not a Match, and one
read/write entry point over the rule editors that already exist (rules_cfg for pay and location, learned for words,
levels and targets). Nothing here changes how roles are gated."""
import re

from . import learned, relevance, rules_cfg, store
from .runs import board

GATES = ("pay", "location", "work_auth", "relevance")
_SPLIT = re.compile(r"(?:^|;\s*)(?=(?:" + "|".join(GATES) + r"):)")
_LEARNED = re.compile(r"learned: you marked (\d+) roles with '([^']+)'")
MAX_ROLES_PER_GROUP = 200


def is_hidden(r):
    """True for a role the user marked Not a Match (kept in the database so learning still sees it)."""
    return board.is_hidden(r)


def reasons_of(text):
    """'location: onsite in Dallas; relevance: learned: ...' -> [{'gate', 'kind', 'detail', 'term'?}]. Unknown text -> one 'other'."""
    out = []
    for part in _SPLIT.split(str(text or "")):
        part = part.strip()
        if not part:
            continue
        gate, _, detail = part.partition(":")
        gate, detail = gate.strip(), detail.strip()
        if gate not in GATES:
            out.append({"gate": "other", "kind": "other", "detail": part})
            continue
        item = {"gate": gate, "kind": gate, "detail": detail}
        if gate == "relevance":
            m = _LEARNED.match(detail)
            if m:
                item.update(kind="learned", term=m.group(2), count=int(m.group(1)))
            elif detail.startswith("level:"):
                item["kind"] = "level"
            elif detail.startswith("AI suggested"):
                item["kind"] = "ai"
            elif detail.startswith("title says"):
                item["kind"] = "excluded_word"
        out.append(item)
    return out or [{"gate": "other", "kind": "other", "detail": ""}]


def _brief(r, rid):
    return {"id": rid, "key": r.get("key", ""), "company": r.get("company", ""), "title": r.get("title", ""),
            "location": r.get("location", ""), "source": r.get("source", ""), "found": r.get("date_found", ""),
            "link": r.get("link", "") if str(r.get("link", "")).startswith("http") else "",
            "gate_review": r.get("gate_review", ""), "status": r.get("status", "")}


def failed_by_reason(docs):
    """docs: [{'id', 'data'}] of roles. Roles still waiting in Failed your rules, grouped by reason kind and, for
    learned words, by word. A role with two reasons appears in both groups. -> {'total', 'groups': [...]}."""
    groups, total = {}, 0
    for d in docs:
        r = d["data"]
        if is_hidden(r) or not board.gate_open(r) or "skip" not in str(r.get("status", "")).lower():
            continue
        total += 1
        for x in reasons_of(r.get("reason_or_gaps", "")):
            gid = x["kind"] + (":" + x["term"] if x.get("term") else "")
            g = groups.setdefault(gid, {"id": gid, "kind": x["kind"], "gate": x["gate"], "term": x.get("term", ""),
                                        "sample": x["detail"], "count": 0, "roles": []})
            g["count"] += 1
            if len(g["roles"]) < MAX_ROLES_PER_GROUP:
                g["roles"].append(_brief(r, d["id"]))
    ordered = sorted(groups.values(), key=lambda g: (-g["count"], g["id"]))
    return {"total": total, "groups": ordered}


def hidden_roles(docs):
    return sorted((_brief(d["data"], d["id"]) for d in docs if is_hidden(d["data"])), key=lambda b: b["found"], reverse=True)


def view(conn, data_dir, jobs=None):
    docs = store.list_docs(conn, "roles")
    return {"failed": failed_by_reason(docs), "hidden": hidden_roles(docs),
            "rules": rules_cfg.load(data_dir), "relevance": learned.view(conn, jobs or [])}


def apply(conn, data_dir, data, jobs=None):
    """One write entry point. data: {action, ...}. Rule changes go through the existing validators; the result is view()."""
    act = data.get("action")
    if act == "rules":
        rules_cfg.save(data_dir, data.get("rules"))
    elif act in ("forget", "restore"):
        if not isinstance(data.get("term"), str):
            raise ValueError("term: text expected")
        learned.forget(conn, data["term"], restore=act == "restore")
    elif act in ("exclude", "include", "targets"):
        learned.set_list(conn, act, data.get("items"))
    elif act == "levels":
        learned.set_levels(conn, data.get("items"))
    elif act == "unhide":
        rid = data.get("id")
        cur = store.get(conn, "roles", rid) if isinstance(rid, str) else None
        if cur is None or not is_hidden(cur["data"]):
            raise ValueError("id: no hidden role with that id")
        store.update_doc(conn, "roles", rid, {"status": "skipped (unhidden)", "decision": "skip"}, if_version=cur["version"], by="owner")
    else:
        raise ValueError("unknown action")
    return view(conn, data_dir, jobs)
