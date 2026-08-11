# Solicitation-driven Draft Review

**Date:** 2026-08-11
**Status:** design, awaiting approval
**Branch:** `feat/draft-review-and-budget-fixes`

## The ask

> "The draft should be judged based on the solicitation, doesn't matter what draft it is.
> People should review the EiR by putting the EiR solicitation. If there is any other proposal
> from NIH, you put in the solicitation from NIH, and according to the NIH solicitation you get
> your draft reviewed. Not only one specific EiR one."

One uniform path for every proposal: **attach a solicitation → paste/upload the draft → the draft
is reviewed against that solicitation's own requirements.** NSF 23-598 stops being a special case.

A second, non-negotiable requirement from the same conversation:

> "The solicitation should fully read the PDF without missing anything."

## Where we are today

| Piece | State |
|---|---|
| `services/eir_review.py` (733 lines) | The 4-stage engine: locate → deterministic → semantic → score. Generic in shape, but imports `eir_solicitation` directly. |
| `services/eir_solicitation.py` (476 lines) | ~20 hand-curated requirement rows for NSF 23-598, each carrying the verbatim solicitation sentence. Data only. |
| `POST /api/me/submissions/{id}/eir-review` (+ `/upload`) | `main.py:4304` / `main.py:4350`. Stateless — the draft is never persisted. |
| `MyProposals.jsx:64` `isEirProposal()` | Regex on title/notes. Gates the button so the tool is hidden on 4 of 5 proposals. |
| `Submission` | Has `notes`, `budget_json`, `compliance_json`, `sections_json`. **No column holds the solicitation.** |
| `reconstruct_solicitation_context()` | Re-parses cap / page limits / attachments back out of `notes` prose. |
| `solicitation_extractor.py` | One Gemini pass (`gemini-3.6-flash`, `global`) → a thin contract: sponsor, deadline, page limits, attachments, cap, eligibility, portal. **No narrative requirements.** |

Two gaps stand between this and the ask:

1. **There are no requirements to judge against for any solicitation but one.** The stored contract
   knows the budget cap and which attachments are named; it does not know that the funder asks for
   a sustainability plan, a timeline, or specific success metrics.
2. **The solicitation's text is never persisted** — only the summary parsed into `notes`.

## Design

### 1. Requirements become data attached to the proposal

New column `Submission.solicitation_json` (TEXT, nullable), written with a self-healing migration in
`main.py:init_db()` per golden rule 5. It holds `json.dumps` of:

```json
{
  "contract":     { ...existing solicitation_extractor output... },
  "requirements": [ {id, label, section, kind, scored, source, why, keywords}, ... ],
  "sections":     { "project_description": {label, aliases}, ... },
  "read_report":  {chars, pages, pages_without_text, truncated, engine},
  "extracted_at": "2026-08-11T...",
  "model":        "gemini-3.6-flash"
}
```

Requirement rows are shaped **exactly** like `EIR_REQUIREMENTS` so the engine cannot tell a derived
row from a curated one. `source` is the verbatim solicitation sentence the row came from.

### 2. `services/solicitation_requirements.py` — reading the PDF without missing anything

This is the module the ask turns on. Both failure modes below are silent today.

**Input completeness.**

- `extract_text_from_pdf` already loops every page with no page cap — nothing is lost there.
- `extract_from_text` does `text[:250_000]` with no flag. A long FOA (~80+ pages) silently loses its
  tail, and the extractor's own prompt notes that load-bearing facts appear late. Truncation is now
  **reported**, and the chunked reader below removes the need for it.
- pdfplumber yields nothing for scanned/image pages and there is no OCR. A wholly scanned PDF
  currently returns `None` and surfaces as "couldn't read the PDF."

`read_report` therefore carries `chars`, `pages`, `pages_without_text` and `truncated`, and the UI
shows it. A scan reports *"0 of 34 pages had extractable text"* — never a short requirement list
that looks complete. This is the `looks_unreadable()` invariant from `kb_scraper`: **an unreadable
input is never reported as an absent requirement.**

**Extraction completeness.** One pass drops requirements even when the whole text is in the prompt —
measured on NSF 23-598, `gemini-3.6-flash` returned **3 required attachments on one run and 5 on the
next**, identical input, temperature 0 (recorded in CLAUDE.md). Over 100 pages it gets worse. So:

1. **Chunk** the document into overlapping windows well inside the model's limit; extract rows from
   each; union by `id`.
2. **Sweep until dry** — feed the document back with the list built so far and ask what requirement
   in this text is *not* yet in the list. Repeat until a pass adds nothing (bounded by a max round
   count, and what the bound dropped is logged, never silently truncated).
3. **Quote-verify every row** against the full text with `services.text_match.quote_in`. A row whose
   `source` is not verbatim in the document is dropped (golden rule 2).
4. **Deduplicate** on normalized label + section.

**Where `sections` comes from.** The locate stage needs a section universe, which for EiR was
hand-written. Here it is assembled deterministically from what extraction already produced: the
distinct `section` values the requirement rows carry (the extractor labels each requirement with the
section the solicitation itself names for it), unioned with the contract's `page_limits` keys and
`required_attachments` names. Aliases for each section are generated from its label — the label
itself, lowercased, plus its de-numbered form — which is what `_heading_regex` matches against. A
requirement whose section is not in that universe is filed under a catch-all `other` section so it is
still scored, never dropped.

Cost is several model calls per solicitation instead of one. That is affordable because it runs
**once at attach time and is stored** — never per review.

**Human reviews before commit (golden rule 4).** The extracted requirement list is returned for
review and saved only when the PI confirms, exactly like the existing extraction flow. This is the
mitigation for a model-derived requirement universe: the PI sees every ask and its quote before
anything is judged against it.

### 3. `services/draft_review.py` — the engine, solicitation-agnostic

`eir_review.py` is renamed and takes `requirements` + `sections` as arguments instead of importing
`eir_solicitation`. The four stages, the `could_not_locate` rule, the quote verification, the
code-computed score and its suppression when the AI layer is down are all **unchanged** — this is a
parameterization, not a rewrite.

What generalizes in the deterministic stage (stage 2): page limits per section, required attachments
present, budget vs cap — all read from the contract, which has real numbers for them.

**Explicit loss, stated plainly:** EiR's bespoke code-only rules (30% equipment cap, 2-page
institutional letter, `Excellence in Research:` title prefix, cost-sharing prohibition, LOI/full
deadline math) have no generic equivalent. Under the uniform path those become *semantic* rows
carrying their solicitation quote — still checked, still grounded, but by the model rather than by
arithmetic. Anything the contract holds as a number stays deterministic.

### 4. `eir_solicitation.py` becomes the accuracy fixture

It stops deciding anything at runtime and stays in the tree as the **only human-verified requirement
list we have**. A test runs `solicitation_requirements` over the NSF 23-598 PDF and asserts it
recovers those ~20 rows. That is the recall floor for the derived extractor; without it there is no
way to know whether generic extraction is good enough to ship.

### 5. UI

- `isEirProposal()` deleted. **Draft Review appears on every proposal**, in the Review & submit stage.
- No solicitation on file (every proposal created before this change, and every hand-made one) → the
  modal opens on an **attach step**: upload the PDF or paste the URL, reusing the existing extractor
  and the SSRF-guarded `url_fetcher`. Extract → review the requirement list → save → then the draft box.
- The modal header names what it is judging against ("Reviewed against NSF 23-598" / "…against
  PAR-24-118"), and the requirement list is expandable with each row's quote, so a PI can see the
  asks are real and flag a bad one.
- The read report is visible wherever it is not clean.
- Score semantics unchanged: code-computed completeness against *this* solicitation, withheld
  (`score: None`) when Gemini is unavailable, `could_not_locate` never rendered as missing.

### 6. Endpoints

`POST /api/me/submissions/{id}/draft-review` and `/draft-review/upload`, plus
`POST /api/me/submissions/{id}/solicitation-requirements` (extract → preview) and a `PUT` to save the
confirmed set. The two `/eir-review` paths stay as thin aliases so nothing in flight breaks.

## Risks

| Risk | Mitigation |
|---|---|
| Requirement universe is model-derived, not fixed in code — the guarantee EiR had is weaker | Every row quote-verified against the PDF; the PI reviews the list before it is saved; the list is visible at review time |
| `gemini-3.6-flash` is non-deterministic — two extractions of one PDF can differ | Extract once and store; chunk + sweep-until-dry raises recall; the NSF 23-598 fixture measures it |
| A scanned PDF yields no requirements | `read_report` says so explicitly; the review refuses to score rather than reporting everything missing |
| Several model calls per solicitation | One-time at attach, not per review |
| EiR loses its bespoke deterministic checks | Stated above as an accepted trade for uniformity; they survive as quoted semantic rows |

## Out of scope

- OCR for scanned solicitations (reported, not solved).
- Re-extracting requirements for the four proposals already in the local DB — they use the attach step.
- Editing individual requirement rows by hand after saving (accept-or-reject the extracted set only).
