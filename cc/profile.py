"""Wizard step 3, form path: the user types their facts and confirms each one. Only confirmed facts reach
profile.json (the form-filler) and Candidate_Profile.md (scoring, kits, claim checks). Nothing is guessed or
defaulted into either file; a blank or unconfirmed fact is simply absent. Files live in the private data folder."""
import json
import os
import re
import shutil
from pathlib import Path

from . import config

FORM_FILE = "profile_form.json"
MAX_FIELD = 300
MAX_JOBS = 20
MAX_FACTS = 30
YESNO = ("Yes", "No")
# key, label, group, kind ("text" | "yesno"); keys are the ones extension/profile.example.json uses.
FIELDS = (
    ("firstName", "First name", "You", "text"), ("lastName", "Last name", "You", "text"),
    ("email", "Email", "You", "text"), ("phone", "Phone (application forms only)", "You", "text"),
    ("address1", "Street address", "Where you live", "text"), ("city", "City", "Where you live", "text"),
    ("province", "Province or state", "Where you live", "text"),
    ("provinceCode", "Province or state code", "Where you live", "text"),
    ("postalCode", "Postal code", "Where you live", "text"), ("country", "Country", "Where you live", "text"),
    ("linkedin", "LinkedIn address", "Links", "text"), ("website", "Website", "Links", "text"),
    ("citizenship", "Citizenship", "Work authorization", "text"),
    ("authorizedHome", "Legally entitled to work in your home country", "Work authorization", "yesno"),
    ("sponsorshipHome", "Need sponsorship in your home country", "Work authorization", "yesno"),
    ("relocate", "Willing to relocate", "Availability", "yesno"),
    ("startDate", "Earliest start date", "Availability", "text"),
    ("education", "Highest education", "Education", "text"), ("school", "School", "Education", "text"),
    ("fieldOfStudy", "Field of study", "Education", "text"),
    ("englishFluent", "Fluent in English", "Languages", "yesno"), ("frenchFluent", "Fluent in French", "Languages", "yesno"),
    # The rest are answers the form-filler extension uses (extension/profile.example.json). "list" is one entry per
    # line; "map" is one "Employer: answer" per line and is written to profile.json as an object.
    ("phoneCountryCode", "Phone country code (digits only)", "You", "text"),
    ("usAuthorizedNow", "Authorized to work in the US right now", "Work authorization", "yesno"),
    ("usAuthText", "US work authorization, in your own words", "Work authorization", "text"),
    ("clearance", "Security clearance", "Work authorization", "text"),
    ("bachelorYesNo", "Has a bachelor's degree", "Education", "yesno"),
    ("eduStart", "Education start year", "Education", "text"), ("eduEnd", "Education end year", "Education", "text"),
    ("schoolAlt", "School, alternate spelling", "Education", "text"), ("schoolOtherText", "School, 'other' answer", "Education", "text"),
    ("previouslyApplied", "Previously applied to the employer (default answer)", "Application answers", "yesno"),
    ("formerEmployers", "Employers you worked for (one per line)", "Application answers", "list"),
    ("rehire", "Rehire eligibility (one per line, as Employer: Eligible)", "Application answers", "map"),
)
KINDS = {k: kind for k, _, _, kind in FIELDS}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _text(v, what, limit=MAX_FIELD):
    if not isinstance(v, str) or len(v) > limit or not v.isprintable():
        raise ValueError(f"{what}: text only, up to {limit} characters, on one line")
    return v.strip()


def _lines(v, what, pairs):
    """A list field: text with one entry per line, or a list of strings. pairs=True needs 'Name: answer' on each."""
    if isinstance(v, str):
        v = v.split("\n")
    if not isinstance(v, list) or len(v) > MAX_FACTS * 2:
        raise ValueError(f"{what}: a list of up to {MAX_FACTS * 2} lines")
    out = [_text(x, what, 120) for x in v if not isinstance(x, str) or x.strip()]
    for x in out:
        if pairs and not (":" in x and x.split(":", 1)[0].strip() and x.split(":", 1)[1].strip()):
            raise ValueError(f"{what}: write each line as Employer: answer")
    return out


def empty():
    return {"fields": {}, "jobs": [], "skills": {"value": "", "ok": False}}


def validate(raw):
    """-> a clean form state, or ValueError naming the first bad field."""
    if not isinstance(raw, dict):
        raise ValueError("form must be an object")
    out = empty()
    fields = raw.get("fields", {})
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    for key, cell in fields.items():
        if key not in KINDS:
            raise ValueError(f"unknown field {str(key)[:40]!r}")
        if not isinstance(cell, dict) or not isinstance(cell.get("ok", False), bool):
            raise ValueError(f"{key}: bad entry")
        if KINDS[key] in ("list", "map"):
            v = _lines(cell.get("value", []), key, KINDS[key] == "map")
        else:
            v = _text(cell.get("value", ""), key)
        if v and KINDS[key] == "yesno" and v not in YESNO:
            raise ValueError(f"{key}: answer Yes or No")
        if v and key == "email" and not EMAIL.match(v):
            raise ValueError("email: that does not look like an email address")
        out["fields"][key] = {"value": v, "ok": bool(cell.get("ok")) and bool(v)}
    jobs = raw.get("jobs", [])
    if not isinstance(jobs, list) or len(jobs) > MAX_JOBS:
        raise ValueError(f"jobs: a list of up to {MAX_JOBS}")
    for j in jobs:
        if not isinstance(j, dict) or not isinstance(j.get("ok", False), bool):
            raise ValueError("jobs: bad entry")
        facts = j.get("facts", [])
        if not isinstance(facts, list) or len(facts) > MAX_FACTS:
            raise ValueError(f"facts: a list of up to {MAX_FACTS} lines")
        row = {k: _text(j.get(k, ""), k, 120) for k in ("employer", "title", "dates")}
        row["facts"] = [f for f in (_text(x, "fact", 400) for x in facts) if f]
        if not (row["employer"] and row["title"]):
            raise ValueError("each job needs an employer and a title")
        row["ok"] = bool(j.get("ok"))
        out["jobs"].append(row)
    sk = raw.get("skills", {})
    if not isinstance(sk, dict) or not isinstance(sk.get("ok", False), bool):
        raise ValueError("skills: bad entry")
    v = _text(sk.get("value", ""), "skills", 600)
    out["skills"] = {"value": v, "ok": bool(sk.get("ok")) and bool(v)}
    return out


def _cell(v):
    """Keep free text from becoming markdown structure: no table pipes, no heading or list markers at the start."""
    return v.replace("|", "/").lstrip("#-* ").strip()


def profile_json(state):
    out = {}
    for k, c in state["fields"].items():
        if not (c["ok"] and c["value"]):
            continue
        if KINDS[k] == "map":
            out[k] = {a.strip(): b.strip() for a, b in (x.split(":", 1) for x in c["value"])}
        else:
            out[k] = c["value"]
    return out


_CA = {"alberta": "AB", "british columbia": "BC", "manitoba": "MB", "new brunswick": "NB",
       "newfoundland and labrador": "NL", "nova scotia": "NS", "ontario": "ON", "prince edward island": "PE",
       "quebec": "QC", "québec": "QC", "saskatchewan": "SK", "northwest territories": "NT", "nunavut": "NU", "yukon": "YT"}


def contact_parts(f):
    """Contact-line parts from confirmed fields; the first is 'City, ON' (Canadian provinces abbreviated)."""
    city, prov = f.get("city", ""), f.get("province", "")
    raw = prov.strip()
    prov = _CA.get(raw.lower(), raw)
    shown = {p.strip().lower() for p in city.split(",")[1:]}
    place = city if not prov or prov.lower() in shown or raw.lower() in shown else ", ".join(x for x in (city, prov) if x)
    return [x for x in [place] + [f.get(k) for k in ("phone", "email", "linkedin", "website")] if x]


def markdown(state):
    f = profile_json(state)
    name = " ".join(x for x in (f.get("firstName"), f.get("lastName")) if x) or "Candidate"
    lines = [f"# Candidate Profile: {_cell(name)}", "",
             MARK + " from facts the user confirmed. Anything not in this file is unverified.", ""]
    contact = contact_parts(f)
    lines +=["## 1. Contact block (resumes only)", " | ".join(_cell(x) for x in ([name] + contact)), ""]
    jobs = [j for j in state["jobs"] if j["ok"]]
    lines += ["## 2. Employment history (exact titles and dates)", "| Employer | Title | Dates |", "|---|---|---|"]
    lines += [f"| {_cell(j['employer'])} | {_cell(j['title'])} | {_cell(j['dates'])} |" for j in jobs]
    lines += ["", "## 3. Verified facts by employer"]
    for j in jobs:
        if j["facts"]:
            lines += [f"### {_cell(j['employer'])}"] + [f"- {_cell(x)}" for x in j["facts"]]
    sk = state["skills"]
    lines += ["", "## 4. Confirmed skills and tools", f"- {_cell(sk['value'])}" if sk["ok"] else "", "",
              "## 5. Education and certifications"]
    edu = [f[k] for k in ("education", "school", "fieldOfStudy") if k in f]
    lines += [f"- {_cell(', '.join(edu))}" if edu else "", "", "## 6. Banned claims",
              "- Any number, team size, employer, tool or result not listed in this file.", ""]
    ans = []
    for label, key in (("Legal name", None), ("Email", "email"), ("Phone", "phone"), ("City", "city"),
                       ("Street address", "address1"), ("Postal code", "postalCode"), ("Citizenship", "citizenship"),
                       ("Legally entitled to work in the home country", "authorizedHome"),
                       ("Requires sponsorship in the home country", "sponsorshipHome"),
                       ("Willing to relocate", "relocate"), ("Earliest start", "startDate")):
        v = name if key is None and (f.get("firstName") or f.get("lastName")) else f.get(key) if key else None
        if v:
            ans.append(f"- {label}: {_cell(v)}.")
    lines += ["## 9. Standard application form answers"] + ans + [""]
    return "\n".join(lines)


def load(data_dir):
    p = Path(data_dir) / FORM_FILE
    if not p.exists():
        return empty()
    try:
        return validate(json.loads(p.read_text(encoding="utf-8")))
    except (ValueError, OSError, RecursionError):
        return empty()  # a damaged draft is replaced by a blank form, never half-used


def _write(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


MARK = "Written by the setup wizard"
LEGACY_MARK = "generated by tools/make_profile.py"  # the imported file: replaced only after a copy is kept


def _ours(path):
    """A file the wizard may replace: missing, or carrying its mark. A hand-written one is never overwritten."""
    if not path.exists():
        return True
    try:
        head = path.read_text(encoding="utf-8")[:2000]
    except (OSError, UnicodeDecodeError):
        return False
    return MARK in head or LEGACY_MARK in head


def save(data_dir, raw):
    """Validate, keep the form draft, and (re)write both outputs from the confirmed facts only."""
    state = validate(raw)
    d = Path(data_dir)
    for name in ("profile.json", "Candidate_Profile.md"):
        if not _ours(d / name):
            raise ValueError(f"{name} already exists and was not written by the wizard, so it was left alone. "
                             "Move or rename it first if you want the wizard to write a new one.")
    old, backup = d / "profile.json", d / "profile.previous.json"
    if old.exists() and not backup.exists() and LEGACY_MARK in old.read_text(encoding="utf-8")[:2000]:
        shutil.copyfile(old, backup)  # the imported file is kept beside the new one
    _write(d / FORM_FILE, json.dumps(state, indent=1))
    _write(d / "profile.json", json.dumps({"_source": MARK, **profile_json(state)}, indent=1))
    _write(d / "Candidate_Profile.md", markdown(state))
    from . import drafts
    drafts.clear(d, "profile")
    unconfirmed = sum(1 for c in state["fields"].values() if c["value"] and not c["ok"]) + \
        sum(1 for j in state["jobs"] if not j["ok"]) + (1 if state["skills"]["value"] and not state["skills"]["ok"] else 0)
    from .kits import qc_check
    warnings = [w for w in (qc_check.dates_problem(j["employer"], j["dates"]) for j in state["jobs"]) if w]
    return {"written": ["profile.json", "Candidate_Profile.md"], "unconfirmed": unconfirmed, "date_warnings": warnings}


# Answers a job search cannot start without. The form-filler extras (FIELDS beyond these) stay optional.
CRITICAL = ("firstName", "lastName", "email", "phone", "city", "province", "country", "citizenship",
            "authorizedHome", "sponsorshipHome", "relocate", "startDate")
RESUME_DIR = "resumes"
RESUME_EXT = (".docx", ".pdf", ".txt", ".md")


def has_resume(data_dir):
    """A resume exists: one the wizard stored, or a resume file in a kit."""
    d = Path(data_dir)
    if any(p.is_file() for p in (d / RESUME_DIR).glob("*")):
        return True
    kits = config.kits_dir(d)
    return any("resume" in p.name.lower() and p.suffix.lower() in RESUME_EXT for g in ("*/*", "*/*/*") for p in kits.glob(g))


def completeness(data_dir):
    """-> {percent, done, total, missing: [{key, label, group}]}. 100 only when every critical item is done,
    and a fact counts only once confirmed."""
    state, labels = load(data_dir), {k: (lb, g) for k, lb, g, _ in FIELDS}
    items = [(k, *labels[k], bool(state["fields"].get(k, {}).get("ok"))) for k in CRITICAL]
    items += [("jobs", "At least one job, confirmed", "Experience", any(j["ok"] for j in state["jobs"])),
              ("skills", "Skills, confirmed", "Experience", bool(state["skills"]["ok"])),
              ("resume", "At least one resume uploaded", "Resume", has_resume(data_dir)),
              ("rules", "Job-matching rules saved (pay, location, work authorization)", "Rules",
               (Path(data_dir) / "scoring.toml").is_file())]
    done = sum(1 for *_, ok in items if ok)
    return {"percent": done * 100 // len(items), "done": done, "total": len(items),
            "first_name": state["fields"].get("firstName", {}).get("value", ""),
            "missing": [{"key": k, "label": lb, "group": g} for k, lb, g, ok in items if not ok]}
