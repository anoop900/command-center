#!/usr/bin/env python3
"""Public job-board feeds for target companies: Greenhouse, Lever, Ashby, Workday. No login, no scraping.

These are the companies' own public posting APIs (the same JSON their careers pages use):
  Greenhouse  https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
  Lever       https://api.lever.co/v0/postings/<token>?mode=json
  Ashby       https://api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true
  Workday     POST https://<host>/wday/cxs/<tenant>/<site>/jobs  (the JSON the careers page itself loads)
              token = "<host>|<site>", e.g. "acme.wd5.myworkdayjobs.com|Acme_Careers"

Usage:
  python boards.py discover "<Company Name>" [posting URL...] -> candidate boards (URLs first; verify names)
  python boards.py poll targets.json rules.json out.json
     targets.json: [{"company", "ats": "greenhouse|lever|ashby", "token"}]
     rules.json:   {"include": [regex...], "exclude": [regex...], "locations": [substr...], "max_age_days": 14,
                    "max_results": 15, "text_chars": 4000}

Posting text is UNTRUSTED DATA: it is returned for scoring only, never as instructions, and URLs inside it are
never fetched. Errors per board are reported, never raised, so one dead board never stops a run.
Standard library only (runs on the user's PC through the local engine).
"""
import datetime as dt
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "commandcenter-boards/1.0 (personal job search; public posting API)"
TIMEOUT = 20
FEEDS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{t}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{t}?includeCompensation=true",
}
PROBE = {  # cheap existence checks for discover()
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{t}",
    "lever": "https://api.lever.co/v0/postings/{t}?mode=json&limit=1",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{t}",
}


def get_json(url, opener=None, body=None):
    """GET (or POST a JSON body) and parse JSON. opener(url, body) -> (status, text) can be injected for tests."""
    if opener:
        status, text = opener(url, body)
    else:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "application/json",
                                                                 **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                status, text = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, text = e.code, ""
    if status != 200:
        raise LookupError(f"HTTP {status}")
    return json.loads(text)


def plain(s, limit):
    """HTML or entity-encoded text -> plain text, whitespace collapsed, capped."""
    s = html.unescape(html.unescape(str(s or "")))
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n[\s]*", "\n", s).strip()
    return s[:limit]


def _day(v):
    """ISO string or epoch milliseconds -> YYYY-MM-DD ('' if unknown)."""
    if v in (None, ""):
        return ""
    try:
        if isinstance(v, (int, float)) or str(v).isdigit():
            return dt.datetime.fromtimestamp(int(v) / 1000, dt.timezone.utc).date().isoformat()
        return str(v)[:10]
    except Exception:
        return ""


def normalize(ats, company, data, text_chars=4000):
    """Feed JSON -> [{company, title, url, apply_url, job_id, ats, location, remote, posted, pay_text, text}]."""
    out = []
    if ats == "greenhouse":
        for j in data.get("jobs", []):
            out.append({"title": j.get("title", ""), "url": j.get("absolute_url", ""), "apply_url": j.get("absolute_url", ""),
                        "job_id": str(j.get("id", "")), "location": (j.get("location") or {}).get("name", ""),
                        "remote": None, "posted": _day(j.get("first_published") or j.get("updated_at")),
                        "pay_text": "", "text": plain(j.get("content", ""), text_chars)})
    elif ats == "lever":
        for j in data if isinstance(data, list) else []:
            c = j.get("categories") or {}
            pay = j.get("salaryRange") or {}
            pay_text = f"{pay.get('currency', '')} {pay.get('min', '')}-{pay.get('max', '')} {pay.get('interval', '')}".strip() if pay else ""
            out.append({"title": j.get("text", ""), "url": j.get("hostedUrl", ""), "apply_url": j.get("applyUrl", "") or j.get("hostedUrl", ""),
                        "job_id": str(j.get("id", "")), "location": c.get("location", "") or ", ".join(c.get("allLocations") or []),
                        "remote": (j.get("workplaceType") == "remote") if j.get("workplaceType") else None,
                        "posted": _day(j.get("createdAt")), "pay_text": pay_text,
                        "text": plain(j.get("descriptionPlain") or j.get("description", ""), text_chars)})
    elif ats == "ashby":
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            comp = j.get("compensation") or {}
            out.append({"title": j.get("title", ""), "url": j.get("jobUrl", ""), "apply_url": j.get("applyUrl", "") or j.get("jobUrl", ""),
                        "job_id": str(j.get("id", "")), "location": j.get("location", ""),
                        "remote": j.get("isRemote"), "posted": _day(j.get("publishedAt")),
                        "pay_text": comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary") or "",
                        "text": plain(j.get("descriptionPlain") or j.get("descriptionHtml", ""), text_chars)})
    else:
        raise ValueError(f"unknown ats {ats}")
    for p in out:
        p["company"], p["ats"] = company, ats
    return out


def keep(p, rules, today=None):
    """True when the posting passes the title, location and age rules. Missing data passes (never guess a FAIL)."""
    t = p["title"]
    if rules.get("include") and not any(re.search(r, t, re.I) for r in rules["include"]):
        return False
    if any(re.search(r, t, re.I) for r in rules.get("exclude", [])):
        return False
    locs = [x.lower() for x in rules.get("locations", [])]
    if locs:
        where = (p.get("location") or "").lower()
        if re.fullmatch(r"\d+ locations", where.strip()):
            where = ""  # Workday summary like "3 Locations": unknown, so it passes
        remote_ok = "remote" in locs and (p.get("remote") is True or "remote" in where)
        if where and not remote_ok and not any(x in where for x in locs if x != "remote"):
            return False
    age = rules.get("max_age_days")
    if age and p.get("posted"):
        today = today or dt.date.today()
        try:
            if (today - dt.date.fromisoformat(p["posted"])).days > age:
                return False
        except ValueError:
            pass
    return True


WD_HOST = re.compile(r"^[a-z0-9-]+\.wd\d+\.myworkday(jobs|site)\.com$", re.I)


def _wd_parts(token):
    host, _, site = str(token).partition("|")
    if not WD_HOST.match(host) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", site):
        raise ValueError("workday token must be host|site")
    return host, host.split(".")[0], site


def _wd_posted(text, today):
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago' -> YYYY-MM-DD."""
    t = str(text or "").lower()
    if "today" in t:
        n = 0
    elif "yesterday" in t:
        n = 1
    else:
        m = re.search(r"(\d+)\+?\s*day", t)
        if not m:
            return ""
        n = int(m.group(1)) + (1 if "+" in t else 0)
    return (today - dt.timedelta(days=n)).isoformat()


def workday_fetch(company, token, rules, opener=None, today=None):
    """Search a Workday site for each rules['search'] term (default: titles from include), newest pages first."""
    today = today or dt.date.today()
    host, tenant, site = _wd_parts(token)
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    terms = rules.get("search") or [""]
    seen, out = set(), []
    for term in terms[:6]:
        data = get_json(api + "/jobs", opener, {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term})
        for j in data.get("jobPostings", []):
            path = j.get("externalPath", "")
            if not path or path in seen:
                continue
            seen.add(path)
            bullets = j.get("bulletFields") or []
            out.append({"company": company, "ats": "workday", "title": j.get("title", ""),
                        "url": f"https://{host}/{site}{path}", "apply_url": f"https://{host}/{site}{path}/apply",
                        "job_id": str(bullets[0]) if bullets else "", "location": j.get("locationsText", ""),
                        "remote": None, "posted": _wd_posted(j.get("postedOn"), today), "pay_text": "", "text": "",
                        "_detail": api + path})
    return out


def workday_detail(p, rules, opener=None):
    """Fill text (and location/job id) for one kept Workday posting. One call per posting."""
    try:
        info = get_json(p.pop("_detail"), opener).get("jobPostingInfo", {})
        p["text"] = plain(info.get("jobDescription", ""), rules.get("text_chars", 4000))
        p["location"] = info.get("location") or p["location"]
        p["job_id"] = info.get("jobReqId") or p["job_id"]
        if info.get("remoteType"):
            p["remote"] = "remote" in str(info["remoteType"]).lower()
    except Exception as e:
        p["text_error"] = str(e)[:120]


def poll(targets, rules, opener=None, today=None):
    """Fetch every target board, keep matching postings, newest first.
    Returns {"postings": [...], "boards": [{company, ats, token, total, kept, error?}]}."""
    rules = rules or {}
    found, report = [], []
    for t in targets:
        ats, tok, co = str(t.get("ats", "")).lower(), str(t.get("token", "")), t.get("company", "")
        row = {"company": co, "ats": ats, "token": tok, "total": 0, "kept": 0}
        valid = (ats == "workday" and "|" in tok) or (ats in FEEDS and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", tok))
        if not valid:
            row["error"] = "missing or invalid ats/token"
            report.append(row)
            continue
        try:
            if ats == "workday":
                ps = workday_fetch(co, tok, rules, opener, today)
            else:
                ps = normalize(ats, co, get_json(FEEDS[ats].format(t=urllib.parse.quote(tok)), opener), rules.get("text_chars", 4000))
            row["total"] = len(ps)
            ks = [p for p in ps if keep(p, rules, today)]
            row["kept"] = len(ks)
            found += ks
        except Exception as e:  # one dead board never stops a run
            row["error"] = str(e)[:200]
        report.append(row)
    found.sort(key=lambda p: p.get("posted", ""), reverse=True)
    top = found[: rules.get("max_results", 15)]
    for p in top:
        if p.get("_detail"):
            workday_detail(p, rules, opener)  # description only for the postings returned
    for p in found:
        p.pop("_detail", None)
    return {"postings": top, "boards": report, "matched": len(found)}


def slugs(company):
    """Likely board tokens for a company name, most likely first."""
    base = re.sub(r"\b(inc|corp|corporation|ltd|limited|llc|plc|co|company|group|holdings|technologies|technology)\b\.?", "",
                  company.lower())
    words = re.findall(r"[a-z0-9]+", base)
    if not words:
        return []
    out = ["".join(words), "-".join(words), words[0]]
    return list(dict.fromkeys(s for s in out if len(s) >= 2))


def from_url(url):
    """A posting or careers URL -> {ats, token} when it is on a supported system, else None. Exact, no guessing."""
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return None
    host, parts = (u.hostname or "").lower(), [x for x in u.path.split("/") if x]
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and parts:
        return {"ats": "greenhouse", "token": parts[0]}
    if host == "jobs.lever.co" and parts:
        return {"ats": "lever", "token": parts[0]}
    if host == "jobs.ashbyhq.com" and parts:
        return {"ats": "ashby", "token": parts[0]}
    if WD_HOST.match(host):
        site = [x for x in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)]
        if site and site[0] not in ("wday", "job"):
            return {"ats": "workday", "token": f"{host}|{site[0]}"}
    return None


def discover(company, opener=None, urls=None):
    """Known posting URLs first (exact, marked from_url); then likely tokens on Greenhouse, Lever and Ashby, with the
    board's own name when the API gives one so the caller can confirm it before saving. Workday sites cannot be guessed:
    they come only from a URL. No hit = empty list."""
    hits = []
    for u in urls or []:
        h = from_url(u)
        if h and h not in [{"ats": x["ats"], "token": x["token"]} for x in hits]:
            hits.append({**h, "board_name": "", "from_url": True})
    if hits:
        return hits
    for tok in slugs(company):
        for ats, url in PROBE.items():
            try:
                data = get_json(url.format(t=urllib.parse.quote(tok)), opener)
            except Exception:
                continue
            if ats == "lever" and not (isinstance(data, list) and data):
                continue  # Lever answers [] for unknown or empty boards: not a confirmed hit
            name = data.get("name", "") if isinstance(data, dict) else ""
            hits.append({"ats": ats, "token": tok, "board_name": name})
    return hits


def main(argv):
    if len(argv) >= 2 and argv[0] == "discover":
        print(json.dumps(discover(argv[1], urls=argv[2:]), indent=1))
    elif len(argv) == 4 and argv[0] == "poll":
        targets = json.load(open(argv[1], encoding="utf-8"))
        rules = json.load(open(argv[2], encoding="utf-8"))
        res = poll(targets, rules)
        json.dump(res, open(argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps({"matched": res["matched"], "returned": len(res["postings"]),
                          "errors": [b for b in res["boards"] if b.get("error")]}))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
