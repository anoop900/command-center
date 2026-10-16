"""Fetch the run's inputs (mail, calendar feed, scoring config, profile). Every failure becomes a note; the run goes on."""
import datetime as dt
import urllib.request

from cc import config, profile, secrets
from cc.ingest import imap, recruiter
from cc.rules import score

MAX_ICS = 2_000_000


def fetch_ics(url, opener=urllib.request.urlopen):
    """The user's own feed URL from the keyring (never a URL found in content). https only, size capped."""
    if not str(url).lower().startswith("https://"):
        raise ValueError("calendar URL must start with https://")
    with opener(url, timeout=30) as r:
        return r.read(MAX_ICS).decode("utf-8", "replace")


def gather(cfg, mode, today=None, contact_emails=(), imap_fetch=imap.fetch, ics_fetch=fetch_ics):
    """-> kwargs for morning.run: msgs, ics_text, score_cfg, profile, errors."""
    today = today or dt.date.today()
    out = {"msgs": None, "ics_text": None, "score_cfg": None, "profile": "", "errors": [], "links": ""}
    pw = secrets.get("mail_password")
    if cfg.mail.host and pw:
        since = today - dt.timedelta(days=1 if mode == "morning" else 0)
        try:
            out["msgs"] = imap_fetch(cfg.mail, pw, since, extra=lambda h: recruiter.looks_recruiter(h, contact_emails))
        except Exception as e:  # IngestError and anything unexpected degrade the same way
            out["errors"].append(f"Mail fetch failed: {str(e)[:100]}")
    url = secrets.get("calendar_url") if cfg.calendar.ics_configured else None
    if url:
        try:
            out["ics_text"] = ics_fetch(url)
        except Exception as e:
            out["errors"].append(f"Calendar fetch failed: {str(e)[:100]}")
    d = config.data_dir()
    f = profile.profile_json(profile.load(d))
    out["links"] = "\n".join(f"{k}: {f[k]}" for k in ("website", "linkedin") if f.get(k))
    if (d / "scoring.toml").exists():
        try:
            out["score_cfg"] = score.load_config(d / "scoring.toml")
        except Exception as e:
            out["errors"].append(f"scoring.toml is invalid: {str(e)[:100]}")
    if (d / "Candidate_Profile.md").exists():
        out["profile"] = (d / "Candidate_Profile.md").read_text(encoding="utf-8")
    return out
