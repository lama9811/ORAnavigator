"""A quote split by a page footer is still a quote from the draft.

THE FOURTH CHARACTER-LEVEL PDF ARTIFACT, same signature as the three CLAUDE.md
already records (welded words, dash-broken words, lost ligatures): the tool
says something FALSE about the PROPOSAL instead of reporting a bad read.

MEASURED 2026-08-28 on the awarded NSF EiR package. Research.gov stamps every
page of a submitted PDF with a three-line footer, so a sentence crossing a page
boundary extracts as:

    'Hence, your research programs are strongly
     Page 52 of 56
     Revised Proposal Budget Revision #1 for 2503008 Submitted On ...
     Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008
     supported by me and they firmly align with the mission ...'

The reviewer reads the sentence correctly and quotes it whole. `quote_in` does
a contiguous match, fails, and golden rule 2 demotes `addressed` to
`not_found` -- so "Attach Letter of Institutional Support" was reported MISSING
on a funded proposal whose letter is right there, under a note describing the
letter. A required attachment declared missing is the compliance rejection this
tool exists to prevent, invented by the tool.

WHY THE STRIPPING IS ANCHORED TO THE PAGE MARKER AND NOT TO REPETITION. In that
same file the line "Sincerely," occurs three times -- it is real prose, in three
letters. A "drop lines that repeat" rule would delete an author's words from the
text every claim is checked against, which is the dangerous direction. Only a
run of lines adjacent to a `Page N of M` marker is treated as furniture.
"""
import pytest

from services.text_match import quote_in


FOOTER = (
    "Page 52 of 56\n"
    "Revised Proposal Budget Revision #1 for 2503008 Submitted On Fri Jun 20 "
    "09:52:24 EDT 2025 Electronic Signature\n"
    "Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008\n"
)

# The footer repeats, which is what makes it furniture rather than content.
DRAFT = (
    "Dear Colleagues,\n"
    "The department is committed to this work.\n"
    + FOOTER +
    "Hence, your research programs are strongly\n"
    + FOOTER.replace("Page 52", "Page 53") +
    "supported by me and they firmly align with the mission of the department "
    "and strategic plans for the university to become a research high, R1 "
    "institution.\n"
    "Sincerely,\n"
    + FOOTER.replace("Page 52", "Page 54") +
    "Sincerely,\n"
)

SPLIT_QUOTE = ("Hence, your research programs are strongly supported by me and "
               "they firmly align with the mission of the department")


def test_a_quote_split_by_a_page_footer_verifies():
    assert quote_in(DRAFT, SPLIT_QUOTE), (
        "the sentence is in the draft; only Research.gov's page furniture "
        "sits in the middle of it")


def test_an_ordinary_quote_still_verifies():
    assert quote_in(DRAFT, "The department is committed to this work")


def test_a_quote_that_is_not_in_the_draft_is_still_rejected():
    """The widening must not turn the gate off."""
    assert not quote_in(DRAFT, "The department will provide $400,000 in matching funds")
    assert not quote_in(DRAFT, "your research programs are weakly supported by me")


def test_repeated_prose_is_not_treated_as_furniture():
    """'Sincerely,' repeats three times and is the author's word, not a footer.

    Stripping it would delete real text from the haystack, and a claim quoting
    it would then be judged against a draft that no longer contains it.
    """
    assert quote_in(DRAFT, "Sincerely,")


def test_furniture_is_not_glued_into_a_false_positive():
    """Removing the footer must not manufacture a sentence nobody wrote.

    The words either side of a stripped footer become adjacent, so a quote
    spanning them verifies -- that is the fix. What must NOT verify is a quote
    built out of the footer's own words joined to the prose.
    """
    assert not quote_in(DRAFT, "Electronic Signature Hence, your research programs")
