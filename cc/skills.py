"""Skill inventory (PLAN F1, D7/D8): skills the user has confirmed are true, each with evidence. A miner reads past
resumes, kits and cover letters and only PROPOSES skills; nothing reaches the inventory until the user ticks it.
The inventory is what makes a keyword profile-backed for tailoring, alongside Candidate_Profile.md."""
import datetime as dt
import re
from pathlib import Path

from . import config, resume, store
from .profile import RESUME_DIR, RESUME_EXT
from .kits import ats_check

SKILLS, PROPOSALS = "skills", "skill_proposals"
KINDS = ("have", "adjacent")
MAX_FILES = 2000
SKIP_NAMES = {"resume.baseline.txt"}                 # a text copy of the resume beside it
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_SNIPPET = 200
MAX_EVIDENCE = 300
MAX_TERM = 60
SOURCES_PER_TERM = 3


def _id(term):
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")[:MAX_TERM] or "x"


def _term(x):
    if not isinstance(x, str) or not x.strip():
        raise ValueError("term: text expected")
    t = re.sub(r"\s+", " ", x.strip().lower())
    if len(t) > MAX_TERM:
        raise ValueError(f"term: {MAX_TERM} characters at most")
    return t


def _today(today=None):
    return (today or dt.date.today()).isoformat()


def source_files(home):
    """Resume, kit and cover-letter files to read: the kits folder (the Resumes folder) and uploaded resumes.
    Work folders (.tmp-*) and hidden files are skipped."""
    home = Path(home)
    roots = [config.kits_dir(home), home / RESUME_DIR]
    seen, out = set(), []
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).parts
            if any(part.startswith(".") for part in rel) or p.name.lower() in SKIP_NAMES or p.suffix.lower() not in RESUME_EXT:
                continue
            key = str(p.resolve()).lower()
            if key in seen or not p.is_file():
                continue
            seen.add(key)
            out.append((root, p))
            if len(out) >= MAX_FILES:
                return out
    return out


def terms_in(text):
    """Known skill terms present as whole words (ats_check.TERMS) plus certifications. -> {term: snippet line}."""
    low = text.lower()
    found = {t for t in ats_check.TERMS if ats_check._word(t, low)}
    found.update(c.lower().strip() for c in ats_check.CREDS.findall(text))
    out = {}
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n") if ln.strip()]
    for t in found:
        line = next((ln for ln in lines if ats_check._word(t, ln.lower())), "")
        out[t] = line[:MAX_SNIPPET]
    return out


def _backed(conn, home):
    """Lower-case text of everything already profile-backed: Candidate_Profile.md plus the inventory."""
    p = Path(home) / "Candidate_Profile.md"
    try:
        text = p.read_text(encoding="utf-8", errors="replace").lower() if p.is_file() else ""
    except OSError:
        text = ""
    return text + "\n" + "\n".join(d["data"].get("term", "") for d in store.list_docs(conn, SKILLS))


def _put(conn, coll, doc_id, data):
    cur = store.get(conn, coll, doc_id)
    store.set_doc(conn, coll, doc_id, data, if_version=cur["version"] if cur else None, by="owner")


def mine(conn, home, today=None):
    """Read the source files and record proposals for skills not yet backed or dismissed. Writes only proposals."""
    backed = _backed(conn, home)
    found, read, failed = {}, 0, []
    for root, p in source_files(home):
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("too large")
            text = resume.extract_text(p.name, p.read_bytes())
        except (OSError, ValueError):
            failed.append(str(p.relative_to(root)))
            continue
        read += 1
        for t, snip in terms_in(text).items():
            found.setdefault(t, []).append({"file": str(p.relative_to(root)), "snippet": snip})
    new = 0
    for t, srcs in found.items():
        if ats_check._word(t, backed):
            continue
        cur = store.get(conn, PROPOSALS, _id(t))
        if cur and cur["data"].get("status") in ("dismissed", "accepted"):
            continue
        new += cur is None
        _put(conn, PROPOSALS, _id(t), {"term": t, "status": "new", "files": len(srcs), "found": _today(today),
                                       "sources": srcs[:SOURCES_PER_TERM]})
    return {"files": read + len(failed), "read": read, "failed": failed[:20], "proposed": len(found), "new": new}


def accept(conn, term, evidence="", kind="have", today=None):
    """The user's tick: the skill is true. Evidence defaults to the proposal's first snippet; one is required."""
    t = _term(term)
    if kind not in KINDS:
        raise ValueError("kind: have or adjacent")
    prop = store.get(conn, PROPOSALS, _id(t))
    ev = evidence.strip() if isinstance(evidence, str) else ""
    src = ""
    if prop and prop["data"].get("sources"):
        first = prop["data"]["sources"][0]
        ev = ev or first.get("snippet", "")
        src = first.get("file", "")
    if not ev:
        raise ValueError("evidence: say where this skill was used")
    _put(conn, SKILLS, _id(t), {"term": t, "kind": kind, "evidence": ev[:MAX_EVIDENCE], "source": src or "added by you",
                                "added": _today(today)})
    if prop:
        _put(conn, PROPOSALS, _id(t), dict(prop["data"], status="accepted"))


def dismiss(conn, term, restore=False):
    t = _term(term)
    prop = store.get(conn, PROPOSALS, _id(t))
    if prop is None:
        raise ValueError("term: no proposal with that name")
    _put(conn, PROPOSALS, _id(t), dict(prop["data"], status="new" if restore else "dismissed"))


def remove(conn, term):
    """Take a skill out of the inventory; its proposal (if any) goes back to the list."""
    t = _term(term)
    cur = store.get(conn, SKILLS, _id(t))
    if cur is None:
        raise ValueError("term: not in the inventory")
    store.delete_doc(conn, SKILLS, _id(t), if_version=cur["version"], by="owner")
    prop = store.get(conn, PROPOSALS, _id(t))
    if prop and prop["data"].get("by") == "ats":      # never mined: nothing to go back to
        store.delete_doc(conn, PROPOSALS, _id(t), if_version=prop["version"], by="owner")
    elif prop:
        _put(conn, PROPOSALS, _id(t), dict(prop["data"], status="new"))


def decline(conn, term):
    """The user's "No": the keyword is not true for them. Stored as a dismissed proposal so neither the optimizer nor the
    miner asks again. A skill in the inventory must be removed first."""
    t = _term(term)
    if store.get(conn, SKILLS, _id(t)) is not None:
        raise ValueError("term: it is in your skill inventory; remove it there first")
    cur = store.get(conn, PROPOSALS, _id(t))
    base = cur["data"] if cur else {"term": t, "files": 0, "sources": [], "found": _today(), "by": "ats"}
    _put(conn, PROPOSALS, _id(t), dict(base, status="dismissed"))


def undecline(conn, term):
    """Undo "No". A decline the miner never proposed is deleted; a mined one goes back to the proposals."""
    t = _term(term)
    cur = store.get(conn, PROPOSALS, _id(t))
    if cur is None or cur["data"].get("status") != "dismissed":
        raise ValueError("term: not marked No")
    if cur["data"].get("by") == "ats":
        store.delete_doc(conn, PROPOSALS, _id(t), if_version=cur["version"], by="owner")
    else:
        _put(conn, PROPOSALS, _id(t), dict(cur["data"], status="new"))


def declined(conn):
    return {d["data"].get("term", "") for d in store.list_docs(conn, PROPOSALS) if d["data"].get("status") == "dismissed"}


def inventory(conn):
    return sorted((d["data"] for d in store.list_docs(conn, SKILLS)), key=lambda s: s.get("term", ""))


def backed_text(conn):
    """Confirmed inventory as profile text for tailoring and ATS 'profile-backed' checks. Empty when none."""
    inv = inventory(conn)
    if not inv:
        return ""
    line = lambda s: f"- {s['term']}{' (related experience)' if s.get('kind') == 'adjacent' else ''}: {s.get('evidence', '')}"
    return "\n## Skill inventory (confirmed by you)\n" + "\n".join(line(s) for s in inv) + "\n"


def view(conn):
    props = [d["data"] for d in store.list_docs(conn, PROPOSALS)]
    order = lambda p: (-p.get("files", 0), p.get("term", ""))
    return {"inventory": inventory(conn),
            "proposals": sorted((p for p in props if p.get("status") == "new"), key=order),
            "dismissed": sorted((p for p in props if p.get("status") == "dismissed"), key=order)}


def apply(conn, home, data, today=None):
    """One write entry point. data: {action: mine|accept|add|dismiss|undismiss|remove, ...}. -> view() (+ 'mined')."""
    if not isinstance(data, dict):
        raise ValueError("body: object expected")
    act = data.get("action")
    mined = None
    if act == "mine":
        mined = mine(conn, home, today)
    elif act in ("accept", "add"):
        accept(conn, data.get("term"), data.get("evidence", ""), data.get("kind", "have"), today)
    elif act == "dismiss":
        dismiss(conn, data.get("term"))
    elif act == "undismiss":
        undecline(conn, data.get("term"))
    elif act == "remove":
        remove(conn, data.get("term"))
    else:
        raise ValueError("unknown action")
    out = view(conn)
    if mined is not None:
        out["mined"] = mined
    return out
