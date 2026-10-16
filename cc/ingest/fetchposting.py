"""Fetch the job description for a role from its own link, so Build kit does not have to ask for a paste.

fetch(link) -> (text, reason). text is '' when nothing usable came back and reason says why in plain words.
Links come from untrusted mail, so only https links on the known job sites below are read (DESIGN section 10,
2026-10-14), every redirect must stay on that list, and the host must resolve to a public address. Company job-board
links use the public posting APIs; other listed sites are read as a page (structured data first, then visible text).
Standard library only. opener(url) -> (status, text) can be injected for tests.
"""
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from . import boards

KNOWN_HOSTS = ("linkedin.com", "indeed.com", "builtin.com", "glassdoor.com", "ziprecruiter.com", "jobbank.gc.ca",
               "workopolis.com", "builtintoronto.com", "jobs.ca", "myworkdayjobs.com", "greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com",
               "workable.com", "icims.com", "jobvite.com")
MIN_CHARS = 200
MAX_BYTES = 2_000_000
CHARS = 20_000
_UA = "Mozilla/5.0 (compatible; commandcenter-posting/1.0; personal job search)"


def known(url):
    """True for an https link on a listed job site (the site itself or a subdomain of it)."""
    u = urllib.parse.urlparse(str(url or "").strip())
    h = (u.hostname or "").lower()
    return u.scheme == "https" and any(h == d or h.endswith("." + d) for d in KNOWN_HOSTS)


def _public(url):
    try:
        for info in socket.getaddrinfo(urllib.parse.urlparse(url).hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
    except (socket.gaierror, ValueError, TypeError):
        return False
    return True


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not known(newurl):
            raise urllib.error.URLError("the link redirects to a site that is not on the known job-site list")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url, opener=None, accept="text/html,application/json"):
    if opener:
        return opener(url)
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": accept, "Accept-Language": "en"})
    try:
        with urllib.request.build_opener(_Stay).open(req, timeout=boards.TIMEOUT) as r:
            return r.status, r.read(MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def _json(url, opener):
    status, text = _get(url, opener, "application/json")
    if status != 200:
        raise LookupError(f"HTTP {status}")
    return json.loads(text)


def _page_text(page):
    """Description from a job page: schema.org JobPosting if present, else the LinkedIn description block, else visible text."""
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, flags=re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and "JobPosting" in str(item.get("@type", "")) and item.get("description"):
                return boards.plain(item["description"], CHARS)
    m = re.search(r'class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', page, flags=re.S | re.I)
    if m:
        return boards.plain(m.group(1), CHARS)
    page = re.sub(r"<(script|style|noscript|svg|head|nav|footer)\b.*?</\1>", " ", page, flags=re.S | re.I)
    return boards.plain(page, CHARS)


def _by_ats(u, opener):
    """Posting API text for company job-board links; None when the link is not one."""
    host, parts = u.hostname.lower(), [p for p in u.path.split("/") if p]
    if host.endswith(".myworkdayjobs.com") and "job" in parts:
        parts = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        info = _json(f"https://{host}/wday/cxs/{host.split('.')[0]}/{parts[0]}/" + "/".join(parts[parts.index("job"):]), opener)
        return boards.plain(info.get("jobPostingInfo", {}).get("jobDescription", ""), CHARS)
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and len(parts) >= 3 and parts[1] == "jobs":
        return boards.plain(_json(f"https://boards-api.greenhouse.io/v1/boards/{parts[0]}/jobs/{parts[2]}", opener).get("content", ""), CHARS)
    if host == "jobs.lever.co" and len(parts) >= 2:
        j = _json(f"https://api.lever.co/v0/postings/{parts[0]}/{parts[1]}", opener)
        return boards.plain(j.get("descriptionPlain") or j.get("description", ""), CHARS)
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        for j in _json(boards.FEEDS["ashby"].format(t=urllib.parse.quote(parts[0])), opener).get("jobs", []):
            if parts[1] in str(j.get("id", "")) + str(j.get("jobUrl", "")):
                return boards.plain(j.get("descriptionPlain") or j.get("descriptionHtml", ""), CHARS)
    m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?]*-)?(\d{6,})", u.geturl())
    if m:
        return _page_text(_get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}", opener)[1])
    return None


def why_not(link):
    """Plain reason a link cannot be read automatically, or '' when it can be tried."""
    if not str(link or "").startswith("https://"):
        return "This role has no job link to read."
    if not known(link):
        return "The job link is not on a site Command Center reads (LinkedIn, Indeed, Built In, company job boards and similar)."
    return ""


def fetch(link, opener=None):
    link = str(link or "").strip()
    if why_not(link):
        return "", why_not(link)
    if not opener and not _public(link):
        return "", "The job link does not point to a public web page."
    u = urllib.parse.urlparse(link)
    try:
        text = _by_ats(u, opener)
        if text is None:
            status, page = _get(link, opener)
            if status in (404, 410):
                return "", "The posting is gone (the job site says it no longer exists)."
            if status != 200:
                return "", f"The job site refused the automatic read (HTTP {status}); open the link and paste the description."
            text = _page_text(page)
    except (LookupError, ValueError, OSError) as e:
        return "", f"Could not read the job page ({str(e)[:80] or type(e).__name__})."
    if len(text) < MIN_CHARS:
        return "", "The job page did not show a job description, usually because the site wants a sign-in."
    return text, ""
