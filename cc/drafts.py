"""Unsaved-form drafts, so a half-finished profile survives a closed tab or a restart. A draft is never used by a run:
only the saved profile is. It is validated like a save (but nothing is written to the profile files) and removed when
the form is saved for real."""
import json
import os
from pathlib import Path

from . import profile

DIR = "drafts"
FORMS = {"profile": profile}
MAX_BYTES = 1 << 20


def _path(data_dir, form):
    if form not in FORMS:
        raise ValueError("unknown form")
    return Path(data_dir) / DIR / f"{form}.json"


def write(data_dir, form, raw):
    state = FORMS[form].validate(raw) if form in FORMS else None
    p = _path(data_dir, form)
    text = json.dumps(state, indent=1)
    if len(text.encode()) > MAX_BYTES:
        raise ValueError("draft too large")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)
    return {"saved": True}


def read(data_dir, form):
    """The draft state, or None when there is none or it is damaged (a damaged draft is ignored, never half-used)."""
    try:
        return FORMS[form].validate(json.loads(_path(data_dir, form).read_text(encoding="utf-8")))
    except (OSError, ValueError, RecursionError, KeyError):
        return None


def clear(data_dir, form):
    try:
        _path(data_dir, form).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass
