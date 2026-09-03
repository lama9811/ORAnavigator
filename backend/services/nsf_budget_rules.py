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

from services.nsf_budget import MAX_SENIOR_MONTHS

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
