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


# ---------------------------------------------------------------------------
# Lines D, E, F, G
# ---------------------------------------------------------------------------

def _settings():
    return nb.blank_document()["settings"]


def test_equipment_total_sums_the_line_items():
    s = nb.blank_sheet()
    s["equipment"] = [{"description": "Confocal", "amount": 60_000},
                      {"description": "Freezer", "amount": 12_000}]
    r = nb.compute_direct_lines(s, _settings(), [])
    assert r["D"]["total"] == 72_000.0


def test_travel_splits_domestic_and_international():
    s = nb.blank_sheet()
    s["travel"]["domestic"] = [{"description": "PI to conf", "amount": 3_000}]
    s["travel"]["international"] = [{"description": "Collab visit", "amount": 4_500}]
    r = nb.compute_direct_lines(s, _settings(), [])
    assert r["E"]["domestic"] == 3_000.0
    assert r["E"]["international"] == 4_500.0
    assert r["E"]["total"] == 7_500.0


def test_participant_support_totals_all_four_sublines():
    s = nb.blank_sheet()
    s["participant_support"] = {"count": 20, "stipends": 10_000, "travel": 4_000,
                                "subsistence": 3_000, "other": 1_000}
    r = nb.compute_direct_lines(s, _settings(), [])
    assert r["F"]["total"] == 18_000.0
    assert r["F"]["count"] == 20


def test_g_total_includes_subawards():
    s = nb.blank_sheet()
    s["other_direct"]["materials_supplies"] = [{"description": "Reagents", "amount": 5_000}]
    s["other_direct"]["subawards"] = [{"organization": "Partner U", "amount": 50_000}]
    r = nb.compute_direct_lines(s, _settings(), [])
    assert r["G"]["subawards"]["total"] == 50_000.0
    assert r["G"]["total"] == 55_000.0


def test_mtdc_exempt_g6_items_are_tallied_separately():
    s = nb.blank_sheet()
    s["other_direct"]["other"] = [
        {"description": "Grad tuition remission", "amount": 40_000, "mtdc_exempt": True},
        {"description": "Lab fees", "amount": 5_000, "mtdc_exempt": False},
    ]
    r = nb.compute_direct_lines(s, _settings(), [])
    assert r["G"]["other"] == 45_000.0          # both are still direct costs
    assert r["mtdc_exempt_total"] == 40_000.0   # but only tuition leaves the F&A base


def test_blank_sheet_computes_all_zeros_without_crashing():
    r = nb.compute_direct_lines(nb.blank_sheet(), _settings(), [])
    assert r["D"]["total"] == 0.0 and r["G"]["total"] == 0.0


# ---------------------------------------------------------------------------
# The rollup: H through M, MTDC, and the cumulative sheet
# ---------------------------------------------------------------------------

def test_line_h_is_the_sum_of_a_through_g():
    s = nb.blank_sheet()
    s["senior"][0].update(base_salary=90_000, appointment_basis="academic_9", acad=2)
    s["equipment"] = [{"description": "Rig", "amount": 40_000}]
    s["travel"]["domestic"] = [{"description": "Conf", "amount": 3_000}]
    c = nb.compute_sheet(s, _settings(), [])
    # A 20,000 + C 8,400 + D 40,000 + E 3,000
    assert c["lines"]["H"] == 71_400.0


def test_line_i_is_fa_on_mtdc_not_on_total_direct():
    s = nb.blank_sheet()
    s["equipment"] = [{"description": "Rig", "amount": 40_000}]
    s["other_direct"]["materials_supplies"] = [{"description": "Reagents", "amount": 10_000}]
    c = nb.compute_sheet(s, _settings(), [])
    assert c["lines"]["H"] == 50_000.0
    assert c["mtdc"]["base"] == 10_000.0          # equipment is out of the base
    assert c["lines"]["I"] == 5_400.0             # 54% of 10k, not of 50k


def test_line_j_is_h_plus_i_and_l_equals_j_without_a_fee():
    s = nb.blank_sheet()
    s["other_direct"]["materials_supplies"] = [{"description": "Reagents", "amount": 10_000}]
    c = nb.compute_sheet(s, _settings(), [])
    assert c["lines"]["J"] == 15_400.0
    assert c["lines"]["L"] == 15_400.0


def test_a_fee_is_subtracted_on_line_l():
    s = nb.blank_sheet()
    s["other_direct"]["materials_supplies"] = [{"description": "Reagents", "amount": 10_000}]
    s["fee"] = 1_000
    c = nb.compute_sheet(s, _settings(), [])
    assert c["lines"]["K"] == 1_000.0
    assert c["lines"]["L"] == 14_400.0            # J - K


def test_cumulative_sums_every_line_across_years():
    doc = nb.blank_document(years=2)
    for y in doc["years"]:
        y["other_direct"]["materials_supplies"] = [{"description": "Reagents", "amount": 10_000}]
    r = nb.compute_document(doc)
    assert r["cumulative"]["lines"]["H"] == 20_000.0
    assert r["cumulative"]["lines"]["I"] == 10_800.0
    assert r["cumulative"]["lines"]["J"] == 30_800.0


def test_worked_example_totals_exactly():
    """Hand-checked end-to-end figure, like the generic $161,556 case.

    A: PI 90,000/9 x 2 acad months            =  20,000
    B: 1 grad student                         =  30,000
    C: 42% of 20,000 + 9% of 30,000           =  11,100
    D: equipment                              =  40,000
    E: 3,000 domestic + 2,000 international   =   5,000
    F: participant support                    =  10,000
    G: 5,000 supplies + 50,000 subaward
       + 25,000 tuition (F&A-exempt)          =  80,000
    H                                         = 196,100
    MTDC = 196,100 - 40,000 - 10,000
           - 25,000 (sub tail) - 25,000 (tuition) = 96,100
    I    = 54% of 96,100                      =  51,894
    J = L                                     = 247,994
    """
    doc = nb.blank_document()
    s = doc["years"][0]
    s["senior"][0].update(base_salary=90_000, appointment_basis="academic_9",
                          acad=2, fringe_key="faculty_ay")
    s["other_personnel"]["grad_students"] = {
        "count": 1, "amount": 30_000, "fringe_key": "contractual"}
    s["equipment"] = [{"description": "Confocal", "amount": 40_000}]
    s["travel"]["domestic"] = [{"description": "Conf", "amount": 3_000}]
    s["travel"]["international"] = [{"description": "Collab", "amount": 2_000}]
    s["participant_support"] = {"count": 15, "stipends": 10_000, "travel": None,
                                "subsistence": None, "other": None}
    s["other_direct"]["materials_supplies"] = [{"description": "Reagents", "amount": 5_000}]
    s["other_direct"]["subawards"] = [{"organization": "Partner U", "amount": 50_000}]
    s["other_direct"]["other"] = [
        {"description": "Grad tuition remission", "amount": 25_000, "mtdc_exempt": True}]

    c = nb.compute_document(doc)["years"][0]
    assert c["lines"]["H"] == 196_100.0
    assert c["mtdc"]["base"] == 96_100.0
    assert c["lines"]["I"] == 51_894.0
    assert c["lines"]["L"] == 247_994.0


def test_cap_over_is_reported_against_the_cumulative_total():
    doc = nb.blank_document()
    doc["settings"]["cap"] = 10_000
    doc["years"][0]["other_direct"]["materials_supplies"] = [
        {"description": "Reagents", "amount": 10_000}]
    r = nb.compute_document(doc)
    assert r["cap"]["status"] == "over"
    assert r["cap"]["overage"] == 5_400.0          # 15,400 total vs a 10,000 cap
