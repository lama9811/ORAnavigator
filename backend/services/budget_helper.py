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


def _budget_table(year_results: list, multi: bool) -> dict:
    """Render-ready spreadsheet model built from one or more per-year computed
    budgets. Pure function of numbers the math core already produced -- it never
    computes a figure itself. Row order mirrors `budget_to_csv` so the on-screen
    grid and the CSV export stay consistent.

      single-year -> columns ["Amount"], one value per row.
      multi-year  -> columns ["Year 1", ..., "Year N", "Total"]; each row's
                     trailing cell is the sum of its year cells.

    Each row: {label, detail, values: [float, ...], kind: line|subtotal|total}.
    """
    yrs = year_results or []
    n = len(yrs)
    columns = ([f"Year {i + 1}" for i in range(n)] + ["Total"]) if multi else ["Amount"]
    rows: list = []
    if n == 0:
        return {"columns": columns, "rows": rows}

    def add(label: str, detail: str, values: list, kind: str = "line") -> None:
        vals = [round(float(v or 0), 2) for v in values]
        cells = (vals + [round(sum(vals), 2)]) if multi else vals
        rows.append({"label": label, "detail": detail, "values": cells, "kind": kind})

    first = yrs[0]

    # Personnel: salary + fringe per person, aligned by list index across years
    # (same people each year, only salaries escalate).
    for idx, p0 in enumerate(first.get("personnel") or []):
        def _person_cell(yr, field, i=idx):
            pl = yr.get("personnel") or []
            return pl[i][field] if i < len(pl) else 0
        name = p0.get("name") or f"Person {idx + 1}"
        add("Salary", f"{name} ({float(p0.get('effort_pct') or 0):.0f}% effort)",
            [_person_cell(yr, "salary") for yr in yrs])
        add("Fringe", f"{name} ({p0.get('fringe_label', '')}, {round(float(p0.get('fringe_rate') or 0) * 100)}%)",
            [_person_cell(yr, "fringe") for yr in yrs])

    # Other direct-cost categories (omit a category that's zero in every year).
    for key, label in (("equipment", "Equipment"), ("travel", "Travel"),
                       ("supplies", "Materials & supplies"),
                       ("participant_support", "Participant support"),
                       ("other", "Other")):
        vals = [yr.get(key) or 0 for yr in yrs]
        if any(vals):
            add(label, "", vals)

    # Subawards, aligned by index.
    for i in range(len(first.get("subawards") or [])):
        vals = []
        for yr in yrs:
            sl = yr.get("subawards") or []
            vals.append(sl[i] if i < len(sl) else 0)
        if any(vals):
            add("Subaward", f"#{i + 1}", vals)

    # Subtotals / total.
    add("Total direct costs", "", [yr.get("direct_costs", 0) for yr in yrs], kind="subtotal")
    fa_pct = round(float(first.get("fa_rate") or 0) * 100)
    add(f"F&A ({first.get('fa_rate_label', '')}, {fa_pct}%)", "",
        [yr.get("fa_amount", 0) for yr in yrs], kind="subtotal")
    add("TOTAL", "", [yr.get("total", 0) for yr in yrs], kind="total")

    return {"columns": columns, "rows": rows}


def compute_budget(inputs: dict) -> dict:
    """Compute a grant budget. Single-year by default; when `project_years` > 1,
    project the Year-1 line items across the project with `escalation_pct`
    applied to salaries and return per-year + cumulative totals under a
    `multi_year` key. All existing fields are unchanged; a render-ready `table`
    (spreadsheet model) is added on top for the UI's grid view."""
    inputs = inputs or {}
    try:
        years = int(inputs.get("project_years") or 1)
    except (TypeError, ValueError):
        years = 1
    if years <= 1:
        r = _compute_single(inputs)
        r["table"] = _budget_table([r], multi=False)
        return r

    try:
        esc = float(inputs.get("escalation_pct") or 0) / 100.0
    except (TypeError, ValueError):
        esc = 0.0
    years = min(years, 10)  # sanity cap

    # Year 1 drives the familiar top-level fields (with NO cap at this level --
    # the sponsor cap on a multi-year award is a PROJECT total, checked below).
    base = _compute_single({**inputs, "cap": None, "project_years": None})

    per_year, cum = [], {"direct_costs": 0.0, "fa_amount": 0.0, "total": 0.0}
    year_results = []   # full per-year breakdowns, retained for the spreadsheet grid
    for i in range(years):
        people = []
        for p in (inputs.get("people") or []):
            p2 = dict(p or {})
            try:
                p2["base_salary"] = round(float(p.get("base_salary") or 0) * ((1.0 + esc) ** i), 2)
            except (TypeError, ValueError):
                pass
            people.append(p2)
        cy = _compute_single({**inputs, "people": people, "cap": None, "project_years": None})
        year_results.append(cy)
        per_year.append({"year": i + 1, "direct_costs": cy["direct_costs"],
                         "fa_amount": cy["fa_amount"], "total": cy["total"]})
        for k in cum:
            cum[k] = round(cum[k] + cy[k], 2)

    try:
        cap = float(inputs.get("cap")) if inputs.get("cap") not in (None, "") else 0.0
    except (TypeError, ValueError):
        cap = 0.0
    if not cap:
        cap_status, cap_overage, cap_out = "none", 0.0, None
    elif cum["total"] > cap:
        cap_status, cap_overage, cap_out = "over", round(cum["total"] - cap, 2), cap
    else:
        cap_status, cap_overage, cap_out = "ok", 0.0, cap

    base["multi_year"] = {
        "project_years": years,
        "escalation_pct": round(esc * 100, 2),
        "years": per_year,
        "cumulative": cum,
        "cap": cap_out,
        "cap_status": cap_status,
        "cap_overage": cap_overage,
    }
    base["table"] = _budget_table(year_results, multi=True)
    return base


def _compute_single(inputs: dict) -> dict:
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
    """Deterministic budget-justification narrative in sponsor-ready PROSE
    (labeled paragraphs, full sentences) -- reads like what a PI pastes into a
    real proposal, not a terse bullet list.

    Every figure comes straight from `budget` (never invented). The AI-polish
    layer only refines wording and is forbidden to change a number; this
    template is the HARD fallback, so a complete justification always returns
    even when the LLM is unavailable or truncates.
    """
    paras = ["BUDGET JUSTIFICATION", ""]

    people = budget.get("personnel") or []
    if people:
        sents = []
        for p in people:
            sents.append(
                f"{p['name']} will devote {p['effort_pct']:.0f}% effort to the project. "
                f"Based on an annual base salary of {_fmt(p['base_salary'])}, the requested "
                f"salary is {_fmt(p['salary'])}. Fringe benefits are calculated at the "
                f"{p['fringe_label']} rate of {p['fringe_rate']*100:.0f}%, totaling "
                f"{_fmt(p['fringe'])}."
            )
        sents.append(
            f"Total personnel costs, including salary and fringe benefits, are "
            f"{_fmt(budget['personnel_total'])}."
        )
        paras.append("Personnel. " + " ".join(sents))
        paras.append("")

    cat_sents = []
    for key, label, note in [
        ("equipment", "Equipment", " This equipment is essential to the proposed work and, "
         "as equipment, is excluded from the F&A base."),
        ("travel", "Travel", " Travel supports project meetings and dissemination of results."),
        ("supplies", "Materials and supplies", " These cover consumables required for the "
         "proposed activities."),
        ("participant_support", "Participant support", " These costs support project "
         "participants and are excluded from the F&A base."),
        ("other", "Other direct costs", " These cover additional direct costs required to "
         "carry out the project."),
    ]:
        amt = budget.get(key)
        if amt:
            cat_sents.append(f"{label}: {_fmt(amt)} is requested.{note}")
    if budget.get("subawards_total"):
        cat_sents.append(
            f"Subawards: {_fmt(budget['subawards_total'])} total is requested; consistent with "
            f"federal policy, only the first $25,000 of each subaward is included in the "
            f"modified total direct cost base."
        )
    if cat_sents:
        paras.append("Other direct costs. " + " ".join(cat_sents))
        paras.append("")

    paras.append(
        "Facilities and administrative (F&A) costs. F&A is applied at the "
        f"{budget['fa_rate_label']} rate of {budget['fa_rate']*100:.0f}% to the modified total "
        f"direct cost (MTDC) base of {_fmt(budget['mtdc_base'])}, which excludes equipment, "
        f"participant support, and the portion of each subaward over $25,000, yielding "
        f"{_fmt(budget['fa_amount'])} in F&A costs."
    )
    paras.append("")
    paras.append(
        f"Total project cost. Total direct costs are {_fmt(budget['direct_costs'])} and F&A "
        f"costs are {_fmt(budget['fa_amount'])}, for a total project cost of "
        f"{_fmt(budget['total'])}."
    )
    return "\n".join(paras)


def per_line_justifications(budget: dict) -> list[dict]:
    """One short, deterministic narrative per budget line (numbers from the
    computed budget, never invented). Returns [{line, amount, text}]."""
    out: list[dict] = []
    for p in budget.get("personnel") or []:
        out.append({
            "line": p["name"],
            "amount": p["subtotal"],
            "text": (f"{p['name']} is budgeted at {p['effort_pct']:.0f}% effort on a "
                     f"{_fmt(p['base_salary'])} base ({_fmt(p['salary'])} salary), plus "
                     f"{p['fringe_label']} fringe at {p['fringe_rate']*100:.0f}% "
                     f"({_fmt(p['fringe'])}), for {_fmt(p['subtotal'])}."),
        })
    for key, label, why in [
        ("equipment", "Equipment", "needed to carry out the proposed work"),
        ("travel", "Travel", "for project-related travel (conferences, fieldwork, collaboration)"),
        ("supplies", "Materials & Supplies", "consumables required for the project"),
        ("participant_support", "Participant Support", "stipends/travel paid to participants (F&A-exempt)"),
        ("other", "Other Direct Costs", "additional direct costs of the project"),
    ]:
        amt = budget.get(key) or 0
        if amt:
            out.append({"line": label, "amount": amt,
                        "text": f"{label}: {_fmt(amt)} {why}."})
    if budget.get("subawards_total"):
        out.append({"line": "Subawards", "amount": budget["subawards_total"],
                    "text": (f"Subawards total {_fmt(budget['subawards_total'])}; F&A applies only "
                             f"to the first $25,000 of each.")})
    return out


def budget_to_csv(budget: dict) -> str:
    """Render a computed budget as CSV (opens in Excel/Sheets). Deterministic."""
    import csv
    from io import StringIO
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["Category", "Detail", "Amount (USD)"])
    for p in budget.get("personnel") or []:
        w.writerow(["Personnel", f"{p['name']} ({p['effort_pct']:.0f}% effort)", f"{p['salary']:.2f}"])
        w.writerow(["Fringe", f"{p['name']} ({p['fringe_label']}, {p['fringe_rate']*100:.0f}%)", f"{p['fringe']:.2f}"])
    for key, label in [("equipment", "Equipment"), ("travel", "Travel"),
                       ("supplies", "Materials & Supplies"),
                       ("participant_support", "Participant Support"), ("other", "Other")]:
        if budget.get(key):
            w.writerow([label, "", f"{budget[key]:.2f}"])
    for i, s in enumerate(budget.get("subawards") or [], 1):
        if s:
            w.writerow(["Subaward", f"#{i}", f"{s:.2f}"])
    w.writerow([])
    w.writerow(["Total direct costs", "", f"{budget.get('direct_costs', 0):.2f}"])
    w.writerow([f"F&A ({budget.get('fa_rate_label', '')}, {budget.get('fa_rate', 0)*100:.0f}%)",
                f"on MTDC base {budget.get('mtdc_base', 0):.2f}", f"{budget.get('fa_amount', 0):.2f}"])
    w.writerow(["TOTAL PROJECT COST", "", f"{budget.get('total', 0):.2f}"])
    my = budget.get("multi_year")
    if my:
        w.writerow([])
        w.writerow([f"Multi-year ({my['project_years']} yrs, {my['escalation_pct']:.0f}% escalation)", "", ""])
        for yr in my["years"]:
            w.writerow([f"Year {yr['year']}", "total", f"{yr['total']:.2f}"])
        w.writerow(["Cumulative total", "", f"{my['cumulative']['total']:.2f}"])
    return buf.getvalue()


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
