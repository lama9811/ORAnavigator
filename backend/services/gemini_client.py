"""Shared Gemini client helper — one reusable phone line to Gemini 2.5 Flash.

Factored out of the (older) inline pattern in services/solicitation_extractor.py so
the ADVISORY AI layers (Draft Review coverage, Deadline Watcher personalized
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
import threading
import time
from typing import Optional

_genai = None
_client = None
# "The one attempt has FINISHED" — success or failure. Set in a `finally`, so a
# thread that is still inside the constructor has NOT set it and a second
# caller waits for the answer instead of being told there is none. See
# get_client() for why that ordering is the whole bug.
_init_attempted = False
_init_lock = threading.Lock()

# The model + region every existing caller gets. Do NOT change these to "upgrade"
# callers wholesale — the chat path is latency-critical and deliberately on Flash.
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_LOCATION = "us-central1"

# Clients for any NON-default region, built lazily and cached per location.
# Needed because some models are region-locked: gemini-3.6-flash answers ONLY on
# the "global" endpoint and 404s in us-central1 (same trap kb_scraper/adjudicator
# hit — see CLAUDE.md). Keyed by location string.
_alt_clients: dict = {}

# Transient rate-limit / quota errors are retryable; other errors fail fast to
# the caller's deterministic fallback. Backoffs are the per-retry sleeps (so
# len() == number of retries after the first attempt). Tests monkeypatch this
# to () for a no-delay single attempt.
_RETRY_MARKERS = ("429", "resource_exhausted", "too many requests", "rate limit")
_RETRY_BACKOFFS = (1.0, 2.0)


def _is_retryable(err: Exception) -> bool:
    msg = str(err).lower()
    return any(m in msg for m in _RETRY_MARKERS)


def get_client():
    """Lazily build + cache a Gemini client (Vertex first, API key fallback).

    Returns None — and stays None for the rest of the process — if init fails,
    so callers can detect "AI unavailable" without a network round-trip.

    SERIALISED, because a Section Check is itself a concurrent caller:
    `_voted_batch` asks the model three times at once. This used to set
    `_init_attempted = True` BEFORE building the client, so while the first
    thread sat in the constructor every other thread read the flag, took it for
    a finished failure, and got None — "AI unavailable" for a layer that was
    merely still starting. Measured 2026-08-28: three of four concurrent
    callers, and end to end an uploaded Project Summary returning in 0.3s with
    every semantic rule `unclear`.

    The flag now means "the one attempt has FINISHED", set in a `finally`, so
    the fast-None-forever contract on a REAL failure is unchanged — a genuine
    failure still costs exactly one attempt for the life of the process."""
    global _client, _init_attempted, _genai
    if _client is not None:
        return _client
    if _init_attempted:
        return None
    with _init_lock:
        # Re-checked under the lock: whoever waited here while another thread
        # built the client must return THAT client, not start a second one.
        if _client is not None:
            return _client
        if _init_attempted:
            return None
        try:
            from google import genai
            _genai = genai
            project = os.getenv("GOOGLE_CLOUD_PROJECT") or "infra-vertex-494621-v1"
            try:
                _client = genai.Client(vertexai=True, project=project,
                                       location=DEFAULT_LOCATION)
            except Exception:
                api_key = os.getenv("GEMINI_API_KEY", "")
                if api_key:
                    _client = genai.Client(api_key=api_key)
        except Exception as e:
            print(f"   [GEMINI] client init failed: {e}")
        finally:
            _init_attempted = True
    return _client


def _client_for(location: Optional[str]):
    """Client for `location`, or None if AI is unavailable.

    Deliberately probes the DEFAULT client first and returns None if that is
    None. Two reasons, both load-bearing:
      - It preserves the "fast None when unavailable" contract for every region,
        so an alternate-region caller still falls back deterministically offline.
      - tests/conftest.py disables the whole AI layer by pinning get_client() ->
        None. Building an alternate-region client without this probe would make
        REAL network calls during the unit suite, which is exactly what that
        fixture exists to prevent.
    """
    base = get_client()
    if base is None or not location or location == DEFAULT_LOCATION:
        return base
    if location in _alt_clients:
        return _alt_clients[location]
    client = None
    try:
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or "infra-vertex-494621-v1"
        client = _genai.Client(vertexai=True, project=project, location=location)
    except Exception as e:
        print(f"   [GEMINI] client init failed for location={location}: {e}")
    _alt_clients[location] = client
    return client


def _build_config(temperature: float, max_output_tokens: int,
                  json_mode: bool, timeout_s: Optional[float],
                  system_instruction: Optional[str],
                  thinking_budget: Optional[int] = None,
                  seed: Optional[int] = None) -> dict:
    config: dict = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if seed is not None:
        # THE SAMPLING SEED, and it DOES NOT BUY DETERMINISM on Vertex -- measured,
        # see the note by draft_review.SEMANTIC_VOTES. It reaches the SDK (verified
        # at the wire, config.seed == 11) and the same draft still scored 86% and
        # 93% across 10 runs. Kept because it is free and correct plumbing; do not
        # reach for it expecting reproducible reads. Omitted (None) => every
        # existing caller sends a byte-identical request.
        config["seed"] = seed
    if thinking_budget is not None:
        # Gemini 2.5 Flash "thinking" is ON by default and adds seconds of
        # latency. For latency-critical, low-temperature, grounded/structured
        # tasks a caller can pass thinking_budget=0 to disable it. google-genai
        # coerces this nested dict into a ThinkingConfig. Omitted (None) => the
        # model's default thinking stays on, so existing callers are unchanged.
        config["thinking_config"] = {"thinking_budget": thinking_budget}
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
              system_instruction: Optional[str] = None,
              thinking_budget: Optional[int] = None,
              seed: Optional[int] = None,
              model: Optional[str] = None,
              location: Optional[str] = None) -> Optional[str]:
    """Single Gemini round-trip → raw response text, or None on any failure.
    Never raises.

    `model`/`location` default to DEFAULT_MODEL @ DEFAULT_LOCATION, so every
    pre-existing caller is byte-for-byte unchanged. Pass both together when a
    model is region-locked (gemini-3.6-flash needs location="global")."""
    client = _client_for(location)
    if client is None:
        return None
    model = model or DEFAULT_MODEL
    # One attempt, plus a bounded retry-with-backoff on transient 429 /
    # RESOURCE_EXHAUSTED errors (common under burst load). Non-retryable errors
    # return None immediately so the caller falls back to its deterministic path.
    for attempt in range(len(_RETRY_BACKOFFS) + 1):
        try:
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=_build_config(temperature, max_output_tokens, json_mode,
                                         timeout_s, system_instruction, thinking_budget,
                                         seed),
                )
            except TypeError:
                # SDK rejected the http_options timeout key — retry without it.
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=_build_config(temperature, max_output_tokens, json_mode,
                                         None, system_instruction, thinking_budget,
                                         seed),
                )
            return (response.text or "").strip() or None
        except Exception as e:
            if _is_retryable(e) and attempt < len(_RETRY_BACKOFFS):
                delay = _RETRY_BACKOFFS[attempt]
                print(f"   [GEMINI] rate-limited (attempt {attempt + 1}); retrying in {delay}s")
                time.sleep(delay)
                continue
            print(f"   [GEMINI] generate failed: {e}")
            return None


def generate_text(prompt: str, *, temperature: float = 0.0,
                  max_output_tokens: int = 2048,
                  timeout_s: Optional[float] = None,
                  system_instruction: Optional[str] = None,
                  thinking_budget: Optional[int] = None,
                  seed: Optional[int] = None,
                  model: Optional[str] = None,
                  location: Optional[str] = None) -> Optional[str]:
    """Free-text Gemini call. Returns the text, or None if unavailable/failed."""
    return _generate(prompt, temperature=temperature,
                     max_output_tokens=max_output_tokens,
                     json_mode=False, timeout_s=timeout_s,
                     system_instruction=system_instruction,
                     thinking_budget=thinking_budget,
                     seed=seed,
                     model=model, location=location)


def _close_unbalanced(text: str) -> Optional[str]:
    """`text` with any unclosed objects/arrays closed, or None if unrepairable.

    Observed live: a reviewer reply ending `..."}\\n]` -- the outer object's
    closing brace simply absent, with finish_reason STOP and 1932 of 8192 output
    tokens used, so not truncation at the ceiling. One missing character costs a
    whole batch, because `_review_batch` reads a failed parse as a failed call
    and marks every requirement in it `unclear`.

    Scans OUTSIDE strings only -- an evidence quote containing braces is
    ordinary in this app ("the set {x, y}"), and counting those as structure
    would append the wrong closers. Returns None while a string is still open,
    because a reply cut mid-value is genuinely lost and guessing at it would
    invent data.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                return None          # genuinely malformed, not merely unfinished
            stack.pop()
    if in_string or not stack:
        return None
    return text + "".join(reversed(stack))


def _as_dict(parsed, list_key: Optional[str]) -> Optional[dict]:
    """A dict as-is; a bare list wrapped when the caller named a key for it."""
    if isinstance(parsed, dict):
        return parsed
    if list_key and isinstance(parsed, list):
        return {list_key: parsed}
    return None


def generate_json(prompt: str, *, temperature: float = 0.0,
                  max_output_tokens: int = 4096,
                  timeout_s: Optional[float] = None,
                  system_instruction: Optional[str] = None,
                  thinking_budget: Optional[int] = None,
                  seed: Optional[int] = None,
                  model: Optional[str] = None,
                  location: Optional[str] = None,
                  list_key: Optional[str] = None) -> Optional[dict]:
    """JSON Gemini call. Forces application/json, strips any markdown fences,
    parses with strict=False (tolerates control chars from PDF text). Returns a
    dict, or None on unavailable / malformed / non-dict output. Never raises.

    `list_key` opts a caller in to a BARE TOP-LEVEL ARRAY, wrapping it as
    {list_key: [...]}. Seen live on a 15-rule Project Description: the reviewer
    answered with `[{...}, {...}]` instead of `{"findings": [...]}`. It parsed
    perfectly and every assessment in it was right, and the dict-only return
    threw all fifteen away — the section then scored 3 of 3 on its deterministic
    rules and displayed a confident 100% while fifteen real rules went
    unchecked. A false 100% is worse than a wrong number: it says "nothing to
    fix".

    Opt-in BY NAME rather than a silent widening, because five other callers
    rely on the dict contract and a shape that changes underneath them is how
    this class of bug started.
    """
    raw = _generate(prompt, temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    json_mode=True, timeout_s=timeout_s,
                    system_instruction=system_instruction,
                    thinking_budget=thinking_budget,
                    model=model, location=location,
                    seed=seed)
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
        # A CLOSING fence with no opening one. Observed live: the reviewer
        # returned valid JSON followed by a bare "```", the stripper above never
        # fired because the text does not START with a fence, and the call was
        # thrown away with "Extra data: line 1 column 8421".
        #
        # The cost is a whole batch, not a row: `draft_review._review_batch`
        # reads None as a failed call and falls back to `unclear` for EVERY
        # requirement in it. Measured on a real Project Description, 15 rules
        # went `unclear` at once and the section scored 3 of 3 on its
        # deterministic rules instead of 13 of 16 -- and `unclear` is absent
        # from _CREDIT, so they left the denominator with nothing on screen to
        # say so.
        #
        # Repaired only AFTER an honest parse has failed, so a response that
        # already parses is never touched -- an `evidence` quote containing a
        # code fence is a real thing a reviewer can return.
        repaired = text.rstrip()
        if repaired.endswith("```"):
            repaired = repaired[:-3].rstrip()
        for candidate in (repaired, _close_unbalanced(repaired)):
            if candidate is None or candidate == text:
                continue
            try:
                parsed = json.loads(candidate, strict=False)
            except (json.JSONDecodeError, ValueError):
                continue
            return _as_dict(parsed, list_key)
        snippet = text[:300].replace("\n", "\\n")
        print(f"   [GEMINI] JSON parse failed: {e} | {snippet}")
        return None
    return _as_dict(parsed, list_key)
