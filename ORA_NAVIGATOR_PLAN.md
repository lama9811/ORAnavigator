# ORA Navigator — Build Plan

**Project:** ORA Navigator — an AI assistant for the Office of Research Administration
**Institution:** Morgan State University
**Repository:** [github.com/lama9811/ORAnavigator](https://github.com/lama9811/ORAnavigator)
**Domain:** `ora.inavigator.ai`
**Forked from:** ORA Navigator (a similar assistant built for the Computer Science department)
**Document date:** 2026-05-12

---

## 1. Overview

ORA Navigator is a chatbot that answers questions for **faculty, researchers, and administrative staff** interacting with the Office of Research Administration. It is built on the same architecture as ORA Navigator — a working chatbot built for Morgan State's Computer Science department — but with content, copy, and features tailored to research administration.

**What it answers** (initial scope):
- Pre-award: grant submission procedures, F&A rates, eligibility, deadlines
- Post-award: budget revisions, no-cost extensions, effort certification
- Compliance: IRB/IACUC/COI submission requirements, training requirements
- Forms & templates: where to find them, when they're required
- Staff directory: who to contact for what

**What it does not do** (out of scope):
- Process grant applications
- Submit IRB protocols
- Replace human ORA staff for complex/edge-case questions

Every answer cites its source documents, so users can verify and click through to the canonical material.

---

## 2. Architecture

Three services, deployed on Google Cloud Run, sharing infrastructure with ORA Navigator but logically isolated.

| Service | Stack | Purpose | Port (local) |
|---|---|---|---|
| Frontend | Vite + React 19 | Chat UI, auth, admin dashboard | 3001 |
| Backend | FastAPI + SQLAlchemy | REST API, JWT auth, business logic | 5002 |
| ADK Agent | Google ADK + Gemini 2.5 Flash | LLM agent with knowledge base tool | 8081 |

**Cloud dependencies:**

| Resource | Value |
|---|---|
| GCP Project | `infra-vertex-494621-v1` (shared with ORA Navigator) |
| GCP Region | `us-central1` |
| Vertex AI Search datastore | `oranavigator-kb-local` (new, separate from CS) |
| Cloud SQL MySQL | `cs-navigatrodb` instance, new database `oranavigator` inside it |
| Cloud Run services | `oranavigator-frontend`, `oranavigator-backend`, `oranavigator-adk` |
| Secret Manager | New entries: `ora-database-url`, `ora-jwt-secret`, etc. |
| Domain | `ora.inavigator.ai` (frontend), optionally `api.ora.inavigator.ai` (backend) |

---

## 3. Approach: Fork to a New Repository

ORA Navigator is a **fork of ORA Navigator into a separate repository**, not a modification of the existing codebase. Both projects coexist.

```
~/Desktop/
├── ora-navigator/          ← unchanged, continues running at its existing URLs
└── ora-navigator/         ← new repository, fresh git history
```

### Why fork instead of branching or in-place rewrite

1. **Two products, not one.** ORA Navigator continues to ship and improve in parallel.
2. **Clean blast radius.** Experiments on ORA can't break CS in production.
3. **Independent deployment.** Different Cloud Run services, different domains, different release cadences.
4. **No conditional code.** Each repo *is* its department — no `if dept == "X"` branching across the codebase.
5. **Clean git log.** ORA's commit history reads as ORA-specific work from day one.

### What is shared vs forked

| Resource | Shared with CS | Forked / new |
|---|---|---|
| GCP project | ✓ Same | |
| Cloud SQL instance | ✓ Same instance | New database `oranavigator` inside it |
| Vertex AI Search datastore | | ✓ New: `oranavigator-kb-local` |
| Cloud Run services | | ✓ New service names |
| Secret Manager entries | | ✓ New entries |
| IAM service account | | ✓ New: `oranavigator-backend@...iam.gserviceaccount.com` |
| Domain | | ✓ New: `ora.inavigator.ai` |
| Source code repository | | ✓ New: `lama9811/ORAnavigator` |

### Trade-offs accepted

- Bug fixes to shared chat infrastructure must be applied in both repos
- Two GitHub repositories to maintain
- Modest duplication of CI/CD configuration

For two products, this is acceptable. If the number grows to 5+ departments, consolidating into a parameterized codebase would become the better trade-off.

---

## 4. Phase-by-Phase Plan

The plan is structured in seven phases. Each phase leaves the application in a runnable state, so progress can pause at any phase boundary.

### Phase 1: Strip student-specific features

ORA Navigator has features that exist only because its users are students: Canvas LMS integration, "My Classes" page, major dropdown, signup with `@morgan.edu` enforcement, degree-requirements validator (Group A/B/C/D, 120 credit hours). None of these apply to ORA's audience.

**Frontend components to delete or strip:**
- `frontend/src/components/ProfilePage.jsx` — Canvas LMS sync, major dropdown, "Open Morgan State" button. Reduce to minimal name/email/signout.
- "My Classes" route and component (Canvas-integrated)
- `frontend/src/components/DocumentationViewer.jsx` — full of COSC course examples; delete or rewrite with ORA examples
- `frontend/src/components/AdminDashboard.jsx` — placeholder text "Course Code (e.g. COSC 101)" needs relabeling to generic "Document ID"

**Backend endpoints to delete:**
- `backend/main.py` lines 689–715 — degree-requirements validator endpoint
- `backend/main.py` lines 3032–3065 — `QUESTION_POOL` of 24 CS-specific sample questions
- Canvas LMS integration endpoints and `CANVAS_API_TOKEN` env var

**Backend code changes:**
- `backend/main.py` line 1718 — `DEPT_PREFIXES` regex (currently includes COSC, MATH, etc.) — either remove course-code extraction or repurpose for grant numbers
- `backend/models.py` line 43 — `major` column default `"Computer Science"` should be `NULL` or removed via migration

**Scripts to delete or move aside:**
- `scripts/build_courses_v2.py`, `enrich_courses_with_curriculum.py`, `build_topic_track_docs.py`, `build_degree_program_docs.py`, `build_teaching_docs.py`, `refresh_faculty_from_dept_website.py`, `split_faculty_docs.py`, `split_course_docs.py`
- Data files: `scripts/course_data.json`, `backend/kb_structured/schedule_*.json`

---

### Phase 2: Rebrand

Find-and-replace pass for all CS-specific strings.

**Global substitutions:**

| Find | Replace |
|---|---|
| `"ORA Navigator"` | `"ORA Navigator"` |
| `"ORA Navigator"` | `"Morgan State ORA Navigator"` |
| `"Department of Computer Science"` | `"Office of Research Administration"` |
| `"Computer Science students"` | `"researchers and staff"` |
| `cs.inavigator.ai` | `ora.inavigator.ai` |
| `compsci@morgan.edu` | (ORA email — to be confirmed) |
| `(443) 885-3962` | (ORA phone — to be confirmed) |
| `morgan.edu/computer-science` | `morgan.edu/research` (verify exact URL) |

**Files with branding text:**
- `frontend/index.html` (page title)
- `frontend/src/components/NavBar.jsx`
- `frontend/src/components/GuestChatbox.jsx`
- `frontend/src/components/Chatbox.jsx`
- `frontend/src/components/auth/AuthLayout.jsx`
- `frontend/src/components/AIChatbot.jsx`

**Logos:**
- `frontend/public/msu_logo.{webp,png}` — keep (still Morgan State)
- `frontend/public/main_logo.{webp,png}` — replace with ORA logo asset

**Featured questions on landing page** (8 placeholders in `Chatbox.jsx` and `GuestChatbox.jsx`):

Suggested ORA-shaped examples:
1. "What's the indirect cost rate for federal grants?"
2. "How do I submit an IRB protocol?"
3. "What's the deadline for the NSF CAREER program this cycle?"
4. "Who do I contact for post-award financial questions?"
5. "What forms are required for a no-cost extension?"
6. "What's the F&A rate for industry-sponsored research?"
7. "How do I add a co-investigator after submission?"
8. "What's the difference between a grant and a cooperative agreement?"

---

### Phase 3: Create a new Vertex AI Search datastore

ORA Navigator uses a datastore named `oranavigator-kb-local`. ORA Navigator needs its own.

**Changes in `scripts/setup_kb_datastore.py`:**
- Line 24: `DATASTORE_ID` → `"oranavigator-kb-local"`
- Line 25: `DISPLAY_NAME` → `"ORA Navigator KB"`

**Environment variables to update** (in `.env` and Secret Manager):
- `UNIFIED_KB_ID=oranavigator-kb-local`
- `GOOGLE_CLOUD_PROJECT=infra-vertex-494621-v1` (no change)

**Steps:**
1. Empty out `backend/kb_structured/` (CS docs live in the CS repo)
2. Update the script's datastore ID
3. Run the script — creates the empty datastore on Vertex AI Search
4. Seed with a few starter ORA documents (Phase 4)

ORA Navigator's datastore is unaffected.

---

### Phase 4: Build the ORA knowledge base

The CS KB had ~300 documents across five shapes: faculty, courses, teaching schedules, programs, tracks. ORA's natural document shapes are different.

**Proposed document types:**

| Document type | Directory | Fields |
|---|---|---|
| Policy / procedure | `_generated_policies/` | `policy_id`, `title`, `applies_to`, `body`, `last_updated`, `source_url` |
| Funding opportunity | `_generated_opportunities/` | `sponsor`, `program_name`, `deadline`, `eligibility`, `award_size`, `link` |
| Form / template | `_generated_forms/` | `form_id`, `title`, `purpose`, `download_url` |
| Staff directory | `_generated_staff/` | `name`, `title`, `email`, `phone`, `service_area` |
| FAQ | `_generated_faqs/` | `question`, `answer`, `tags` |
| Service area overview | `_generated_service_areas/` | One per ORA function: pre-award, post-award, compliance, contracts |

**Universal fields preserved** (so the datastore upload script keeps working): `doc_id`, `title`, `category`, `content`, `struct_data`.

**Source material needed from ORA:**
- Internal policy documents (PDFs or web pages)
- Current staff directory with contact information
- Active funding opportunity calendar (NSF, NIH, internal programs)
- Forms inventory with download URLs
- Internal SOPs and procedure guides
- Historical FAQ log if one exists

**Ingestion scripts to write** (mirroring ORA Navigator's pattern):
- `scripts/build_policies.py`
- `scripts/build_staff_directory.py`
- `scripts/build_funding_opportunities.py`
- `scripts/build_forms.py`
- `scripts/build_faqs.py`
- `scripts/build_service_areas.py`

Each script reads source data, normalizes into the JSON shape above, and writes to the corresponding subdirectory.

---

### Phase 5: Rewrite the LLM agent's instructions

The chatbot's behavior is governed by a system prompt in `adk_agent/ora_navigator_unified/agent.py`, lines 350–458. The prompt currently encodes CS-specific rules (eight academic tracks, "Group A/B/C/D" elective logic, course-code regex).

**New ORA-flavored system prompt structure:**

1. **Identity** — "You are ORA Navigator, an AI assistant for Morgan State University's Office of Research Administration. You help researchers, faculty, and staff find authoritative answers about grants, compliance, and research operations."

2. **Service area discipline** — Do not mix policies across service areas. Pre-award rules are separate from post-award rules; IRB rules are separate from IACUC rules.

3. **Answer depth** — When the knowledge base contains a full procedure, return the full procedure. Do not summarize a multi-step process into one sentence.

4. **Temporal relevance** — Discard expired funding opportunities. Prefer the most recently updated version of any policy.

5. **Citations** — Every fact-based answer ends with a `📚 Sources:` block listing the documents referenced.

6. **Removed** — Course track logic, "Group A/B/C/D" elective rules, COSC course-code disambiguation.

**Also update:**
- Greeting copy (lines 131–144) — remove "Computer Science students"
- Meta-link (line 156) — change `cs.inavigator.ai` → `ora.inavigator.ai`
- Datastore IDs (lines 52–62, and `kb_prefetch.py` line 25) — repoint to `oranavigator-kb-local`

---

### Phase 6: Authentication & access policy

ORA Navigator requires signup with a `@morgan.edu` email. For ORA, the decision is open:

| Option | Pros | Cons |
|---|---|---|
| (a) Public, no auth | Lowest friction; anyone can ask a question | Bot traffic; no usage attribution |
| (b) Morgan email required | Restricts to faculty/staff/students with morgan.edu addresses | Excludes external collaborators |
| (c) Allowlist of named users | Tightest control — only invited PIs and ORA staff | Highest setup overhead |

**Recommendation:** Option (b). Most ORA users have morgan.edu emails; restricting to this domain matches the audience and reuses the existing auth code in `backend/routers/auth.py` essentially unchanged.

**Configuration changes:**
- Default admin email (currently `admin@morgan.edu`) — swap to an ORA admin address
- Email domain filter — keep `morgan.edu`
- SMTP for verification emails — same situation as ORA Navigator (currently unconfigured; manual DB toggle works as workaround)

---

### Phase 7: Deployment

Cloud Run service names default to `oranavigator-*` in `deploy-cloudrun.sh`. ORA needs its own service names so the two systems don't collide.

**Changes in `deploy-cloudrun.sh`:**
- Frontend service: `oranavigator-frontend` → `oranavigator-frontend`
- Backend service: `oranavigator-backend` → `oranavigator-backend`
- ADK service: `oranavigator-adk` → `oranavigator-adk`
- Service account: `oranavigator-backend@...` → `oranavigator-backend@...`

**Domain mapping** (already started):
- `ora.inavigator.ai` → `oranavigator-frontend` (in progress; SSL cert pending DNS verification)
- Optionally `api.ora.inavigator.ai` → `oranavigator-backend` (cleaner URLs; not strictly required)

**Required IAM roles on the new service account** (same as ORA Navigator):
- `roles/aiplatform.user` — invoke Gemini
- `roles/discoveryengine.viewer` — query Vertex AI Search
- `roles/storage.objectViewer` — read from Cloud Storage
- `roles/secretmanager.secretAccessor` — read DATABASE_URL etc.
- `roles/run.invoker` — Cloud Run identity
- `roles/cloudsql.client` — required for Cloud SQL Auth Proxy

---

## 5. Verification (End-to-End Test)

The build is considered complete when all of the following pass:

1. **Local boot.** All three services start cleanly. Frontend loads at `localhost:3001` with ORA branding visible — no remaining "ORA Navigator" or "Computer Science" strings in the UI.

2. **Smoke chat test.** Question: "What does the Office of Research Administration do?" The agent returns a coherent ORA-shaped answer, citing at least one knowledge base document in a `📚 Sources:` block.

3. **Datastore check.** Command `gcloud discoveryengine data-stores list` returns both `oranavigator-kb-local` and (still) `oranavigator-kb-local`.

4. **No CS leakage.** A code-wide grep for `COSC`, `Computer Science`, `cs navigator`, `oranavigator` returns zero functional matches in `frontend/src/`, `backend/main.py`, or `adk_agent/` (only allowed: archived/legacy directories and historical commit messages).

5. **Cloud deployment.** `./deploy-cloudrun.sh` succeeds. `https://ora.inavigator.ai` loads with a valid SSL certificate. A live chat from production hits the new datastore and returns ORA-shaped answers.

6. **Login flow.** A new user can sign up with a morgan.edu email, get verified (manually if SMTP unconfigured), log in, and start a chat session.

7. **Admin dashboard.** The admin user can log in, see the dashboard, and the document-upload form uses ORA-appropriate labels (no "Course Code" placeholder).

---

## 6. Order of Operations (Execution Sequence)

Each step leaves the application in a runnable state.

1. Fork the repository: `cp -R ora-navigator/ ora-navigator/`; scrub the old `.git`; `git init`
2. Delete CS-only KB documents and CS-only generation scripts
3. First commit on the new repository
4. Create the new Vertex AI Search datastore (`setup_kb_datastore.py` with new ID)
5. Provision new Cloud SQL database and app user
6. Rewrite the ADK agent's system prompt; repoint to new datastore
7. Strip student-only routes and features from backend
8. Strip student-only components from frontend
9. Apply branding find-and-replace pass
10. Seed the KB with 5–10 starter ORA documents so chat returns *something*
11. Run end-to-end local test (ports 3001 / 5002 / 8081 to avoid conflict with ORA Navigator)
12. Create new Cloud Run services, Secret Manager entries, and IAM service account
13. Deploy: `./deploy-cloudrun.sh` (with renamed service variables)
14. Verify production SSL certificate on `ora.inavigator.ai` and confirm chat works
15. (Ongoing) Build out the full knowledge base with real ORA source materials

---

## 7. Open Questions

Items to confirm before or during implementation:

1. **App name.** "ORA Navigator" is a working title — confirm or replace.
2. **ORA logo asset.** Need a replacement for `main_logo.webp` (the department logo).
3. **Initial KB source materials.** Minimum 10 starter documents (policies, staff list, FAQ examples) to seed the knowledge base for early testing.
4. **Authentication policy.** Confirm Option (b) — morgan.edu email required — or choose alternative.
5. **Backend domain mapping.** Decide whether `api.ora.inavigator.ai` is also mapped, or backend stays on its `*.run.app` URL.
6. **ORA contact information.** Email and phone number for the office (used in the agent's "where to follow up" responses).
7. **Verification of external URLs.** Confirm the canonical Morgan State research/ORA web page URL.

---

## 8. Critical Files (Implementation Checklist)

**Frontend**
- `frontend/index.html`
- `frontend/src/components/NavBar.jsx`
- `frontend/src/components/Chatbox.jsx` and `GuestChatbox.jsx`
- `frontend/src/components/auth/AuthLayout.jsx`
- `frontend/src/components/AIChatbot.jsx`
- `frontend/src/components/ProfilePage.jsx` (strip Canvas + major)
- `frontend/src/components/DocumentationViewer.jsx` (delete or rewrite)
- `frontend/src/SignUp.jsx`, `Login.jsx`, `ForgotPassword.jsx`
- `frontend/public/main_logo.{webp,png}` (replace asset)

**Backend**
- `backend/main.py` lines 689–715 (degree-requirements validator — delete)
- `backend/main.py` lines 3032–3065 (QUESTION_POOL — replace with ORA examples)
- `backend/main.py` lines 1718, 2489, 2874 (miscellaneous CS references)
- `backend/models.py` line 43 (`major` column default)
- Delete: Canvas-related routes and services

**ADK Agent**
- `adk_agent/ora_navigator_unified/agent.py` lines 350–458 (system prompt rewrite)
- `adk_agent/ora_navigator_unified/agent.py` lines 52–62 (datastore IDs)
- `adk_agent/ora_navigator_unified/kb_prefetch.py` line 25 (DATASTORE_ID)
- Optional folder rename: `ora_navigator_unified/` → `ora_navigator_unified/`

**Scripts**
- `scripts/setup_kb_datastore.py` lines 24–25 (DATASTORE_ID, DISPLAY_NAME)
- Delete CS-specific scripts (listed in Phase 1)
- Write new ingestion scripts (listed in Phase 4)

**Configuration**
- `.env` — update `UNIFIED_KB_ID`, `ADK_APP_NAME`, database connection
- `deploy-cloudrun.sh` — service names
- `CLAUDE.md` — rewrite for the new project (separate task)

---

## 9. Summary

ORA Navigator inherits the architecture, deployment pattern, and engineering practices of ORA Navigator — a working production chatbot — and applies them to a new content domain and audience. The fork-and-strip approach minimizes new code while delivering a separately-deployable product on its own domain.

The bulk of the remaining work is content (Phase 4: building the ORA knowledge base) rather than engineering. The technical scaffolding is largely reusable; the project's success depends on the quality and coverage of the source materials provided.

**Estimated effort:**
- Engineering (Phases 1–3, 5–7): 2–4 days of focused work
- Knowledge base construction (Phase 4): ongoing; usable MVP achievable with 20–30 starter documents
- Verification and polish: 1–2 days
