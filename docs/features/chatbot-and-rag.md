# Chatbot & RAG Pipeline

**In one line:** the grounded answer engine that turns a question into a trustworthy,
source-cited answer — and refuses rather than guesses.

## What it does (plain English)
A user asks an ORA question. The assistant searches Morgan's ORA knowledge base, writes an
answer **only** from what it found, shows the source links, and — if it can't find a real
answer — says so and points the user to ORA instead of making something up.

## Where it lives
- `adk_agent/ora_navigator_unified/agent.py` — the LLM agent, its tools, and the grounding rules.
- `adk_agent/ora_navigator_unified/kb_prefetch.py` — Layer 1 TF-IDF prefetch.
- `backend/vertex_agent.py` — the trust pipeline (Layers 2-3): `_run_verified`,
  `_evaluate_grounding`, `_check_identifier_faithfulness`.
- `backend/main.py` — `/chat/stream`, `/chat/guest` endpoints.
- `frontend/src/components/Chatbox.jsx` — the chat UI + the SSE streaming reader.

## How it works — three layers
```
QUICK CHECKS (no AI): greeting → canned · seen before → cache · "list X" → KB menu
   │
LAYER 1  TF-IDF prefetch: top-5 KB docs injected into the system prompt (~30ms, no cost)
   │
THE AI (Gemini): "search the KB on EVERY ORA question" → drafts an answer
   │
LAYER 2  grounding chunks → morgan.edu URLs become the "Sources" block
   │
LAYER 3  fact-checker: grade ok/weak → if weak, redo strictly KB-only → still nothing → REFUSE
         ("contact ORA"); flag any number/date/SOP#/email/phone not verbatim in the KB
   │
SAVE to cache → SEND answer + Sources
```
Layer 3 is the guarantee: it **checks the result** instead of trusting the model's confidence,
and forces a redo or refusal.

## API & data
- Endpoints: `POST /chat/stream` (authed), `POST /chat/guest` (rate-limited).
- Tables: `chat_history` (includes a `citations` JSON column so Sources survive refresh/cache),
  `failed_queries` (logs misses — fuel for a future KB Gap-Filler).

## Don't regress (load-bearing)
- **`/run_sse` Bearer token** on ADK calls — without it Cloud Run returns 403.
- **`GOOGLE_GENAI_USE_VERTEXAI=TRUE`** — else genai falls back to the public API.
- **Single-worker uvicorn** — grounding state in `vertex_agent.py` is thread-local.
- **Grounding trust-the-redo fix** — empty Pass-1 text *with zero chunks* degrades to a
  refusal via a `done` event (never a dead-end error). Paired with agent **Grounding Rule 7**.
- **Frontend SSE buffer flush** — after the read loop ends, the trailing partial line must be
  re-parsed; otherwise a late final `data:{done}` frame is dropped and the UI shows a false
  "couldn't generate a response."
- **Refusals/personal-recall are never cached.**

## Status
✅ Built & deployed. Core feature.
