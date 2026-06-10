# Compliance Sentinel (deterministic, no AI)

**In one line:** answers a short questionnaire + reads the proposal's sponsor and tells a PI exactly which approvals their project needs — IRB, IACUC, COI, RCR, Export Control / Research Security — each with a why, a timing note, and the right Morgan form.

## What it does (plain English)
On a proposal, click **Check compliance**. Answer ~5 yes/no questions (human subjects? animals?
financial interest? foreign collaborators? export-controlled tech?). A live checklist shows each
compliance area as **Required / Review / Not required**, with a plain-English reason, a realistic
timing note ("IRB review commonly takes 3–6 weeks — submit early"), and an **Open form** link.
Some requirements come from the **sponsor** automatically (NSF/NIH → RCR training; PHS/NIH → COI
disclosure for all investigators), so they're not even asked. **Save** persists the answers, and
**Add required to my proposal** creates the matching tasks (with the form linked) on the proposal
tracker.

## Where it lives
- `backend/services/compliance_sentinel.py` — deterministic core (`assess_compliance`,
  `suggested_tasks`, `questionnaire`, the `RULES` table).
- `backend/services/forms_catalog.py` — `resolve_kb_doc()` + a `get_form()` KB-index fallback so
  compliance hub/training docs (which aren't form-like) still resolve a live Open-form link.
- Endpoints in `backend/main.py`: `GET /api/compliance/questions`, `POST /api/compliance/assess`,
  `GET`/`PUT /api/me/submissions/{id}/compliance`, `POST /api/me/submissions/{id}/compliance/tasks`.
- Storage: `submissions.compliance_json` (nullable, self-healed in `init_db()`). `has_compliance`
  flag on the submissions listing.
- Frontend: `frontend/src/components/ComplianceSentinelModal.jsx` (+ `.css`, rendered via a
  **portal**), wired into the **Check compliance** button in `MyProposals.jsx`.

## How it works
- **100% deterministic — no LLM.** Which approvals are required is decided entirely by code rules
  (same trust model as the Budget Helper's math). The explanations are deterministic templates.
- **Two trigger sources:** (1) the yes/no questionnaire; (2) the proposal's `sponsor`
  (NSF/NIH → RCR; PHS/NIH → COI for all investigators, regardless of the disclosed interest).
- **Statuses:** `required` (a trigger is met), `review` (needs human judgment — e.g. foreign
  collaboration → possible NSPM-33 Research-Security review — or an unanswered gating question),
  `not_required`.
- **Verified links, never fabricated.** Each rule points at a real KB doc id
  (`form_irb_approval_request`, `compliance_iacuc_forms`, `form_coi_fcoi_sponsored_disclosure`,
  `form_citi_training_program`, `compliance_research_security_technology_control_plan` /
  `_nspm_33`). The unit tests assert every required/review item resolves a live URL, so a KB rename
  fails the build instead of shipping a dead link.
- **Add-tasks is idempotent** — re-running skips any task whose title already exists on the proposal.

## Coverage (v1)
IRB (human subjects), IACUC (animals), COI (financial conflict / PHS), RCR training (NSF/NIH),
Export Control / Research Security (export-controlled tech → TCP; foreign collaboration → NSPM-33
review).

## Tests
`backend/tests/test_compliance_sentinel.py` (22 unit) + `backend/tests/test_compliance_api_e2e.py`
(7 e2e). Spec: `docs/superpowers/specs/2026-06-10-compliance-sentinel-design.md`.

## Out of scope (v1)
AI-written explanations; auto-scanning proposal text to pre-fill answers; biosafety/IBC, DURC, data
security/CUI as separate items; a portfolio-wide compliance roll-up.
