"""Unit tests for the Phase 4 task guidance catalog (keyword-matched how-to)."""
from services.task_guidance import guidance_for


def test_budget_justification_has_how_to_and_sample():
    g = guidance_for("Write the budget justification")
    assert g and g["how_to"] and g["sample"]


def test_build_budget_matches_budget_rule_not_justification():
    g = guidance_for("Build the proposal budget")
    assert g and "Build budget" in g["how_to"]
    assert "sample" not in g          # the plain-budget rule has no sample


def test_data_management_plan_guidance():
    g = guidance_for("Draft the Data Management Plan (2 pages max)")
    assert g and "repositor" in g["how_to"].lower() or "share" in g["how_to"].lower()


def test_biosketch_guidance():
    assert guidance_for("Collect biosketches for all senior personnel")["how_to"]


def test_current_and_pending_needs_both_keywords():
    assert guidance_for("Draft Current & Pending Support for each senior person") is not None


def test_unknown_task_has_no_guidance():
    assert guidance_for("Buy more coffee for the lab") is None
    assert guidance_for("") is None
