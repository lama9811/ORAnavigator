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
- **Deterministic core is authoritative** — page/attachment/section/budget/formatting checks + the verdict.
- **Advisory `ai_review`** (Gemini, `include_ai=True`) adds prose feedback with a strict prompt;
  `_verify_evidence` **drops any finding not quote-backed by the draft**. The AI never alters the verdict.
- **Frontend gating:** the button shows **only** for proposals that carry solicitation rules —
  `hasSolicitation()` checks the line-anchored notes (`^Budget cap:`, `^Page limits:`,
  `^Required attachments:`) or a `Prepare required attachment:` task. Manual proposals (no rules)
  hide the button (a critique with nothing to check is useless).

### Formatting check (font & margins, added 2026-09-03)
Sponsors return proposals **without review** over type size and margins (NSF PAPPG: "Arial ... at a
font size of 10 points or larger", "Margins, in all directions, must be at least an inch"). The
solicitation extractor now captures those rules; `check_formatting` measures the draft's own glyph
geometry (`_measure_layout`) and compares.

- `font_pt` = the **most common** character size (the body text), so figure captions and subscripts
  don't drag it down. The required size is the **smallest** the solicitation permits, since a rule
  like "Arial 10pt or larger; Times New Roman 11pt or larger" makes only sub-10pt unambiguously wrong.
- `margin_in` = **left/right only**, median across pages. Top and bottom are deliberately excluded:
  running headers, footers and page numbers legitimately sit in that band. Measuring all four edges
  reported **0.49"** on NSF 24-1 — a professionally typeset 1-inch-margin document — while the side
  margins correctly measured **0.96"**. Checking the vertical edges would warn on compliant drafts.
- The row **warns, never fails**, and is omitted entirely when the solicitation states no such rule.

## Don't regress (load-bearing)
- AI is advisory only; deterministic core wins.
- Keep the `hasSolicitation()` gating in sync with `reconstruct_solicitation_context`.
- The formatting check must stay **warn-only** and must not measure top/bottom margins.
- Don't change `_extract_pdf`'s `(text, page_count, pages_text)` signature — several test suites
  monkeypatch it. Layout measurement lives in the separate `_measure_layout`.

## Status
✅ Built & deployed. **Formatting check added 2026-09-03 — built + tested locally, NOT yet deployed**
(`backend/tests/test_draft_critic_formatting.py`).
