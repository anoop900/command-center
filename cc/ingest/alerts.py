"""Job-alert email parsers (LinkedIn, Indeed, Glassdoor, ZipRecruiter).

Layout-tolerant: a job is an anchor whose href looks like a job page on the sender's own site; the title is the
anchor text and the next text lines are company and location. Unrecognised mail returns []; never raises.
"""
import base64
import json
import re
import zlib
from urllib.parse import parse_qs, urlencode, urlparse

from . import mailparse as mp

# LinkedIn card badges that sit where a location should be; they are not places.
_BADGE = re.compile(r"\b(connections?|alum|alumni|actively recruiting|easy apply|promoted|viewed|applicants?|"
                    r"be an early applicant|school alum|hiring)\b|^\d+\s", re.I)

# Button-style anchors that share a job's link but are not its title.
_GENERIC = {"view job", "view details", "apply now", "apply", "easy apply", "learn more", "see job", "see more jobs",
            "view", "save", "yes", "no", "this is a bad match", "edit profile", "view all jobs", "see all jobs"}


def is_badge(text):
    return bool(_BADGE.search(text or ""))


SOURCES = {  # sender domain -> (source, path fragments that mark a job link, query keys worth keeping)
    "linkedin.com": ("linkedin", ("/jobs/view/", "/comm/jobs/view/"), ()),
    "indeed.com": ("indeed", ("/viewjob", "/rc/clk", "/pagead/clk"), ("jk", "ad", "mo")),
    "glassdoor.com": ("glassdoor", ("/job-listing/", "/partner/jobListing"), ("jobListingId",)),
    "ziprecruiter.com": ("ziprecruiter", ("/c/", "/k/", "/jobs/", "/km/", "/ekm/"), ()),
}
# Job links may sit on the sender's country sites (an alert from glassdoor.com links to glassdoor.ca).
_COUNTRY = ("com", "ca", "co.uk", "com.au", "co.in", "ie", "de", "fr", "nl", "be", "at", "ch", "sg", "com.hk")
_SEPS_DOT = (" · ", " • ")
_CTS_PATH = re.compile(r"/v\d{1,2}/")   # Indeed's click-tracking path carries a format number


def source_of(msg):
    d = mp.sender_domain(msg)
    for base, (name, frag, _keep) in SOURCES.items():
        if mp.domain_is(d, base):
            return base, name, frag
    return None


def _host_ok(host, base):
    brand = base.rsplit(".", 1)[0]
    return any(mp.domain_is(host, f"{brand}.{c}") for c in _COUNTRY)


def _indeed_redirect(u):
    """Indeed wraps job links as cts.indeed.com/v<n>/<base64 gzip JSON>; returns the job URL inside, or None.
    Size-capped so a hostile token cannot balloon."""
    m = _CTS_PATH.match(u.path)
    tok = u.path[m.end():].split("/")[0] if m else ""
    if not tok or len(tok) > 8000:
        return None
    try:
        raw = zlib.decompressobj(31).decompress(base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4)), 65536)
        j = json.loads(raw)
    except (ValueError, zlib.error, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(j, dict) or not isinstance(j.get("m"), dict) or j["m"].get("clickType") != "viewjob":
        return None
    return j.get("u") if isinstance(j.get("u"), str) else None


def _job_url(href, host_base, frags, keep=(), depth=0):
    """The job link without tracking parameters; None when it is not an https link to a job on the sender's site."""
    u = urlparse(href)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not _host_ok(host, host_base):
        return None
    if depth == 0 and host == "cts.indeed.com" and _CTS_PATH.match(u.path):
        inner = _indeed_redirect(u)
        return _job_url(inner, host_base, frags, keep, 1) if inner else None
    if not any(f in u.path for f in frags):
        return None
    q = parse_qs(u.query)
    kept = [(k, q[k][0][:400]) for k in keep if k in q]
    if kept and kept[0][0] == "jk":   # Indeed's job id says it all
        kept = kept[:1]
    return f"https://{host}{u.path}" + (f"?{urlencode(kept)}" if kept else "")


def _split(line, seps=(" · ", " • ", " | ", " - ")):
    for sep in seps:
        if sep in line:
            a, b = line.split(sep, 1)
            return a, b
    return line, ""


_RATING = re.compile(r"^(\d(\.\d)?\s*)?★$")
_NOT_PLACE = re.compile(r"^\$|^\d+\s*[dh]\+?$|\bEst\.|^new$", re.I)


def _glassdoor_card(pieces):
    """Glassdoor puts the whole card in one link: company, '3.9 ★', title, place, then pay, badges and age.
    Returns (company, title, place) or None."""
    pieces = [p for p in pieces if p]
    r = next((i for i, p in enumerate(pieces) if _RATING.match(p)), None)
    head, rest = (pieces[:r], pieces[r + 1:]) if r is not None else (pieces[:1], pieces[1:])
    if not head or not rest:
        return None
    place = rest[1] if len(rest) > 1 and not _NOT_PLACE.search(rest[1]) and not is_badge(rest[1]) else ""
    return " ".join(head), rest[0], place


def parse_alert(msg):
    s = source_of(msg)
    if not s:
        return []
    base, name, frags = s
    keep = SOURCES[base][2]
    toks = mp.html_tokens(msg)
    jobs, seen = [], set()
    for i, t in enumerate(toks):
        if t[0] != "a" or not t[2] or mp.clean(t[2]).lower() in _GENERIC:
            continue
        url = _job_url(t[1], base, frags, keep)
        if not url or url in seen:
            continue
        title, company, location = t[2], "", ""
        card = _glassdoor_card(t[3] if len(t) > 3 else ()) if name == "glassdoor" else None
        if card:
            company, title, location = card
        else:
            nxt = [x[1] for x in toks[i + 1:i + 4] if x[0] == "t"][:2]
            company, location = (_split(nxt[0]) if len(nxt) == 1 else (nxt[0], nxt[1]) if nxt else ("", ""))
            # LinkedIn and ZipRecruiter cards read 'Company · Place': the place is not part of the company.
            company, place = _split(company, _SEPS_DOT)
            location = place or ("" if is_badge(location) else location)
        same = (title.lower(), company.lower(), location.lower())   # one job, several tracking links
        if same in seen:
            continue
        seen.update((url, same))
        jobs.append({"source": name, "title": mp.clean(title), "company": mp.clean(company),
                     "location": mp.clean(location), "url": url})
    return jobs[:100]
