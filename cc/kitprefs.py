"""Kit-edit preferences (DESIGN section 10, offline). At build time the resume text is saved beside the kit; later, words
the user took out by hand are counted across kits. A short phrase removed in 2 or more different kits, and still absent
from every current kit, becomes an avoid phrase that the tailor step gets as data (banned_phrases). Phrases only, never
full text; the user can forget any of them."""
import re
import zipfile
from collections import Counter

from cc import config, store
from cc.kits.content import find_resume
from cc.rules import job_key

BASELINE = "resume.baseline.txt"
MIN_KITS = 2
MAX_PHRASES = 30
MAX_BYTES = 2_000_000
STOP = set("a an and are as at be by for from in into is it of on or our the their this to with your you we i".split())


def docx_text(path):
    """Plain text of a .docx (paragraph per line), or '' when unreadable. Standard library only; size-limited."""
    if path is None:
        return ""
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_BYTES:
                return ""
            xml = z.read(info).decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", " ", xml)
    return re.sub(r"<[^>]+>", "", xml).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def _words(text):
    return re.findall(r"[a-z][a-z'+#.-]*", text.lower())


def grams(text):
    """Two- and three-word phrases with at least one content word of 4+ letters."""
    w, out = _words(text), set()
    for n in (2, 3):
        for i in range(len(w) - n + 1):
            g = w[i:i + n]
            if g[0] in STOP or g[-1] in STOP or not any(len(x) >= 4 and x not in STOP for x in g):
                continue
            out.add(" ".join(g))
    return out


def save_baseline(folder):
    """Called right after a kit is built: remember the generated text so later hand edits can be seen."""
    t = docx_text(find_resume(folder, "docx"))
    if t.strip():
        (folder / BASELINE).write_text(t, encoding="utf-8")


def removed(folder):
    """Phrases in the saved baseline that the current resume.docx no longer contains (empty when unedited or unreadable)."""
    try:
        base = (folder / BASELINE).read_text(encoding="utf-8")
    except OSError:
        return set(), set()
    cur = docx_text(find_resume(folder, "docx"))
    if not cur.strip():
        return set(), set()
    now = grams(cur)
    return grams(base) - now, now


def train(conn, home=None):
    """Rebuild learned/kitprefs from every kit folder. Idempotent; a missing or odd folder is skipped."""
    kits = config.kits_dir(home)
    seen, present = Counter(), set()
    try:
        folders = [b.parent for b in kits.rglob(BASELINE)]      # kits sit in date folders: <date>/<Company_Role>
    except OSError:
        folders = []
    for f in folders[:500]:
        gone, now = removed(f)
        seen.update(gone)
        present |= now
    phrases = [{"phrase": p, "kits": n} for p, n in seen.most_common() if n >= MIN_KITS and p not in present][:MAX_PHRASES]
    cur = store.get(conn, "learned", "kitprefs")
    store.set_doc(conn, "learned", "kitprefs", {"phrases": phrases}, if_version=cur["version"] if cur else None, by="learned")
    return phrases


def phrases(conn, forgotten=()):
    """Avoid phrases for the tailor step, minus any the user forgot."""
    d = store.get(conn, "learned", "kitprefs")
    gone = {str(g).lower() for g in forgotten}
    return [p["phrase"] for p in (d["data"].get("phrases", []) if d else []) if ("phrase:" + p["phrase"])[:40] not in gone]


def key(phrase):
    return ("phrase:" + phrase).lower()[:40]
