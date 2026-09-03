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
