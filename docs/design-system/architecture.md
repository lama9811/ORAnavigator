# Architecture

How ORA Navigator is structured, how the pieces talk to each other, how an answer is
produced, and how it ships to production.

---

## 1. The big picture — three services

ORA Navigator is **three separate services** that run independently and talk over HTTP.

| Service | Local port | Stack | Job |
|---|---|---|---|
| **Frontend** | 3001 | Vite + React 19 (PWA) | The UI users see and click. |
| **Backend** | 5002 | FastAPI + SQLAlchemy | The brain: REST API, auth, business logic, the trust/grounding pipeline, all the AI agents. |
| **ADK Agent** | 8081 | Google ADK + Gemini 2.5 Flash | The LLM agent that searches the knowledge base and drafts answers. |

```
 ┌────────────┐        ┌────────────┐        ┌──────────────┐
 │  Frontend  │  HTTP  │  Backend   │  HTTP  │  ADK Agent   │
 │  React 19  │ ─────► │  FastAPI   │ ─────► │  Gemini 2.5  │
 │  (browser) │        │  (the API) │        │  + KB tool   │
 └────────────┘        └─────┬──────┘        └──────────────┘
                             │
              ┌──────────────┼───────────────┐
              ▼              ▼                ▼
        Cloud SQL      Vertex AI Search   Secret Manager
        (MySQL 8.4)    (KB datastore)     (secrets)
```

The frontend never talks to the ADK agent directly — everything goes **through the
backend**, which adds auth, caching, and the trust pipeline.

## 2. Architectural style

- **Service-oriented, not a monolith.** Each tier can be deployed and scaled on its own.
- **The backend is the single point of trust.** It owns auth, the grounding/verification
  pipeline, caching, and the database. The LLM (ADK) is treated as an *untrusted drafter* —
  the backend checks its work (see §4).
- **Multi-agent via shared state, not direct calls.** The specialized AI agents
  (Solicitation Ingestion, Draft Critic, Deadline Watcher) never call each other. They
  coordinate **through the proposal record in the database** — each one reads and writes
  the shared `submissions` / `submission_tasks` rows. This keeps them decoupled.
- **"AI proposes, deterministic verifies, human confirms."** The house pattern for every
  agent: the model suggests, deterministic code checks it against evidence, and for anything
  risky a human approves before it commits. (e.g. Solicitation Ingestion is a two-step
  confirm so a wrong AI-extracted deadline never auto-commits.)

## 3. The cloud topology (production)

Everything runs in GCP project `infra-vertex-494621-v1`, region `us-central1`.

| Resource | Name / detail |
|---|---|
| Cloud Run services | `oranavigator-{frontend,backend,adk}` |
| Cloud SQL | `oranavigator-db` — MySQL 8.4, db-g1-small (SSL required) |
| Vertex AI Search | datastore `oranavigator-kb-v8` (location `us`, 382 docs) |
| Secret Manager | DB URL, JWT secret, admin creds, Redis URL, research secret |
| Cloud Scheduler | `ora-deadline-watcher` (7am ET), `ora-memory-consolidate` (3am ET) |
| Domain | `ora.inavigator.ai` → frontend |

**Live URLs:** frontend `ora.inavigator.ai`, backend `oranavigator-backend-ollhkgeova-uc.a.run.app`,
ADK is private (backend → ADK only).

### Database connection (two modes)
- **Local dev:** TCP + SSL to the Cloud SQL public IP. `backend/db.py` auto-attaches an SSL
  context (MySQL 8.4 requires it).
- **Cloud Run:** Unix socket via the Cloud SQL Auth Proxy. `backend/db.py` detects the
  `unix_socket=` param and uses SQLAlchemy's `creator` pattern.

### Cold-start note
Cloud Run services scale to zero by default, so the first request after idle pays a
~30s container boot. The **backend is pinned to `min-instances=1`** to keep it warm
(eliminates the slow "first login" wait).

## 4. How an answer is produced — the 3-layer RAG pipeline

This is the core of the product. A chat turn becomes a *trustworthy* answer in layers.
Full detail lives in [`../features/chatbot-and-rag.md`](../features/chatbot-and-rag.md);
the short version:

```
YOUR QUESTION
  │
  ├─ QUICK CHECKS (no AI): greetings → canned; seen before → cache; "list X" → KB menu
  │
  ▼
LAYER 1 — head start: TF-IDF keyword prefetch injects top-5 KB docs into the prompt (~30ms)
  │
  ▼
THE AI (Gemini, in the ADK agent): searches the KB via Vertex AI Search, writes a draft
  │
  ▼
LAYER 2 — the receipt: grounding chunks → morgan.edu URLs become the "Sources" block
  │
  ▼
LAYER 3 — the fact-checker (the real guarantee): grade the answer; if weak, redo strictly
  "KB-only"; if still nothing, REFUSE ("contact ORA"); flag any number/date/SOP#/email/phone
  not found verbatim in the KB
  │
  ▼
SAVE to cache → SEND answer + Sources
```

**Key idea:** Layer 3 doesn't trust the model's own confidence — it re-checks the result
and forces a redo or a refusal. That's what makes the assistant safe to put in front of
faculty asking about compliance.

## 5. Where the code lives (orientation map)

```
backend/
  main.py                 # FastAPI app, routes, startup, internal cron endpoints
  vertex_agent.py         # the trust/grounding pipeline (Layers 2-3)
  db.py                   # SQLAlchemy engine + the two connection modes
  models.py               # all database tables (ORM)
  cache.py                # L1/L2/semantic caching
  kb_browser.py           # deterministic KB enumeration ("list X")
  datastore_manager.py    # read/write the Vertex AI Search datastore
  routers/                # auth and other route groups
  services/               # the AI agents + helpers (see features/)
    gemini_client.py      #   shared Gemini access for all agents
    solicitation_extractor.py, draft_critic.py, deadline_watcher.py,
    memory_service.py, forms_catalog.py, proposal_templates.py, ics_export.py
  kb_structured/          # the knowledge base: 382 JSON docs + manifests
  tests/                  # backend test suite (~337 passing)

adk_agent/ora_navigator_unified/
  agent.py                # the LLM agent, tool registration, grounding rules
  kb_prefetch.py          # Layer 1 TF-IDF prefetch
  list_kb_tool.py         # exposes the KB tree to the agent
  eval/                   # faithfulness eval harness (promptfoo)

frontend/src/
  components/Chatbox.jsx  # the chat UI + SSE streaming reader
  ... pages (Login, MyProposals, Forms, Profile, Admin), lazy-loaded
```

## 6. Deployment

- `deploy-cloudrun.sh <service>` builds and deploys **one** service per invocation
  (it silently ignores extra args — deploy each separately).
- It **overwrites** Cloud Run env vars (`--set-env-vars` replaces the whole block) and resets
  `--min-instances 0`, so it wipes `SMTP_*`, `API_URL`, `RESEARCH_SECRET`, `ALLOW_TEST_EMAILS`,
  `FROM_EMAIL` and the warm-instance setting. **Use `bash scripts/deploy_backend.sh`** — a durable
  wrapper that captures the live env + min-instances before the deploy and restores them after
  (replaces the old, non-durable `/tmp/post_deploy_backend.sh`). No secrets are stored in the repo.
- The frontend is a **PWA** (`registerType: 'autoUpdate'`) — verify UI changes in
  **incognito** to bypass the service-worker cache.
- Single-worker uvicorn is required (thread-local grounding state in `vertex_agent.py`).

## 7. Scheduled jobs (crons)

| Job | When | Hits | Does |
|---|---|---|---|
| `ora-deadline-watcher` | daily 7am ET | `POST /api/internal/deadlines/check` | emails PIs about upcoming proposal deadlines |
| `ora-memory-consolidate` | daily 3am ET | `POST /api/internal/memory/consolidate` | distills durable facts from chat history into `user_memories` |

Both authenticate with the shared `X-Research-Secret` header. A 5-minute idle-sweep
endpoint also exists for near-real-time memory extraction.

## 8. Diagrams

Visual versions of the pipeline live in [`../diagrams/`](../diagrams/):
`1_current_pipeline_failures.png`, `2_plan_a_tighten_bolts.png`,
`3_plan_b_retrieval_verification.png`, `4_plan_c_structured_architecture.png`.
