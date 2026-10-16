"""Application-status emails from the big ATS senders (Workday, Greenhouse, Lever, SuccessFactors).

kind is received | rejected | offer | interview | unknown. Rejection wins over receipt because rejection mails often thank
the candidate for applying. Mail from other senders returns None. Never raises.
"""
import re
from email.utils import parseaddr

from . import mailparse as mp

ATS = {"myworkday.com": "workday", "workday.com": "workday", "greenhouse.io": "greenhouse",
       "greenhouse-mail.io": "greenhouse", "lever.co": "lever", "successfactors.com": "successfactors",
       "successfactors.eu": "successfactors", "sapsf.com": "successfactors"}
REJECTED = re.compile(r"not (?:be )?(?:moving|proceeding)|unfortunately|regret to inform|other candidates|"
                      r"will not be (?:moving|proceeding)|decided to pursue", re.I)
# An offer needs an affirmative phrase; bare "offer" is far too common (special offer, offer to help).
# Adjectives cover revised / updated / final / signed ... letters. Bounded repeats only: mail is untrusted.
OFFER = re.compile(r"(?:pleased|happy|delighted|excited|thrilled)\s+to\s+(?:formally\s+)?(?:offer|extend)\b|"
                   r"\bextend(?:ing)?\s+(?:to\s+you\s+)?(?:an?|the)\s+(?:\w+\s+){0,2}offer\b|"
                   r"\b(?:(?:revised|updated|amended|final|formal|written|signed|countersigned|new|verbal)\s+)?"
                   r"(?:job\s+)?offer\s+(?:letter|of\s+employment|package|details)\b|\bjob\s+offer\b|\byour\s+offer\b", re.I)
# These beat everything: the company is saying there is NO offer (or taking it back).
OFFER_NO = re.compile(r"not\s+(?:be\s+)?(?:extending|making)\s+(?:you\s+)?(?:an?|any)\s+offer|unable\s+to\s+(?:extend|make)\s+"
                      r"(?:you\s+)?(?:an?|any)\s+offer|(?:rescind|withdraw)\w*\s+(?:the|our|your|this)\s+(?:\w+\s+)?offer", re.I)
VARIANT = re.compile(r"\b(revised|updated|amended)\b|\b(signed|countersigned)\b", re.I)
INTERVIEW = re.compile(r"\binterview\b|schedule a (?:call|time|conversation)|invite you to (?:meet|speak)", re.I)
RECEIVED = re.compile(r"received your application|thank(?:s| you) for applying|application (?:has been )?received|"
                      r"thank you for your interest", re.I)
COMPANY = re.compile(r"(?:applying to|application (?:to|with|at)|interest in|joining)\s+([A-Z][\w&.' -]{1,60}?)"
                     r"(?=[!.,:\n]| for | - | as |$)")
TITLE = re.compile(r"(?:for the|position of|role of|for)\s+([A-Z][\w&/.,' -]{2,80}?)\s+(?:position|role)\b|"
                   r"(?:Application for|applying for)\s+([A-Z][\w&/.,' -]{2,80}?)(?=[!.,\n]| at | - |$)")


def ats_of(msg):
    d = mp.sender_domain(msg)
    for base, name in ATS.items():
        if mp.domain_is(d, base):
            return name
    return None


def classify_text(subject, text):
    """Order: an explicit "no offer" wins; an offer phrase in the subject beats stray rejection words ("unfortunately the
    start date moved"); then rejection, offer in the body, interview, receipt."""
    if OFFER_NO.search(text):
        return "rejected"
    if OFFER.search(subject):
        return "offer"
    if REJECTED.search(text):
        return "rejected"
    return ("offer" if OFFER.search(text) else "interview" if INTERVIEW.search(text)
            else "received" if RECEIVED.search(text) else "unknown")


def variant_of(text):
    m = VARIANT.search(text)
    return "revised" if m and m.group(1) else "signed" if m else "offer"


def parse_offer(msg):
    """Offer mail from a sender that is not an ATS. -> {kind: "offer", variant, company, title} or None. Never raises."""
    subject = mp.clean(str(msg.get("Subject", "")), 300)
    text = subject + "\n" + mp.text_of(msg)[:4000]
    if classify_text(subject, text) != "offer":
        return None
    m, t = COMPANY.search(text), TITLE.search(text)
    return {"kind": "offer", "variant": variant_of(text), "company": mp.clean(m.group(1), 80) if m else "",
            "title": mp.clean((t.group(1) or t.group(2)) if t else "", 120)}


def parse_status(msg):
    ats = ats_of(msg)
    if not ats:
        return None
    subject = mp.clean(str(msg.get("Subject", "")), 300)
    text = subject + "\n" + mp.text_of(msg)[:4000]
    kind = classify_text(subject, text)
    m = COMPANY.search(text)
    company = m.group(1) if m else re.sub(r"\b(?:recruiting|careers|talent|jobs|hr|no-?reply)\b", "",
                                          parseaddr(str(msg.get("From", "")))[0], flags=re.I)
    t = TITLE.search(text)
    title = (t.group(1) or t.group(2)) if t else ""
    return {"kind": kind, "variant": variant_of(text), "ats": ats, "company": mp.clean(company, 80), "title": mp.clean(title, 120)}
