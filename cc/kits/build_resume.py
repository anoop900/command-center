#!/usr/bin/env python3
"""Build a PDF resume (two pages unless told otherwise) from a JSON content file.
Usage: python build_resume.py content.json OUT_DIR
content.json must carry name, contact and education (personal data lives only in that private file).
Needs the optional python-docx and pdfplumber, and LibreOffice (soffice) for the PDF.
"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

MIN_FILL, MAX_FILL = 0.75, 0.93   # the last page must be at least this full, and keep at least this much free in Word
REQUIRED = ("name", "contact", "headline", "profile", "skills", "jobs", "education", "file_stem")
LADDER = [
    (10.0, 1.00, 1.0, 6, 4, 0.55),
    (10.0, 1.04, 2.0, 8, 6, 0.6),
    (10.5, 1.00, 2.0, 8, 6, 0.65),
    (10.5, 1.04, 2.5, 9, 7, 0.7),
    (10.5, 1.08, 3.0, 10, 8, 0.7),
    (10.5, 1.12, 3.5, 11, 9, 0.7),
    (11.0, 1.08, 3.0, 10, 8, 0.7),
    (11.0, 1.12, 3.5, 12, 10, 0.75),
]


def build(c, cfg, path):
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    NAVY = RGBColor(0x1F, 0x3A, 0x5F)
    body, ls, b_after, h_before, j_before, margin = cfg
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(margin)
    sec.top_margin = sec.bottom_margin = Inches(0.6)
    st = doc.styles["Normal"]
    st.font.name = "Calibri"; st.font.size = Pt(body)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    pf = st.paragraph_format
    pf.space_after = Pt(0); pf.space_before = Pt(0); pf.line_spacing = ls
    usable = 8.5 - 2 * margin

    def para(text="", bold=False, size=None, align=None, color=None, italic=False, after=0, before=0):
        p = doc.add_paragraph()
        if text:
            r = p.add_run(text); r.bold = bold; r.italic = italic
            if size: r.font.size = Pt(size)
            if color: r.font.color.rgb = color
        if align is not None: p.alignment = align
        p.paragraph_format.space_after = Pt(after); p.paragraph_format.space_before = Pt(before)
        return p

    def heading(t):
        p = para(t, bold=True, size=body + 1, color=NAVY, before=h_before, after=3)
        pPr = p._p.get_or_add_pPr(); bd = OxmlElement("w:pBdr"); bt = OxmlElement("w:bottom")
        for k, v in (("val", "single"), ("sz", "6"), ("space", "1"), ("color", "1F3A5F")):
            bt.set(qn("w:" + k), v)
        bd.append(bt); pPr.append(bd)

    def bullet(text, label=None):
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.left_indent = Inches(0.25); p.paragraph_format.first_line_indent = Inches(-0.15)
        p.paragraph_format.space_after = Pt(b_after)
        if label:
            r = p.add_run(label + ": "); r.bold = True
        p.add_run(text)

    para(c["name"].upper(), bold=True, size=18, align=WD_ALIGN_PARAGRAPH.CENTER, color=NAVY)
    para(c["contact"], size=9.5, align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
    para(c["headline"], bold=True, size=body + 1, align=WD_ALIGN_PARAGRAPH.CENTER, color=NAVY)
    if c.get("subhead"):
        para(c["subhead"], size=9.5, align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
    heading("PROFILE"); para(c["profile"])
    heading(c.get("skills_heading", "CORE SKILLS"))
    for lab, txt in c["skills"]: bullet(txt, lab)
    if c.get("project"):
        heading("SELECTED AI PROJECT"); bullet(c["project"][1], c["project"][0])
    heading("PROFESSIONAL EXPERIENCE")
    for j in c["jobs"]:
        p = para(before=j_before)
        p.paragraph_format.tab_stops.add_tab_stop(Inches(usable), WD_TAB_ALIGNMENT.RIGHT)
        r = p.add_run(j["company"]); r.bold = True; r.font.color.rgb = NAVY
        p.add_run("\t" + j["dates"]).bold = True
        para(j["title"], italic=True, bold=True, after=1)
        if j.get("intro"): para(j["intro"], after=1)
        for b in j["bullets"]: bullet(b)
    if c.get("earlier"):
        heading("EARLIER CAREER")
        for lab, txt in c["earlier"]: bullet(txt, lab)
    heading("EDUCATION & CERTIFICATIONS")
    for line in c["education"]: bullet(line)
    doc.save(path)


def to_pdf(docx, outdir):
    exe = soffice_path()
    with tempfile.TemporaryDirectory() as prof:
        try:
            subprocess.run([exe, "-env:UserInstallation=" + Path(prof).as_uri(), "--headless", "--convert-to", "pdf",
                            "--outdir", outdir, docx],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            raise RuntimeError("LibreOffice could not make the PDF. Close any open LibreOffice window and try Build kit again.") from None
    return docx[:-5] + ".pdf"


def soffice_path():
    p = shutil.which("soffice") or shutil.which("libreoffice")
    if not p:
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"), "/Applications/LibreOffice.app/Contents/MacOS"):
            for rel in ("LibreOffice/program/soffice.exe", "soffice"):
                cand = os.path.join(root or "", rel)
                if root and os.path.isfile(cand):
                    return cand
        raise RuntimeError("LibreOffice is not installed, and it makes the PDF resume. Install it from https://www.libreoffice.org/download/ "
                           "(or run the installer again), then try Build kit again.")
    return p


def measure(pdf):
    import pdfplumber
    with pdfplumber.open(pdf) as d:
        n = len(d.pages)
        last = d.pages[-1]
        words = last.extract_words()
        bottom = max((w["bottom"] for w in words), default=0)
        fill = (bottom - 43) / (last.height - 86)
    return n, round(max(0.0, min(fill, 1.0)), 2)


def build_pdf(c, outdir, pages=2):
    """Fit the content onto exactly `pages` pages. Returns {"ok", "pdf", "pages", "page2_fill", "note"}; raises
    RuntimeError for a missing tool or an unusable content file."""
    missing = [k for k in REQUIRED if not c.get(k)]
    if missing:
        raise RuntimeError(f"content is missing {missing}")
    try:
        import docx, pdfplumber  # noqa: F401
    except ImportError:
        raise RuntimeError("python-docx and pdfplumber are not installed (run the installer again)") from None
    stem = os.path.basename(str(c["file_stem"]))
    outdir = os.path.abspath(outdir); os.makedirs(outdir, exist_ok=True)
    docx_path = os.path.join(outdir, stem + ".docx")
    best = None
    fits = []
    try:
        for cfg in LADDER:
            build(c, cfg, docx_path); pdf = to_pdf(docx_path, outdir); n, fill = measure(pdf)
            if n == pages:
                fits.append((cfg, fill))
            elif n > pages:
                break
        # Word lays text out a little differently from LibreOffice, so a last page that is nearly full spills onto a
        # new page there. Prefer a setting that leaves room; if none does, take the largest that fits, as before.
        roomy = [f for f in fits if MIN_FILL <= f[1] <= MAX_FILL] if pages > 1 else []
        best = (roomy or fits or [None])[-1]
        if best is None and n > pages:
            return {"ok": False, "pages": n, "reason": f"content does not fit {pages} page{'s' if pages > 1 else ''} at the most compact setting"}
        if best is None:    # even the largest setting leaves it short: keep that build and say so
            return {"ok": False, "pdf": pdf, "pages": n, "page2_fill": fill, "note": f"content fills only {n} page{'s' if n > 1 else ''}, not {pages}"}
        build(c, best[0], docx_path); pdf = to_pdf(docx_path, outdir); n, fill = measure(pdf)
    except (subprocess.SubprocessError, OSError) as e:
        raise RuntimeError(f"PDF build failed: {type(e).__name__}") from None
    full = pages == 1 or fill >= MIN_FILL
    return {"ok": n == pages and full, "pdf": pdf, "pages": n, "page2_fill": fill,
            "note": "" if full else "last page under 75% full"}


def main():
    try:
        r = build_pdf(json.load(open(sys.argv[1], encoding="utf-8")), sys.argv[2])
    except (RuntimeError, OSError, ValueError) as e:
        sys.exit(f"build_resume: {e}")
    print(json.dumps(r))
    sys.exit(0 if r.get("pdf") else 1)


if __name__ == "__main__":
    main()
