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
    if not re.search(r"[\w.]+@[\w.]+\.\w+", low): fmt.append("email not parseable")
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


def main():
    from .qc_check import read_pdf
    pdf, posting_path, profile_path = sys.argv[1:4]
    r = check(read_pdf(pdf)[0], open(posting_path, encoding="utf-8").read(), open(profile_path, encoding="utf-8").read())
    print(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
