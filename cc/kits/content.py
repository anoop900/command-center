"""Resume content for the kit builder, derived only from facts the user confirmed in the profile (nothing is invented)."""
import hashlib
import json
import os
import re
import shutil

from .. import profile


def resume_stem(name):
    """'Firstname_Lastname_Resume' from the name on the resume; one name alone gives 'Name_Resume'; none gives 'Resume'."""
    parts = [re.sub(r"[^\w.-]+", "", p).strip(".") for p in str(name or "").split()]
    return "_".join([p for p in parts if p] + ["Resume"])


def find_resume(folder, ext):
    """The kit's resume file with this extension ('pdf' or 'docx'), or None. Older kits are named resume.<ext>."""
    for p in sorted(folder.glob("*." + ext)):
        if p.stem.lower() == "resume" or p.stem.endswith("_Resume"):
            return p
    return None


_LEVERAGE = {"leveraging": "using", "leveraged": "used", "leverages": "uses", "leverage": "use"}


def _banned_words(home):
    try:
        words = json.loads((home / "qc_rules.json").read_text("utf-8")).get("banned_words") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [w.lower() for w in words if isinstance(w, str)]


def _tidy(text, banned):
    """Apply the two wording rules that have one safe fix, so a resume built from the user's own profile does not fail
    their own voice check: an em dash becomes a comma, and 'leverag*' becomes 'use*'. Other banned words are left for the
    check to report."""
    if not isinstance(text, str):
        return text
    if "\u2014" in banned:
        text = re.sub("\\s*\u2014\\s*", ", ", text)
    if any(w and w.startswith("leverag") for w in banned):
        def swap(m):
            new = _LEVERAGE[m.group(0).lower()]
            return new.capitalize() if m.group(0)[0].isupper() else new
        text = re.sub(r"\bleverag(?:ing|ed|es|e)\b", swap, text, flags=re.I)
    return text


def tidy_content(c, home):
    """Tidy the prose fields of a content dict in place (used on AI-tailored text too)."""
    banned = _banned_words(home)
    for k in ("headline", "profile", "subhead"):
        if k in c:
            c[k] = _tidy(c[k], banned)
    c["skills"] = [[_tidy(x, banned) for x in row] for row in c.get("skills", [])]
    for j in c.get("jobs", []):
        j["bullets"] = [_tidy(b, banned) for b in j.get("bullets", [])]
    return c


def from_profile(home):
    """Return the content.json dict, or None when the profile has no confirmed name and job."""
    st = profile.load(home)
    f = {k: v["value"] for k, v in st["fields"].items() if v.get("ok") and v.get("value")}
    jobs = [j for j in st["jobs"] if j["ok"]]
    name = " ".join(x for x in (f.get("firstName"), f.get("lastName")) if x)
    if not name or not jobs:
        return None
    skills = st["skills"]
    edu = ", ".join(f[k] for k in ("education", "school", "fieldOfStudy") if k in f)
    banned = _banned_words(home)
    t = lambda x: _tidy(x, banned)
    return {"name": name,
            "contact": " | ".join(profile.contact_parts(f)),
            "headline": jobs[0]["title"],
            "profile": f"{jobs[0]['title']} at {jobs[0]['employer']}" + (f" ({jobs[0]['dates']})." if jobs[0]["dates"] else "."),
            "skills": [["Skills", t(skills["value"])]] if skills["ok"] and skills["value"] else [],
            "jobs": [{"company": j["employer"], "dates": j["dates"], "title": j["title"], "bullets": [t(b) for b in j["facts"]]} for j in jobs],
            "education": [edu] if edu else []}


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _generated_untouched(home, path):
    """True when content.json is still exactly what we generated (so a profile change may replace it safely)."""
    try:
        return (home / "content.sha").read_text("utf-8").strip() == _digest(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError):
        return False


def _save_generated(home, path, data):
    _write(path, data)
    (home / "content.sha").write_text(_digest(data), encoding="utf-8")


def ensure(home, force=False):
    """Create content.json (from the profile) and a default qc_rules.json when missing, and bring it back in line with the
    profile when the profile changed: only if nobody edited it by hand (the old file is kept as content.previous.json). Returns notes for the user.
    Raises ValueError with a plain message when there is nothing to build from."""
    notes = []
    path = home / "content.json"
    exists = path.exists()
    untouched = exists and _generated_untouched(home, path)
    if not exists or untouched:
        data = from_profile(home)
        gaps = ["name and a job"] if data is None else             [label for key, label in (("skills", "skills"), ("education", "education (school or degree)")) if not data[key]]
        if gaps and not exists:
            raise ValueError("There is no resume content yet. Finish the Profile step in Setup (confirm your name, a job and its facts), then try again."
                             if data is None else "Your profile still needs confirmed " + " and ".join(gaps) + ". Add them in Setup > Profile, then try again.")
        if gaps and force:      # keep the file we have rather than block the rebuild, and say why it is not up to date
            notes.append("Resume content was not refreshed from your profile, which still needs confirmed " + " and ".join(gaps) + ". The existing content was used.")
        elif not gaps and not exists:
            _save_generated(home, path, data)
            notes.append("Resume content was built from the facts you confirmed in your profile.")
        elif not gaps and _digest(data) != _digest(_load(path)):
            shutil.copy2(path, home / "content.previous.json")
            _save_generated(home, path, data)
            notes.append("Resume content was refreshed from your profile (the file it replaced is kept as content.previous.json).")
    elif force:     # hand edits are never replaced, not even by a requested rebuild
        notes.append("Your resume content has your own edits, so it was kept as it is. To rebuild it from your profile, "
                     "rename content.json in your data folder and build again.")
    try:
        st = profile.load(home)
        from . import qc_check
        notes += [w for w in (qc_check.dates_problem(j["employer"], j["dates"]) for j in st["jobs"] if j["ok"]) if w]
    except (OSError, ValueError, KeyError):
        pass        # a notice must never stop a build
    rules = home / "qc_rules.json"
    if not rules.exists():
        _write(rules, {})
        notes.append("No resume-check rules are set, so defaults were used. Add yours under Setup > Resume checks.")
    return notes


def _load(path):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _write(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
