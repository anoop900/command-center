"""One-time move of ATS scores out of kit folders: score every kit's resume into the database, then strip `ats` from
its kit.json. Each changed kit.json is backed up first. Safe to run again (already-clean kits are only rescored)."""
import json
import shutil
from datetime import datetime

from .. import config, store
from . import ats_check, pipeline, qc_check


def run(conn, home=None):
    """Returns {scored, stripped, skipped: [{folder, why}], backup}."""
    home = home or config.data_dir()
    kits = config.kits_dir(home)
    profile = (home / "Candidate_Profile.md").read_text(encoding="utf-8") if (home / "Candidate_Profile.md").is_file() else ""
    backup = home / "backups" / ("kitjson-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    out = {"scored": 0, "stripped": 0, "skipped": [], "backup": ""}
    for kj in sorted(kits.glob("**/kit.json")) if kits.is_dir() else []:
        if "previous" in kj.parts or any(p.startswith(".") for p in kj.relative_to(kits).parts):
            if "previous" in kj.parts and _strip(kj, kits, backup):
                out["stripped"] += 1
            continue
        rel = kj.parent.relative_to(kits).as_posix()
        try:
            role_id = json.loads(kj.read_text(encoding="utf-8")).get("role")
        except (OSError, ValueError, AttributeError):
            out["skipped"].append({"folder": rel, "why": "kit.json unreadable"})
            continue
        role = store.get(conn, "roles", role_id) if isinstance(role_id, str) else None
        pdf = next((p for p in kj.parent.glob("*.pdf") if "Cover_Letter" not in p.name), None)
        posting = (role or {"data": {}})["data"].get("posting") or ""
        if role is None or pdf is None or not posting.strip():
            out["skipped"].append({"folder": rel, "why": "no role" if role is None else "no resume PDF" if pdf is None
                                   else "no posting text"})
        else:
            try:
                text = qc_check.read_pdf(str(pdf))[0]
            except Exception as e:      # a damaged PDF must not stop the rest
                out["skipped"].append({"folder": rel, "why": f"PDF unreadable ({type(e).__name__})"})
            else:
                r = ats_check.score(text, posting, profile, role["data"].get("title", ""), role["data"].get("company", ""))
                pipeline.save_score(conn, role_id, ats_check.summary(r))
                out["scored"] += 1
        if _strip(kj, kits, backup):
            out["stripped"] += 1
    out["backup"] = str(backup) if backup.exists() else ""
    return out


def _strip(kj, kits, backup):
    try:
        has = any(k in json.loads(kj.read_text(encoding="utf-8")) for k in pipeline.DB_ONLY)
    except (OSError, ValueError, TypeError):
        return False
    if not has:
        return False
    dest = backup / kj.relative_to(kits)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(kj, dest)
    return pipeline.strip_score(kj)
