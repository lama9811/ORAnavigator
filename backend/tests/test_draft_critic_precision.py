"""Precision-fix tests for the Draft Critic (2026-06-08).

Covers the three weak spots a real-proposal benchmark exposed:
  1. False "missing" from exact-string section matching (aliases + plurals).
  2. Fragile budget heuristic (stray huge number / only-$0).
  3. Page-limit over-flagging full packages (scope to the named section).
"""

from services.draft_critic import (
    _estimate_section_pages,
    _section_present,
    _section_present_pages,
    check_budget_cap,
    check_page_count,
    check_required_attachments,
)


# ---------------------------------------------------------------------------
# Task 1: section matching -- curated aliases + plural tolerance
# ---------------------------------------------------------------------------

def test_plural_header_matches_singular_required():
    text = "2. Budget Justifications\nPersonnel costs are described here."
    assert _section_present(text, "Budget Justification")


def test_bibliography_alias_matches_references_cited():
    text = "Bibliography and References Cited\n[1] Smith J, 2024."
    assert _section_present(text, "References Cited")


def test_data_mgmt_and_sharing_alias_matches_dmp():
    text = "Data Management and Sharing Plan\nData will be archived publicly."
    assert _section_present(text, "Data Management Plan")


def test_strict_matching_still_rejects_prefix_regression():
    # The anti-false-positive guarantee must survive: "Budget" is NOT
    # "Budget Justification".
    text = "Budget\n$100,000 total"
    assert not _section_present(text, "Budget Justification")


# ---------------------------------------------------------------------------
# Task 2: budget check -- labeled totals + sanity bound
# ---------------------------------------------------------------------------

def test_budget_prefers_labeled_total_over_stray_large_number():
    text = "Our target market is worth $295,000,000.\nTotal Direct Costs: $275,000\n"
    r = check_budget_cap(text, 300_000)
    assert r["status"] == "ok"          # uses the $275k labeled total, not the $295M stray


def test_budget_warns_instead_of_false_fail_on_stray_huge_number():
    text = "The global antibiotics market is $295,000,000 annually.\n"  # no budget total
    r = check_budget_cap(text, 300_000)
    assert r["status"] == "warn"        # don't hard-fail on a number that isn't the budget


def test_budget_warns_instead_of_false_pass_on_only_zero():
    text = "Budget Period Anticipated Amount ($) $0\n"
    r = check_budget_cap(text, 500_000)
    assert r["status"] == "warn"        # $0 is not a real 'under cap' pass


def test_budget_still_fails_on_genuine_overage():
    text = "Total Costs: $650,000 requested.\n"
    r = check_budget_cap(text, 500_000)
    assert r["status"] == "fail"


def test_budget_ignores_labeled_total_without_dollar_sign():
    # NIH budget forms print totals as bare numbers; a "Total Direct Costs 75"
    # must NOT be read as a $75 budget (that was a real false 'ok' on liu_r01).
    text = "Budget Form\nTotal Direct Costs 75 (line 7 of the form)\n"
    r = check_budget_cap(text, 500_000)
    assert r["status"] == "warn"   # no real $ figure -> honest warn, not a false 'ok $75'


# ---------------------------------------------------------------------------
# Task 3: page-limit check scoped to the named section, not the whole document
# ---------------------------------------------------------------------------

def test_page_count_scopes_to_named_section_when_pages_given():
    # 15-page packet, but the Research Strategy itself spans ~2 pages; a
    # 12-page section cap should therefore be OK, not a false 'fail'.
    pages = (["Cover", "Project Summary/Abstract\n..."]
             + ["Research Strategy\nA. Significance ..."]
             + ["...aim 1 and aim 2 detail..."]
             + ["Biographical Sketch\nDr X ..."]
             + ["pad"] * 9
             + ["References Cited\n[1] ..."])      # 15 pages total, RS ~2
    r = check_page_count(len(pages), {"research_strategy": 12}, pages_text=pages)
    assert r["status"] == "ok"


def test_page_count_falls_back_to_total_when_section_not_found():
    r = check_page_count(20, {"project_description": 15}, pages_text=["x", "y"])
    assert r["status"] == "fail"        # header absent -> total-doc fallback


def test_page_count_backward_compatible_without_pages_text():
    r = check_page_count(10, {"project_description": 15})
    assert r["status"] == "ok"


# ---------------------------------------------------------------------------
# Round 2 / #1: running-header (positional, multi-page) matching
# ---------------------------------------------------------------------------

def test_running_header_with_page_number_detected():
    # "Research Strategy 80/81" as the first line of >=2 pages = a real header.
    pages = ["Cover sheet",
             "Research Strategy 80\nAim 1 narrative ...",
             "Research Strategy 81\n... continued ...",
             "Biographical Sketch\nDr X ..."]
    assert _section_present_pages("\n\n".join(pages), pages, "Research Strategy")


def test_toc_single_pagenumber_line_is_not_a_false_positive():
    # A table-of-contents that lists "Research Strategy 80" ONCE must not count.
    toc = "Table of Contents\nResearch Strategy 80\nBudget Justification 95\nReferences 110"
    pages = [toc, "Body text that never repeats a Research Strategy header."]
    assert not _section_present_pages("\n\n".join(pages), pages, "Research Strategy")


def test_running_header_needs_two_pages_not_one():
    pages = ["Research Strategy 80\nonly once here", "totally different content"]
    assert not _section_present_pages("\n\n".join(pages), pages, "Research Strategy")


# ---------------------------------------------------------------------------
# Round 2 / #2: synonym groups
# ---------------------------------------------------------------------------

def test_resource_sharing_plan_satisfies_data_management_plan():
    text = "Resource Sharing Plan\nData and models will be shared publicly."
    assert _section_present_pages(text, [text], "Data Management Plan")


def test_bare_references_satisfies_references_cited():
    text = "References\n[1] Smith J. 2024. Journal of Things."
    assert _section_present_pages(text, [text], "References Cited")


def test_synonyms_flow_through_required_attachments_check():
    text = "Resource Sharing Plan\nData shared.\nReferences\n[1] X."
    r = check_required_attachments(text, ["Data Management Plan", "References Cited"],
                                   pages_text=[text])
    assert r["status"] == "ok"
    assert r["missing"] == []


def test_required_attachments_still_flags_a_truly_absent_section():
    text = "Specific Aims\nWe aim to do things.\nResearch Strategy\nApproach ..."
    r = check_required_attachments(text, ["Biographical Sketch"], pages_text=[text])
    assert r["status"] == "fail"
    assert "Biographical Sketch" in r["missing"]


# ---------------------------------------------------------------------------
# Round 2 / #3: section page-span estimate stops at the next MAJOR section
# ---------------------------------------------------------------------------

def test_estimate_section_pages_ignores_internal_subheadings():
    pages = [
        "Specific Aims\nWe aim ...",
        "Research Strategy\nSignificance ...",   # section starts here (index 1)
        "Significance continued discussion ...",  # internal -> NOT a boundary
        "Approach\nmethods and timeline ...",      # internal -> NOT a boundary
        "Biographical Sketch\nDr X ...",           # next MAJOR section -> boundary (index 4)
        "more biosketch",
    ]
    assert _estimate_section_pages(pages, "Research Strategy") == 3


def test_estimate_section_pages_handles_pagenumber_running_headers():
    pages = [
        "Research Strategy 80\nAim 1 ...",
        "Research Strategy 81\n... continued ...",
        "Research Strategy 82\n... continued ...",
        "References Cited 83\n[1] Smith ...",      # next MAJOR section -> boundary
    ]
    assert _estimate_section_pages(pages, "Research Strategy") == 3


# ---------------------------------------------------------------------------
# Round 3: real-corpus gaps (Facilities naming + NIH per-page PI banner)
# ---------------------------------------------------------------------------

def test_facilities_matches_facilities_and_other_resources():
    text = "Facilities & Other Resources\nOur labs include a BSL-3 suite."
    assert _section_present_pages(text, [text], "Facilities")


def test_running_header_detected_below_per_page_pi_banner():
    # NIH assembled apps stamp a "Contact PD/PI:" banner as line 1 of EVERY
    # page; the real section running-header sits on line 2-3. Must still detect.
    pages = [
        "Contact PD/PI: Smith, Jane\nResearch Strategy 80\nAim 1 ...",
        "Contact PD/PI: Smith, Jane\nResearch Strategy 81\n... continued ...",
        "Contact PD/PI: Smith, Jane\nBiographical Sketch\nDr X ...",
    ]
    assert _section_present_pages("\n\n".join(pages), pages, "Research Strategy")
