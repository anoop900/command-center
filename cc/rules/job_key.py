#!/usr/bin/env python3
"""One home for role identity: the tracker key and the duplicate check.

key(company, title) is byte-identical to the key make_tracker.py and sync_db.py
used before this module existed ("company|normalised title"), so existing records
keep their ids. Nothing is re-keyed.

match_keys(company, title, url) adds looser handles used ONLY to detect duplicates:
  - the same key with legal suffixes stripped ("Acme Corp." == "Acme")
  - the posting's numeric id from its URL, prefixed by host family, so the same
    LinkedIn or Workday posting seen through two channels collapses to one role.

Idea credit: MadsLorentzen/ai-job-search tools/job_key.py (MIT) - a dedup key must
be a pure function of the posting, never re-derived by a model each run.

Usage:
  python3 job_key.py key "Acme Corp." "Sr. Cloud Architect"
  python3 job_key.py dupes roles.json            # list duplicate groups in a roles export
  python3 job_key.py check ROLES_DIR "Acme" "Cloud Architect" [URL]   # prints the duplicate's doc id, or NEW
"""
import json
import re
import sys
from urllib.parse import urlparse

_SUFFIX = re.compile(
    r"[,.]?\s+\b(inc|incorporated|ltd|limited|llc|llp|corp|corporation|co|company|plc|gmbh|ag|sa|ulc)\b\.?$")
_ID = re.compile(r"(\d{6,})")


def _title(title):
    t = re.sub(r"[^a-z0-9& ]", " ", str(title).lower())
    t = re.sub(r"\b(sr|senior)\b", "senior", t)
    return re.sub(r"\s+", " ", t).strip()


def key(company, title):
    """Canonical tracker key. Must stay identical to the original make_tracker.key."""
    return str(company).lower().strip() + "|" + _title(title)


def company_norm(company):
    c = re.sub(r"^the\s+", "", str(company).lower().strip())
    prev = None
    while prev != c:
        prev, c = c, _SUFFIX.sub("", c).strip(" ,.")
    return re.sub(r"\s+", " ", c)


def doc_id(k):
    """Store/doc id for a role key."""
    return re.sub(r"[^a-z0-9]+", "-", k.lower()).strip("-")[:120] or "x"


def url_id(url):
    """'linkedin:4012345678' style handle, or '' when the URL carries no long numeric id."""
    if not url:
        return ""
    p = urlparse(str(url))
    host = p.netloc.lower()
    m = _ID.search(p.path + "?" + p.query)
    if not m:
        return ""
    fam = ("linkedin" if "linkedin." in host else "workday" if "myworkdayjobs" in host
           else "greenhouse" if "greenhouse" in host else "lever" if "lever.co" in host
           else "ashby" if "ashbyhq" in host else host)
    return f"{fam}:{m.group(1)}"


def match_keys(company, title, url=""):
    ks = {key(company, title), company_norm(company) + "|" + _title(title)}
    u = url_id(url)
    if u:
        ks.add("url:" + u)
    return ks


def find_duplicate(company, title, url, existing):
    """existing: iterable of (key, company, title, url). Returns the first matching key or None."""
    mine = match_keys(company, title, url)
    for k, c, t, u in existing:
        if mine & match_keys(c, t, u):
            return k
    return None


def dupe_groups(rows):
    """rows: list of dicts with company, title (or role), url. Returns groups of keys sharing a handle."""
    owner, groups = {}, {}
    for r in rows:
        t = r.get("title") or r.get("role") or ""
        k = key(r.get("company", ""), t)
        for h in match_keys(r.get("company", ""), t, r.get("url") or r.get("link") or ""):
            if h in owner and owner[h] != k:
                groups.setdefault(owner[h], {owner[h]}).add(k)
            owner.setdefault(h, k)
    return [sorted(g) for g in groups.values()]


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "key":
        print(key(sys.argv[2], sys.argv[3]))
    elif len(sys.argv) in (5, 6) and sys.argv[1] == "check":
        import os
        folder = sys.argv[2]
        existing = []
        for f in sorted(os.listdir(folder)):
            if f.endswith(".json") and not f.startswith("_"):
                r = json.load(open(os.path.join(folder, f), encoding="utf-8"))
                existing.append((f[:-5], r.get("company", ""), r.get("title", ""), r.get("link", "")))
        dup = find_duplicate(sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) == 6 else "", existing)
        print(dup or "NEW")
        sys.exit(0)
    elif len(sys.argv) == 3 and sys.argv[1] == "dupes":
        data = json.load(open(sys.argv[2], encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("roles", list(data.values()))
        g = dupe_groups(rows)
        print(json.dumps(g, indent=1))
        sys.exit(1 if g else 0)
    else:
        sys.exit(__doc__)
