#!/usr/bin/env python3
"""ATS check. Usage: python ats_check.py resume.pdf posting.txt Candidate_Profile.md"""
import json, re, sys

TERMS = ["azure", "aws", "gcp", "google cloud", "hybrid cloud", "multi-cloud", "cloud migration", "data centre",
 "data center", "vmware", "hyper-v", "kubernetes", "aks", "eks", "docker", "containers", "terraform", "ansible",
 "infrastructure as code", "ci/cd", "devops", "devsecops", "gitops", "github", "powershell", "python",
 "site reliability", "sre", "observability", "monitoring", "logging", "alerting", "grafana", "azure monitor",
 "prometheus", "datadog", "splunk", "slo", "sli", "sla", "incident management", "incident response",
 "problem management", "change management", "itil", "itsm", "servicenow", "jira", "disaster recovery",
 "business continuity", "high availability", "failover", "resilience", "capacity planning", "finops",
 "cost optimization", "licensing", "budget", "vendor management", "contract", "outsourc", "managed service",
 "procurement", "rfp", "cybersecurity", "identity", "iam", "entra id", "active directory", "okta", "sso",
 "single sign-on", "mfa", "multi-factor", "conditional access", "rbac", "zero trust", "defender", "siem", "nist",
 "iso 27001", "pci", "soc 2", "compliance", "audit", "risk management", "governance", "privacy", "pipeda",
 "networking", "hub-and-spoke", "expressroute", "direct connect", "vpn", "firewall", "dns", "segmentation",
 "sd-wan", "microsoft 365", "m365", "copilot", "azure openai", "llm", "generative ai", "genai",
 "ai governance", "responsible ai", "agentic", "automation", "fabric", "databricks", "power bi",
 "end-user", "service desk", "workplace technology", "vdi", "citrix", "platform engineering", "self-service",
 "operating model", "playbook", "intake", "roadmap", "strategy", "architecture", "landing zone",
 "well-architected", "cloud adoption framework", "modernization", "transformation", "coaching", "mentor",
 "hiring", "talent", "stakeholder", "executive", "agile", "scrum", "pmp", "program management",
 "project management", "portfolio", "public sector", "regulated", "financial services", "healthcare"]

SYN = {"multi-factor": ["mfa"], "mfa": ["multi-factor"], "single sign-on": ["sso"], "sso": ["single sign-on"],
       "monitoring": ["observability", "grafana", "azure monitor"], "vendor management": ["vendor"],
       "business continuity": ["disaster recovery", "dr "], "risk management": ["risk"],
       "service desk": ["end-user support", "support team"], "end-user": ["end-user support"],
       "talent": ["hiring", "coach"], "transformation": ["migration", "moderniz"], "privacy": ["pipeda", "phipa"],
       "outsourc": ["outsourced"], "mentor": ["coach"], "governance": ["governance"]}

def has(t, s, syn=True):
    if re.search(r"(?<![a-z0-9])" + re.escape(t), s):
        return True
    return syn and any(re.search(r"(?<![a-z0-9])" + re.escape(x), s) for x in SYN.get(t, []))


def check(text, posting, profile):
    """Return {"pass", "coverage", ...}. Pure: takes the resume text, not a PDF."""
    profile = profile.lower()
    low = re.sub(r"\s+", " ", text.lower())
    fmt = []
    if len(low) < 2500: fmt.append("little extractable text: PDF may be image-based")
    if not re.search(r"[\w.]{1,64}@[\w.]{1,255}\.\w{1,24}", low): fmt.append("email not parseable")   # bounded: no quadratic scan
    if not re.search(r"\d{3}-\d{3}-\d{4}", low): fmt.append("phone not parseable")
    for h in ["profile", "professional experience", "education"]:
        if h not in low: fmt.append(f"standard heading missing: {h}")
    if not re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* 20\d\d", low):
        fmt.append("no month-year dates found")

    post_low = posting.lower()
    in_post = [t for t in TERMS if has(t, post_low)]
    matched = [t for t in in_post if has(t, low, syn=False)]
    add = [t for t in in_post if t not in matched and has(t, profile)]
    gaps = [t for t in in_post if t not in matched and not has(t, profile)]
    cov = round(len(matched) / max(1, len(matched) + len(add)), 2)
    return {"pass": not fmt and cov >= 0.8, "coverage": cov, "format_issues": fmt,
            "add_these_exact_wording": add, "not_in_profile_gaps": gaps, "matched": matched}


# score(): a Jobscan-style estimate (0-100). check() above stays for the format checks it already does.
WEIGHTS = {"keywords": 45, "title": 15, "quantified": 10, "years": 10, "education": 10, "format": 10}
PASS_SCORE = 80
MAX_SCORE_INPUT = 200_000
STOP = set("""a an the and or of to in for on with by at as is are be will you your our we this that from into across
including within their them they it its such other all any can may must should able ability strong excellent experience
years year work working team teams role position job candidate ideal plus preferred required requirements responsibilities
qualifications skills knowledge understanding etc using use new more who what how about well both also have has
company canada canadian ontario toronto remote hybrid office benefits salary equal opportunity employer""".split())
BOILER = {"experience", "knowledge", "proven", "strong", "excellent", "demonstrated", "bachelor", "master", "familiarity",
          "proficiency", "understanding", "expertise", "hands", "minimum", "lead", "leads", "manage", "build", "design",
          "develop", "drive", "ensure", "support", "own", "partner", "deliver", "define", "collaborate", "previous",
          "solid", "deep", "good", "great", "nice", "bonus", "must", "ideally", "preferably", "senior", "junior",
          "exceptional", "expert", "less"}
NOISE = {"salary", "compensation", "notice", "disclaimer", "benefits", "skills", "employer", "background", "bring", "area",
         "certified", "degree", "doctorate", "desirable", "ca", "us", "usa", "uk", "eu", "on", "bc", "qc", "ab",
         "computer science", "development", "information technology"}
REQ_LINE = re.compile(r"experience|knowledge|proficien|familiar|certif|skill|expert|hands-on|background in|degree|"
                      r"understanding of|preferred|required|must have|nice to have", re.I)
CAPS = re.compile(r"\b([A-Z]{2,6}s?|[A-Z][a-z]+[A-Z][A-Za-z]+|[A-Z][a-z]{2,}(?: [A-Z][a-z]{2,}){0,3})\b")
CREDS = re.compile(r"(pmp|itil|togaf|cissp|aws certified[\w -]{0,30}|"
                   r"azure solutions architect expert)", re.I)


def _str(x):
    return x[:MAX_SCORE_INPUT] if isinstance(x, str) else ""


def _toks(s):
    return re.findall(r"[a-z][a-z0-9+#./-]{1,}", s.lower())


def _word(t, s):
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", s) is not None


def _years(s):
    return [int(x) for x in re.findall(r"(?<!\d)(\d{1,2})\s*\+?\s*(?:years|yrs)", s.lower()) if 0 < int(x) <= 40]


def posting_terms(posting, company=""):
    """Skill-like words a recruiter or ATS would search: TERMS hits, plus acronyms and capitalised tech nouns on
    requirement lines, minus the company name and boilerplate."""
    posting = _str(posting).replace("\r", "")
    low = re.sub(r"\s+", " ", posting.lower())
    terms = {t for t in TERMS if has(t, low, syn=False)}
    co = set(_toks(_str(company)))
    for line in posting.split("\n"):
        if not REQ_LINE.search(line):
            continue
        for m in CAPS.findall(line):
            w = m.lower()
            ws = w.split()
            if ws[0] in STOP or ws[0] in BOILER or set(ws) & (co | NOISE) or w in NOISE:
                continue
            terms.add(w)
        terms.update(d.lower().strip() for d in CREDS.findall(line))
    return {t for t in terms if len(t) > 1}


def score(text, posting, profile, title="", company=""):
    """Resume text vs posting -> {score 0-100, pass, parts {name: [points, max]}, matched, add, gaps, format_issues}.
    add = posting words missing from the resume that the profile backs; gaps = ones it does not (never to be added)."""
    text, posting, profile, title = _str(text), _str(posting), _str(profile).lower(), _str(title)[:300]
    low = re.sub(r"\s+", " ", text.lower())
    post_low = posting.lower()
    terms = posting_terms(posting, company)
    hit = lambda t, s: has(t, s, syn=False) if t in TERMS else _word(t, s)
    matched = sorted(t for t in terms if hit(t, low))
    miss = sorted(terms - set(matched))
    add = [t for t in miss if (has(t, profile) if t in TERMS else _word(t, profile))]
    gaps = [t for t in miss if t not in add]
    kw = len(matched) / len(terms) if terms else 1.0

    tl = re.sub(r"\s+", " ", title.lower()).strip()
    core = [w for w in _toks(tl) if w not in STOP and len(w) > 2]
    t_part = 1.0 if not tl or tl in low else 0.6 * sum(_word(w, low) for w in core) / max(1, len(core))
    need, have = max(_years(posting), default=0), max(_years(text), default=0)
    y_part = 1.0 if have >= need else have / need
    edu_need = bool(re.search(r"bachelor|degree|university", post_low))
    edu_have = bool(re.search(r"bachelor|degree|diploma|b\.?sc|b\.?e\b|engineering|master|mba", low))
    lines = [x for x in text.splitlines() if len(x) > 40]
    q = sum(bool(re.search(r"\d+%|\$\s?\d|\d+\s?(k|m|million|bn)\b|\b\d{2,}\b", x.lower())) for x in lines)
    q_part = min(1.0, q / max(1, len(lines)) / 0.5)
    fmt = check(text, posting, "")["format_issues"]
    frac = {"keywords": kw, "title": t_part, "quantified": q_part, "years": y_part,
            "education": 1.0 if edu_have or not edu_need else 0.0, "format": 0.0 if fmt else 1.0}
    parts = {k: [round(WEIGHTS[k] * frac[k]), WEIGHTS[k]] for k in WEIGHTS}
    total = round(sum(WEIGHTS[k] * frac[k] for k in WEIGHTS))
    return {"score": total, "pass": total >= PASS_SCORE, "parts": parts, "matched": matched[:80], "add": add[:40],
            "gaps": gaps[:40], "format_issues": fmt, "title_on_resume": bool(tl) and tl in low,
            "years": [have, need]}


def summary(r, retry=None):
    """The compact form stored in the database for the portal (never written into a kit folder)."""
    out = {k: r[k] for k in ("score", "pass", "parts", "add", "gaps", "format_issues", "title_on_resume", "years")}
    out["matched"] = r["matched"][:40]
    if retry:
        out["retry"] = retry
    return out


def main():
    from .qc_check import read_pdf
    pdf, posting_path, profile_path = sys.argv[1:4]
    r = check(read_pdf(pdf)[0], open(posting_path, encoding="utf-8").read(), open(profile_path, encoding="utf-8").read())
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
