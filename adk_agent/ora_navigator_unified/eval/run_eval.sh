#!/usr/bin/env bash
# ORA Navigator — Faithfulness Exam runner (the pre-deploy gate).
#
#   ./run_eval.sh                 # full pipeline (backend /chat/guest), gate vs baseline
#   ./run_eval.sh --adk           # ADK-direct (Layers 1-2 only)
#   ./run_eval.sh --update-baseline   # record current metrics as the new baseline
#
# Prerequisites:
#   - Backend running on :5002 with the rate limit relaxed:
#       cd backend && GUEST_RATE_LIMIT=100000 uvicorn main:app --host 127.0.0.1 --port 5002
#   - For --adk: ADK running on :8081:  cd adk_agent && adk web . --port 8081
#   - GCP ADC configured (the Vertex Gemini grader uses it).
set -euo pipefail

EVAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${EVAL_DIR}/../../.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"
RESULTS="${EVAL_DIR}/results.json"

USE_ADK=0
UPDATE_BASELINE=0
for arg in "$@"; do
  case "$arg" in
    --adk) USE_ADK=1 ;;
    --update-baseline) UPDATE_BASELINE=1 ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

cd "${EVAL_DIR}"
export PROMPTFOO_PYTHON="${VENV_PY}"

PROVIDER_ARGS=()
if [[ "${USE_ADK}" -eq 1 ]]; then
  echo ">>> Mode: ADK-direct (Layers 1-2)"
  PROVIDER_ARGS=(--providers "python:adk_provider.py")
else
  echo ">>> Mode: backend full pipeline (Layers 1-3)"
fi

echo ">>> Running promptfoo eval..."
# "${arr[@]+"${arr[@]}"}" safely expands a possibly-empty array under `set -u`
# (bare "${arr[@]}" is an unbound-variable error on macOS's bash 3.2).
# `|| true`: promptfoo exits non-zero (100) whenever any case fails its
# assertions — that is the NORMAL case for this harness. score.py is the real
# gate; the genuine-failure signal is a missing results.json, checked below.
npx promptfoo eval -c promptfooconfig.yaml --no-cache \
  -o "${RESULTS}" "${PROVIDER_ARGS[@]+"${PROVIDER_ARGS[@]}"}" || true

if [[ ! -f "${RESULTS}" ]]; then
  echo "ERROR: promptfoo did not produce ${RESULTS} — the eval did not run." >&2
  exit 1
fi

echo ">>> Scoring..."
if [[ "${UPDATE_BASELINE}" -eq 1 ]]; then
  "${VENV_PY}" score.py "${RESULTS}" --update-baseline
else
  "${VENV_PY}" score.py "${RESULTS}"
fi
