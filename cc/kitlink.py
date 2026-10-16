"""Point roles at the kit folders already on this PC (imported roles keep the resume path in `resume_file`, not `kit`)."""
import os
from pathlib import Path

from . import config, store


def _find(kits, resume_file):
    """'Daily/2026-10-06/Northwind_X/resume.pdf' -> 2026-10-06/Northwind_X; a bare 'Resume_Northwind_X.pdf' -> any <date>/Northwind_X (imported paths use backslashes)."""
    parts = [p for p in str(resume_file or "").replace("/", "\\").split("\\") if p and p not in (".", "..") and ":" not in p]
    if len(parts) >= 3 and (kits / Path(*parts[1:-1])).is_dir():
        return os.sep.join(parts[1:-1])
    if len(parts) == 1 and parts[0].lower().endswith(".pdf") and "_Resume_" in parts[0]:
        name = parts[0][:-4].split("_Resume_", 1)[1]
        hits = sorted(p for p in kits.glob("*/" + name) if p.is_dir()) if name and not set(name) & set("*?[]") else []
        if hits:
            return os.sep.join(hits[-1].relative_to(kits).parts)
    return ""


def link_all(conn, home):
    """Set `kit` on roles that have an imported resume path whose folder exists here, and on their board rows. Returns the count."""
    kits = config.kits_dir(home)
    found = {}
    indexed = {x["data"].get("key"): x["data"].get("folder") for x in store.list_docs(conn, "kitindex")
               if x["data"].get("files") and x["data"].get("folder")}
    for d in store.list_docs(conn, "roles"):
        if d["data"].get("kit"):
            continue
        sub = indexed.get(d["data"].get("key")) or _find(kits, d["data"].get("resume_file"))
        if sub:
            found[d["data"].get("key") or d["id"]] = (d, sub)
    for key, (d, sub) in found.items():
        store.update_doc(conn, "roles", d["id"], {"kit": sub}, if_version=d["version"], by="kitlink")
    board = store.get(conn, "board", "roles")
    if found and board:
        rows = [dict(r, kit=found[r.get("key")][1]) if r.get("key") in found and not r.get("kit") else r for r in board["data"].get("roles", [])]
        try:
            store.set_doc(conn, "board", "roles", dict(board["data"], roles=rows), if_version=board["version"], by="kitlink")
        except store.Conflict:
            pass        # a run rebuilt the board meanwhile; it reads the role record, which now has the folder
    return len(found)
