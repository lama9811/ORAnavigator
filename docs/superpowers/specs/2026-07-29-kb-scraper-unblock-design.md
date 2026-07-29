# KB scrape Job — unblock the admin button

**Date:** 2026-07-29
**Status:** approved
**Scope:** make the existing **Run scrape** button work. No new capability.

## Problem

Clicking **Run scrape** in the admin dashboard fails with:

```
Could not start scrape: 403 Permission 'run.jobs.runWithOverrides' denied on
resource 'projects/infra-vertex-494621-v1/locations/us-central1/jobs/oranavigator-kb-scraper'
```

Investigation found **three** independent causes, not one.

### 1. The Cloud Run Job does not exist

`gcloud run jobs list --region=us-central1` returns *Listed 0 items*, and Artifact
Registry holds no `kb-scraper` image. The Job was never built. `cloudbuild.kb-scraper.yaml`
is deliberately excluded from the merge-triggered `cloudbuild.yaml`, so nothing
ever built it automatically and the manual `gcloud builds submit` was never run.

The backend code is live in prod (revision `oranavigator-backend-00195-7xs` answers
`/api/admin/kb-scrape/status` with 403-auth while an unknown route 404s), so the
button exists and calls a Job that does not.

### 2. The documented IAM role is the wrong role

`cloudbuild.kb-scraper.yaml:25-28` and CLAUDE.md both instruct granting
`roles/run.invoker`. The backend service account **already holds that role
project-wide**, and the call still fails, because:

| Role | `run.jobs.run` | `run.jobs.runWithOverrides` |
|---|---|---|
| `roles/run.invoker` | ✅ | ❌ |
| `roles/run.jobsExecutorWithOverrides` | ✅ | ✅ |
| `roles/run.developer` | ✅ | ✅ (plus ~80 others) |

`kb_scrape_service.py:112-118` passes a container override to inject `SCRAPE_RUN_ID`,
so it needs `runWithOverrides`. Following the documented instruction would have
granted a role the SA already had and produced the identical 403.

### 3. The build config cannot build from a local directory

`cloudbuild.kb-scraper.yaml` tags the image `kb-scraper:${SHORT_SHA}`. `SHORT_SHA`
is populated only for repo-triggered builds; on `gcloud builds submit --config=... .`
it is empty, yielding the invalid tag `kb-scraper:`.

## Design

### Model: `gemini-3.6-flash`, scraper only

The adjudicator moves from `gemini-2.5-flash` to `gemini-3.6-flash`. It is an
offline batch judgment over ~59 pages where quality matters and latency does not,
and it decides materiality plus drafts replacement KB text.

**`gemini-3.6-flash` is not served from `us-central1`** — verified by direct
`generateContent` calls against this project:

```
gemini-3.6-flash  us-central1  404
gemini-3.6-flash  global       200
gemini-2.5-flash  us-central1  200
```

`adjudicator.py:145-148` builds its client with
`location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")`, which the Job sets to
`us-central1`. Changing `SCRAPE_MODEL` alone would 404 on every adjudication, and
because golden rule 3 makes model failure fall back silently, the run would report
**zero changes** and look clean. That failure mode is the reason this needs its own
location variable rather than a one-word model swap.

New env var `SCRAPE_MODEL_LOCATION`, read **only** by `adjudicator.py`, defaulting
to `GOOGLE_CLOUD_LOCATION` so every other Gemini caller is unaffected. The chat
agent, coaching, budget prose, and opportunity ranking stay on `gemini-2.5-flash`
in `us-central1`.

Note: 3.6-flash returns `thoughtSignature` parts — thinking is **on by default**.
Acceptable for an offline adjudicator, but the adjudicator parses JSON from the
response, so the dry run must confirm parsing still holds.

### Behavior: propose-only

The working tree's uncommitted revision of `kb_scraper/run.py` (plus matching
backend and frontend changes) replaces auto-apply with **propose-only**: the Job
never modifies a document, and every material change becomes a `pending` row in
`scrape_changes` that an admin approves in the dashboard. It also adds
first-sighting **baselining** — on a page's first crawl the fingerprint is
recorded and nothing is adjudicated, because the stored `content` is an LLM
summary written in May rather than page text, so comparing it to live text would
mark essentially every page "material" and propose rewrites of correct documents.

This ships instead of the merged auto-apply behavior. A first run against the live
KB should not write to it.

## Steps

1. `adjudicator.py` — read `SCRAPE_MODEL_LOCATION`, defaulting to `GOOGLE_CLOUD_LOCATION`.
2. `cloudbuild.kb-scraper.yaml` — set `SCRAPE_MODEL=gemini-3.6-flash` and `SCRAPE_MODEL_LOCATION=global`.
3. Commit the propose-only working tree so the image matches a known commit.
4. Build and deploy the Job: `gcloud builds submit --config=cloudbuild.kb-scraper.yaml . --substitutions=SHORT_SHA=$(git rev-parse --short HEAD)`.
5. Grant `roles/run.jobsExecutorWithOverrides` to the backend SA, **scoped to the job resource**, not the project.
6. Correct the wrong role in `cloudbuild.kb-scraper.yaml` and CLAUDE.md.
7. Verify.

Least privilege in step 5 is deliberate: the role is three permissions
(`run.jobs.run`, `run.jobs.runWithOverrides`, `run.executions.cancel`) — exactly
what the backend does — against `run.developer`'s ~80, which include
`run.services.delete`.

## Verification

Evidence required before calling this done:

- `gcloud run jobs list` shows `oranavigator-kb-scraper`.
- `python run.py --dry-run --limit 20` completes, touches no database, and logs
  adjudications — proving the model resolves at `global` and its JSON parses.
- Backend test suite green (`pytest -q --ignore=tests/test_agent_instruction.py`).
- The admin button returns without 403; `gcloud run jobs executions list` shows an
  execution; the `ScrapeRun` row leaves `queued`.
- After a real run: `scrape_changes` rows exist and **no** KB document was modified
  (propose-only invariant).

## Amendment (2026-07-29): the fetch engine is Gemini, by product decision

The scrape engine is `gemini-3.6-flash` via the URL Context tool
(`kb_scraper/gemini_crawler.py`, `SCRAPE_ENGINE=gemini`, the default). Playwright
remains available as `--engine=playwright` but is not what the button runs.

Measured on all 59 live ORA URLs before deploying:

| | Gemini engine | Playwright engine |
|---|---|---|
| Pages readable | 33 / 59 | 59 / 59 |
| Blocked | **26 (all RECITATION)** | 0 |
| Text | model extraction | verbatim |
| Same page ×3, temp 0 | 1444 / 1466 / 1478 chars | identical SHA-256 |

The 26 blocked pages are the compliance core: all of research-compliance, most of
pre-award, post-award reporting, the PI handbooks, and `/ora`. **Those pages are
not monitored.** A change to the IRB, COI, or budget-development page will not be
detected. The pages that do work are About, Mission & Vision, Announcements, Staff
Directory and Funding Sources.

Consequences accepted with this choice:

- **No fingerprint gate.** The extraction is not byte-stable, so `run.py` forces
  `--audit` for this engine rather than reporting that every page moved on every
  run. Every readable page is adjudicated every run — ~2 model calls per page.
- **Weaker grounding.** `_quote_in` verifies the adjudicator's quote against the
  model's own extraction rather than against the page, so golden rule 2 holds more
  loosely here than on the Playwright engine.
- **No discovery.** The work list is the KB's 59 known URLs; a new page on
  morgan.edu is invisible to this engine.

A Gemini-first / Playwright-fallback hybrid was offered and declined; it would have
given 59/59 coverage with Gemini still doing the scraping wherever it can. Revisit
by setting `SCRAPE_ENGINE=playwright`, which needs no code change.

## Out of scope

- Widening reach beyond the 28 single-URL documents (the 31 multi-document pages
  stay report-only).
- Any change to the chat path's model, including the `gemini-3.6-flash` question.
- Widening coverage beyond the 33 pages the Gemini engine can read (see below).
- Scheduling the scrape via Cloud Scheduler (`/api/internal/kb-scrape/run` still has
  no schedule).
