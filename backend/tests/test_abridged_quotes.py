"""A quote the reviewer shortened with an ellipsis is still evidence.

MEASURED 2026-08-28 on the awarded NSF EiR Project Description. The reviewer
returns its evidence ABRIDGED:

    "There are presently 50 math and actuarial science majors along with 37
     doctoral students... Faculty at MSU are expected to carry 12 credit hours"

Both halves are in the draft, in that order, with a sentence between them. It is
not a CONTIGUOUS span, so golden rule 2 dropped it and the row was reported
`not_found` -- the draft told it never described its institutional context.
Across 50 runs, 30 findings were demoted this way and every one was in Project
Description; after the reading-order fix, 32 of 55 dropped quotes contained an
ellipsis and 25 of those had every fragment present, in order. The same shape
defeats this repo's own curated NSF fixture row.

WHAT IS AND IS NOT WIDENED. Golden rule 2 asks that a claim be checkable against
the author's own words. Requiring every fragment to appear IN ORDER keeps that:
every word of the quote is still found in the draft, in the sequence the
reviewer put them. What is given up is only the requirement that they be
ADJACENT. A fabricated sentence still cannot pass, because a fabrication is not
in the text under any reading.

THREE GUARDS, and the first is what stops this becoming a bag-of-words match:
  * EVERY fragment must clear _MIN_FRAGMENT characters. One short fragment and
    the whole quote is rejected -- "the PI... the work... the results" would
    otherwise verify against almost any draft.
  * They must appear in ORDER, each after the previous one ends. Finding the
    same words scattered backwards is not finding the sentence.
  * Only attempted after an honest contiguous match has failed, and only when
    the quote actually contains an ellipsis -- so this cannot reach a quote that
    does not use one.
"""
import pytest

from services.text_match import quote_in


DRAFT = (
    "In Fall 2024 the PI joined the Department of Mathematics.\n"
    "There are presently 50 math and actuarial science majors along with 37 "
    "doctoral students, almost all of whom pursue the PhD.\n"
    "The department has recently expanded its seminar series.\n"
    "Faculty at MSU are expected to carry 12 credit hours of teaching each "
    "semester, which shapes the research calendar.\n"
    "The research supports two undergraduates in Year 1.\n"
)


def test_an_abridged_quote_whose_fragments_are_all_present_verifies():
    q = ("There are presently 50 math and actuarial science majors along with "
         "37 doctoral students... Faculty at MSU are expected to carry 12 "
         "credit hours")
    assert quote_in(DRAFT, q)


def test_the_unicode_ellipsis_works_the_same_way():
    q = ("There are presently 50 math and actuarial science majors… "
         "Faculty at MSU are expected to carry 12 credit hours")
    assert quote_in(DRAFT, q)


def test_a_bracketed_ellipsis_works_the_same_way():
    q = ("There are presently 50 math and actuarial science majors [...] "
         "Faculty at MSU are expected to carry 12 credit hours")
    assert quote_in(DRAFT, q)


def test_a_trailing_ellipsis_leaves_one_fragment_and_still_verifies():
    assert quote_in(DRAFT, "There are presently 50 math and actuarial science "
                           "majors along with 37 doctoral students...")


def test_fragments_out_of_order_are_rejected():
    """Finding the words backwards is not finding the sentence."""
    q = ("Faculty at MSU are expected to carry 12 credit hours... There are "
         "presently 50 math and actuarial science majors along with 37 doctoral")
    assert not quote_in(DRAFT, q)


def test_a_fragment_that_is_not_in_the_draft_is_rejected():
    q = ("There are presently 50 math and actuarial science majors... the "
         "department guarantees each student a full tuition waiver")
    assert not quote_in(DRAFT, q)


def test_a_short_fragment_rejects_the_whole_quote():
    """The guard that stops this becoming a bag-of-words match.

    Every one of these scraps IS in the draft and in order; the quote is still
    refused, because fragments this small carry no evidence.
    """
    assert not quote_in(DRAFT, "the PI... the work... the results")
    assert not quote_in(DRAFT, "In Fall 2024... Faculty at MSU are expected to "
                               "carry 12 credit hours of teaching")


def test_a_fabricated_claim_cannot_be_stitched_together():
    q = ("There are presently 50 math and actuarial science majors... and the "
         "program will be terminated at the end of Year 2")
    assert not quote_in(DRAFT, q)


def test_a_quote_with_no_ellipsis_is_unaffected():
    assert quote_in(DRAFT, "The department has recently expanded its seminar series")
    assert not quote_in(DRAFT, "The department has recently closed its seminar series")


def test_an_ellipsis_the_author_actually_wrote_still_matches_contiguously():
    """A draft containing a real ellipsis matches on the honest path, before any
    of this runs -- the widening is only ever reached after that fails."""
    drafted = "The team asked: what if...? and then answered it."
    assert quote_in(drafted, "what if...? and then answered it")


def test_the_widening_composes_with_the_page_furniture_reading():
    """Both artifacts in one quote: abridged AND split by a page footer."""
    doc = (
        "There are presently 50 math and actuarial science majors along with\n"
        "Page 3 of 9\n"
        "Submitted/PI: A Researcher /Proposal No: 2503008\n"
        "37 doctoral students, almost all of whom pursue the PhD.\n"
        "The department has recently expanded its seminar series.\n"
        "Faculty at MSU are expected to carry 12 credit hours each semester.\n"
        "Page 4 of 9\n"
        "Submitted/PI: A Researcher /Proposal No: 2503008\n"
    )
    q = ("There are presently 50 math and actuarial science majors along with "
         "37 doctoral students... Faculty at MSU are expected to carry 12 "
         "credit hours")
    assert quote_in(doc, q)
