"""Shared Gemini client helper — one reusable phone line to Gemini 2.5 Flash.

Factored out of the (older) inline pattern in services/solicitation_extractor.py so
the new ADVISORY AI layers (Draft Critic AI review, Deadline Watcher personalized
emails) all share one client + one set of safety guarantees:

  - Vertex-first, API-key fallback, cached client (no per-call init cost).
  - **Fast None when unavailable.** If the client can't initialize (no ADC /
    Vertex unreachable — the offline/CI condition), every later call returns
    None/"" IMMEDIATELY without touching the network. This is what lets every
    caller fall back to its deterministic path with zero hang.
  - generate_json / generate_text NEVER raise — they return None on any error.

Model is "gemini-2.5-flash" (2.0 404s in this Vertex project). Reuses the env the
app already requires (GOOGLE_CLOUD_PROJECT / ADC / GOOGLE_GENAI_USE_VERTEXAI) — no
new secrets.

NOTE: solicitation_extractor.py keeps its own copy for now (its prompt + budget /
sponsor coercion are tightly coupled); it could later delegate to this module.
"""

import json
import os
from typing import Optional

_genai = None
_client = None
_init_attempted = False


def get_client():
    """Lazily build + cache a Gemini client (Vertex first, API key fallback).

    Returns None — and stays None for the rest of the process — if init fails,
    so callers can detect "AI unavailable" without a network round-trip."""
    global _client, _init_attempted, _genai
    if _client is not None:
        return _client
    if _init_attempted:
        return None
    _init_attempted = True
    try:
        from google import genai
        _genai = genai
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or "infra-vertex-494621-v1"
        try:
            _client = genai.Client(vertexai=True, project=project,
                                   location="us-central1")
        except Exception:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if api_key:
                _client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"   [GEMINI] client init failed: {e}")
    return _client


def _build_config(temperature: float, max_output_tokens: int,
                  json_mode: bool, timeout_s: Optional[float],
                  system_instruction: Optional[str]) -> dict:
    config: dict = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if json_mode:
        config["response_mime_type"] = "application/json"
    if system_instruction:
        # The strict "rules of the road" for the model -- carries more weight
        # than putting the same text inline in the prompt.
        config["system_instruction"] = system_instruction
    if timeout_s:
        # google-genai accepts a per-request http timeout in milliseconds via
        # http_options. Some SDK versions don't, so callers of _generate retry
        # without it on TypeError.
        config["http_options"] = {"timeout": int(timeout_s * 1000)}
    return config


def _generate(prompt: str, *, temperature: float, max_output_tokens: int,
              json_mode: bool, timeout_s: Optional[float],
              system_instruction: Optional[str] = None) -> Optional[str]:
    """Single Gemini round-trip → raw response text, or None on any failure.
    Never raises."""
    client = get_client()
    if client is None:
        return None
    try:
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=_build_config(temperature, max_output_tokens, json_mode,
                                     timeout_s, system_instruction),
            )
        except TypeError:
            # SDK rejected the http_options timeout key — retry without it.
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=_build_config(temperature, max_output_tokens, json_mode,
                                     None, system_instruction),
            )
        return (response.text or "").strip() or None
    except Exception as e:
        print(f"   [GEMINI] generate failed: {e}")
        return None


def generate_text(prompt: str, *, temperature: float = 0.0,
                  max_output_tokens: int = 2048,
                  timeout_s: Optional[float] = None,
                  system_instruction: Optional[str] = None) -> Optional[str]:
    """Free-text Gemini call. Returns the text, or None if unavailable/failed."""
    return _generate(prompt, temperature=temperature,
                     max_output_tokens=max_output_tokens,
                     json_mode=False, timeout_s=timeout_s,
                     system_instruction=system_instruction)


def generate_json(prompt: str, *, temperature: float = 0.0,
                  max_output_tokens: int = 4096,
                  timeout_s: Optional[float] = None,
                  system_instruction: Optional[str] = None) -> Optional[dict]:
    """JSON Gemini call. Forces application/json, strips any markdown fences,
    parses with strict=False (tolerates control chars from PDF text). Returns a
    dict, or None on unavailable / malformed / non-dict output. Never raises."""
    raw = _generate(prompt, temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    json_mode=True, timeout_s=timeout_s,
                    system_instruction=system_instruction)
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        parsed = json.loads(text, strict=False)
    except (json.JSONDecodeError, ValueError) as e:
        snippet = text[:300].replace("\n", "\\n")
        print(f"   [GEMINI] JSON parse failed: {e} | {snippet}")
        return None
    return parsed if isinstance(parsed, dict) else None
