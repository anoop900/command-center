"""Read-only Excel copy of the roles collection, written into the data folder. openpyxl is optional (the `kits` extra)."""
import importlib
import os
import shutil
from pathlib import Path

from . import config, store


class TrackerError(Exception):
    pass


def default_path():
    return config.data_dir() / "exports" / "Applications_Tracker.xlsx"


def rows_from_store(conn):
    return [{k: ("" if v is None else str(v)) for k, v in d["data"].items()} for d in store.list_docs(conn, "roles")]


def export_rows(rows, path=None):
    """Write rows to path (default: <data>/exports/Applications_Tracker.xlsx). Returns the path written."""
    path = Path(path) if path else default_path()
    if path.suffix.lower() != ".xlsx":
        raise TrackerError("the tracker path must end in .xlsx")
    try:
        make_tracker = importlib.import_module("cc.rules.make_tracker")
    except ImportError:
        raise TrackerError("openpyxl is not installed (pip install openpyxl)") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    make_tracker.write(str(tmp), [], rows)
    try:
        os.replace(tmp, path)  # Windows refuses while Excel has the file open
    except OSError:
        tmp.unlink(missing_ok=True)
        raise TrackerError(f"could not replace {path.name}; close it in Excel and try again") from None
    return path


def rotate_copies(path=None, keep=4):
    """Shift <name>.1.xlsx .. <name>.<keep>.xlsx up one place and save the current file as .1 (the oldest drops off).
    Returns the number of copies kept; 0 when there is no tracker yet."""
    path = Path(path) if path else default_path()
    if not path.exists():
        return 0
    def copy(n):
        return path.with_name(f"{path.stem}.{n}{path.suffix}")
    copy(keep).unlink(missing_ok=True)
    for n in range(keep - 1, 0, -1):
        if copy(n).exists():
            os.replace(copy(n), copy(n + 1))
    shutil.copyfile(path, copy(1))
    return sum(copy(n).exists() for n in range(1, keep + 1))


def export(conn, path=None):
    rows = rows_from_store(conn)
    return len(rows), export_rows(rows, path)
