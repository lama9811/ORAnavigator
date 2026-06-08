# ORA Navigator — Documentation

ORA Navigator is an AI assistant for the **Office of Research Administration (ORA)**
at Morgan State University. It helps faculty, PIs, research staff, and department
admins with grants, compliance (IRB / IACUC / COI / RCR / Research Security),
pre-award, post-award, forms, and ORA staff contacts.

This folder is the human-readable map of the project. It is organized so that **each
feature has its own page** and the system design lives in one place.

> **Note on `CLAUDE.md`** — the big `CLAUDE.md` at the repo root is the *machine* context
> file (auto-loaded by the AI assistant and kept as the canonical, always-current memory).
> These docs are the *human* view: cleaner, split by topic, easier to read. When they
> disagree, `CLAUDE.md` is authoritative because it is updated every working session.

## How this is organized

| Folder | What's in it |
|---|---|
| [`design-system/`](design-system/) | How the system is built and what it's built with — start here to understand the project. |
| [`features/`](features/) | One page per feature (chatbot, memory, proposals, etc.). Read these to understand a specific capability. |
| `diagrams/` | Architecture & pipeline diagrams (PNG). |
| `evidence/` | Deploy / revision screenshots used in write-ups. |
| `screenshots/` | UI screenshots used in the README and guide. |
| `sections/` | HTML chapters of the full plain-English guide (`ORA_Navigator_Complete_Guide.html`). |
| `superpowers/` | Saved implementation plans & specs from past work sessions. |

## Quick links

- **Architecture overview** → [`design-system/architecture.md`](design-system/architecture.md)
- **Tech stack** → [`design-system/tech-stack.md`](design-system/tech-stack.md)
- **How the AI agents work** → [`design-system/agents.md`](design-system/agents.md)
- **Feature index** → [`features/README.md`](features/README.md)
- **Full plain-English guide** → `ORA_Navigator_Complete_Guide.html` (regenerate with `python docs/build_guide.py`)

## Working rules (how we change this project)

These are enforced by the `ora-deploy-discipline` skill in `.claude/skills/`:

1. **One feature = one focused change.** Add only what the feature needs; don't refactor
   or restyle unrelated code. A small diff is a safe diff.
2. **Don't break what already works.** Run the backend tests before any push or deploy.
3. **New feature → new feature page here** (and, where it makes sense, a new skill in
   `.claude/skills/`) so the docs grow alongside the code.
4. **Evidence before claims.** Never say "it works" or deploy without verifying.
