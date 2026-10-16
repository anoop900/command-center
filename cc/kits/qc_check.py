#!/usr/bin/env python3
"""Hard quality gate for a generated resume PDF.
Usage: python qc_check.py resume.pdf Candidate_Profile.md [qc_rules.json]
qc_rules.json (private, every key optional; a missing key means that check is off, except watch_terms):
  home_city                             city the contact line must show
  banned {"<regex>": "<message>"}       claims that must not appear
  never_claim ["<phrase>", ..]          plain phrases that must not appear (no regex needed)
  unless_nearby {"<regex>": "<word>"}   pattern allowed only with this word within 40 characters
  cert_needs {"<regex>": "<phrase>"}    a cert must be followed by this phrase
  pages, min_last_page_fill             exact page count and minimum fill of the last page (0-1)
  no_contractions (bool), banned_words [..]   voice rules; DEFAULT_BANNED_WORDS is a starter list
  watch_terms [..], watch_terms_add [..]      tools/credentials that must be in the profile if on the resume
"""
import json, re, sys

MAX_ITEMS = 200
MAX_TEXT = 300
DEFAULT_BANNED_WORDS = ["genuinely", "honestly", "straightforward", "resonates", "leverag", "seamless", "robust",
                        "passionate about", "welcome the opportunity", "spearhead", "synerg", "delve", "tapestry",
                        "pivotal", "cutting-edge", "game-chang", "results-driven",
                        "proven track record", "dynamic leader", "fostering", "in today's", "ever-evolving", "\u2014"]
DEFAULT_WATCH_TERMS = ["databricks", "snowflake", "gcp", "google cloud", "kafka", "datadog", "splunk", "pagerduty",
                       "new relic", "dynatrace", "prometheus", "opentelemetry", "golang", "go,", "java", "typescript",
                       "perl", "ruby", "scala", "c#", ".net", "chef", "puppet", "salt", "jenkins", "gitlab", "argo",
                       "helm", "istio", "openshift", "rancher", "nutanix", "zerto", "cisco aci", "palo alto",
                       "fortinet", "zscaler", "cyberark", "crowdstrike", "sentinelone", "iso 27001", "pci", "fedramp",
                       "hitrust", "togaf", "cissp", "ccnp", "itil 4", "itil certified", "aws certified",
                       "safe agilist", "csm", "cursor", "airtable", "langchain", "sagemaker", "bedrock", "vertex",
                       "power bi", "tableau", "synapse", "oracle data guard", "exadata", "mongodb", "cosmos db",
                       "cassandra"]


def _text(v, what):
    if not isinstance(v, str) or len(v) > MAX_TEXT:
        raise ValueError(f"{what}: text up to {MAX_TEXT} characters")
    return v


def _list(v, what):
    if not isinstance(v, list) or len(v) > MAX_ITEMS:
        raise ValueError(f"{what}: a list of up to {MAX_ITEMS} items")
    return [_text(x, what) for x in v]


def _regex_map(v, what):
    if not isinstance(v, dict) or len(v) > MAX_ITEMS:
        raise ValueError(f"{what}: an object with up to {MAX_ITEMS} entries")
    for pat, val in v.items():
        _text(pat, what), _text(val, what)
        try:
            re.compile(pat)
        except re.error as e:
            raise ValueError(f"{what}: bad pattern {pat[:40]!r} ({e})") from None
    return dict(v)


def normalize(rules):
    """Validate a qc_rules object and fill defaults. Raises ValueError with a short, plain message."""
    if not isinstance(rules, dict):
        raise ValueError("qc_rules must be an object")
    out = {"home_city": "", "banned": {}, "unless_nearby": {}, "cert_needs": {}, "pages": None,
           "min_last_page_fill": None, "no_contractions": False, "never_claim": [], "banned_words": [],
           "watch_terms": list(DEFAULT_WATCH_TERMS), "watch_terms_add": []}
    if "home_city" in rules:
        out["home_city"] = _text(rules["home_city"], "home_city").strip()
    for k in ("banned", "unless_nearby", "cert_needs"):
        if k in rules:
            out[k] = _regex_map(rules[k], k)
    if rules.get("pages") is not None:
        p = rules["pages"]
        if isinstance(p, bool) or not isinstance(p, int) or not 1 <= p <= 20:
            raise ValueError("pages: a whole number from 1 to 20, or leave it out")
        out["pages"] = p
    if rules.get("min_last_page_fill") is not None:
        f = rules["min_last_page_fill"]
        if isinstance(f, bool) or not isinstance(f, (int, float)) or not 0 <= f <= 1:
            raise ValueError("min_last_page_fill: a number from 0 to 1, or leave it out")
        out["min_last_page_fill"] = float(f)
    if "no_contractions" in rules:
        if not isinstance(rules["no_contractions"], bool):
            raise ValueError("no_contractions: true or false")
        out["no_contractions"] = rules["no_contractions"]
    for k in ("banned_words", "watch_terms", "watch_terms_add", "never_claim"):
        if k in rules:
            out[k] = _list(rules[k], k)
    for phrase in out["never_claim"]:
        if phrase.strip():
            out["banned"].setdefault(re.escape(phrase.strip().lower()), "claims '" + phrase.strip() + "'")
    return out


def read_pdf(path):
    """(text, pages, last-page fill). Needs the optional pdfplumber."""
    import pdfplumber
    with pdfplumber.open(path) as d:
        text = "\n".join(p.extract_text() or "" for p in d.pages)
        last = d.pages[-1]
        bottom = max((w["bottom"] for w in last.extract_words()), default=0)
        return text, len(d.pages), (bottom - 43) / (last.height - 86)


def norm(tok):
    return re.sub(r"\s+", "", tok.lower().replace(",", ""))


def check(text, pages, fill, profile, rules):
    """Return {"pass", "pages", "page2_fill", "issues"}. Pure: no files, no PDF library."""
    rules = normalize(rules)
    prof_low = profile.lower()
    flat = re.sub(r"\s+", " ", text)
    low = flat.lower()
    issues = []

    if rules["pages"] is not None and pages != rules["pages"]:
        issues.append(f"layout: {pages} pages, must be exactly {rules['pages']}")
    if rules["min_last_page_fill"] is not None and fill < rules["min_last_page_fill"]:
        issues.append(f"layout: last page is {fill:.0%} full, must be at least {rules['min_last_page_fill']:.0%}")

    if rules["home_city"] and rules["home_city"].lower() not in low:
        issues.append(f"contact: {rules['home_city']} missing")

    for pat, msg in rules["banned"].items():
        if re.search(pat, low):
            issues.append("banned: " + msg)
    for pat, ok_word in rules["unless_nearby"].items():
        for m in re.finditer(r".{0,40}" + pat + r".{0,40}", low):
            if ok_word not in m.group(0):
                issues.append("banned: " + pat + " -> " + m.group(0).strip()[:80])
    for pat, need in rules["cert_needs"].items():
        for m in re.finditer(pat + r"[^\n]{0,40}", low):
            if need not in m.group(0):
                issues.append("cert: shown as current -> " + m.group(0)[:60])

    if rules["no_contractions"]:
        for m in re.finditer(r"\b\w+(n't|'re|'ve|'ll|'m|'d)\b", flat, re.I):
            issues.append("voice: contraction '" + m.group(0) + "'")
    for w in rules["banned_words"]:
        if w.lower() in low:
            issues.append("voice: banned word '" + w + "'")

    prof_nums = set(norm(t).rstrip(".") for t in re.findall(r"\$?\d[\d,.]*\+?\s?[kKmM%]?", profile))
    prof_nums |= set(re.findall(r"\d+", profile))
    for tok in set(re.findall(r"\$?\d[\d,.]*\+?\s?[kKmM%]?", flat)):
        t = norm(tok).rstrip(".")
        core = re.sub(r"[^\d.]", "", t).rstrip(".")
        if not core or re.fullmatch(r"(19|20)\d\d", core) or core in {"1", "2", "3"}:
            continue
        if t not in prof_nums and core not in prof_nums:
            issues.append("number not in profile: " + tok.strip())

    for term in rules["watch_terms"] + rules["watch_terms_add"]:
        pat = r"(?<![a-z0-9])" + re.escape(term.strip(" ,")) + r"(?![a-z0-9])"
        if re.search(pat, low) and not re.search(pat, prof_low):
            issues.append("tool/credential not in profile: " + term.strip(" ,"))

    return {"pass": not issues, "pages": pages, "page2_fill": round(fill, 2), "issues": issues}


def main():
    pdf_path, profile_path = sys.argv[1], sys.argv[2]
    rules_path = sys.argv[3] if len(sys.argv) > 3 else "qc_rules.json"
    rules = json.load(open(rules_path, encoding="utf-8"))
    profile = open(profile_path, encoding="utf-8").read()
    r = check(*read_pdf(pdf_path), profile, rules)
    print(json.dumps(r, indent=1))
    sys.exit(0 if r["pass"] else 1)


if __name__ == "__main__":
    main()
