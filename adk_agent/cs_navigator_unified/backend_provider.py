"""Promptfoo provider that tests via the backend /chat/guest endpoint."""
import requests
import json

def call_api(prompt, options, context):
    """Send query to backend guest endpoint and return response."""
    config = options.get("config", {})
    base_url = config.get("base_url", "http://127.0.0.1:5001")

    try:
        r = requests.post(
            f"{base_url}/chat/guest",
            json={"query": prompt},
            timeout=60,
        )
        if r.status_code == 200:
            resp = r.json().get("response", "")
            return {"output": resp}
        elif r.status_code == 429:
            return {"output": "RATE_LIMITED", "error": "429 Too Many Requests"}
        else:
            return {"output": "", "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"output": "", "error": str(e)}
