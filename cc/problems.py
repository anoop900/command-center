"""One place that turns any failure into a short message plus one thing the user can do about it.

describe(x) takes an exception or text and returns {"code", "message", "fix", "action", "detail"}. `action` names an existing
Setup page (profile, qc, ai, modes, backup, kits, posting) or is ""; `detail` is the original text for the log, never the headline.
"""
import re

# (code, pattern on the lowercased text, message, fix, action). First match wins, so specific entries come first.
_CATALOGUE = [
    ("kit_folder_locked", r"cannot write the kit folder|close any open kit files|kit folder.*(locked|in use)",
     "The kit could not be saved because a file in its folder is open.",
     "Close the resume or Word file if it is open, then press Try again.", ""),
    ("no_posting", r"no job link|posting text is empty|paste the job posting",
     "There is no job posting to read for this role.", "Paste the job description text, then press Build kit.", "posting"),
    ("tools_missing", r"python-docx|pdfplumber|resume tools are not installed|not installed",
     "A part the resume builder needs is not installed.", "Run the installer again, then press Try again.", ""),
    ("ai_budget", r"budget|calls per run",
     "The AI call limit for this run was reached.", "Wait for the next run, or raise the limit in Setup > Modes.", "modes"),
    ("ai_no_answer", r"runner failed|did not answer|ai step did not run|no answer|unavailable",
     "The AI did not answer.", "Check the AI service in Setup > AI, then press Try again.", "ai"),
    ("ai_waiting", r"waiting for (your )?pasted answer",
     "Your AI mode is waiting for an answer you paste.", "Open the AI cards, paste the answer, then press Check again.", ""),
    ("profile_gap", r"no resume content|profile still needs|finish the profile|confirmed (name|skills|education)|candidate_profile",
     "Your profile is missing something the resume needs.", "Open Setup > Profile, confirm your name, a job, skills and education, then try again.", "profile"),
    ("qc_rules_bad", r"qc_rules|resume check rules|resume-check rules",
     "Your resume check rules could not be read.", "Open Setup > Resume checks and save them again.", "qc"),
    ("content_bad", r"content\.json",
     "Your resume content file could not be read.", "Open Setup > Profile and save it again to rebuild the resume content.", "profile"),
    ("config_bad", r"config\.toml|config error|configerror|unknown (mode|preset)|\[ai\]|\[mode\]|\[budget\]",
     "A setting could not be read.", "Open Setup and save the page you were on. If it keeps happening, restore the latest backup in Setup > Backup.", "backup"),
    ("db_busy", r"database (is )?(busy|locked)|database is locked",
     "Command Center is busy with another task.", "Wait a few seconds and try again.", ""),
    ("db_bad", r"database disk image|malformed|not a database|sqlite",
     "The data file looks damaged.", "Restore the latest backup in Setup > Backup.", "backup"),
    ("backup_bad", r"backup|ccbackup|restore",
     "The backup step did not work.", "Check the file is a .ccbackup made by Command Center, then try again.", "backup"),
    ("folder_open", r"could not open the folder|could not change the kit folder",
     "The folder could not be opened or changed.", "Check the folder exists and you can write to it, or pick another in Setup > Kit folder.", "kits"),
    ("bad_input", r"body (must be|too large)|bad content-length|text expected|expected|invalid|must be|too long|not allowed",
     "That input was not accepted.", "Check what you typed or chose, then try again.", ""),
    ("blocked_request", r"bad host|cross-site",
     "The request was blocked for safety.", "Open Command Center from its own address and try again.", ""),
    ("not_found", r"not found|missing|no such|unknown collection|no role",
     "That item could not be found.", "Refresh the page. If it is gone, add it again.", ""),
    ("network", r"timed? ?out|connection|network|unreachable|dns|ssl|certificate",
     "A network request did not work.", "Check your internet connection, then try again.", ""),
    ("disk", r"permission|access is denied|no space|disk full|read-only|errno",
     "Command Center could not read or write a file.", "Check the folder is writable and the disk has space, then try again.", ""),
]
_FALLBACK = ("unknown", "Something went wrong.", "Try again. If it keeps happening, restore the latest backup or ask for help with the Details text.", "")

# qc issue prefixes -> (what to tell the user, what to do)
_QC = [
    (r"^voice: banned word|^banned:", "uses words or phrases your rules ban",
     "Reword those lines in Setup > Profile, or remove the word in Setup > Resume checks, then press Rebuild kit.", "profile"),
    (r"^voice: contraction", "uses contractions your rules do not allow",
     "Reword those lines in Setup > Profile (write \"do not\", not \"don't\"), then press Rebuild kit.", "profile"),
    (r"^layout:|fills only|pages?, not|page target", "does not fit the page rules",
     "Shorten or lengthen the profile text in Setup > Profile, or change the page rule in Setup > Resume checks, then press Rebuild kit.", "profile"),
    (r"^contact:", "is missing your city",
     "Add your city in Setup > Profile, then press Rebuild kit.", "profile"),
    (r"^(number|date range|tool/credential) not in profile|^cert:", "says something your profile does not confirm",
     "Confirm it in Setup > Profile, or remove it from the resume text, then press Rebuild kit.", "profile"),
]


def _text(x):
    if isinstance(x, BaseException):
        return str(x) or type(x).__name__
    return str(x or "")


def describe(x, detail=None):
    """Return {"code", "message", "fix", "action", "detail"} for an exception or a text. Never raises."""
    raw = _text(x).strip()[:1000]     # hostile or huge text must not make the matching slow
    low = raw.lower()
    for code, pat, message, fix, action in _CATALOGUE:
        if re.search(pat, low):
            break
    else:
        code, message, fix, action = _FALLBACK
    own = not isinstance(x, BaseException) or type(x).__module__.startswith("cc") or isinstance(x, ValueError)
    if raw and own and _is_plain(raw):      # our own raise sites already write plain, specific sentences: keep them
        message = raw[0].upper() + raw[1:] + ("" if raw[-1] in ".!?" else ".")
    return {"code": code, "message": message, "fix": fix, "action": action, "detail": (detail if detail is not None else raw)[:400]}


def describe_qc(issues):
    """One plain sentence for a resume that failed its checks: what kind of problem, the first few examples, and what to do."""
    issues = [str(i) for i in issues or []]
    if not issues:
        return {"code": "qc_failed", "message": "The resume did not pass its checks.",
                "fix": "Open Setup > Resume checks to see the rules, then press Rebuild kit.", "action": "qc", "detail": ""}
    for pat, what, fix, action in _QC:
        hits = [i for i in issues if re.search(pat, i)]
        if hits:
            eg = "; ".join(re.sub(r"^[a-z/ ]+: ?", "", h)[:40] for h in hits[:3])
            more = f" and {len(issues) - 1} more" if len(issues) > 1 and len(hits) < len(issues) else ""
            return {"code": "qc_failed", "message": f"The resume {what} ({eg}{more}).", "fix": fix, "action": action,
                    "detail": " | ".join(issues)[:400]}
    return {"code": "qc_failed", "message": "The resume did not pass its checks.",
            "fix": "Open Setup > Resume checks to see the rules, then press Rebuild kit.", "action": "qc", "detail": " | ".join(issues)[:400]}


def for_body(body):
    """Add fix/code/action to an API body that carries `error`, keeping `error` as the plain message. Returns a new dict."""
    if not isinstance(body, dict) or not body.get("error") or body.get("fix"):
        return body
    d = describe(body["error"])
    return dict(body, error=d["message"], code=d["code"], fix=d["fix"], action=d["action"], detail=d["detail"])


def _is_plain(text):
    t = str(text)
    return len(t) < 300 and not re.search(r"Traceback|\w+Error\b|Exception|errno|['\"]\w+['\"]\s*:|\bline \d+", t, re.I)


def line(p):
    """message + fix as one string, for the CLI and for places that show a single line."""
    return f"{p['message']} {p['fix']}".strip()
