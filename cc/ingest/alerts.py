"""Job-alert email parsers (LinkedIn, Indeed, Glassdoor, ZipRecruiter).

Layout-tolerant: a job is an anchor whose href looks like a job page on the sender's own domain; the title is the
anchor text and the next text lines are company and location. Unrecognised mail returns []; never raises.
"""
import re
from urllib.parse import parse_qs, urlparse

from . import mailparse as mp

# LinkedIn card badges that sit where a location should be; they are not places.
_BADGE = re.compile(r"\b(connections?|alum|alumni|actively recruiting|easy apply|promoted|viewed|applicants?|"
                    r"be an early applicant|school alum|hiring)\b|^\d+\s", re.I)


def is_badge(text):
    return bool(_BADGE.search(text or ""))


SOURCES = {  # sender domain -> (source, path fragments that mark a job link)
    "linkedin.com": ("linkedin", ("/jobs/view/", "/comm/jobs/view/")),
    "indeed.com": ("indeed", ("/viewjob", "/rc/clk", "/pagead/clk")),
    "glassdoor.com": ("glassdoor", ("/job-listing/", "/partner/jobListing")),
    "ziprecruiter.com": ("ziprecruiter", ("/c/", "/k/", "/jobs/")),
}


def source_of(msg):
    d = mp.sender_domain(msg)
    for base, (name, frag) in SOURCES.items():
        if mp.domain_is(d, base):
            return base, name, frag
    return None


def _job_url(href, host_base, frags):
    """The job link without tracking parameters; None when it is not an https link to a job on the sender's domain."""
    u = urlparse(href)
    if u.scheme != "https" or not mp.domain_is((u.hostname or "").lower(), host_base):
        return None
    if not any(f in u.path for f in frags):
        return None
    keep = ""
    if "jk" in parse_qs(u.query):  # Indeed's job id
        keep = "?jk=" + parse_qs(u.query)["jk"][0][:40]
    return f"https://{u.hostname}{u.path}{keep}"


def _split(line):
    for sep in (" · ", " • ", " | ", " - "):
        if sep in line:
            a, b = line.split(sep, 1)
            return a, b
    return line, ""


def parse_alert(msg):
    s = source_of(msg)
    if not s:
        return []
    base, name, frags = s
    toks = mp.html_tokens(msg)
    jobs, seen = [], set()
    for i, t in enumerate(toks):
        if t[0] != "a" or not t[2]:
            continue
        url = _job_url(t[1], base, frags)
        if not url or url in seen:
            continue
        seen.add(url)
        nxt = [x[1] for x in toks[i + 1:i + 4] if x[0] == "t"][:2]
        company, location = (_split(nxt[0]) if len(nxt) == 1 else (nxt[0], nxt[1]) if nxt else ("", ""))
        location = "" if is_badge(location) else location
        jobs.append({"source": name, "title": mp.clean(t[2]), "company": mp.clean(company),
                     "location": mp.clean(location), "url": url})
    return jobs[:100]
