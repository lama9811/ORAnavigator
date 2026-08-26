"""Draft Review checks the solicitation and NSF's basics — not all 204 rules.

Product decision, 2026-08-26, and the direct extension of the same decision
taken for Check a Section earlier the same day. Measured on a live proposal:

    before   204 rules  =  48 solicitation + 14 NSF basics + 142 extended PAPPG
    after     62 rules  =  48 solicitation + 14 NSF basics

WHAT THIS RETIRES, RECORDED RATHER THAN GLOSSED
-----------------------------------------------
Check a Section already excludes the extended rows, so Draft Review was the only
place they still ran. After this they are live in nothing: 142 reviewed PAPPG
rules sit in `kb_structured/_pappg_24_1_rules.json` and no code path reads them
for a review. Among them are the 19 PROHIBITIONS -- "Do not request NSF funds
for alcoholic beverages", "Prohibit voluntary committed cost sharing", "Do not
frame the Project Summary as an abstract" -- which are the rules a PI is least
able to discover without reading the PAPPG themselves.

What makes it defensible: NONE of the 142 carries a deterministic check. Every
code-decided rule is in the curated 14, so this narrows the MODEL-judged half
and leaves the arithmetic untouched.
"""

import pytest

from services import draft_review
from services import rulebook_baseline as rb
from services import solicitation_profile as sp


PACKAGE = (
    "Project Summary\n\nOverview\nWe study estuarine salinity sensing.\n\n"
    "Intellectual Merit\nThe work advances polymer science.\n\n"
    "Broader Impacts\nFour undergraduates will be trained each year.\n\n"
    "Project Description\n\nThe proposed work develops zwitterionic networks.\n\n"
    "Budget Justification\n\nThe PI requests 1.5 months of summer salary.\n")


def _profile():
    """A profile whose solicitation CITES the PAPPG, so the rulebook rows are
    injected exactly as `load_solicitation_profile` injects them in production.
    A fixture that skips the citation tests nothing -- that is how the same
    filter looked like it worked on Check a Section and did not."""
    cite = "Proposers must follow the PAPPG for all proposal preparation."
    rows = [
        {"id": "sol_ps", "section": "project_summary",
         "section_label": "Project Summary",
         "label": "Include the LOI number in the Project Summary",
         "kind": "semantic", "scored": True, "source": cite,
         "why": "", "keywords": []},
        {"id": "sol_bud", "section": "budget_justification",
         "section_label": "Budget and Budget Justification",
         "label": "Cap equipment at 30 percent", "kind": "semantic",
         "scored": True, "source": cite, "why": "", "keywords": []},
    ]
    return sp.build_generic({}, rows, id="NSF 23-598", title="t")


def test_the_fixture_really_does_inject_the_rulebook():
    injected = [r for r in _profile()["requirements"] if r.get("tier") == "extended"]
    assert len(injected) > 100, len(injected)


def test_a_review_checks_the_solicitation_and_the_basics_only():
    result = draft_review.review_draft(PACKAGE, profile=_profile(), use_ai=False)
    seen = {f["id"] for f in result["findings"]}
    extended = {r["id"] for r in rb.rules_for("the PAPPG", tier="extended")}
    assert not (seen & extended), sorted(seen & extended)[:6]


def test_the_solicitations_own_rules_are_still_checked():
    result = draft_review.review_draft(PACKAGE, profile=_profile(), use_ai=False)
    seen = {f["id"] for f in result["findings"]}
    assert {"sol_ps", "sol_bud"} <= seen, sorted(seen)


def test_the_nsf_basics_are_still_checked():
    result = draft_review.review_draft(PACKAGE, profile=_profile(), use_ai=False)
    seen = {f["id"] for f in result["findings"]}
    basics = {r["id"] for r in rb.rules_for("the PAPPG", tier="basic")}
    assert seen & basics, "no NSF basic rule was checked"


def test_a_section_left_with_no_rules_is_not_reported_missing():
    """The extended rows brought sections of their own into the universe. With
    those rows gone the sections hold nothing, and listing them as "not located"
    would tell a PI their package is missing parts nothing was going to check.
    """
    result = draft_review.review_draft(PACKAGE, profile=_profile(), use_ai=False)
    checked = {f.get("section") for f in result["findings"]}
    for entry in result["sections_missing"]:
        assert entry["key"] in checked or any(
            f.get("section") == entry["key"] for f in result["findings"]), entry


def test_the_review_is_much_shorter_than_it_was():
    """The number the decision was taken on. A fix-list of 204 rows buries the
    handful that matter -- the failure this repo already predicted in writing
    before the PAPPG rules shipped."""
    result = draft_review.review_draft(PACKAGE, profile=_profile(), use_ai=False)
    assert len(result["findings"]) < 40, len(result["findings"])


def test_a_section_that_exists_for_a_REQUIRED_ATTACHMENT_survives():
    """Caught by the existing suite, not by this file.

    The first version dropped every section left with no rules. A required
    attachment puts a section in the universe carrying no requirement rows of
    its own -- the locate stage still looks for it, and "this attachment is
    missing" is the compliance rejection the whole tool exists to prevent. Only
    sections an EXTENDED row brought in are dropped.
    """
    prof = _profile()
    prof["sections"]["collaboration_letters"] = {
        "label": "Letters of Collaboration", "aliases": ["letters of collaboration"]}
    result = draft_review.review_draft(PACKAGE, profile=prof, use_ai=False)
    keys = ({s["key"] for s in result["sections_missing"]}
            | {s["key"] for s in result["sections_located"]})
    assert "collaboration_letters" in keys, sorted(keys)
