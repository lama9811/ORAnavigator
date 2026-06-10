# Compliance Sentinel — Design Spec

**Date:** 2026-06-10
**Status:** Approved (brainstorm) → implementation
**Pattern parent:** Budget Helper (`services/budget_helper.py` + modal + `/api/budget/*` + self-healing column)

## Problem

A Morgan PI writing a grant must obtain certain approvals/training depending on
what the project involves (human subjects, animals, financial conflicts, foreign
collaboration, controlled technology, federal training mandates). Most PIs don't
know which apply to them. Missing one can freeze or reject an award. ORA staff
field these "do I need IRB for this?" questions repeatedly.

**Compliance Sentinel** reads a proposal + a short questionnaire and produces a
deterministic checklist of exactly which approvals are Required / Not required /
Need review, each with a plain-English why, a realistic timing note, and a link
to the right Morgan form. It can add the required items to the proposal as tasks.

## Design decisions (from brainstorm)

1. **Trigger source = short yes/no questionnaire** (deterministic rules map answers
   → requirements). Compliance is too important to let an AI guess. Some triggers
   are derived from the proposal's `sponsor` field, not asked.
2. **Coverage (v1):** IRB (human subjects), IACUC (animals), COI (financial
   conflict), RCR training, Export Control / Research Security.
3. **Output = show + persist + add tasks.** Render the checklist, save answers on
   the proposal, and offer one-click "Add required items to my proposal" creating
   `SubmissionTask`s with the form linked.
4. **Explanations = deterministic templates.** Zero AI in v1. The rules ARE the
   product. (No Gemini dependency, no hallucination risk, instant.)

## Architecture

```
backend/services/compliance_sentinel.py        ← NEW, deterministic core (no AI)
  RULES            : list[ComplianceRule]        the 5 checks (data, in-code)
  QUESTIONS        : list[{key,label,help}]       derived questionnaire
  assess_compliance(answers: dict, sponsor: str|None) -> dict
  suggested_tasks(result: dict) -> list[{title, description, kb_doc_id}]
  _resolve_doc(doc_id) -> {kb_doc_id, kb_doc_url, kb_doc_title} | None

backend/services/forms_catalog.py               ← extend
  resolve_kb_doc(doc_id) -> dict|None            KB-index resolver (any doc, not
                                                 just form_* ); get_form() and the
                                                 task-dict link path fall back to it
                                                 so compliance_* / training ids
                                                 never produce a dead link.
```

### Trigger sources

- **Questionnaire answers** (`"yes"` / `"no"` / unanswered):
  `human_subjects`, `animals`, `financial_interest`, `foreign_collaboration`,
  `export_controlled`.
- **Sponsor-derived** (from `Submission.sponsor`, case-insensitive):
  - sponsor ∈ {NIH, NSF} → **RCR training** required.
  - sponsor ∈ {NIH, PHS} → **COI** required for ALL investigators (PHS FCOI rule),
    regardless of the `financial_interest` answer.

### The rule set (v1 — 5 items)

| id | Trigger | status logic | KB doc (verified) | timing note |
|---|---|---|---|---|
| `irb` | `human_subjects` | yes→required, no→not_required, unanswered→review | `form_irb_approval_request` (Human Subjects Research Approval Request Form) | IRB review commonly takes 3–6 weeks (longer for full board) — submit early. |
| `iacuc` | `animals` | yes→required, no→not_required, unanswered→review | `compliance_iacuc_forms` (IACUC — Forms) | IACUC reviews monthly; allow 4–8 weeks for protocol approval. |
| `coi` | sponsor∈{NIH,PHS} OR `financial_interest` | required if either; else not_required | `form_coi_fcoi_sponsored_disclosure` (FCOI Disclosure Form — Sponsored Research) | FCOI disclosures must be on file before award; PHS requires training + annual updates. |
| `rcr` | sponsor∈{NIH,NSF} | required if federal-training sponsor; else not_required | `form_citi_training_program` (CITI Training Program) | NSF requires RCR training for students/postdocs; NIH for trainees. CITI ≈ a few hours. |
| `export_security` | `export_controlled` / `foreign_collaboration` | export_controlled yes→required (TCP); else foreign_collaboration yes→review (NSPM-33); both no→not_required | required→`compliance_research_security_technology_control_plan`; review→`compliance_research_security_nspm_33` | Export / Research-Security review can gate your start date — involve ORA early. |

**Statuses:** `required` | `not_required` | `review`. `review` = needs human
judgment or an unanswered question that *could* trigger a requirement.

### `assess_compliance` return shape

```json
{
  "answers": {"human_subjects": "yes", ...},
  "sponsor": "NIH",
  "items": [
    {"id":"irb","title":"IRB — Human Subjects Protection","status":"required",
     "why":"Because your project involves human subjects ...",
     "timing":"IRB review commonly takes 3–6 weeks ...",
     "kb_doc_id":"form_irb_approval_request",
     "kb_doc_url":"https://...","kb_doc_title":"Human Subjects Research Approval Request Form"}
  ],
  "summary": {"required": 3, "review": 1, "not_required": 1},
  "warnings": []
}
```

`suggested_tasks(result)` returns one task **per `required` item only**
(skips both `not_required` and `review` — review items are advisory and shown in
the modal but not auto-added, keeping the proposal task list clean) →
`{title: "Submit IRB protocol …", description: <why+timing>, kb_doc_id}`.

## Data model & endpoints

- **New nullable column** `submissions.compliance_json` (Text) — stores
  `{"answers": {...}}`. Self-healed in `main.py init_db` via a `SELECT … →
  ALTER TABLE … ADD COLUMN` guard, mirroring `budget_json`. Recomputed
  deterministically on load (answers are the source of truth, not the snapshot).

- **Endpoints** (mirror `/api/budget/*`; all `Depends(get_current_user)`):
  - `GET  /api/compliance/questions` → questionnaire definition.
  - `POST /api/compliance/assess` → stateless: `{answers, sponsor}` → result.
  - `GET  /api/me/submissions/{id}/compliance` → saved answers + fresh assess
    (uses `sub.sponsor`). Ownership-checked.
  - `PUT  /api/me/submissions/{id}/compliance` → save answers (validate compute).
  - `POST /api/me/submissions/{id}/compliance/tasks` → create `SubmissionTask`s
    for required items; returns created tasks. Idempotency: skip a task whose
    title already exists on the submission (case-insensitive).

## Frontend

- `frontend/src/components/ComplianceSentinelModal.jsx` — rendered via React
  portal (like `BudgetHelperModal`). Left: questionnaire (yes/no toggles). Right:
  live checklist grouped by status with why + timing + "Open form ↗" links.
  Buttons: **Save**, **Add required items to my proposal**.
- `MyProposals.jsx` — a **"Check compliance"** button on every proposal (compliance
  applies regardless of solicitation, unlike Draft Critic's gated button).
- `ComplianceSentinelModal.css` — match the Budget Helper modal styling.

## Error handling

- Unknown/blank answers → treated as unanswered (status `review` where the
  question gates a requirement); never crash. Junk values coerced, with a warning
  appended (mirrors `budget_helper._money`).
- Unknown sponsor → only questionnaire triggers apply (no sponsor-derived
  requirements); no error.
- Link resolution miss → item still renders with `kb_doc_url: null` (UI shows no
  button); never a dead link.
- Ownership/404 on the per-submission endpoints identical to the budget endpoints.

## Testing (TDD)

- `backend/tests/test_compliance_sentinel.py` — unit:
  each trigger (yes/no/unanswered), sponsor-derived RCR + PHS-COI, combined
  export/foreign logic, status counts, `suggested_tasks` content + idempotency
  shaping, link resolution for every rule's doc_id (asserts a non-null URL so a
  future KB rename is caught), junk-answer coercion.
- `backend/tests/test_compliance_api_e2e.py` — full-app TestClient (SQLite
  in-memory) for the 5 endpoints incl. ownership + task creation.
- Target: keep `main` green; ~20+ new tests.

## Out of scope (v1 / YAGNI)

- AI-written explanations (deterministic only).
- Auto-scanning proposal text to pre-fill answers (chosen: explicit questionnaire).
- Biosafety/IBC, human stem cells, DURC, data-security/CUI as separate items
  (fold into `review` guidance later if needed).
- A dashboard/portfolio compliance roll-up across all proposals.
