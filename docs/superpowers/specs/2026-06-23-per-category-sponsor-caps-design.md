# Per-category sponsor caps in Budget Helper

**Date:** 2026-06-23
**Status:** Approved design, ready for implementation plan

## Problem

NSF/NIH solicitations frequently define multiple proposal **categories / tracks**, each with
its own award maximum and duration. The IDSS solicitation is the motivating example:

| Category | Award max | Duration |
|---|---|---|
| Category I | $10M–$30M (range) | up to 5 years |
| Category II | up to $9M | up to 3 years |
| Category III | up to $500,000 | up to 2 years |

Today the solicitation extractor collapses this to a **single** `budget_cap` using a
"most restrictive wins" rule, so the Budget Helper only ever pre-fills the smallest cap
($500,000 for IDSS). A PI applying under Category I or II has no way to see or pick the
cap that actually applies to them — the tool silently assumes the lowest tier.

## Goal

When a solicitation defines multiple category caps, let the PI **choose their funding
category** in the Budget Helper; that choice fills the Sponsor cap field. Single-cap
solicitations are unaffected.

## Non-goals

- Auto-setting the **Years** field from the category's max duration. The duration will be
  shown in the dropdown label for context, but "Years" stays a separate manual input this
  round. (Possible follow-up.)
- Any change to the deterministic cap math in `budget_helper.py`.
- A dedicated DB column for caps — we reuse the existing `notes` text convention.

## Design

Mirrors the **existing `deadline_details` pattern**: keep the single canonical value for
backward compatibility, and add an *additive* structured field that captures every
category. The change is purely additive — single-cap solicitations behave exactly as
before.

### 1. Extraction — `backend/services/solicitation_extractor.py`

Add a new contract-dict key **`budget_cap_details`**: a list of `{category, cap}` objects.

- Add `budget_cap_details` to `_CONTRACT_KEYS`.
- Gemini prompt rule: *if the solicitation defines multiple proposal categories/tracks with
  different award maxima, return each as `{category, cap}` in `budget_cap_details`. For a
  stated range (e.g. "$10 million to $30 million"), use the **maximum** as the cap. Caps are
  integer dollars, no commas/symbols.* If there is only one (or no) category, return an
  empty list.
- The existing single **`budget_cap`** is unchanged (still the most-restrictive / smallest
  value), because Draft Critic and `reconstruct_solicitation_context` consumers depend on it.
- Each cap in `budget_cap_details` must be backed by a `source_quotes` entry, same grounding
  rule as every other extracted field (golden rule 2). Apply the same `_coerce_budget`
  integer normalization to each `cap`.

Expected IDSS output:
```json
"budget_cap": 500000,
"budget_cap_details": [
  {"category": "Category I",   "cap": 30000000},
  {"category": "Category II",  "cap": 9000000},
  {"category": "Category III", "cap": 500000}
]
```

### 2. Storage — `backend/services/proposals_service.py`

In `create_submission_from_solicitation`, when `budget_cap_details` has 2+ entries, append
one human-readable **and** machine-parseable line to `notes`, directly mirroring the
`Deadlines:` line:

```
Category caps: Category I — $30,000,000; Category II — $9,000,000; Category III — $500,000
```

- Format per entry: `<category> — $<comma-grouped amount>`, joined by `; `.
- The existing `Budget cap: $500,000` line is still written (keeps single-cap consumers and
  the current frontend prefill working).
- Add a `_CATEGORY_CAPS_NOTE_RE` and parse logic to `reconstruct_solicitation_context` so the
  structured list round-trips back out of `notes` (parallel to `_BUDGET_NOTE_RE`). It returns
  `budget_cap_details` as a list of `{category, cap}`.

No new DB column — consistent with how `deadline_details`, eligibility, page limits, and
required attachments are all stored in the `notes` TEXT column.

### 3. Frontend — `frontend/src/components/BudgetHelperModal.jsx`

- Add `categoryCapsFromNotes(notes)`: regex-parse the `Category caps:` line into
  `[{category, cap}]` (cap as a numeric string). Returns `[]` when the line is absent.
- **If 2+ categories** parsed: render a **"Funding category"** `<select>` immediately above
  the existing "Sponsor cap" field, in the same F&A/cap area.
  - First option is a disabled placeholder `Select your category…`; the cap field starts
    **blank** (the "force a choice" default).
  - Option labels: `Category I — $30,000,000` (amount comma-grouped).
  - `onChange` sets `inputs.cap` to the selected category's cap.
  - The cap input remains editable for a custom value; typing a custom number that doesn't
    match any option resets the dropdown to the placeholder.
- **If 0–1 categories**: do **not** render the dropdown. Keep today's `capFromNotes` single
  prefill behavior unchanged.
- **Already-saved budget**: cap still loads from saved `inputs` as today. The dropdown still
  renders (so the PI can switch tiers) but does **not** clobber the saved cap on load; if the
  saved cap equals one of the category caps, pre-select that option, otherwise show the
  placeholder.

### 4. Backend math — `backend/services/budget_helper.py`

**No change.** `compute_budget` already receives `cap` as a single number and checks
total-vs-cap. The dropdown only decides which number is sent.

## Data flow

```
Solicitation (PDF/URL)
  → extractor: budget_cap (smallest) + budget_cap_details [{category, cap}]  [grounded by source_quotes]
  → confirm: notes gets "Budget cap: $500,000" + "Category caps: Cat I — $30,000,000; …"
  → BudgetHelperModal: categoryCapsFromNotes(notes) → dropdown
  → user picks Category II → inputs.cap = 9000000
  → POST /api/budget/compute { …, cap: 9000000 }  (unchanged endpoint)
  → deterministic cap check
```

## Edge cases

- **Single cap** → empty `budget_cap_details`, no `Category caps:` line, no dropdown, current
  behavior.
- **Range cap** (Cat I) → store the maximum.
- **Custom cap typed** → allowed; dropdown falls back to placeholder.
- **Saved budget** → saved cap wins on load; dropdown reflects it if it matches a tier.
- **Gemini unavailable / no per-category data** → empty list, graceful fallback to single-cap
  behavior (golden rule 3).

## Testing

- `solicitation_extractor`: `_coerce_extracted` keeps/normalizes `budget_cap_details`; a
  range maps to its max; empty/absent list when only one category.
- `proposals_service`: `create_submission_from_solicitation` writes the `Category caps:` line
  for 2+ entries and omits it otherwise; `reconstruct_solicitation_context` round-trips the
  list back from notes.
- Frontend: `categoryCapsFromNotes` parses the line and returns `[]` when absent (dropdown
  hidden); selecting an option sets the cap.
- Keep the backend pytest suite green (per CLAUDE.md command).

## Files touched

- `backend/services/solicitation_extractor.py` — new field + prompt rule + coercion
- `backend/services/proposals_service.py` — write + reconstruct the `Category caps:` note line
- `frontend/src/components/BudgetHelperModal.jsx` — parse + "Funding category" dropdown
- Tests for the above
