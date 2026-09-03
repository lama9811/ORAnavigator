# Solicitation Ingestion (AI Agent)

**In one line:** drop a sponsor's funding PDF and it becomes a tracked proposal with a task list —
read page by page, so nothing in the solicitation is missed.

## What it does (plain English)
Upload an NSF/NIH/DoD solicitation PDF (or paste a link). The agent reads **every page** and pulls
out the deadline, budget cap, page limits, required attachments, eligibility, and the formatting
rules (font/margins/spacing). You **review** the extracted fields — each shown next to the sentence
and page number it came from, with genuinely unsupported values flagged red — confirm, and it
creates a Submission with a checklist of tasks.

## Where it lives
- `backend/services/solicitation_extractor.py` (pdfplumber + Gemini, two-stage sweep).
- `backend/services/proposals_service.py` (`create_submission_from_solicitation`,
  `reconstruct_solicitation_context`).
- `frontend/src/components/SolicitationUploadModal.jsx` (review screen).

## How it works

### Full coverage is a property of the code, not a promise from the model
The PDF is split into per-page text, grouped into ~90k-character slices, and **each slice is sent to
Gemini separately, in parallel**. Every page lands in exactly one slice, so no page can be skipped.
A second call folds the merged findings into the strict JSON contract.

Measured on NSF 24-1 (the PAPPG — 216 pages, 748,400 chars):

| approach | page limits found |
|---|---|
| single pass truncated at 250k chars (pre-2026-09-03) | 5 |
| single pass over the **full** text, gemini-3.6-flash | 6 |
| **page-by-page sweep** | **31** |

The single full-text pass *reported* `"pages_examined": 216` and still missed 25 of them — a model's
self-report of coverage is not coverage. The response carries a `coverage` block
(`pages_total` / `pages_read` / `slices_failed`) that the UI shows as "Read all 216 pages", and it
says so honestly when a slice fails.

### Special proposal types don't poison the headline numbers
NSF 24-1 states a 2-page Project Description for an Ideas Lab preliminary proposal. Applying "most
restrictive wins" blindly would tell every PI their Project Description is 2 pages and fail a
compliant 15-page draft. So the headline `page_limits` / `budget_cap` describe a **standard full
proposal**, and everything type-specific goes to `page_limit_variants` / `budget_cap_variants`
tagged with its `applies_to` (RAPID $200k, EAGER $300k, planning $100k/yr, RAISE $1M…). Nothing is
lost; nothing is misapplied.

### Flags mean something
`_verify_source_quotes_detailed` returns two things:
- `unverified_fields` — filled values with **no** grounded evidence at all (red, "don't trust this").
- `partially_verified` — `{field: [entries]}` when a field supplies one quote per entry and only
  some fail (amber, "check just these").

Evidence is accepted in the shapes the model actually produces: one quote per field, **one quote per
entry** (page limits usually), the value itself appearing verbatim (identity fields, including
multi-part ones like "Research.gov, Grants.gov"), or the items of a merged list being named in the
document (`required_attachments`). Before this, `page_limits` and `eligibility` were flagged red on
100% of runs with correct, verbatim quotes — a warning that is always on is the same as no warning.

### Two-step by design
Extract → human confirms → commit. A wrong AI deadline never auto-commits. This is the canonical
"AI proposes, deterministic code verifies, human confirms" pattern.

## API & data
- Endpoints: `POST /api/me/submissions/from-solicitation` (PDF),
  `POST .../from-solicitation-url` (link), `POST .../from-solicitation/confirm`.
- Tables: `submissions` (incl. nullable `solicitation_json`), `submission_tasks`.
- `submissions.solicitation_json` stores the **full confirmed extraction** — every source quote,
  the page each value came from, the variants, the formatting rules, the coverage audit. The `notes`
  blob remains the lossy, user-editable summary; the column is the receipt, so months later
  "where did 15 pages come from?" still has an answer.
- `reconstruct_solicitation_context` still reads `budget_cap` / `page_limits` /
  `required_attachments` from notes+tasks (a user edit must win over the original extraction) and
  layers the extras on from `solicitation_json`.

## Model
- Primary **`gemini-3.6-flash`** in the **`global`** Vertex location — it 404s in `us-central1`, so
  the model and the location move together.
- Automatic fallback to `gemini-2.5-flash` in `us-central1` if the primary pair is unavailable, so
  a user's upload never fails just because a model is down.
- Overridable via `SOLICITATION_MODEL` / `SOLICITATION_LOCATION`.

## Performance
NSF 24-1 (216 pages, 4.6 MB) = 9 slices, ~128s end-to-end (16s pdfplumber + ~60s sweep +
~50s consolidate). A typical 10–40 page program solicitation is 1–2 slices, ~25–35s. `_MAX_SLICES`
(40) bounds the worst case well under Cloud Run's 300s request timeout.

## Validated against real solicitations

**NSF 24-1 (PAPPG, 216 pages)** — the agency-wide rulebook. 216/216 pages read, 5 headline page
limits + 17 special-type variants, 5 per-type budget caps, 14 attachments, **63 other requirements**,
font/margin rules captured, `deadline` and `budget_cap` correctly **null** (the PAPPG states neither),
0 false flags. ~96s.

**NSF 23-598 (HBCU-EiR, 17 pages)** — a real program solicitation, the typical PI upload.
17/17 pages, ~45s, 0 false flags. Graded against the document by hand:

| field | extracted | correct? |
|---|---|---|
| deadline | `2023-10-17` | ✅ the **full proposal** date, not the July 13 LOI date |
| deadline_notes | LOI July 13 + both annual recurrences | ✅ |
| budget_cap | `null` | ✅ **the trap** — the only dollar figure in the document is `$28,000,000`, the *Anticipated Funding Amount* for the whole program, correctly not treated as a per-award cap |
| page_limits | `letter_of_institutional_support: 2` | ✅ verbatim ("no more than 2 pages in length") — the only page limit stated |
| eligibility | accredited HBCUs only | ✅ |
| required_attachments | 6 | ✅ complete (see below) |
| other_requirements | 18 | ✅ incl. the 30% equipment cap, prohibited cost sharing, required grantee-meeting travel, 1-proposal-per-deadline, career max of 2 awards |

**Defect found and fixed by this test:** the first run returned only **3** attachments — it missed
`PROJECT SUMMARY`, `PROJECT DESCRIPTION` and `BUDGET AND BUDGET JUSTIFICATION`, because the
solicitation names them as headed sections that defer their content to the PAPPG, and the sweep read
them as prose rather than as required components. Both prompts now state that a named component is
required **even when its content is deferred to an agency-wide guide**. Recall went 3 → 6 (complete);
NSF 24-1 re-checked for regression (none).

### The recall audit — measured, and re-measured

"It reads every page" is not the same as "it keeps everything it reads", so the two were measured
separately. Every hard, checkable requirement in NSF 23-598 was enumerated **by hand from the PDF**
(34 of them), then graded against the extractor's output — each requirement checked against the
FIELD that should carry it, not merely searched for in the serialized blob (a loose substring grade
scored `"hbcu"` as a hit off the program name and inflated an early figure to 94%).

Graded strictly, over **three fresh runs of the same PDF**:

| | requirements kept |
|---|---|
| before the catch-all | **14 / 34 (41%)** |
| after, run 1 / 2 / 3 | **28 / 24 / 28 → 82% / 71% / 82%** |

**Read both numbers.** The catch-all roughly doubled recall, and the result is **not deterministic** —
the long tail of `other_requirements` varies run to run (13 / 11 / 14 rules captured from the same
document). Do not quote a single run as the accuracy of this feature.

**What IS stable across runs** (verified 3/3): `deadline` = `2023-10-17`, `budget_cap` = `null`
(never fooled by the `$28,000,000` program total), the page limit, coverage 17/17, and **zero false
"unverified" flags**. The high-stakes scalar fields are reliable; the catch-all is a best-effort net.

**Root cause of the original 20 losses was a single missing slot.** The sweep's categories were page
limits, attachments, deadlines, budget caps, eligibility and formatting — so a rule like *"No more
than 30% of the budget can be allocated for equipment"* matched none of them and was simply not
reported. 10 of the 20 were never emitted by the sweep at all; the other 10 were emitted and then
discarded at consolidation. Fixed by adding **`other_requirements`** to the sweep schema, the
contract and the consolidator, with an explicit rule: *never discard a rule because it has no obvious
home*. Each row carries `{requirement, category, applies_to, page, quote}` and is dropped if it has
no supporting quote, so the catch-all cannot become a hallucination back door.

The same audit caught three narrower gaps, all fixed: **eligibility described only the institution**
and not who may serve as PI; **content required *inside* a component** (LOI number in the Project
Summary, labeled Broader Impacts in the Project Description) was not reported; and the
**Letter of Intent date** was not carried into `deadline_notes`.

⚠️ Tightening the deadline wording once regressed `deadline` from `2023-10-17` to `null` — the field
that drives the .ics export and the reminder emails. The rule now states that a specific
full-proposal date **must** be returned even alongside a recurrence and even when past, and that an
LOI date must never be used as `deadline`. **Re-measure after every prompt change.**

**Known misses (consistent across all three runs):**
- the "5 p.m. submitter's local time" cutoff does not reliably reach `deadline_notes`;
- "there are no restrictions or limits" on proposals per organization (the *absence* of a rule);
- the Letter of Intent's own form fields (PI/co-PI contact details, submitting institution's name).

**Known flaky (captured in some runs, not others):** the one-proposal-per-deadline and career-max-2
limits, the non-HBCU subaward rule, the LOI-number and Broader-Impacts content rules, the max-4
personnel / max-5 organizations caps — and, in one run of three, **"Letter of Intent" itself as a
required attachment.** Treat the review screen as something a human must read, not a finished
checklist. Reducing this variance (a second sweep pass, or a deterministic merge across repeated
sweeps) is the obvious next piece of work.

## Don't regress (load-bearing)
- Keep the two-step confirm — never auto-commit extracted fields.
- **Never reintroduce a character cap** on the text sent to the model. That single constant
  (`_MAX_PROMPT_CHARS = 250_000`) silently discarded 67% of NSF 24-1, including its entire Proposal
  Preparation Checklist. `test_no_truncation_constant_survives` guards this.
- Coverage must stay a code guarantee (slice every page), never an instruction to the model.
- Don't let a special-proposal-type value overwrite a headline `page_limits` / `budget_cap`.
- `source_pages` values must be flat integers — the review modal renders them directly, and a bare
  object as a React child blanks the whole screen (hit live 2026-09-03).
- Keep flags precise. Widening `unverified_fields` back to "any field without one plain string
  quote" retrains users to ignore red.

## Status
✅ Built & deployed (original). **Rewritten 2026-09-03 for full-document coverage +
gemini-3.6-flash — built, tested and verified locally end-to-end, NOT yet deployed to Cloud Run.**
Tests: `backend/tests/test_solicitation_extractor.py`, `backend/tests/test_solicitation_accuracy.py`.
