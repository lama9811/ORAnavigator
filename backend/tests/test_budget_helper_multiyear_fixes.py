"""Regression tests for the five confirmed multi-year Budget Helper bugs
(found 2026-08-10 by auditing the module against NSF PAPPG 24-1 II.D.2.f and a
real AWARDED Morgan State HBCU-EiR budget).

Each test here failed before the fix. The two marked DANGEROUS put a wrong
dollar figure into a document a PI can paste straight into a submission.

Calibration source: NSF-EiR-ProposalTomHulse.pdf (morgan.edu, awarded) carries
FOUR budget pages — Year 1, Year 2, Year 3 and Cumulative — which is what PAPPG
means by "Each proposal must contain a budget for each year of support
requested." Our multi-year output is a projection, not that; these tests pin the
parts we CAN make correct without redesigning the input model.
"""

import re

from services.budget_helper import (
    budget_to_csv,
    compute_budget,
    draft_justification,
)


def _multi(**over):
    inputs = {
        "people": [{"name": "PI", "base_salary": 100_000, "effort_pct": 20}],
        "equipment": 60_000, "travel": 5_000, "supplies": 3_000,
        "project_years": 3, "escalation_pct": 3,
    }
    inputs.update(over)
    return compute_budget(inputs)


def _row(budget, label):
    return next(r for r in budget["table"]["rows"] if r["label"] == label)


# ── BUG 1: one-time costs were multiplied by the number of years ────────────

def test_equipment_is_not_repeated_in_every_year_by_default():
    """A $60,000 instrument became $180,000 across a 3-year budget — inflating
    the table, the cumulative total AND the cap check, with no way to say
    'Year 1 only'."""
    r = _multi()
    assert _row(r, "Equipment")["values"] == [60_000.0, 0.0, 0.0, 60_000.0]


def test_recurring_categories_still_repeat_every_year():
    """Travel and supplies ARE annual costs — only capital purchases default to
    one-time. Fixing equipment must not silently zero out the rest."""
    r = _multi()
    assert _row(r, "Travel")["values"] == [5_000.0, 5_000.0, 5_000.0, 15_000.0]
    assert _row(r, "Materials & supplies")["values"] == [3_000.0, 3_000.0, 3_000.0, 9_000.0]


def test_one_time_treatment_is_explicit_and_overridable():
    """The PI can override in either direction — nothing about this is implicit."""
    repeated = _multi(one_time_categories=[])
    assert _row(repeated, "Equipment")["values"] == [60_000.0, 60_000.0, 60_000.0, 180_000.0]
    once = _multi(travel=5_000, one_time_categories=["equipment", "travel"])
    assert _row(once, "Travel")["values"] == [5_000.0, 0.0, 0.0, 5_000.0]


def test_multi_year_reports_which_categories_were_treated_as_one_time():
    """Silence is what made this a bug. The treatment must be inspectable."""
    r = _multi()
    assert r["multi_year"]["one_time_categories"] == ["equipment"]


def test_one_time_treatment_is_surfaced_as_an_advisory():
    r = _multi()
    msgs = " ".join(a["message"] for a in r["advisories"])
    assert "equipment" in msgs.lower()
    assert "year 1" in msgs.lower()


def test_single_year_ignores_one_time_entirely():
    r = compute_budget({"equipment": 60_000, "one_time_categories": ["equipment"]})
    assert r["equipment"] == 60_000.0
    assert "multi_year" not in r


# ── BUG 2 (DANGEROUS): the justification stated two contradictory totals ────

def test_justification_states_one_total_and_it_is_cumulative():
    """Before: 'cumulative project cost across all 3 years is $352,144' followed
    two paragraphs later by 'for a total project cost of $116,056' — Year 1
    wearing the label 'total project cost'. A PI pasting that understates their
    request by two thirds."""
    r = _multi()
    text = draft_justification(r)
    cumulative = r["multi_year"]["cumulative"]["total"]
    year_one = r["multi_year"]["years"][0]["total"]

    m = re.search(r"for a total project cost of \$([\d,]+)", text)
    assert m, "the closing total sentence is missing"
    stated = float(m.group(1).replace(",", ""))
    assert stated == round(cumulative), f"closing total {stated} is not the cumulative {cumulative}"
    assert stated != round(year_one)


def test_justification_labels_the_year_one_figures_as_year_one():
    """Year-1 direct/F&A figures may still appear — they just may not be called
    the project total."""
    text = draft_justification(_multi())
    assert "Year 1" in text


def test_single_year_justification_total_is_unchanged():
    r = compute_budget({"people": [{"name": "PI", "base_salary": 100_000, "effort_pct": 50}],
                        "equipment": 10_000})
    text = draft_justification(r)
    assert f"{r['total']:,.0f}" in text


# ── BUG 3 (DANGEROUS): the CSV labelled Year 1 as the project total ─────────

def test_csv_does_not_call_year_one_the_project_total():
    """The CSV is the artifact most likely to be forwarded to ORA. It said
    'TOTAL PROJECT COST,,116056.00' while its own appendix said
    'Cumulative total,,352143.60'."""
    r = _multi()
    # csv.writer emits \r\n, so normalise before matching with $.
    csv = budget_to_csv(r).replace("\r\n", "\n")
    year_one = r["multi_year"]["years"][0]["total"]
    m = re.search(r"^TOTAL PROJECT COST,[^,]*,([\d.]+)$", csv, re.MULTILINE)
    assert m, "TOTAL PROJECT COST row missing"
    assert float(m.group(1)) != round(year_one, 2)
    assert float(m.group(1)) == round(r["multi_year"]["cumulative"]["total"], 2)
    # ...and the Year-1 figures are still present, just labelled honestly.
    assert re.search(r"^Year 1 total,,([\d.]+)$", csv, re.MULTILINE)


def test_csv_still_shows_the_year_one_column_labelled_honestly():
    csv = budget_to_csv(_multi())
    assert "Year 1" in csv


def test_single_year_csv_total_is_unchanged():
    r = compute_budget({"equipment": 10_000})
    assert f"TOTAL PROJECT COST,,{r['total']:.2f}" in budget_to_csv(r)


# ── BUG 4: trim suggestions were dead for multi-year proposals ──────────────

def test_trim_suggestions_fire_when_over_a_multi_year_cap():
    """`cap: None` was passed into the per-year computation, so cap_status was
    always 'none' and the 'ideas to get under the cap' panel never rendered —
    i.e. never rendered when it mattered."""
    r = _multi(cap=200_000)
    assert r["multi_year"]["cap_status"] == "over"
    assert r["trim_suggestions"], "no trim suggestions despite being over the cap"


def test_trim_suggestions_stay_empty_when_under_a_multi_year_cap():
    r = _multi(cap=10_000_000)
    assert r["multi_year"]["cap_status"] == "ok"
    assert r["trim_suggestions"] == []


def test_single_year_trim_suggestions_unchanged():
    r = compute_budget({"travel": 50_000, "supplies": 50_000, "cap": 10_000})
    assert r["cap_status"] == "over"
    assert r["trim_suggestions"]


# ── BUG 5: a per-year cap was compared against the cumulative total ─────────

def test_per_year_cap_is_compared_against_each_year_not_the_total():
    """solicitation_extractor is instructed 'If stated per year, return the
    per-year value', but the cap was compared to the 3-year total — telling a
    correctly-budgeted PI to cut two thirds of their project."""
    r = _multi(cap=200_000, cap_basis="per_year")
    my = r["multi_year"]
    assert my["cap_basis"] == "per_year"
    assert my["cap_status"] == "ok", "a compliant per-year budget was reported over cap"


def test_per_year_cap_still_catches_a_year_that_exceeds_it():
    r = _multi(cap=50_000, cap_basis="per_year")
    assert r["multi_year"]["cap_status"] == "over"


def test_cap_basis_defaults_to_total_so_existing_behaviour_is_unchanged():
    r = _multi(cap=200_000)
    assert r["multi_year"]["cap_basis"] == "cumulative"
    assert r["multi_year"]["cap_status"] == "over"


def test_per_year_cap_overage_reports_the_worst_year_not_the_sum():
    r = _multi(cap=50_000, cap_basis="per_year")
    my = r["multi_year"]
    worst = max(y["total"] for y in my["years"])
    assert my["cap_overage"] == round(worst - 50_000, 2)
