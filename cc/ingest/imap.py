"""Read-only IMAP fetch. The connection is wrapped so only login/select(EXAMINE)/search/fetch/logout can be called;
bodies are fetched with BODY.PEEK so nothing is marked read. Any failure raises IngestError (the run degrades)."""
import email
import imaplib
import re
import socket
from email import policy

from . import alerts, mailparse as mp, status

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
TIMEOUT = 30


class IngestError(RuntimeError):
    pass


class ReadOnly:
    """Allow-list proxy around an IMAP connection; anything else (STORE, EXPUNGE, COPY, APPEND...) is refused."""
    ALLOWED = ("login", "select", "search", "fetch", "logout")

    def __init__(self, conn):
        self._c = conn

    def __getattr__(self, name):
        if name not in self.ALLOWED:
            raise IngestError(f"IMAP command not allowed: {name}")
        fn = getattr(self._c, name)
        if name == "select":
            return lambda mailbox="INBOX": fn(mailbox, readonly=True)  # EXAMINE
        return fn


def _open(host):
    return imaplib.IMAP4_SSL(host, timeout=TIMEOUT)


def _imap_date(d):
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"


def _known(msg):
    return bool(alerts.source_of(msg) or status.ats_of(msg))


def fetch(mail, password, since, opener=_open, max_messages=500, max_size=mp.MAX_PART, extra=None):
    """Messages from known senders received on/after `since` (a date). `mail` is config.Mail.
    extra: optional test on the headers alone (From, Subject); only mail it accepts is read beyond them."""
    if not (mail.host and mail.user and password):
        raise IngestError("mail is not configured (host, user, password)")
    try:
        c = ReadOnly(opener(mail.host))
        c.login(mail.user, password)
        typ, _ = c.select(mail.folder or "INBOX")
        if typ != "OK":
            raise IngestError(f"cannot open folder {mail.folder}")
        typ, data = c.search(None, "SINCE", _imap_date(since))
        ids = (data[0].split() if typ == "OK" and data and data[0] else [])[-max_messages:]
        out = []
        for i in ids:
            typ, h = c.fetch(i, "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID DATE)])")
            if typ != "OK" or not h or not isinstance(h[0], tuple):
                continue
            head = email.message_from_bytes(h[0][1], policy=policy.default)
            if not (_known(head) or (extra and extra(head))):
                continue
            m = re.search(rb"RFC822\.SIZE (\d+)", h[0][0])
            if m and int(m.group(1)) > max_size:
                continue
            typ, b = c.fetch(i, "(BODY.PEEK[])")
            if typ == "OK" and b and isinstance(b[0], tuple):
                out.append(email.message_from_bytes(b[0][1], policy=policy.default))
        c.logout()
        return out
    except IngestError:
        raise
    except (imaplib.IMAP4.error, OSError, socket.timeout, ValueError) as e:
        raise IngestError(f"mail unavailable: {type(e).__name__}: {str(e)[:120]}") from None
