"""One-page cover letter file (Word + PDF) from an already validated letter. Facts come from content.json (name, contact)."""
import os
import re
from datetime import date

from . import build_resume


def stem(name):
    """'Firstname_Lastname_Cover_Letter'; no name gives 'Cover_Letter'."""
    parts = [re.sub(r"[^\w.-]+", "", p).strip(".") for p in str(name or "").split()]
    return "_".join([p for p in parts if p] + ["Cover_Letter"])


def find(folder, ext="pdf"):
    for p in sorted(folder.glob("*." + ext)):
        if p.stem.endswith("Cover_Letter"):
            return p
    return None


def build(letter, name, contact, outdir, today=None):
    """Write <stem>.docx and <stem>.pdf into outdir. Returns the PDF path. Raises RuntimeError (plain message) when the
    tools are missing or the letter does not fit one page."""
    try:
        import docx
        from docx.shared import Pt, Inches
    except ImportError:
        raise RuntimeError("python-docx is not installed (run the installer again)") from None
    base = os.path.join(os.path.abspath(outdir), os.path.basename(stem(name)))
    os.makedirs(outdir, exist_ok=True)
    for size in (11.0, 10.5, 10.0):
        d = docx.Document()
        sec = d.sections[0]
        sec.left_margin = sec.right_margin = Inches(1)
        sec.top_margin = sec.bottom_margin = Inches(0.9)
        st = d.styles["Normal"]
        st.font.name, st.font.size = "Calibri", Pt(size)
        st.paragraph_format.space_after = Pt(8)

        def para(text, bold=False, after=None):
            p = d.add_paragraph()
            r = p.add_run(text)
            r.bold = bold
            if after is not None:
                p.paragraph_format.space_after = Pt(after)
            return p
        para(name, bold=True, after=0)
        if contact:
            para(contact, after=14)
        para((today or date.today()).strftime("%d %B %Y").lstrip("0"), after=14)
        para(letter["greeting"])
        for t in letter["paragraphs"]:
            para(t)
        para(letter["closing"], after=26)
        para(name)
        d.save(base + ".docx")
        pdf = build_resume.to_pdf(base + ".docx", os.path.dirname(base))
        pages, _ = build_resume.measure(pdf)
        if pages == 1:
            return pdf
    raise RuntimeError("The letter did not fit on one page. Press Cover letter again for a shorter one.")
