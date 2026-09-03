# NSF Form 1030 Budget Template — Design

**Date:** 2026-09-03
**Status:** Approved sections 1–2; section 3 pending first review
**Feature area:** Budget Helper (`backend/services/budget_helper.py`, `frontend/src/components/BudgetHelperModal.jsx`)

---

## 1. Purpose

Today's Budget Helper is a flat five-category calculator: people, equipment, travel,
supplies, participant support, other, plus a list of subawards. It computes F&A on a
modified total direct cost (MTDC) base at Morgan's real rates and checks a sponsor cap.
It is deterministic and trustworthy, but it does not look like anything a PI actually
submits.

NSF proposals are submitted on **Form 1030, the Summary Proposal Budget** — sections A
through M, one sheet per year of support plus a cumulative sheet. This design adds that
form as a first-class template: a grid of empty boxes the PI and research staff fill in,
with every subtotal, the F&A calculation, and the year-over-year rollup computed by code.

### Goals

1. A PI opens a proposal, picks **NSF (Form 1030)**, and sees the real form with blank
   placeholders — senior personnel with person-months, other personnel, fringe,
   equipment, travel, participant support, other direct costs including subawards,
   indirect costs, fee, and cost sharing.
2. Every derived number — every subtotal, line H, line I, line J, line L, and the
   cumulative column — is computed by code, never typed and never produced by an LLM.
3. Multiple years, with a cumulative sheet that sums them automatically.
4. NSF policy violations are surfaced as warnings with their PAPPG citation. Warnings
   never block editing, computing, or saving.
5. The PI can export a filled `.xlsx` laid out as Form 1030, and draft a budget
   justification narrative from the figures.

### Non-goals

- Direct submission to Research.gov. This produces a document, not a submission.
- Replacing the existing generic Budget Helper. It stays, unchanged, as the default.
- NIH SF424 or other sponsor forms. The schema discriminator is designed so one can be
  added later as a third schema, but it is not in this scope.
- A pixel-accurate PDF recreation of Form 1030. Excel was chosen instead (see §6).

### Sources

- **NSF PAPPG 24-1**, Chapter II.D.2.f, "Budget and Budget Justification"
  (pp. II-13 to II-22). Every rule in §5 cites the specific subsection.
- **2 CFR § 200.1** — definitions of MTDC, equipment, participant support costs,
  voluntary committed cost sharing.
- **Morgan State rate figures**, read from the knowledge base:
  `backend/kb_structured/pre_award/fanda_cost_rates/…` and
  `backend/kb_structured/pre_award/fringe_benefit_rate/…`
- The user-supplied blank Form 1030 (`Budget (1).pdf`), Morgan State / PI Timothy
  Oladunni, Year 1 + Cumulative pages, used to fix the exact line labels and ordering.

---

## 2. Architecture

The chosen approach shares one calculation engine between the generic and NSF templates.
The single most important number in this feature — F&A charged on MTDC — must be computed
in exactly one place, or the two templates will eventually disagree and neither will be
trustworthy.

```
backend/services/
  budget_helper.py          (existing; keeps generic math, EXPORTS the shared primitives)
      ├── FA_RATES, FRINGE_RATES, SUBAWARD_MTDC_CAP     ← rate tables
      ├── _money(), _effort()                            ← input coercion
      └── mtdc_and_fa()                                  ← NEW: extracted shared engine
  nsf_budget.py             (NEW: A–M sheet structure, per-year + cumulative, rollup)
  nsf_budget_rules.py       (NEW: the rules table + predicates)
  nsf_budget_export.py      (NEW: openpyxl workbook builder)

frontend/src/components/
  BudgetHelperModal.jsx     (existing; gains a template selector)
  NsfBudgetSheet.jsx        (NEW: the A–M grid, year tabs, flags panel)
  NsfBudgetSheet.css        (NEW)
```

`mtdc_and_fa()` is extracted from the body of the existing `compute_budget()`, which then
calls it. That refactor is behaviour-preserving and is covered by the existing 21
budget-helper tests, which must stay green without modification.

### Why not the alternatives

- **One unified model where generic is a view of NSF.** Fewest code paths, but it forces
  NSF vocabulary onto NIH, DOE, and foundation proposals, and turns every existing saved
  budget into a migration problem for no user-visible benefit.
- **A fully separate NSF feature.** Fastest to write and zero regression risk, but it
  duplicates the MTDC/F&A engine — precisely the code that must never drift.

---

## 3. Data model

### 3.1 Storage and the schema discriminator

The budget is persisted in the existing nullable `submissions.budget_json` column
(`MEDIUMTEXT`, self-healed by the `SELECT` → `ALTER` check in `init_db()`). No migration
is required.

A `schema` key discriminates the two templates. **A saved budget with no `schema` key
loads as a generic budget, exactly as today.** That is the entire backward-compatibility
story, and it is guarded by an explicit regression test.

```json
{
  "schema": "nsf_1030",
  "version": 1,
  "meta": {
    "organization": "Morgan State University",
    "pi_name": "",
    "duration_months": 36,
    "sponsor_program": "standard",
    "mandatory_cost_sharing": false
  },
  "settings": {
    "fa_year": "fy_2025_2026",
    "fa_rate_key": "organized_research_on_campus",
    "escalation_pct": 3.0,
    "capitalization_level": 5000
  },
  "years": [ { "year": 1, "…": "sheet" }, { "year": 2, "…": "sheet" } ]
}
```

`sponsor_program` is one of `standard`, `sbir_sttr`, `major_facility`; it gates the line K
fee rule. `mandatory_cost_sharing` is set by the PI when the solicitation requires cost
sharing, and gates the line M rule.

**The cumulative sheet is never stored.** It is recomputed by summing the year sheets on
every request. One source of truth; it cannot drift out of sync with the years.

### 3.2 One year sheet = the A–M form

```json
{
  "year": 1,
  "senior": [
    { "name": "", "role": "PI", "appointment_basis": "academic_9",
      "base_salary": null, "cal": 0, "acad": 0, "sumr": 0,
      "fringe_key": "faculty_ay" }
  ],
  "other_personnel": {
    "postdocs":            { "count": 0, "months": 0, "amount": null, "fringe_key": "full_time" },
    "other_professionals": { "count": 0, "months": 0, "amount": null, "fringe_key": "full_time" },
    "grad_students":       { "count": 0, "amount": null, "fringe_key": "contractual" },
    "undergrads":          { "count": 0, "amount": null, "fringe_key": "contractual" },
    "clerical":            { "count": 0, "amount": null, "fringe_key": "full_time" },
    "other":               { "count": 0, "amount": null, "fringe_key": "contractual" }
  },
  "equipment": [ { "description": "", "amount": null } ],
  "travel": {
    "domestic":      [ { "description": "", "amount": null } ],
    "international": [ { "description": "", "amount": null } ]
  },
  "participant_support": {
    "count": 0, "stipends": null, "travel": null, "subsistence": null, "other": null
  },
  "other_direct": {
    "materials_supplies": [ { "description": "", "amount": null } ],
    "publication":        [ { "description": "", "amount": null } ],
    "consultant":         [ { "description": "", "amount": null } ],
    "computer_services":  [ { "description": "", "amount": null } ],
    "subawards":          [ { "organization": "", "amount": null } ],
    "other":              [ { "description": "", "amount": null, "mtdc_exempt": false } ]
  },
  "fee": 0,
  "cost_sharing": { "proposed": 0, "agreed": null }
}
```

`null` is the empty placeholder. It renders as a blank box rather than `$0`, so a PI can
distinguish "not yet filled in" from "deliberately zero" — which matters when research
staff hand a partly-filled sheet back and forth.

The line-item lists (equipment, both travel sub-lines, and the four itemised G lines)
exist because PAPPG requires items to be "listed individually by description and estimated
cost", and because the budget justification has to name them. Participant support keeps
single amounts per sub-line, since stipends / travel / subsistence / other *is* already
the required breakdown.

### 3.3 Person-months and salary

NSF budgets effort in **person-months** across three columns: CAL (calendar), ACAD
(academic), SUMR (summer). Salary follows from the appointment basis:

| Appointment basis | Monthly rate | Columns that apply |
|---|---|---|
| `academic_9` (9-month faculty) | `base_salary ÷ 9` | ACAD, SUMR |
| `calendar_12` (12-month appointment) | `base_salary ÷ 12` | CAL |

`salary = (cal + acad + sumr) × monthly_rate`

The UI shows the equivalent % effort live beside the months, so PIs who think in percent
are not stranded. Entering CAL months against a 9-month appointment, or ACAD/SUMR against
a 12-month one, produces a warning — not an error, because unusual appointments exist.

### 3.4 Fringe benefits

Form 1030 has a single fringe box on line C. Morgan's fringe rate is not a single number:
42% for faculty (academic year) and full-time staff, 9% for summer faculty and contractual
or student appointments.

Fringe is therefore computed **per personnel row** at that row's category rate, and line C
is the sum. The form still shows one figure, as NSF requires; the per-row breakdown feeds
the budget justification, which has to explain the rates anyway.

This is the one place the model is richer than the paper form. It is the difference
between a correct line C and a hand-arithmetic error — mixing a postdoc at 42% with
graduate students at 9% by hand is a common source of budget revisions.

### 3.5 MTDC exclusions

Per 2 CFR § 200.1, the F&A base is total direct costs **minus**:

| Exclusion | How it is identified |
|---|---|
| Equipment (line D) | The **capitalization test** — per-unit cost ≥ the lesser of Morgan's capitalization level or $5,000. *Not* the form's $10,000 itemisation label. |
| Participant support (line F) | The whole of line F. |
| The portion of **each** subaward over $25,000 | Per subaward, not per total. |
| Tuition remission, scholarships and fellowships, rental costs, patient care | The `mtdc_exempt` flag on a G-line item. |

The last row needs explanation. NSF's form has no tuition line, and Morgan books graduate
tuition remission in **G.6 "Other"** alongside items that *do* bear F&A. The tool cannot
tell `$40,000 tuition` from `$40,000 lab fees` by looking at them, and guessing wrong
moves the total by roughly $21,600 at the 54% rate.

So each G.6 line carries a checkbox — *"exclude from F&A base (tuition, scholarships,
rent, patient care)"*. The PI states the fact once; the math follows. The rejected
alternative was keyword-matching the description for "tuition", which is exactly the kind
of guessing that makes a total indefensible under review.

### 3.6 Rollup

```
A = Σ senior salaries
B = Σ other-personnel amounts
C = Σ per-row fringe
D = Σ equipment items
E = domestic + international
F = stipends + travel + subsistence + other
G = G.1 + G.2 + G.3 + G.4 + G.5 (subawards) + G.6
H = A + B + C + D + E + F + G
MTDC = H − D − F − Σ max(0, subaward − 25,000) − Σ mtdc_exempt items
I = MTDC × F&A rate
J = H + I
K = fee (0 unless SBIR/STTR or Major Facilities)
L = J, or J − K when a fee applies
M = cost sharing (informational; not part of the request)
```

The cumulative sheet sums each line across all years independently, and recomputes its own
flags at proposal scope.

### 3.7 Escalation

"Add year" clones the previous year's sheet and multiplies **salary bases only** by
`escalation_pct` (default 3%, editable per year). Every other category carries over flat
for the PI to adjust. Escalating travel, supplies, and participant support automatically
was rejected: it silently inflates a budget the PI may never re-check.

### 3.8 Input coercion

The existing pattern is reused rather than reinvented: `_money()` and `_effort()` coerce
junk or negative input to `0` and append a human-readable warning instead of raising.
A new `_months()` helper does the same for person-months, clamped to 0–12.

**No input can crash a compute.** A PI mid-typing must always get a number back.

---

## 4. Computed response shape

`compute` returns a tree mirroring the input, with every node resolved and every subtotal
present, so the frontend renders values and never does arithmetic:

```json
{
  "years": [
    { "year": 1,
      "lines": { "A": { "rows": [], "total": 0 }, "B": { "rows": [], "total": 0 },
                 "C": 0, "D": { "rows": [], "total": 0 }, "E": { "domestic": 0, "international": 0, "total": 0 },
                 "F": { "count": 0, "stipends": 0, "travel": 0, "subsistence": 0, "other": 0, "total": 0 },
                 "G": { "materials_supplies": 0, "publication": 0, "consultant": 0,
                        "computer_services": 0, "subawards": { "rows": [], "total": 0 },
                        "other": 0, "total": 0 },
                 "H": 0, "I": 0, "J": 0, "K": 0, "L": 0, "M": 0 },
      "mtdc": { "base": 0, "exclusions": { "equipment": 0, "participant_support": 0,
                                           "subaward_over_25k": 0, "mtdc_exempt": 0 } },
      "fa": { "year": "fy_2025_2026", "rate_key": "organized_research_on_campus",
              "rate": 0.54, "label": "Organized Research (On-Campus)" },
      "flags": [] }
  ],
  "cumulative": { "lines": {}, "mtdc": {}, "flags": [] },
  "cap": { "value": null, "status": "none", "overage": 0 },
  "flags": [],
  "warnings": []
}
```

`warnings` carries input-coercion messages ("could not read base salary 'abc'; using $0").
`flags` carries NSF rule findings. They are deliberately separate: one is about the tool
failing to read input, the other is about policy.

---

## 5. The NSF rules

Each rule is a **data entry plus a small predicate**, registered in
`nsf_budget_rules.py`. Adding a rule is one table entry and one function — not a change to
the math. This is what makes the user's own NSF rules list cheap to fold in later.

```python
{"id": "nsf.senior.two_month_cap", "line": "A", "severity": "warn",
 "title": "Senior salary over 2 months",
 "message": "…",
 "citation": "PAPPG 24-1 II.D.2.f(i)(a)",
 "scope": "year"}      # year | cumulative | proposal
```

Two severities, and **neither blocks** editing, computing, saving, or exporting:

- `warn` — a rule is likely broken. Red.
- `info` — a requirement that must be satisfied somewhere else (usually in the
  justification or in a separate document). Grey.

Every flag carries its `line` letter so the UI pins it beside the row that caused it, and
its `citation` so the PI can check the source — the same grounding discipline the chat
pipeline and Compliance Sentinel already follow.

| Line | Rule | Severity | Citation |
|---|---|---|---|
| A | Senior/key person over **2 months** of salary in a year | warn | II.D.2.f(i)(a) |
| A | CAL months on a 9-month appointment, or ACAD/SUMR on a 12-month one | warn | — |
| A/B | Months entered with no base salary, or salary with no months | warn | — |
| C | Fringe category missing on a paid row | warn | — |
| D | Equipment item **below** the capitalization level — belongs in G.1, and bears F&A | warn | II.D.2.f(iii) |
| D | Equipment item over **$10,000** with no description (form requires itemisation) | warn | Form 1030 line D |
| D | General-purpose equipment (office, general IT) is normally unallowable | info | II.D.2.f(iii) |
| E | International travel present — Fly America / US-flag carrier, justify separately | info | II.D.2.f(iv)(c) |
| F | Participant dollars entered but **participant count is 0** | warn | II.D.2.f(v) |
| F | Participants may not be employees; human-subject incentives belong in **G.6, not F** | info | II.D.2.f(v) |
| F | No F&A is charged on participant support | info | II.D.2.f(v) |
| G.5 | Each subaward needs its **own budget and ≤5-page justification**, at its own negotiated rate | info | II.D.2.f(vi)(e) |
| G.5 | Subaward over $25,000 — shows exactly how much is excluded from the F&A base | info | 2 CFR 200.1 |
| I | **F&A rate below Morgan's negotiated rate** — NSF treats this as a cost-sharing violation | warn | II.D.2.f(viii) |
| I | Manual F&A rate that is not one of Morgan's KB rates | warn | II.D.2.f(viii) |
| K | **Fee > 0** outside SBIR/STTR or Major Facilities | warn | II.D.2.f(x) |
| M | **Cost sharing > 0** when the solicitation does not mandate it | warn | II.D.2.f(xii) |
| — | Fewer year sheets than the stated project duration | warn | II.D.2.f |
| — | Cumulative total over the solicitation's budget cap | warn | solicitation |
| — | The budget justification is limited to 5 pages | info | II.D.2.f |

### Two limits stated honestly in the UI

1. **The 2-month cap counts across all NSF awards.** This tool sees only this proposal.
   The flag text says so. It catches the common case — someone budgeting three summer
   months here — but it cannot know about the PI's other NSF grants.
2. **The capitalization level defaults to $5,000**, from PAPPG's "lesser of the
   organization's capitalization level or $5,000". Note that Form 1030's line D label says
   $10,000; that is the *itemisation* threshold on the form, a different thing from the
   equipment definition that drives the MTDC exclusion. Both are encoded separately. If
   ORA confirms Morgan capitalizes at a different figure, changing that one setting
   reclassifies equipment correctly everywhere.

---

## 6. API surface

All existing endpoints keep their paths and their behaviour for generic budgets.

| Method | Path | Status | Purpose |
|---|---|---|---|
| GET | `/api/budget/rates` | extended | F&A + fringe tables; gains `capitalization_level` and `escalation_pct` defaults |
| GET | `/api/budget/nsf/template?years=N` | **new** | An empty NSF skeleton, so the frontend never hardcodes the form structure |
| POST | `/api/budget/nsf/compute` | **new** | Stateless. Body is the full `nsf_1030` document; returns §4 |
| POST | `/api/budget/nsf/justification` | **new** | Deterministic template, AI-polished, hard fallback |
| GET | `/api/me/submissions/{id}/budget` | extended | Returns `{schema, inputs, computed}`; detects the schema |
| PUT | `/api/me/submissions/{id}/budget` | extended | Persists either schema |
| GET | `/api/me/submissions/{id}/budget.xlsx` | **new** | Streams the workbook |

All are Bearer-authed and scoped to the calling user's own submission, matching the
existing proposals endpoints.

**Download mechanics.** The `.xlsx` endpoint is on the backend origin, so the frontend
must `fetch()` it with the Authorization header and download the response as a
**same-origin blob** — not an `<a download>` pointing cross-origin, which the browser
silently ignores. This is the exact bug fixed for the `.ics` calendar export on
2026-06-10; the same pattern applies here.

---

## 7. Frontend

`BudgetHelperModal.jsx` gains a template selector at the top: **Generic** | **NSF (Form
1030)**. Generic keeps today's view untouched. NSF swaps the body for a new
`NsfBudgetSheet.jsx`.

- **Year tabs:** `Year 1 · Year 2 · … · + Add year · Cumulative`. The cumulative tab is
  read-only with shaded inputs.
- **The sheet** renders A–M as the real form's rows and labels. Editable cells are input
  boxes; computed cells (every subtotal, H, I, J, L) are read-only and shaded, so it is
  visually obvious which numbers a human owns and which the tool owns.
- **Sticky summary rail** on the right: H, I, J, L and the sponsor-cap badge — reusing the
  layout already proven in the generic Budget Helper.
- **Flags panel:** red and grey chips pinned to their line, each showing its PAPPG
  citation.
- **Actions:** Save, Draft justification, Download `.xlsx`.
- **Recompute** is debounced at 300 ms against `/api/budget/nsf/compute`, the same pattern
  as today, keeping the last good totals on a failed request.
- Rendered through a **React portal**, as the existing modal already is, so the desktop
  sidebar cannot overlay it.
- Modal overlay `z-index: 1200` — the established above-navbar value in this codebase.

---

## 8. Excel export

`services/nsf_budget_export.py`, built on **openpyxl** — a new backend dependency, pure
Python, no build step. It must be added to `backend/requirements.txt` before deploy.

- One worksheet per year, plus a `Cumulative` sheet.
- Rows laid out as Form 1030, A through M, with the form's own labels.
- **Live formulas, not baked values.** Subtotals are `=SUM(...)`; line I is
  `=MTDC_cell * rate`. ORA can change one cell and the sheet re-totals — which is the
  whole reason Excel was chosen over a static PDF recreation.
- A header block carrying organization, PI/PD name, duration, and proposal/award numbers.
- A `Flags` worksheet listing every warning and info finding with its citation.
- Currency number formats, a frozen header row, and sensible column widths.

---

## 9. Budget justification

`draft_justification()` is extended to the NSF categories. The deterministic template
comes first and is built **only** from the computed figures; the AI polish is layered at
the endpoint with the existing hard fallback, so a justification always returns even when
Gemini is unavailable or rate-limited.

Per year it covers: each senior person's person-months, base salary, computed salary and
fringe rate; other personnel by category and headcount; each equipment item by description
and cost; travel split domestic/international with purpose; participant count with the
per-category breakdown; each subaward by organization, with a note on the $25,000 MTDC
portion; and an F&A sentence naming the rate, the base, and the resulting amount. A
closing cumulative paragraph gives the multi-year totals.

The strict "never change a figure" system prompt already used by the generic justification
is reused verbatim.

---

## 10. Testing

Test-driven: every rule and every arithmetic path gets a test before its implementation.

**`tests/test_nsf_budget.py`**
- Person-months → salary, for both appointment bases.
- Per-row fringe summing correctly into line C, across mixed rate categories.
- MTDC exclusions, one test each: equipment by capitalization test, participant support,
  the per-subaward $25,000 split, and the `mtdc_exempt` flag.
- The H / I / J / L rollup, including fee subtraction on L.
- Cumulative equals the sum of the year sheets.
- Escalation applies to salary bases only, and to nothing else.
- **Every rule in §5: one test that fires it, one that does not.**
- Junk, negative, `None`, and missing keys never raise.
- A full worked example with a hand-checked total, in the style of the existing
  `$161,556` generic example.

**`tests/test_nsf_budget_export.py`**
- The workbook opens; sheet names match the years plus `Cumulative` and `Flags`.
- Subtotal cells contain formulas rather than constants.
- The flags sheet lists the expected findings.

**`tests/test_budget_api_e2e.py`** (extended)
- Template, compute, justification, save → load round-trip, and `.xlsx` download.
- **Regression guard: a saved generic budget with no `schema` key still loads and computes
  exactly as before.**

The 21 existing `test_budget_helper.py` tests must stay green **without modification** —
that is the proof the `mtdc_and_fa()` extraction was behaviour-preserving.

Expected: roughly 40–50 new tests, taking the backend suite from 422 to about 470.

---

## 11. Risks and open items

| Item | Status |
|---|---|
| **Morgan's capitalization level** is unconfirmed. PAPPG's $5,000 is the default. | Needs ORA confirmation; it is a setting, so a wrong default is a one-value fix. |
| **Morgan's 54% F&A and 42%/9% fringe** are read from the KB, not from ORA's live rate sheet. | Worth a sanity check with ORA before this is used for a real submission. |
| **`openpyxl` is a new dependency.** | Must be in `backend/requirements.txt` before the Cloud Run build, or the image fails to import. |
| **Existing generic budgets must keep loading.** | Explicit regression test; the schema discriminator defaults to generic on absence. |
| **Deploy** | Backend via `scripts/deploy_backend.sh` (preserves env vars and `min-instances`); frontend deployed separately — the script takes one target per invocation. |
| **PWA cache** | Frontend changes must be verified in incognito; the service worker serves the previous bundle to returning users. |

---

## 12. Decisions made during design

| Decision | Rationale |
|---|---|
| NSF as a **new schema alongside** generic, not a replacement | Existing saved budgets keep working; NIH SF424 can drop in later as a third schema. |
| **Shared MTDC/F&A engine** rather than a parallel implementation | The one number a PI must be able to trust is computed in exactly one place. |
| **Excel**, not PDF | ORA and PIs need to tweak and re-total; live formulas beat a static picture of a form. |
| **Person-months** as the input, with a % helper | It is what the form and ORA check; % effort needs an appointment assumption that can be wrong. |
| **3% escalation on salaries only**, editable | Standard practice; escalating everything silently inflates budgets. |
| **Warn, never block** | Matches Draft Critic and Compliance Sentinel — deterministic advice, the user stays in control. |
| **`mtdc_exempt` checkbox** rather than keyword-matching "tuition" | The PI states the fact; the tool never guesses at a number worth ~$21,600. |
| **Per-row fringe** summed into line C | Morgan's rates differ by category; hand arithmetic across 42% and 9% is a known error source. |
| **Cumulative computed, never stored** | It cannot drift out of sync with the year sheets. |
