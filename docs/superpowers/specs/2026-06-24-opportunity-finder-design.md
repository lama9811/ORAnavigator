# Design: Opportunity Finder + Fit Advisor

**Date:** 2026-06-24
**Branch:** `feat/opportunity-finder`

## Problem (researched, cited)
New PIs waste months applying to the wrong program: "up to 60% of proposals are
eliminated on first reading" for poor match; eligibility threshold misses are a
top decline reason; wrong mechanism delays a research program a full cycle. At
under-resourced institutions (36% of HBCUs run sponsored-programs offices with
≤3 staff) there's no research-development office to help a first-timer choose.
ORA Navigator today only helps *after* the PI already has a solicitation in hand
— the discovery/fit gap is unaddressed.

## Goal
Let a PI describe their work in plain text and get a ranked list of **live, open
federal funding opportunities**, each with a **grounded fit explanation**, a
**deterministic institution-eligibility gate**, a **PI-level eligibility
advisory**, and a **mechanism explainer** — then hand a chosen opportunity
straight into the existing proposal pipeline.

## Verified external API (checked live 2026-06-24)
Grants.gov public REST (no key, fixed host `api.grants.gov`):
- `POST /v1/api/search2` — body `{keyword, oppStatuses:"posted", rows}` → `oppHits[]`
  with `id, number, title, agency, agencyCode, openDate, closeDate, cfdaList`.
  (Thin rows — no eligibility/description here.)
- `POST /v1/api/fetchOpportunity` — body `{opportunityId}` → `data.synopsis` with
  `applicantTypes[{id,description}]`, `applicantEligibilityDesc`, `synopsisDesc`,
  `awardCeiling/Floor`, `estimatedFunding`, `costSharing`, `responseDate`,
  `agencyContactName/Email/Phone`, `fundingActivityCategories`, plus
  `synopsisDocumentURLs` / `assistURL` (the real solicitation docs).

## Approach (chosen): deterministic retrieval + AI advisory re-rank/explain
The live API is authoritative for what *exists*; Gemini is advisory and only
ranks/explains what the API already returned (golden rules 1–3). Falls back to
API relevance order + deterministic eligibility when Gemini is unavailable.

## Architecture
**Backend — `services/opportunity_finder.py`:**
1. `extract_query(description, profile)` → search keyword string (deterministic
   keyword pull; profile interests appended as hints).
2. `search_grantsgov(keyword, rows)` → `search2`; returns top-N opp ids + basics.
   Fixed host (no user URL → no SSRF). 5-min cache keyed by keyword.
3. `fetch_opportunity(opp_id)` → `fetchOpportunity`; returns the synopsis fields
   we use. Top-N fetched (cap 12), concurrently.
4. `eligibility_gate(applicant_types)` → **deterministic**: match Morgan State's
   institutional profile (public IHE / HBCU / MSI) against the returned
   applicant-type descriptions → `eligible` | `ineligible` | `unrestricted` |
   `see_text`. Keys off description strings (e.g. "institutions of higher
   education" + public/state-controlled), not memorized codes.
5. `rank_and_explain(description, opps)` → Gemini advisory: re-rank top-N and,
   per opp, a 1–2 sentence fit explanation **quoting `synopsisDesc`** (drop
   unquotable claims via the existing `_verify_evidence` whitespace-collapse
   pattern); a PI-level eligibility advisory quoting `applicantEligibilityDesc`;
   a mechanism note from the opp number/activity category. Deterministic
   fallback (API order, no AI prose) when Gemini returns None.
6. Result row: `{id, title, agency, closeDate, internal_deadline (via
   proposals_service.internal_routing_deadline), award_ceiling, fit_explanation,
   fit_quote, institution_eligibility, pi_eligibility_note, mechanism_note,
   solicitation_url, contact{name,email,phone}}`.

**Morgan State institutional profile:** baked-in constant (like the F&A rates) —
`{public IHE, HBCU, MSI, state: MD}` with the set of applicant-type descriptions
it satisfies.

**Endpoint:** `POST /api/opportunities/search` body `{description}` → ranked list.
Auth like other proposal endpoints; profile pulled from the `User` row.

**Frontend — `OpportunityFinder` (standalone surface, styled like
`SampleProposalsLibrary`/`FormsCatalog`):** description box (pre-filled with
profile interest hints), "Find opportunities" button, ranked result cards. Each
card: title · agency · close date · **internal routing deadline** · eligibility
badge (deterministic) · fit explanation w/ quoted basis · PI-eligibility advisory
· mechanism note · **"Start a proposal from this"**.

**Handoff (the payoff):** "Start a proposal" sends the opp's `solicitation_url`
into the existing `solicitation_extractor` → `create_submission_from_solicitation`,
dropping the PI into the guided pathway already built.

## Error handling / freshness / safety
- Grants.gov down/timeout → clear "couldn't reach the federal opportunity
  database" message; **never fabricate opportunities.**
- Gemini down → API relevance order + deterministic eligibility + raw opp text;
  tool still works.
- 5-min cache keyed by keyword (matches other caches); live otherwise.
- Fixed Grants.gov host in the search path (no SSRF). Handoff URL fetch goes
  through the existing SSRF-guarded `url_fetcher`. Results capped at 12.

## Testing
- Backend unit tests with **mocked Grants.gov responses** (`requests` mocked):
  - `extract_query` builds keywords from description + profile.
  - `eligibility_gate`: public-IHE list → eligible; private-only list →
    ineligible; "Unrestricted" → unrestricted; "Others (see text)" → see_text.
  - `rank_and_explain` fallback when Gemini returns None (API order preserved,
    no fabricated prose).
  - `search_grantsgov` / `fetch_opportunity` graceful on API error → `[]` / None.
- Full backend suite stays green (standard command).
- Manual: real description → real open opportunities; verify a handoff creates a
  submission.

## Non-goals (roadmap, not v1)
- Foundations / non-federal sources (no free API).
- NIH RePORTER similarity, full mechanism *recommender* wizard, PO-outreach
  drafter — all future, though the contact fields are already captured.

## Golden-rule alignment
- Deterministic core authoritative (API existence + institution eligibility);
  AI advisory + grounded (quotes or dropped).
- Graceful fallback at every external boundary.
- One focused feature; reuses solicitation ingestion, internal-deadline math,
  evidence-verification, profile injection.
