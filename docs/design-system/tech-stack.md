# Tech Stack

Everything ORA Navigator is built with, by layer. Versions are from
`frontend/package.json` and `backend/requirements.txt` (kept in sync with the code).

---

## Frontend (the UI)

| Thing | Choice | Notes |
|---|---|---|
| Language | JavaScript (JSX, ES modules) | `"type": "module"` |
| Framework | **React 19** | `react` / `react-dom` ^19 |
| Build tool | **Vite 6** | dev server on port 3001, `npm run dev` |
| Routing | `react-router-dom` 7 | pages are `React.lazy`-loaded behind a `Suspense` boundary |
| PWA | `vite-plugin-pwa` (`registerType: 'autoUpdate'`) | installable; **service worker caches the bundle — test in incognito** |
| Markdown | `react-markdown` 10 + `remark-gfm` | renders chat answers |
| Code highlighting | `react-syntax-highlighter` 16 (PrismLight) | only the used languages are registered (perf) |
| Animation | `framer-motion` 12 | |
| Icons | `lucide-react`, `react-icons`, `@react-icons/all-files` | |
| Command palette | `cmdk` | |
| Toasts | `sonner` | |
| Auth helper | `jwt-decode` | reads the JWT client-side for route guards |
| Linting | ESLint 9 + react-hooks / react-refresh plugins | |

**Performance note:** first-paint JS was cut ~77% (≈458 KB → ≈105 KB gzip) via PrismLight,
lazy page splitting, and a `react-vendor` manual chunk.

## Backend (the API + brain)

| Thing | Choice | Notes |
|---|---|---|
| Language | **Python 3** | |
| Web framework | **FastAPI** 0.115 (Starlette 0.46) | served by **uvicorn** 0.34, **single worker** (required) |
| Server model | async + `asyncio.to_thread` for blocking work | |
| ORM | **SQLAlchemy** 2.0 | `backend/db.py`, `backend/models.py` |
| Database driver | **PyMySQL** | MySQL 8.4 over TCP+SSL (local) or unix socket (Cloud Run) |
| Validation | **Pydantic** 2.11 (+ pydantic-settings) | request/response models |
| Auth | `python-jose[cryptography]` (JWT) + `passlib[bcrypt]` / `bcrypt` 4.0 | bcrypt rounds = 12 |
| Caching | `cachetools` (L1 TTL) + `redis` 5.2 (L2) | semantic cache via embeddings |
| File parsing | `pdfplumber` 0.11, `pypdf` 5.6, `python-docx` 1.2 | sponsor PDFs, draft PDFs |
| HTTP | `httpx`, `requests`, `aiohttp` | |
| Tests | `pytest` 8.4 (+ asyncio, benchmark, socket, recording) | ~337 passing |

### AI / Google Cloud libraries (backend)
| Library | Used for |
|---|---|
| `google-genai` 1.14 | Gemini 2.5 Flash access (shared via `services/gemini_client.py`) |
| `google-cloud-discoveryengine` 0.13.5 | reading/writing the Vertex AI Search KB datastore |
| `google-cloud-storage` 2.19 | object storage |
| `text-embedding-004` (via genai) | semantic cache + memory recall embeddings |

> **Retrieval is Vertex AI Search only.** The former Pinecone + LangChain RAG pipeline
> (dense-vector search, `chatbot.py`, `services/hybrid_retrieval.py`) has been removed — the
> live path is the ADK agent's `VertexAiSearchTool` plus the `kb_prefetch` layer. `openai` is
> still present for **TTS only** (API key blank by default); it is not on the retrieval path.

## ADK Agent (the LLM agent)

| Thing | Choice | Notes |
|---|---|---|
| Framework | **Google ADK** (Agent Development Kit) | run headless with `python -m google.adk.cli api_server . --port 8081` |
| Model | **Gemini 2.5 Flash** | model id `gemini-2.5-flash` everywhere (`2.0-flash` 404s in this project) |
| Retriever tool | `VertexAiSearchTool` | semantic search over `oranavigator-kb-v8` |
| Prefetch | custom TF-IDF (`kb_prefetch.py`) | Layer 1 head-start, no extra cost |
| Env | `GOOGLE_GENAI_USE_VERTEXAI=TRUE` | forces Vertex backend (else it demands a public API key) |

## Cloud & infrastructure

| Thing | Choice |
|---|---|
| Cloud provider | **Google Cloud Platform** (project `infra-vertex-494621-v1`, `us-central1`) |
| Compute | **Cloud Run** (3 services, container-based) |
| Database | **Cloud SQL** — MySQL 8.4 |
| Vector / semantic search | **Vertex AI Search** (Discovery Engine) |
| Secrets | **Secret Manager** |
| Scheduling | **Cloud Scheduler** (cron → internal HTTP endpoints) |
| Container registry | **Artifact Registry** (`oranavigator` repo) |
| Email | SMTP via Gmail (App Password) |
| Cache (L2) | Redis (`ora-redis-url`) — local dev falls back to L1 only |

## Dev & ops tooling

- **gcloud SDK** for all GCP ops; **ADC** for local auth.
- `deploy-cloudrun.sh` — build + deploy one service; stages source to `/tmp` first.
- `/tmp/post_deploy_backend.sh` — restores env vars wiped by a backend deploy.
- Python venv at `~/Desktop/ora-navigator/.venv`.
- Eval harness: **promptfoo** (faithfulness exam, graded by Gemini) in `adk_agent/.../eval/`.
