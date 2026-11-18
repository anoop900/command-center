"""ATS optimizer (PLAN F1, D7/D8): per-kit score breakdown, what-if, and a Have / Adjacent / No answer for each
keyword the posting asks for that the resume lacks. Have and Adjacent put the keyword in the skill inventory with
evidence (Adjacent needs an explicit "this is true"), which makes it profile-backed for the next kit build. No records
that it is not true, so it is never asked again. The score lives in the database only, never in a kit folder."""
import math
from pathlib import Path

from . import skills, store
from .kits import ats_check

KW_MAX = ats_check.WEIGHTS["keywords"]
MAX_KITS = 500


def _profile(home):
    p = Path(home) / "Candidate_Profile.md"
    try:
        return p.read_text(encoding="utf-8", errors="replace").lower() if p.is_file() else ""
    except OSError:
        return ""


def _in(term, text):
    return ats_check.has(term, text) if term in ats_check.TERMS else ats_check._word(term, text)


def counts(ats):
    """[matched, asked] keyword counts. Older scores lack them; the capped lists then give an estimate."""
    kw = ats.get("kw")
    if isinstance(kw, list) and len(kw) == 2 and all(isinstance(x, int) and x >= 0 for x in kw) and kw[0] <= kw[1]:
        return kw
    m = len(ats.get("matched") or [])
    return [m, m + len(ats.get("add") or []) + len(ats.get("gaps") or [])]


def whatif(ats, extra):
    """Estimated score if `extra` more posting keywords were on the resume. Only the keyword part moves."""
    m, t = counts(ats)
    if not t:
        return int(ats.get("score") or 0)
    return math.floor(float(ats.get("score") or 0) + KW_MAX * (min(t, m + max(0, extra)) - m) / t + 0.5)    # = JS Math.round


def _state(term, profile, inv, no):
    if term in inv:
        return inv[term].get("kind", "have")
    if term in no:
        return "no"
    if _in(term, profile):
        return "backed"         # already in Candidate_Profile.md: the next build may use it
    return "open"


def view(conn, home):
    """Every scored kit, lowest score first, with each missing keyword's current answer."""
    profile = _profile(home)
    inv = {s["term"]: s for s in skills.inventory(conn)}
    no = skills.declined(conn)
    roles = {d["id"]: d["data"] for d in store.list_docs(conn, "roles")}
    kits = []
    for d in store.list_docs(conn, "kitindex"):
        a = d["data"].get("ats")
        if not isinstance(a, dict) or not isinstance(a.get("score"), (int, float)):
            continue
        r = roles.get(d["id"], {})
        lists = [a.get(k) if isinstance(a.get(k), list) else [] for k in ("add", "gaps")]
        missing = [t for t in lists[0] + lists[1] if isinstance(t, str) and t.strip()]
        rows = [{"term": t, "state": _state(t, profile, inv, no)} for t in dict.fromkeys(missing)]
        ready = sum(x["state"] in ("have", "adjacent", "backed") for x in rows)
        m, t = counts(a)
        kits.append({"role": d["id"], "key": d["data"].get("key") or d["id"], "title": r.get("title", ""),
                     "company": r.get("company", ""), "built": d["data"].get("built", ""), "score": a["score"],
                     "parts": a.get("parts") or {}, "title_on_resume": a.get("title_on_resume"), "years": a.get("years"),
                     "format_issues": a.get("format_issues") or [], "kw": [m, t], "estimated_counts": "kw" not in a,
                     "missing": rows, "projected": whatif(a, ready), "has_posting": bool(str(r.get("posting") or "").strip())})
    kits.sort(key=lambda k: (k["score"], k["title"]))
    return {"kits": kits[:MAX_KITS], "total": len(kits), "pass_score": ats_check.PASS_SCORE, "kw_max": KW_MAX,
            "skills": skills.view(conn)}


def apply(conn, home, data):
    """data: {action: have|adjacent|no|undo, term, evidence?, confirm?}. -> view()."""
    if not isinstance(data, dict):
        raise ValueError("body: object expected")
    act, term = data.get("action"), data.get("term")
    if act == "have":
        skills.accept(conn, term, data.get("evidence", ""), "have")
    elif act == "adjacent":
        if data.get("confirm") is not True:
            raise ValueError("confirm: tick 'this is true' before related wording can go into a kit")
        skills.accept(conn, term, data.get("evidence", ""), "adjacent")
    elif act == "no":
        skills.decline(conn, term)
    elif act == "undo":
        t = skills._term(term)
        if store.get(conn, skills.SKILLS, skills._id(t)) is not None:
            skills.remove(conn, t)
        else:
            skills.undecline(conn, t)
    else:
        raise ValueError("unknown action")
    return view(conn, home)
