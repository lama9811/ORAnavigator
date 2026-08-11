"""Deterministic checks that must work for every funder.

Two of these guard against wrong DOLLAR figures and wrong claims about a PI's
own text, which is why they assert on the status vocabulary and not just on
"something was returned".
"""
import re

from services import generic_checks as gc


def _ctx(**kw):
    base = {"text": "", "spans": {}, "title": None, "budget": None, "profile": {}}
    base.update(kw)
    return base


# ── contract_requirements ───────────────────────────────────────────────────

def test_rows_are_built_for_limits_attachments_and_cap():
    rows = gc.contract_requirements({
        "page_limits": {"project_description": 12},
        "required_attachments": ["Data Management Plan"],
        "budget_cap": 400000,
    })
    assert {r["check"] for r in rows} == {"page_limit", "attachment_present", "budget_cap"}
    assert all(r["kind"] == "deterministic" for r in rows)
    # Every row carries the sentence it came from (golden rule 2).
    assert all(r["source"] for r in rows)


def test_a_row_prefers_the_contracts_own_verbatim_quote():
    rows = gc.contract_requirements({
        "budget_cap": 400000,
        "source_quotes": {"budget_cap": "Awards will not exceed $400,000 total."},
    })
    assert rows[0]["source"] == "Awards will not exceed $400,000 total."


def test_a_non_numeric_page_limit_produces_no_check_rather_than_a_guess():
    rows = gc.contract_requirements({"page_limits": {"project_description": "fifteen"}})
    assert rows == []


def test_limit_rows_are_prohibitions_so_the_ui_never_calls_them_missing():
    rows = gc.contract_requirements({"page_limits": {"x": 2}, "budget_cap": 100})
    assert all(r["flag_if_present"] for r in rows)


# ── page limit ──────────────────────────────────────────────────────────────

def test_an_over_length_section_is_flagged_and_says_it_is_an_estimate():
    req = {"id": "pl", "label": "x", "check": "page_limit",
           "check_args": {"section": "project_description", "limit": 1}}
    ctx = _ctx(spans={"project_description": {"text": "word " * 1200}})
    status, detail, _ = gc.CHECKS["page_limit"](ctx, req)
    assert status == "flagged"
    assert "estimate" in detail.lower()


def test_a_section_within_its_limit_is_clear():
    req = {"id": "pl", "label": "x", "check": "page_limit",
           "check_args": {"section": "project_description", "limit": 15}}
    ctx = _ctx(spans={"project_description": {"text": "word " * 300}})
    assert gc.CHECKS["page_limit"](ctx, req)[0] == "clear"


def test_a_section_we_never_found_is_unlocated_never_over_the_limit():
    # The distinction the whole feature rests on: "we did not find it" is not
    # the same claim as "it is too long", and only one of them is about the PI.
    req = {"id": "pl", "label": "x", "check": "page_limit",
           "check_args": {"section": "project_description", "limit": 1}}
    assert gc.CHECKS["page_limit"](_ctx(), req)[0] == "could_not_locate"


# ── attachment present ──────────────────────────────────────────────────────

def _att_req():
    return {"id": "at", "label": "Data Management Plan", "check": "attachment_present",
            "check_args": {"name": "Data Management Plan",
                           "section_key": "data_management_plan"}}


def test_a_located_section_span_satisfies_the_attachment_check():
    ctx = _ctx(spans={"data_management_plan": {"text": "All data will be deposited.",
                                               "marker": "Data Management Plan"}})
    status, _, evidence = gc.CHECKS["attachment_present"](ctx, _att_req())
    assert status == "addressed"
    assert evidence


def test_a_heading_line_satisfies_the_attachment_check():
    ctx = _ctx(text="Project Summary\n\nDATA MANAGEMENT PLAN\nAll data will be deposited.")
    assert gc.CHECKS["attachment_present"](ctx, _att_req())[0] == "addressed"


def test_a_mention_in_prose_is_partial_not_addressed():
    # The false positive that would cost a submission: the narrative referring to
    # a plan that was never actually attached.
    ctx = _ctx(text="Our data will be shared as described in our Data Management Plan.")
    status, detail, evidence = gc.CHECKS["attachment_present"](ctx, _att_req())
    assert status == "partial"
    assert "own section" in detail
    assert evidence


def test_an_absent_attachment_is_not_found():
    ctx = _ctx(text="Project Summary\n\nWe will study polymers.")
    assert gc.CHECKS["attachment_present"](ctx, _att_req())[0] == "not_found"


def test_an_attachment_with_no_name_is_not_checked():
    req = {"id": "at", "label": "x", "check": "attachment_present", "check_args": {"name": ""}}
    assert gc.CHECKS["attachment_present"](_ctx(text="anything"), req)[0] == "not_checked"


# ── budget cap ──────────────────────────────────────────────────────────────

def _cap_req(cap=100000):
    return {"id": "bc", "label": "cap", "check": "budget_cap", "check_args": {"cap": cap}}


def test_a_single_year_budget_over_cap_is_flagged():
    status, detail, _ = gc.CHECKS["budget_cap"](_ctx(budget={"total": 150000}), _cap_req())
    assert status == "flagged"
    assert "50,000 over" in detail


def test_no_saved_budget_is_not_checked_not_failed():
    status, detail, _ = gc.CHECKS["budget_cap"](_ctx(), _cap_req())
    assert status == "not_checked"
    assert "Budget Helper" in detail


def test_a_multi_year_budget_is_measured_on_its_cumulative_total():
    # budget["total"] is YEAR 1. Measuring the cap against it would tell a PI
    # requesting $300k over three years that they are inside a $100k cap.
    budget = {"total": 100000,
              "multi_year": {"project_years": 3, "cumulative": {"total": 300000},
                             "cap": None, "cap_status": "none", "cap_basis": "cumulative"}}
    status, detail, _ = gc.CHECKS["budget_cap"](_ctx(budget=budget), _cap_req())
    assert status == "flagged"
    assert "300,000" in detail and "all 3 years" in detail


def test_the_budget_helpers_own_verdict_wins_when_it_covers_the_same_cap():
    budget = {"total": 100000,
              "multi_year": {"project_years": 3, "cumulative": {"total": 290000},
                             "cap": 300000, "cap_status": "ok", "cap_basis": "cumulative"}}
    status, _, _ = gc.CHECKS["budget_cap"](_ctx(budget=budget), _cap_req(300000))
    assert status == "clear"


def test_a_per_year_cap_reports_the_worst_year_not_the_sum():
    budget = {"total": 120000,
              "multi_year": {"project_years": 3, "cumulative": {"total": 360000},
                             "cap": 100000, "cap_status": "over", "cap_overage": 20000,
                             "cap_basis": "per_year"}}
    status, detail, _ = gc.CHECKS["budget_cap"](_ctx(budget=budget), _cap_req(100000))
    assert status == "flagged"
    assert "worst year" in detail and "per-year cap" in detail


# ── the guarantee that made this module necessary ───────────────────────────

def test_no_check_in_this_module_names_a_funder():
    import inspect
    src = inspect.getsource(gc).lower()
    for token in (r"23-598", r"\beir\b", r"\bhbcu\b", r"\bnsf\b", r"\bnih\b"):
        assert not re.search(token, src), f"funder-specific token in generic_checks: {token}"
