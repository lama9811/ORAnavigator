"""Tests for the Budget Helper deterministic math core (2026-06-09).

The whole point of the Budget Helper is that the NUMBERS are computed by code,
not the LLM. These tests pin the federal budget rules that trip PIs up:

  * fringe applied per employee category
  * F&A (indirect) applied to the MODIFIED total direct costs (MTDC), i.e. with
    equipment, participant support, and each subaward's amount over $25k EXCLUDED
  * F&A rate selection (Organized Research / Instruction / off-campus, by FY)
  * sponsor cap ok/over

Rates are the real Morgan State figures (KB: pre_award_fanda_cost_rates /
pre_award_fringe_benefit_rate).
"""

from services.budget_helper import compute_budget


# ---------------------------------------------------------------------------
# Personnel: salary = base * effort%, fringe by category
# ---------------------------------------------------------------------------

def test_salary_is_base_times_effort():
    r = compute_budget({"people": [{"name": "Dr. X", "base_salary": 100_000, "effort_pct": 50}]})
    assert r["personnel"][0]["salary"] == 50_000.0


def test_faculty_ay_fringe_is_42_percent():
    r = compute_budget({"people": [{"base_salary": 100_000, "effort_pct": 50, "fringe": "faculty_ay"}]})
    assert r["personnel"][0]["fringe"] == 21_000.0          # 42% of 50k
    assert r["personnel"][0]["subtotal"] == 71_000.0


def test_summer_and_contractual_fringe_is_9_percent():
    summer = compute_budget({"people": [{"base_salary": 10_000, "effort_pct": 100, "fringe": "faculty_summer"}]})
    assert summer["personnel"][0]["fringe"] == 900.0        # 9%
    contractual = compute_budget({"people": [{"base_salary": 10_000, "effort_pct": 100, "fringe": "contractual"}]})
    assert contractual["personnel"][0]["fringe"] == 900.0


def test_zero_effort_yields_zero_salary_and_fringe():
    r = compute_budget({"people": [{"base_salary": 100_000, "effort_pct": 0}]})
    assert r["personnel"][0]["salary"] == 0.0
    assert r["personnel"][0]["fringe"] == 0.0


def test_multiple_people_sum_into_personnel_total():
    r = compute_budget({"people": [
        {"base_salary": 100_000, "effort_pct": 50, "fringe": "faculty_ay"},   # 50k + 21k
        {"base_salary": 60_000, "effort_pct": 100, "fringe": "full_time"},    # 60k + 25.2k
    ]})
    assert r["personnel_total"] == 71_000.0 + 85_200.0


# ---------------------------------------------------------------------------
# MTDC exclusions — the load-bearing rules
# ---------------------------------------------------------------------------

def test_equipment_is_excluded_from_mtdc_but_in_direct_costs():
    r = compute_budget({"supplies": 5_000, "equipment": 40_000})
    assert r["direct_costs"] == 45_000.0       # equipment IS a direct cost
    assert r["mtdc_base"] == 5_000.0           # but NOT in the F&A base


def test_participant_support_is_excluded_from_mtdc():
    r = compute_budget({"supplies": 5_000, "participant_support": 8_000})
    assert r["direct_costs"] == 13_000.0
    assert r["mtdc_base"] == 5_000.0


def test_only_first_25k_of_a_subaward_is_in_mtdc():
    r = compute_budget({"subawards": [40_000]})
    assert r["direct_costs"] == 40_000.0       # full subaward is a direct cost
    assert r["mtdc_base"] == 25_000.0          # only first $25k counts toward F&A
    assert r["mtdc_exclusions"]["subaward_over_25k"] == 15_000.0


def test_subaward_under_25k_fully_counts_and_each_subaward_is_independent():
    r = compute_budget({"subawards": [40_000, 20_000]})
    # 40k -> 15k excluded; 20k -> 0 excluded
    assert r["mtdc_exclusions"]["subaward_over_25k"] == 15_000.0
    assert r["subawards_total"] == 60_000.0


# ---------------------------------------------------------------------------
# F&A rate selection
# ---------------------------------------------------------------------------

def test_default_fa_rate_is_organized_research_on_campus_54():
    r = compute_budget({"supplies": 100_000})
    assert r["fa_rate"] == 0.54
    assert r["fa_amount"] == 54_000.0
    assert "Organized Research" in r["fa_rate_label"]


def test_off_campus_rate_is_26():
    r = compute_budget({"supplies": 100_000, "fa_rate_key": "off_campus"})
    assert r["fa_rate"] == 0.26
    assert r["fa_amount"] == 26_000.0


def test_instruction_rate_is_64():
    r = compute_budget({"supplies": 100_000, "fa_rate_key": "instruction_on_campus"})
    assert r["fa_amount"] == 64_000.0


def test_prior_fiscal_year_organized_research_is_53():
    r = compute_budget({"supplies": 100_000, "fa_year": "fy_2024_2025"})
    assert r["fa_rate"] == 0.53


# ---------------------------------------------------------------------------
# Full worked example (end-to-end, hand-verified)
# ---------------------------------------------------------------------------

def test_full_worked_example_total():
    r = compute_budget({
        "people": [{"name": "Dr. Smith", "base_salary": 80_000, "effort_pct": 25, "fringe": "faculty_ay"}],
        "equipment": 40_000,
        "travel": 3_000,
        "supplies": 5_000,
        "participant_support": 2_000,
        "subawards": [50_000],
        "fa_rate_key": "organized_research_on_campus",
        "fa_year": "fy_2025_2026",
    })
    # personnel: 20,000 salary + 8,400 fringe = 28,400
    assert r["personnel_total"] == 28_400.0
    # TDC = 28,400 + 40,000 + 3,000 + 5,000 + 2,000 + 50,000 = 128,400
    assert r["direct_costs"] == 128_400.0
    # MTDC = 128,400 - 40,000(equip) - 2,000(participant) - 25,000(sub>25k) = 61,400
    assert r["mtdc_base"] == 61_400.0
    # F&A = 61,400 * 0.54 = 33,156
    assert r["fa_amount"] == 33_156.0
    # TOTAL = 128,400 + 33,156 = 161,556
    assert r["total"] == 161_556.0


# ---------------------------------------------------------------------------
# Cap check
# ---------------------------------------------------------------------------

def test_cap_ok_when_total_under_cap():
    r = compute_budget({"supplies": 10_000, "cap": 100_000})
    assert r["cap_status"] == "ok"
    assert r["cap_overage"] == 0.0


def test_cap_over_reports_overage():
    r = compute_budget({"supplies": 100_000, "cap": 120_000})  # total 154,000
    assert r["cap_status"] == "over"
    assert r["cap_overage"] == 34_000.0


def test_no_cap_set_is_status_none():
    r = compute_budget({"supplies": 10_000})
    assert r["cap_status"] == "none"


# ---------------------------------------------------------------------------
# Robustness — never crash on junk input
# ---------------------------------------------------------------------------

def test_empty_input_is_all_zeros():
    r = compute_budget({})
    assert r["direct_costs"] == 0.0
    assert r["total"] == 0.0
    assert r["cap_status"] == "none"


def test_unknown_fringe_key_falls_back_and_warns():
    r = compute_budget({"people": [{"base_salary": 100_000, "effort_pct": 100, "fringe": "bogus"}]})
    assert any("fringe" in w.lower() for w in r["warnings"])
    # still produced a number (default fringe applied), didn't crash
    assert r["personnel"][0]["salary"] == 100_000.0


def test_negative_amounts_coerced_to_zero_with_warning():
    r = compute_budget({"supplies": -5_000})
    assert r["supplies"] == 0.0
    assert r["warnings"]


def test_effort_over_100_is_clamped():
    r = compute_budget({"people": [{"base_salary": 100_000, "effort_pct": 150}]})
    assert r["personnel"][0]["salary"] == 100_000.0        # clamped to 100%
    assert r["warnings"]


# ---------------------------------------------------------------------------
# Phase 1: Budget Coaching -- advisories + trim suggestions (additive, advisory).
# These NEVER change a computed number; they only coach. A clean budget => [].
# ---------------------------------------------------------------------------

def test_clean_budget_has_no_advisories():
    r = compute_budget({
        "people": [{"name": "PI", "base_salary": 100_000, "effort_pct": 20}],
        "supplies": 5_000, "travel": 3_000,
    })
    assert r["advisories"] == []
    assert r["trim_suggestions"] == []


def test_equipment_heavy_flags_advisory():
    r = compute_budget({
        "people": [{"name": "PI", "base_salary": 100_000, "effort_pct": 10}],
        "equipment": 80_000,  # >40% of direct
    })
    fields = [a["field"] for a in r["advisories"]]
    assert "equipment" in fields
    # advisory only -- the math is untouched
    assert r["equipment"] == 80_000.0


def test_travel_heavy_flags_advisory():
    r = compute_budget({"travel": 50_000, "supplies": 10_000})  # travel >25%
    assert any(a["field"] == "travel" for a in r["advisories"])


def test_salary_but_no_effort_flags_advisory():
    r = compute_budget({"people": [{"name": "Maria", "base_salary": 90_000, "effort_pct": 0}],
                        "supplies": 5_000})
    msgs = " ".join(a["message"] for a in r["advisories"])
    assert "Maria" in msgs and "0% effort" in msgs


def test_no_personnel_is_info_advisory():
    r = compute_budget({"supplies": 10_000})
    assert any(a["field"] == "personnel" and a["severity"] == "info" for a in r["advisories"])


def test_subawards_majority_flags_advisory():
    r = compute_budget({"people": [{"base_salary": 50_000, "effort_pct": 10}],
                        "subawards": [60_000]})
    assert any(a["field"] == "subawards" for a in r["advisories"])


def test_no_trim_suggestions_when_under_cap():
    r = compute_budget({"supplies": 10_000, "cap": 100_000})
    assert r["cap_status"] == "ok"
    assert r["trim_suggestions"] == []


def test_trim_suggestions_bring_under_cap():
    r = compute_budget({"travel": 100_000, "cap": 120_000})   # total 154,000; over by 34,000
    assert r["cap_status"] == "over"
    trims = r["trim_suggestions"]
    assert trims and trims[0]["line"] == "Travel"
    # Applying the suggested cuts (each saves cut*(1+F&A)) should reach the cap.
    total_cut = sum(t["reduce_by"] for t in trims)
    projected = r["total"] - round(total_cut * (1 + r["fa_rate"]), 2)
    assert projected <= r["cap"] + 1.0


def test_compute_output_keys_unchanged_plus_new():
    """Regression guard: every pre-existing key still present; new keys added;
    warnings unchanged for the worked example."""
    r = compute_budget({
        "people": [{"name": "Dr. X", "base_salary": 100_000, "effort_pct": 20, "fringe": "faculty_ay"}],
        "equipment": 40_000, "travel": 3_000, "supplies": 5_000,
        "participant_support": 2_000, "subawards": [50_000],
    })
    for key in ("personnel", "personnel_total", "equipment", "travel", "supplies",
                "participant_support", "other", "subawards", "subawards_total",
                "direct_costs", "mtdc_base", "mtdc_exclusions", "fa_year",
                "fa_rate_key", "fa_rate", "fa_rate_label", "fa_amount", "total",
                "cap", "cap_status", "cap_overage", "warnings"):
        assert key in r, f"existing key disappeared: {key}"
    assert r["warnings"] == []          # clean inputs -> no warnings, as before
    assert "advisories" in r and "trim_suggestions" in r
    assert r["total"] == 161_556.0      # math identical to the existing worked example
