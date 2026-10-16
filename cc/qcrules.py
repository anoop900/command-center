"""Wizard page "Resume checks": the user's own rules for the resume quality gate, saved to qc_rules.json in the data
folder. Only the form's keys are written; any other key in a hand-edited file is kept. Nothing is defaulted: a blank
box means that check is off. The checks themselves live in kits/qc_check.py."""
import json
from pathlib import Path

from . import profile
from .kits import qc_check

FILE = "qc_rules.json"
FORM_KEYS = ("home_city","never_claim", "pages", "min_last_page_fill", "no_contractions",
             "banned_words", "watch_terms_add")


def _read(d):
    p = Path(d) / FILE
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        raise ValueError(f"{FILE} in your data folder is damaged. Fix or move it, then reopen this page.") from None
    if not isinstance(data, dict):
        raise ValueError(f"{FILE} in your data folder is damaged. Fix or move it, then reopen this page.")
    return data


def load(d):
    raw = _read(d)
    form = {k: raw[k] for k in FORM_KEYS if k in raw}
    other = sorted(k for k in raw if k not in FORM_KEYS)
    return {"form": form, "kept_as_is": other, "starter_banned_words": qc_check.DEFAULT_BANNED_WORDS}


def save(d, form):
    if not isinstance(form, dict):
        raise ValueError("form must be an object")
    raw = _read(d)
    for k in FORM_KEYS:
        v = form.get(k)
        if v in (None, "", [], False):
            raw.pop(k, None)    # blank means off
        else:
            raw[k] = v
    for k in form:
        if k not in FORM_KEYS:
            raise ValueError(f"unknown setting {str(k)[:40]!r}")
    qc_check.normalize(raw)
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    profile._write(d / FILE, json.dumps(raw, indent=1, ensure_ascii=False))
    return {"written": [FILE]}
