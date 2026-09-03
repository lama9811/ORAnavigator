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


# ── Lines A, B, C ──────────────────────────────────────────────────────────
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

    Salary comes from person-months: the monthly rate is the base salary
    divided by 9 (academic appointment) or 12 (calendar). Fringe is computed
    per row at that row's own category rate, and line C is their sum -- the
    form shows one number, but Morgan's rates differ by category (42% vs 9%),
    and mixing them by hand is where fringe errors come from.
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
        if fringe:
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
    c_total = round(sum(r["fringe"] for r in a_rows)
                    + sum(r["fringe"] for r in b_rows), 2)

    return {
        "A": {"rows": a_rows, "total": a_total},
        "B": {"rows": b_rows, "total": b_total},
        "C": c_total,
        "fringe_rows": fringe_rows,
        "salaries_and_wages": round(a_total + b_total, 2),
    }


# ── Lines D, E, F, G ───────────────────────────────────────────────────────
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
    """Lines D, E, F and G, plus the G.6 items exempted from the F&A base.

    NSF's form has no tuition line and Morgan books graduate tuition remission
    in G.6 alongside items that DO bear F&A, so the exemption is an explicit
    per-item flag the PI sets -- never a guess from the description text.
    """
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
    g, g_rows = {}, {}
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
