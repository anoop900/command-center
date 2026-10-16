"""The one capability table (DESIGN section 4). Wizard, dashboard panel, runs and docs read this; nothing else
hard-codes mode behaviour."""

MODES = ("autopilot", "assisted", "chat", "no-ai")
DEFAULT = "no-ai"

# Cell values: "yes"/"auto" run without the user, "on request"/"when started"/"paste..." need a user step, "no" is off.
# A feature is "ai" when any mode relies on an AI step for it; the rest are deterministic in every mode.
_ROWS = (
    ("ingest_mail", "Alerts and status emails read, dedup, tracker", False, ("auto", "auto", "auto", "auto")),
    ("interview_invites", "Interview invites (calendar)", False, ("yes", "yes", "yes", "yes")),
    ("board_feeds", "Company job-board feeds", False, ("yes", "yes", "yes", "yes")),
    ("gates", "Pay / location / work-authorization gates", False, ("yes", "yes", "yes", "yes")),
    ("reminders", "Reminders, people, tasks, offers", False, ("yes", "yes", "yes", "yes")),
    ("fit_scoring", "Fit scoring", True, ("auto", "when started", "paste batch", "keyword score only")),
    ("kits", "Tailored resume + answers", True, ("auto (top roles)", "on request", "paste per kit", "base resume")),
    ("claim_review", "Claim trace + independent review", True, ("yes", "yes", "paste", "no")),
    ("interview_prep", "Interview prep from stories", True, ("auto", "on request", "paste", "no")),
    ("cover_letter", "Cover letter (one page, only when you press the button)", True, ("on request", "on request", "paste", "no")),
    ("coach", "Daily improvement coaching (wording and praise)", True, ("auto", "on request", "paste", "plain wording")),
    ("linkedin_reading", "LinkedIn reading beyond alert emails", True,
     ("browser-capable AI only", "same", "no", "no")),
    ("learning_ai", "Active learning: an AI suggests title terms to avoid (optional, off until you turn it on)", True,
     ("if turned on", "if turned on", "no", "no")),
    ("form_filler", "Form-filler extension", False, ("yes", "yes", "yes", "yes")),
    ("phone_dashboard", "Phone dashboard", False, ("later", "later", "later", "later")),
)

LABELS = {"autopilot": "Autopilot", "assisted": "Assisted", "chat": "Chat", "no-ai": "No AI"}
UPGRADE_ORDER = ("no-ai", "chat", "assisted", "autopilot")  # cheapest to most automatic

# One line per off cell (not "later"): why it is off. test_modes checks none is missing.
REASONS = {
    ("claim_review", "no-ai"): "Needs an AI to compare each claim with your profile.",
    ("interview_prep", "no-ai"): "Needs an AI to draft answers from your stories.",
    ("cover_letter", "no-ai"): "Needs an AI to write a letter in your own words.",
    ("learning_ai", "chat"): "Needs an AI that runs by itself; the offline learning still works.",
    ("learning_ai", "no-ai"): "Needs an AI; the offline learning still works.",
    ("linkedin_reading", "chat"): "Needs an AI that can browse; a chatbot cannot open LinkedIn for you.",
    ("linkedin_reading", "no-ai"): "Needs an AI that can browse. Alert emails are still read.",
}

FEATURES = tuple(k for k, *_ in _ROWS)
_TABLE = {k: dict(zip(MODES, cells)) for k, _, _, cells in _ROWS}
_LABEL = {k: label for k, label, _, _ in _ROWS}
_AI = {k: ai for k, _, ai, _ in _ROWS}


class ModeError(ValueError):
    pass


def uses_provider(mode):
    """True for the modes that call an AI provider themselves (Chat pastes; No AI sends nothing)."""
    return mode in ("assisted", "autopilot")


def check(mode):
    if mode not in MODES:
        raise ModeError(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
    return mode


def label(feature):
    return _LABEL[feature]


def uses_ai(feature):
    return _AI[feature]


def capability(feature, mode):
    """The table cell: what this feature does in this mode."""
    if feature not in _TABLE:
        raise ModeError(f"unknown feature {feature!r}")
    return _TABLE[feature][check(mode)]


def is_off(feature, mode):
    return capability(feature, mode) in ("no", "later")


def is_automatic(feature, mode):
    """Runs on a schedule with no user step. No-AI is deterministic, so everything on in it is automatic."""
    if is_off(feature, mode):
        return False
    return mode == "no-ai" or capability(feature, mode).split(" ")[0] in ("yes", "auto")


def needs_user_step(feature, mode):
    """On, but only after the user starts it or pastes an answer, or when the AI backend allows it."""
    return not is_off(feature, mode) and not is_automatic(feature, mode)


def schedules_ai(mode):
    """True only in autopilot: the scheduler may call an AI backend with nobody present."""
    return check(mode) == "autopilot"


def rows(mode):
    """Everything a panel needs for one mode, in table order."""
    check(mode)
    return [{"feature": k, "label": _LABEL[k], "ai": _AI[k], "value": _TABLE[k][mode],
             "off": is_off(k, mode), "automatic": is_automatic(k, mode)} for k in FEATURES]


def panel(mode):
    """What the dashboard's Mode panel shows: badge, switched-off list with reasons, user-started list, next step up."""
    check(mode)
    off, on_request, later = [], [], []
    for k in FEATURES:
        cell = capability(k, mode)
        if cell == "later":
            later.append({"label": _LABEL[k]})
        elif is_off(k, mode):
            off.append({"label": _LABEL[k], "reason": REASONS[(k, mode)]})
        elif not is_automatic(k, mode):
            on_request.append({"label": _LABEL[k], "how": cell})
    i = UPGRADE_ORDER.index(mode)
    up = UPGRADE_ORDER[i + 1] if i + 1 < len(UPGRADE_ORDER) else None
    nxt = {"mode": up, "label": LABELS[up], "line": f'[mode]\nname = "{up}"'} if up else None
    return {"mode": mode, "label": LABELS[mode], "off": off, "on_request": on_request, "later": later, "next": nxt}


# Shown at the top of the Ask panel; modes that can answer on their own have no entry.
ASK_NOTES = {
    "no-ai": "No AI is set up. Ask needs an AI to answer questions in plain language, so it cannot answer yet. Everything else in Command Center works without one. To use Ask, choose an AI mode in Setup.",
    "chat": "Chat mode. Ask writes a question for you to paste into any chatbot, then you paste the answer back (see the AI cards).",
}
# Plain words shown on each mode card in the wizard: a tagline, what it does, what it costs, what leaves the computer.
TAGLINES = {"no-ai": "No AI at all", "chat": "AI by copy and paste", "assisted": "AI when you click",
            "autopilot": "AI runs by itself"}
SUMMARY = {
    "no-ai": "No AI is used. Roles are scored by keyword match and resumes are built from your base resume. Everything else works.",
    "chat": "Nothing to connect. The app writes a prompt, you paste it into any chatbot (ChatGPT, Claude, Gemini and so on) and paste the answer back.",
    "assisted": "You connect an AI account in the next step. The app uses it only when you click an AI button (score, tailor a resume, review, interview prep). Nothing AI runs on its own.",
    "autopilot": "You connect an AI account in the next step. The AI buttons work, and the AI also runs by itself on every morning and midday run, up to a daily limit.",
}
NOTES = {
    "no-ai": ("Free.", "Nothing leaves your computer."),
    "chat": ("Free if you already have a chatbot subscription.", "Only what you paste into the chatbot leaves your computer."),
    "assisted": ("Costs whatever your AI account charges, only for the steps you start.",
                 "Job posting and profile text go to your AI provider when you click an AI button."),
    "autopilot": ("Costs whatever your AI account charges, up to the daily call limit (100 by default).",
                  "Job posting and profile text go to your AI provider on every run."),
}


def table():
    """Everything the wizard's choose-AI step shows: modes with notes, and every feature's cell in every mode."""
    return {
        "modes": [{"name": m, "label": LABELS[m], "tagline": TAGLINES[m], "summary": SUMMARY[m], "cost": NOTES[m][0],
                  "privacy": NOTES[m][1]} for m in UPGRADE_ORDER],
        "features": [{"key": k, "label": _LABEL[k], "ai": _AI[k], "cells": dict(_TABLE[k])} for k in FEATURES],
    }

# Headless agent CLIs (DESIGN section 11: flags change, so each entry carries the date its flags were read from
# `--help`). The prompt goes in on stdin. Tools are disabled or sandboxed read-only because postings are untrusted.
# Deliberately not listed (read from --help and a real run, 2026-10-11): `gemini -p` takes the prompt on stdin but needs
# a key or login to answer, so a reply could not be verified here, and in an untrusted folder it drops to approval
# prompts. `opencode run` answers over stdin but has no flag to turn its tools (web fetch) off, which the untrusted-
# input rule requires. Use the Gemini API or OpenRouter provider instead; add a CLI here only after a verified reply.
CLI_RUNNERS = {
    "claude": {"argv": ["claude", "-p", "--tools", "", "--no-session-persistence", "--output-format", "text"],
               "checked": "2026-10-10"},
    "codex": {"argv": ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral"],
              "checked": "2026-10-10"},
}
