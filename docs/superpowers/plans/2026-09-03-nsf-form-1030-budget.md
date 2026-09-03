# NSF Form 1030 Budget Template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add NSF's Summary Proposal Budget (Form 1030, sections A–M) to the Budget Helper as a second schema — a multi-year grid of placeholders where every subtotal, the F&A calculation, and the cumulative rollup are computed by code, with NSF policy warnings and an Excel export.

**Architecture:** One shared MTDC/F&A engine is extracted from the existing `compute_budget()` into `mtdc_and_fa()` and used by both the generic and NSF templates, so the two can never disagree on the number a PI must trust. A new `services/nsf_budget.py` owns the A–M sheet structure and rollup; `services/nsf_budget_rules.py` owns a data-driven rules table; `services/nsf_budget_export.py` builds the workbook. The saved `budget_json` gains a `schema` discriminator — its absence means "generic", so every existing saved budget keeps working untouched.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy backend, pytest; React 19 / Vite frontend; `openpyxl` (new dependency) for the Excel export.

**Spec:** `docs/superpowers/specs/2026-09-03-nsf-form-1030-budget-design.md`

## Global Constraints

- **Every derived number is computed by code.** The LLM writes prose only, and is told never to change a figure. No exceptions.
- **Warnings never block.** No rule may prevent editing, computing, saving, or exporting.
- **Backward compatibility is absolute.** A `budget_json` with no `schema` key must load and compute exactly as before. The 21 existing tests in `backend/tests/test_budget_helper.py` must stay green **without modification** — that is the proof the extraction was behaviour-preserving.
- **No input may crash a compute.** Junk, negative, `None`, and missing keys coerce to `0` plus a warning, following the existing `_money()` / `_effort()` pattern.
- **Schema discriminator:** `"schema": "nsf_1030"`, `"version": 1`.
- **MTDC exclusions** (2 CFR § 200.1): equipment by the capitalization test, all participant support, the portion of **each** subaward over `$25,000`, and any G-line item flagged `mtdc_exempt`.
- **Rate constants:** `DEFAULT_CAPITALIZATION = 5000.0`, `SUBAWARD_MTDC_CAP = 25_000.0`, `DEFAULT_ESCALATION_PCT = 3.0`. F&A and fringe rates come from the existing `FA_RATES` / `FRINGE_RATES` tables — do not redefine them.
- **Appointment bases:** `academic_9` → divide base salary by 9; `calendar_12` → divide by 12.
- **Every rule carries a PAPPG citation string** shown in the UI.
- **Test env prefix** for any suite importing `main`:
  `DATABASE_URL="sqlite:///:memory:" TRUSTED_HOSTS="testserver,localhost,127.0.0.1"`
- **Run tests from** `backend/` with the venv: `cd backend && ../.venv/bin/python -m pytest`
- Commit after every task. Do not push, deploy, or merge without asking.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/services/budget_helper.py` | **Modify.** Keeps generic math; gains the extracted `mtdc_and_fa()` and `_months()`, which it exports for reuse. |
| `backend/services/nsf_budget.py` | **Create.** Sheet skeleton, per-line math, rollup, cumulative, escalation, justification. |
| `backend/services/nsf_budget_rules.py` | **Create.** The rules table plus one predicate per rule, and `evaluate()`. |
| `backend/services/nsf_budget_export.py` | **Create.** openpyxl workbook builder. |
| `backend/main.py` | **Modify.** Four new endpoints; two existing ones become schema-aware. |
| `backend/requirements.txt` | **Modify.** Add `openpyxl`. |
| `backend/tests/test_nsf_budget.py` | **Create.** Math, rollup, cumulative, escalation. |
| `backend/tests/test_nsf_budget_rules.py` | **Create.** One firing and one non-firing test per rule. |
| `backend/tests/test_nsf_budget_export.py` | **Create.** Workbook structure and formulas. |
| `backend/tests/test_budget_api_e2e.py` | **Modify.** New endpoints plus the generic-schema regression guard. |
| `frontend/src/components/NsfBudgetSheet.jsx` | **Create.** The A–M grid, year tabs, flags panel. |
| `frontend/src/components/NsfBudgetSheet.css` | **Create.** Sheet styling. |
| `frontend/src/components/BudgetHelperModal.jsx` | **Modify.** Template selector; delegates to the sheet. |
| `docs/features/budget-helper.md` | **Modify.** Document the NSF template. |

---

## Task 1: Extract the shared MTDC/F&A engine

The single most important refactor in this plan. `compute_budget()` currently computes MTDC and F&A inline. Both templates must use one implementation.

**Files:**
- Modify: `backend/services/budget_helper.py` (the MTDC/F&A block inside `compute_budget`, around lines 145–160)
- Test: `backend/tests/test_nsf_budget.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `mtdc_and_fa(direct_total, equipment, participant_support, subawards, extra_exempt=0.0, fa_year=None, fa_rate_key=None, warnings=None) -> dict` with keys `mtdc_base`, `exclusions` (`equipment`, `participant_support`, `subaward_over_25k`, `mtdc_exempt`), `fa_year`, `fa_rate_key`, `fa_rate`, `fa_rate_label`, `fa_amount`.
  - `_months(v, warnings, field) -> float` clamped to 0–12.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_nsf_budget.py`:

```python
"""Tests for the NSF Form 1030 budget template (2026-09-03).

Same contract as the generic Budget Helper: every number here is computed by
code. These tests pin the NSF-specific rules -- person-months, per-row fringe
summed into line C, the MTDC exclusions, and the multi-year cumulative.
"""
from services.budget_helper import mtdc_and_fa, _months


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -v`
Expected: FAIL — `ImportError: cannot import name 'mtdc_and_fa' from 'services.budget_helper'`

- [ ] **Step 3: Add `_months()` and `mtdc_and_fa()` to `budget_helper.py`**

Add after the existing `_effort()` function:

```python
def _months(v, warnings, field):
    """Coerce person-months to 0-12. Junk -> 0 with a warning."""
    if v in (None, ""):
        return 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        warnings.append(f"Could not read {field} '{v}'; using 0.")
        return 0.0
    if x < 0:
        warnings.append(f"{field.capitalize()} was negative; using 0.")
        return 0.0
    if x > 12:
        warnings.append(f"{field.capitalize()} over 12; clamped to 12.")
        return 12.0
    return x


def mtdc_and_fa(direct_total, equipment, participant_support, subawards,
                extra_exempt=0.0, fa_year=None, fa_rate_key=None, warnings=None):
    """The one MTDC base + F&A calculation, shared by every budget template.

    MTDC (2 CFR 200.1) = total direct costs MINUS equipment, participant
    support, the portion of EACH subaward over $25,000, and any separately
    exempt items (tuition remission, scholarships, rent, patient care).
    """
    warnings = warnings if warnings is not None else []

    year = fa_year or DEFAULT_FA_YEAR
    if year not in FA_RATES:
        warnings.append(f"Unknown F&A year '{year}'; using {DEFAULT_FA_YEAR}.")
        year = DEFAULT_FA_YEAR
    year_rates = FA_RATES[year]
    key = fa_rate_key or DEFAULT_FA_KEY
    if key not in year_rates:
        warnings.append(f"Unknown F&A rate '{key}'; using {DEFAULT_FA_KEY}.")
        key = DEFAULT_FA_KEY
    label, rate = year_rates[key]

    sub_over = round(sum(max(0.0, float(s or 0)) - SUBAWARD_MTDC_CAP
                         for s in (subawards or [])
                         if float(s or 0) > SUBAWARD_MTDC_CAP), 2)
    exempt = round(float(extra_exempt or 0), 2)
    base = round(float(direct_total or 0) - float(equipment or 0)
                 - float(participant_support or 0) - sub_over - exempt, 2)
    if base < 0:
        base = 0.0

    return {
        "mtdc_base": base,
        "exclusions": {
            "equipment": round(float(equipment or 0), 2),
            "participant_support": round(float(participant_support or 0), 2),
            "subaward_over_25k": sub_over,
            "mtdc_exempt": exempt,
        },
        "fa_year": year,
        "fa_rate_key": key,
        "fa_rate": rate,
        "fa_rate_label": label,
        "fa_amount": round(base * rate, 2),
    }
```

- [ ] **Step 4: Rewrite `compute_budget()` to call the shared engine**

Replace the inline MTDC/F&A block in `compute_budget` (the lines computing `sub_over_25k`, `mtdc`, `fa_amount`, and the F&A rate selection near the top) so the function delegates. Delete the now-duplicated rate-selection lines at the top of `compute_budget` and use the returned values:

```python
    eng = mtdc_and_fa(
        direct_total=direct, equipment=equipment,
        participant_support=participant, subawards=subawards,
        fa_year=inputs.get("fa_year"), fa_rate_key=inputs.get("fa_rate_key"),
        warnings=warnings,
    )
    mtdc = eng["mtdc_base"]
    fa_rate, fa_label = eng["fa_rate"], eng["fa_rate_label"]
    fa_amount = eng["fa_amount"]
    year, fa_key = eng["fa_year"], eng["fa_rate_key"]
    total = round(direct + fa_amount, 2)
```

The returned dict's `mtdc_exclusions` key keeps its existing three sub-keys so the generic API response shape does not change:

```python
        "mtdc_exclusions": {
            "equipment": eng["exclusions"]["equipment"],
            "participant_support": eng["exclusions"]["participant_support"],
            "subaward_over_25k": eng["exclusions"]["subaward_over_25k"],
        },
```

- [ ] **Step 5: Run the new tests AND the whole existing budget suite**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py tests/test_budget_helper.py -v`
Expected: all PASS. **`test_budget_helper.py` must pass with zero edits to that file.** If any of its 21 tests fail, the extraction changed behaviour — fix `mtdc_and_fa`, not the test.

- [ ] **Step 6: Commit**

```bash
git add backend/services/budget_helper.py backend/tests/test_nsf_budget.py
git commit -m "refactor(budget): extract shared mtdc_and_fa engine + _months coercion"
```

---

## Task 2: The blank NSF sheet skeleton

**Files:**
- Create: `backend/services/nsf_budget.py`
- Test: `backend/tests/test_nsf_budget.py` (append)

**Interfaces:**
- Consumes: `budget_helper._money`, `_months`, `mtdc_and_fa`, `FRINGE_RATES`.
- Produces:
  - `SCHEMA = "nsf_1030"`, `VERSION = 1`, `DEFAULT_CAPITALIZATION = 5000.0`, `DEFAULT_ESCALATION_PCT = 3.0`
  - `OTHER_PERSONNEL_ROWS: list[tuple[key, label, has_months]]`
  - `G_ITEM_LINES: list[tuple[key, label]]`
  - `blank_sheet(year: int = 1) -> dict`
  - `blank_document(years: int = 1, **meta) -> dict`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_nsf_budget.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -k blank -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.nsf_budget'`

- [ ] **Step 3: Create `backend/services/nsf_budget.py`**

```python
"""NSF Form 1030 (Summary Proposal Budget) template — deterministic math.

Sections A-M of the form NSF proposals are actually submitted on, one sheet
per year of support plus a computed cumulative sheet. EVERY derived number
here is computed by code; the LLM only writes the justification prose.

The F&A / MTDC engine is imported from budget_helper so this template and the
generic one can never disagree about the number a PI relies on.

Source: NSF PAPPG 24-1 Chapter II.D.2.f; 2 CFR 200.1.
Design: docs/superpowers/specs/2026-09-03-nsf-form-1030-budget-design.md
"""
from __future__ import annotations

from services.budget_helper import (
    DEFAULT_FA_KEY, DEFAULT_FA_YEAR, FRINGE_RATES, SUBAWARD_MTDC_CAP,
    _money, _months, mtdc_and_fa,
)

SCHEMA = "nsf_1030"
VERSION = 1
DEFAULT_CAPITALIZATION = 5000.0     # PAPPG: lesser of org capitalization or $5,000
FORM_ITEMISE_THRESHOLD = 10_000.0   # Form 1030 line D asks to itemise above this
DEFAULT_ESCALATION_PCT = 3.0
MAX_SENIOR_MONTHS = 2.0             # PAPPG II.D.2.f(i)(a)

MONTHS_PER_BASIS = {"academic_9": 9.0, "calendar_12": 12.0}

# (key, form label, whether the form shows person-months for this row)
OTHER_PERSONNEL_ROWS = [
    ("postdocs", "Postdoctoral Scholars", True),
    ("other_professionals", "Other Professionals (Technician, Programmer, etc.)", True),
    ("grad_students", "Graduate Students", False),
    ("undergrads", "Undergraduate Students", False),
    ("clerical", "Secretarial - Clerical (if charged directly)", False),
    ("other", "Other", False),
]

# G.1-G.4 are itemised lists; G.5 subawards and G.6 other are handled separately.
G_ITEM_LINES = [
    ("materials_supplies", "Materials and Supplies"),
    ("publication", "Publication Costs/Documentation/Dissemination"),
    ("consultant", "Consultant Services"),
    ("computer_services", "Computer Services"),
]

_DEFAULT_FRINGE = {
    "postdocs": "full_time", "other_professionals": "full_time",
    "grad_students": "contractual", "undergrads": "contractual",
    "clerical": "full_time", "other": "contractual",
}


def _blank_item():
    return {"description": "", "amount": None}


def blank_sheet(year: int = 1) -> dict:
    """One empty A-M year sheet. `None` amounts are placeholders, not zeros."""
    other_personnel = {}
    for key, _label, has_months in OTHER_PERSONNEL_ROWS:
        row = {"count": 0, "amount": None, "fringe_key": _DEFAULT_FRINGE[key]}
        if has_months:
            row["months"] = 0
        other_personnel[key] = row

    return {
        "year": year,
        "senior": [{
            "name": "", "role": "PI", "appointment_basis": "academic_9",
            "base_salary": None, "cal": 0, "acad": 0, "sumr": 0,
            "fringe_key": "faculty_ay",
        }],
        "other_personnel": other_personnel,
        "equipment": [_blank_item()],
        "travel": {"domestic": [_blank_item()], "international": [_blank_item()]},
        "participant_support": {
            "count": 0, "stipends": None, "travel": None,
            "subsistence": None, "other": None,
        },
        "other_direct": {
            **{key: [_blank_item()] for key, _ in G_ITEM_LINES},
            "subawards": [{"organization": "", "amount": None}],
            "other": [{"description": "", "amount": None, "mtdc_exempt": False}],
        },
        "fee": 0,
        "cost_sharing": {"proposed": 0, "agreed": None},
    }


def blank_document(years: int = 1, **meta) -> dict:
    """A fresh NSF budget document with `years` empty sheets."""
    years = max(1, min(int(years or 1), 10))
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "meta": {
            "organization": meta.get("organization", "Morgan State University"),
            "pi_name": meta.get("pi_name", ""),
            "duration_months": meta.get("duration_months", years * 12),
            "sponsor_program": meta.get("sponsor_program", "standard"),
            "mandatory_cost_sharing": meta.get("mandatory_cost_sharing", False),
        },
        "settings": {
            "fa_year": DEFAULT_FA_YEAR,
            "fa_rate_key": DEFAULT_FA_KEY,
            "escalation_pct": DEFAULT_ESCALATION_PCT,
            "capitalization_level": DEFAULT_CAPITALIZATION,
        },
        "years": [blank_sheet(i + 1) for i in range(years)],
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/nsf_budget.py backend/tests/test_nsf_budget.py
git commit -m "feat(nsf-budget): blank Form 1030 sheet + document skeleton"
```

---

## Task 3: Lines A, B, C — person-months, salaries, per-row fringe

**Files:**
- Modify: `backend/services/nsf_budget.py`
- Test: `backend/tests/test_nsf_budget.py` (append)

**Interfaces:**
- Consumes: `blank_sheet`, `MONTHS_PER_BASIS`, `FRINGE_RATES`, `_money`, `_months`.
- Produces: `compute_personnel(sheet, warnings) -> dict` with keys `A` (`{"rows": [...], "total": float}`), `B` (same shape), `C` (float), and `fringe_rows` (the per-row breakdown the justification needs). Each A row carries `name`, `role`, `appointment_basis`, `base_salary`, `cal`, `acad`, `sumr`, `months_total`, `monthly_rate`, `effort_pct`, `salary`, `fringe_key`, `fringe_label`, `fringe_rate`, `fringe`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -k personnel -v`
Expected: FAIL — `AttributeError: module 'services.nsf_budget' has no attribute 'compute_personnel'`

- [ ] **Step 3: Implement `compute_personnel`**

Append to `nsf_budget.py`:

```python
def _fringe_for(key, warnings):
    """Resolve a fringe category to (key, label, rate), defaulting safely."""
    if key not in FRINGE_RATES:
        if key:
            warnings.append(f"Unknown fringe category '{key}'; using faculty_ay.")
        key = "faculty_ay"
    label, rate = FRINGE_RATES[key]
    return key, label, rate


def compute_personnel(sheet, warnings):
    """Lines A, B and C.

    Salary comes from person-months: monthly rate is the base salary divided by
    9 (academic appointment) or 12 (calendar). Fringe is computed per row at
    that row's own category rate, and line C is their sum -- the form shows one
    number, but Morgan's rates differ by category (42% vs 9%).
    """
    a_rows, fringe_rows = [], []

    for p in sheet.get("senior") or []:
        p = p or {}
        basis = p.get("appointment_basis") or "academic_9"
        if basis not in MONTHS_PER_BASIS:
            warnings.append(f"Unknown appointment basis '{basis}'; using academic_9.")
            basis = "academic_9"
        divisor = MONTHS_PER_BASIS[basis]

        base = _money(p.get("base_salary"), warnings, "base salary")
        cal = _months(p.get("cal"), warnings, "calendar months")
        acad = _months(p.get("acad"), warnings, "academic months")
        sumr = _months(p.get("sumr"), warnings, "summer months")
        months = round(cal + acad + sumr, 2)

        monthly = round(base / divisor, 2) if base else 0.0
        salary = round(monthly * months, 2)
        fkey, flabel, frate = _fringe_for(p.get("fringe_key"), warnings)
        fringe = round(salary * frate, 2)

        row = {
            "name": (p.get("name") or "").strip() or "Unnamed",
            "role": (p.get("role") or "").strip(),
            "appointment_basis": basis, "base_salary": base,
            "cal": cal, "acad": acad, "sumr": sumr, "months_total": months,
            "monthly_rate": monthly,
            "effort_pct": round(months / divisor * 100.0, 2) if divisor else 0.0,
            "salary": salary,
            "fringe_key": fkey, "fringe_label": flabel,
            "fringe_rate": frate, "fringe": fringe,
        }
        a_rows.append(row)
        fringe_rows.append({"label": row["name"], "rate": frate, "amount": fringe})

    b_rows = []
    for key, label, has_months in OTHER_PERSONNEL_ROWS:
        raw = (sheet.get("other_personnel") or {}).get(key) or {}
        amount = _money(raw.get("amount"), warnings, label.lower())
        fkey, flabel, frate = _fringe_for(raw.get("fringe_key"), warnings)
        fringe = round(amount * frate, 2)
        row = {
            "key": key, "label": label,
            "count": int(raw.get("count") or 0),
            "amount": amount,
            "fringe_key": fkey, "fringe_label": flabel,
            "fringe_rate": frate, "fringe": fringe,
        }
        if has_months:
            row["months"] = _months(raw.get("months"), warnings, f"{label} months")
        b_rows.append(row)
        if fringe:
            fringe_rows.append({"label": label, "rate": frate, "amount": fringe})

    a_total = round(sum(r["salary"] for r in a_rows), 2)
    b_total = round(sum(r["amount"] for r in b_rows), 2)
    c_total = round(sum(r["fringe"] for r in a_rows) + sum(r["fringe"] for r in b_rows), 2)

    return {
        "A": {"rows": a_rows, "total": a_total},
        "B": {"rows": b_rows, "total": b_total},
        "C": c_total,
        "fringe_rows": fringe_rows,
        "salaries_and_wages": round(a_total + b_total, 2),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/nsf_budget.py backend/tests/test_nsf_budget.py
git commit -m "feat(nsf-budget): lines A/B/C - person-months salary + per-row fringe"
```

---

## Task 4: Lines D, E, F, G — equipment, travel, participants, other direct

**Files:**
- Modify: `backend/services/nsf_budget.py`
- Test: `backend/tests/test_nsf_budget.py` (append)

**Interfaces:**
- Consumes: `_money`, `G_ITEM_LINES`.
- Produces: `compute_direct_lines(sheet, settings, warnings) -> dict` with keys `D` (`{"rows","total"}`), `E` (`{"domestic","international","total"}`), `F` (`{"count","stipends","travel","subsistence","other","total"}`), `G` (per-line totals plus `subawards: {"rows","total"}` and `total`), and `mtdc_exempt_total` (the sum of G.6 items flagged exempt).

- [ ] **Step 1: Write the failing tests**

```python
def test_equipment_total_sums_the_line_items():
    s = nb.blank_sheet()
    s["equipment"] = [{"description": "Confocal", "amount": 60_000},
                      {"description": "Freezer", "amount": 12_000}]
    r = nb.compute_direct_lines(s, nb.blank_document()["settings"], [])
    assert r["D"]["total"] == 72_000.0


def test_travel_splits_domestic_and_international():
    s = nb.blank_sheet()
    s["travel"]["domestic"] = [{"description": "PI to conf", "amount": 3_000}]
    s["travel"]["international"] = [{"description": "Collab visit", "amount": 4_500}]
    r = nb.compute_direct_lines(s, nb.blank_document()["settings"], [])
    assert r["E"]["domestic"] == 3_000.0
    assert r["E"]["international"] == 4_500.0
    assert r["E"]["total"] == 7_500.0


def test_participant_support_totals_all_four_sublines():
    s = nb.blank_sheet()
    s["participant_support"] = {"count": 20, "stipends": 10_000, "travel": 4_000,
                                "subsistence": 3_000, "other": 1_000}
    r = nb.compute_direct_lines(s, nb.blank_document()["settings"], [])
    assert r["F"]["total"] == 18_000.0
    assert r["F"]["count"] == 20


def test_g_total_includes_subawards():
    s = nb.blank_sheet()
    s["other_direct"]["materials_supplies"] = [{"description": "Reagents", "amount": 5_000}]
    s["other_direct"]["subawards"] = [{"organization": "Partner U", "amount": 50_000}]
    r = nb.compute_direct_lines(s, nb.blank_document()["settings"], [])
    assert r["G"]["subawards"]["total"] == 50_000.0
    assert r["G"]["total"] == 55_000.0


def test_mtdc_exempt_g6_items_are_tallied_separately():
    s = nb.blank_sheet()
    s["other_direct"]["other"] = [
        {"description": "Grad tuition remission", "amount": 40_000, "mtdc_exempt": True},
        {"description": "Lab fees", "amount": 5_000, "mtdc_exempt": False},
    ]
    r = nb.compute_direct_lines(s, nb.blank_document()["settings"], [])
    assert r["G"]["other"] == 45_000.0          # both are still direct costs
    assert r["mtdc_exempt_total"] == 40_000.0   # but only tuition leaves the F&A base


def test_blank_sheet_computes_all_zeros_without_crashing():
    r = nb.compute_direct_lines(nb.blank_sheet(), nb.blank_document()["settings"], [])
    assert r["D"]["total"] == 0.0 and r["G"]["total"] == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -k "equipment or travel or participant or g_total or exempt" -v`
Expected: FAIL — `AttributeError: module 'services.nsf_budget' has no attribute 'compute_direct_lines'`

- [ ] **Step 3: Implement `compute_direct_lines`**

```python
def _items(raw, warnings, field):
    """Normalise a list of {description, amount} line items."""
    out = []
    for it in raw or []:
        it = it or {}
        out.append({
            "description": (it.get("description") or "").strip(),
            "amount": _money(it.get("amount"), warnings, field),
        })
    return out


def compute_direct_lines(sheet, settings, warnings):
    """Lines D, E, F and G, plus the G.6 items exempted from the F&A base."""
    equipment = _items(sheet.get("equipment"), warnings, "equipment")
    d_total = round(sum(i["amount"] for i in equipment), 2)

    travel = sheet.get("travel") or {}
    dom = _items(travel.get("domestic"), warnings, "domestic travel")
    intl = _items(travel.get("international"), warnings, "international travel")
    dom_total = round(sum(i["amount"] for i in dom), 2)
    intl_total = round(sum(i["amount"] for i in intl), 2)

    ps = sheet.get("participant_support") or {}
    f_parts = {k: _money(ps.get(k), warnings, f"participant {k}")
               for k in ("stipends", "travel", "subsistence", "other")}
    f_total = round(sum(f_parts.values()), 2)

    od = sheet.get("other_direct") or {}
    g = {}
    g_rows = {}
    for key, label in G_ITEM_LINES:
        rows = _items(od.get(key), warnings, label.lower())
        g_rows[key] = rows
        g[key] = round(sum(i["amount"] for i in rows), 2)

    subs = []
    for s in od.get("subawards") or []:
        s = s or {}
        subs.append({
            "organization": (s.get("organization") or "").strip(),
            "amount": _money(s.get("amount"), warnings, "subaward"),
        })
    subs_total = round(sum(s["amount"] for s in subs), 2)

    others, exempt_total = [], 0.0
    for it in od.get("other") or []:
        it = it or {}
        amt = _money(it.get("amount"), warnings, "other direct cost")
        exempt = bool(it.get("mtdc_exempt"))
        others.append({"description": (it.get("description") or "").strip(),
                       "amount": amt, "mtdc_exempt": exempt})
        if exempt:
            exempt_total += amt
    other_total = round(sum(i["amount"] for i in others), 2)

    g_total = round(sum(g.values()) + subs_total + other_total, 2)

    return {
        "D": {"rows": equipment, "total": d_total},
        "E": {"domestic": dom_total, "international": intl_total,
              "domestic_rows": dom, "international_rows": intl,
              "total": round(dom_total + intl_total, 2)},
        "F": {"count": int(ps.get("count") or 0), **f_parts, "total": f_total},
        "G": {**g, "rows": g_rows,
              "subawards": {"rows": subs, "total": subs_total},
              "other": other_total, "other_rows": others,
              "total": g_total},
        "mtdc_exempt_total": round(exempt_total, 2),
        "subaward_amounts": [s["amount"] for s in subs],
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/nsf_budget.py backend/tests/test_nsf_budget.py
git commit -m "feat(nsf-budget): lines D/E/F/G with mtdc_exempt tracking"
```

---

## Task 5: The rollup — H through M, and the cumulative sheet

**Files:**
- Modify: `backend/services/nsf_budget.py`
- Test: `backend/tests/test_nsf_budget.py` (append)

**Interfaces:**
- Consumes: `compute_personnel`, `compute_direct_lines`, `mtdc_and_fa`.
- Produces:
  - `compute_sheet(sheet, settings, warnings) -> dict` with `year`, `lines` (`A`–`M`), `mtdc`, `fa`.
  - `compute_document(doc) -> dict` with `years`, `cumulative`, `cap`, `warnings`, `flags` (flags filled in Task 9), `settings`, `meta`.

- [ ] **Step 1: Write the failing tests**

```python
def test_line_h_is_the_sum_of_a_through_g():
    s = nb.blank_sheet()
    s["senior"][0].update(base_salary=90_000, appointment_basis="academic_9", acad=2)
    s["equipment"] = [{"description": "Rig", "amount": 40_000}]
    s["travel"]["domestic"] = [{"description": "Conf", "amount": 3_000}]
    c = nb.compute_sheet(s, nb.blank_document()["settings"], [])
    # A 20,000 + C 8,400 + D 40,000 + E 3,000
    assert c["lines"]["H"] == 71_400.0


def test_line_i_is_fa_on_mtdc_not_on_total_direct():
    s = nb.blank_sheet()
    s["equipment"] = [{"description": "Rig", "amount": 40_000}]
    s["other_direct"]["materials_supplies"] = [{"description": "Reagents", "amount": 10_000}]
    c = nb.compute_sheet(s, nb.blank_document()["settings"], [])
    assert c["lines"]["H"] == 50_000.0
    assert c["mtdc"]["base"] == 10_000.0          # equipment is out of the base
    assert c["lines"]["I"] == 5_400.0             # 54% of 10k, not of 50k


def test_line_j_is_h_plus_i_and_l_equals_j_without_a_fee():
    s = nb.blank_sheet()
    s["other_direct"]["materials_supplies"] = [{"description": "Reagents", "amount": 10_000}]
    c = nb.compute_sheet(s, nb.blank_document()["settings"], [])
    assert c["lines"]["J"] == 15_400.0
    assert c["lines"]["L"] == 15_400.0


def test_a_fee_is_subtracted_on_line_l():
    s = nb.blank_sheet()
    s["other_direct"]["materials_supplies"] = [{"description": "Reagents", "amount": 10_000}]
    s["fee"] = 1_000
    c = nb.compute_sheet(s, nb.blank_document()["settings"], [])
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
    """Hand-checked end-to-end figure, in the style of the generic $161,556 case.

    A: PI 90,000/9 x 2 acad months            = 20,000
    B: 1 grad student                         = 30,000
    C: 42% of 20,000 + 9% of 30,000           =  11,100
    D: equipment                              =  40,000
    E: 3,000 domestic + 2,000 international   =   5,000
    F: participant support                    =  10,000
    G: 5,000 supplies + 50,000 subaward
       + 25,000 tuition (F&A-exempt)          =  80,000
    H                                         = 196,100
    MTDC = 196,100 - 40,000 - 10,000 - 25,000 (sub tail) - 25,000 (tuition)
                                              =  96,100
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -k "line_ or cumulative or worked or cap_over" -v`
Expected: FAIL — `AttributeError: … has no attribute 'compute_sheet'`

- [ ] **Step 3: Implement `compute_sheet` and `compute_document`**

```python
def compute_sheet(sheet, settings, warnings):
    """One year: lines A-M, the MTDC base, and the F&A figure."""
    settings = settings or {}
    people = compute_personnel(sheet, warnings)
    direct = compute_direct_lines(sheet, settings, warnings)

    h = round(people["A"]["total"] + people["B"]["total"] + people["C"]
              + direct["D"]["total"] + direct["E"]["total"]
              + direct["F"]["total"] + direct["G"]["total"], 2)

    eng = mtdc_and_fa(
        direct_total=h,
        equipment=direct["D"]["total"],
        participant_support=direct["F"]["total"],
        subawards=direct["subaward_amounts"],
        extra_exempt=direct["mtdc_exempt_total"],
        fa_year=settings.get("fa_year"),
        fa_rate_key=settings.get("fa_rate_key"),
        warnings=warnings,
    )

    i = eng["fa_amount"]
    j = round(h + i, 2)
    k = _money(sheet.get("fee"), warnings, "fee")
    l = round(j - k, 2)
    cs = sheet.get("cost_sharing") or {}
    m = _money(cs.get("proposed"), warnings, "cost sharing")

    return {
        "year": sheet.get("year", 1),
        "lines": {
            "A": people["A"], "B": people["B"], "C": people["C"],
            "salaries_and_wages": people["salaries_and_wages"],
            "salaries_wages_fringe": round(people["salaries_and_wages"] + people["C"], 2),
            "D": direct["D"], "E": direct["E"], "F": direct["F"], "G": direct["G"],
            "H": h, "I": i, "J": j, "K": k, "L": l, "M": m,
        },
        "mtdc": {"base": eng["mtdc_base"], "exclusions": eng["exclusions"]},
        "fa": {"year": eng["fa_year"], "rate_key": eng["fa_rate_key"],
               "rate": eng["fa_rate"], "label": eng["fa_rate_label"]},
        "fringe_rows": people["fringe_rows"],
        "flags": [],
    }


_SUMMABLE = ("C", "H", "I", "J", "K", "L", "M")


def _sum_cumulative(year_results):
    """Cumulative sheet: every line summed across years. Never stored."""
    lines = {k: round(sum(y["lines"][k] for y in year_results), 2) for k in _SUMMABLE}
    for key in ("A", "B", "D"):
        lines[key] = {"rows": [], "total": round(
            sum(y["lines"][key]["total"] for y in year_results), 2)}
    lines["E"] = {
        "domestic": round(sum(y["lines"]["E"]["domestic"] for y in year_results), 2),
        "international": round(sum(y["lines"]["E"]["international"] for y in year_results), 2),
        "total": round(sum(y["lines"]["E"]["total"] for y in year_results), 2),
    }
    lines["F"] = {
        "count": max((y["lines"]["F"]["count"] for y in year_results), default=0),
        **{k: round(sum(y["lines"]["F"][k] for y in year_results), 2)
           for k in ("stipends", "travel", "subsistence", "other")},
        "total": round(sum(y["lines"]["F"]["total"] for y in year_results), 2),
    }
    g = {k: round(sum(y["lines"]["G"][k] for y in year_results), 2)
         for k, _ in G_ITEM_LINES}
    g["other"] = round(sum(y["lines"]["G"]["other"] for y in year_results), 2)
    g["subawards"] = {"rows": [], "total": round(
        sum(y["lines"]["G"]["subawards"]["total"] for y in year_results), 2)}
    g["total"] = round(sum(y["lines"]["G"]["total"] for y in year_results), 2)
    lines["G"] = g
    lines["salaries_and_wages"] = round(
        sum(y["lines"]["salaries_and_wages"] for y in year_results), 2)
    lines["salaries_wages_fringe"] = round(
        sum(y["lines"]["salaries_wages_fringe"] for y in year_results), 2)

    mtdc = {
        "base": round(sum(y["mtdc"]["base"] for y in year_results), 2),
        "exclusions": {k: round(sum(y["mtdc"]["exclusions"][k] for y in year_results), 2)
                       for k in ("equipment", "participant_support",
                                 "subaward_over_25k", "mtdc_exempt")},
    }
    return {"lines": lines, "mtdc": mtdc, "flags": []}


def compute_document(doc):
    """Compute every year sheet plus the cumulative rollup and the cap check."""
    doc = doc or {}
    warnings: list[str] = []
    settings = doc.get("settings") or blank_document()["settings"]
    sheets = doc.get("years") or [blank_sheet(1)]

    years = [compute_sheet(s or {}, settings, warnings) for s in sheets]
    cumulative = _sum_cumulative(years)

    raw_cap = settings.get("cap")
    total = cumulative["lines"]["L"]
    if raw_cap in (None, ""):
        cap = {"value": None, "status": "none", "overage": 0.0}
    else:
        cap_val = _money(raw_cap, warnings, "cap")
        over = round(total - cap_val, 2)
        cap = {"value": cap_val,
               "status": "over" if over > 0 else "ok",
               "overage": over if over > 0 else 0.0}

    return {
        "schema": SCHEMA, "version": VERSION,
        "meta": doc.get("meta") or blank_document()["meta"],
        "settings": settings,
        "years": years, "cumulative": cumulative,
        "cap": cap, "warnings": warnings, "flags": [],
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -v`
Expected: PASS, including `test_worked_example_totals_exactly`.

- [ ] **Step 5: Commit**

```bash
git add backend/services/nsf_budget.py backend/tests/test_nsf_budget.py
git commit -m "feat(nsf-budget): H-M rollup, MTDC, cumulative sheet, cap check"
```

---

## Task 6: Add-year with salary escalation

**Files:**
- Modify: `backend/services/nsf_budget.py`
- Test: `backend/tests/test_nsf_budget.py` (append)

**Interfaces:**
- Consumes: `blank_sheet`, `DEFAULT_ESCALATION_PCT`.
- Produces: `add_year(doc, escalation_pct=None) -> dict` (returns the document with one more year appended; does not mutate the input).

- [ ] **Step 1: Write the failing tests**

```python
def test_add_year_escalates_salary_bases_by_three_percent():
    doc = nb.blank_document()
    doc["years"][0]["senior"][0].update(base_salary=100_000, acad=2)
    out = nb.add_year(doc)
    assert len(out["years"]) == 2
    assert out["years"][1]["senior"][0]["base_salary"] == 103_000.0
    assert out["years"][1]["senior"][0]["acad"] == 2          # months carry over


def test_add_year_escalates_other_personnel_amounts():
    doc = nb.blank_document()
    doc["years"][0]["other_personnel"]["postdocs"]["amount"] = 60_000
    out = nb.add_year(doc)
    assert out["years"][1]["other_personnel"]["postdocs"]["amount"] == 61_800.0


def test_add_year_does_not_escalate_equipment_or_travel_or_subawards():
    doc = nb.blank_document()
    y = doc["years"][0]
    y["equipment"] = [{"description": "Rig", "amount": 40_000}]
    y["travel"]["domestic"] = [{"description": "Conf", "amount": 3_000}]
    y["other_direct"]["subawards"] = [{"organization": "Partner U", "amount": 50_000}]
    out = nb.add_year(doc)
    y2 = out["years"][1]
    assert y2["equipment"][0]["amount"] == 40_000
    assert y2["travel"]["domestic"][0]["amount"] == 3_000
    assert y2["other_direct"]["subawards"][0]["amount"] == 50_000


def test_add_year_does_not_mutate_the_original_document():
    doc = nb.blank_document()
    doc["years"][0]["senior"][0]["base_salary"] = 100_000
    nb.add_year(doc)
    assert len(doc["years"]) == 1
    assert doc["years"][0]["senior"][0]["base_salary"] == 100_000


def test_escalation_rate_is_overridable():
    doc = nb.blank_document()
    doc["years"][0]["senior"][0]["base_salary"] = 100_000
    out = nb.add_year(doc, escalation_pct=0)
    assert out["years"][1]["senior"][0]["base_salary"] == 100_000.0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -k add_year -v`
Expected: FAIL — `AttributeError: … has no attribute 'add_year'`

- [ ] **Step 3: Implement `add_year`**

```python
def add_year(doc, escalation_pct=None):
    """Append a new year by cloning the last one, escalating SALARY BASES only.

    Escalating travel/supplies/participant support automatically was rejected in
    design: it silently inflates a budget the PI may never re-check.
    """
    import copy

    doc = copy.deepcopy(doc or blank_document())
    years = doc.setdefault("years", [])
    if not years:
        years.append(blank_sheet(1))
        return doc

    if escalation_pct is None:
        escalation_pct = (doc.get("settings") or {}).get(
            "escalation_pct", DEFAULT_ESCALATION_PCT)
    try:
        factor = 1.0 + (float(escalation_pct) / 100.0)
    except (TypeError, ValueError):
        factor = 1.0 + (DEFAULT_ESCALATION_PCT / 100.0)

    new = copy.deepcopy(years[-1])
    new["year"] = len(years) + 1

    for p in new.get("senior") or []:
        if p.get("base_salary") not in (None, ""):
            try:
                p["base_salary"] = round(float(p["base_salary"]) * factor, 2)
            except (TypeError, ValueError):
                pass
    for row in (new.get("other_personnel") or {}).values():
        if row.get("amount") not in (None, ""):
            try:
                row["amount"] = round(float(row["amount"]) * factor, 2)
            except (TypeError, ValueError):
                pass

    years.append(new)
    return doc
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/nsf_budget.py backend/tests/test_nsf_budget.py
git commit -m "feat(nsf-budget): add_year clones with salary-only escalation"
```

---

## Task 7: The rules framework and the personnel rules (lines A–C)

**Files:**
- Create: `backend/services/nsf_budget_rules.py`
- Test: `backend/tests/test_nsf_budget_rules.py` (create)

**Interfaces:**
- Consumes: `nsf_budget.MAX_SENIOR_MONTHS`, `MONTHS_PER_BASIS`.
- Produces:
  - `Flag = dict` with keys `id`, `line`, `severity`, `title`, `message`, `citation`, `scope`, `year`, `detail`.
  - `RULES: list[dict]` — each with `id`, `line`, `severity`, `title`, `message`, `citation`, `scope`, `check`.
  - `evaluate(doc, computed) -> list[Flag]`.
  - `_ctx` shape passed to each `check`: `{"sheet","computed","settings","meta","year"}` for `scope="year"`, and `{"doc","computed","settings","meta"}` for `scope="proposal"`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_nsf_budget_rules.py`:

```python
"""Every NSF rule gets one test that fires it and one that does not.

Rules WARN, they never block: nothing here may prevent a compute from
returning. Each rule carries its PAPPG 24-1 citation.
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.nsf_budget_rules'`

- [ ] **Step 3: Create `backend/services/nsf_budget_rules.py`**

```python
"""NSF budget rules — data, not code buried in the math.

Each rule is a table entry plus one predicate. Adding a rule is one entry and
one function; it never touches the arithmetic. Every rule carries its PAPPG
24-1 citation, shown in the UI beside the flag.

Severities: "warn" (a rule is likely broken) and "info" (a requirement to be
satisfied elsewhere). NEITHER BLOCKS anything -- not editing, not computing,
not saving, not exporting.
"""
from __future__ import annotations

from services.nsf_budget import (
    FORM_ITEMISE_THRESHOLD, MAX_SENIOR_MONTHS, MONTHS_PER_BASIS,
    SUBAWARD_MTDC_CAP,
)

PAPPG = "PAPPG 24-1"


# ── line A / B predicates ──────────────────────────────────────────────────

def _check_two_month_cap(ctx):
    out = []
    for row in ctx["computed"]["lines"]["A"]["rows"]:
        if row["months_total"] > MAX_SENIOR_MONTHS:
            out.append(f"{row['name']}: {row['months_total']:g} months requested "
                       f"(NSF's limit is {MAX_SENIOR_MONTHS:g} per year).")
    return out


def _check_basis_mismatch(ctx):
    out = []
    for row in ctx["computed"]["lines"]["A"]["rows"]:
        if row["appointment_basis"] == "academic_9" and row["cal"] > 0:
            out.append(f"{row['name']}: calendar months on a 9-month appointment.")
        if row["appointment_basis"] == "calendar_12" and (row["acad"] > 0 or row["sumr"] > 0):
            out.append(f"{row['name']}: academic/summer months on a 12-month appointment.")
    return out


def _check_incomplete_rows(ctx):
    out = []
    for row in ctx["computed"]["lines"]["A"]["rows"]:
        if row["months_total"] > 0 and row["base_salary"] <= 0:
            out.append(f"{row['name']}: months entered with no base salary.")
        elif row["base_salary"] > 0 and row["months_total"] <= 0:
            out.append(f"{row['name']}: base salary entered with no person-months.")
    for row in ctx["computed"]["lines"]["B"]["rows"]:
        if row["count"] > 0 and row["amount"] <= 0:
            out.append(f"{row['label']}: headcount entered with no dollars.")
    return out


RULES = [
    {"id": "nsf.senior.two_month_cap", "line": "A", "severity": "warn", "scope": "year",
     "title": "Senior salary over two months",
     "message": ("NSF limits each senior/key person to no more than two months of "
                 "salary in any one year, counted across ALL NSF awards. This tool "
                 "sees only this proposal, so it cannot check the PI's other NSF "
                 "grants. Anything over two months must be disclosed in the budget "
                 "justification."),
     "citation": f"{PAPPG} II.D.2.f(i)(a)",
     "check": _check_two_month_cap},

    {"id": "nsf.senior.basis_mismatch", "line": "A", "severity": "warn", "scope": "year",
     "title": "Person-months do not match the appointment",
     "message": ("A 9-month academic appointment is budgeted in academic and summer "
                 "months; a 12-month appointment in calendar months. Check the "
                 "appointment basis, or confirm this is an unusual appointment."),
     "citation": f"{PAPPG} II.D.2.f(i)",
     "check": _check_basis_mismatch},

    {"id": "nsf.personnel.incomplete_row", "line": "A", "severity": "warn", "scope": "year",
     "title": "Incomplete personnel row",
     "message": ("A personnel row has months without a salary, a salary without "
                 "months, or a headcount without dollars. It will contribute $0."),
     "citation": f"{PAPPG} II.D.2.f(i)",
     "check": _check_incomplete_rows},
]


def evaluate(doc, computed):
    """Run every rule and return a flat list of flags. Never raises."""
    doc = doc or {}
    settings = computed.get("settings") or {}
    meta = computed.get("meta") or {}
    flags = []

    for rule in RULES:
        try:
            if rule["scope"] == "year":
                for idx, year_computed in enumerate(computed.get("years") or []):
                    sheets = doc.get("years") or []
                    ctx = {"sheet": sheets[idx] if idx < len(sheets) else {},
                           "computed": year_computed, "settings": settings,
                           "meta": meta, "year": year_computed.get("year", idx + 1)}
                    for detail in rule["check"](ctx) or []:
                        flags.append(_flag(rule, detail, ctx["year"]))
            else:
                ctx = {"doc": doc, "computed": computed,
                       "settings": settings, "meta": meta}
                for detail in rule["check"](ctx) or []:
                    flags.append(_flag(rule, detail, None))
        except Exception as e:                      # a broken rule must never
            print(f"[NSF-RULES] rule {rule['id']} failed: {e}")   # break a compute
    return flags


def _flag(rule, detail, year):
    return {"id": rule["id"], "line": rule["line"], "severity": rule["severity"],
            "title": rule["title"], "message": rule["message"],
            "citation": rule["citation"], "scope": rule["scope"],
            "year": year, "detail": detail}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget_rules.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/nsf_budget_rules.py backend/tests/test_nsf_budget_rules.py
git commit -m "feat(nsf-budget): rules framework + lines A-C personnel rules"
```

---

## Task 8: Rules for lines D, E, F and G

**Files:**
- Modify: `backend/services/nsf_budget_rules.py`
- Test: `backend/tests/test_nsf_budget_rules.py` (append)

**Interfaces:**
- Consumes: the Task 7 framework.
- Produces: seven more entries in `RULES`, ids `nsf.equipment.below_capitalization`, `nsf.equipment.unitemised`, `nsf.equipment.general_purpose`, `nsf.travel.international`, `nsf.participants.no_count`, `nsf.participants.not_employees`, `nsf.subaward.separate_budget`, `nsf.subaward.over_25k`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget_rules.py -v`
Expected: FAIL on the new tests — the rule ids are not in `RULES`.

- [ ] **Step 3: Add the predicates and rule entries**

Insert the predicates above `RULES`, and the entries into the `RULES` list:

```python
def _check_equipment_below_cap(ctx):
    level = float(ctx["settings"].get("capitalization_level") or 5000.0)
    out = []
    for row in ctx["computed"]["lines"]["D"]["rows"]:
        if 0 < row["amount"] < level:
            name = row["description"] or "an unnamed item"
            out.append(f"{name} at ${row['amount']:,.0f} is below the "
                       f"${level:,.0f} capitalization level.")
    return out


def _check_equipment_unitemised(ctx):
    return [f"An equipment item of ${row['amount']:,.0f} has no description."
            for row in ctx["computed"]["lines"]["D"]["rows"]
            if row["amount"] > FORM_ITEMISE_THRESHOLD and not row["description"]]


def _check_equipment_general_purpose(ctx):
    return (["Confirm each item is research equipment, not general-purpose."]
            if ctx["computed"]["lines"]["D"]["total"] > 0 else [])


def _check_international_travel(ctx):
    amt = ctx["computed"]["lines"]["E"]["international"]
    return ([f"${amt:,.0f} of international travel is budgeted."] if amt > 0 else [])


def _check_participants_no_count(ctx):
    f = ctx["computed"]["lines"]["F"]
    return (["Participant support dollars are budgeted but the participant "
             "count is 0."] if f["total"] > 0 and f["count"] <= 0 else [])


def _check_participants_not_employees(ctx):
    return (["Confirm no participant is an employee."]
            if ctx["computed"]["lines"]["F"]["total"] > 0 else [])


def _check_subaward_separate_budget(ctx):
    return [f"{s['organization'] or 'An unnamed subrecipient'} needs its own "
            f"budget and a justification of no more than five pages."
            for s in ctx["computed"]["lines"]["G"]["subawards"]["rows"]
            if s["amount"] > 0]


def _check_subaward_over_25k(ctx):
    out = []
    for s in ctx["computed"]["lines"]["G"]["subawards"]["rows"]:
        if s["amount"] > SUBAWARD_MTDC_CAP:
            excluded = s["amount"] - SUBAWARD_MTDC_CAP
            out.append(f"{s['organization'] or 'Subaward'}: ${excluded:,.0f} is "
                       f"excluded from the F&A base (only the first "
                       f"${SUBAWARD_MTDC_CAP:,.0f} of each subaward is in MTDC).")
    return out
```

```python
    {"id": "nsf.equipment.below_capitalization", "line": "D", "severity": "warn",
     "scope": "year", "title": "Equipment below the capitalization level",
     "message": ("NSF defines equipment as having a useful life over one year and a "
                 "per-unit cost of at least the lesser of the organization's "
                 "capitalization level or $5,000. Cheaper items belong in G.1 "
                 "Materials and Supplies -- and unlike equipment, they DO bear F&A."),
     "citation": f"{PAPPG} II.D.2.f(iii)", "check": _check_equipment_below_cap},

    {"id": "nsf.equipment.unitemised", "line": "D", "severity": "warn", "scope": "year",
     "title": "Equipment over $10,000 with no description",
     "message": ("Form 1030 line D requires each item over $10,000 to be listed "
                 "individually by description and estimated cost."),
     "citation": "NSF Form 1030 line D", "check": _check_equipment_unitemised},

    {"id": "nsf.equipment.general_purpose", "line": "D", "severity": "info",
     "scope": "year", "title": "General-purpose equipment is normally unallowable",
     "message": ("Office equipment, furnishings, and general IT are typically not "
                 "eligible for direct-cost support. Special-purpose or scientific "
                 "computers may be requested when justified."),
     "citation": f"{PAPPG} II.D.2.f(iii)", "check": _check_equipment_general_purpose},

    {"id": "nsf.travel.international", "line": "E", "severity": "info", "scope": "year",
     "title": "International travel",
     "message": ("Foreign travel must be listed separately and justified, and is "
                 "subject to the Fly America Act's U.S.-flag air carrier requirement."),
     "citation": f"{PAPPG} II.D.2.f(iv)(c)", "check": _check_international_travel},

    {"id": "nsf.participants.no_count", "line": "F", "severity": "warn", "scope": "year",
     "title": "No participant count",
     "message": ("Form 1030 requires the total number of participants in the "
                 "parentheses on line F, and the costs must be itemised and "
                 "justified in the budget justification."),
     "citation": f"{PAPPG} II.D.2.f(v)", "check": _check_participants_no_count},

    {"id": "nsf.participants.not_employees", "line": "F", "severity": "info",
     "scope": "year", "title": "Participants may not be employees",
     "message": ("Participant support is for participants and trainees, not "
                 "employees. Speakers and trainers are generally not participants. "
                 "Human-subject incentive payments belong on line G.6, not here. "
                 "No F&A is charged on participant support."),
     "citation": f"{PAPPG} II.D.2.f(v)", "check": _check_participants_not_employees},

    {"id": "nsf.subaward.separate_budget", "line": "G.5", "severity": "info",
     "scope": "year", "title": "Each subaward needs its own budget",
     "message": ("A separate budget and a justification of no more than five pages "
                 "is required for each identified subrecipient, using that "
                 "subrecipient's own federally negotiated indirect cost rate."),
     "citation": f"{PAPPG} II.D.2.f(vi)(e)", "check": _check_subaward_separate_budget},

    {"id": "nsf.subaward.over_25k", "line": "G.5", "severity": "info", "scope": "year",
     "title": "Subaward over $25,000",
     "message": ("Only the first $25,000 of each subaward is included in the "
                 "modified total direct cost base that F&A is charged on."),
     "citation": "2 CFR 200.1", "check": _check_subaward_over_25k},
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget_rules.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/nsf_budget_rules.py backend/tests/test_nsf_budget_rules.py
git commit -m "feat(nsf-budget): rules for lines D/E/F/G"
```

---

## Task 9: Rules for lines I, K, M and the proposal-scope rules

**Files:**
- Modify: `backend/services/nsf_budget_rules.py`, `backend/services/nsf_budget.py`
- Test: `backend/tests/test_nsf_budget_rules.py` (append)

**Interfaces:**
- Consumes: the Task 7 framework, `budget_helper.FA_RATES`.
- Produces: rule ids `nsf.fa.below_negotiated`, `nsf.fa.unknown_rate`, `nsf.fee.restricted`, `nsf.cost_sharing.voluntary`, `nsf.structure.missing_years`, `nsf.cap.exceeded`, `nsf.justification.five_pages`. Also wires `evaluate()` into `compute_document()` so `computed["flags"]` and each `computed["years"][i]["flags"]` are populated.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget_rules.py -v`
Expected: FAIL on the new tests.

- [ ] **Step 3: Support `fa_rate_override` in the engine**

In `nsf_budget.compute_sheet`, after the `mtdc_and_fa` call, honour an explicit override so the rule has something to flag:

```python
    override = settings.get("fa_rate_override")
    if override not in (None, ""):
        try:
            rate = float(override)
            eng["fa_rate"] = rate
            eng["fa_rate_label"] = f"Manual rate ({rate * 100:.1f}%)"
            eng["fa_amount"] = round(eng["mtdc_base"] * rate, 2)
        except (TypeError, ValueError):
            warnings.append(f"Could not read F&A override '{override}'; using the negotiated rate.")
```

Place this immediately after `eng = mtdc_and_fa(...)` and before `i = eng["fa_amount"]`.

- [ ] **Step 4: Add the predicates and rule entries**

```python
from services.budget_helper import FA_RATES


def _negotiated_rate(settings):
    year = settings.get("fa_year") or "fy_2025_2026"
    key = settings.get("fa_rate_key") or "organized_research_on_campus"
    rates = FA_RATES.get(year) or {}
    entry = rates.get(key)
    return entry[1] if entry else None


def _check_fa_below_negotiated(ctx):
    negotiated = _negotiated_rate(ctx["settings"])
    applied = ctx["computed"]["fa"]["rate"]
    if negotiated is None or applied >= negotiated:
        return []
    return [f"{applied * 100:.1f}% applied against a negotiated rate of "
            f"{negotiated * 100:.0f}%."]


def _check_fa_unknown_rate(ctx):
    return (["A manual F&A rate is in use rather than one of Morgan's "
             "negotiated rates."]
            if ctx["settings"].get("fa_rate_override") not in (None, "") else [])


def _check_fee_restricted(ctx):
    program = (ctx["meta"].get("sponsor_program") or "standard").lower()
    if ctx["computed"]["lines"]["K"] > 0 and program not in ("sbir_sttr", "major_facility"):
        return [f"A fee of ${ctx['computed']['lines']['K']:,.0f} is budgeted on a "
                f"'{program}' proposal."]
    return []


def _check_voluntary_cost_sharing(ctx):
    if ctx["computed"]["lines"]["M"] > 0 and not ctx["meta"].get("mandatory_cost_sharing"):
        return [f"${ctx['computed']['lines']['M']:,.0f} of cost sharing is proposed."]
    return []


def _check_missing_years(ctx):
    months = int(ctx["meta"].get("duration_months") or 0)
    if not months:
        return []
    expected = -(-months // 12)                     # ceiling division
    actual = len(ctx["computed"].get("years") or [])
    if actual < expected:
        return [f"{actual} year sheet(s) for a {months}-month project "
                f"({expected} expected)."]
    return []


def _check_cap_exceeded(ctx):
    cap = ctx["computed"].get("cap") or {}
    if cap.get("status") == "over":
        return [f"The cumulative request exceeds the cap by "
                f"${cap['overage']:,.0f}."]
    return []


def _check_five_page_justification(ctx):
    return ["The budget justification may be no more than five pages."]
```

```python
    {"id": "nsf.fa.below_negotiated", "line": "I", "severity": "warn", "scope": "year",
     "title": "F&A rate below the negotiated rate",
     "message": ("NSF requires the applicable federally negotiated indirect cost "
                 "rate. Using a lower rate is itself a violation of NSF's cost "
                 "sharing policy -- it is not a way to fit under a budget cap."),
     "citation": f"{PAPPG} II.D.2.f(viii)", "check": _check_fa_below_negotiated},

    {"id": "nsf.fa.unknown_rate", "line": "I", "severity": "warn", "scope": "year",
     "title": "Manual F&A rate",
     "message": ("The rate in use is not one of Morgan's negotiated rates from the "
                 "knowledge base. Confirm it against ORA's current rate agreement."),
     "citation": f"{PAPPG} II.D.2.f(viii)", "check": _check_fa_unknown_rate},

    {"id": "nsf.fee.restricted", "line": "K", "severity": "warn", "scope": "year",
     "title": "Fee outside SBIR/STTR or Major Facilities",
     "message": ("Line K is available only to the SBIR/STTR programs and Major "
                 "Facilities programs, and only when the solicitation specifies it."),
     "citation": f"{PAPPG} II.D.2.f(x)", "check": _check_fee_restricted},

    {"id": "nsf.cost_sharing.voluntary", "line": "M", "severity": "warn", "scope": "year",
     "title": "Voluntary committed cost sharing",
     "message": ("Voluntary committed cost sharing is prohibited. Line M is used "
                 "only when the program solicitation mandates cost sharing -- mark "
                 "the proposal accordingly if it does."),
     "citation": f"{PAPPG} II.D.2.f(xii)", "check": _check_voluntary_cost_sharing},

    {"id": "nsf.structure.missing_years", "line": "-", "severity": "warn",
     "scope": "proposal", "title": "Fewer year sheets than the project duration",
     "message": "A budget is required for each year of support requested.",
     "citation": f"{PAPPG} II.D.2.f", "check": _check_missing_years},

    {"id": "nsf.cap.exceeded", "line": "-", "severity": "warn", "scope": "proposal",
     "title": "Over the solicitation's budget cap",
     "message": "The cumulative request exceeds the cap recorded for this proposal.",
     "citation": "solicitation", "check": _check_cap_exceeded},

    {"id": "nsf.justification.five_pages", "line": "-", "severity": "info",
     "scope": "proposal", "title": "Budget justification page limit",
     "message": ("The budget justification is limited to five pages per proposal, "
                 "plus up to five pages for each subrecipient."),
     "citation": f"{PAPPG} II.D.2.f", "check": _check_five_page_justification},
```

- [ ] **Step 5: Wire `evaluate()` into `compute_document()`**

At the end of `nsf_budget.compute_document`, before the return, replace the `"flags": []` entry:

```python
    from services import nsf_budget_rules          # imported late to avoid a cycle

    result = {
        "schema": SCHEMA, "version": VERSION,
        "meta": doc.get("meta") or blank_document()["meta"],
        "settings": settings,
        "years": years, "cumulative": cumulative,
        "cap": cap, "warnings": warnings, "flags": [],
    }
    all_flags = nsf_budget_rules.evaluate(doc, result)
    result["flags"] = all_flags
    by_year = {}
    for f in all_flags:
        if f.get("year"):
            by_year.setdefault(f["year"], []).append(f)
    for y in years:
        y["flags"] = by_year.get(y["year"], [])
    return result
```

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py tests/test_nsf_budget_rules.py tests/test_budget_helper.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/services/nsf_budget_rules.py backend/services/nsf_budget.py backend/tests/test_nsf_budget_rules.py
git commit -m "feat(nsf-budget): lines I/K/M + proposal rules, wired into compute"
```

---

## Task 10: The NSF budget justification

**Files:**
- Modify: `backend/services/nsf_budget.py`
- Test: `backend/tests/test_nsf_budget.py` (append)

**Interfaces:**
- Consumes: `compute_document`.
- Produces: `draft_justification(doc, computed=None) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
def _worked_doc():
    doc = nb.blank_document()
    s = doc["years"][0]
    s["senior"][0].update(name="Dr. Oladunni", role="PI", base_salary=90_000,
                          appointment_basis="academic_9", acad=2, fringe_key="faculty_ay")
    s["equipment"] = [{"description": "Confocal microscope", "amount": 40_000}]
    s["travel"]["international"] = [{"description": "Collaborator visit", "amount": 2_000}]
    s["participant_support"] = {"count": 15, "stipends": 10_000, "travel": None,
                                "subsistence": None, "other": None}
    s["other_direct"]["subawards"] = [{"organization": "Partner U", "amount": 50_000}]
    return doc


def test_justification_names_each_senior_person_with_months_and_fringe():
    text = nb.draft_justification(_worked_doc())
    assert "Dr. Oladunni" in text
    assert "2 academic months" in text
    assert "42%" in text


def test_justification_lists_each_equipment_item():
    assert "Confocal microscope" in nb.draft_justification(_worked_doc())


def test_justification_reports_the_participant_count():
    assert "15 participants" in nb.draft_justification(_worked_doc())


def test_justification_names_each_subaward_and_its_mtdc_note():
    text = nb.draft_justification(_worked_doc())
    assert "Partner U" in text
    assert "$25,000" in text


def test_justification_states_the_fa_rate_and_base():
    text = nb.draft_justification(_worked_doc())
    assert "54%" in text
    assert "modified total direct cost" in text.lower()


def test_justification_of_a_blank_budget_does_not_crash():
    assert nb.draft_justification(nb.blank_document())


def test_multi_year_justification_has_a_cumulative_paragraph():
    doc = nb.add_year(_worked_doc())
    text = nb.draft_justification(doc)
    assert "Year 1" in text and "Year 2" in text
    assert "cumulative" in text.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -k justification -v`
Expected: FAIL — `AttributeError: … has no attribute 'draft_justification'`

- [ ] **Step 3: Implement `draft_justification`**

```python
def _fmt(amount):
    return f"${amount:,.0f}"


def _months_phrase(row):
    parts = []
    for key, label in (("cal", "calendar"), ("acad", "academic"), ("sumr", "summer")):
        if row.get(key):
            parts.append(f"{row[key]:g} {label} months")
    return ", ".join(parts) or "no person-months"


def draft_justification(doc, computed=None):
    """Deterministic NSF budget justification. Figures come ONLY from `computed`.

    The AI polish is layered at the endpoint with a hard fallback to this text,
    so a justification always returns even when Gemini is unavailable.
    """
    computed = computed or compute_document(doc)
    lines = ["BUDGET JUSTIFICATION", ""]

    for yc in computed["years"]:
        lines.append(f"Year {yc['year']}")
        lines.append("")
        L = yc["lines"]

        if L["A"]["rows"]:
            lines.append("A. Senior/Key Personnel")
            for row in L["A"]["rows"]:
                if row["salary"] <= 0:
                    continue
                lines.append(
                    f"  - {row['name']}"
                    + (f" ({row['role']})" if row["role"] else "")
                    + f": {_months_phrase(row)} at a base salary of "
                      f"{_fmt(row['base_salary'])} = {_fmt(row['salary'])}, plus "
                      f"{row['fringe_label']} fringe at {row['fringe_rate'] * 100:.0f}% "
                      f"= {_fmt(row['fringe'])}.")
            lines.append("")

        paid_b = [r for r in L["B"]["rows"] if r["amount"] > 0]
        if paid_b:
            lines.append("B. Other Personnel")
            for row in paid_b:
                count = f"{row['count']} " if row["count"] else ""
                lines.append(f"  - {count}{row['label']}: {_fmt(row['amount'])}, plus "
                             f"fringe at {row['fringe_rate'] * 100:.0f}% "
                             f"= {_fmt(row['fringe'])}.")
            lines.append("")

        if L["C"]:
            lines.append(f"C. Fringe Benefits: {_fmt(L['C'])} total, applied per "
                         f"employee category at Morgan State's negotiated rates.")
            lines.append("")

        if L["D"]["total"]:
            lines.append("D. Equipment")
            for row in L["D"]["rows"]:
                if row["amount"] > 0:
                    lines.append(f"  - {row['description'] or 'Unnamed item'}: "
                                 f"{_fmt(row['amount'])}.")
            lines.append(f"  Total equipment: {_fmt(L['D']['total'])}.")
            lines.append("")

        if L["E"]["total"]:
            lines.append("E. Travel")
            if L["E"]["domestic"]:
                lines.append(f"  - Domestic: {_fmt(L['E']['domestic'])}. "
                             + "; ".join(r["description"] for r in L["E"]["domestic_rows"]
                                         if r["description"]))
            if L["E"]["international"]:
                lines.append(f"  - International: {_fmt(L['E']['international'])}. "
                             + "; ".join(r["description"]
                                         for r in L["E"]["international_rows"]
                                         if r["description"])
                             + " Foreign travel will use U.S.-flag air carriers as "
                               "required by the Fly America Act.")
            lines.append("")

        if L["F"]["total"]:
            lines.append(
                f"F. Participant Support: {_fmt(L['F']['total'])} for "
                f"{L['F']['count']} participants — "
                + ", ".join(f"{k} {_fmt(L['F'][k])}"
                            for k in ("stipends", "travel", "subsistence", "other")
                            if L["F"][k])
                + ". No F&A is charged on participant support costs.")
            lines.append("")

        if L["G"]["total"]:
            lines.append("G. Other Direct Costs")
            for key, label in G_ITEM_LINES:
                if L["G"][key]:
                    lines.append(f"  - {label}: {_fmt(L['G'][key])}.")
            for s in L["G"]["subawards"]["rows"]:
                if s["amount"] > 0:
                    note = ""
                    if s["amount"] > SUBAWARD_MTDC_CAP:
                        note = (f" Only the first {_fmt(SUBAWARD_MTDC_CAP)} is "
                                f"included in the F&A base.")
                    lines.append(f"  - Subaward to {s['organization'] or 'a subrecipient'}: "
                                 f"{_fmt(s['amount'])}.{note}")
            for it in L["G"]["other_rows"]:
                if it["amount"] > 0:
                    note = " (excluded from the F&A base)" if it["mtdc_exempt"] else ""
                    lines.append(f"  - {it['description'] or 'Other'}: "
                                 f"{_fmt(it['amount'])}{note}.")
            lines.append("")

        lines.append(
            f"Total direct costs for Year {yc['year']} are {_fmt(L['H'])}. "
            f"Facilities & Administrative costs are applied at the "
            f"{yc['fa']['label']} rate of {yc['fa']['rate'] * 100:.0f}% on the "
            f"modified total direct cost base of {_fmt(yc['mtdc']['base'])}, "
            f"yielding {_fmt(L['I'])}. Total for Year {yc['year']}: {_fmt(L['J'])}.")
        lines.append("")

    if len(computed["years"]) > 1:
        c = computed["cumulative"]["lines"]
        lines.append(
            f"Cumulative: total direct costs of {_fmt(c['H'])} and F&A of "
            f"{_fmt(c['I'])} across {len(computed['years'])} years, for a total "
            f"request of {_fmt(c['L'])}.")

    return "\n".join(lines).strip()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/nsf_budget.py backend/tests/test_nsf_budget.py
git commit -m "feat(nsf-budget): deterministic NSF budget justification"
```

---

## Task 11: The Excel export

**Files:**
- Create: `backend/services/nsf_budget_export.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_nsf_budget_export.py` (create)

**Interfaces:**
- Consumes: `compute_document`.
- Produces: `build_workbook(doc, computed=None) -> openpyxl.Workbook`, `workbook_bytes(doc, computed=None) -> bytes`.

- [ ] **Step 1: Add the dependency and install it**

Append to `backend/requirements.txt`:

```
openpyxl==3.1.5
```

Run: `cd backend && ../.venv/bin/python -m pip install openpyxl==3.1.5`
Expected: installs cleanly. (The venv's `pip` script has a stale shebang from an old project path — always invoke pip as `python -m pip`.)

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_nsf_budget_export.py`:

```python
"""The .xlsx export must be a LIVE workbook, not a picture of one -- ORA edits
a cell and the sheet re-totals. So subtotals are formulas, not baked values."""
import io

from openpyxl import load_workbook

from services import nsf_budget as nb
from services import nsf_budget_export as ex


def _doc():
    doc = nb.blank_document(years=2)
    for y in doc["years"]:
        y["senior"][0].update(name="Dr. Oladunni", base_salary=90_000,
                              appointment_basis="academic_9", acad=2)
        y["equipment"] = [{"description": "Confocal", "amount": 40_000}]
    return doc


def _loaded(doc, **kw):
    return load_workbook(io.BytesIO(ex.workbook_bytes(doc)), **kw)


def test_workbook_has_one_sheet_per_year_plus_cumulative_and_flags():
    wb = _loaded(_doc())
    assert wb.sheetnames == ["Year 1", "Year 2", "Cumulative", "Flags"]


def test_subtotals_are_formulas_not_baked_values():
    wb = _loaded(_doc())          # data_only=False is the default
    ws = wb["Year 1"]
    formulas = [c.value for row in ws.iter_rows() for c in row
                if isinstance(c.value, str) and c.value.startswith("=")]
    assert any(f.startswith("=SUM(") for f in formulas)


def test_the_header_block_carries_the_organization_and_pi():
    doc = _doc()
    doc["meta"]["pi_name"] = "Timothy Oladunni"
    ws = _loaded(doc)["Year 1"]
    text = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    assert "Morgan State University" in text
    assert "Timothy Oladunni" in text


def test_every_form_line_letter_appears_on_a_year_sheet():
    ws = _loaded(_doc())["Year 1"]
    text = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    for label in ["A.", "B.", "C.", "D.", "E.", "F.", "G.", "H.", "I.", "J.", "K.", "L.", "M."]:
        assert label in text


def test_the_flags_sheet_lists_findings_with_their_citation():
    doc = _doc()
    doc["years"][0]["fee"] = 5_000
    ws = _loaded(doc)["Flags"]
    text = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    assert "PAPPG 24-1 II.D.2.f(x)" in text


def test_a_blank_budget_still_exports():
    assert len(ex.workbook_bytes(nb.blank_document())) > 0
```

- [ ] **Step 3: Run to verify failure**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.nsf_budget_export'`

- [ ] **Step 4: Create `backend/services/nsf_budget_export.py`**

```python
"""Form 1030 as a live .xlsx — one sheet per year, plus Cumulative and Flags.

Subtotals are FORMULAS, not baked values: ORA changes one cell and the sheet
re-totals. That is the whole reason Excel was chosen over a static PDF.
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from services.nsf_budget import G_ITEM_LINES, OTHER_PERSONNEL_ROWS, compute_document

MONEY = '"$"#,##0'
_HEAD_FILL = PatternFill("solid", fgColor="0B2F5E")
_TOTAL_FILL = PatternFill("solid", fgColor="EEF2F7")
_HEAD_FONT = Font(color="FFFFFF", bold=True, size=11)
_BOLD = Font(bold=True)
_THIN = Side(style="thin", color="D0D7E2")
_BORDER = Border(bottom=_THIN)


def _money_cell(ws, row, value):
    c = ws.cell(row=row, column=4, value=value)
    c.number_format = MONEY
    return c


def _label(ws, row, text, indent=0, bold=False):
    c = ws.cell(row=row, column=1, value=text)
    c.alignment = Alignment(indent=indent)
    if bold:
        c.font = _BOLD
    return c


def _write_year(ws, meta, yc):
    """Lay one A-M year sheet out, returning nothing. Column D holds money."""
    L = yc["lines"]
    ws.column_dimensions["A"].width = 52
    for col in "BCD":
        ws.column_dimensions[col].width = 16

    ws["A1"] = "SUMMARY PROPOSAL BUDGET"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"Organization: {meta.get('organization', '')}"
    ws["A3"] = f"Principal Investigator / Project Director: {meta.get('pi_name', '')}"
    ws["A4"] = f"Duration (months): {meta.get('duration_months', '')}"
    ws["A5"] = f"Year {yc['year']}"
    ws["A5"].font = _BOLD

    r = 7
    hdr = ws.cell(row=r, column=1, value="NSF Funded Person-months / Funds Requested")
    hdr.fill, hdr.font = _HEAD_FILL, _HEAD_FONT
    for col in range(2, 5):
        ws.cell(row=r, column=col).fill = _HEAD_FILL
    ws.cell(row=r, column=2, value="MONTHS").font = _HEAD_FONT
    ws.cell(row=r, column=4, value="FUNDS REQUESTED").font = _HEAD_FONT

    r += 1
    _label(ws, r, "A. SENIOR/KEY PERSONNEL", bold=True)
    a_first = r + 1
    for row in L["A"]["rows"]:
        r += 1
        _label(ws, r, f"   {row['name']}"
                      + (f" - {row['role']}" if row["role"] else ""), indent=1)
        ws.cell(row=r, column=2, value=row["months_total"])
        _money_cell(ws, r, row["salary"])
    a_last = max(r, a_first)
    r += 1
    _label(ws, r, "   TOTAL SENIOR/KEY PERSONNEL", bold=True)
    _money_cell(ws, r, f"=SUM(D{a_first}:D{a_last})").font = _BOLD
    a_total_row = r

    r += 1
    _label(ws, r, "B. OTHER PERSONNEL", bold=True)
    b_first = r + 1
    for key, label, _has_months in OTHER_PERSONNEL_ROWS:
        r += 1
        row = next(x for x in L["B"]["rows"] if x["key"] == key)
        _label(ws, r, f"   ({row['count']}) {label}", indent=1)
        _money_cell(ws, r, row["amount"])
    b_last = r
    r += 1
    _label(ws, r, "   TOTAL SALARIES AND WAGES (A + B)", bold=True)
    _money_cell(ws, r, f"=D{a_total_row}+SUM(D{b_first}:D{b_last})").font = _BOLD
    ab_row = r

    r += 2
    _label(ws, r, "C. FRINGE BENEFITS", bold=True)
    _money_cell(ws, r, L["C"])
    c_row = r
    r += 1
    _label(ws, r, "   TOTAL SALARIES, WAGES AND FRINGE BENEFITS (A + B + C)", bold=True)
    _money_cell(ws, r, f"=D{ab_row}+D{c_row}").font = _BOLD
    abc_row = r

    r += 2
    _label(ws, r, "D. EQUIPMENT", bold=True)
    d_first = r + 1
    for item in L["D"]["rows"]:
        r += 1
        _label(ws, r, f"   {item['description'] or 'Unnamed item'}", indent=1)
        _money_cell(ws, r, item["amount"])
    d_last = max(r, d_first)
    r += 1
    _label(ws, r, "   TOTAL EQUIPMENT", bold=True)
    _money_cell(ws, r, f"=SUM(D{d_first}:D{d_last})").font = _BOLD
    d_row = r

    r += 2
    _label(ws, r, "E. TRAVEL", bold=True)
    r += 1
    _label(ws, r, "   1. DOMESTIC", indent=1)
    _money_cell(ws, r, L["E"]["domestic"])
    e_first = r
    r += 1
    _label(ws, r, "   2. INTERNATIONAL", indent=1)
    _money_cell(ws, r, L["E"]["international"])
    e_last = r
    r += 1
    _label(ws, r, "   TOTAL TRAVEL", bold=True)
    _money_cell(ws, r, f"=SUM(D{e_first}:D{e_last})").font = _BOLD
    e_row = r

    r += 2
    _label(ws, r, "F. PARTICIPANT SUPPORT COSTS", bold=True)
    f_first = r + 1
    for key, label in (("stipends", "1. STIPENDS"), ("travel", "2. TRAVEL"),
                       ("subsistence", "3. SUBSISTENCE"), ("other", "4. OTHER")):
        r += 1
        _label(ws, r, f"   {label}", indent=1)
        _money_cell(ws, r, L["F"][key])
    f_last = r
    r += 1
    _label(ws, r, f"   TOTAL NUMBER OF PARTICIPANTS ({L['F']['count']})"
                  f"  TOTAL PARTICIPANT COSTS", bold=True)
    _money_cell(ws, r, f"=SUM(D{f_first}:D{f_last})").font = _BOLD
    f_row = r

    r += 2
    _label(ws, r, "G. OTHER DIRECT COSTS", bold=True)
    g_first = r + 1
    for n, (key, label) in enumerate(G_ITEM_LINES, start=1):
        r += 1
        _label(ws, r, f"   {n}. {label.upper()}", indent=1)
        _money_cell(ws, r, L["G"][key])
    r += 1
    _label(ws, r, "   5. SUBAWARDS", indent=1)
    _money_cell(ws, r, L["G"]["subawards"]["total"])
    r += 1
    _label(ws, r, "   6. OTHER", indent=1)
    _money_cell(ws, r, L["G"]["other"])
    g_last = r
    r += 1
    _label(ws, r, "   TOTAL OTHER DIRECT COSTS", bold=True)
    _money_cell(ws, r, f"=SUM(D{g_first}:D{g_last})").font = _BOLD
    g_row = r

    r += 2
    _label(ws, r, "H. TOTAL DIRECT COSTS (A THROUGH G)", bold=True)
    _money_cell(ws, r, f"=D{abc_row}+D{d_row}+D{e_row}+D{f_row}+D{g_row}").font = _BOLD
    ws.cell(row=r, column=1).fill = _TOTAL_FILL
    h_row = r

    r += 1
    _label(ws, r, f"I. INDIRECT COSTS (F&A) — {yc['fa']['label']} at "
                  f"{yc['fa']['rate'] * 100:.0f}% of an MTDC base of "
                  f"{yc['mtdc']['base']:,.0f}", bold=True)
    _money_cell(ws, r, L["I"]).font = _BOLD
    i_row = r

    r += 1
    _label(ws, r, "J. TOTAL DIRECT AND INDIRECT COSTS (H + I)", bold=True)
    _money_cell(ws, r, f"=D{h_row}+D{i_row}").font = _BOLD
    j_row = r

    r += 1
    _label(ws, r, "K. FEE", bold=True)
    _money_cell(ws, r, L["K"])
    k_row = r

    r += 1
    _label(ws, r, "L. AMOUNT OF THIS REQUEST (J MINUS K)", bold=True)
    _money_cell(ws, r, f"=D{j_row}-D{k_row}").font = _BOLD
    ws.cell(row=r, column=1).fill = _TOTAL_FILL

    r += 1
    _label(ws, r, "M. COST SHARING PROPOSED LEVEL", bold=True)
    _money_cell(ws, r, L["M"])

    r += 2
    ws.cell(row=r, column=1,
            value="MTDC excludes equipment, participant support, the portion of each "
                  "subaward over $25,000, and items marked exempt (tuition remission, "
                  "scholarships, rent, patient care).").font = Font(italic=True, size=9)


def build_workbook(doc, computed=None):
    """A Workbook with one sheet per year, plus Cumulative and Flags."""
    computed = computed or compute_document(doc)
    meta = computed.get("meta") or {}

    wb = Workbook()
    wb.remove(wb.active)

    for yc in computed["years"]:
        _write_year(wb.create_sheet(f"Year {yc['year']}"), meta, yc)

    cum = dict(computed["cumulative"])
    cum["year"] = "Cumulative"
    cum.setdefault("fa", computed["years"][0]["fa"] if computed["years"] else
                   {"label": "", "rate": 0.0})
    ws = wb.create_sheet("Cumulative")
    _write_year(ws, meta, cum)
    ws["A5"] = "Cumulative (all years)"
    ws["A5"].font = _BOLD

    fws = wb.create_sheet("Flags")
    fws.column_dimensions["A"].width = 10
    fws.column_dimensions["B"].width = 8
    fws.column_dimensions["C"].width = 40
    fws.column_dimensions["D"].width = 60
    fws.column_dimensions["E"].width = 26
    for col, head in enumerate(["YEAR", "LINE", "FINDING", "DETAIL", "CITATION"], start=1):
        c = fws.cell(row=1, column=col, value=head)
        c.fill, c.font = _HEAD_FILL, _HEAD_FONT
    for n, f in enumerate(computed.get("flags") or [], start=2):
        fws.cell(row=n, column=1, value=f.get("year") or "all")
        fws.cell(row=n, column=2, value=f["line"])
        fws.cell(row=n, column=3, value=f"[{f['severity'].upper()}] {f['title']}")
        fws.cell(row=n, column=4, value=f.get("detail") or f["message"])
        fws.cell(row=n, column=5, value=f["citation"])
    return wb


def workbook_bytes(doc, computed=None):
    """The workbook as bytes, ready to stream from an endpoint."""
    buf = io.BytesIO()
    build_workbook(doc, computed).save(buf)
    return buf.getvalue()
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_nsf_budget_export.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/services/nsf_budget_export.py backend/tests/test_nsf_budget_export.py backend/requirements.txt
git commit -m "feat(nsf-budget): Form 1030 .xlsx export with live formulas"
```

---

## Task 12: API endpoints

**Files:**
- Modify: `backend/main.py` (the Budget Helper block, from the `/api/budget/rates` endpoint onward)
- Test: `backend/tests/test_budget_api_e2e.py` (append)

**Interfaces:**
- Consumes: `nsf_budget.blank_document`, `compute_document`, `add_year`, `draft_justification`; `nsf_budget_export.workbook_bytes`.
- Produces:
  - `GET /api/budget/nsf/template?years=N` → `{"document": <blank doc>}`
  - `POST /api/budget/nsf/compute` → the §4 computed shape
  - `POST /api/budget/nsf/justification` → `{"justification": str, "ai": bool}`
  - `GET /api/me/submissions/{id}/budget.xlsx` → streamed workbook
  - `GET`/`PUT /api/me/submissions/{id}/budget` become schema-aware.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_budget_api_e2e.py`:

```python
# --- NSF Form 1030 template ----------------------------------------------

def test_nsf_template_endpoint_returns_a_blank_document(ctx):
    client, headers = ctx
    r = client.get("/api/budget/nsf/template?years=3", headers=headers)
    assert r.status_code == 200
    doc = r.json()["document"]
    assert doc["schema"] == "nsf_1030"
    assert len(doc["years"]) == 3


def test_nsf_compute_endpoint_returns_years_cumulative_and_flags(ctx):
    client, headers = ctx
    doc = client.get("/api/budget/nsf/template", headers=headers).json()["document"]
    doc["years"][0]["other_direct"]["materials_supplies"] = [
        {"description": "Reagents", "amount": 10_000}]
    r = client.post("/api/budget/nsf/compute", json=doc, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["years"][0]["lines"]["I"] == 5_400.0
    assert "cumulative" in body and "flags" in body


def test_nsf_justification_endpoint_returns_text(ctx):
    client, headers = ctx
    doc = client.get("/api/budget/nsf/template", headers=headers).json()["document"]
    doc["years"][0]["senior"][0].update(name="Dr. Oladunni", base_salary=90_000,
                                        appointment_basis="academic_9", acad=2)
    r = client.post("/api/budget/nsf/justification",
                    json={"inputs": doc, "use_ai": False}, headers=headers)
    assert r.status_code == 200
    assert "Dr. Oladunni" in r.json()["justification"]


# --- persistence ----------------------------------------------------------

def test_saving_and_loading_an_nsf_budget_round_trips(ctx):
    client, headers, sub_id = ctx_with_submission(ctx)
    doc = client.get("/api/budget/nsf/template", headers=headers).json()["document"]
    doc["years"][0]["equipment"] = [{"description": "Confocal", "amount": 40_000}]
    assert client.put(f"/api/me/submissions/{sub_id}/budget",
                      json={"inputs": doc}, headers=headers).status_code == 200
    body = client.get(f"/api/me/submissions/{sub_id}/budget", headers=headers).json()
    assert body["schema"] == "nsf_1030"
    assert body["inputs"]["years"][0]["equipment"][0]["amount"] == 40_000
    assert body["computed"]["years"][0]["lines"]["D"]["total"] == 40_000.0


def test_a_generic_budget_with_no_schema_key_still_loads(ctx):
    """THE regression guard. Existing saved budgets must not change behaviour."""
    client, headers, sub_id = ctx_with_submission(ctx)
    assert client.put(f"/api/me/submissions/{sub_id}/budget",
                      json={"inputs": WORKED}, headers=headers).status_code == 200
    body = client.get(f"/api/me/submissions/{sub_id}/budget", headers=headers).json()
    assert body.get("schema") in (None, "generic")
    assert body["computed"]["total"] > 0          # the generic response shape
    assert "personnel" in body["computed"]


# --- export ---------------------------------------------------------------

def test_xlsx_download_returns_a_workbook(ctx):
    client, headers, sub_id = ctx_with_submission(ctx)
    doc = client.get("/api/budget/nsf/template", headers=headers).json()["document"]
    client.put(f"/api/me/submissions/{sub_id}/budget",
               json={"inputs": doc}, headers=headers)
    r = client.get(f"/api/me/submissions/{sub_id}/budget.xlsx", headers=headers)
    assert r.status_code == 200
    assert r.content[:2] == b"PK"                 # a zip container, i.e. xlsx
    assert "spreadsheetml" in r.headers["content-type"]


def test_xlsx_download_requires_auth(ctx):
    client, headers, sub_id = ctx_with_submission(ctx)
    assert client.get(f"/api/me/submissions/{sub_id}/budget.xlsx").status_code == 401
```

Add this helper near the top of the file, after the `ctx` fixture, so the tests above have a submission to work with:

```python
def ctx_with_submission(ctx):
    """Create a proposal for the fixture user and return (client, headers, id)."""
    client, headers = ctx
    r = client.post("/api/me/submissions",
                    json={"title": "NSF CAREER", "sponsor": "NSF"}, headers=headers)
    assert r.status_code in (200, 201)
    return client, headers, r.json()["id"]
```

> If the existing `ctx` fixture already yields a submission id, use it and delete this helper. Read the fixture before writing the tests.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && DATABASE_URL="sqlite:///:memory:" TRUSTED_HOSTS="testserver,localhost,127.0.0.1" ../.venv/bin/python -m pytest tests/test_budget_api_e2e.py -v`
Expected: FAIL — 404 on the new routes.

- [ ] **Step 3: Add the new endpoints**

Insert after the existing `/api/budget/justification` endpoint in `main.py`:

```python
# ---------------------------------------------------------------------------
# NSF Form 1030 budget template. Same contract as the generic Budget Helper:
# every number is computed by services/nsf_budget.py, never by the LLM.
# ---------------------------------------------------------------------------

@app.get("/api/budget/nsf/template")
async def nsf_budget_template(years: int = 1, user: dict = Depends(get_current_user)):
    """A blank Form 1030 document so the frontend never hardcodes the structure."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    from services.nsf_budget import blank_document
    return {"document": blank_document(years=years)}


@app.post("/api/budget/nsf/compute")
async def nsf_budget_compute(payload: dict, user: dict = Depends(get_current_user)):
    """Stateless: compute every year sheet, the cumulative rollup, and the flags."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    from services.nsf_budget import compute_document
    return compute_document(payload.get("inputs", payload) or {})


@app.post("/api/budget/nsf/add-year")
async def nsf_budget_add_year(payload: dict, user: dict = Depends(get_current_user)):
    """Clone the last year, escalating salary bases only."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    from services.nsf_budget import add_year
    doc = payload.get("inputs", payload) or {}
    return {"document": add_year(doc, escalation_pct=payload.get("escalation_pct"))}


@app.post("/api/budget/nsf/justification")
async def nsf_budget_justification(payload: dict, user: dict = Depends(get_current_user)):
    """NSF budget justification: deterministic template, AI-polished, HARD fallback."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    from services.nsf_budget import compute_document, draft_justification
    doc = payload.get("inputs", payload) or {}
    computed = compute_document(doc)
    template = draft_justification(doc, computed)
    if not payload.get("use_ai", True):
        return {"justification": template, "ai": False}
    try:
        from services import gemini_client
        prompt = (
            "You are a grants budget specialist at Morgan State University. Rewrite "
            "the NSF budget justification below into clear, professional, "
            "sponsor-ready prose. RULES: Do NOT change, add, or remove ANY dollar "
            "figure, percentage, person-month count, name, or rate -- reproduce them "
            "EXACTLY. Do not invent line items. Keep the year-by-year structure.\n\n"
            f"{template}"
        )
        text_out = (gemini_client.generate_text(
            prompt, temperature=0.2, max_output_tokens=1600) or "").strip()
        if text_out:
            return {"justification": text_out, "ai": True, "template": template}
    except Exception as e:
        print(f"[NSF-BUDGET] AI justification failed, using template: {e}")
    return {"justification": template, "ai": False}


@app.get("/api/me/submissions/{submission_id}/budget.xlsx")
async def download_submission_budget_xlsx(
    submission_id: int,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stream the saved NSF budget as a Form 1030 workbook."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id,
                                            user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    raw = getattr(sub, "budget_json", None)
    try:
        doc = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        doc = {}
    from services.nsf_budget import SCHEMA, blank_document
    if doc.get("schema") != SCHEMA:
        raise HTTPException(400, "This proposal does not have an NSF Form 1030 budget.")
    from services.nsf_budget_export import workbook_bytes
    data = workbook_bytes(doc)
    safe = "".join(ch for ch in (sub.title or "budget") if ch.isalnum() or ch in " -_")[:60]
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe or "budget"}.xlsx"'},
    )
```

Confirm `Response` is imported in `main.py` (`from fastapi import Response`); add it to the existing FastAPI import if it is not there.

- [ ] **Step 4: Make the load/save endpoints schema-aware**

In `get_submission_budget`, replace the compute call:

```python
    from services.nsf_budget import SCHEMA as NSF_SCHEMA, compute_document
    from services.budget_helper import compute_budget
    if inputs.get("schema") == NSF_SCHEMA:
        return {"schema": NSF_SCHEMA, "inputs": inputs,
                "computed": compute_document(inputs)}
    return {"schema": "generic", "inputs": inputs, "computed": compute_budget(inputs)}
```

In `save_submission_budget`, do the same for the validation compute:

```python
    from services.nsf_budget import SCHEMA as NSF_SCHEMA, compute_document
    from services.budget_helper import compute_budget
    inputs = payload.get("inputs", payload) or {}
    if inputs.get("schema") == NSF_SCHEMA:
        computed = compute_document(inputs)       # validate it computes cleanly
    else:
        computed = compute_budget(inputs)
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && DATABASE_URL="sqlite:///:memory:" TRUSTED_HOSTS="testserver,localhost,127.0.0.1" ../.venv/bin/python -m pytest tests/test_budget_api_e2e.py -v`
Expected: PASS, **including `test_a_generic_budget_with_no_schema_key_still_loads`.**

- [ ] **Step 6: Run the FULL backend suite**

Run: `cd backend && DATABASE_URL="sqlite:///:memory:" TRUSTED_HOSTS="testserver,localhost,127.0.0.1" ../.venv/bin/python -m pytest -q`
Expected: every test passes. Record the new total in the commit message.

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/tests/test_budget_api_e2e.py
git commit -m "feat(nsf-budget): template/compute/add-year/justification/xlsx endpoints"
```

---

## Task 13: The frontend NSF sheet

**Files:**
- Create: `frontend/src/components/NsfBudgetSheet.jsx`, `frontend/src/components/NsfBudgetSheet.css`
- Modify: `frontend/src/components/BudgetHelperModal.jsx`

**Interfaces:**
- Consumes: the Task 12 endpoints.
- Produces: `<NsfBudgetSheet submission doc onChange computed flags />`, default-exported.

- [ ] **Step 1: Add the template selector to `BudgetHelperModal.jsx`**

Add state and a selector rendered above the existing body:

```jsx
const [template, setTemplate] = useState("generic");   // "generic" | "nsf_1030"
const [nsfDoc, setNsfDoc] = useState(null);
```

In the load effect, detect the saved schema and set both:

```jsx
if (budgetData?.schema === "nsf_1030" || budgetData?.inputs?.schema === "nsf_1030") {
  setTemplate("nsf_1030");
  setNsfDoc(budgetData.inputs);
  setComputed(budgetData.computed);
}
```

Render the selector, and switch the body:

```jsx
<div className="bh-template-row">
  <label>Template</label>
  <select value={template} onChange={(e) => switchTemplate(e.target.value)}>
    <option value="generic">Generic</option>
    <option value="nsf_1030">NSF (Form 1030)</option>
  </select>
</div>
```

```jsx
async function switchTemplate(next) {
  setTemplate(next);
  if (next === "nsf_1030" && !nsfDoc) {
    const r = await fetch(`${API_BASE}/api/budget/nsf/template?years=1`, { headers: authHeaders() });
    if (r.ok) setNsfDoc((await r.json()).document);
  }
}
```

```jsx
{template === "nsf_1030"
  ? <NsfBudgetSheet submission={submission} doc={nsfDoc} onChange={setNsfDoc}
                    computed={computed} />
  : /* the existing generic body, unchanged */ null}
```

- [ ] **Step 2: Create `NsfBudgetSheet.jsx`**

Structure — write it in this order, keeping the file focused on rendering:

```jsx
import React, { useEffect, useRef, useState } from "react";
import { getApiBase } from "../lib/apiBase";
import "./NsfBudgetSheet.css";

const API_BASE = getApiBase();
const fmt = (n) => `$${Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const authHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${localStorage.getItem("token")}`,
});

export default function NsfBudgetSheet({ submission, doc, onChange, computed: outerComputed }) {
  const [activeYear, setActiveYear] = useState(1);   // 0 means the Cumulative tab
  const [computed, setComputed] = useState(outerComputed || null);
  const debounceRef = useRef(null);

  // Debounced recompute — keeps the last good totals if a request fails.
  useEffect(() => {
    if (!doc) return;
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const r = await fetch(`${API_BASE}/api/budget/nsf/compute`, {
          method: "POST", headers: authHeaders(), body: JSON.stringify({ inputs: doc }),
        });
        if (r.ok) setComputed(await r.json());
      } catch { /* keep the last good totals */ }
    }, 300);
    return () => clearTimeout(debounceRef.current);
  }, [doc]);

  // ... year tabs, the A–M rows, the sticky rail, the flags panel
}
```

Requirements the implementation must satisfy:
- **Year tabs** rendering `Year 1 … Year N`, a `+ Add year` button that POSTs `/api/budget/nsf/add-year` with the current doc and replaces it with the response, and a `Cumulative` tab.
- **The cumulative tab is read-only** — render values, no inputs.
- **Editable cells are inputs; computed cells are read-only and shaded.** A PI must be able to see at a glance which numbers a human owns.
- Empty amounts render as blank (`value={row.amount ?? ""}`), never as `0`.
- **Sticky right rail** showing H, I, J, L and the cap badge.
- **Flags panel** grouping by line, red for `warn` and grey for `info`, each showing `title`, `detail`, and `citation`.
- **Download** must `fetch()` the `.xlsx` with the auth header and save the response as a **same-origin blob** — an `<a download>` pointing at the backend origin is silently ignored cross-origin:

```jsx
async function downloadXlsx() {
  const r = await fetch(`${API_BASE}/api/me/submissions/${submission.id}/budget.xlsx`,
                        { headers: { Authorization: `Bearer ${localStorage.getItem("token")}` } });
  if (!r.ok) { setError("Save the budget before downloading."); return; }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${submission.title || "budget"}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 3: Create `NsfBudgetSheet.css`**

Follow `BudgetHelperModal.css` for colours and spacing. Required rules:
- `.nsf-sheet` grid with a label column and a right-aligned money column.
- `.nsf-row-computed` — shaded background, no input border.
- `.nsf-line-total` — bold, top border.
- `.nsf-year-tabs` — horizontal, active tab underlined.
- `.nsf-flag.warn` red left border; `.nsf-flag.info` grey.
- `.nsf-rail` sticky, matching the generic helper's summary panel.
- A `@media (max-width: 768px)` block collapsing the rail below the sheet.

- [ ] **Step 4: Verify in the real browser**

Start the stack (three terminals):

```bash
cd backend && ../.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 5002
cd frontend && npm run dev -- --port 3001
```

Then, per the `ora-deploy-discipline` skill, drive the page with the **Playwright MCP** — not curl:
1. Log in, open a proposal, click **Build budget**.
2. Switch the template to **NSF (Form 1030)**.
3. Enter the worked example from Task 5 and confirm the rail reads **H 196,100 / I 51,894 / L 247,994**.
4. Click **+ Add year**; confirm the Year 2 base salary escalated by 3% and equipment did not.
5. Open **Cumulative**; confirm it sums both years and its inputs are read-only.
6. Set a senior person to 3 summer months; confirm the red two-month flag appears with its citation and that **saving still works**.
7. Save, then **Download .xlsx**; open the file and confirm the totals and that subtotal cells show formulas.
8. Check the browser console for errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/NsfBudgetSheet.jsx frontend/src/components/NsfBudgetSheet.css frontend/src/components/BudgetHelperModal.jsx
git commit -m "feat(nsf-budget): Form 1030 sheet UI with year tabs, flags, xlsx download"
```

---

## Task 14: Documentation

**Files:**
- Modify: `docs/features/budget-helper.md`, `CLAUDE.md`

- [ ] **Step 1: Document the NSF template**

Add a section to `docs/features/budget-helper.md` covering: the two templates and how the `schema` key selects them; the A–M structure; person-months and the appointment bases; per-row fringe into line C; the MTDC exclusions including the `mtdc_exempt` checkbox and why it exists; the rules table with citations; the Excel export; and the two open items (Morgan's capitalization level, and confirming the rates against ORA's current rate sheet).

- [ ] **Step 2: Update `CLAUDE.md`**

Add a dated entry to the current deployment state describing the feature, the new endpoints, the new dependency, the new test count, and the spec/plan paths.

- [ ] **Step 3: Run the full suite one last time**

Run: `cd backend && DATABASE_URL="sqlite:///:memory:" TRUSTED_HOSTS="testserver,localhost,127.0.0.1" ../.venv/bin/python -m pytest -q`
Expected: all pass. Put the exact count in the commit message.

- [ ] **Step 4: Commit**

```bash
git add docs/features/budget-helper.md CLAUDE.md
git commit -m "docs(nsf-budget): document the Form 1030 template"
```

---

## Deployment (only when the user asks)

Do not deploy without being asked. When asked:

1. `bash scripts/deploy_backend.sh` — the env-preserving path. It captures the live env and `min-instances`, deploys, restores them, and verifies health. Do **not** call `deploy-cloudrun.sh` directly.
2. Confirm `openpyxl` made it into the image — the container fails to import `nsf_budget_export` without it.
3. Deploy the frontend **separately**: `deploy-cloudrun.sh frontend`. The script silently ignores a second target.
4. Verify the UI in **incognito** — the PWA service worker serves the previous bundle to returning users.

---

## Self-Review Notes

- **Spec coverage:** §3 data model → Tasks 2–4; §3.6 rollup and §3.7 escalation → Tasks 5–6; §4 response shape → Task 5; §5 rules → Tasks 7–9; §6 API → Task 12; §7 frontend → Task 13; §8 export → Task 11; §9 justification → Task 10; §10 testing is distributed across every task; §11 risks → Task 14 docs and the deployment section.
- **Naming consistency:** `mtdc_and_fa`, `compute_personnel`, `compute_direct_lines`, `compute_sheet`, `compute_document`, `add_year`, `draft_justification`, `evaluate`, `build_workbook`, `workbook_bytes` are used identically in every task that references them.
- **Known gap to resolve during Task 12:** the existing `ctx` fixture in `test_budget_api_e2e.py` may already create a submission. Read it before writing the new tests and drop the `ctx_with_submission` helper if it is redundant.
