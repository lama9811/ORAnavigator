# Budget Helper — Design Spec

**Date:** 2026-06-09
**Status:** approved (build)
**Author:** ORA Navigator session

## One line
A guided budget builder for PIs that computes a sponsor-compliant grant budget
(direct costs → MTDC → F&A → total) with **deterministic code**, flags cap
overages, and uses the LLM **only** to draft the budget-justification prose.

## Why
Budget building is the #1 pain for PIs, and the error-prone part is the F&A
(indirect) rules — applying the rate to the wrong base (forgetting equipment /
the subaward-over-$25k exclusion) overstates a budget and gets proposals bounced.
The math is finite, knowable rules → perfect for deterministic code. Pairs with
the existing **Draft Critic** (which already has `check_budget_cap`).

## Golden rule (unchanged)
**AI proposes → deterministic code verifies → human confirms.** Here the numbers
are 100% deterministic; the AI only writes the justification narrative, and the
figures it cites are injected from the deterministic result (never invented).

## Real rates (source: KB, encoded as constants)
From `backend/kb_structured/pre_award/fanda_cost_rates/pre_award_fanda_cost_rates.json`
and `.../fringe_benefit_rate/pre_award_fringe_benefit_rate.json`:

**F&A (Facilities & Administrative / indirect), FY2025–2026 (default set):**
| Type | Rate |
|---|---|
| Organized Research (On-Campus) — **default** | 54% |
| Instruction (On-Campus) | 64% |
| Other Sponsored Activity (On-Campus) | 42% |
| All Programs (Off-Campus) | 26% |

(FY2024–2025 Organized Research is 53% — keep both years selectable.)

**Fringe benefit rates (by employee category):**
| Category | Rate |
|---|---|
| Faculty (Academic Year) | 42% |
| Faculty (Summer) | 9% |
| Full-Time / Non-Contractual | 42% |
| Contractual (<6 mo or <30 hr/wk) | 9% |

## The math (deterministic core)
```
For each person:  requested_salary = base_salary * effort_pct
                  fringe = requested_salary * fringe_rate[category]
personnel_total = Σ(requested_salary + fringe)

direct_costs (TDC) = personnel_total + equipment + travel + supplies
                     + participant_support + other + Σ(subaward_total)

MTDC = TDC
       − equipment                      (all equipment, capitalized >$5k)
       − participant_support
       − Σ(max(0, subaward_total − 25000))   (only first $25k of EACH subaward)

F&A   = MTDC * fa_rate            (default Organized Research on-campus 54%)
TOTAL = TDC + F&A
```
Cap check: compare TOTAL (default) vs the sponsor cap → `ok | over` with the
overage amount. (Reuse the spirit of `draft_critic.check_budget_cap`.)

Every formula is unit-tested (à la `test_draft_critic_precision.py`).

## Layout (chosen: A · Split view)
A modal opened from a proposal in **My Proposals** ("Build budget" button, next to
"Critique Draft"):
- **Left:** editable line items — People (name, base salary, % effort, fringe
  category), Equipment, Travel, Supplies, Participant support, Other, Subawards.
  An F&A-rate selector (defaults to Organized Research on-campus 54%).
- **Right:** a live **Summary** that recomputes on every change — Direct costs,
  MTDC base, F&A (rate%), **Total**, and a green/red cap badge.
- A **"Draft justification"** button (advisory AI) and a **Save** button.

## Architecture / components
1. **`backend/services/budget_helper.py`** — deterministic core (pure functions,
   no I/O): rate tables + `compute_budget(inputs: dict) -> dict` returning the full
   breakdown + cap check. Plus `draft_justification(budget, use_ai=True)` (Gemini
   via `services/gemini_client`, advisory, HARD fallback to a deterministic
   template; figures injected, never generated).
2. **Endpoints (`backend/main.py`):**
   - `POST /api/budget/compute` (auth) — stateless: body = budget inputs → returns
     the computed breakdown. Drives the live summary.
   - `POST /api/me/submissions/{id}/budget` (auth, owner) — save the budget JSON
     onto the submission; `GET` returns it.
   - `POST /api/budget/justification` (auth) — returns the AI/template narrative.
3. **Storage:** new nullable `budget_json` (Text/JSON) column on `submissions`
   (self-healed by the `init_db()` SELECT→ALTER pattern, per CLAUDE.md — NOT
   `create_all`). No new table needed for MVP.
4. **Frontend:** `BudgetHelperModal.jsx` (+ `.css`) opened from `MyProposals.jsx`;
   calls `/api/budget/compute` (debounced) for the live total; Save persists.

## Data flow
User edits a line → frontend debounced `POST /api/budget/compute` → deterministic
breakdown back → live Summary updates. Save → `POST .../submissions/{id}/budget`.
"Draft justification" → `POST /api/budget/justification` → prose. Draft Critic can
later read `budget_json` to check the real numbers against the real cap.

## Error handling
- Inputs validated/coerced server-side (non-negative numbers; effort 0–100%;
  unknown fringe/F&A keys → safe default + a `warnings[]` entry, never a crash).
- AI justification failure → deterministic template (a reminder always returns).
- Cap missing → compute still works; cap badge shows "no cap set".

## Testing
- `backend/tests/test_budget_helper.py` — unit tests for the math: fringe by
  category, MTDC exclusions (equipment, participant support, subaward $25k rule),
  F&A by rate, total, cap ok/over, multi-person, edge cases (0 effort, empty).
- An e2e test for `/api/budget/compute` (TestClient) like the existing
  `test_proposals_api_e2e.py` (prefix `DATABASE_URL="sqlite:///:memory:"`).

## MVP scope (YAGNI — explicitly OUT for v1)
- Multi-year / escalation (v1 = single budget period).
- Cost-share, program income, modular NIH budgets, SF-424 export.
- Per-person summer vs academic split lines (one fringe category per person row).
These are easy follow-ups once the core is proven.

## Don't-regress
- Numbers come ONLY from deterministic code; AI never edits a figure.
- `migrate_db.migrate()` is not auto-run — the new column self-heals via
  `init_db()` SELECT→ALTER (don't rely on `create_all` for existing tables).
- Single-worker uvicorn assumption unchanged (pure functions, no shared state).
