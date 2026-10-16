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
    return {"name": name,
            "contact": " | ".join(profile.contact_parts(f)),
            "headline": jobs[0]["title"],
            "profile": f"{jobs[0]['title']} at {jobs[0]['employer']}" + (f" ({jobs[0]['dates']})." if jobs[0]["dates"] else "."),
            "skills": [["Skills", skills["value"]]] if skills["ok"] and skills["value"] else [],
            "jobs": [{"company": j["employer"], "dates": j["dates"], "title": j["title"], "bullets": list(j["facts"])} for j in jobs],
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
    profile when the profile changed: always with force (a rebuild the user asked for; the old file is kept as
    content.previous.json), otherwise only if nobody edited it by hand. Returns notes for the user.
    Raises ValueError with a plain message when there is nothing to build from."""
    notes = []
    path = home / "content.json"
    exists = path.exists()
    if not exists or force or _generated_untouched(home, path):
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
