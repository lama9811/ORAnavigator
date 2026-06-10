# Features

**One page per feature.** Each page follows the same template so they're quick to scan and
easy for anyone (you, a teammate, a recruiter, a new dev) to understand.

## The rule for adding a feature

> When you add a new feature: **make a focused change** that adds only what the feature needs.
> Don't refactor, rename, or restyle unrelated code — a small diff is a safe diff that won't
> break existing features. Then **add a new page here** (and, where useful, a new skill in
> `.claude/skills/`) so the docs grow with the code.

Every feature page has these sections:

- **In one line** — what it is.
- **What it does (plain English)** — the user-facing behavior.
- **Where it lives** — the key files.
- **How it works** — the short technical version.
- **API & data** — endpoints and database tables it touches.
- **Don't regress** — load-bearing details that must not break.
- **Status** — built / deployed / partial / idea.

## Feature index

### Core chat experience
- [Chatbot & RAG pipeline](chatbot-and-rag.md) — the 3-layer grounded answer engine.
- [Knowledge Base](knowledge-base.md) — the 382-doc KB and how it's enumerated & searched.
- [Caching](caching.md) — L1 / L2 / semantic cache.
- [Suggested questions](suggested-questions.md) — personalized starter prompts.

### Memory & personalization
- [Memory system](memory-system.md) — rolling summaries, semantic recall, daily consolidation.

### AI agents (proposal workflow)
- [Solicitation Ingestion](solicitation-ingestion.md) — sponsor PDF → submission + tasks.
- [Draft Critic](draft-critic.md) — pre-submission check of a draft vs. the solicitation.
- [Budget Helper](budget-helper.md) — deterministic grant-budget builder + AI justification.
- [Compliance Sentinel](compliance-sentinel.md) — deterministic "which approvals do I need?" checklist (IRB/IACUC/COI/RCR/Export).
- [Deadline Watcher](deadline-watcher.md) — deadline reminder emails.

### User-facing tools
- [Proposals tracker](proposals-tracker.md) — the "My Proposals" workspace.
- [Forms catalog](forms-catalog.md) — searchable ORA forms.
- [Calendar (.ics) export](calendar-ics-export.md) — add deadlines to your calendar.

### Platform
- [Auth & roles](auth-and-roles.md) — signup, login, verification, admin.
- [Admin & KB management](admin-and-kb-management.md) — admin dashboard + KB edit endpoints.
- [Faithfulness eval harness](eval-harness.md) — the hallucination test suite.

## Ideas not yet built
See `CLAUDE.md` → "Open work" and our brainstorms. Strong candidates: **KB Gap-Filler**
(self-improving KB from logged misses), **Training/Cert Tracker**, **Budget Helper**,
**KB-Sync web scraper**. Each should become its own page here when built.
