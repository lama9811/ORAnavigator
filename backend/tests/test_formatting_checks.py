"""Formatting rules judged against the uploaded PDF's own geometry.

NSF returns proposals WITHOUT REVIEW for type size and margins, and until now
all nine `format_of_the_proposal` rules reported `not_checked` with the address
"the formatting of the PDF you upload — check it there, not in the text". That
was the honest answer while nothing measured the PDF: formatting genuinely is
not a property of pasted text, and reporting `not_found` against a compliant
draft is the presence-rendered-as-verdict mistake this repo has unshipped
several times.

Three of those nine ARE measurable once a PDF is in hand — font size, margins
and paper size — so they now mirror `rb_page_limit`: a real measurement gives a
real verdict, and no measurement keeps the old row, its quote and its address.

Run: cd backend && python3 -m pytest tests/test_formatting_checks.py -q
"""

import pytest

from services import document_text as dt
from services import rulebook_baseline as rb
from services import rulebook_checks as rc


# ── the measurement ─────────────────────────────────────────────────────────

class _FakePage:
    def __init__(self, chars, width=612.0, height=792.0):
        self.chars, self.width, self.height = chars, width, height


class _FakePDF:
    def __init__(self, pages): self.pages = pages


def _char(x0, x1, size=11.0):
    return {"x0": x0, "x1": x1, "size": size}


def _body(n=400, x0=72.0, x1=540.0, size=11.0):
    """A page of body text with exact 1-inch side margins on US Letter."""
    return [_char(x0, x1, size) for _ in range(n)]


def test_measures_body_font_and_side_margins():
    got = dt._measure_layout(_FakePDF([_FakePage(_body())]))
    assert got["font_pt"] == 11.0
    assert got["margin_in"] == 1.0
    assert got["page_w_in"] == 8.5
    assert got["page_h_in"] == 11.0


def test_dominant_font_wins_over_small_captions():
    """Body text is the MOST COMMON size, not the smallest. NSF explicitly
    permits smaller type in figures, tables and captions, so measuring the
    minimum would flag compliant drafts."""
    chars = _body(400, size=11.0) + _body(40, x1=200.0, size=7.0)
    assert dt._measure_layout(_FakePDF([_FakePage(chars)]))["font_pt"] == 11.0


def test_margins_ignore_headers_and_footers():
    """THE measured reason margins are side-only.

    A footer 6pt from the bottom edge is normal and permitted — page numbers
    live there. Measuring all four edges reported 0.49" on NSF's own PAPPG, a
    professionally typeset 1-inch-margin document, and would have flagged it.
    """
    footer = [{"x0": 72.0, "x1": 540.0, "size": 8.0, "top": 778.0, "bottom": 786.0}]
    got = dt._measure_layout(_FakePDF([_FakePage(_body() + footer)]))
    assert got["margin_in"] == 1.0


def test_one_full_bleed_page_does_not_condemn_the_document():
    """margin_in is the MEDIAN across pages, so a single figure page that runs
    to the edge cannot fail an otherwise compliant draft."""
    ok = _FakePage(_body())
    bleed = _FakePage([_char(10.0, 602.0) for _ in range(200)])
    got = dt._measure_layout(_FakePDF([ok, ok, bleed, ok, ok]))
    assert got["margin_in"] == 1.0


def test_pages_without_glyphs_yield_no_measurement():
    """An image-only scan must produce None, not a fabricated verdict."""
    got = dt._measure_layout(_FakePDF([_FakePage([])]))
    assert got["font_pt"] is None
    assert got["margin_in"] is None


# ── the check ───────────────────────────────────────────────────────────────

def _req(prop):
    return {"check": "rb_formatting",
            "check_args": {"property": prop,
                           "section": "format_of_the_proposal",
                           "handled_by": "the formatting of the PDF you upload"}}


def _ctx(**layout):
    return {"text": "", "spans": {}, "title": None, "budget": None,
            "profile": {}, "pages": {}, "layout": layout}


@pytest.mark.parametrize("pt,expected", [
    (11.0, "clear"),
    (10.0, "clear"),      # 10pt Arial is compliant and must not be flagged
    (9.96, "clear"),      # writers emit 9.96 for nominal 10pt
    (9.0, "flagged"),
    (7.5, "flagged"),
])
def test_font_verdicts(pt, expected):
    status, detail, _ = rc.rb_formatting(_ctx(font_pt=pt), _req("font"))
    assert status == expected
    if expected == "flagged":
        assert "without review" in detail


@pytest.mark.parametrize("inches,expected", [
    (1.0, "clear"),
    (0.96, "clear"),      # a true 1" margin measures ~0.96 (glyph side bearing)
    (0.92, "clear"),
    (0.5, "flagged"),
    (0.25, "flagged"),
])
def test_margin_verdicts(inches, expected):
    status, detail, _ = rc.rb_formatting(_ctx(margin_in=inches), _req("margins"))
    assert status == expected
    if expected == "flagged":
        assert "one inch" in detail


@pytest.mark.parametrize("w,h,expected", [
    (8.5, 11.0, "clear"),
    (11.0, 8.5, "clear"),          # landscape letter is still letter
    (8.27, 11.69, "flagged"),      # A4
    (11.0, 17.0, "flagged"),       # tabloid
])
def test_paper_size_verdicts(w, h, expected):
    status, _, _ = rc.rb_formatting(_ctx(page_w_in=w, page_h_in=h), _req("paper"))
    assert status == expected


def test_no_measurement_keeps_the_old_not_checked_row():
    """A pasted draft carries no geometry. The rule must report exactly what it
    reported before this check existed -- never a pass, never a failure."""
    for prop in ("font", "margins", "paper"):
        status, detail, _ = rc.rb_formatting(_ctx(), _req(prop))
        assert status == "not_checked"
        assert "not something the text of a draft can show" in detail
        assert "the formatting of the PDF you upload" in detail


def test_unknown_property_does_not_guess():
    """A fabricated verdict on a rule we cannot evaluate is worse than silence."""
    status, _, _ = rc.rb_formatting(_ctx(font_pt=9.0), _req("nonsense"))
    assert status == "not_checked"


# ── wiring ──────────────────────────────────────────────────────────────────

def test_exactly_the_measurable_rules_were_repointed():
    rows = [r for r in rb.RULES["the PAPPG"] if r.get("check") == "rb_formatting"]
    assert {r["id"] for r in rows} == {
        "pappg_format_use_approved_fonts_and_sizes",
        "pappg_format_maintain_one_inch_margins",
        "pappg_format_use_standard_letter_paper_size",
    }
    # Every one still names what to measure, and keeps its fallback address.
    for r in rows:
        assert r["check_args"]["property"] in {"font", "margins", "paper"}
        assert r["check_args"].get("handled_by")


def test_rules_that_a_pdf_cannot_settle_were_left_alone():
    """Line spacing, margin content, single-column and page numbering are NOT
    measured here. Re-pointing them without a measurement behind them would
    manufacture verdicts."""
    untouched = [r for r in rb.RULES["the PAPPG"]
                 if r.get("section") == "format_of_the_proposal"
                 and r.get("check") == "rb_not_in_text"]
    assert len(untouched) == 6


def test_check_is_registered():
    assert rc.CHECKS["rb_formatting"] is rc.rb_formatting


def test_format_section_stays_out_of_the_section_picker():
    """The picker deliberately omits Format of the Proposal. Re-pointing three
    of its rules must not smuggle the section back in -- it would still hand a
    PI mostly rows they cannot act on from a paste."""
    offered = {s["key"] for s in rb.sections_offered("the PAPPG")}
    assert "format_of_the_proposal" not in offered
