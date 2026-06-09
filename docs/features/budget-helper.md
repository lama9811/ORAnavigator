# Budget Helper (AI-assisted, deterministic math)

**In one line:** builds a sponsor-compliant grant budget (direct costs → F&A → total) with code, and drafts the justification with AI.

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

## Status
✅ Built, tested (21 unit + 5 e2e), and deployed (backend `00087-bqf`, frontend `00048-bcn`).
Design spec: `docs/superpowers/specs/2026-06-09-budget-helper-design.md`.
