# AI Agents — How They All Work

The contract **every** AI agent in ORA Navigator follows. Read this before building or
changing an agent. Per-feature detail lives in [`../features/`](../features/); this page is
the shared rulebook.

---

## The golden rule

> **AI proposes · deterministic code verifies · a human confirms anything risky.**

The language model is treated as an *untrusted drafter*. Its output is never the final word —
deterministic code checks it against evidence, and for anything that could mislead a user or
corrupt data, a human approves before it commits. This is what makes an AI safe to put in front
of faculty asking about compliance.

## The seven shared principles

1. **Ground every claim in evidence.** An agent may only assert what it can back with a source —
   a KB chunk, a quote from the uploaded PDF, or a field in the proposal record. No source → no claim.

2. **Verify, don't trust.** After the model answers, deterministic code re-checks it
   (grounding grade, quote verification, identifier faithfulness). Findings that aren't
   quote-backed are **flagged or dropped**, never shown as fact.

3. **Always have a hard fallback.** If Gemini is slow, down, or returns junk, the agent must
   degrade to a deterministic result (a template email, a refusal, a "contact ORA") — never crash,
   never hang, never block the user. `gemini_client` returns `None` on any failure and **never raises**;
   callers must handle `None`.

4. **Refuse over guess.** When the KB has no answer, say so and route to ORA. A correct "I don't
   know" beats a confident fabrication. (e.g. IACUC SOP 37 is deliberately absent — never invent it.)

5. **Coordinate through shared state, not direct calls.** Agents don't call each other. They read
   and write the **shared proposal record** (`submissions` / `submission_tasks`) so they stay decoupled.

6. **Be idempotent.** Anything that acts (sends email, mutates the KB) writes to a log table so a
   repeat run — a cron retry, a double-click — never double-acts.

7. **Strict system instructions.** Rules go in the `system_instruction` (not inline in the prompt)
   so the model weights them heavily. Temperature low (0.0–0.3) for extraction/verification work.

## Anti-hallucination — the concrete checks (in code)

Principle #2 ("verify, don't trust") is enforced by real code, not just prompts:

- **Grounding grade** (`_evaluate_grounding`, `vertex_agent.py`) — scores the answer's support;
  a weak grade forces a strict **KB-only regeneration**, and still-empty falls through to a refusal.
- **Identifier faithfulness** (`_check_identifier_faithfulness`) — checks SOP/FWA/EIN/UEI numbers,
  F&A %, **dates, dollar amounts, emails, phone numbers** appear **verbatim** in the retrieved KB
  chunks. Whitelist holds the canned ORA contact so it doesn't self-flag. *(The user-facing
  "could not be verified" footer was removed 2026-06-02 — it false-positived on refuted figures;
  unverified identifiers are now only logged.)*
- **Evidence verification** (`_verify_evidence` / `_verify_source_quotes`) — for the agents,
  any finding or extracted field whose quote isn't in the source PDF/draft is **dropped or flagged**,
  never presented as fact.
- **Trust-the-redo** — the pipeline trusts a clean strict-regeneration instead of re-gating on
  Gemini's flaky chunk count; empty Pass-1 + zero chunks degrades to a refusal, never a dead-end error.

> Model id is **`gemini-2.5-flash`** everywhere (`2.0-flash` 404s in this Vertex project), and the
> grounding pipeline is only safe under **single-worker uvicorn** (thread-local state).

## Shared plumbing

| Concern | How |
|---|---|
| Model access | `backend/services/gemini_client.py` — `generate_json()` / `generate_text()`, Gemini **2.5 Flash** only |
| Failure mode | client returns `None` on any error; first failure short-circuits later calls (fast fallback) |
| Internal triggers | `POST /api/internal/...` endpoints, authed by the `X-Research-Secret` header |
| Scheduling | Cloud Scheduler jobs → those internal endpoints (e.g. 7am deadlines, 3am memory) |
| State / idempotency | a dedicated log table per acting agent (e.g. `deadline_reminder_log`) |
| Tests | `conftest.py` forces Gemini **offline by default** so the deterministic fallback is always tested; AI paths are opt-in via mocking |

## The agents today

| Agent | Job | Trigger | Its safety mechanism |
|---|---|---|---|
| **Chatbot** ([rag](../features/chatbot-and-rag.md)) | Answer ORA questions from the KB | user chat | 3-layer grounding pipeline; refuse if no KB support |
| **Solicitation Ingestion** ([doc](../features/solicitation-ingestion.md)) | Sponsor PDF → submission + tasks | user upload | quote-verify each field; **two-step human confirm** before commit |
| **Draft Critic** ([doc](../features/draft-critic.md)) | Check a draft vs. solicitation rules | user upload | **deterministic core is authoritative**; AI is advisory, evidence-verified |
| **Deadline Watcher** ([doc](../features/deadline-watcher.md)) | Email deadline reminders | daily 7am cron | hard fallback to deterministic email; idempotency log |
| **Memory** ([doc](../features/memory-system.md)) | Distill durable user facts | 3am cron + idle sweep | reads chat only; caps facts; excludes PII/financial |

## How to add a new agent

1. **Write `backend/services/<agent>.py`** — import `gemini_client`, define a strict
   `_SYSTEM_INSTRUCTION`, write pure helper functions, and a main entry point returning a summary dict.
   Always check `if result is None:` and fall back deterministically.
2. **Add a log/state table** in `backend/models.py` if the agent *acts* (for idempotency).
3. **Wire a trigger** — an internal `POST /api/internal/<agent>/...` endpoint (X-Research-Secret),
   and a Cloud Scheduler job if it runs on a schedule; or a user-facing endpoint if on-demand.
4. **Decide the human gate** — anything that mutates the KB or sends external messages should
   stage for review or have a confirm step, per the golden rule.
5. **Test it** — deterministic path first (Gemini offline by default); opt into the AI path with mocks.
6. **Document it** — add a page in [`../features/`](../features/) and a row in the table above.
   Keep the change **focused** — don't refactor unrelated code.

### Planned agents (designed, not built)
**KB Gap-Filler** (turn logged misses into reviewed KB entries — self-improving), **Training/Cert
Tracker** (clone of Deadline Watcher), **Budget Helper**, **KB-Sync web scraper**, **Answer-Quality
Auditor**. Each must obey the same contract above.
