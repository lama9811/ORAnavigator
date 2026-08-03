# KB scrape: Playwright reads, Gemini judges

**Date:** 2026-08-03
**Status:** Design approved, pending implementation
**Branch:** `worktree-kb-scrape-playwright-engine`

## Goal

Clicking **Run scrape** in the admin dashboard should crawl the whole ORA section of
morgan.edu — including the pages the current engine cannot read — and report what
genuinely changed. No manual database step, no flood of junk proposals to clear.

## Problem

The scrape job defaults to `SCRAPE_ENGINE=gemini` (`kb_scraper/gemini_crawler.py`),
which sends each URL to `gemini-3.6-flash` with the URL Context tool and asks for a
markdown extraction. Two measured properties make it unfit for the job:

1. **26 of 59 ORA URLs (44%) come back empty with `finishReason: RECITATION`** — the
   safety filter for reproducing source material. The blocked set is the compliance
   core: all of research-compliance (human subjects, animal research, COI, research
   security, NSPM-33, RCR), most of pre-award, post-award reporting, the PI handbooks,
   and `/ora` itself. Those pages are reported unreadable and left alone, which is the
   safe behaviour — but it means **they are not monitored at all**.

2. **The extraction is not byte-stable.** One unchanged page read three times at
   temperature 0 measured 1444/1466/1478 chars — three different hashes. Because a
   fingerprint that never matches cannot gate anything, `run.py:147` force-enables
   `--audit` for this engine, disabling the fingerprint gate entirely. Consequence:
   every mapped page is re-adjudicated on every run, and a page the model keeps
   judging material produces a **fresh pending proposal every run**, with nothing
   deduplicating against previously-rejected ones.

## Approach

Switch page reading to the **Playwright engine that already exists** (`kb_scraper/crawler.py`,
`--engine=playwright`), and leave adjudication on Gemini.

Playwright was the original engine; the Gemini engine was added later in `557da89`, not
because Playwright was broken. Compared to any LLM reader it:

- has no safety filter, so no page is refused
- returns byte-stable text, so the fingerprint gate works and only genuinely changed
  pages cost a model call
- **clicks open the accordions** — 249 of 382 documents are `playwright_verified`
  because the IRB/IACUC content is not in the served HTML
- discovers links, so pages with no corresponding document can be surfaced
- verifies adjudicator quotes against the **actual page text** rather than the model's
  own paraphrase, restoring golden rule 2 to full strength
- costs nothing per page (~3s/page)

**Not chosen:** Claude as the page reader. Claude's `web_fetch` tool is unavailable on
Vertex AI (the repo's only model path), so it would require a first-party Anthropic API
key, a new Secret Manager entry and IAM binding, and new egress — to obtain something
strictly worse than the browser already in the image (no accordion expansion, no link
discovery, no byte-stable text).

## Design

### 1. Engine default

`kb_scraper/run.py` — flip the `--engine` default:

```python
default=os.getenv("SCRAPE_ENGINE", "playwright"),   # was "gemini"
```

The forced-audit block at `run.py:147` is already conditioned on
`args.engine == "gemini"`, so it stops firing on its own and the fingerprint gate
returns. The Gemini engine remains fully available via `--engine=gemini` or
`SCRAPE_ENGINE=gemini`, with its forced-audit behaviour intact.

### 2. Engine-scoped fingerprints

`KbPageFingerprint.fingerprint` is a SHA-256 of the extracted page text. Gemini's
markdown extraction and Playwright's `inner_text()` of the *same unchanged page* hash
differently, so every existing row is a stale baseline for the new engine.

Left alone, the first Playwright run takes the "something moved" branch for every page:
~59 model calls and up to 59 bogus pending proposals. The `known is None` baseline
branch at `run.py:270` never fires, because a stale row is not `None`.

The removed-page sweep breaks the same way: `gone = [u for u in prior if u not in seen_urls]`
compares against every URL in the table, so the Gemini-era URL set would generate a
batch of proposals claiming pages had been deleted from the site.

**Fix:** record which engine produced each hash, and only trust same-engine rows.

```python
# models.py — KbPageFingerprint
engine = Column(String(20), nullable=True)   # "playwright" | "gemini"; NULL = pre-migration
```

- Added with a self-healing migration in `main.py:init_db()` (try `SELECT engine`; on
  error `ALTER TABLE ADD COLUMN`) per golden rule 5. No Alembic.
- `run.py` loads `prior` filtered to the current engine, and tags every fingerprint it
  writes with that engine.
- A row written by another engine (or a pre-migration `NULL`) is therefore invisible to
  the comparison — the page reads as a first sighting, hits the existing baseline
  branch, records its hash, and proposes nothing.

**Accepted limitation:** `url` is `unique=True`, so there is exactly one row per page.
The engine tag records who last wrote it; it does not preserve a per-engine baseline
side by side. Switching engines therefore re-baselines rather than restoring a previous
baseline. Keeping both would require replacing the unique index with a composite key —
a heavier and riskier migration on prod MySQL than the ADD COLUMN convention this repo
uses, for a case that does not arise in normal operation.

**Rejected alternatives:** truncating `kb_page_fingerprints` before the first run does
the same thing manually, and produces ~59 junk proposals the one time it is forgotten
(there is no bulk reject in the UI). Doing nothing produces them immediately.

### 3. Unchanged

Everything outside page reading and the fingerprint baseline stays as it is:

- `adjudicator.py` — `gemini-3.6-flash`, `SCRAPE_MODEL_LOCATION=global`,
  `thinking_budget=0`, verbatim-quote grounding via `_quote_in`
- The proposal model — the job never writes to a document; `approve_change()` is still
  the only path that does
- `kb_scrape_service.py`, all `/api/admin/kb-scrape/*` endpoints, `KbScrapePanel.jsx`
- `kb_scraper/Dockerfile` — Playwright and Chromium are already in the image
- Job resources — `--memory=2Gi` and `--task-timeout=3600` were already sized for
  Chromium (1Gi OOM-kills partway through a crawl)

## Behaviour after the change

```
Admin clicks Run scrape
  → POST /api/admin/kb-scrape/run                       (unchanged)
  → Cloud Run Job, SCRAPE_RUN_ID injected               (unchanged)
      ├ crawl(seeds=["/office-of-research-administration"])  ← Playwright, breadth-first
      │    strips header/footer/nav, expands every accordion,
      │    extracts main-content text + in-scope links
      ├ unreadable?                → skipped row, document untouched
      ├ hash == this engine's baseline? → unchanged, no model call
      └ hash moved / first sighting     → adjudicate (Gemini) → pending proposal
  → panel polls /status every 2s                        (unchanged)
  → admin approves                                      (unchanged; only path that writes)
```

Run 1 baselines silently. From run 2 on, only genuinely changed pages cost a model
call — typically none.

### Expected new behaviour

**New-page proposals.** Playwright follows in-scope links, so it will reach pages the KB
has no document for. Each becomes a `change_type="new"` pending row. On the first run
this may be dozens. This is kept deliberately: it is a genuine inventory of ORA content
with no corresponding document, which is useful given the known KB content gaps.

**One deliberate audit sweep.** After run 1 establishes the baseline, a single `--audit`
run will surface drift accumulated since the KB was written in May — nothing has ever
compared these documents against the live pages. This is an operational step, not code,
and the admin button cannot trigger it: `--audit` has to be passed as an args override
on a manual `gcloud run jobs execute` (alongside `SCRAPE_RUN_ID`, which the backend
normally injects).

## Risks

| Risk | Mitigation |
|---|---|
| Playwright's live pass rate on morgan.edu is unmeasured (the 56% Gemini figure is measured; this one is not) | `--dry-run --engine=playwright --limit 20` smoke test against the live site, first task in the plan, before any code change |
| A page that fails to render looks like deleted content | Already handled — `looks_unreadable()` routes 404s, timeouts, empty renders and 404-bodies-served-with-200 to a `skipped` row that never touches the document |
| Crawl exceeds the 3600s task timeout | ~3s/page + 0.4s politeness delay; `MAX_PAGES=600` is a runaway guard. ~59-150 pages ≈ 5-10 min. Measured in the smoke test |
| First run floods the review queue with new-page rows | Accepted (see above). If it proves unusable, gate new-page proposals behind a flag in a follow-up |

## Testing

Added to `backend/tests/test_kb_scraper.py` (26 existing tests stay green):

- the `--engine` default resolves to `playwright`
- the forced-audit branch fires for `gemini` and does **not** fire for `playwright`
- a fingerprint written by one engine is not treated as a baseline by another
- a `NULL` engine row (pre-migration) is treated as a first sighting, not a change
- the removed-page sweep only considers same-engine URLs
- the `init_db()` migration is idempotent

Plus the live smoke test, run by hand and its result recorded in the plan.

## Rollout

1. Smoke-test Playwright against the live site (`--dry-run --limit 20`)
2. Implement, with tests green (`cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py`)
3. Rebuild the scraper job image — `gcloud builds submit --config=cloudbuild.kb-scraper.yaml . --substitutions=SHORT_SHA=$(git rev-parse --short HEAD)`. The `SHORT_SHA` substitution is required; a local submit leaves it empty and the image tag becomes the invalid `kb-scraper:`
4. Deploy the backend (for the `init_db()` migration) — `gcloud builds submit --config=cloudbuild.yaml .`, and **confirm the deploy actually ran** with `gcloud run services describe <svc> --region=us-central1 --format='value(status.latestReadyRevisionName)'`. A merge to `main` ships nothing
5. Run 1 from the admin panel — expect a silent baseline plus new-page rows, and zero modification proposals
6. Run 2 — expect near-zero changes on a static site
7. Optionally, one manual `--audit` run to sweep drift since May

## Out of scope

- Claude as either reader or adjudicator
- Deduplicating proposals against previously-rejected ones (the fingerprint gate makes
  this far less pressing, but it is not solved)
- A Cloud Scheduler entry for `/api/internal/kb-scrape/run` (still unscheduled)
- Re-splitting multi-document pages; those 31 URLs feeding 355 documents remain
  report-only
