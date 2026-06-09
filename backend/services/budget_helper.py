"""Budget Helper — deterministic grant-budget math (2026-06-09).

The trustworthy core of the Budget Helper. EVERY number here is computed by code,
never by the LLM, so a PI can rely on it. The companion AI step
(`draft_justification`) only writes prose and is fed these figures verbatim.

The federal rule that trips people up is **F&A (indirect cost) is charged on the
MODIFIED total direct costs (MTDC)** — not the whole budget. MTDC excludes
equipment, participant support, and the portion of EACH subaward over $25,000.

Rates are the real Morgan State figures, sourced from the KB:
  backend/kb_structured/pre_award/fanda_cost_rates/pre_award_fanda_cost_rates.json
  backend/kb_structured/pre_award/fringe_benefit_rate/pre_award_fringe_benefit_rate.json
Keep these in sync if Morgan renegotiates its rate agreement.
"""

from __future__ import annotations

# ── Rate tables (label, decimal rate) ──────────────────────────────────────
DEFAULT_FA_YEAR = "fy_2025_2026"
DEFAULT_FA_KEY = "organized_research_on_campus"
DEFAULT_FRINGE = "faculty_ay"
SUBAWARD_MTDC_CAP = 25_000.0  # only the first $25k of each subaward is in MTDC

FA_RATES = {
    "fy_2025_2026": {
        "organized_research_on_campus": ("Organized Research (On-Campus)", 0.54),
        "instruction_on_campus": ("Instruction (On-Campus)", 0.64),
        "other_sponsored_activity_on_campus": ("Other Sponsored Activity (On-Campus)", 0.42),
        "off_campus": ("All Programs (Off-Campus)", 0.26),
    },
    "fy_2024_2025": {
        "organized_research_on_campus": ("Organized Research (On-Campus)", 0.53),
        "instruction_on_campus": ("Instruction (On-Campus)", 0.64),
        "other_sponsored_activity_on_campus": ("Other Sponsored Activity (On-Campus)", 0.42),
        "off_campus": ("All Programs (Off-Campus)", 0.26),
    },
}

FRINGE_RATES = {
    "faculty_ay": ("Faculty (Academic Year)", 0.42),
    "faculty_summer": ("Faculty (Summer)", 0.09),
    "full_time": ("Full-Time / Non-Contractual", 0.42),
    "contractual": ("Contractual (<6 mo or <30 hr/wk)", 0.09),
}


# ── input coercion (never crash on junk; warn instead) ─────────────────────
def _money(v, warnings, field):
    """Coerce to a non-negative float (2dp). Junk/negative -> 0 with a warning."""
    if v in (None, ""):
        return 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        warnings.append(f"Could not read {field} value '{v}'; using $0.")
        return 0.0
    if x < 0:
        warnings.append(f"{field.capitalize()} was negative; using $0.")
        return 0.0
    return round(x, 2)


def _effort(v, warnings):
    """Coerce % effort to 0–100."""
    if v in (None, ""):
        return 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        warnings.append(f"Could not read effort '{v}'; using 0%.")
        return 0.0
    if x < 0:
        warnings.append("Effort was negative; using 0%.")
        return 0.0
    if x > 100:
        warnings.append("Effort over 100%; clamped to 100%.")
        return 100.0
    return x


def compute_budget(inputs: dict) -> dict:
    """Compute a full grant-budget breakdown from raw line-item inputs.

    inputs (all optional):
      people: [{name, base_salary, effort_pct, fringe}]   fringe in FRINGE_RATES
      equipment, travel, supplies, participant_support, other: numbers
      subawards: [number, ...]
      fa_rate_key (FA_RATES[year] key), fa_year (FA_RATES key)
      cap: sponsor total cap (number, optional)

    Returns the breakdown + MTDC base + F&A + total + cap check + warnings.
    """
    inputs = inputs or {}
    warnings: list[str] = []

    # F&A rate selection
    year = inputs.get("fa_year") or DEFAULT_FA_YEAR
    if year not in FA_RATES:
        warnings.append(f"Unknown F&A year '{year}'; using {DEFAULT_FA_YEAR}.")
        year = DEFAULT_FA_YEAR
    year_rates = FA_RATES[year]
    fa_key = inputs.get("fa_rate_key") or DEFAULT_FA_KEY
    if fa_key not in year_rates:
        warnings.append(f"Unknown F&A rate '{fa_key}'; using {DEFAULT_FA_KEY}.")
        fa_key = DEFAULT_FA_KEY
    fa_label, fa_rate = year_rates[fa_key]

    # Personnel
    personnel = []
    for p in inputs.get("people") or []:
        p = p or {}
        base = _money(p.get("base_salary"), warnings, "base salary")
        effort = _effort(p.get("effort_pct"), warnings)
        salary = round(base * effort / 100.0, 2)
        fkey = p.get("fringe") or DEFAULT_FRINGE
        if fkey not in FRINGE_RATES:
            warnings.append(f"Unknown fringe category '{fkey}'; using {DEFAULT_FRINGE}.")
            fkey = DEFAULT_FRINGE
        flabel, frate = FRINGE_RATES[fkey]
        fringe = round(salary * frate, 2)
        personnel.append({
            "name": (p.get("name") or "").strip() or "Unnamed",
            "base_salary": base,
            "effort_pct": effort,
            "salary": salary,
            "fringe": fringe,
            "fringe_rate": frate,
            "fringe_key": fkey,
            "fringe_label": flabel,
            "subtotal": round(salary + fringe, 2),
        })
    personnel_total = round(sum(pp["subtotal"] for pp in personnel), 2)

    # Other direct-cost categories
    equipment = _money(inputs.get("equipment"), warnings, "equipment")
    travel = _money(inputs.get("travel"), warnings, "travel")
    supplies = _money(inputs.get("supplies"), warnings, "supplies")
    participant = _money(inputs.get("participant_support"), warnings, "participant support")
    other = _money(inputs.get("other"), warnings, "other")
    subawards = [_money(s, warnings, "subaward") for s in (inputs.get("subawards") or [])]
    subawards_total = round(sum(subawards), 2)

    # Total Direct Costs (TDC)
    direct = round(
        personnel_total + equipment + travel + supplies + participant + other + subawards_total, 2
    )

    # Modified Total Direct Costs (MTDC) — the base F&A is charged on
    sub_over_25k = round(sum(max(0.0, s - SUBAWARD_MTDC_CAP) for s in subawards), 2)
    mtdc = round(direct - equipment - participant - sub_over_25k, 2)
    if mtdc < 0:
        mtdc = 0.0

    fa_amount = round(mtdc * fa_rate, 2)
    total = round(direct + fa_amount, 2)

    # Sponsor cap check
    raw_cap = inputs.get("cap")
    cap_val = _money(raw_cap, warnings, "cap") if raw_cap not in (None, "") else 0.0
    if not cap_val:
        cap_status, cap_overage, cap_out = "none", 0.0, None
    elif total > cap_val:
        cap_status, cap_overage, cap_out = "over", round(total - cap_val, 2), cap_val
    else:
        cap_status, cap_overage, cap_out = "ok", 0.0, cap_val

    return {
        "personnel": personnel,
        "personnel_total": personnel_total,
        "equipment": equipment,
        "travel": travel,
        "supplies": supplies,
        "participant_support": participant,
        "other": other,
        "subawards": subawards,
        "subawards_total": subawards_total,
        "direct_costs": direct,
        "mtdc_base": mtdc,
        "mtdc_exclusions": {
            "equipment": equipment,
            "participant_support": participant,
            "subaward_over_25k": sub_over_25k,
        },
        "fa_year": year,
        "fa_rate_key": fa_key,
        "fa_rate": fa_rate,
        "fa_rate_label": fa_label,
        "fa_amount": fa_amount,
        "total": total,
        "cap": cap_out,
        "cap_status": cap_status,
        "cap_overage": cap_overage,
        "warnings": warnings,
    }


def rate_options() -> dict:
    """Expose the rate tables so the UI can render the F&A / fringe selectors."""
    return {
        "fa_rates": {
            yr: [{"key": k, "label": lbl, "rate": rate} for k, (lbl, rate) in rates.items()]
            for yr, rates in FA_RATES.items()
        },
        "fringe_rates": [
            {"key": k, "label": lbl, "rate": rate} for k, (lbl, rate) in FRINGE_RATES.items()
        ],
        "defaults": {"fa_year": DEFAULT_FA_YEAR, "fa_rate_key": DEFAULT_FA_KEY, "fringe": DEFAULT_FRINGE},
    }


def _fmt(amount: float) -> str:
    return f"${amount:,.0f}"


def draft_justification(budget: dict) -> str:
    """Deterministic budget-justification template built from the computed figures.

    Numbers come straight from `budget` (never invented). The AI-polished version
    is layered on at the endpoint with a HARD fallback to this template, so a
    justification always returns even if the LLM is unavailable.
    """
    lines = ["BUDGET JUSTIFICATION", ""]
    if budget.get("personnel"):
        lines.append("Personnel")
        for p in budget["personnel"]:
            lines.append(
                f"  • {p['name']}: {p['effort_pct']:.0f}% effort at a base salary of "
                f"{_fmt(p['base_salary'])} = {_fmt(p['salary'])} requested salary, plus "
                f"{p['fringe_label']} fringe at {p['fringe_rate']*100:.0f}% = {_fmt(p['fringe'])}."
            )
        lines.append(f"  Personnel subtotal: {_fmt(budget['personnel_total'])}.")
        lines.append("")
    for key, label in [
        ("equipment", "Equipment"), ("travel", "Travel"), ("supplies", "Materials & Supplies"),
        ("participant_support", "Participant Support"), ("other", "Other Direct Costs"),
    ]:
        if budget.get(key):
            lines.append(f"{label}: {_fmt(budget[key])} requested.")
    if budget.get("subawards_total"):
        lines.append(f"Subawards: {_fmt(budget['subawards_total'])} total.")
    lines.append("")
    lines.append(
        f"Total direct costs are {_fmt(budget['direct_costs'])}. Facilities & "
        f"Administrative costs are applied at the {budget['fa_rate_label']} rate of "
        f"{budget['fa_rate']*100:.0f}% on the modified total direct cost base of "
        f"{_fmt(budget['mtdc_base'])} (excluding equipment, participant support, and the "
        f"portion of each subaward over $25,000), yielding {_fmt(budget['fa_amount'])}. "
        f"The total project cost is {_fmt(budget['total'])}."
    )
    return "\n".join(lines)
