"""
Custom Promptfoo provider for ADK Agent Engine.
Sends queries to the local ADK server and returns the agent's response.

Usage:
  1. Start ADK locally: cd adk_agent && adk web .
  2. Run: npx promptfoo eval -c adk_agent/cs_navigator_unified/promptfooconfig.yaml
  3. View: npx promptfoo view
"""

import json
import logging
import requests

ADK_BASE = "http://127.0.0.1:8080"
APP_NAME = "cs_navigator_unified"
USER_ID = "promptfoo-tester"

logging.basicConfig(filename='/tmp/adk_provider.log', level=logging.DEBUG)


def create_session():
    """Create a new ADK session."""
    resp = requests.post(
        f"{ADK_BASE}/apps/{APP_NAME}/users/{USER_ID}/sessions",
        json={"state": {}},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("id")


def query_agent(prompt, session_id):
    """Send a query to the ADK agent via SSE and collect the full response."""
    payload = {
        "app_name": APP_NAME,
        "user_id": USER_ID,
        "session_id": session_id,
        "new_message": {
            "role": "user",
            "parts": [{"text": prompt}],
        },
        "streaming": False,
    }
    resp = requests.post(
        f"{ADK_BASE}/run_sse",
        json=payload,
        stream=True,
        timeout=120,
    )
    resp.raise_for_status()

    all_texts = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            break
        try:
            data = json.loads(data_str)
            content = data.get("content", {})
            parts = content.get("parts", [])
            for part in parts:
                if "text" in part and len(part["text"].strip()) > 0:
                    all_texts.append(part["text"])
        except json.JSONDecodeError:
            continue

    if all_texts:
        return max(all_texts, key=len)
    return ""


def call_api(prompt, options, context):
    """Promptfoo provider entry point."""
    try:
        logging.debug(f"PROMPT ({len(prompt)} chars): {prompt[:100]}")
        session_id = create_session()
        logging.debug(f"SESSION: {session_id}")
        output = query_agent(prompt, session_id)
        logging.debug(f"OUTPUT ({len(output)} chars): {output[:200]}")
        return {"output": output}
    except Exception as e:
        logging.error(f"ERROR: {e}")
        return {"error": str(e)}
