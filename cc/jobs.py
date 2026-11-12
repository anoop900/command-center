"""In-portal kit builds and reviews: one background worker, state in collection `jobs` (doc id "<kind>-<role id>").

States: running -> done | failed | needs_posting | waiting (Chat mode: an AI card is waiting for a pasted answer).
The page polls `jobs`; it never opens an AI window. A job left `running` by a stopped server is marked failed on start.
"""
import threading
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, modes, problems, store
from .ai import RunnerError, features, runners
from .ingest import fetchposting
from .kits import content, cover, pipeline

KINDS = ("kit", "review", "cover")
MAX_POSTING = pipeline.MAX_INPUT
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cc-job")
_lock = threading.Lock()


def job_id(kind, role_id):
    return f"{kind}-{role_id}"


def _put(conn, kind, role_id, **fields):
    jid = job_id(kind, role_id)
    cur = store.get(conn, "jobs", jid)
    data = dict(cur["data"]) if cur else {}
    data.update(kind=kind, key=role_id, **fields)
    store.set_doc(conn, "jobs", jid, data, by="jobs", force=True)


def _patch(conn, collection, doc_id, fields):
    """Merge fields into a document the morning run may also be writing: one retry on a version conflict."""
    for attempt in (0, 1):
        cur = store.get(conn, collection, doc_id)
        if cur is None:
            return
        try:
            store.update_doc(conn, collection, doc_id, fields, if_version=cur["version"], by="jobs")
            return
        except store.Conflict:
            if attempt:
                raise


def recover(conn):
    """A server that stopped mid-build leaves `running` rows behind; say so instead of showing progress forever."""
    for d in store.list_docs(conn, "jobs"):
        if d["data"].get("state") == "running":
            store.set_doc(conn, "jobs", d["id"], dict(d["data"], state="failed", finished=store.now(),
                                                      reason="Command Center was closed while this was running. Try again."),
                          by="jobs", force=True)


def _text_for(conn, kind, role_id, role, posting=""):
    """-> (text, why). The pasted text, else the saved one, else the job description read from the role's link (and saved)."""
    text = (posting or "").strip() or str(role["data"].get("posting") or "").strip()
    why = ""
    if not text:
        text, why = fetchposting.fetch(role["data"].get("link"))
        if text:
            _patch(conn, "roles", role_id, {"posting": text[:MAX_POSTING]})
    if not text:
        _put(conn, kind, role_id, state="needs_posting", ts=store.now(), reason=why)
    return text, why


def start(db_file, cfg_file, kind, role_id, posting="", runner=None):
    """Queue one job. Returns the job row; only a change of state is ever written, so a double click is harmless."""
    if kind not in KINDS:
        raise ValueError("unknown job kind")
    conn = store.connect(db_file)
    try:
        role = store.get(conn, "roles", role_id)
        if role is None:
            raise ValueError("no such role")
        text, _ = _text_for(conn, kind, role_id, role, posting)
        if not text:
            return store.get(conn, "jobs", job_id(kind, role_id))["data"]
        if (posting or "").strip():
            _patch(conn, "roles", role_id, {"posting": text[:MAX_POSTING]})
        with _lock:
            cur = store.get(conn, "jobs", job_id(kind, role_id))
            if cur and cur["data"].get("state") == "running":
                return cur["data"]
            _put(conn, kind, role_id, state="running", ts=store.now(), reason="", finished="")
        _pool.submit(_run, db_file, cfg_file, kind, role_id, text[:MAX_POSTING], runner)
        return store.get(conn, "jobs", job_id(kind, role_id))["data"]
    finally:
        conn.close()


def run_now(db_file, cfg_file, kind, role_id, runner=None):
    """Build in the calling thread (scheduled runs wait for the result). Uses the role's saved posting; returns the job row."""
    conn = store.connect(db_file)
    try:
        role = store.get(conn, "roles", role_id)
        text, _ = _text_for(conn, kind, role_id, role or {"data": {}})
        if text:
            _put(conn, kind, role_id, state="running", ts=store.now(), reason="", finished="")
            _run(db_file, cfg_file, kind, role_id, text[:MAX_POSTING], runner)
        return store.get(conn, "jobs", job_id(kind, role_id))["data"]
    finally:
        conn.close()


def _run(db_file, cfg_file, kind, role_id, text, runner):
    conn = store.connect(db_file)
    try:
        prob = None
        try:
            cfg = config.load(cfg_file)
            run = runner or runners.make_runner(cfg, conn)
            fn = {"kit": pipeline.build_kit, "review": features.review_role, "cover": features.cover_role}[kind]
            home = Path(db_file).parent
            # a rebuild (the role already has a kit) re-reads the profile; a first build only does if the content file is untouched
            notes = content.ensure(home, force=store.get(conn, "kitindex", role_id) is not None) if kind == "kit" else []
            out = fn(conn, role_id, text, cfg.mode.name, run, home)
            state, reason = _outcome(kind, out)
            if state == "failed":
                prob = _problem(kind, out, reason)
                reason = prob["message"]
            if kind == "cover" and state == "done":
                _publish_cover(conn, role_id, out, home)
            if kind == "kit" and state == "done":
                t = out.get("tailor") or {}
                if modes.uses_provider(cfg.mode.name) and t.get("source") != "ai":
                    notes.append(f"The AI step did not run ({t.get('reason') or 'no answer'}), so this kit uses your base resume.")
                notes.append(_publish_kit(conn, role_id, out, home))
                reason = " ".join(n for n in notes if n)
        except (pipeline.KitError, RunnerError, config.ConfigError, ValueError, RuntimeError) as e:
            prob = problems.describe(e)
            state, reason = "failed", prob["message"]
        except Exception as e:  # a worker must always leave a final state, or the page shows progress forever
            prob = problems.describe(e, detail=f"{type(e).__name__}: {e}")
            state, reason = "failed", prob["message"]
        prob = prob or {}
        _put(conn, kind, role_id, state=state, reason=reason, finished=store.now(), code=prob.get("code", ""),
             fix=prob.get("fix", ""), action=prob.get("action", ""), detail=prob.get("detail", ""))
    finally:
        conn.close()


def _problem(kind, out, reason):
    """The plain message and next step for a failed pipeline result (a kit that failed its checks gets the resume-specific one)."""
    out = out or {}
    if kind == "kit" and out.get("status") == "blocked":
        issues = list((out.get("qc") or {}).get("issues") or [])
        note = str((out.get("resume") or {}).get("note") or "")
        return problems.describe_qc(issues or ([note] if note else []))
    return problems.describe(reason)


def _outcome(kind, out):
    """Map a finished pipeline result to a job state and a plain-language reason."""
    status = str((out or {}).get("status") or "")
    if status == "waiting":
        return "waiting", "Waiting for your pasted answer. Open the AI cards."
    if kind == "kit" and status in ("blocked", "unavailable"):
        if status == "blocked":
            why = [str((out.get("resume") or {}).get("note") or "")] + [str(i) for i in (out.get("qc") or {}).get("issues", [])[:3]]
            return "failed", " ".join(["The kit was built but did not pass its checks."] + [w for w in why if w])
        return "failed", str(out.get("resume") or "The resume tools are not installed.")
    if status in ("unavailable", "error", "fallback"):
        return "failed", str(out.get("reason") or "The AI did not answer.")
    return "done", ""


def _kit_files(folder):
    """(slot, file name) for each download the kit folder has."""
    found = [("resume", content.find_resume(folder, "pdf")), ("docx", content.find_resume(folder, "docx")),
             ("cover", cover.find(folder)), ("kit", folder / "kit.json")]
    return [(slot, p.name) for slot, p in found if p is not None and p.is_file()]


def _publish_cover(conn, role_id, out, home=None):
    """Add the letter's download to the kit index and remember its file name on the role (the tracker's cover_letter)."""
    home = home or config.data_dir()
    idx = store.get(conn, "kitindex", role_id)
    if idx is None:
        return
    slug = idx["data"].get("folder") or out["folder"]
    files = [f for f in idx["data"].get("files", []) if f.get("slot") != "cover"]
    files.insert(1 if files else 0, {"slot": "cover", "name": out["file"], "url": f"/kits/{quote(slug)}/{quote(out['file'])}"})
    store.set_doc(conn, "kitindex", role_id, dict(idx["data"], files=files), by="jobs", force=True)
    _patch(conn, "roles", role_id, {"cover_letter": out["file"]})


def _publish_kit(conn, role_id, kit, home=None):
    """Make a finished kit visible: kit index (download buttons), role status, and the board row. Returns a note."""
    home = home or config.data_dir()
    slug = kit.get("folder") or pipeline._slug(role_id)       # relative to the kits folder
    folder = config.kits_dir(home) / slug
    role = store.get(conn, "roles", role_id)
    key = role["data"].get("key") or role_id
    files = [{"slot": slot, "name": name, "url": f"/kits/{quote(slug)}/{quote(name)}"} for slot, name in _kit_files(folder)]
    store.set_doc(conn, "kitindex", role_id, {"role": role_id, "key": key, "folder": slug, "files": files, "on_pc": True,
                                              "status": kit["status"], "built": kit["built"],
                                              "tailor": kit["tailor"]["source"],
                                              "unsupported": _unsupported(kit)}
                  | ({"ats": kit["ats"]} if isinstance(kit.get("ats"), dict) else {}), by="jobs", force=True)
    _patch(conn, "roles", role_id, {"status": "ready", "kit": slug})
    board = store.get(conn, "board", "roles")
    if board:
        rows = [dict(r, stage="ready", status="ready", kit=slug) if r.get("key") == key and r.get("stage") in ("new", "ready") else r
                for r in board["data"].get("roles", [])]
        try:
            store.set_doc(conn, "board", "roles", dict(board["data"], roles=rows), if_version=board["version"], by="jobs")
        except store.Conflict:
            pass        # a run rebuilt the board meanwhile; it reads the role record, which already says ready
    n = len(_unsupported(kit))
    return f"{n} statement(s) could not be traced to your profile. Open the role's Details to see which ones, and check them before you use the kit." if n else ""


def _unsupported(kit):
    """The statements the claim trace could not tie to the profile, as plain {claim, reason} rows for the page (bounded, text only)."""
    rows = (kit.get("trace") or {}).get("unsupported")
    out = []
    for x in rows if isinstance(rows, list) else []:
        if isinstance(x, dict) and isinstance(x.get("claim"), str) and x["claim"].strip():
            out.append({"claim": x["claim"].strip()[:400], "reason": str(x.get("reason") or "")[:300]})
    return out[:50]
