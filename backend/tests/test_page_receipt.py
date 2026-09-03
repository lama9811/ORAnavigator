"""The receipt is the proof a page was read. These tests are its guarantee.

`quote_in` is the wrong instrument here and the second test says why: on a
SINGLE page its furniture path degenerates and accepts a quote stitched across
deleted lines. That test is mutation-tested — swap `receipt_ok` for `quote_in`
and it must go red, or it guards nothing.
"""
import pytest

from services import page_ledger as pl
from services import text_match as tm
from services.text_match import quote_in

PAGE = "\n".join([
    "Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008",
    "Data Management and Sharing Plan — Dwight Anderson Williams II",
    "Sharing The research yields journal articles with manuscripts hosted",
    "in the arXiv and videos summarizing mathematical results visible on",
    "the PI's website and backed up in university-provided accounts.",
    "Data The research may also produce quantitative data on video hits.",
    "Page 46 of 56",
])
OTHER = "\n".join([
    "Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008",
    "Mentoring Plan — Dwight Anderson Williams II",
    "Background Details of my undergraduate and graduate mentoring are",
    "drawn from a continually updated practice and varied experiences.",
    "Page 47 of 56",
])
# CORRECTED FIXTURE. `pdf_sections._furniture`'s floor is
# `max(2, int(0.5 * n_pages))`. The brief's `[PAGE, OTHER] * 10` gives 20
# pages, a floor of 10, and PAGE's own heading line repeats exactly 10 times
# (once per PAGE copy) -- which clears the floor and gets misclassified as
# furniture, failing `test_furniture_is_found_by_repetition_not_by_wording`.
# Padding with 18 copies of a THIRD, filler page keeps the floor at 10 while
# each heading line still appears only once (in PAGE, or in OTHER) and the
# stamp -- present on every page, filler included -- clears it honestly.
_FILLER = ("Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008\n"
           "Filler body text for this page.")
FURNITURE = pl.document_furniture([PAGE, OTHER] + [_FILLER] * 18)


def _body(page):
    return pl.body_text(page, FURNITURE)


def test_a_real_quote_from_this_page_verifies():
    assert pl.receipt_ok(_body(PAGE), "The research yields journal articles with manuscripts hosted")


def test_a_quote_from_another_page_is_rejected():
    assert not pl.receipt_ok(_body(PAGE), "Details of my undergraduate and graduate mentoring are")


# THE SECURITY FIXTURE, built to actually exercise `_strip_page_furniture`'s
# widening rather than sidestep it. `_FURNITURE_REACH` is 4 lines each side of
# a "Page N of M" marker, and on a SINGLE page `floor = len(marks) = 1`, so any
# non-empty line within reach is deleted regardless of what it says. Nine
# filler lines (4 before the marker, the marker itself, 4 after) sit between
# two real sentences -- SURVIVOR_A and SURVIVOR_B -- that are nowhere near each
# other in the raw page but land ADJACENT once the furniture walk deletes
# everything between them.
RAW_PAGE = "\n".join([
    "This project builds on longstanding research collaborations across",
    "multiple institutions and trains undergraduate students in applied statistics",
    "Table 1 summarizes annual enrollment by cohort and funding source",
    "Figure 2 shows the retention curve across the first three years",
    "Appendix B lists the courses each trainee is required to complete",
    "Committee members review portfolios each spring during the site visit",
    "Page 12 of 40",
    "Advisors meet weekly with trainees to discuss coursework and progress",
    "Travel funds support conference attendance twice per academic year",
    "External evaluators audit the program every three years for compliance",
    "A logic model ties each activity to a measurable outcome",
    "before advancing into competitive doctoral programs nationwide.",
    "Program alumni now hold positions at national laboratories and universities.",
])


def test_the_quote_in_furniture_hole_is_closed():
    """THE SECURITY TEST. A quote welded from two survivors of one page.

    Built EMPIRICALLY rather than hand-typed, so the test proves its own
    premise instead of assuming it: run `text_match._strip_page_furniture`
    directly on the raw page, see what it actually leaves after deleting the
    nine lines around the "Page 12 of 40" marker, and weld the tail of one
    survivor to the head of the next -- two sentences that sit nine lines
    apart in the real page and are never adjacent in an honest reading of it.
    """
    stripped = tm._strip_page_furniture(RAW_PAGE)
    assert stripped is not None, (
        "fixture must actually trigger the furniture walk, or this proves nothing")
    survivors = stripped.split("\n")
    assert len(survivors) == 4, "expected exactly the 4 lines outside the reach of the marker"
    forged = " ".join(survivors[1].split()[-5:] + survivors[2].split()[:5])

    # PRECONDITION: quote_in, given the whole raw page, is fooled by the weld --
    # this is the hole this module exists to close. If the upstream fix ever
    # lands and this stops being true, this assertion fails loudly instead of
    # the test passing for a reason that no longer holds.
    assert quote_in(RAW_PAGE, forged), (
        "PRECONDITION: quote_in must accept this forgery, or this test proves nothing")

    # THE GUARANTEE: once the page marker is stripped by body_text (as every
    # real caller does before calling receipt_ok), the same forgery is rejected.
    assert not pl.receipt_ok(pl.body_text(RAW_PAGE, FURNITURE), forged)

    # receipt_ok MUST enforce this itself, not merely reward a caller who
    # remembers to pre-clean. Handed the RAW page directly -- no body_text
    # call at all -- it must still reject the forgery.
    assert not pl.receipt_ok(RAW_PAGE, forged)


def test_an_ellipsis_can_span_non_adjacent_lines_on_ONE_page():
    """The adjacency claim in `receipt_ok`'s docstring has a named exception:
    `quote_in`'s own abridgement fallback accepts two fragments joined by an
    ellipsis when each fragment, on its own, is present on the page in order --
    even with real prose between them. Both fragments here are lines 1 and 4 of
    PAGE's body (two lines apart, never merged by furniture-stripping), each
    well over the fragment floor, so this pins the behaviour as real rather
    than assumed from reading `text_match`. It cannot reach across a page: a
    fragment from OTHER would still fail, because page_body is one page only.
    """
    quote = ("The research yields journal articles with manuscripts hosted"
              " ... "
              "The research may also produce quantitative data on video hits")
    assert pl.receipt_ok(_body(PAGE), quote)
    cross_page = ("The research yields journal articles with manuscripts hosted"
                  " ... "
                  "Details of my undergraduate and graduate mentoring are")
    assert not pl.receipt_ok(_body(PAGE), cross_page)


def test_a_quote_shorter_than_the_floor_is_rejected():
    # 5 words -- one short of RECEIPT_MIN_WORDS -- and a REAL, in-order
    # substring of the page otherwise, so this pins the floor at exactly 6
    # rather than merely proving some floor >= 4 exists. Lower RECEIPT_MIN_WORDS
    # to 5 and this quote would verify; the test would then fail to catch it.
    assert not pl.receipt_ok(_body(PAGE), "The research yields journal articles")


def test_an_empty_quote_never_verifies():
    assert not pl.receipt_ok(_body(PAGE), "")
    assert not pl.receipt_ok(_body(PAGE), "   ")


def test_a_dropped_curly_quote_still_verifies():
    """Measured on p26 of the real package: the page reads `Superalgebras". Functional`
    and the model returned the sentence with the closing curly quote OMITTED, which
    `normalize`'s character fold does not cover. The model had read the page."""
    page = 'Kac. “Classification of Simple Lie Superalgebras”. Functional Analysis and Its Applications'
    assert pl.receipt_ok(page, "Classification of Simple Lie Superalgebras. Functional Analysis and Its")


def test_words_out_of_order_are_rejected():
    assert not pl.receipt_ok(_body(PAGE), "journal articles yields research The with manuscripts hosted")


def test_furniture_is_found_by_repetition_not_by_wording():
    assert "Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008" in FURNITURE
    assert "Data Management and Sharing Plan — Dwight Anderson Williams II" not in FURNITURE


def test_a_page_with_no_body_text_is_blank():
    assert pl.is_blank(_body("Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008\nPage 49 of 56"))
    assert not pl.is_blank(_body(PAGE))


def test_the_blank_threshold_is_exactly_40_chars():
    # 300+ chars vs 0 chars (the case above) passes for any BLANK_CHARS in
    # 1..300 -- not a pin. 39 vs 40 is the actual boundary.
    assert pl.is_blank("x" * (pl.BLANK_CHARS - 1))
    assert not pl.is_blank("x" * pl.BLANK_CHARS)


def test_the_page_marker_is_stripped_even_when_it_is_not_repeated():
    """`Page 49 of 56` differs on every page, so it is NOT document furniture and
    survives the share threshold. It must still go, or `quote_in`'s single-page
    furniture path re-engages on the body we hand it."""
    assert "Page 46 of 56" not in _body(PAGE)
