"""
Resume file parsing — PDF, DOCX, TXT.

THE FAILURE THAT MATTERS
------------------------
A scanned or image-only PDF has no text layer. pdfplumber returns "" and every
downstream step happily computes a fit score of near-zero against an empty
skill set. The user sees a confident report built on nothing.

So the parser distinguishes "no text found" from "parsed successfully with
little text" and refuses the former loudly. Silent degradation is the enemy
here — a wrong report is worse than a rejected upload.

Dependencies are imported lazily so the app boots without them; a missing
library produces an actionable message instead of an ImportError at startup.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

MIN_CHARS = 220          # below this a "successful" parse is almost certainly wrong
MIN_WORDS = 40


@dataclass
class ParsedResume:
    text: str
    source_format: str
    pages: int = 0
    ok: bool = True
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def _clean(text: str) -> str:
    """Normalise whitespace without destroying line structure.

    Line breaks matter: bullet detection in deterministic.py keys off lines
    starting with a bullet marker, so collapsing everything to one line would
    quietly downgrade every skill to 'mentioned but not evidenced'.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2022", "- ").replace("\uf0b7", "- ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def parse_pdf(data: bytes) -> ParsedResume:
    try:
        import pdfplumber
    except ImportError:
        return ParsedResume("", "pdf", ok=False,
                            error="PDF support needs pdfplumber. Run: pip install pdfplumber")

    try:
        chunks, pages = [], 0
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = len(pdf.pages)
            for page in pdf.pages:
                chunks.append(page.extract_text() or "")
    except Exception as exc:  # noqa: BLE001
        return ParsedResume("", "pdf", ok=False,
                            error=f"Could not open the PDF ({type(exc).__name__}). "
                                  "If it's password-protected, remove the password first.")

    text = _clean("\n".join(chunks))

    # The scanned-resume case. A PDF with pages but no extractable text is
    # image-only; there is nothing to analyse and we must say so.
    if len(text) < MIN_CHARS:
        return ParsedResume(
            text, "pdf", pages, ok=False,
            error=("This PDF has no readable text layer — it's almost certainly a "
                   "scan or an exported image. Re-export it from Word or Google Docs "
                   "as a real PDF, or paste the text instead."))

    warnings = []
    if len(text.split()) < MIN_WORDS * 2:
        warnings.append("Very little text extracted — check the parsed output below.")
    if pages > 4:
        warnings.append(f"{pages} pages is unusual for a resume; "
                        "make sure you uploaded the right file.")
    return ParsedResume(text, "pdf", pages, warnings=warnings)


def parse_docx(data: bytes) -> ParsedResume:
    try:
        import docx
    except ImportError:
        return ParsedResume("", "docx", ok=False,
                            error="DOCX support needs python-docx. Run: pip install python-docx")

    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return ParsedResume("", "docx", ok=False,
                            error=f"Could not open the DOCX ({type(exc).__name__}). "
                                  "If it's a .doc, save it as .docx first.")

    lines = []
    for para in doc.paragraphs:
        t = para.text.strip()
        if not t:
            continue
        # Word list styles carry the bullet in the style name, not the text.
        style = (para.style.name or "").lower()
        lines.append(f"- {t}" if "list" in style and not t.startswith("-") else t)

    # Many resumes are laid out in tables; skipping them loses the whole body.
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(cells))

    text = _clean("\n".join(lines))
    if len(text) < MIN_CHARS:
        return ParsedResume(text, "docx", ok=False,
                            error="Almost no text found in this DOCX. If the content "
                                  "is inside text boxes or images, paste it instead.")
    return ParsedResume(text, "docx")


def parse_text(data: bytes) -> ParsedResume:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = _clean(data.decode(enc))
            break
        except UnicodeDecodeError:
            continue
    else:
        return ParsedResume("", "txt", ok=False, error="Could not decode this file as text.")

    if len(text) < MIN_CHARS:
        return ParsedResume(text, "txt", ok=False,
                            error=f"Only {len(text)} characters found — that's too short "
                                  "to analyse. Paste your full resume.")
    return ParsedResume(text, "txt")


def parse_resume(filename: str, data: bytes) -> ParsedResume:
    """Dispatch on extension, falling back to content sniffing.

    Extensions lie — people rename files. The magic bytes don't.
    """
    name = (filename or "").lower()
    if data[:4] == b"%PDF":
        return parse_pdf(data)
    if data[:2] == b"PK" and (name.endswith(".docx") or b"word/" in data[:4000]):
        return parse_docx(data)
    if name.endswith(".pdf"):
        return parse_pdf(data)
    if name.endswith(".docx"):
        return parse_docx(data)
    if name.endswith(".doc"):
        return ParsedResume("", "doc", ok=False,
                            error="Legacy .doc isn't supported. Open it in Word and "
                                  "save as .docx, or paste the text.")
    return parse_text(data)
