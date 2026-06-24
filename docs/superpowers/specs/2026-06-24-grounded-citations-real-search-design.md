# Design: Answer from the real KB search, with guaranteed Sources

**Date:** 2026-06-24
**Branch:** `feat/grounded-citations-real-search`

## Problem

Curated "top 10" questions (e.g. *"How long does IRB approval typically take and when
does the IRB meet?"*) come back **correct but with no Sources attached**, and the turn is
slow (~40s).

Root cause: on every fresh user turn the ADK agent injects pre-fetched KB docs straight
into the model instruction (`adk_agent/ora_navigator_unified/agent.py:135`, via
`kb_prefetch.prefetch_kb_context`). The injected block is labeled *"use this to ground
your answer,"* so the model answers **from the injected text** rather than from the native
`VertexAiSearchTool` retrieval grounding. When the answer's spans trace to injected
instruction text instead of retrieved passages, `groundingSupports`/`groundingChunks` come
back empty → the backend attaches no citation (`backend/vertex_agent.py:1066-1088`) → the
weak-grounding verdict also fires, triggering Pass-2 regeneration (the ~40s).

**Verified constraint:** `VertexAiSearchTool` renders as *native retrieval grounding*
(`Tool.retrieval = Retrieval(vertex_ai_search=...)`). There is **no** force/mode knob for
retrieval — `ToolConfig.function_calling_config.mode = ANY` governs only
`function_declarations`, and `DynamicRetrievalConfig` belongs to GoogleSearchRetrieval, not
`vertex_ai_search`. So we cannot "force the tool call." We instead (B) stop letting prefetch
be the answer so native grounding attributes/cites it, and (C) guarantee Sources in the
backend regardless of model attribution.

## Goal

Prefetch stays **only** as a hallucination guard. The visible answer is driven and sourced
by the real KB search (B), and the backend **guarantees** a Sources block even when the
model's grounding metadata is empty (C).

## Part B — demote prefetch (ADK agent)

1. `kb_prefetch.py:202-205` — reframe the injected block header from
   *"PRE-FETCHED KB CONTEXT - use this to ground your answer"* to a **reference/fact-check
   only** framing: it must NOT be treated as the retrieved answer; the answer and its
   sources must rest on the model's knowledge-base search results.
2. `agent.py` `BASE_INSTRUCTION` GROUNDING RULES — add one rule stating the reference
   context is a hallucination check, and the visible answer must be grounded in the KB
   search results (so native retrieval grounding attributes the answer → grounding metadata
   populates → citations appear).

B is a soft, model-dependent nudge. C is the hard guarantee.

## Part C — deterministic citation fallback (backend)

New helper `_fallback_citations(query) -> list[{title, url}]` in
`backend/vertex_agent.py`:
- Runs a **live Discovery Engine search** over the KB datastore (mirrors `kb_prefetch`'s
  retrieval/datastore config), takes the top 3-5 results.
- Resolves each result's title to a URL via the existing `_get_kb_url_map()` /
  `_norm_title()`, returning the same shape as `_extract_citations`.
- Returns `[]` when retrieval scores no real match (never a blank guess).

Wire-in at the **DELIVER** step of both `_run_verified` (`~1318`) and
`_run_verified_stream` (`~1388`): if `result["citations"]` is empty AND the answer is a
genuine ORA content answer, set `result["citations"] = _fallback_citations(message)` before
`_set_grounding(...)` / the `citations` event.

### Guards (must all hold before attaching fallback citations)
- **Skip small talk / greetings / meta** — reuse `_is_smalltalk(query)`; a greeting has no
  source. (Greetings are canned by the ADK fast-path and would otherwise wrongly get
  sources.)
- **Skip on refusal / outage / `kb_fail`** — no Sources on "I don't have that."
- **Only attach when retrieval actually matches** (score threshold like prefetch).

This mirrors the precedent at `_extract_citations:390` (retrieval-order fallback when
supports are absent); C extends that safety net to the zero-chunk case.

## Data flow (after change)

prefetch (anti-hallucination, not answer-ready) → native VertexAiSearch grounds + cites the
answer (B) → backend; if citations still empty on a real ORA answer →
`_fallback_citations` attaches them (C) → `get_last_grounding()` → UI Sources block.

## Testing

- Backend pytest stays green:
  `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py`
- New unit tests for `_fallback_citations`:
  - returns sources for an IRB-style content query (Discovery Engine mocked),
  - returns `[]` for small talk,
  - guards fire on refusal / outage / `kb_fail` (no fallback attached).
- Manual: ask the IRB question in a fresh window; confirm a Sources block renders.

## Non-goals / scope guard

- No change to the deterministic core, refusal logic, or greeting fast-path behavior.
- Not "forcing" the retrieval tool (verified unsupported for `vertex_ai_search`).
- B touches 2 spots; C adds one helper + two wire-ins + guards.

## Golden-rule alignment

- AI stays advisory; citations are resolved deterministically from the KB URL map (rule 1).
- Graceful fallback preserved: `_fallback_citations` returns `[]` on any retrieval failure;
  the answer still delivers (rule 3).
- One focused feature; no unrelated refactor (rule 6).
