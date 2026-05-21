"""
Promptfoo provider for the ORA Navigator backend `/chat/guest` endpoint.

This exercises the FULL pipeline (Layers 1-3: cache, KB browser, ADK agent,
regenerate-then-refuse). It is the default provider and the pre-deploy gate.

Config (from promptfooconfig.yaml `providers[].config`):
  base_url - backend base URL (default http://127.0.0.1:5002)
Env:
  BACKEND_URL - overrides base_url if set

Run the backend with GUEST_RATE_LIMIT relaxed so the eval is not throttled:
  GUEST_RATE_LIMIT=100000 uvicorn main:app --port 5002
"""
import os
import time

import requests

DEFAULT_BASE_URL = "http://127.0.0.1:5002"
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 3


def call_api(prompt, options, context):
    """Promptfoo provider entry point. Returns {"output": str} or {"error": str}."""
    config = (options or {}).get("config", {})
    base_url = os.getenv("BACKEND_URL") or config.get("base_url", DEFAULT_BASE_URL)
    url = f"{base_url}/chat/guest"

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(url, json={"query": prompt}, timeout=90)
        except Exception as e:  # noqa: BLE001
            return {"output": "", "error": str(e)}

        if r.status_code == 200:
            return {"output": r.json().get("response", "")}
        if r.status_code == 429:
            last_err = "429 Too Many Requests"
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        return {"output": "", "error": f"HTTP {r.status_code}"}

    return {"output": "", "error": f"rate limited after {MAX_RETRIES} retries ({last_err})"}
