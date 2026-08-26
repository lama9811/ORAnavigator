"""Extract plain text from an uploaded proposal file.

Used by the EiR reviewer so a PI can upload their actual proposal files instead
of pasting. Deliberately small and dependency-free beyond what the app already
pins (pdfplumber, python-docx — both already in requirements.txt).

TWO RULES, both learned the hard way in this repo:

1. **NEVER TRUNCATE SILENTLY.** CLAUDE.md records `MAX_PDF_PAGES = 40` /
   `MAX_CHARS = 200_000` quietly returning 36% of a 118-page policy document as
   clean text with no error, so the document "looked complete" and a coverage
   check wrongly cleared. Here the consequence would be worse than a bad audit:
   a truncated read makes the LAST sections of a proposal look MISSING, and the
   PI would go rewrite a sustainability plan they already wrote. So the caps
   below are generous, and hitting one sets a `truncated` flag that the caller
   MUST surface. A truncated read is reported, never swallowed.

2. **AN UNREADABLE FILE IS NOT AN EMPTY FILE.** A failed parse returns an
   `error` string, not "". The reviewer reports it as a file it could not read,
   never as content the author omitted — same invariant as the KB scraper's
   `looks_unreadable()`.
"""

from __future__ import annotations

import io
import os
from typing import Optional

# Generous, and enforced VISIBLY. A 15-page NSF Project Description is ~10k
# words; a full package with letters and budget justification is well under
# these. Anything past them is almost certainly the wrong file.
MAX_PAGES = 400
MAX_CHARS = 1_500_000

# How pdfplumber decides where one word ends and the next begins.
#
# It measures the horizontal GAP between glyphs against a tolerance, and that
# tolerance defaults to a FIXED 3 points. TeX does not write a space character
# between words — it moves the pen — and in a 10pt document that move is often
# ~2pt, under the default. The words are then welded together.
#
# Measured on a real awarded NSF 23-598 package (Morgan State, 56 pages,
# LaTeX): 863 run-together tokens over 18 characters, including every heading
# the reviewer looks for — `IntellectualMerit`, `BroaderImpacts`,
# `ResultsfromPriorNSFsupport`. Nothing reported a broken read; three separate
# features just said false things about the PROPOSAL instead. The locate stage
# could not find those sections; `quote_in` rejected the reviewer's own quotes,
# so 21 of 34 fix-list rows were correct findings demoted to `not_found`; and
# the language checks returned 142 "mistakes" of which exactly one was real.
#
# A RATIO scales the tolerance with the font size instead of pinning it, which
# is the property that makes it safe across documents: 0.15 is 1.5pt at 10pt
# body text and proportionally more in a heading. Measured across both awarded
# packages: run-together tokens 863 -> 1 and 21 -> 1, all heading probes
# recovered, and the whole-word counts of ordinary vocabulary UNCHANGED
# (representation 85, mathematics 78, undergraduate 60, research 269) — so the
# spacing was not bought by chopping real words up, which is the mirror failure
# and would break `quote_in` just as badly while looking like a different bug.
#
# 0.20 already regressed (111 welded tokens); 0.25 regressed badly (613).
# Shared with services/solicitation_extractor.read_pdf so the upload path and
# the solicitation path cannot drift into reading the same PDF differently.
PDF_X_TOLERANCE_RATIO = 0.15

# Extensions we can actually read. Checked against the filename only as a first
# pass — content sniffing decides for anything ambiguous.
PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}
TEXT_EXTS = {".txt", ".md", ".markdown", ".text", ".rst", ".csv", ".tex"}
SUPPORTED_EXTS = PDF_EXTS | DOCX_EXTS | TEXT_EXTS

# Formats that need a dependency this app does not pin. Named explicitly so the
# PI gets "convert this to PDF" instead of a generic failure.
KNOWN_UNSUPPORTED = {
    ".doc": "legacy Word (.doc)",
    ".rtf": "rich text (.rtf)",
    ".pages": "Apple Pages",
    ".odt": "OpenDocument (.odt)",
    ".wpd": "WordPerfect",
}


def _extract_pdf(data: bytes) -> tuple[str, int, bool]:
    """(text, page_count, truncated). Raises on an unparseable PDF."""
    import pdfplumber

    pages: list[str] = []
    truncated = False
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            if i >= MAX_PAGES:
                truncated = True
                break
            pages.append(
                page.extract_text(x_tolerance_ratio=PDF_X_TOLERANCE_RATIO) or "")
    text = "\n".join(pages)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        truncated = True
    return text, total, truncated


def _extract_docx(data: bytes) -> tuple[str, int, bool]:
    """(text, page_count, truncated). Page count is 0 — a .docx has no fixed
    pagination until it is rendered, and inventing one would feed a bogus number
    into the 2-page letter check."""
    import docx

    doc = docx.Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    # Tables carry real content in budget justifications and timelines; skipping
    # them would drop exactly the numbers a reviewer is looking for.
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    text = "\n".join(parts)
    truncated = len(text) > MAX_CHARS
    return (text[:MAX_CHARS] if truncated else text), 0, truncated


def _extract_plain(data: bytes) -> tuple[str, int, bool]:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    truncated = len(text) > MAX_CHARS
    return (text[:MAX_CHARS] if truncated else text), 0, truncated


def _looks_like_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def _looks_like_zip(data: bytes) -> bool:
    # .docx is a zip. So is .pptx/.xlsx, which is why the caller still checks
    # the extension before trusting this.
    return data[:2] == b"PK"


def extract_upload(filename: str, data: bytes) -> dict:
    """Read one uploaded file.

    Returns {filename, text, pages, chars, truncated, error}. NEVER raises —
    an unreadable file comes back with `error` set and `text` empty, so one bad
    file in a multi-file upload cannot take down the whole review."""
    name = filename or "file"
    ext = os.path.splitext(name)[1].lower()
    out = {"filename": name, "text": "", "pages": 0, "chars": 0,
           "truncated": False, "error": None}

    if not data:
        out["error"] = "The file is empty."
        return out

    if ext in KNOWN_UNSUPPORTED:
        out["error"] = (f"{KNOWN_UNSUPPORTED[ext]} isn't supported. "
                        "Save it as PDF or .docx and try again.")
        return out

    try:
        # Sniff content first: a mislabelled extension is common when files come
        # off a shared drive, and the bytes are authoritative.
        if _looks_like_pdf(data):
            text, pages, truncated = _extract_pdf(data)
        elif ext in DOCX_EXTS and _looks_like_zip(data):
            text, pages, truncated = _extract_docx(data)
        elif ext in TEXT_EXTS or not ext:
            text, pages, truncated = _extract_plain(data)
        elif ext in PDF_EXTS:
            out["error"] = "This is named .pdf but isn't a PDF file."
            return out
        elif _looks_like_zip(data):
            out["error"] = ("This looks like an Office file that isn't .docx "
                            "(maybe .pptx or .xlsx). Save it as PDF and try again.")
            return out
        else:
            out["error"] = (f"Can't read {ext or 'this file type'}. "
                            "Upload a PDF, .docx, or plain text file.")
            return out
    except Exception as e:                      # noqa: BLE001 — never raise to the caller
        out["error"] = f"Couldn't read this file ({type(e).__name__})."
        return out

    text = text.strip()
    if not text:
        # A real failure mode: a scanned/photographed proposal is a PDF of
        # IMAGES with no text layer. Say so, because "0 words" alone reads as an
        # empty file and the PI will re-upload the same thing.
        out["error"] = ("No text found. If this is a scanned PDF, it has no text "
                        "layer — export it from the original document instead.")
        return out

    out.update(text=text, pages=pages, chars=len(text), truncated=truncated)
    return out


def combine(files: list[dict]) -> str:
    """Join several extracted files into one document for the reviewer.

    Each file's name is emitted as a heading line. That is not cosmetic: the EiR
    reviewer segments by heading, and a file called "Letter of Institutional
    Support.pdf" gives the locate stage a perfect marker for a section that is
    otherwise easy to miss. The stem is used verbatim so it reads as a heading
    rather than a path."""
    chunks = []
    for f in files:
        if f.get("error") or not f.get("text"):
            continue
        stem = os.path.splitext(f["filename"])[0].replace("_", " ").strip()
        chunks.append(f"{stem}\n\n{f['text']}")
    return "\n\n".join(chunks)
