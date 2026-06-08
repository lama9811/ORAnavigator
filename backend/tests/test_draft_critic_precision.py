"""Precision-fix tests for the Draft Critic (2026-06-08).

Covers the three weak spots a real-proposal benchmark exposed:
  1. False "missing" from exact-string section matching (aliases + plurals).
  2. Fragile budget heuristic (stray huge number / only-$0).
  3. Page-limit over-flagging full packages (scope to the named section).
"""

from services.draft_critic import (
    _section_present,
    check_budget_cap,
    check_page_count,
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
