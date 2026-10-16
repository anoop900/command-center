"""Move data between the engine and a folder of JSON files.

An export folder has the layout:
    <root>/<collection path>/<doc_id>.json      (each file = the record's JSON object)
import_folder() loads it (idempotent: same input, no new versions). diff() compares the engine with a folder
field by field. export_folder() writes the same layout back.
"""
import json
from pathlib import Path

from . import store

DEFAULT_SKIP = ("backups",)  # large page backups, not records


def _load(f):
    """Parse one export file; absurdly nested JSON is a bad file like any other, not a crash."""
    try:
        return json.loads(Path(f).read_text(encoding="utf-8"))
    except RecursionError:
        raise ValueError("JSON nested too deeply") from None


def _walk(root):
    root = Path(root)
    for f in sorted(root.rglob("*.json")):
        if f.name.startswith("_") or any(p.startswith("_") for p in f.relative_to(root).parts[:-1]):
            continue
        rel = f.relative_to(root)
        collection = "/".join(rel.parts[:-1])
        if not collection:
            continue
        yield collection, f.stem, f


def import_folder(conn, root, skip=DEFAULT_SKIP, by="import"):
    """Returns {collection: {"added": n, "changed": n, "same": n}}. Never deletes engine records."""
    report = {}
    for collection, doc_id, f in _walk(root):
        if collection.split("/")[0] in skip:
            continue
        data = _load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{f}: expected a JSON object")
        cur = store.get(conn, collection, doc_id)
        r = report.setdefault(collection, {"added": 0, "changed": 0, "same": 0})
        if cur is None:
            r["added"] += 1
        elif cur["data"] == data:
            r["same"] += 1
            continue
        else:
            r["changed"] += 1
        store.set_doc(conn, collection, doc_id, data, by=by, force=True)
    return report


def diff(conn, root, skip=DEFAULT_SKIP):
    """Differences between the export (theirs) and the engine (ours). Empty list = identical."""
    out, seen = [], set()
    for collection, doc_id, f in _walk(root):
        if collection.split("/")[0] in skip:
            continue
        seen.add((collection, doc_id))
        theirs = _load(f)
        cur = store.get(conn, collection, doc_id)
        if cur is None:
            out.append({"collection": collection, "id": doc_id, "issue": "missing in engine"})
            continue
        ours = cur["data"]
        for k in sorted(set(theirs) | set(ours)):
            if theirs.get(k) != ours.get(k):
                out.append({"collection": collection, "id": doc_id, "field": k,
                            "export": theirs.get(k), "engine": ours.get(k)})
    for collection in store.collections(conn):
        if collection.split("/")[0] in skip:
            continue
        for d in store.list_docs(conn, collection):
            if (collection, d["id"]) not in seen:
                out.append({"collection": collection, "id": d["id"], "issue": "missing in export"})
    return out


def export_folder(conn, root, only=None):
    """Write engine records in the export layout. only: optional list of collections."""
    root = Path(root)
    n = 0
    for collection in store.collections(conn):
        if only and collection not in only:
            continue
        d = root / collection
        d.mkdir(parents=True, exist_ok=True)
        for doc in store.list_docs(conn, collection):
            (d / f"{doc['id']}.json").write_text(json.dumps(doc["data"], indent=1, ensure_ascii=False),
                                                 encoding="utf-8")
            n += 1
    return n
