"""Every NSF rule gets one test that fires it and one that does not.

Rules WARN, they never block: nothing here may prevent a compute from
returning. Each rule carries its PAPPG 24-1 citation.

Design: docs/superpowers/specs/2026-09-03-nsf-form-1030-budget-design.md
"""

from services import nsf_budget as nb
from services import nsf_budget_rules as rules


def _flags(doc):
    return rules.evaluate(doc, nb.compute_document(doc))


def _ids(doc):
    return {f["id"] for f in _flags(doc)}


def _doc_with_senior(**over):
    doc = nb.blank_document()
    doc["years"][0]["senior"][0].update(over)
    return doc


# --- A: the two-month cap -------------------------------------------------

def test_three_summer_months_fires_the_two_month_cap():
    doc = _doc_with_senior(base_salary=90_000, appointment_basis="academic_9", sumr=3)
    assert "nsf.senior.two_month_cap" in _ids(doc)


def test_two_summer_months_does_not_fire_the_cap():
    doc = _doc_with_senior(base_salary=90_000, appointment_basis="academic_9", sumr=2)
    assert "nsf.senior.two_month_cap" not in _ids(doc)


def test_the_two_month_flag_says_it_cannot_see_other_nsf_awards():
    doc = _doc_with_senior(base_salary=90_000, appointment_basis="academic_9", sumr=3)
    flag = next(f for f in _flags(doc) if f["id"] == "nsf.senior.two_month_cap")
    assert "other NSF" in flag["message"]
    assert flag["citation"].startswith("PAPPG 24-1 II.D.2.f(i)(a)")


# --- A: appointment-basis mismatch ---------------------------------------

def test_calendar_months_on_a_nine_month_appointment_warns():
    doc = _doc_with_senior(base_salary=90_000, appointment_basis="academic_9", cal=1)
    assert "nsf.senior.basis_mismatch" in _ids(doc)


def test_academic_months_on_a_nine_month_appointment_is_fine():
    doc = _doc_with_senior(base_salary=90_000, appointment_basis="academic_9", acad=1)
    assert "nsf.senior.basis_mismatch" not in _ids(doc)


def test_academic_months_on_a_twelve_month_appointment_warns():
    doc = _doc_with_senior(base_salary=120_000, appointment_basis="calendar_12", acad=1)
    assert "nsf.senior.basis_mismatch" in _ids(doc)


# --- A/B: incomplete rows -------------------------------------------------

def test_months_without_a_base_salary_warns():
    doc = _doc_with_senior(base_salary=None, acad=2)
    assert "nsf.personnel.incomplete_row" in _ids(doc)


def test_base_salary_without_months_warns():
    doc = _doc_with_senior(base_salary=90_000, acad=0, cal=0, sumr=0)
    assert "nsf.personnel.incomplete_row" in _ids(doc)


def test_a_complete_senior_row_does_not_warn():
    doc = _doc_with_senior(base_salary=90_000, appointment_basis="academic_9", acad=2)
    assert "nsf.personnel.incomplete_row" not in _ids(doc)


# --- nothing blocks -------------------------------------------------------

def test_a_violating_budget_still_computes():
    doc = _doc_with_senior(base_salary=90_000, appointment_basis="academic_9", sumr=6)
    out = nb.compute_document(doc)
    assert out["years"][0]["lines"]["L"] > 0        # a warning never stops the math


# --- D: equipment ---------------------------------------------------------

def test_equipment_under_the_capitalization_level_warns():
    doc = nb.blank_document()
    doc["years"][0]["equipment"] = [{"description": "Laptop", "amount": 2_500}]
    assert "nsf.equipment.below_capitalization" in _ids(doc)


def test_equipment_above_the_capitalization_level_does_not_warn():
    doc = nb.blank_document()
    doc["years"][0]["equipment"] = [{"description": "Confocal", "amount": 60_000}]
    assert "nsf.equipment.below_capitalization" not in _ids(doc)


def test_the_capitalization_level_is_configurable():
    doc = nb.blank_document()
    doc["settings"]["capitalization_level"] = 1_000
    doc["years"][0]["equipment"] = [{"description": "Laptop", "amount": 2_500}]
    assert "nsf.equipment.below_capitalization" not in _ids(doc)


def test_equipment_over_10k_with_no_description_warns():
    doc = nb.blank_document()
    doc["years"][0]["equipment"] = [{"description": "", "amount": 60_000}]
    assert "nsf.equipment.unitemised" in _ids(doc)


def test_described_equipment_over_10k_does_not_warn():
    doc = nb.blank_document()
    doc["years"][0]["equipment"] = [{"description": "Confocal microscope", "amount": 60_000}]
    assert "nsf.equipment.unitemised" not in _ids(doc)


# --- E: travel ------------------------------------------------------------

def test_international_travel_raises_an_info_flag():
    doc = nb.blank_document()
    doc["years"][0]["travel"]["international"] = [{"description": "Collab", "amount": 4_000}]
    flags = {f["id"]: f for f in _flags(doc)}
    assert flags["nsf.travel.international"]["severity"] == "info"


def test_domestic_only_travel_raises_no_travel_flag():
    doc = nb.blank_document()
    doc["years"][0]["travel"]["domestic"] = [{"description": "Conf", "amount": 3_000}]
    assert "nsf.travel.international" not in _ids(doc)


# --- F: participant support ----------------------------------------------

def test_participant_dollars_with_a_zero_count_warns():
    doc = nb.blank_document()
    doc["years"][0]["participant_support"] = {
        "count": 0, "stipends": 10_000, "travel": None,
        "subsistence": None, "other": None}
    assert "nsf.participants.no_count" in _ids(doc)


def test_participant_dollars_with_a_count_does_not_warn():
    doc = nb.blank_document()
    doc["years"][0]["participant_support"] = {
        "count": 20, "stipends": 10_000, "travel": None,
        "subsistence": None, "other": None}
    assert "nsf.participants.no_count" not in _ids(doc)


def test_participant_support_raises_the_employees_info_flag():
    doc = nb.blank_document()
    doc["years"][0]["participant_support"] = {
        "count": 20, "stipends": 10_000, "travel": None,
        "subsistence": None, "other": None}
    assert "nsf.participants.not_employees" in _ids(doc)


# --- G.5: subawards -------------------------------------------------------

def test_a_subaward_raises_the_separate_budget_info_flag():
    doc = nb.blank_document()
    doc["years"][0]["other_direct"]["subawards"] = [
        {"organization": "Partner U", "amount": 20_000}]
    assert "nsf.subaward.separate_budget" in _ids(doc)


def test_a_subaward_over_25k_reports_the_excluded_amount():
    doc = nb.blank_document()
    doc["years"][0]["other_direct"]["subawards"] = [
        {"organization": "Partner U", "amount": 50_000}]
    flag = next(f for f in _flags(doc) if f["id"] == "nsf.subaward.over_25k")
    assert "25,000" in flag["detail"]


def test_a_subaward_under_25k_does_not_report_an_exclusion():
    doc = nb.blank_document()
    doc["years"][0]["other_direct"]["subawards"] = [
        {"organization": "Partner U", "amount": 20_000}]
    assert "nsf.subaward.over_25k" not in _ids(doc)


# --- I: indirect costs ----------------------------------------------------

def test_an_fa_rate_below_the_negotiated_rate_warns():
    doc = nb.blank_document()
    doc["settings"]["fa_rate_override"] = 0.20
    assert "nsf.fa.below_negotiated" in _ids(doc)


def test_the_full_negotiated_rate_does_not_warn():
    doc = nb.blank_document()          # 54% organized research, the negotiated rate
    assert "nsf.fa.below_negotiated" not in _ids(doc)


def test_the_below_rate_flag_says_it_is_a_cost_sharing_violation():
    doc = nb.blank_document()
    doc["settings"]["fa_rate_override"] = 0.20
    flag = next(f for f in _flags(doc) if f["id"] == "nsf.fa.below_negotiated")
    assert "cost sharing" in flag["message"].lower()


# --- K: fee ---------------------------------------------------------------

def test_a_fee_on_a_standard_proposal_warns():
    doc = nb.blank_document()
    doc["years"][0]["fee"] = 5_000
    assert "nsf.fee.restricted" in _ids(doc)


def test_a_fee_on_an_sbir_proposal_does_not_warn():
    doc = nb.blank_document()
    doc["meta"]["sponsor_program"] = "sbir_sttr"
    doc["years"][0]["fee"] = 5_000
    assert "nsf.fee.restricted" not in _ids(doc)


# --- M: cost sharing ------------------------------------------------------

def test_voluntary_cost_sharing_warns():
    doc = nb.blank_document()
    doc["years"][0]["cost_sharing"] = {"proposed": 20_000, "agreed": None}
    assert "nsf.cost_sharing.voluntary" in _ids(doc)


def test_mandated_cost_sharing_does_not_warn():
    doc = nb.blank_document()
    doc["meta"]["mandatory_cost_sharing"] = True
    doc["years"][0]["cost_sharing"] = {"proposed": 20_000, "agreed": None}
    assert "nsf.cost_sharing.voluntary" not in _ids(doc)


# --- proposal scope -------------------------------------------------------

def test_fewer_year_sheets_than_the_duration_warns():
    doc = nb.blank_document(years=1)
    doc["meta"]["duration_months"] = 36
    assert "nsf.structure.missing_years" in _ids(doc)


def test_matching_years_and_duration_does_not_warn():
    doc = nb.blank_document(years=3)
    doc["meta"]["duration_months"] = 36
    assert "nsf.structure.missing_years" not in _ids(doc)


def test_exceeding_the_cap_warns():
    doc = nb.blank_document()
    doc["settings"]["cap"] = 10_000
    doc["years"][0]["other_direct"]["materials_supplies"] = [
        {"description": "Reagents", "amount": 10_000}]
    assert "nsf.cap.exceeded" in _ids(doc)


def test_the_five_page_justification_note_is_always_present():
    assert "nsf.justification.five_pages" in _ids(nb.blank_document())


# --- wiring ---------------------------------------------------------------

def test_compute_document_attaches_flags():
    doc = nb.blank_document()
    doc["years"][0]["fee"] = 5_000
    out = nb.compute_document(doc)
    assert any(f["id"] == "nsf.fee.restricted" for f in out["flags"])


def test_year_scoped_flags_are_attached_to_their_year():
    doc = nb.blank_document(years=2)
    doc["years"][1]["fee"] = 5_000
    out = nb.compute_document(doc)
    assert [f["id"] for f in out["years"][0]["flags"]].count("nsf.fee.restricted") == 0
    assert [f["id"] for f in out["years"][1]["flags"]].count("nsf.fee.restricted") == 1
