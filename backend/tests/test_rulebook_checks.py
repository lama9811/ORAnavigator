"""The deterministic half of the rulebook baseline.

FALSE POSITIVES ARE THE RISK, and every guard here has its own test.
mechanical_checks shipped a case-insensitive \\bTO\\s?DO\\b that flagged
"would allow us to do much more work" as unfilled template text -- its own
docstring had warned about exactly that, and a USER found it, not a test.
"""
import pytest

from services import rulebook_checks as rc


def _ctx(section_text, *, key="project_summary", pages=None):
    return {"text": section_text, "spans": {key: {"text": section_text,
                                                  "marker": key, "start": 0}},
            "title": None, "budget": None, "profile": {}, "pages": pages}


def _req(check_args):
    return {"check_args": check_args}


# ── the bug this whole feature exists to remove ─────────────────────────────

FIVE_LINE_SUMMARY = """Project Summary

We propose to study trustworthy cardiac AI using multimodal physiological
sensing. The work will develop new models and validate them on clinical data.
We expect the results to be significant for the field.
"""

ARGS3 = {"section": "project_summary",
         "headings": ["Overview", "Intellectual Merit", "Broader Impacts"]}


def test_a_five_line_summary_with_no_headings_is_not_found():
    """The exact draft that came back 'Addressed' before this existed."""
    status, detail, evidence = rc.rb_headings(_ctx(FIVE_LINE_SUMMARY), _req(ARGS3))
    assert status == "not_found"
    assert "Overview" in detail and "Intellectual Merit" in detail


def test_all_three_headings_present_is_addressed():
    text = ("Overview\nWe will build X.\n\n"
            "Intellectual Merit\nThis advances Y.\n\n"
            "Broader Impacts\nStudents trained.\n")
    status, _, _ = rc.rb_headings(_ctx(text), _req(ARGS3))
    assert status == "addressed"


def test_two_of_three_headings_is_partial_and_names_the_missing_one():
    text = "Overview\nWe will build X.\n\nIntellectual Merit\nThis advances Y.\n"
    status, detail, _ = rc.rb_headings(_ctx(text), _req(ARGS3))
    assert status == "partial"
    assert "Broader Impacts" in detail


# ── heading-shape guards ────────────────────────────────────────────────────

@pytest.mark.parametrize("heading", [
    "Overview", "OVERVIEW", "overview", "**Overview**", "1. Overview",
    "Overview:", "  Overview  ", "- Overview", "II. Overview",
])
def test_real_heading_shapes_are_found(heading):
    text = f"{heading}\nWe will build X.\n\nIntellectual Merit\nY.\n\nBroader Impacts\nZ.\n"
    status, _, _ = rc.rb_headings(_ctx(text), _req(ARGS3))
    assert status == "addressed", heading


def test_a_heading_word_inside_a_sentence_is_not_a_heading():
    """NSF's own criterion is 'on its own line with no other text on that line'.
    Without this, a summary DISCUSSING intellectual merit passes the check for
    HAVING an Intellectual Merit statement -- which is the original bug wearing
    a different hat."""
    text = ("Overview\nWe will build X.\n\n"
            "The intellectual merit of this work is considerable, and the "
            "broader impacts are substantial.\n")
    status, detail, _ = rc.rb_headings(_ctx(text), _req(ARGS3))
    assert status == "partial"
    assert "Intellectual Merit" in detail


def test_the_evidence_quotes_the_headings_actually_found():
    text = "Overview\nX.\n\nIntellectual Merit\nY.\n\nBroader Impacts\nZ.\n"
    _, _, evidence = rc.rb_headings(_ctx(text), _req(ARGS3))
    assert "Overview" in evidence


def test_a_missing_section_is_could_not_locate_not_not_found():
    """could_not_locate != not_found. Telling a PI their summary has no
    Intellectual Merit statement when we simply never found the summary sends
    them rewriting something they already wrote."""
    ctx = {"text": "", "spans": {}, "title": None, "budget": None,
           "profile": {}, "pages": None}
    status, _, _ = rc.rb_headings(ctx, _req(ARGS3))
    assert status == "could_not_locate"


# ── URLs in the Project Description ─────────────────────────────────────────

PD = {"section": "project_description"}


@pytest.mark.parametrize("url", [
    "See https://example.edu/data for details.",
    "Available at http://example.edu.",
    "See www.example.edu for the code.",
    "Archived at doi.org/10.1234/abcd.",
])
def test_a_url_in_the_project_description_is_flagged(url):
    status, _, evidence = rc.rb_no_urls(
        _ctx(url, key="project_description"), _req(PD))
    assert status == "flagged"
    assert evidence


def test_an_email_address_is_not_a_hyperlink():
    """A collaborator's address is not a hyperlink and flagging it is noise."""
    status, _, _ = rc.rb_no_urls(
        _ctx("Contact pi@morgan.edu for details.", key="project_description"),
        _req(PD))
    assert status == "clear"


def test_ordinary_prose_is_clear():
    status, _, _ = rc.rb_no_urls(
        _ctx("We will study cardiac signals in year one.",
             key="project_description"), _req(PD))
    assert status == "clear"


# ── financial information in Facilities ─────────────────────────────────────

FE = {"section": "facilities_equipment_and_other_resources"}


def test_a_dollar_figure_in_facilities_is_flagged():
    status, _, evidence = rc.rb_no_financials(
        _ctx("The cluster was purchased for $240,000 in 2024.",
             key="facilities_equipment_and_other_resources"), _req(FE))
    assert status == "flagged"
    assert "$240,000" in evidence


def test_the_word_funds_is_not_financial_information():
    """NSF's OWN instruction for this section reads 'for whom no funds are being
    requested'. A word-level match would flag the section for complying with the
    rule it is being checked against."""
    status, _, _ = rc.rb_no_financials(
        _ctx("Dr. Smith contributes effort for whom no funds are being requested.",
             key="facilities_equipment_and_other_resources"), _req(FE))
    assert status == "clear"


def test_a_year_is_not_a_dollar_figure():
    status, _, _ = rc.rb_no_financials(
        _ctx("The laboratory was renovated in 2019 and holds 12 benches.",
             key="facilities_equipment_and_other_resources"), _req(FE))
    assert status == "clear"


# ── et al. ──────────────────────────────────────────────────────────────────

RC_ARGS = {"section": "references_cited"}


def test_et_al_is_flagged():
    status, _, evidence = rc.rb_et_al(
        _ctx("Smith et al. (2019). A paper.", key="references_cited"),
        _req(RC_ARGS))
    assert status == "flagged"
    assert "et al." in evidence


def test_et_alia_is_not_et_al():
    status, _, _ = rc.rb_et_al(
        _ctx("Smith, Jones et alia (2019).", key="references_cited"),
        _req(RC_ARGS))
    assert status == "clear"


def test_a_surname_etal_is_not_et_al():
    status, _, _ = rc.rb_et_al(
        _ctx("Etal, M. (2019). A paper.", key="references_cited"), _req(RC_ARGS))
    assert status == "clear"


# ── page limits: an estimate is never a verdict ─────────────────────────────

PAGE1 = {"section": "project_summary", "limit": 1}


def test_a_real_page_count_gives_a_real_verdict():
    ctx = _ctx("Short summary.", pages={"project_summary": 2})
    status, detail, _ = rc.rb_page_limit(ctx, _req(PAGE1))
    assert status == "flagged"
    assert "estimate" not in detail.lower()


def test_a_real_page_count_within_the_limit_is_clear():
    ctx = _ctx("Short summary.", pages={"project_summary": 1})
    status, _, _ = rc.rb_page_limit(ctx, _req(PAGE1))
    assert status == "clear"


def test_a_paste_never_returns_a_pass_or_fail_page_verdict():
    """Pages are a PDF property. Reporting a word-count estimate as a verdict is
    the same error class as the scraper calling an unreadable page a deleted
    one."""
    ctx = _ctx("word " * 4000)          # ~7 pages by estimate, well over 1
    status, detail, _ = rc.rb_page_limit(ctx, _req(PAGE1))
    assert status == "not_checked"
    assert "estimate" in detail.lower()
    assert "upload" in detail.lower()


def test_a_paste_estimate_is_excluded_from_the_score():
    """not_checked is outside draft_review._CREDIT, so an estimate can never
    move the number."""
    from services import draft_review
    assert "not_checked" not in draft_review._CREDIT


def test_every_check_is_registered():
    assert set(rc.CHECKS) == {"rb_not_in_text", "rb_formatting",
                              "rb_headings", "rb_no_urls", "rb_no_financials",
                              "rb_et_al", "rb_narrative", "rb_page_limit",
                              # 2026-09-01: References Cited's first
                              # deterministic scored rule. See
                              # test_reference_year_check.py.
                              "rb_citation_year"}


def test_every_deterministic_rule_names_a_check_that_exists():
    """Lives HERE, not in test_rulebook_baseline.py, so Task 1 could commit
    green — it needs both modules. A row whose check resolves to nothing is
    silently SKIPPED by run_deterministic, so a typo removes a rule with
    nothing going red. That is what this guards."""
    from services import rulebook_baseline as rb
    for rows in rb.RULES.values():
        for r in rows:
            if r["kind"] == "deterministic":
                assert r["check"] in rc.CHECKS, r["id"]
