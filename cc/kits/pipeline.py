"""Kit for one role: tailor -> build_resume -> qc_check -> ats_check -> trace, written to <data>/kits/<role>/."""
import copy
import json
import os
import re
import shutil
from datetime import datetime, timezone

from .. import ai, config, kitfolder, kitprefs, relevance, resume, store
from . import ats_check, build_resume, qc_check
from . import content as kit_content

MAX_INPUT = 500_000
DB_ONLY = ("ats", "ats_retry")      # kept in the database for the portal, never written into a kit folder


class KitError(Exception):
    """A short, safe message for the user (missing input, locked folder)."""


def _read(path, what, as_json=False):
    try:
        if path.stat().st_size > MAX_INPUT:
            raise KitError(f"{what} is too large ({path.name})")
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if as_json else text
    except FileNotFoundError:
        raise KitError(f"{what} not found: put {path.name} in {path.parent}") from None
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise KitError(f"cannot read {path.name}: {type(e).__name__}") from None


def _slug(role_id):
    s = re.sub(r"[^a-z0-9._-]+", "-", str(role_id).lower()).strip(".-")[:80]
    if not s:
        raise KitError("role id has no usable characters")
    return s


def _apply_tailor(base, t):
    """Merge a validated tailor result into a copy of the base content. Unmatched bullet edits are dropped."""
    c = copy.deepcopy(base)
    c["headline"], c["profile"] = t["headline"], t["profile"]
    if t.get("subhead"):
        c["subhead"] = t["subhead"]
    c["skills"] = [[s["label"], s["text"]] for s in t["skills"]]
    dropped = []
    for ch in t.get("bullet_changes", []):
        hits = [(j, i) for j in c["jobs"] for i, b in enumerate(j["bullets"]) if b == ch["old"]]
        if len(hits) == 1:
            hits[0][0]["bullets"][hits[0][1]] = ch["new"]
        else:
            dropped.append(ch["old"][:60])
    return c, dropped


def _build(content, outdir, target, profile, rules, posting, role):
    """Build the resume into outdir -> (build result, qc, ats score). qc and ats are None when no PDF was made."""
    b = build_resume.build_pdf(content, str(outdir), target)
    if not b.get("pdf"):
        return b, None, None
    text, pages, fill = qc_check.read_pdf(b["pdf"])
    return (b, qc_check.check(text, pages, fill, profile, rules),
            ats_check.score(text, posting, profile, role.get("title", ""), role.get("company", "")))


def _wants_retry(ats, t, mode, runner):
    """One extra tailor pass only when the AI answered directly and the score has profile-backed room to grow."""
    return (ats["score"] < ats_check.PASS_SCORE and t.status == "ok" and t.source == "ai" and mode != "chat"
            and not getattr(runner, "queues", False) and bool(ats["add"] or not ats["title_on_resume"]))


def kit_json(kit):
    return json.dumps({k: v for k, v in kit.items() if k not in DB_ONLY}, indent=1, ensure_ascii=False)


def strip_score(path):
    """Remove the score from a kit.json (older kits stored it there). Returns True when the file changed."""
    try:
        k = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(k, dict) or not any(x in k for x in DB_ONLY):
        return False
    path.write_text(kit_json(k), "utf-8")
    return True


def save_score(conn, role_id, ats):
    """Score on the role record (learning reads it) and on its kit index entry (the portal reads it)."""
    for attempt in (0, 1):
        cur = store.get(conn, "roles", role_id)
        if cur is None:
            return
        try:
            store.update_doc(conn, "roles", role_id, {"ats": ats["score"]}, if_version=cur["version"], by="cc kit")
            break
        except store.Conflict:
            if attempt:
                raise
    idx = store.get(conn, "kitindex", role_id)
    if idx is not None:
        store.set_doc(conn, "kitindex", role_id, dict(idx["data"], ats=ats), by="cc kit", force=True)


def existing_folder(conn, role_id, home):
    """Absolute folder of the kit this program built for the role, or None. A kit from another tool is never reused."""
    role, idx = store.get(conn, "roles", role_id), store.get(conn, "kitindex", role_id)
    rel = (role or {"data": {}})["data"].get("kit") or (idx or {"data": {}})["data"].get("folder") or _slug(role_id)
    f = config.kits_dir(home) / rel
    try:
        ok = json.loads((f / "kit.json").read_text(encoding="utf-8")).get("role") == role_id
    except (OSError, ValueError, AttributeError):
        ok = False
    return f if ok else None


def build_kit(conn, role_id, posting, mode, runner=ai.none_runner, home=None):
    """Run the pipeline. Returns the kit.json dict; raises KitError when an input is missing or the folder is locked."""
    home = home or config.data_dir()
    role = store.get(conn, "roles", role_id)
    if role is None:
        raise KitError(f"no role {role_id!r}")
    if not isinstance(posting, str) or not posting.strip():
        raise KitError("the posting text is empty")
    base = _read(home / "content.json", "resume content", True)
    rules = _read(home / "qc_rules.json", "qc rules", True)
    profile = _read(home / "Candidate_Profile.md", "candidate profile")
    if not isinstance(base, dict):
        raise KitError("content.json must be an object")
    try:
        qc_check.normalize(rules)
    except ValueError as e:
        raise KitError("qc_rules.json: " + str(e)) from None

    target = resume.page_target(home, rules)
    rules = dict(rules or {}, pages=target)
    kit = {"role": role_id, "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "status": "ok", "notes": []}
    norm = qc_check.normalize(rules)
    liked = kitprefs.phrases(conn, relevance.settings(conn)["forgotten"])
    wording = {"banned_phrases": sorted(set(norm["banned"].values()) | set(liked)), "banned_words": norm["banned_words"],
               "no_contractions": norm["no_contractions"]}
    tin = {"posting": posting, "profile": profile,
           "rules": json.dumps(wording, ensure_ascii=False) if any(wording.values()) else ""}
    t = ai.run_task("tailor", tin, mode, runner)
    content = copy.deepcopy(base)
    kit["tailor"] = {"source": t.source or "none", "reason": t.reason}
    if t.status == "ok" and t.source == "ai":
        content, dropped = _apply_tailor(base, t.data)
        if dropped:
            kit["notes"].append(f"{len(dropped)} bullet change(s) dropped: old text not found exactly once")
    elif t.status == "queued":
        kit["status"] = "waiting"
        kit["notes"].append("tailor answer is pending: use `cc ai cards` / `cc ai answer`, then rerun")
        return kit      # nothing is built or stored until the answer arrives
    _finish(content, home)

    kits =config.kits_dir(home)
    old = existing_folder(conn, role_id, home)
    rel = old.relative_to(kits).as_posix() if old else kitfolder.folder_for(role["data"], kits, datetime.now().date())
    kit["folder"] = rel
    work = kits / (".tmp-" + _slug(role_id))
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        try:
            b, qc, ats = _build(content, work, target, profile, rules, posting, role["data"])
        except RuntimeError as e:
            kit.update(status="unavailable", resume=str(e))
            b = qc = ats = None
        if qc is not None and qc["pass"] and b["ok"] and _wants_retry(ats, t, mode, runner):
            content, t, b, qc, ats = _retry(content, t, b, qc, ats, tin, role["data"], base, home, work,
                                            (target, profile, rules, posting), mode, runner, kit)
        if b is not None:
            kit["resume"] = {k: b[k] for k in ("ok", "pages", "page2_fill", "note", "reason") if k in b}
            if not b.get("pdf"):
                kit["status"] = "blocked"
            else:
                kit["qc"] = qc
                kit["ats"] = ats_check.summary(ats, kit.pop("ats_retry", None))
                if not kit["qc"]["pass"] or not b["ok"]:
                    kit["status"] = "blocked"
                kit["files"] = [os.path.basename(b["pdf"]), content["file_stem"] + ".docx"]
        if kit["status"] == "ok":
            sent = json.dumps({k: content.get(k) for k in ("headline", "subhead", "profile", "skills")}
                              | ({"answers": t.data.get("answers"), "why_company": t.data.get("why_company")}
                                 if t.source == "ai" else {}), ensure_ascii=False)
            tr = ai.run_task("trace", {"posting": posting, "profile": profile, "kit": sent}, mode, runner)
            if tr.status == "ok":
                kit["trace"] = {"unsupported": tr.data["unsupported"]}
                if tr.data["unsupported"]:
                    kit["status"] = "review"
            else:
                kit["trace"] = {"skipped": tr.reason}
        if old:         # a cover letter made on request survives a rebuild of the kit
            for f in old.glob("*Cover_Letter.*"):
                shutil.copy2(f, work / f.name)
        if old:         # one level of undo: the kit being replaced stays in previous/ (only the latest one is kept)
            prev = work / "previous"
            prev.mkdir()
            for f in old.iterdir():
                if f.is_file():
                    shutil.copy2(f, prev / f.name)
            strip_score(prev / "kit.json")
        kitprefs.save_baseline(work)
        if t.source == "ai":
            (work / "tailor.json").write_text(json.dumps(t.data, indent=1, ensure_ascii=False), "utf-8")
        (work / "kit.json").write_text(kit_json(kit), "utf-8")
        _swap(work, kits / rel)
    except OSError as e:
        shutil.rmtree(work, ignore_errors=True)
        raise KitError(f"cannot write the kit folder ({e.strerror or type(e).__name__}); close any open kit files") from None
    store.set_doc(conn, "kitindex", role_id, {"role": role_id, "status": kit["status"], "built": kit["built"],
                                              "folder": rel, "tailor": kit["tailor"]["source"]}, by="cc kit", force=True)
    if kit.get("ats"):
        save_score(conn, role_id, kit["ats"])
    return kit


def _finish(content, home):
    kit_content.tidy_content(content, home)     # the working copy only; the user's content.json is never rewritten here
    content["file_stem"] = kit_content.resume_stem(content.get("name"))


def _retry(content, t, b, qc, ats, tin, role, base, home, work, build_args, mode, runner, kit):
    """Second tailor pass with the title, the profile-backed missing phrases and a push for numbers. Keeps whichever
    build scores higher; the second one must also pass its checks. At most one extra AI call."""
    hint = {"title": str(role.get("title") or "")[:200], "add": ats["add"], "gaps": ats["gaps"], "score": ats["score"]}
    t2 = ai.run_task("tailor", dict(tin, ats=json.dumps(hint, ensure_ascii=False)), mode, runner)
    kit["ats_retry"] = {"first": ats["score"], "kept": "first"}
    if t2.status != "ok" or t2.source != "ai":
        kit["ats_retry"]["skipped"] = (t2.reason or t2.status)[:200]
        return content, t, b, qc, ats
    c2, dropped = _apply_tailor(base, t2.data)
    _finish(c2, home)
    alt = work / ".retry"
    shutil.rmtree(alt, ignore_errors=True)
    alt.mkdir()
    try:
        try:
            b2, qc2, ats2 = _build(c2, alt, *build_args, role)
        except RuntimeError:
            return content, t, b, qc, ats
        if ats2 is not None:
            kit["ats_retry"]["second"] = ats2["score"]
        if ats2 is None or not (qc2["pass"] and b2["ok"]) or ats2["score"] <= ats["score"]:
            return content, t, b, qc, ats
        for f in work.iterdir():
            if f.is_file() and f.suffix.lower() in (".pdf", ".docx"):
                f.unlink()
        for f in alt.iterdir():
            if f.is_file():
                os.replace(f, work / f.name)
        b2["pdf"] = str(work / os.path.basename(b2["pdf"]))
        kit["ats_retry"]["kept"] = "second"
        if dropped:
            kit["notes"].append(f"{len(dropped)} bullet change(s) dropped in the second pass: old text not found exactly once")
        return c2, t2, b2, qc2, ats2
    finally:
        shutil.rmtree(alt, ignore_errors=True)


def _swap(work, final):
    """Replace the kit folder; on failure the previous kit stays in place."""
    final.parent.mkdir(parents=True, exist_ok=True)
    old = final.with_name(final.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if final.exists():
        os.replace(final, old)
    try:
        os.replace(work, final)
    except OSError:
        if old.exists():
            os.replace(old, final)
        raise
    shutil.rmtree(old, ignore_errors=True)
