# Page Ledger — every page of an uploaded PDF is accounted for

**Date:** 2026-09-03
**Status:** design, approved in chat, not yet implemented
**Origin:** a PI uploaded an *awarded* NSF EiR package (56-page combined Research.gov
PDF) to Draft Review and was told five sections were missing. All five were in the
document. Their words: *"I don't want them to get false information about their draft."*

---

## 1. The problem, as measured

`~/Desktop/My works/Awarded NSF EIR Porposal (1).pdf`, 56 pages, run through the real
splitter on 2026-09-03.

`services/pdf_sections.split()` labelled **5 sections covering pages 4–37**. Pages 1–3
and 38–56 were assigned to nothing. The nineteen unattributed pages contain the
Biographical Sketch, Current & Pending Support, Synergistic Activities, the **Data
Management and Sharing Plan (p46)**, the **Mentoring Plan (p47)** and **seven pages of
letters (p48–54)** — including the Letter of Institutional Support the review reported
as absent.

Three independent causes, each confirmed by running the code:

**(a) The section-name matcher cannot see past the author's own header.** Every heading
in this document reads `<Section Name> — Dwight Anderson Williams II (L02623274)`.
`pdf_sections._label_block` tier 1 compares the *whole line's* word set against the
section's (`solicitation_profile.section_signature`), so the signature carries
`{dwight, anderson, william, ii, l02623274}` and can never equal `{data, management,
plan}`. Tier 2 is a raw substring test, defeated by an interpolated word
(`Data Management **and Sharing** Plan`) and by the reverse direction
(document says `Mentoring Plan`, universe says `Postdoctoral Mentoring Plan`).
References Cited matched only because its page says nothing else; Facilities matched
only on tier 2.

**(b) The letters cannot be named from their text at all.** Pages 48–54 are letters on
letterhead. The only place in 56 pages that says they are the Supplementary Documents
block is NSF's own Table of Contents on page 5.

**(c) The off-by-one is load-bearing.** `project_description` came back as pages **5–20
(16 pages)** against a true 6–20 (15). Page 5 is the Table of Contents; the universe has
no `table_of_contents` section, so that block is unlabelled, and `_full("project_summary")`
is already satisfied at its stated 1 page, so `pending_forward` folds it onto Project
Description (`pdf_sections.py:341-380`). That produced the PI's false
*"16 pages, over the 15-page limit — an over-length section is returned without review."*

**And it cannot simply be corrected.** `MIN_COVERAGE = 0.60` needs 33.6 of 56 pages.
The five spans cover exactly 34. Returning page 5 drops coverage to 33 and the whole
split is discarded (`pdf_sections.py:458-461`), falling back to the model locate that
once read all 45 pages as References Cited.

### Nothing noticed

There is no counter, log line, or response field anywhere in the review path that
records which pages fell outside every span. Confirmed by grep across `services/` and
`main.py`. The one thing named for the job, `_coverage_warning`
(`draft_review.py:1393-1409`), counts **section keys, not pages**, and requires
`total > 15` sections to fire — so a 9-section NSF profile can never trip it.

### Why a wrong label is worse than a missing one

Traced through `review_draft`. On a ~50-rule review, one mislabelled section costs
**~12 points** against **~2** for the same section merely missing — and the damage is
bidirectional:

- `generic_checks._check_attachment_present` tier 1 returns `addressed` **purely because
  a span exists under that key** (`generic_checks.py:158-164`). A wrong label reports an
  absent required attachment as present.
- Prohibitions default to `clear` (`draft_review.py:881`) and their quote gate runs
  against the *wrong* span (`:889`), so a violated prohibition is downgraded to
  "reported as respected".
- `_quote_in` cannot catch it: it verifies model-against-span, never span-against-label,
  so a wrong finding arrives with a genuine verbatim quote attached.
- A mislabelled span **counts as located**, so it pushes `_coverage_warning` further from
  firing. Mislabelling looks better than not labelling.
- The frontend renders a located section as `label + word_count` only
  (`DraftReviewModal.jsx:716-720`). A wrongly-located section is visually identical to a
  correct one.

**This is the constraint the design is built around: completeness must not be bought
with confidence.**

---

## 2. Non-goals

- Not OCR. A scanned page with no text layer stays unreadable; it is *reported* as such.
- Not a rewrite of `pdf_sections`. Its block detection is correct — 29 blocks on this
  document, every true section start among them — and is kept.
- Not a change to what any rule checks, to `_CREDIT`, or to the score formula.
- Not a quality grade. Unchanged: this counts rules met.

---

## 3. Design

### 3.1 The ledger

A new module `services/page_ledger.py`. For a PDF, code builds one row per page
**before any model call**:

```python
{"page": 12, "section": "project_description", "source": "structure",
 "quote": "...", "verified": True, "chars": 2418}
```

`source` is one of:

| source | meaning |
|---|---|
| `structure` | `pdf_sections` named it. Deterministic; identical every run. |
| `model` | the page walk named it. |
| `blank` | the page has no body text to read or to quote. |
| `unassigned` | nothing could name it. **Rendered, never dropped.** |

The loop is `for page in range(1, n+1)` in Python. A page cannot be skipped; it can only
be *unanswered*, which is a row with `source: "unassigned"` and therefore visible.

### 3.2 Check 1 — the roll call

The walk sends pages in windows and requires exactly one object per page number sent.
Code reconciles the returned page numbers against the window — **by id, not by count**,
the same pattern `_review_batch` already uses (`by_id.get(req["id"])` → explicit
`unclear` row). Any page not returned is re-asked **individually**; still missing after
that becomes `unassigned`.

**`PAGE_WINDOW = 4`, and the size was measured rather than chosen.** Four runs over the
real 56-page document at three window sizes:

| window | pages answered | receipts verified | wall clock | calls |
|---|---|---|---|---|
| **4** | 56/56 | **56/56** | **22.8s** | 14 |
| 12 | 56/56, 56/56 | 55/56, 56/56 | 41.6s, 30.2s | 5 |
| 28 | 56/56 | 54/56 | 23.5s | 2 |

Smaller windows are **more accurate and faster** — short calls run concurrently under
`_MODEL_SLOTS`, so 14 small calls beat 5 large ones on wall clock. Labels were identical
across all four runs except p49, the blank page (§3.3).

This agrees with the published direction. arXiv:2301.08721 §4.1 measures accuracy
falling as batch size rises, *"with a significant drop at b=6 across four out of five
datasets"* (AddSub 86.6 → 68.1 at b=6). BatchPrompt (arXiv:2309.00384, ICLR 2024) states
in its abstract that batching in longer contexts *"will inevitably lead to worse
performance"* and that results correlate with **position within the batch**. It also
agrees with this repo's own strongest measurement — one rule per call gave 92% ten times
out of ten where batch=15 split 83%/92% (`draft_review.py:126-158`).

**Two prompt requirements are load-bearing and come from BatchPrompt's Appendix D**,
which names this exact failure — *"15 generated answers when input batch size is 16"*:
every page is tagged with its **number**, never a bare delimiter, and the prompt states
the **expected count** explicitly. Both were present in the measured probe. Neither is
the guarantee; the reconciliation is.

*Measured across all four runs:* **56/56 answers every time, zero re-asks needed.**

### 3.3 Check 2 — the receipt

Each page's answer must carry a verbatim span of ≥6 words from **that page**. Code
verifies it. This converts "did you read it?" from unanswerable into checkable.

**`text_match.quote_in` MUST NOT be used for this, and this is the sharpest finding of
the investigation.** `_strip_page_furniture` (`text_match.py:195-223`) sets
`floor = len(marks)`; on a whole document that is ~56 and only genuine stamps clear it,
but **on a single page there is one marker, so `floor == 1` and every line qualifies**.
It then deletes up to nine consecutive lines of the author's prose and offers that as an
additional haystack — verified: **a fabricated quote stitched across nine deleted lines
passes.** A receipt checker built on it would accept a forged receipt.

So `page_ledger` implements its own `receipt_ok(page_text, quote)`:

1. Strip page furniture using **`pdf_sections._furniture`'s share threshold**
   (`max(2, int(_FURNITURE_SHARE * n_pages))`), computed over the whole document, which
   does not degenerate. Furniture must be excluded because the same stamp is on all 56
   pages and proves nothing about *which* page.
2. Accept if `quote_in(body, quote)` passes — this inherits the four PDF-artifact
   readings (welded words, dash line-breaks, lost ligatures, curly quotes) that the repo
   has already paid for, minus the furniture path.
3. Otherwise accept if the quote's words appear **as a contiguous sequence, in order**,
   in the body, ignoring punctuation and case, with a floor of 6 words.

Step 3 exists because of a measured case: on p26 the model returned
`Classification of Simple Lie Superalgebras. Functional Analysis and Its` where the page
reads `…Superalgebras”. Functional…` — the model **omitted** a curly quote rather than
substituting a straight one, which `normalize`'s character fold does not cover. The
model demonstrably read the page.

**The adversarial test is the gate on this design.** Each page's quote was checked
against a *different* page: **0 of 56 accepted.** Without the furniture strip, 2 of 56
were accepted — both from near-blank pages whose only quotable line was the stamp.
`tests/test_page_receipt.py` runs this test over the whole document and asserts zero.

**A blank page cannot produce a receipt, and that is correct.** Measured: p40 has 71
non-furniture characters (`Other Personnel Biographical Information / Data Not
Available`) and p49 has **13** — nothing but the page stamp, almost certainly a scanned
signature page. These become `source: "blank"`, not `unassigned`, and the ledger says
so. Threshold: fewer than 40 non-furniture characters.

### 3.4 Check 3 — NSF's own Table of Contents

`pdf_sections.toc_roster` already parses NSF's generated TOC into
`[(section key or None, pages)]` and it already resolves 9 rows on this document. It is
used today only to validate sections the *roster itself* could name.

The ledger holds it against the walk. This is an **external** check — NSF wrote those
counts, not us and not the model — and it is what defends against the wrong-label risk
in §1, because a wrong label almost always breaks the arithmetic.

*Measured, both runs:*

| section | walk | NSF TOC | |
|---|---|---|---|
| Project Summary | 1 | 1 | ✓ |
| Table of Contents | 1 | 1 | ✓ |
| **Project Description** | **15** | **15** | ✓ |
| References Cited | 6 | 6 | ✓ |
| Budget + Justification | 10 | 10 | ✓ |
| Current & Pending | 2 | 2 | ✓ |
| Synergistic Activities | 1 | 1 | ✓ |
| Facilities | 1 | 1 | ✓ |
| Supplementary block p46–54 | 9 (run 1) / 8 (run 2) | 9 | ✓ / differs |
| Biographical Sketch | 3 | 2 | differs |

**A TOC mismatch REPORTS, it never overrides.** Same discipline as `suspicious_yield`
(`pappg_ingest.py`): report, do not raise, and tolerate a legitimate exception. The
biographical-sketch mismatch is *correct* — p40 is an auto-generated NSF "Data Not
Available" filler page the TOC does not count. The document and its own table of
contents genuinely disagree; the ledger shows that rather than picking a side.

**`toc_roster` needs one change:** it currently discards the raw label text of a row it
cannot resolve (`pdf_sections.py:197`), so `Special Information/Supplementary Documents 9`
becomes `(None, 9)` and the *identity* is lost while the count survives. It will return
the raw name alongside, for reporting only. No existing consumer reads the new field.

### 3.5 Precedence, and the Option A reversal

```
chosen (PI override)  >  structure  >  model walk  >  blank  >  unassigned
```

**Structure always wins over the walk.** Where `pdf_sections` named a section, the walk
cannot overrule it; a disagreement is recorded on the row and rendered, never silently
resolved. This preserves the fixed denominator that the 2026-09-02 Option A decision
bought (`assessed` fixed at 46, `located` fixed at 6 across five uploads).

**This design does partially reverse Option A**, and that must be stated plainly rather
than slipped in. Option A stopped the model filling structural gaps because
`locate_sections` is an *unconstrained search over 152,000 characters* and was measured
finding 1 section on one run and 6 on another. The page walk is a different shape: a
**closed-list classification of one page at a time**, reconciled by page id, receipted
against that page, and cross-checked against the funder's own table. The evidence that
earns the reversal:

- two independent walks agreed on **55 of 56 pages**;
- the single disagreement is **p49, the blank page** — run 1 guessed
  `letters_of_collaboration`, run 2 said `unsure`. Under this design that page is `blank`
  either way, so the ledger is identical on both runs;
- every section's page count matched NSF's TOC on both runs except the one genuine
  document/TOC discrepancy above.

`locate_sections`' model half stays exactly as Option A left it — suppressed when the
structure spoke — and continues to serve pasted text, which has no structure to read.

### 3.6 The `MIN_COVERAGE` coupling

`pdf_sections.MIN_COVERAGE` is evaluated *before* any model involvement, which is why
the stolen TOC page is currently holding the split above the floor (§1c). Under this
design the gate moves: `pdf_sections` still refuses on its nine other bail conditions,
but page coverage is assessed **on the completed ledger**, after the walk. The
off-by-one then becomes safe to fix, because the walk supplies the pages the correction
gives back.

The existing `_furniture`, block detection and all other bails are untouched.

---

## 4. What changes, file by file

| file | change |
|---|---|
| `backend/services/page_ledger.py` | **new.** `build_ledger()`, `receipt_ok()`, `_walk_pages()`, `reconcile_toc()`, `spans_from_ledger()`. |
| `backend/services/document_text.py` | `extract_upload` returns `page_texts` (today it is computed and dropped, `:178-258`); builds the ledger for a multi-section PDF; derives `section_spans` from the ledger. Offsets must be built on the same `"\n".join(page_texts)` + `shift` rebase already used at `:249-256`, or they will disagree with existing spans. |
| `backend/services/pdf_sections.py` | `toc_roster` also returns the unresolved row's raw label. `MIN_COVERAGE` gate moves to the ledger. The TOC-page fold (§1c) is fixed. |
| `backend/services/draft_review.py` | accepts `ledger=`; withholds the score when the ledger is incomplete; emits `page_ledger` in the result. |
| `backend/main.py` | threads the ledger through both draft-review endpoints; emits it in the response. |
| `frontend/src/components/DraftReviewModal.jsx` | renders the ledger panel; renders `extraction.sections` provenance, **which the backend already emits and the frontend has never read** (`main.py:4713`, comment: *"so a mis-map is visible on screen"*). |

### Model call parameters

`gemini-3.6-flash` on `location="global"`, named explicitly — `DEFAULT_MODEL` is
`gemini-2.5-flash`, so a caller that forgets silently downgrades, and
`test_every_call_on_both_review_paths_names_the_model_and_the_region` exists for this.
`temperature=0`, `max_output_tokens=8192`, `list_key` set so a bare top-level array is
not discarded (commit `3553be5`: a bare array cost 15 rules and rendered a false 100%).

**`response_schema` is deliberately NOT used, even though `google-genai==1.14.0`
supports it** (verified on the installed package; it is not plumbed through
`gemini_client._build_config` today). Two documented failure modes make it the wrong
instrument here. Constrained decoding drops optional fields — *"silently omitted under
strict modes because the constrained decoder treats them as zero-cost to skip"*
(arXiv:2606.09395) — so any schema used later must mark **every** field required. Worse,
a schema that enforces `minItems` to guarantee one row per page removes the model's
option to omit, leaving **fabrication** as the remaining exit. **A fabricated page-and-quote
is strictly worse than an omission**: an omission is caught by set difference, a
fabrication is caught only by the receipt. Reconciliation plus receipt already gives the
guarantee without inviting that failure.

**Thinking is CAPPED, not disabled** (`THINKING_BUDGET = 1024`, matching `draft_review`).
Disabling it in `draft_review` was measured to drop `assessed` 38.7 → 35.3 with one run
collapsing to 27, because **the reviewer omits rows when it cannot think** — the exact
failure mode this feature exists to prevent. Note the opposite was correct in
`solicitation_requirements`; measure per caller.

### Concurrency

Every call goes through `draft_review._ask_model`, so the page walk contends for the
existing `_MODEL_SLOTS` semaphore (default 8, `REVIEW_MAX_CONCURRENT_MODEL_CALLS`)
rather than adding a fourth uncapped pool. `services/proofread.py` already sits *outside*
that semaphore with 5 concurrent calls; the page walk must not repeat that. Backoffs in
`gemini_client` are `(1.0, 2.0)` with **no jitter**, so an uncapped burst retries in
lockstep and trips quota again.

*Measured cost:* 5 calls, **41.6s** and **30.2s** on two runs. Whole-package review today
is ~50–60s, so the target is ~80–100s, inside the 300s Cloud Run request cap.

---

## 5. Failure modes — what each does

| what happened | ledger row | effect on the review |
|---|---|---|
| model returned no answer for a page | `unassigned` | page named on screen; its section's rules `could_not_locate`; **score withheld** |
| answer returned, receipt fails twice | `unassigned` | as above |
| page has < 40 non-furniture chars | `blank` | reported as *"no readable text — if this is a scan, nothing on it was checked"*; **not** counted against completeness |
| walk and structure disagree | keeps `structure` | disagreement rendered on the row |
| TOC count ≠ ledger count | keeps the ledger | mismatch rendered above the score |
| Gemini unavailable | all `structure` or `unassigned` | today's behaviour exactly: rules unlocated, **score withheld** (golden rule 3) |
| PDF text truncated (`MAX_CHARS`) | ledger not built | existing path unchanged |

**Withholding the score on an incomplete ledger** copies the existing pattern verbatim
(`draft_review.py:1804-1805`, :1891-1900), added after an AI outage rendered a Project
Summary as *100%, green, "No problems found"* in 3 of 10 runs. A percentage computed
over pages we could not confirm we read describes our reading, not the draft.

`verdict()` already refuses to say `clean` without a score; that stays.

---

## 6. Testing

Written first, each observed red before the fix.

**`tests/test_page_ledger.py`**
- every page 1..N has exactly one row, for a synthetic 20-page package
- a model response omitting page 7 triggers exactly one re-ask for page 7
- a page still missing after the re-ask is `unassigned`, never silently absent
- an out-of-order label (page 47 → a section that ended at page 20) is rejected
- structure wins on disagreement, and the disagreement is recorded
- Gemini unavailable → ledger complete, all rows `structure`/`unassigned`, no raise

**`tests/test_page_receipt.py`** — the security tests
- **the adversarial sweep**: for every page, the quote from a *different* page is
  rejected. Asserts 0 accepted.
- **the `quote_in` hole**: a quote stitched across lines that `_strip_page_furniture`
  would delete on a single page is REJECTED by `receipt_ok`. Mutation-tested — swapping
  `receipt_ok` for `quote_in` must turn this red, or the test guards nothing.
- a quote shorter than 6 words is rejected
- a real quote whose only defect is a dropped curly quote is accepted (the p26 case)
- a page whose only quotable line is furniture yields `blank`, not a passing receipt

**`tests/test_toc_reconciliation.py`**
- a section whose ledger count differs from the TOC is reported and **not** overridden
- the p40 filler case: biosketch 3 vs TOC 2 reports a mismatch and changes no span

**`tests/test_page_limit_off_by_one.py`**
- the TOC page is not folded into Project Description
- with the ledger, a 15-page Project Description is `clear` against a 15-page limit

**Live gate, opt-in, never in CI** — `PAGE_LEDGER_GATE=1`, in the manner of
`test_pappg_recall.py`. Runs the real awarded package end to end and asserts:
56/56 answered, ≥54/56 receipted, 0 wrong-page quotes, and Project Description = 15
pages. **If this stops passing, the method is wrong and nothing else it produced should
be trusted.**

---

## 7. Risks

**A wrong label is still possible and still costs ~6× a missing one.** The three checks
narrow it; none eliminates it. Mitigations, in order of strength: structure wins;
the TOC cross-check; the receipt (proves contact with the page, not correctness of the
label); rendering provenance so a PI can see *how* each section was named.

**Evidence is n=1 for the TOC check.** One document, two runs. `toc_roster` already
fails closed on a PDF with no NSF table of contents (`"no NSF table of contents"`), so a
non-Research.gov package simply gets no Check 3 — and the roll call and receipt still
apply.

**Latency roughly doubles** on a whole-package review. Accepted explicitly by the product
owner over the two cheaper options.

**Cost.** 14 extra model calls per 56-page upload (`ceil(pages / 4)`), bounded by
`_MODEL_SLOTS`.

**An API cannot tell us a page was omitted.** `finish_reason == STOP` does not mean
complete — commit `96b4a2d` records a reply that ended one character short at
`STOP` with 1,932 of 8,192 tokens used, costing fifteen rules. There is no
`system_fingerprint` on Vertex Gemini (grepped the SDK: zero occurrences), so a caller
cannot detect a backend change that invalidates a seed, and `seed` is Pre-GA. There is
also an open, unanswered report that `candidateCount` returns fewer candidates than
requested at `FinishReason.STOP` (googleapis/python-genai#1888) — the same
"silently returned fewer than asked, with a clean finish reason" shape, one level up.
**Every completeness guarantee in this design is therefore code-side by necessity, not
by preference.**

---

## 8. Out of scope, raised for a separate decision

**Whole-document semantic rules are judged against the Project Description alone.**
`draft_review.py:1547-1550` files every `section: None` semantic row — which is where
`contract_requirements` puts **every required attachment** (`generic_checks.py:118`) —
into a job whose span is `pd_span`, falling back to the full text only when Project
Description was not located. On this upload those rules read pages 5–20 and nothing else.
This is a genuine case of "the AI is not reading those pages" and is independent of the
ledger. Small to fix; deliberately not bundled here (golden rule 6).

**`extraction.sections` is emitted and never rendered.** `main.py:4713` ships a per-file
`source` (`chosen` / `filename` / `filename_subset` / `filename_narrowed` /
`pdf_structure` / `duplicate`) with the comment *"so a mis-map is visible on screen
rather than silently shaping the score"* — and `DraftReviewModal`'s `ExtractionReport`
reads only `extraction.files`, and only when a file errored. `filename_narrowed`, the tier
the code names as its one confident-wrong-verdict path, reaches no screen and no log.
Rendering it is included above because the ledger panel is the natural home.

**UNVERIFIED, AND BIGGER THAN THIS SPEC IF TRUE: `gemini-3.6-flash` may ignore
`temperature` entirely.** Raised by the API research pass from Google's own model card;
not confirmed against the live endpoint. Every review path in this repo passes
`temperature=0.0` — `draft_review`, `solicitation_requirements`, `solicitation_extractor`,
`proofread`, and the page walk above. If the parameter is ignored, all of them have been
sampling at the model's default, which would be a larger fact about output stability than
anything in this design, and it would partly explain measurements this repo has already
recorded as "non-deterministic at temperature 0" (43 vs 47 requirements on identical
input; 3 vs 5 required attachments). **Check it before drawing further conclusions about
run-to-run variance** — two calls at `temperature=0` and `temperature=2` on a
deliberately open-ended prompt will settle it. Independent of the ledger; recorded here
because this spec cites temperature-0 measurements.

**The LOI attachment row.** `generic_checks` files "Letters of Intent included" at
`section: None`, so `apply_package_scope` (which keys on `section`) cannot reach it and
it stays in the fix list on a package that correctly has no LOI. A fix is already in the
uncommitted working tree (`draft_scope.py` + `test_loi_is_not_in_the_package.py`); note
its rule 2 keys on `check_args`, which `_finding` does not copy onto a finding
(`draft_review.py:1245-1277`), so rule 2 is dead against production shapes and rule 3
(label substring) is doing the work. Separate change.
