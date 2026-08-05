# KB scrape: cover the documents, not just the pages

**Date:** 2026-08-05
**Status:** Design approved, pending implementation
**Branch:** `fixes`

## Goal

A new PDF posted on the ORA site should end up in the knowledge base, so a PI asking
the chatbot can get an answer from it — after an admin clicks **Approve**, never
before. And a PDF that ORA *revises* should be reported, so nobody is left quoting a
superseded handbook.

## Problem

The scrape covers 100% of ORA's web pages and **0% of its files**.

Verified 2026-08-05 by crawling the ORA section and comparing against the KB:

```
files linked on the ORA site   : 260
files the KB has a record of   : 236   (10 of which the crawl found no link to)
→ in common                    : 226
→ on the site, no KB record    :  34
```

Those 34 are not obscure. They include `test prep/herd.pdf` and
`test prep/internalcontrols.pdf` — the exact two topics an independent page diff
found missing from the test-prep page — the slide decks for the two June 2026 D-RED
seminars the KB is also missing, eight Diversity and EEO attachments (nondiscrimination
policy, sexual harassment policy, prohibited conduct, cultural diversity plans), the
IACUC biological safety manual, the IRB informed consent template, and Title VI.

The gap has three independent causes:

1. **Out of scope by path.** `fingerprint.py:69-80` admits a URL only when its path
   starts with `/office-of-research-administration`. Every ORA file lives under
   `/Documents/ADMINISTRATION/OFFICES/ora/…`, so it fails before anything else runs.
   Note `.pdf` is *not* in `_SKIP_SUFFIXES` — the exclusion is an accident of where
   morgan.edu stores files, not a decision that files don't matter.
2. **The reader cannot read them.** `crawler.py` pulls text from the DOM via content
   selectors. Chromium pointed at a PDF yields the viewer shell, which
   `looks_unreadable()` would correctly reject.
3. **No parser in the image.** `kb_scraper/requirements.txt` is deliberately minimal.
   `pdfplumber==0.11.1` and `pypdf==5.6.0` exist in `backend/requirements.txt` — the
   solicitation extractor already uses them — but are absent from the Job.

The links are also discarded before anything could use them: `crawler.py:143-145`
filters every extracted link through `is_in_scope()`, so file URLs are dropped at the
moment they are found.

## Measured facts that constrain the design

**HTTP metadata cannot be trusted as a change signal on this origin.** The same
unchanged file returns different validators depending on which backend node answers:

| Probe | `2024-07-01_PIHandbook5.pdf` |
|---|---|
| 1, 2, 5 | ETag `"1MOLqAMMqEY34SK6jy5Sbw=="`, Last-Modified `05:09:15` |
| 3, 4 | ETag `"5GeZ+zzekrrgbFoTqxDCPQ=="`, Last-Modified `05:21:00` |
| all five | Content-Length `1319429` — **SHA-256 identical every time** |

`Last-Modified` is worthless besides: only 10 distinct values across 235 files, all
from a bulk re-upload on 2026-08-03. A HEAD-based check would therefore emit false
"changed" reports at random — the same class of defect as the non-byte-stable Gemini
extraction that forced `--audit`. **Detection must download and hash the bytes.**

Cost of doing so: **178 MB across 235 files**, median 210 KB, largest 25.7 MB. Trivial
for a Job that already runs seven minutes and pulls whole pages through Chromium.

**The work list already exists.** 369 of 383 documents carry a `procedure_url`. For 219
it names a PDF, for 43 an Office file, for 107 a web page (already covered by the
crawl). The 236 distinct file targets map to documents almost perfectly 1:1:

```
PDFs feeding exactly 1 document : 187
PDFs feeding 2+ documents       :   6   (32 documents; PI Handbook 5 alone feeds 22)
```

This is the inverse of the HTML case, where 31 URLs feed 355 documents and are mostly
unrewritable. Here 97% of files are unambiguous.

**One link is already broken:** `HR02 Accident Investigation` (IACUC form) returns 403.

## Approach

Add a **file phase** to the existing Job, after the page crawl, in the same run. Every
file is covered, in both directions:

| Case | Behaviour |
|---|---|
| File on the site with no document | **Draft a new document** from its text, queued for Approve. |
| Known file changed, feeds **exactly 1** document | **Draft replacement content** for that document, queued for Approve. 187 of 193 PDFs. |
| Known file changed, feeds **2+** documents | **One draft per derived document** — not one bulk rewrite. Each is queued and approved separately. |
| Unreadable format (DocuSign / Google form) | Link-only document; detected and reported, contents cannot be read. |

**The multi-document case is drafted per document, never re-split.** The dangerous
operation — take a revised PI Handbook and redistribute its paragraphs across the 22
documents that came from it — is not performed. Instead each of the 22 is handled on
its own: the model receives the new file text plus *that document's* current content
and updates only what the file contradicts. The blast radius of a bad draft is one
document with its own diff and its own Undo, and 21 unrelated documents are untouched.
The cost is 22 approvals rather than one, which is the correct trade when the
alternative is an unreviewable bulk edit.

Nothing reaches the datastore without an admin clicking Approve, unchanged from the
2026-07-29 rule. Every applied change stores `previous_content`, so one click undoes it.

### Components

**`kb_scraper/files.py`** — new. Same contract as `crawler.py` and `gemini_crawler.py`
so `run.py` treats it as one more reader:

- `known_files(docs, snapshot)` → `{file_url: [doc_id, …]}` from `procedure_url`,
  snapshot-first with live `struct_data` as overlay — the same precedence and the same
  reason as `_load_url_index`.
- `fetch(url)` → streams the response, computes SHA-256 over the bytes, returns
  `FileResult(url, digest, content_type, size, text, unreadable, error)`.
  Downloads run through a `ThreadPoolExecutor` of 6.
- `extract_text(raw, content_type)` → dispatches on type, capped at **40 pages / 200k
  characters**:

  | Type | Reader | New dependency |
  |---|---|---|
  | `.pdf` | `pdfplumber` | pinned `0.11.1`, matching `backend/requirements.txt` |
  | `.docx` | `python-docx` | yes |
  | `.pptx` | `python-pptx` | yes |
  | `.xlsx` / `.xls` | `openpyxl` | yes (already a transitive dep of pandas-free installs; pin explicitly) |
  | `.doc` (legacy binary) | none | reported, not drafted |

  Three readers are added because "every document" includes the `.docx` IRB informed
  consent template, the IACUC forms and the `.pptx` research-misconduct deck. They are
  small, pure-Python and add no system libraries to the image.

**`crawler.py`** — stop discarding file links. `_fetch` keeps a second list,
`file_links`, of in-scope-adjacent document URLs (morgan.edu host, a document
extension) alongside the existing page links. `is_in_scope` is untouched, so page
crawling behaviour does not change at all.

**`run.py`** — a file phase after the page loop: union the `procedure_url` list with
the file links the crawl saw, fetch and hash each, then branch on known-vs-new.

**Fingerprints reuse `KbPageFingerprint` with `engine="file"`.** No migration. That
column exists to partition baselines by which reader produced them, and the file
downloader is exactly such a reader; file and page fingerprints can never collide.
`doc_ids` holds the derived documents, `char_count` the byte size.

### Change types

| `change_type` | `status` | Carries a draft? |
|---|---|---|
| `file_changed` | `pending` | Yes when the file feeds exactly one document and grounding succeeds; otherwise no, and the UI offers Dismiss only |
| `file_new` | `pending` | Yes, when extraction and grounding both succeed |
| `file_missing` | `skipped` | No — 403/404/timeout |

`KbScrapePanel.jsx` needs three `TYPE_LABEL` entries ("file changed", "new file",
"couldn't fetch"). The existing approvable/review-by-hand split then sorts them
correctly with no further UI work: only `file_new` carries `new_content`.

### Updating an existing document whose file changed

Same extraction and grounding as below, with one difference in the prompt: the model is
given **both** the new file text and the document's current content, and asked to update
what the file now contradicts while **preserving detail the file does not address**.

This matters more than it looks. The stored `content` is not a transcript of the file —
it is LLM-summarised prose plus hand-authored material a scrape cannot regenerate
(`key_facts` in 51 documents, `leadership_history`, `irb_voting_members`, staff
`phone`/`office`). A naive "rewrite from the PDF" would silently delete it. The
mitigations are: an update-not-replace prompt, the before/after diff the admin reads
before approving, and `previous_content` making every approval revertible.

Falls back to a plain report — no draft — when extraction is empty, grounding fails, or
the model is unavailable.

### Drafting a document from a new PDF

1. Extract text. Empty (scanned image, no text layer) → `file_new` with **no draft**,
   reported for manual handling. Never draft from an empty read.
2. One Gemini call — same client and model as `adjudicator.py` — returning
   `{title, content, category, subcategory, quotes[]}`.
3. **Grounding, golden rule 2:** every `quotes[]` entry must appear in the extracted
   text under the whitespace-collapsing comparison (`" ".join(s.split())`) already used
   by `section_coach` and the adjudicator. Any unverifiable quote → drop the draft,
   keep the row as a pointer. A draft that cannot prove it came from the file is worse
   than no draft.
4. Placement: propose a `kb_path` from the file's folder and the page that links it,
   validated against `kb_tree.node_paths()`. Unresolvable → **Unfiled**, which is
   visible and one click from filed. `suggest_doc_id` supplies the `doc_id`.
5. Approve → `create_kb_document` with `source_url` set to the file URL, so citations
   resolve and the next run maps the file to its new document.

`approve_change` currently rejects `change_type == "new"` outright because nothing says
where the document belongs. `file_new` carries a validated `kb_path`, so it gets its
own branch rather than relaxing that guard.

### Invariants preserved

- **A failed read is never a deletion.** 403, 404, timeout, or empty extraction →
  reported, document untouched. The single most destructive thing this job could do.
- **Nothing is written without approval.** The Job's only side effects stay
  `scrape_changes` rows and fingerprints.
- **First run baselines, it does not propose.** Every file in the work list — the
  `procedure_url` set unioned with what the crawl saw, ~270 URLs — is hashed and
  recorded, and zero `file_changed` rows are emitted. Without this, run one reports
  every file as changed. New files are the exception: they are genuinely new
  information, so the 34 do draft on the first run.
- **Model failure degrades to a pointer**, never to silence and never to an
  ungrounded draft (golden rule 3).

## Non-goals

- **Re-splitting** a multi-document file. Each derived document is drafted
  independently; the job never redistributes one file's contents across many documents.
- Reading formats with no pure-Python reader: legacy `.doc`, and **DocuSign / Google
  form links**, which are interactive web forms with no downloadable body. These are
  detected, linked and reported — a document *about* the form, never its contents.
- Badging derived documents in the admin tree when their file moves. Considered and
  declined: one PI Handbook 5 revision would light up 22 documents at once.
- OCR for scanned PDFs.
- Files outside the ORA document tree.

## Testing

`backend/tests/test_kb_scraper.py`, alongside the existing suite, with a fake fetcher —
no network:

1. A stable hash across repeated fetches produces no change row.
2. First run records fingerprints and emits zero `file_changed` rows.
3. A moved hash emits exactly one row naming every derived document.
4. 403/404 → `file_missing`/`skipped`, and the document is not touched.
5. Empty extraction → `file_new` with no draft, never a draft from empty text.
6. A draft whose quote is absent from the extracted text is dropped.
7. A quote differing only in whitespace/line wrapping still verifies.
8. `known_files` prefers the snapshot and overlays live `struct_data`.
9. A changed file feeding one document produces a draft; feeding two or more produces
   a report with no draft.
10. An update draft preserves a hand-authored passage the file does not contradict.

## Risks

- **34 drafts on the first run** — 34 Gemini calls and a large review queue in one go.
  Bounded, because change detection baselines on run one and proposes nothing; only the
  new files draft. If it proves unwieldy, cap drafts per run and carry the rest over.
- **One multi-document file can produce many drafts at once.** A PI Handbook 5 revision
  yields 22 separate drafts to review. That is the deliberate trade against a single
  unreviewable bulk edit, but it is a real review burden and the UI should group drafts
  by their source file so they can be worked through — or dismissed — together.
- **178 MB per run.** Fine today. If ORA's library grows substantially, revisit with a
  Content-Length pre-filter (stable across nodes, unlike ETag) to skip unchanged files —
  it cannot stand alone, since a same-size edit would slip through, but it can cheapen
  the common case.
- **Slide decks make poor KB documents.** Several of the 34 are training presentations;
  extracted slide text is fragmentary. The grounding check limits the damage, and the
  admin can dismiss.
- **`procedure_url` is only as good as the snapshot.** The two copies of KB metadata
  still do not sync, so a file linked only by a dashboard-authored document is found via
  the crawl rather than the snapshot. The union of both sources covers this.

## Open question

Ten files the KB references — all under `/OTT/`, the COI disclosure form, procedures,
flowchart and state ethics law — were not linked from any ORA page the crawl reached.
They still resolve. Either ORA reorganised them or the crawl missed a link. Worth
confirming before treating the "referenced but unlinked" signal as meaningful; this
design does not act on it.
