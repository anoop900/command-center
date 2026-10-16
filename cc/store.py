"""SQLite document store with the same shape as the dashboard database.

Every record is (collection, id) -> JSON object, with a version number. A write to an existing
record must name the version it last read (if_version); a stale write is refused, never merged
silently. That rule is what stops concurrent runs from losing rows.

Every change keeps the previous copy in `history` (nothing durable disappears without a trace),
so any record can be inspected or restored.
"""
import datetime as dt
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  collection TEXT NOT NULL,
  id         TEXT NOT NULL,
  data       TEXT NOT NULL,
  version    INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (collection, id)
);
CREATE TABLE IF NOT EXISTS history (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  collection TEXT NOT NULL,
  id         TEXT NOT NULL,
  data       TEXT,
  version    INTEGER NOT NULL,
  op         TEXT NOT NULL,
  at         TEXT NOT NULL,
  by         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS history_doc ON history (collection, id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
SCHEMA_VERSION = "1"
# The record collections the engine stores. The store itself accepts any name.
COLLECTIONS = ("roles", "contacts", "targets", "tasks", "decisions", "offers", "references", "admin",
               "preps", "actions", "requests", "runs", "board",
               "kitfiles", "kitindex", "kitreviews", "reviews", "config", "usage", "jobs")


class Conflict(Exception):
    """The record changed since the caller read it (or the caller did not say which version it read)."""

    def __init__(self, collection, doc_id, current, given):
        self.current = current
        super().__init__(f"{collection}/{doc_id}: current version {current}, write named {given}")


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def connect(path, shared_close=False):
    """Open (and create if needed) the database. WAL lets readers and one writer work at once.
    shared_close: allow another thread to close the connection (it is still used by one thread only)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=not shared_close)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        migrate(conn)
    except Exception:
        conn.close()  # a corrupt file must not stay locked on Windows
        raise
    return conn


def migrate(conn):
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))


class _write:
    """BEGIN IMMEDIATE takes the write lock up front, so read-check-write is atomic."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        for attempt in range(50):
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                return self.conn
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) and "busy" not in str(e):
                    raise
                time.sleep(0.05 * (attempt + 1))
        raise sqlite3.OperationalError("database stayed locked")

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")


def _row(r):
    return None if r is None else {"id": r["id"], "data": json.loads(r["data"]), "version": r["version"],
                                   "updated_at": r["updated_at"], "updated_by": r["updated_by"]}


def get(conn, collection, doc_id):
    return _row(conn.execute("SELECT * FROM docs WHERE collection=? AND id=?", (collection, doc_id)).fetchone())


def list_docs(conn, collection, where=None):
    """All records in a collection. where: optional {field: value} equality filter on the JSON data."""
    rows = [_row(r) for r in conn.execute("SELECT * FROM docs WHERE collection=? ORDER BY id", (collection,))]
    if where:
        rows = [r for r in rows if all(r["data"].get(k) == v for k, v in where.items())]
    return rows


def collections(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT collection, COUNT(*) FROM docs GROUP BY collection ORDER BY 1")}


def _check(cur, collection, doc_id, if_version, force):
    if cur is None or force:
        return
    if if_version is None or int(if_version) != cur["version"]:
        raise Conflict(collection, doc_id, cur["version"], if_version)


# Fields the runs call .lower() on; a wrong type here would crash every run, so refuse it at write time.
STR_FIELDS = {"roles": ("key", "company", "title", "status", "decision", "link", "url", "source", "date_applied",
                        "date_found", "follow_up")}


def _clean(collection, data):
    """Drop null string fields; raise ValueError for a non-string where text is required."""
    fields = STR_FIELDS.get(collection, ())
    if not any(f in data for f in fields):
        return data
    out = {k: v for k, v in data.items() if not (k in fields and v is None)}
    for f in fields:
        if f in out and not isinstance(out[f], str):
            raise ValueError(f"{collection}.{f} must be text")
    return out


def set_doc(conn, collection, doc_id, data, if_version=None, by="", force=False):
    """Replace a record. Existing records need if_version (or force=True, used only by the importer)."""
    if not isinstance(data, dict):
        raise TypeError("data must be a JSON object")
    data = _clean(collection, data)
    with _write(conn):
        cur = get(conn, collection, doc_id)
        _check(cur, collection, doc_id, if_version, force)
        if cur is not None and cur["data"] == data:
            return cur["version"]  # no change, no new version
        version = (cur["version"] + 1) if cur else 1
        if cur is not None:
            conn.execute("INSERT INTO history(collection,id,data,version,op,at,by) VALUES (?,?,?,?,?,?,?)",
                         (collection, doc_id, json.dumps(cur["data"]), cur["version"], "set", now(), by))
        conn.execute("INSERT INTO docs(collection,id,data,version,updated_at,updated_by) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(collection,id) DO UPDATE SET data=excluded.data, version=excluded.version, "
                     "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                     (collection, doc_id, json.dumps(data, ensure_ascii=False, sort_keys=True), version, now(), by))
        return version


def update_doc(conn, collection, doc_id, fields, if_version=None, by=""):
    """Merge fields into a record (None removes a field). Creates the record if it does not exist."""
    with _write(conn):
        cur = get(conn, collection, doc_id)
        _check(cur, collection, doc_id, if_version, False)
        data = dict(cur["data"]) if cur else {}
        for k, v in fields.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        data = _clean(collection, data)
        if cur is not None and cur["data"] == data:
            return cur["version"]
        version = (cur["version"] + 1) if cur else 1
        if cur is not None:
            conn.execute("INSERT INTO history(collection,id,data,version,op,at,by) VALUES (?,?,?,?,?,?,?)",
                         (collection, doc_id, json.dumps(cur["data"]), cur["version"], "update", now(), by))
        conn.execute("INSERT INTO docs(collection,id,data,version,updated_at,updated_by) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(collection,id) DO UPDATE SET data=excluded.data, version=excluded.version, "
                     "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                     (collection, doc_id, json.dumps(data, ensure_ascii=False, sort_keys=True), version, now(), by))
        return version


def bump(conn, collection, doc_id, field, limit):
    """Atomically add 1 to a counter field unless it is already at limit. Returns the new count, or None at the limit.
    Writes no history row: a counter is not worth an audit trail."""
    with _write(conn):
        cur = get(conn, collection, doc_id)
        data = dict(cur["data"]) if cur else {}
        n = data.get(field)
        n = n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0
        if n >= limit:
            return None
        data[field] = n + 1
        conn.execute("INSERT INTO docs(collection,id,data,version,updated_at,updated_by) VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(collection,id) DO UPDATE SET data=excluded.data, version=excluded.version, "
                     "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                     (collection, doc_id, json.dumps(data, sort_keys=True), (cur["version"] + 1) if cur else 1,
                      now(), "budget"))
        return n + 1


def delete_before(conn, collection, cutoff_id):
    """Drop date-named records (YYYY-MM-DD ids) older than cutoff_id, with no history row (for counters).
    Records whose id is not a date are left alone. Returns how many were removed."""
    with _write(conn):
        return conn.execute("DELETE FROM docs WHERE collection=? AND id < ? AND id GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'",
                            (collection, cutoff_id)).rowcount


def delete_doc(conn, collection, doc_id, if_version=None, by=""):
    with _write(conn):
        cur = get(conn, collection, doc_id)
        if cur is None:
            return False
        _check(cur, collection, doc_id, if_version, False)
        conn.execute("INSERT INTO history(collection,id,data,version,op,at,by) VALUES (?,?,?,?,?,?,?)",
                     (collection, doc_id, json.dumps(cur["data"]), cur["version"], "delete", now(), by))
        conn.execute("DELETE FROM docs WHERE collection=? AND id=?", (collection, doc_id))
        return True


def history(conn, collection, doc_id, limit=20):
    return [dict(r) | {"data": json.loads(r["data"]) if r["data"] else None} for r in conn.execute(
        "SELECT * FROM history WHERE collection=? AND id=? ORDER BY seq DESC LIMIT ?", (collection, doc_id, limit))]


def backup(conn, dest_dir, keep=7):
    """Consistent copy via SQLite's online backup (safe while other processes write)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"commandcenter-{dt.date.today().isoformat()}.db"
    out = sqlite3.connect(str(target))
    with out:
        conn.backup(out)
    out.close()
    olds = sorted(dest_dir.glob("commandcenter-*.db"))
    for old in olds[:-keep] if keep else []:
        old.unlink()
    return target
