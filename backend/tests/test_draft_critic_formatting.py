"""Draft Critic: font-size and margin checking (2026-09-03).

Sponsors return proposals WITHOUT REVIEW for violating these rules -- NSF
PAPPG 24-1 states them explicitly ("Arial ... at a font size of 10 points or
larger", "Margins, in all directions, must be at least an inch"). The
extractor now captures them; these tests pin the enforcement side.

Design constraint under test: the check WARNS and never FAILS. A legitimate
figure, table or caption may use smaller type, and a rule like "Arial at 10pt
or larger; Times New Roman at 11pt or larger" is font-conditional in a way
the measurement cannot fully resolve. A false failure would train the user to
ignore the panel -- the exact problem this whole pass set out to remove.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_draft_critic_formatting.py -v
"""
import pytest

from services import draft_critic as dc


# --- parsing the stated rule out of the solicitation -----------------------

_NSF_FONT = ("Arial (not Arial Narrow), Courier New, or Palatino Linotype at a "
             "font size of 10 points or larger; Times New Roman at a font size "
             "of 11 points or larger; or Computer Modern family of fonts at a "
             "font size of 11 points or larger.")


def test_required_font_takes_the_smallest_permitted_size():
    """10pt is permitted for some fonts, so only text below 10pt is
    unambiguously wrong. Taking the largest stated size (11) would
    false-flag a compliant Arial-10 draft."""
    assert dc._required_font_pt({"font": _NSF_FONT}) == 10.0


def test_required_font_ignores_stray_numbers():
    """Section numbers and years in the same sentence must not be mistaken
    for type sizes."""
    assert dc._required_font_pt(
        {"font": "See Chapter 2024 section 15; use 11 points or larger."}) == 11.0


@pytest.mark.parametrize("raw,expected", [
    ("Margins, in all directions, must be at least an inch.", 1.0),
    ("Margins must be at least one inch.", 1.0),
    ("Use 0.5 inch margins.", 0.5),
    ('All margins must be 1" or greater.', 1.0),
    ("Use half-inch margins on all sides.", 0.5),
])
def test_required_margin_parsing(raw, expected):
    assert dc._required_margin_in({"margins": raw}) == expected


@pytest.mark.parametrize("formatting", [
    None, {}, {"font": None, "margins": None},
    {"font": "  "}, {"margins": "as appropriate"},
])
def test_no_stated_rule_yields_nothing(formatting):
    assert dc._required_font_pt(formatting) is None
    assert dc._required_margin_in(formatting) is None


# --- the check itself -------------------------------------------------------

_NSF_FMT = {"font": _NSF_FONT,
            "margins": "Margins, in all directions, must be at least an inch."}


def test_check_is_absent_when_the_solicitation_states_no_rule():
    """No rule -> no row at all, rather than a permanently 'skipped' one
    cluttering the panel for every proposal."""
    assert dc.check_formatting({"font_pt": 9.0, "margin_in": 0.5}, None) is None
    assert dc.check_formatting({"font_pt": 9.0, "margin_in": 0.5}, {}) is None


def test_compliant_draft_passes():
    out = dc.check_formatting({"font_pt": 11.0, "margin_in": 1.0}, _NSF_FMT)
    assert out["status"] == "ok"


def test_undersized_font_warns_and_never_fails():
    out = dc.check_formatting({"font_pt": 9.0, "margin_in": 1.0}, _NSF_FMT)
    assert out["status"] == "warn"
    assert out["status"] != "fail"
    assert "9pt" in out["value"]


def test_narrow_margins_warn():
    out = dc.check_formatting({"font_pt": 11.0, "margin_in": 0.5}, _NSF_FMT)
    assert out["status"] == "warn"
    assert "0.5" in out["detail"]


def test_both_violations_are_reported_together():
    out = dc.check_formatting({"font_pt": 8.0, "margin_in": 0.4}, _NSF_FMT)
    assert out["status"] == "warn"
    assert "8pt" in out["detail"] and "0.4" in out["detail"]


def test_font_tolerance_absorbs_pdf_rounding():
    """PDF writers routinely emit 9.96pt for nominal 10pt type; flagging that
    would be a false alarm on a perfectly compliant document."""
    assert dc.check_formatting({"font_pt": 9.96, "margin_in": 1.0},
                               _NSF_FMT)["status"] == "ok"


def test_margin_tolerance_absorbs_bbox_rounding():
    assert dc.check_formatting({"font_pt": 11.0, "margin_in": 0.97},
                               _NSF_FMT)["status"] == "ok"


def test_unmeasurable_pdf_is_skipped_not_guessed():
    """A scanned / image-only draft has no glyph geometry. Say so; don't
    invent a verdict."""
    out = dc.check_formatting({"font_pt": None, "margin_in": None}, _NSF_FMT)
    assert out["status"] == "skipped"
    assert "couldn't be measured" in out["detail"]


def test_only_one_dimension_measurable_still_checks_it():
    out = dc.check_formatting({"font_pt": 8.0, "margin_in": None}, _NSF_FMT)
    assert out["status"] == "warn"
    assert "8pt" in out["detail"]


def test_only_font_rule_stated_ignores_margins():
    """A solicitation that states type size but not margins must not have a
    margin opinion invented for it."""
    out = dc.check_formatting({"font_pt": 11.0, "margin_in": 0.25},
                              {"font": _NSF_FONT})
    assert out["status"] == "ok"


# --- measurement ------------------------------------------------------------

class _FakePage:
    def __init__(self, chars, width=612.0, height=792.0):
        self.chars, self.width, self.height = chars, width, height


class _FakePDF:
    def __init__(self, pages): self.pages = pages
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakePlumber:
    def __init__(self, pdf): self._pdf = pdf
    def open(self, _stream): return self._pdf


def _char(x0, x1, size=11.0, top=100.0):
    return {"x0": x0, "x1": x1, "size": size, "top": top,
            "bottom": top + size}


def test_margins_ignore_running_headers_and_footers(monkeypatch):
    """Regression guard for a measured false alarm.

    On a 612pt-wide page, body text from x=72 to x=540 is exactly 1-inch
    side margins. A footer sitting 6pt from the bottom edge is normal and
    permitted (page numbers live there). Measuring all four edges reported
    0.08" and warned on a compliant document; on the real NSF 24-1 it
    reported 0.49" against its own 1" rule. Side margins only."""
    body = [_char(72.0, 540.0, top=100.0) for _ in range(500)]
    footer = [_char(72.0, 540.0, size=8.0, top=778.0)]      # 6pt from bottom
    monkeypatch.setattr(dc, "_get_pdfplumber",
                        lambda: _FakePlumber(_FakePDF([_FakePage(body + footer)])))
    assert dc._measure_layout(b"pdf")["margin_in"] == 1.0


def test_dominant_font_size_wins_over_small_captions(monkeypatch):
    """Body text is the MOST COMMON size, not the smallest -- figure
    captions and subscripts must not drag the measurement down."""
    chars = [_char(72.0, 540.0, size=11.0) for _ in range(400)]
    chars += [_char(72.0, 200.0, size=7.0) for _ in range(40)]   # captions
    monkeypatch.setattr(dc, "_get_pdfplumber",
                        lambda: _FakePlumber(_FakePDF([_FakePage(chars)])))
    assert dc._measure_layout(b"pdf")["font_pt"] == 11.0


def test_one_tight_page_does_not_condemn_the_document(monkeypatch):
    """margin_in is the MEDIAN across pages, so a single full-bleed figure
    page can't fail an otherwise compliant draft."""
    ok = _FakePage([_char(72.0, 540.0) for _ in range(200)])
    tight = _FakePage([_char(10.0, 602.0) for _ in range(200)])
    monkeypatch.setattr(dc, "_get_pdfplumber",
                        lambda: _FakePlumber(_FakePDF([ok, ok, tight, ok, ok])))
    assert dc._measure_layout(b"pdf")["margin_in"] == 1.0


def test_measure_layout_on_garbage_bytes_is_graceful():
    """An unparseable upload must return the 'couldn't measure' shape, not
    raise -- critique_pdf calls this before any other guard."""
    assert dc._measure_layout(b"not a pdf at all") == {"font_pt": None,
                                                       "margin_in": None}
    assert dc._measure_layout(b"") == {"font_pt": None, "margin_in": None}


# --- wiring into critique_pdf ----------------------------------------------

def test_critique_adds_no_formatting_row_without_a_rule(monkeypatch):
    monkeypatch.setattr(dc, "_extract_pdf",
                        lambda b: ("Project Description text", 1, ["Project Description text"]))
    out = dc.critique_pdf(b"x", "NSF", {"page_limits": {}}, include_ai=False)
    assert not [c for c in out["checks"] if c["name"].startswith("Formatting")]


def test_critique_adds_a_formatting_row_when_a_rule_exists(monkeypatch):
    monkeypatch.setattr(dc, "_extract_pdf",
                        lambda b: ("Project Description text", 1, ["Project Description text"]))
    monkeypatch.setattr(dc, "_measure_layout",
                        lambda b: {"font_pt": 8.0, "margin_in": 0.4})
    out = dc.critique_pdf(b"x", "NSF", {"formatting": _NSF_FMT}, include_ai=False)
    rows = [c for c in out["checks"] if c["name"].startswith("Formatting")]
    assert len(rows) == 1
    assert rows[0]["status"] == "warn"


def test_formatting_never_contributes_a_failure(monkeypatch):
    """The verdict banner must not be driven to 'fail' by a formatting
    measurement, however bad the numbers look."""
    monkeypatch.setattr(dc, "_extract_pdf",
                        lambda b: ("Project Description text", 1, ["Project Description text"]))
    monkeypatch.setattr(dc, "_measure_layout",
                        lambda b: {"font_pt": 4.0, "margin_in": 0.01})
    out = dc.critique_pdf(b"x", "NSF", {"formatting": _NSF_FMT}, include_ai=False)
    rows = [c for c in out["checks"] if c["name"].startswith("Formatting")]
    assert rows[0]["status"] == "warn"
