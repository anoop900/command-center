"""Resume upload for the setup wizard: pull text out of a .txt/.md/.docx/.pdf and propose contact facts.
The file is untrusted data: it is only read as text and matched with fixed patterns, never followed or executed.
Everything proposed comes back unconfirmed (ok False); the user ticks what is true. Nothing is guessed."""
import base64
import binascii
import html
import io
import re
import zipfile
from pathlib import Path

MAX_BYTES = 5 << 20
MAX_UNZIPPED = 8 << 20
MAX_CHARS = 60_000
MAX_PDF_PAGES = 10

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(\+?\d[\d ()./-]{8,17}\d)(?!\d)")
LINKEDIN = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_%-]{2,100}/?", re.I)
URL = re.compile(r"https?://[^\s<>\"')]{4,200}", re.I)
YEAR = re.compile(r"^(?:19|20)\d\d$")
NAME_WORD = re.compile(r"^[A-Z][a-zA-Z'.-]{1,24}$")


def decode_upload(data):
    """base64 text from the browser -> bytes, or ValueError."""
    if not isinstance(data, str) or not data:
        raise ValueError("no file received")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("the file did not arrive intact; try again") from None
    if len(raw) > MAX_BYTES:
        raise ValueError("file too large (limit 5 MB)")
    return raw


def _docx_text(raw):
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        info = z.getinfo("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("this does not look like a .docx file") from None
    if info.file_size > MAX_UNZIPPED:
        raise ValueError("the document is too large to read")
    xml = z.read(info).decode("utf-8", "replace")
    xml = re.sub(r"</w:p>|<w:br\s*/?>|<w:tab\s*/?>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def _pdf_text(raw):
    try:
        import pdfplumber
    except ImportError:
        raise ValueError("reading a PDF needs a library that is missing (run the installer again); "
                         "or save the resume as .docx or .txt") from None
    try:
        with pdfplumber.open(io.BytesIO(raw)) as d:
            return "\n".join((p.extract_text() or "") for p in d.pages[:MAX_PDF_PAGES])
    except Exception:
        raise ValueError("this PDF could not be read") from None


def extract_text(name, raw):
    ext = str(name or "").lower().rsplit(".", 1)[-1] if "." in str(name or "") else ""
    if ext in ("txt", "md"):
        text = raw.decode("utf-8", "replace")
    elif ext == "docx":
        text = _docx_text(raw)
    elif ext == "pdf":
        text = _pdf_text(raw)
    else:
        raise ValueError("use a .docx, .pdf, .txt or .md file")
    text = "".join(c for c in text if c == "\n" or c == "\t" or c.isprintable())[:MAX_CHARS]
    if not text.strip():
        raise ValueError("no text found in the file")
    return text


def draft_ai(text, mode, runner):
    """Jobs and skills the AI quoted from the text, all unconfirmed. Any failure is a status, never an exception."""
    from . import ai, profile
    r = ai.run_task("resume_parse", {"resume": text}, mode, runner)
    if r.status != "ok" or r.source != "ai":
        return {"status": "queued" if r.status == "queued" else "unavailable", "reason": r.reason or "No AI available."}
    jobs = []
    for j in r.data["jobs"]:
        row = {"employer": j["employer"], "title": j["title"], "dates": j.get("dates", ""), "facts": j.get("facts", []), "ok": False}
        try:
            jobs.append(profile.validate({"jobs": [row]})["jobs"][0])
        except ValueError:
            continue    # a line the profile form would refuse (control characters, too long) is dropped, not repaired
    skills = ", ".join(r.data["skills"])
    return {"status": "ok", "jobs": jobs, "skills": {"value": skills[:600], "ok": False}}


def draft(text):
    """Contact facts found in the text, each unconfirmed. Missing facts are simply absent."""
    found = {}
    m = EMAIL.search(text)
    if m:
        found["email"] = m.group(0)
    m = LINKEDIN.search(text)
    if m:
        found["linkedin"] = m.group(0).rstrip("/")
    for m in URL.finditer(text):
        if "linkedin.com" not in m.group(0).lower():
            found["website"] = m.group(0).rstrip(".,;")
            break
    m = PHONE.search(EMAIL.sub(" ", text))
    if m and 10 <= len(re.sub(r"\D", "", m.group(1))) <= 15 and not all(YEAR.match(t) for t in m.group(1).split()):
        found["phone"] = m.group(1).strip()
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    words = first.split()
    if 2 <= len(words) <= 4 and all(NAME_WORD.match(w) for w in words):
        found["firstName"], found["lastName"] = words[0], words[-1]
    return {k: {"value": v[:300], "ok": False} for k, v in found.items()}


def store(data_dir, name, raw):
    """Keep the uploaded file in the private data folder (next to the profile) so the profile can count it."""
    from pathlib import Path
    from .profile import RESUME_DIR
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(str(name)).name)[-80:].lstrip(".") or "resume"
    d = Path(data_dir) / RESUME_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / stem).write_bytes(raw)
    return stem


DEFAULT_PAGES = 2
MAX_AUTO_PAGES = 3


def page_count(path):
    """Pages in a stored .docx (Word's saved page count) or .pdf; None when it cannot be told."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".docx":
            with zipfile.ZipFile(path) as z:
                m = re.search(r"<Pages>(\d+)</Pages>", z.read("docProps/app.xml").decode("utf-8", "replace"))
            return int(m.group(1)) if m else None
        if path.suffix.lower() == ".pdf":
            import pdfplumber
            with pdfplumber.open(path) as d:
                return len(d.pages)
    except Exception:
        return None
    return None


def page_target(data_dir, rules=None):
    """Pages a built resume must have: the Settings value if set, else the newest uploaded resume's page count
    (1 to 3), else 2."""
    p = (rules or {}).get("pages")
    if p:
        return p
    d = Path(data_dir) / "resumes"
    files = sorted((f for f in d.glob("*") if f.suffix.lower() in (".docx", ".pdf")),
                   key=lambda f: f.stat().st_mtime, reverse=True) if d.is_dir() else []
    for f in files:
        n = page_count(f)
        if n:
            return max(1, min(n, MAX_AUTO_PAGES))
    return DEFAULT_PAGES
