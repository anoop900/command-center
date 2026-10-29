"""Role-level AI features on top of run_task: review (fresh call over the kit) and interview prep."""
import json
import os
import shutil
from pathlib import Path
from datetime import datetime, timezone

from .. import ai, config, store
from .. import kitprefs, relevance
from ..kits import content as kit_content, cover, pipeline, qc_check
from ..kits.pipeline import KitError, _read, _slug


def _load(conn, role_id, posting, home):
    if store.get(conn, "roles", role_id) is None:
        raise KitError(f"no role {role_id!r}")
    if not isinstance(posting, str) or not posting.strip():
        raise KitError("the posting text is empty")
    return home / "Candidate_Profile.md"


def _tailor_path(conn, role_id, home):
    return (pipeline.existing_folder(conn, role_id, home) or config.kits_dir(home) / _slug(role_id)) / "tailor.json"


def _save(conn, collection, role_id, r):
    """Store a finished result, or report why there is none. Nothing is saved for fallback/unavailable/queued."""
    if r.status == "queued":
        return {"status": "waiting", "reason": r.reason}
    if r.status != "ok" or r.source != "ai":
        return {"status": "unavailable", "reason": r.reason, "errors": r.errors}
    doc = dict(r.data, role=role_id, built=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if collection == "kitreviews":  # the dashboard reads key, at and the counts
        edits = r.data.get("part_a", [])
        g = sum(1 for e in edits if e.get("reason") == "grounding")
        doc.update(key=role_id, at=doc["built"], grounding_issues=g, suggestions=len(edits) - g, fixed=0, applied=[])
    store.set_doc(conn, collection, role_id, doc, by="cc", force=True)
    return {"status": "ok", "result": r.data}


def review_role(conn, role_id, posting, mode, runner=ai.none_runner, home=None):
    home = home or config.data_dir()
    prof_path = _load(conn, role_id, posting, home)
    profile = _read(prof_path, "candidate profile")
    kit = _read(_tailor_path(conn, role_id, home), "kit (run `cc kit` first)")
    inputs = {"posting": posting, "profile": profile, "kit": kit}
    try:
        inputs["rules"] = _read(home / "qc_rules.json", "qc rules")
    except KitError:
        pass    # rules are optional context for review
    return _save(conn, "kitreviews", role_id, ai.run_task("review", inputs, mode, runner))


def _shape(v):
    """The skeleton of parsed JSON: keys and list lengths kept, scalar values dropped."""
    if isinstance(v, dict):
        return {k: _shape(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_shape(x) for x in v]
    return type(v).__name__


def apply_review_edit(conn, role_id, index, home=None):
    """Apply one reviewed edit to the kit file, only when asked. The old text must still occur exactly once,
    the result must still be valid JSON, and an edit is applied at most once. Only the kit can be edited:
    review is never given the resume text."""
    home = home or config.data_dir()
    rev = store.get(conn, "kitreviews", role_id)
    edits = rev["data"].get("part_a") if rev else None
    if not isinstance(edits, list):
        raise KitError(f"no review for {role_id!r}")
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(edits):
        raise KitError("no such edit")
    applied = list(rev["data"].get("applied", []))
    if index in applied:
        raise KitError("that edit was already applied")
    e = edits[index]
    if e.get("file") != "kit":
        raise KitError("only kit edits can be applied here; change the resume by hand")
    path = _tailor_path(conn, role_id, home)
    text = _read(path, "kit")
    if not e.get("old_string") or text.count(e["old_string"]) != 1:
        raise KitError("the kit changed since the review: that text no longer occurs exactly once")
    new = text.replace(e["old_string"], e["new_string"], 1)
    try:
        ok = _shape(json.loads(new)) == _shape(json.loads(text))
    except ValueError:
        ok = False
    if not ok:  # a quote in new_string could otherwise add or drop fields
        raise KitError("that edit would change the kit's structure, so it was not applied")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    applied.append(index)
    store.set_doc(conn, "kitreviews", role_id, dict(rev["data"], applied=applied, fixed=len(applied)), by="owner", force=True)
    return {"status": "ok", "applied": applied}


def prep_role(conn, role_id, posting, mode, runner=ai.none_runner, home=None):
    home = home or config.data_dir()
    profile = _read(_load(conn, role_id, posting, home), "candidate profile")
    inputs = {"posting": posting, "profile": profile}
    try:
        inputs["stories"] = _read(home / "Story_Bank.md", "story bank")
    except KitError:
        pass    # prep still works without stories
    return _save(conn, "preps", role_id, ai.run_task("prep", inputs, mode, runner))


def cover_role(conn, role_id, posting, mode, runner=ai.none_runner, home=None):
    """One-page cover letter into the role's kit folder. Only runs on request; pressing again replaces the letter."""
    home = home or config.data_dir()
    profile = _read(_load(conn, role_id, posting, home), "candidate profile")
    folder = pipeline.existing_folder(conn, role_id, home)
    if folder is None:
        raise KitError("Build the kit for this role first; the letter is saved in its folder.")
    try:
        kit_content.ensure(home)
    except ValueError as e:
        raise KitError(str(e)) from None
    base = _read(home / "content.json", "resume content", True)
    try:
        norm = qc_check.normalize(_read(home / "qc_rules.json", "qc rules", True))
    except (KitError, ValueError):
        norm = qc_check.normalize({})
    banned = set(norm["banned"].values()) | set(kitprefs.phrases(conn, relevance.settings(conn)["forgotten"]))
    wording = {"banned_phrases": sorted(banned), "banned_words": norm["banned_words"],
               "no_contractions": norm["no_contractions"]}
    inputs = {"posting": posting, "profile": profile,
              "rules": json.dumps(wording, ensure_ascii=False) if any(wording.values()) else ""}
    r = ai.run_task("cover", inputs, mode, runner)
    if r.status == "queued":
        return {"status": "waiting", "reason": r.reason}
    if r.status != "ok" or r.source != "ai":
        return {"status": "unavailable", "reason": r.reason, "errors": r.errors}
    name = str(base.get("name") or "") if isinstance(base, dict) else ""
    contact = str(base.get("contact") or "") if isinstance(base, dict) else ""
    work = folder.with_name(".tmp-cover-" + folder.name)
    shutil.rmtree(work, ignore_errors=True)
    try:
        pdf = Path(cover.build(r.data, name, contact, work))
        for old in folder.glob("*Cover_Letter.*"):
            old.unlink()
        for f in work.iterdir():
            os.replace(f, folder / f.name)
    except OSError as e:
        raise KitError(f"cannot write the letter ({e.strerror or type(e).__name__}); close any open kit files") from None
    except RuntimeError as e:
        raise KitError(str(e)) from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"status": "ok", "folder": folder.name, "file": pdf.name,
            "words": sum(len(x.split()) for x in r.data["paragraphs"])}


MAX_QUESTION = 4000
MAX_CONTEXT = 30_000


def ask_question(question, context, mode, runner=ai.none_runner):
    """One answer, nothing stored. Raises KitError for a question that is empty, not text, or too long."""
    if not isinstance(question, str) or not question.strip():
        raise KitError("the question is empty")
    if len(question) > MAX_QUESTION:
        raise KitError(f"the question is longer than {MAX_QUESTION} characters")
    if context is not None and (not isinstance(context, str) or len(context) > MAX_CONTEXT):
        raise KitError("the context must be text of at most %d characters" % MAX_CONTEXT)
    inputs = {"question": question}
    if context:
        inputs["context"] = context
    r = ai.run_task("ask", inputs, mode, runner)
    if r.status == "queued":
        return {"status": "waiting", "reason": r.reason}
    if r.status != "ok" or r.source != "ai":
        return {"status": "unavailable", "reason": r.reason or "No AI available.", "errors": r.errors}
    return {"status": "ok", "answer": r.data["answer"]}


MAX_REQUEST = 1500
MAX_THEIRS = 6000


def outreach_role(conn, role_id, channel, request, theirs, mode, runner=ai.none_runner, home=None, to="", name=""):
    """One message draft for any role on the board (new, applied, or added by hand). Nothing is stored or sent.
    request: what the user wants to say, in their words (optional); theirs: a message they received (optional)."""
    home = home or config.data_dir()
    role = store.get(conn, "roles", role_id)
    if role is None:
        raise KitError(f"no role {role_id!r}")
    if channel not in ai.registry.OUTREACH_CHANNELS:
        raise KitError("channel must be email, LinkedIn message or connection note")
    for what, v, limit in (("request", request, MAX_REQUEST), ("their message", theirs, MAX_THEIRS), ("name", name, 60), ("who it is for", to, 60)):
        if v is not None and (not isinstance(v, str) or len(v) > limit):
            raise KitError(f"the {what} must be text of at most {limit} characters")
    r = role["data"]
    profile = _read(home / "Candidate_Profile.md", "candidate profile")
    facts = [f"Role: {r.get('title', '')} at {r.get('company', '')}", f"Status: {r.get('status', '')}"]
    if r.get("date_applied"):
        facts.append(f"Applied on {r['date_applied']}")
    if r.get("source") == "manual" and r.get("found_via"):
        facts.append(f"Found via {r['found_via']}")
    if to.strip():
        facts.append(f"Writing to: {to.strip()}")
    if name.strip():
        facts.append(f"First name: {name.strip()}")
    inputs = {"profile": profile, "role": "\n".join(facts), "channel": channel}
    for key, val in (("posting", r.get("posting")), ("request", request), ("theirs", theirs)):
        if isinstance(val, str) and val.strip():
            inputs[key] = val
    res = ai.run_task("outreach", inputs, mode, runner)
    if res.status == "queued":
        return {"status": "waiting", "reason": res.reason}
    if res.status != "ok" or res.source != "ai":
        return {"status": "unavailable", "reason": res.reason or "No AI available.", "errors": res.errors}
    return {"status": "ok", "subject": res.data.get("subject", ""), "message": res.data["message"]}
