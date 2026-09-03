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
