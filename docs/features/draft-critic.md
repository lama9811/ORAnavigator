# Draft Critic (AI Agent)

**In one line:** checks a draft proposal PDF against the solicitation's rules before you submit.

## What it does (plain English)
Upload your draft. It checks it against the reconstructed solicitation requirements — page limits,
required attachments, budget cap, sections — and gives a verdict banner plus an advisory AI review.
Catches "you're over the page limit" / "you forgot the data-management plan" before ORA does.

## Where it lives
- `backend/services/draft_critic.py`.
- Frontend: the "Critique Draft" button in `frontend/src/MyProposals.jsx`.

## How it works
- **Deterministic core is authoritative** — page/attachment/section/budget checks + the verdict.
- **Advisory `ai_review`** (Gemini, `include_ai=True`) adds prose feedback with a strict prompt;
  `_verify_evidence` **drops any finding not quote-backed by the draft**. The AI never alters the verdict.
- **Frontend gating:** the button shows **only** for proposals that carry solicitation rules —
  `hasSolicitation()` checks the line-anchored notes (`^Budget cap:`, `^Page limits:`,
  `^Required attachments:`) or a `Prepare required attachment:` task. Manual proposals (no rules)
  hide the button (a critique with nothing to check is useless).

## Don't regress (load-bearing)
- AI is advisory only; deterministic core wins.
- Keep the `hasSolicitation()` gating in sync with `reconstruct_solicitation_context`.

## Status
✅ Built & deployed.
