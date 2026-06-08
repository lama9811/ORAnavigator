# Faithfulness Eval Harness

**In one line:** an automated test that scores how often the assistant stays grounded (doesn't hallucinate).

## What it does (plain English)
Runs ~108 graded questions through the assistant and measures a **faithfulness %** — how reliably
it answers only from the KB and refuses when it shouldn't guess. Gates against a recorded baseline
so a change that makes hallucination worse fails the check.

## Where it lives
- `adk_agent/ora_navigator_unified/eval/` (promptfoo-based; graded by Vertex Gemini 2.5 Flash).
- `eval/run_eval.sh`, `eval/baseline.json`, `eval/EVAL.md`.

## How it works
- 108-case graded suite; `run_eval.sh` scores faithfulness and **fails on >2-pt regression** vs
  `baseline.json`. `--adk` runs ADK-direct (Layers 1-2 only).
- `GUEST_RATE_LIMIT` is env-overridable so full runs aren't throttled.

## Status / notes
- ✅ Harness built; baseline recorded `{pass_rate 0.602, faithfulness 0.708, total 108}`
  (full-pipeline run). The headline is a **conservative floor** — several "FAIL" cases are actually
  correct faithful answers the rubric over-penalizes.
- No NIH coverage yet (NIH FOAs are HTML-only) — add a case if a clean PDF appears.
- Must be run with a local ADK (:8081) + backend (:5002) up; can't run from the agent sandbox
  (no network on background servers there).

> **Reuse note:** this harness is the measurement arm for a future **Answer-Quality Auditor** that
> samples live answers and re-grades them after each deploy.
