# Budget Helper (AI-assisted, deterministic math)

**In one line:** builds a sponsor-compliant grant budget (direct costs → F&A → total) with code, and drafts the justification with AI — as a simple generic sheet, or as NSF's Form 1030.

## What it does (plain English)
On a proposal, click **Build budget**. Enter people + % effort, equipment, travel, supplies,
participant support, and subawards, pick the F&A (indirect) rate, and a live summary shows
**Direct costs → MTDC base → F&A → Total** with a sponsor-cap badge. The hard part it gets right is
the federal rule that **F&A is charged on the *modified* total direct costs (MTDC)** — excluding
equipment, participant support, and the portion of each subaward over $25,000 — which PIs routinely
get wrong (overstating the budget). Then **Save** it and **Draft justification** writes the prose.

## Where it lives
- `backend/services/budget_helper.py` — deterministic core (`compute_budget`, `rate_options`,
  `draft_justification`).
- Endpoints in `backend/main.py`: `POST /api/budget/compute`, `GET /api/budget/rates`,
  `POST /api/budget/justification`, `GET`/`PUT /api/me/submissions/{id}/budget`.
- Storage: `submissions.budget_json` (nullable, self-healed in `init_db()`).
- Frontend: `frontend/src/components/BudgetHelperModal.jsx` (+ `.css`, rendered via a **portal**),
  wired into the "Build/Edit budget" button in `MyProposals.jsx`.

## How it works
- **Deterministic core is authoritative** — every figure comes from `compute_budget`; the LLM never
  touches a number. Real Morgan rates from the KB: F&A FY25-26 Organized Research **54%** (+
  Instruction 64% / Other Sponsored Activity 42% / Off-campus 26%, and FY24-25); fringe Faculty AY
  **42%** / Summer 9% / Full-time 42% / Contractual 9%. MTDC excludes equipment, participant support,
  and each subaward's amount over $25k.
- **Advisory AI justification** (`/api/budget/justification`, Gemini) rewrites the deterministic
  template into prose with a strict "never change a figure" prompt, and a **HARD fallback** to the
  template if the AI is unavailable — a justification always returns.
- **Cap auto-prefill:** the sponsor cap is parsed from the proposal's solicitation notes
  (`Budget cap: $…`) when present, so Critique Draft and Budget Helper share the same cap.

## API & data
- Inputs are coerced server-side (non-negative, effort 0–100; unknown rate keys fall back + warn);
  bad input never crashes — it returns `warnings[]`.
- Saved as JSON on the Submission; recomputed deterministically on load.

## Don't regress (load-bearing)
- Numbers come ONLY from `budget_helper.compute_budget`; the AI is advisory and must not edit figures.
- New column self-heals via `init_db()` SELECT→ALTER (not `create_all`).
- Keep the rate tables in sync if Morgan renegotiates its F&A / fringe agreement (source: KB
  `pre_award_fanda_cost_rates` / `pre_award_fringe_benefit_rate`).

## The NSF Form 1030 template (added 2026-09-03)

A **Template** selector at the top of the modal switches between **Generic budget** (everything
above) and **NSF (Form 1030)** — the Summary Proposal Budget NSF proposals are actually submitted
on. The two share one F&A/MTDC engine so they can never disagree.

**Which template loads** is decided by a `schema` key in `submissions.budget_json`:
`"schema": "nsf_1030"` opens the NSF sheet; **a budget with no `schema` key is a generic budget and
behaves exactly as before.** That is the whole backward-compatibility story, and there is an
explicit regression test for it.

### What the PI sees
Sections **A–M** as real form rows with blank placeholders, one tab per year plus a **Cumulative**
tab that is read-only and sums the years. Editable cells are input boxes; computed cells are shaded
and read-only, so it is obvious which numbers a human owns. **+ Add year** clones the previous year
and escalates **salary bases only** (3%, editable) — equipment, travel and subawards carry over flat.
A live rail shows H / I / J / L, the sponsor-cap badge, and the MTDC exclusion breakdown.

### What it gets right
- **Person-months, not % effort.** Monthly rate = base salary ÷ **9** (academic appointment) or
  ÷ **12** (calendar); salary = (CAL + ACAD + SUMR) × that. Equivalent % effort is shown live.
- **Per-row fringe summed into line C.** The form has one fringe box, but Morgan's rate is 42% for
  faculty/full-time and 9% for summer/contractual. Each row is computed at its own rate and summed —
  the form still shows one figure, but it is the right one.
- **MTDC exclusions** (2 CFR 200.1): equipment by the **capitalization test**, all participant
  support, the portion of **each** subaward over $25,000, and any G-line item flagged `mtdc_exempt`.
- **`mtdc_exempt` is an explicit checkbox, never a guess.** NSF's form has no tuition line and Morgan
  books graduate tuition remission in G.6 next to items that *do* bear F&A. The tool cannot tell
  `$40,000 tuition` from `$40,000 lab fees` by looking, and guessing wrong moves the total by ~$21,600
  at 54%. So the PI ticks a box; the math follows. Also covers scholarships, rent, and patient care.

### The rules (warn, never block)
18 rules in `services/nsf_budget_rules.py`, each a **table entry plus one predicate** carrying its
PAPPG 24-1 citation — adding a rule is data, not a refactor. `warn` (red) vs `info` (grey); **neither
blocks editing, computing, saving, or exporting.** Highlights: the **two-month senior salary cap**;
equipment below the capitalization level (belongs in G.1 and *does* bear F&A); unitemised equipment
over $10,000; Fly America on international travel; participant dollars with no participant count;
each subaward needing its own budget + ≤5-page justification; **an F&A rate below Morgan's negotiated
rate, which NSF treats as a cost-sharing violation**; a fee outside SBIR/STTR; and voluntary
committed cost sharing.

### Excel export
`GET /api/me/submissions/{id}/budget.xlsx` streams a Form 1030 workbook (`nsf_budget_export.py`,
openpyxl): one sheet per year plus `Cumulative` and `Flags`. **Subtotals are live `=SUM()` formulas**,
not baked values, so ORA can change a cell and the sheet re-totals — the reason Excel was chosen over
a static PDF. Tests evaluate the emitted formula chain and assert it reproduces the backend's totals.

### Two honest limits (stated in the UI, not hidden)
1. **The two-month cap counts across all NSF awards.** This tool sees only this proposal, so it
   catches the common case but cannot know the PI's other NSF grants.
2. **The capitalization level defaults to $5,000** (PAPPG's "lesser of the organization's
   capitalization level or $5,000"). Form 1030's line D says $10,000 — that is the *itemisation*
   threshold on the form, a different thing from the equipment definition that drives the MTDC
   exclusion. Both are encoded separately. **Confirm Morgan's actual capitalization level with ORA;**
   it is a setting, so a wrong default is a one-value fix.

### NSF endpoints
`GET /api/budget/nsf/template?years=N` · `POST /api/budget/nsf/compute` ·
`POST /api/budget/nsf/add-year` · `POST /api/budget/nsf/justification` ·
`GET /api/me/submissions/{id}/budget.xlsx`

### Don't regress (NSF)
- A `budget_json` with **no `schema` key must keep loading as a generic budget.**
- The 21 existing `test_budget_helper.py` tests must pass **unmodified** — that is the proof the
  `mtdc_and_fa()` extraction stayed behaviour-preserving.
- The `.xlsx` must be fetched with the auth header and saved as a **same-origin blob**; an
  `<a download>` pointing at the backend origin is silently ignored cross-origin.
- `gemini-2.5-flash` is a **thinking** model — its reasoning is billed against `max_output_tokens`.
  A 1600 budget truncated the justification mid-sentence (~1225 tokens went to thinking). Keep the
  6000 budget **and** the check that the AI text still contains the computed final total.

## Status
✅ Generic: built, tested (21 unit + 5 e2e), deployed (backend `00087-bqf`, frontend `00048-bcn`).
Design spec: `docs/superpowers/specs/2026-06-09-budget-helper-design.md`.

✅ NSF Form 1030: built and tested (**+96 tests** — 46 math, 36 rules, 8 export, 8 e2e), verified
end-to-end in a real browser on the local stack. **Not yet deployed.** Design spec:
`docs/superpowers/specs/2026-09-03-nsf-form-1030-budget-design.md`; plan:
`docs/superpowers/plans/2026-09-03-nsf-form-1030-budget.md`.
