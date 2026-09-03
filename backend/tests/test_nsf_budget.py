"""Tests for the NSF Form 1030 budget template (2026-09-03).

Same contract as the generic Budget Helper: every number here is computed by
code. These tests pin the NSF-specific rules -- person-months, per-row fringe
summed into line C, the MTDC exclusions, and the multi-year cumulative.

Design: docs/superpowers/specs/2026-09-03-nsf-form-1030-budget-design.md
"""

from services.budget_helper import _months, mtdc_and_fa


# ---------------------------------------------------------------------------
# The shared MTDC / F&A engine (used by BOTH the generic and NSF templates)
# ---------------------------------------------------------------------------

def test_mtdc_excludes_equipment_participant_and_subaward_over_25k():
    r = mtdc_and_fa(
        direct_total=200_000, equipment=40_000, participant_support=10_000,
        subawards=[50_000],
    )
    # 200k - 40k equipment - 10k participants - 25k (the part of the sub over 25k)
    assert r["mtdc_base"] == 125_000.0
    assert r["exclusions"]["subaward_over_25k"] == 25_000.0


def test_mtdc_counts_each_subaward_separately():
    # Two 20k subawards are fully in MTDC; one 40k sub excludes only its 15k tail.
    r = mtdc_and_fa(direct_total=100_000, equipment=0, participant_support=0,
                    subawards=[20_000, 20_000, 40_000])
    assert r["exclusions"]["subaward_over_25k"] == 15_000.0


def test_extra_exempt_is_removed_from_the_base():
    r = mtdc_and_fa(direct_total=100_000, equipment=0, participant_support=0,
                    subawards=[], extra_exempt=40_000)
    assert r["mtdc_base"] == 60_000.0
    assert r["exclusions"]["mtdc_exempt"] == 40_000.0


def test_fa_amount_uses_the_selected_rate():
    r = mtdc_and_fa(direct_total=100_000, equipment=0, participant_support=0, subawards=[])
    assert r["fa_rate"] == 0.54                      # Organized Research on-campus FY25-26
    assert r["fa_amount"] == 54_000.0


def test_mtdc_never_goes_negative():
    r = mtdc_and_fa(direct_total=10_000, equipment=50_000, participant_support=0, subawards=[])
    assert r["mtdc_base"] == 0.0


def test_months_clamps_to_twelve_with_a_warning():
    w = []
    assert _months(15, w, "calendar months") == 12.0
    assert w


def test_months_coerces_junk_to_zero():
    w = []
    assert _months("abc", w, "academic months") == 0.0
    assert w


# ---------------------------------------------------------------------------
# The blank Form 1030 skeleton
# ---------------------------------------------------------------------------

from services import nsf_budget as nb


def test_blank_document_has_the_schema_discriminator():
    doc = nb.blank_document(years=2)
    assert doc["schema"] == "nsf_1030"
    assert doc["version"] == 1
    assert len(doc["years"]) == 2
    assert [y["year"] for y in doc["years"]] == [1, 2]


def test_blank_sheet_uses_null_placeholders_not_zero():
    # A blank box must be distinguishable from a deliberate zero.
    s = nb.blank_sheet()
    assert s["senior"][0]["base_salary"] is None
    assert s["equipment"][0]["amount"] is None


def test_blank_sheet_has_all_six_other_personnel_rows():
    s = nb.blank_sheet()
    assert set(s["other_personnel"]) == {
        "postdocs", "other_professionals", "grad_students",
        "undergrads", "clerical", "other"}


def test_blank_sheet_has_all_six_g_lines_including_subawards():
    s = nb.blank_sheet()
    assert set(s["other_direct"]) == {
        "materials_supplies", "publication", "consultant",
        "computer_services", "subawards", "other"}


def test_g6_other_items_carry_the_mtdc_exempt_flag():
    s = nb.blank_sheet()
    assert s["other_direct"]["other"][0]["mtdc_exempt"] is False


def test_blank_document_defaults_to_morgan_rates_and_5000_capitalization():
    doc = nb.blank_document()
    assert doc["settings"]["fa_rate_key"] == "organized_research_on_campus"
    assert doc["settings"]["capitalization_level"] == 5000.0
    assert doc["settings"]["escalation_pct"] == 3.0


# ---------------------------------------------------------------------------
# Lines A, B, C — person-months, salaries, per-row fringe
# ---------------------------------------------------------------------------

def _sheet_with_senior(**over):
    s = nb.blank_sheet()
    s["senior"][0].update(over)
    return s


def test_academic_nine_month_salary_is_base_over_nine_per_month():
    s = _sheet_with_senior(base_salary=90_000, appointment_basis="academic_9", sumr=2)
    r = nb.compute_personnel(s, [])
    assert r["A"]["rows"][0]["salary"] == 20_000.0        # 90k/9 = 10k/mo x 2


def test_calendar_twelve_month_salary_is_base_over_twelve_per_month():
    s = _sheet_with_senior(base_salary=120_000, appointment_basis="calendar_12", cal=3)
    r = nb.compute_personnel(s, [])
    assert r["A"]["rows"][0]["salary"] == 30_000.0        # 120k/12 = 10k/mo x 3


def test_months_across_all_three_columns_are_summed():
    s = _sheet_with_senior(base_salary=120_000, appointment_basis="calendar_12",
                           cal=1, acad=1, sumr=1)
    assert nb.compute_personnel(s, [])["A"]["rows"][0]["months_total"] == 3.0


def test_effort_percent_is_derived_from_months():
    s = _sheet_with_senior(base_salary=90_000, appointment_basis="academic_9", acad=4.5)
    assert nb.compute_personnel(s, [])["A"]["rows"][0]["effort_pct"] == 50.0


def test_senior_fringe_uses_the_rows_own_rate():
    s = _sheet_with_senior(base_salary=90_000, appointment_basis="academic_9",
                           acad=2, fringe_key="faculty_ay")
    row = nb.compute_personnel(s, [])["A"]["rows"][0]
    assert row["salary"] == 20_000.0
    assert row["fringe"] == 8_400.0                       # 42% of 20k


def test_line_c_sums_fringe_across_mixed_rate_categories():
    # Faculty at 42% and a grad student at 9% must not share one rate.
    s = _sheet_with_senior(base_salary=90_000, appointment_basis="academic_9",
                           acad=2, fringe_key="faculty_ay")
    s["other_personnel"]["grad_students"] = {
        "count": 1, "amount": 30_000, "fringe_key": "contractual"}
    r = nb.compute_personnel(s, [])
    assert r["C"] == 8_400.0 + 2_700.0                    # 42% of 20k + 9% of 30k


def test_line_b_totals_the_other_personnel_amounts():
    s = nb.blank_sheet()
    s["other_personnel"]["postdocs"]["amount"] = 55_000
    s["other_personnel"]["undergrads"]["amount"] = 5_000
    assert nb.compute_personnel(s, [])["B"]["total"] == 60_000.0


def test_zero_base_salary_yields_zero_not_a_crash():
    s = _sheet_with_senior(base_salary=None, acad=2)
    assert nb.compute_personnel(s, [])["A"]["total"] == 0.0
