# CLAUDE.md

Canonical context for AI assistants working in this repo. If anything elsewhere disagrees, trust this file.

## What this is
**ORA Navigator** — an AI assistant + proposal toolkit for Morgan State University's **Office of
Research Administration** (ORA): grants, compliance, pre/post-award, forms, ORA contacts, and a
guided workspace that helps inexperienced PIs actually write and submit fundable proposals.

## Three services
| Service | Stack | Local port | Container port |
|---|---|---|---|
| **frontend** | Vite + React 19 (PWA) | 3001 | 8080 |
| **backend** | FastAPI, **single-worker uvicorn** | 5002 | 5000 |
| **adk_agent** | Google ADK + Gemini 2.5 Flash (Vertex AI), `VertexAiSearchTool` over one unified KB datastore | 8081 | 8080 |

The chat path: frontend → backend (`vertex_agent.py`) → ADK agent (`adk_agent/ora_navigator_unified/`) → Gemini on Vertex AI. The backend also serves the proposal toolkit (below) and talks to Cloud SQL (MySQL) + Redis.

## Build / test / deploy
- **Backend tests (keep green before any push/deploy):**
  ```bash
  cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
    python3 -m pytest -q --ignore=tests/test_agent_instruction.py
  ```
  Env vars are required because importing `main` constructs the app. `test_agent_instruction.py` needs `google-adk` (not always installed locally). App-level e2e tests use FastAPI `TestClient` with `dependency_overrides` for `get_db` / `get_current_user` (see `tests/test_proposals_api_e2e.py`).
- **Frontend:** `cd frontend && npm run build` (no `node_modules`? `npm install` first). It's a **PWA** — verify changes in a fresh/incognito window.
- **Deploy:** pushes to **`main`** trigger **Cloud Build** (`cloudbuild.yaml`) which builds + deploys all three Cloud Run services in project `infra-vertex-494621-v1`, region `us-central1`. There's also a manual `deploy-cloudrun.sh` (needs local `gcloud`).
- **Secrets** live in Secret Manager as `ora-database-url`, `ora-jwt-secret`, `ora-admin-email`, `ora-admin-password`, `ora-redis-url`, `ora-firecrawl-api-key` (mapped in `cloudbuild.yaml`). The Cloud Build runs as the **default compute service account** (`<projnum>-compute@…`), which lacks `secretmanager.secrets.create` — so the `deploy-backend` step **self-heals** `ora-firecrawl-api-key` (creates a `unset` placeholder if missing) so a missing optional secret never breaks the deploy. Create real secret values via the Console (the project picker prevents the "wrong project" mistake).
- **`GOOGLE_GENAI_USE_VERTEXAI=TRUE`** must be set on the ADK **and** backend services (in `cloudbuild.yaml`) or Gemini falls back to the developer API and every call fails with "No API key was provided."

## Golden rules (followed everywhere; keep following them)
1. **AI is advisory; the deterministic core is authoritative.** Numbers, pass/fail, and eligibility verdicts are computed by code — the LLM only explains, drafts prose, outlines, or gives advisory feedback. It can never change a figure.
2. **Ground every AI claim.** Any "covered/strong" finding must quote the source text; unquotable claims are dropped (`_verify_evidence` in `draft_critic.py` / `section_coach.py`). The membership check **collapses whitespace on both sides** (`" ".join(s.split())`) — a pasted/hard-wrapped draft has newlines where Gemini's quote has spaces, and a raw substring match would wrongly reject a real quote and mark a complete section "NOT FOUND".
3. **Graceful fallback.** Every AI path returns a deterministic result when Gemini is unavailable (`gemini_client.generate_json/text` return `None` → caller falls back). Never error because the model is down.
4. **Human reviews before commit.** Extractions and budgets are returned for review; nothing is saved until the user confirms.
5. **Conventions:** structured data stored as **TEXT columns + `json.dumps/loads`** (e.g. `Submission.budget_json`, `compliance_json`, `sections_json`, `notes`). New columns are added with a **self-healing migration** in `main.py:init_db()` (try `SELECT col`; on error `ALTER TABLE ADD COLUMN`) — safe on prod MySQL, no Alembic.
6. **One feature = one focused change.** Don't refactor unrelated code. Verify before claiming done.

## The Proposal Toolkit (backend `services/` + per-proposal modals in `frontend/src/components/`)
Each proposal (`Submission` + `SubmissionTask` in `models.py`, logic in `proposals_service.py`, sponsor task templates in `proposal_templates.py`) has a toolbar of tools:

- **Solicitation ingestion** — `solicitation_extractor.py` (PDF→text via pdfplumber → Gemini → contract dict with `source_quotes`; fabricated quotes flagged via `_verify_source_quotes`). Two entry points: upload a PDF, or **paste a URL** (`url_fetcher.py`). The URL fetcher is SSRF-guarded (resolve + reject private/loopback/link-local IPs, pin the validated IP to defeat DNS rebinding). Some funder sites (NSF/Akamai) **block cloud-datacenter IPs**, so a direct fetch falls back through a scraper chain: **Firecrawl** (`_fetch_via_firecrawl`, preferred — runs a real browser from non-blocked IPs; needs `FIRECRAWL_API_KEY`, and treats the `unset` deploy-placeholder as no-key), then `r.jina.ai` (set `JINA_API_KEY`), then `allorigins`. Uploaded PDFs are 100% local (pdfplumber) and never hit an IP block. `create_submission_from_solicitation` seeds the checklist; multi-category deadlines are surfaced via `deadline_details`. The extracted **eligibility text** is stored in `notes` and fed to the Drafting Coach.
- **Budget Helper** — `budget_helper.py`: deterministic F&A/MTDC/fringe math (Morgan's real rates), sponsor-cap check. **Coaching layer:** `budget_advisories()` (sanity flags), `suggest_trims()` (concrete cuts to get under cap), category/F&A/fringe tooltips. **v2:** multi-year projection with escalation (`compute_budget` detects `project_years`; single-year output unchanged), CSV export (`budget_to_csv` + `/budget.csv`), per-line justification, and a **deterministic prose** budget justification (`draft_justification` — labeled paragraphs, reads like a real submission). AI only polishes the prose and is forbidden to change figures; the `/api/budget/justification` endpoint falls back to the template when the AI output is empty **or truncated** (missing the total-cost figure — a Gemini-under-load failure mode). The UI **effort→person-months helper is read-only**, derived from each person's `effort_pct` in the People rows (not a separate input), so it can never disagree with the budget.
- **Drafting Coach** — `section_coach.py`: per-section **outline** + advisory **feedback** on the PI's own draft (covered/missing elements, grounded). Coaching only — never writes prose. Section menu is **by sponsor** (`_SPONSOR_ORDER`: NSF → Project Summary / Project Description / Broader Impacts / Data Management Plan; NIH → Specific Aims / Research Strategy / DMP; else generic). **v2:** save drafts (`sections_json`), live length meter (`WORD_TARGETS`), "match THIS solicitation" (feeds required attachments/eligibility/page limits), deterministic `clarity_check()`.
- **Draft Critic vs Drafting Coach** — different jobs: the **Coach** grades the *writing* of one section; the **Critic** (`draft_critic.py`) checks the *whole package's completeness/compliance* (required sections & **solicitation-required attachments**, page limits, budget vs cap) + advisory AI review. Completeness is the Critic + the solicitation checklist, never the Coach.
- **Guided steps** — `task_guidance.py`: keyword-matched how-to + sample per checklist task, attached in `_submission_task_to_dict`; per-task "add to calendar".
- **Draft Critic** (`draft_critic.py`) — mechanical pre-submission checks (page limits, required sections/attachments, budget vs cap) + advisory AI review.
- **Compliance Sentinel** (`compliance_sentinel.py`) — IRB/IACUC/COI/RCR/export rules. **Deadline Watcher** (`deadline_watcher.py`) — AI-personalized reminder emails (cron). `ics_export.py` — calendar feeds.

**Removed:** the former **Fundability / Eligibility self-check** tool (reviewer-lens scoring + go/no-go) was deleted per product decision — `services/fundability.py`, `services/eligibility.py`, their tests, the `FundabilityModal`, and the `/fundability/criteria` · `/eligibility` · `/fundability` endpoints are gone. Do not re-add. The eligibility *text* extracted from the solicitation is still kept (`_eligibility_text_from_notes`) and fed to the Drafting Coach.

## Cron jobs (Cloud Scheduler → internal endpoints, auth via `X-Research-Secret` / `RESEARCH_SECRET`)
4 recurring + 1 manual: `/api/internal/research/run` (daily ~2am), `/api/internal/memory/consolidate` (daily ~3am — rolls conversations into long-term `UserMemory`), `/api/internal/memory/idle-sweep` (every 5 min), `/api/internal/deadlines/check` (daily — Deadline Watcher, idempotent so a retry never double-emails), and `/api/internal/memory/backfill-profiles` (one-off). The actual schedules live in **Cloud Scheduler in GCP**, not the repo.

## Memory
Two tiers: **short-term** (current chat history) and **long-term** (`UserMemory` rows: department, role, active_grant, interests, …). The 3am consolidate cron moves chat facts into long-term; `mirror_profile_to_memories` mirrors saved profile fields into `UserMemory` on profile save. The chat endpoint also injects the user's **saved profile (name/department/title/role) directly from the `User` row every turn** as an authoritative `USER PROFILE` block — independent of memory selection — so "what department am I in?" always works (the top-N memory fetch alone could crowd the `[department]` row out). `build_memory_context` assembles long-term + semantic-recall + verbatim-turn sections for the agent.

## Load-bearing fixes (don't regress)
- **Gemini 429 on the chat path** (`vertex_agent.py`): a quota error arrives as response *text*; `_is_rate_limited` detects it, retries with backoff, then flags an outage — instead of laundering it into "system is busy" and amplifying via Pass-2 regeneration.
- **Greetings / small talk** (`vertex_agent.py` `_is_smalltalk` + `agent.py` GREETINGS rule): "hi", "how are you", "thanks" get a warm KB-free reply — NOT the "I can only help with ORA" refusal, and NOT the "developed for Morgan State / ora.inavigator.ai" identity blurb (that blurb is only for "who made this app"). The grounding gate must NOT grade a greeting reply "weak" and regenerate it (that turned it back into a refusal).
- **Evidence whitespace** (`section_coach.py` / `draft_critic.py`): `_verify_evidence` collapses whitespace before the substring match — see golden rule 2.
- **Budget justification truncation** (`/api/budget/justification`): a non-empty-but-truncated AI fragment must fall back to the deterministic template (guard on the total figure), or the box shows a half-sentence.
- **Chat knows the profile** (`main.py` chat endpoint): inject the saved `User` profile every turn — see Memory section.
- **Cache poisoning** (`cache.py`): transient "busy / try again" messages must never be cached (they're not real answers).
- **Deploy:** `cloudbuild.yaml` must use the `ora-*` secret names and set the full Vertex env on both ADK and backend; the Cloud Build service account needs Cloud Run Admin + Service Account User. An **optional** secret (e.g. `ora-firecrawl-api-key`) must never hard-fail the deploy — the backend step self-heals a placeholder (see Secrets).

## Known limitations / open work
- **NSF (and similar Akamai sites)** block server-side URL fetches by IP. **Firecrawl** (set `FIRECRAWL_API_KEY`, free tier ~500 one-time credits) is the reliable path; the Jina/allorigins fallbacks are throttled from Cloud Run. Locally the direct fetch works (residential IP isn't blocked). Uploaded PDFs always work regardless.
- Drafting-coach AI tailoring/feedback quality depends on the deployed Gemini being healthy; it degrades to deterministic checks otherwise.
- Demo aids live in repo root: `ORA_Navigator_Demo_Script.pdf`, `Sample_Solicitation_for_Demo.pdf` (generated by `scripts/make_demo_pdfs.py`).

## Docs
Human-readable docs in `docs/` (`docs/README.md`, `docs/sections/*.html`). Cross-tool entry point: `AGENTS.md`.
