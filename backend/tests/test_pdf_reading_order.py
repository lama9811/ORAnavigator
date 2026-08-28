"""A line split by a styled run must not scramble the sentence.

THE VERTICAL HALF of the welded-words problem `PDF_X_TOLERANCE_RATIO` fixes.
pdfplumber groups characters into lines by baseline and its default
`y_tolerance` of 3 starts a NEW line whenever part of one sits slightly off --
a superscript-styled numeral, or an italic phrase set in another face.

MEASURED 2026-08-28 on the awarded NSF EiR Project Description, which contains
both shapes:

    "...two undergraduates in Year ; three \\n 1 \\n undergraduates in Year ;"
    "a part of my \\n Super Representation \\n with two primary goals: ... \\n
     Theory research program \\n representation theory of ..."

The digits and the italic phrase are lifted OUT of their sentences onto lines of
their own. The reviewer reassembles the sentence correctly and quotes it,
`quote_in` compares it against our scrambled copy, and golden rule 2 demotes a
real `addressed` to `not_found` -- a funded proposal told it never described its
undergraduate research opportunities, ten runs out of ten.

WHY 5 AND NOT 8: both fix the two cases, but 8 also merges lines that are
genuinely separate (+1,117 chars of run-together text on the same document).
5 changes that document's length by 0.1%.

WHY THIS IS SAFE, measured against CLAUDE.md's own yardstick for the x-ratio:
on the whole awarded package the ordinary-vocabulary counts are UNCHANGED
(representation 40, mathematics 72, undergraduate 48) and no welded token
appears; on NSF 23-598 the extracted text is BYTE-IDENTICAL, so the solicitation
path -- which shares the constant -- reads exactly what it read before. The one
count that moves is on page 1, the NSF cover sheet, a multi-column form whose
rules are `rb_not_in_text` and never checked against text.
"""
import io
import pathlib

import pytest

from services import document_text as dt

_RAISE = 4.0

pdfplumber = pytest.importorskip("pdfplumber")
reportlab = pytest.importorskip("reportlab")

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def _pdf_with_a_raised_numeral() -> bytes:
    """One line of prose whose numerals sit 2pt above the baseline.

    That is the shape the awarded proposal has, reduced to the smallest thing
    that reproduces it: a run of characters on the same visual line but at a
    different y, which pdfplumber's default tolerance splits off.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 10)
    # Laid out with MEASURED widths so the runs sit side by side exactly as they
    # would in a real document -- placing them at guessed x positions makes the
    # characters interleave, which is a different artifact and not this one.
    # 4pt: ABOVE pdfplumber's default y_tolerance of 3, so the default splits
    # these numerals onto their own line (the bug), and BELOW ours of 5, so the
    # fix groups them back. A 2pt raise reproduces nothing -- the default
    # already tolerates it -- and a test built on one passes either way.
    x, y = 72.0, 700.0
    for chunk, raised in [("The research supports two undergraduates in Year ", False),
                          ("1", True),      # raised _RAISE pt -- beyond the DEFAULT tolerance
                          ("; three undergraduates in Year ", False),
                          ("2", True),
                          (".", False)]:
        c.drawString(x, y + (_RAISE if raised else 0), chunk)
        x += c.stringWidth(chunk, "Helvetica", 10)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_a_raised_numeral_stays_inside_its_sentence():
    read = dt.extract_upload("x.pdf", _pdf_with_a_raised_numeral())
    flat = " ".join((read["text"] or "").split())
    assert "in Year 1; three undergraduates in Year 2" in flat, (
        f"the numerals were lifted out of the sentence: {flat!r}")


def test_the_two_pdf_paths_share_one_tolerance():
    """The upload path and the solicitation path must not read one PDF
    differently -- the same contract the x-ratio already carries."""
    from services import solicitation_extractor as se
    assert se._PDF_Y_TOLERANCE is dt.PDF_Y_TOLERANCE
    assert se._PDF_X_TOLERANCE_RATIO is dt.PDF_X_TOLERANCE_RATIO


def test_the_tolerance_is_not_raised_far_enough_to_merge_separate_lines():
    """8 fixed the same cases and merged genuinely separate lines. Guard the
    value, because the failure it causes is invisible: run-together prose that
    still reads like a successful extraction."""
    assert dt.PDF_Y_TOLERANCE <= 5
