"""Shared email helpers. All message content is untrusted data: never executed, never fetched."""
import hashlib
import re
from email.utils import parseaddr
from html.parser import HTMLParser

MAX_PART = 1_000_000
MAX_TEXT = 200_000


def sender_domain(msg):
    """Domain of the From address only; display names are ignored because they are trivially spoofed."""
    return parseaddr(str(msg.get("From", "")))[1].rpartition("@")[2].lower()


def domain_is(domain, base):
    return domain == base or domain.endswith("." + base)


def message_id(msg):
    mid = str(msg.get("Message-ID", "")).strip()
    if mid:
        return mid[:200]
    raw = "|".join(str(msg.get(h, "")) for h in ("From", "Date", "Subject"))
    return "synthetic-" + hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _parts(msg, ctype):
    for part in msg.walk():
        if part.get_content_type() == ctype and part.get_content_disposition() != "attachment":
            raw = part.get_payload(decode=True) or b""
            yield raw[:MAX_PART].decode(part.get_content_charset() or "utf-8", errors="replace")


class _Tokens(HTMLParser):
    """Flattens HTML into ("t", text) and ("a", href, anchor text, text pieces); script/style content is dropped.
    The pieces are the anchor's separate text nodes, for cards that put company, title and place in one link."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens, self._skip, self._href, self._buf = [], 0, None, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            self._href, self._buf = dict(attrs).get("href") or "", []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            pieces = tuple(p for p in (" ".join(b.split()) for b in self._buf) if p)[:20]
            self.tokens.append(("a", self._href, " ".join("".join(self._buf).split()), pieces))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._href is not None:
            self._buf.append(data)
        elif data.strip():
            self.tokens.append(("t", " ".join(data.split())))


def html_tokens(msg):
    p = _Tokens()
    for h in _parts(msg, "text/html"):
        try:
            p.feed(h)
            p.close()
        except Exception:  # malformed markup must never stop a run
            break
    return p.tokens


def text_of(msg):
    """Plain text of the message: the text/plain part, else the text of the HTML part."""
    for t in _parts(msg, "text/plain"):
        return t[:MAX_TEXT]
    toks = html_tokens(msg)
    texts = (t[1] if t[0] == "t" else t[2] for t in toks)
    return " \n".join(x for x in texts if x)[:MAX_TEXT]


def clean(s, limit=200):
    return re.sub(r"[\x00-\x1f\x7f]", " ", s or "").strip()[:limit]
