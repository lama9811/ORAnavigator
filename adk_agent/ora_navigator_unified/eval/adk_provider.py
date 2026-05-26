"""
Promptfoo provider for the ORA Navigator ADK agent (Layers 1-2 only).

Sends queries to a locally running ADK server and returns the agent's reply.
Used by `run_eval.sh --adk`.

Env:
  ADK_BASE_URL  - ADK server base URL (default http://127.0.0.1:8081)

Usage:
  cd adk_agent && adk web . --port 8081
  npx promptfoo eval -c eval/promptfooconfig.yaml --providers python:eval/adk_provider.py
"""
import json
import logging
import os

import requests

ADK_BASE = os.getenv("ADK_BASE_URL", "http://127.0.0.1:8081")
APP_NAME = "ora_navigator_unified"
USER_ID = "promptfoo-tester"

logging.basicConfig(filename="/tmp/adk_provider.log", level=logging.DEBUG)


def extract_text(lines):
    """Pure function: pick the longest text part from ADK SSE `data:` lines."""
    all_texts = []
    for line in lines:
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            break
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        for part in data.get("content", {}).get("parts", []):
            text = part.get("text", "")
            if text and text.strip():
                all_texts.append(text)
    return max(all_texts, key=len) if all_texts else ""


def create_session():
    resp = requests.post(
        f"{ADK_BASE}/apps/{APP_NAME}/users/{USER_ID}/sessions",
        json={"state": {}},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("id")


def query_agent(prompt, session_id):
    payload = {
        "app_name": APP_NAME,
        "user_id": USER_ID,
        "session_id": session_id,
        "new_message": {"role": "user", "parts": [{"text": prompt}]},
        "streaming": False,
    }
    resp = requests.post(f"{ADK_BASE}/run_sse", json=payload, stream=True, timeout=120)
    resp.raise_for_status()
    return extract_text(list(resp.iter_lines(decode_unicode=True)))


def call_api(prompt, options, context):
    """Promptfoo provider entry point."""
    try:
        logging.debug(f"PROMPT ({len(prompt)} chars): {prompt[:100]}")
        session_id = create_session()
        output = query_agent(prompt, session_id)
        logging.debug(f"OUTPUT ({len(output)} chars): {output[:200]}")
        return {"output": output}
    except Exception as e:  # noqa: BLE001
        logging.error(f"ERROR: {e}")
        return {"error": str(e)}
