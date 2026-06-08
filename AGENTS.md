# AGENTS.md

> Cross-tool entry point for AI coding assistants. The canonical, always-current context
> for this repo lives in **[`CLAUDE.md`](CLAUDE.md)** — read it first.

ORA Navigator is an AI assistant for the **Office of Research Administration** at Morgan
State University (grants, compliance, pre/post-award, forms, ORA contacts).

## Read these first
- **[`CLAUDE.md`](CLAUDE.md)** — architecture, live deployment state, load-bearing bug fixes,
  conventions, and "open work." This is the source of truth; if anything here disagrees, trust `CLAUDE.md`.
- **[`docs/`](docs/)** — the human-readable docs:
  - [`docs/design-system/architecture.md`](docs/design-system/architecture.md) — how the system is built
  - [`docs/design-system/tech-stack.md`](docs/design-system/tech-stack.md) — what it's built with
  - [`docs/design-system/agents.md`](docs/design-system/agents.md) — how the AI agents must behave
  - [`docs/features/`](docs/features/) — one page per feature

## How to work in this repo (the short version)
- **Three services:** frontend (Vite/React 19, port 3001), backend (FastAPI, port 5002,
  **single-worker uvicorn**), ADK agent (Google ADK + Gemini 2.5 Flash, port 8081).
- **Build/test:** backend `cd backend && python -m pytest` (must stay green before any push/deploy);
  frontend `cd frontend && npm run build`.
- **Deploy:** `deploy-cloudrun.sh <one-service>`; it overwrites env vars, so run
  `bash /tmp/post_deploy_backend.sh` after every backend deploy. The frontend is a PWA — verify in incognito.
- **One feature = one focused change.** Don't refactor unrelated code. New feature → add a page in
  `docs/features/`; new working rule → a skill in `.claude/skills/`.
- **Verify before claiming.** Evidence before assertions, every time.

The full working-style + deploy rules are enforced by the `ora-deploy-discipline` skill in
[`.claude/skills/`](.claude/skills/).
