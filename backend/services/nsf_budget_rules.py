"""NSF budget rules — data, not code buried in the math.

Each rule is a table entry plus one predicate. Adding a rule is one entry and
one function; it never touches the arithmetic. Every rule carries its PAPPG
24-1 citation, shown in the UI beside the flag.

Severities: "warn" (a rule is likely broken) and "info" (a requirement to be
satisfied elsewhere). NEITHER BLOCKS anything -- not editing, not computing,
not saving, not exporting.

Design: docs/superpowers/specs/2026-09-03-nsf-form-1030-budget-design.md
"""
from __future__ import annotations

from services.nsf_budget import (
    DEFAULT_CAPITALIZATION, FORM_ITEMISE_THRESHOLD, MAX_SENIOR_MONTHS,
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


# ── line D / E / F / G predicates ─────────────────────────────────────────

def _check_equipment_below_cap(ctx):
    level = float(ctx["settings"].get("capitalization_level") or DEFAULT_CAPITALIZATION)
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
]


def _flag(rule, detail, year):
    return {"id": rule["id"], "line": rule["line"], "severity": rule["severity"],
            "title": rule["title"], "message": rule["message"],
            "citation": rule["citation"], "scope": rule["scope"],
            "year": year, "detail": detail}


def evaluate(doc, computed):
    """Run every rule and return a flat list of flags. Never raises."""
    doc = doc or {}
    settings = computed.get("settings") or {}
    meta = computed.get("meta") or {}
    flags = []

    for rule in RULES:
        try:
            if rule["scope"] == "year":
                sheets = doc.get("years") or []
                for idx, year_computed in enumerate(computed.get("years") or []):
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
