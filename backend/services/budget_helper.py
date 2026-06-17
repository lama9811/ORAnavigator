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

# ── Plain-language coaching for the UI tooltips (Phase 1: Budget Coaching) ──
# Static, advisory text only -- no math, no sponsor-specific rules.
CATEGORY_GUIDANCE = {
    "personnel": "Salary + fringe for people working on the project. Requested salary = base salary x effort %. Subject to F&A.",
    "equipment": "A single item costing $5,000+ that lasts more than a year. Equipment is F&A-exempt -- no indirect costs are charged on it.",
    "travel": "Project travel: conferences, fieldwork, collaboration trips. Subject to F&A.",
    "supplies": "Consumables below the equipment threshold -- lab supplies, software, small devices. Subject to F&A.",
    "participant_support": "Money paid TO participants/trainees (stipends, their travel, registration) -- NOT your project staff. F&A-exempt; keep it on its own line.",
    "other": "Direct costs that don't fit the other lines (publication fees, tuition remission, etc.). Subject to F&A.",
    "subawards": "Funds passed to a collaborating institution that does part of the work. Only the first $25,000 of EACH subaward is subject to F&A.",
}
FA_GUIDANCE = (
    "Pick the rate that matches the work: 'Organized Research' for a research project, "
    "'Instruction' for a teaching/training grant, 'Other Sponsored Activity' for "
    "service/outreach, or 'Off-Campus' if most of the work happens away from Morgan's campus."
)
FRINGE_GUIDANCE = (
    "Choose by how the person is appointed: Faculty (Academic Year) and Full-Time staff "
    "are ~42%; Faculty (Summer) and short-term Contractual appointments are ~9%."
)

# Tunable thresholds for the advisory sanity checks. Advisory only -- they never
# change a number or block saving; they just prompt the PI to double-check.
_EQUIPMENT_PCT_FLAG = 0.40   # equipment > 40% of direct costs is unusual
_TRAVEL_PCT_FLAG = 0.25      # travel > 25% of direct costs is unusual


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

    result = {
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
    # Phase 1 coaching layer (additive, advisory only -- never changes the math
    # above). Both are pure functions of the computed `result`.
    result["advisories"] = budget_advisories(result)
    result["trim_suggestions"] = suggest_trims(result)
    return result


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
        "category_guidance": dict(CATEGORY_GUIDANCE),
        "fa_guidance": FA_GUIDANCE,
        "fringe_guidance": FRINGE_GUIDANCE,
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


# ── Phase 1: Budget Coaching (advisory, deterministic) ─────────────────────

def budget_advisories(budget: dict) -> list[dict]:
    """Advisory sanity checks on a computed budget. Returns a list of
    {severity: "warn"|"info", field, message, fix}. These NEVER change a number
    and NEVER block saving -- they only prompt the PI to double-check. A clean,
    typical budget returns []."""
    out: list[dict] = []
    direct = budget.get("direct_costs") or 0.0
    if direct <= 0:
        return out

    equipment = budget.get("equipment") or 0.0
    if equipment and equipment / direct > _EQUIPMENT_PCT_FLAG:
        out.append({
            "severity": "warn", "field": "equipment",
            "message": (f"Equipment is {equipment / direct * 100:.0f}% of your direct "
                        f"costs ({_fmt(equipment)} of {_fmt(direct)}) -- that's unusually high."),
            "fix": "Confirm each item is real equipment ($5k+, lasts over a year). Reviewers question equipment-heavy budgets.",
        })

    travel = budget.get("travel") or 0.0
    if travel and travel / direct > _TRAVEL_PCT_FLAG:
        out.append({
            "severity": "warn", "field": "travel",
            "message": (f"Travel is {travel / direct * 100:.0f}% of your direct costs "
                        f"({_fmt(travel)}) -- higher than typical."),
            "fix": "Make sure each trip is justified and tied to the project.",
        })

    for p in budget.get("personnel") or []:
        if (p.get("base_salary") or 0) > 0 and (p.get("effort_pct") or 0) == 0:
            out.append({
                "severity": "warn", "field": "personnel",
                "message": f"{p.get('name', 'A person')} has a salary but 0% effort, so $0 is requested for them.",
                "fix": "Set the effort % (months of work ÷ appointment length), or remove the line.",
            })
        elif (p.get("effort_pct") or 0) > 0 and (p.get("base_salary") or 0) == 0:
            out.append({
                "severity": "warn", "field": "personnel",
                "message": f"{p.get('name', 'A person')} has effort but a $0 base salary, so $0 is requested for them.",
                "fix": "Enter their annual base salary so the requested amount can be computed.",
            })

    if direct > 0 and not (budget.get("personnel") or []):
        out.append({
            "severity": "info", "field": "personnel",
            "message": "No personnel are listed. Most proposals request salary for at least the PI.",
            "fix": "Add yourself (and any staff) under Personnel unless this budget is intentionally personnel-free.",
        })

    sub_total = budget.get("subawards_total") or 0.0
    if sub_total and sub_total > (direct - sub_total):
        out.append({
            "severity": "warn", "field": "subawards",
            "message": (f"Subawards ({_fmt(sub_total)}) are more than half of your direct costs."),
            "fix": "Confirm the lead institution holds enough of the work; sponsors expect the applicant to lead.",
        })

    return out


def suggest_trims(budget: dict) -> list[dict]:
    """When the budget is OVER the sponsor cap, return concrete reductions that
    would bring it under. Cuts come from the flexible, F&A-eligible lines first
    (travel -> supplies -> other); since those are in the MTDC base, cutting $1
    saves $1 x (1 + F&A rate). Returns [] when not over cap. Deterministic."""
    if budget.get("cap_status") != "over":
        return []
    overage = budget.get("cap_overage") or 0.0       # dollars over cap, in TOTAL terms
    fa_rate = budget.get("fa_rate") or 0.0
    if overage <= 0:
        return []

    # Track the gap in TOTAL dollars. Travel/supplies/other are MTDC-eligible, so
    # cutting $1 there saves $(1 + fa_rate) of total -> we need to cut fewer real
    # dollars than the overage. (Equipment/participant carry no F&A, handled in the
    # fallback, where the gap is expressed conservatively in total dollars.)
    save_per_dollar = 1.0 + fa_rate
    out: list[dict] = []
    remaining_total = overage
    for key, label in [("travel", "Travel"), ("supplies", "Materials & supplies"),
                       ("other", "Other direct costs")]:
        if remaining_total <= 0:
            break
        avail = budget.get(key) or 0.0
        if avail <= 0:
            continue
        max_save = avail * save_per_dollar
        save = min(max_save, remaining_total)
        cut = round(save / save_per_dollar, 2)
        if cut <= 0:
            continue
        out.append({
            "line": label,
            "reduce_by": cut,
            "rationale": (f"Lowering {label.lower()} by {_fmt(cut)} removes about "
                          f"{_fmt(round(cut * save_per_dollar, 2))} from the total (the cut plus its F&A)."),
        })
        remaining_total = round(remaining_total - cut * save_per_dollar, 2)

    if remaining_total > 0:
        out.append({
            "line": "Personnel effort, equipment, or subawards",
            "reduce_by": round(remaining_total, 2),
            "rationale": (f"Travel, supplies, and other aren't enough -- you still need to remove "
                          f"about {_fmt(round(remaining_total, 2))} more from the total. Equipment and "
                          f"participant support carry no F&A (cut that full amount); effort and "
                          f"subawards carry F&A, so a little less goes further."),
        })
    return out
