"""Recruiter / hiring-manager mail: a cheap header test to decide what to read, then a conservative classifier.

All content is untrusted data. The classifier only extracts a sender name, address, company guess and kind.
"""
import re
from email.utils import parseaddr

from . import alerts, mailparse as mp, status

SUBJECT = re.compile(r"opportunit|your background|your profile|role at|position at|schedule a (?:call|chat)|"
                     r"quick (?:call|chat)|recruit|hiring|talent|connect about|intro call", re.I)
BODY_HIRING = re.compile(r"\bhiring manager\b|\bi(?:'m| am) (?:the|a) .{0,40}\b(?:manager|director|vp|head)\b|\bmy team\b", re.I)
BULK_LOCAL = re.compile(r"no-?reply|do-?not-?reply|notification|mailer|bounce|alerts?$|^jobs?$|^careers?$|newsletter|digest", re.I)
FREEMAIL = {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "live.com", "proton.me", "protonmail.com",
            "aol.com", "msn.com"}
SECOND_LEVEL = {"co", "com", "org", "net", "gov", "ac"}


def looks_recruiter(head, contact_emails=()):
    """Header-only test (From, Subject): is this mail worth reading? True for known contacts or a recruiting subject."""
    addr = parseaddr(str(head.get("From", "")))[1].lower()
    if not addr or BULK_LOCAL.search(addr.partition("@")[0]):
        return False
    subject = str(head.get("Subject", ""))[:300]
    # offer mail from anyone gets its body read; classify() is untouched, so it never creates a contact
    return addr in {e.lower() for e in contact_emails} or bool(SUBJECT.search(subject)) or bool(status.OFFER.search(subject))


def company_of(domain):
    if not domain or domain in FREEMAIL:
        return ""
    parts = domain.split(".")
    base = parts[-3] if len(parts) >= 3 and parts[-2] in SECOND_LEVEL else parts[-2] if len(parts) >= 2 else parts[0]
    return base.replace("-", " ").title()


def classify(msg):
    """-> {name, email, company, kind: recruiter|hiring} or None (bulk, job alert, ATS, no display name, no signal)."""
    if alerts.source_of(msg) or status.ats_of(msg):
        return None
    name, addr = parseaddr(str(msg.get("From", "")))
    addr = addr.lower()
    name = mp.clean(name, 80)
    if not addr or "@" not in addr or not name or BULK_LOCAL.search(addr.partition("@")[0]):
        return None
    subject = mp.clean(str(msg.get("Subject", "")), 300)
    if not SUBJECT.search(subject):
        return None
    kind = "hiring" if BODY_HIRING.search(mp.text_of(msg)[:3000]) else "recruiter"
    return {"name": name, "email": addr, "company": company_of(mp.sender_domain(msg)), "kind": kind}
