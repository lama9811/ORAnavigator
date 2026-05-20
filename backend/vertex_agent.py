"""
Vertex AI Agent Engine Client
==============================

Communicates with the ORA Navigator agent running on Google ADK web server.
Handles session management and SSE response parsing.

v4.2: Smart session reuse. Sessions are cached per user with a TTL and
context hash. If the same user sends multiple queries with the same
attached context, we reuse the existing session instead of creating a
new one each time. Saves ~100-200ms per request.

Usage:
    Local dev:  ADK web server at http://127.0.0.1:8080
    Production: Vertex AI Agent Engine (deployed reasoning engine)
"""

import os
import json
import re
import hashlib
import time as time_module
import requests
from typing import Optional
# Gates (retrieval_gate, verification_gate, fast_retrieval) available as admin utilities
# but removed from the hot path. The agent's built-in VertexAiSearchTool handles
# retrieval. The grounding gate + faculty faithfulness check handle quality.

# Configuration
ADK_BASE_URL = os.getenv("ADK_BASE_URL", "http://127.0.0.1:8080")
ADK_APP_NAME = os.getenv("ADK_APP_NAME", "ora_navigator_unified")

# ---------------------------------------------------------------------------
# Procedure Guide Links: maps keywords to ORA Drive/SharePoint doc links.
# If the agent's response mentions a procedure but omits the source link,
# the post-processor appends it so users always get the official guide.
# Populate when ORA publishes its procedure-guide URLs (NCE, IRB, COI, etc.).
# ---------------------------------------------------------------------------
_PROCEDURE_LINKS: dict[str, tuple[str, str]] = {}


def _inject_procedure_links(response_text: str) -> str:
    """Append Drive guide links if the response discusses procedures but lacks the links."""
    if "drive.google.com" in response_text:
        return response_text  # Already has a Drive link, skip

    lower = response_text.lower()
    seen_urls = set()
    matches = []
    for keyword, (label, url) in _PROCEDURE_LINKS.items():
        if keyword in lower and url not in seen_urls:
            matches.append((label, url))
            seen_urls.add(url)

    if matches:
        links = "\n".join(f"- [{label}]({url})" for label, url in matches)
        if len(matches) == 1:
            label, url = matches[0]
            return response_text.rstrip() + f"\n\nFor the full official guide with screenshots, view: [{label}]({url})"
        else:
            return response_text.rstrip() + f"\n\n**Related guides:**\n{links}"
    return response_text


# User-facing message when ADK is down. Clearly says it's a system issue,
# NOT a knowledge gap. Prevents users from thinking the bot can't answer.
_OUTAGE_MSG = (
    "I'm temporarily having trouble connecting to my knowledge base. "
    "This is a system issue, not a gap in my knowledge. "
    "Please try again in a minute. If the problem persists, contact ORA at 443-885-4044."
)

# Grounding validation: minimum thresholds before flagging a response
_GROUNDING_MIN_CHUNKS = 2       # At least 2 KB docs must be cited
_GROUNDING_DISCLAIMER = (
    "\n\n---\n*I may not have complete information on this topic in my knowledge base. "
    "Please verify with ORA at 443-885-4044 or ask.ora@morgan.edu.*"
)

# Patterns that are inherently non-KB (greetings, security refusals, outages)
# These responses don't need KB grounding so skip the gate
_SKIP_GROUNDING_RE = re.compile(
    r'^(Hey!|Hello!|ORA Navigator was developed|I can only help with Morgan State|I\'m temporarily having trouble|You\'re welcome)',
    re.IGNORECASE,
)

# Detects when Gemini self-reports a KB access failure (transient Vertex AI Search issue)
_KB_FAIL_RE = re.compile(r"having trouble (accessing|connecting to) my knowledge base", re.IGNORECASE)

# =============================================================================
# FAITHFULNESS GATE: ORA Staff Entity Whitelist
# =============================================================================
# Catches hallucinated staff names. When a "Dr./Professor X" appears in the
# response and X isn't in the ORA staff roster, append a faithfulness
# disclaimer pointing to the staff directory.
#
# Source of truth: backend/kb_structured/_generated_staff/staff_*.json
# (14 ORA staff as of 2026-05-15). Refresh via:
#   ls backend/kb_structured/_generated_staff/staff_*.json | xargs -n1 jq -r .staff_last | tr A-Z a-z

_FACULTY_LAST_NAMES = {
    "aladesote",   # Olatunde Aladesote — Assistant, Research Compliance
    "boone",       # Taylor Boone — Grant Administrator
    "kamangar",    # Farin Kamangar — Associate Vice President for Research
    "lee",         # Matthew Lee — Senior Grants and Contract Manager
    "li",          # Deshun Li — Research Budget Development Specialist
    "manyara",     # Lucy Manyara — Budget Officer
    "mirithu",     # Poline Mirithu — Grants and Contracts Manager
    "mobley",      # Ryan Mobley — Training and Communications Coordinator
    "moncrieffe",  # Keyshawn Moncrieffe — Acting Director for Research Compliance
    "shine-lee",   # Shamon Shine-Lee — Budget Officer
    "silver",      # Gillian Silver — Director
    "steiner",     # Rebecca Steiner — Grant Administrator
    "talton",      # Katherine Talton — Grant Administrator
    "zhang",       # Ailing Zhang — Senior Grants Manager
}

_PROF_NAME_RE = re.compile(
    r'(?:Dr\.|Professor|Prof\.)\s+(?:[A-Z][a-z]+\s+)?([A-Z][a-zA-Z\-]+)',
)

_FAITHFULNESS_DISCLAIMER = (
    "\n\n---\n*Some names in this response may not match the ORA staff directory. "
    "Please verify at the [ORA Staff Directory](https://www.morgan.edu/office-of-research-administration/about/staff-directory) "
    "or contact ask.ora@morgan.edu.*"
)


def _check_faculty_faithfulness(text: str) -> list[str]:
    """Check if the response mentions professor names not in the Office of Research Administration.
    Returns list of hallucinated names (empty if all names check out)."""
    if not text:
        return []
    matches = _PROF_NAME_RE.findall(text)
    hallucinated = []
    for surname in matches:
        if surname.lower().rstrip(".,;:!?'\"") not in _FACULTY_LAST_NAMES:
            hallucinated.append(surname)
    return hallucinated


def _apply_grounding_gate(text: str, chunks: int, coverage: float = 0.0, has_attached_context: bool = False) -> str:
    """Append a disclaimer when the agent answered with insufficient data sources.

    Checks both chunk count AND coverage ratio. A response needs either:
    - At least 2 KB chunks cited, OR
    - Coverage >= 0.3 (30% of response backed by KB), OR
    - Attached context present (account profile or uploaded file content)

    This prevents responses that cite 1 chunk but are 90% hallucinated from passing.
    """
    if not text or _SKIP_GROUNDING_RE.match(text):
        return text
    if has_attached_context:
        return text
    if chunks >= _GROUNDING_MIN_CHUNKS:
        return text
    if coverage >= 0.3:
        return text
    print(f"   [GROUNDING] Low confidence ({chunks} chunks, {coverage:.1%} coverage, no attached context) - appending disclaimer")
    return text + _GROUNDING_DISCLAIMER


# =============================================================================
# CITATIONS: map KB doc titles -> source URLs so answers can show "Sources"
# =============================================================================
_kb_url_map: Optional[dict] = None


def _norm_title(t: str) -> str:
    """Normalize a doc title for case/whitespace-insensitive matching."""
    return " ".join((t or "").lower().split())


def _get_kb_url_map() -> dict:
    """Lazy-build a {normalized title -> source_url} map from the KB JSON files.
    Grounding chunks carry the doc title; the live datastore does not store the
    source URL, so we resolve titles back to morgan.edu URLs here."""
    global _kb_url_map
    if _kb_url_map is not None:
        return _kb_url_map
    _kb_url_map = {}
    try:
        import glob
        kb_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb_structured")
        for path in glob.glob(os.path.join(kb_dir, "**", "*.json"), recursive=True):
            if os.path.basename(path).startswith("_"):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except Exception:
                continue
            if isinstance(doc, dict):
                title, url = doc.get("title"), doc.get("source_url")
                if title and url:
                    _kb_url_map[_norm_title(title)] = url
        print(f"   [CITATIONS] Loaded {len(_kb_url_map)} KB title->URL mappings")
    except Exception as e:
        print(f"   [CITATIONS] Failed to load KB URL map: {e}")
    return _kb_url_map


def _extract_citations(chunks: list) -> list:
    """Turn Gemini groundingChunks into a deduped list of {title, url} citations.
    Only chunks that resolve to a real URL are kept, so every source is clickable.
    Capped at 5."""
    url_map = _get_kb_url_map()
    citations: list = []
    seen: set = set()
    for c in chunks:
        if not isinstance(c, dict):
            continue
        rc = c.get("retrievedContext") or c.get("web") or {}
        title = (rc.get("title") or "").strip()
        if not title:
            continue
        key = _norm_title(title)
        if key in seen:
            continue
        seen.add(key)
        url = url_map.get(key)
        if not url:
            uri = rc.get("uri", "") or ""
            if uri.startswith("http"):
                url = uri
        if url:
            citations.append({"title": title, "url": url})
        if len(citations) >= 5:
            break
    return citations


# =============================================================================
# IDENTIFIER FAITHFULNESS: SOP/FWA/EIN/UEI numbers and rates must be KB-grounded
# =============================================================================
_IDENTIFIER_PATTERNS = [
    ("SOP number", re.compile(r'\bSOP\s?#?\s?\d{1,3}\b', re.IGNORECASE)),
    ("FWA number", re.compile(r'\bFWA\s?#?\s?\d{6,10}\b', re.IGNORECASE)),
    ("EIN", re.compile(r'\b\d{2}-\d{7}\b')),
    ("UEI", re.compile(r'\bUEI[:\s#]+[A-Z0-9]{12}\b', re.IGNORECASE)),
]
_RATE_KEYWORDS_RE = re.compile(r'F&A|facilities and administrative|indirect cost|fringe', re.IGNORECASE)
_PERCENT_RE = re.compile(r'\b\d{1,3}(?:\.\d+)?\s?%')

_IDENTIFIER_DISCLAIMER = (
    "\n\n---\n*Some figures or identifiers above could not be verified against "
    "ORA's knowledge base. Please confirm them with ORA at 443-885-4044 or "
    "ask.ora@morgan.edu before relying on them.*"
)


def _norm_for_match(s: str) -> str:
    """Lowercase and collapse whitespace for verbatim substring matching."""
    return re.sub(r'\s+', ' ', (s or '').lower())


def _join_chunk_texts(chunks: list) -> str:
    """Concatenate the text snippets from grounding chunks (the retrieved KB
    passages) so identifiers in the answer can be checked against them."""
    parts = []
    for c in chunks:
        if isinstance(c, dict):
            rc = c.get("retrievedContext") or c.get("web") or {}
            t = rc.get("text") or ""
            if t:
                parts.append(t)
    return "\n".join(parts)


def _check_identifier_faithfulness(text: str, grounded_corpus: str) -> list:
    """Return identifiers/rates stated in `text` that do not appear verbatim in
    the retrieved KB text. Skipped when no KB text was retrieved, to avoid false
    positives. Soft guardrail — the caller appends a disclaimer, never blocks."""
    if not text or not grounded_corpus or len(grounded_corpus) < 50:
        return []
    corpus = _norm_for_match(grounded_corpus)
    unverified = []
    for label, pat in _IDENTIFIER_PATTERNS:
        for token in pat.findall(text):
            if _norm_for_match(token) not in corpus:
                unverified.append(f"{label} '{token.strip()}'")
    # Rates: only when the answer frames a number as an F&A/indirect/fringe rate
    if _RATE_KEYWORDS_RE.search(text):
        for pct in _PERCENT_RE.findall(text):
            if _norm_for_match(pct) not in corpus:
                unverified.append(f"rate '{pct.strip()}'")
    return unverified[:6]


# Session reuse settings
SESSION_TTL = 28800  # 8 hours: shorter TTL prevents stale context

# Session cache: user_id -> {"session_id", "created_at", "context_hash"}
_session_cache: dict[str, dict] = {}


# Cloud Run auth: when ADK is --no-allow-unauthenticated, we need an ID token
_id_token_cache: dict = {"token": None, "expires": 0}

def _get_auth_headers() -> dict:
    """Get auth headers for calling the ADK service on Cloud Run.
    Uses the GCE metadata server to fetch an ID token in production.
    Returns plain headers for local dev (localhost)."""
    if "localhost" in ADK_BASE_URL or "127.0.0.1" in ADK_BASE_URL:
        return {"Content-Type": "application/json"}

    now = time_module.time()
    if _id_token_cache["token"] and now < _id_token_cache["expires"] - 60:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {_id_token_cache['token']}"}

    # Method 1: GCE metadata server (works on Cloud Run, GCE, GKE)
    try:
        audience = ADK_BASE_URL.rstrip("/")
        metadata_url = (
            f"http://metadata.google.internal/computeMetadata/v1/"
            f"instance/service-accounts/default/identity?audience={audience}"
        )
        resp = requests.get(metadata_url, headers={"Metadata-Flavor": "Google"}, timeout=5)
        if resp.status_code == 200:
            token = resp.text
            _id_token_cache["token"] = token
            _id_token_cache["expires"] = now + 3600
            print(f"   [AUTH] Got ID token via metadata server")
            return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    except Exception as e:
        print(f"   [AUTH] Metadata server failed: {e}")

    # Method 2: google-auth library fallback
    try:
        import google.auth.transport.requests as gauth_requests
        import google.oauth2.id_token
        auth_req = gauth_requests.Request()
        token = google.oauth2.id_token.fetch_id_token(auth_req, ADK_BASE_URL)
        _id_token_cache["token"] = token
        _id_token_cache["expires"] = now + 3600
        print(f"   [AUTH] Got ID token via google-auth")
        return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    except Exception as e:
        print(f"   [AUTH] google-auth fallback failed: {e}")

    return {"Content-Type": "application/json"}


def _compute_context_hash(context: str) -> str:
    """Hash the attached context string to detect changes between queries."""
    if not context:
        return ""
    return hashlib.md5(context.encode()).hexdigest()[:12]


def _create_session(user_id: str, state: Optional[dict] = None) -> str:
    """Create a new ADK session for the user, optionally with initial state.
    Retries once on timeout to handle Cloud Run cold starts on the ADK service."""
    import time as _time
    body = {"state": state} if state else {}
    for attempt in range(2):
        try:
            resp = requests.post(
                f"{ADK_BASE_URL}/apps/{ADK_APP_NAME}/users/{user_id}/sessions",
                headers=_get_auth_headers(),
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
            session_id = resp.json().get("id")
            if session_id:
                print(f"   ADK session created: {session_id} for user {user_id} (attempt {attempt+1})")
                return session_id
        except Exception as e:
            print(f"   ADK session attempt {attempt+1} failed: {e}")
            if attempt == 0:
                _time.sleep(2)
    return ""


def _get_valid_session(user_id: str, context: str = "", model: str = "") -> Optional[str]:
    """Return a cached session ID if it exists, hasn't expired, and context/model matches."""
    cached = _session_cache.get(user_id)
    if not cached:
        return None

    age = time_module.time() - cached["created_at"]
    ctx_hash = _compute_context_hash(context)

    if age >= SESSION_TTL:
        print(f"   ADK session expired (age={age:.0f}s), creating new")
        _session_cache.pop(user_id, None)
        return None

    if cached["context_hash"] != ctx_hash:
        print(f"   ADK session context changed, creating new")
        _session_cache.pop(user_id, None)
        return None

    if cached.get("model", "") != model:
        print(f"   ADK session model changed ({cached.get('model', '')} -> {model}), creating new")
        _session_cache.pop(user_id, None)
        return None

    print(f"   ADK session reused: {cached['session_id']} (age={age:.0f}s)")
    return cached["session_id"]


def _cache_session(user_id: str, session_id: str, context: str = "", model: str = ""):
    """Store a session in the reuse cache."""
    _session_cache[user_id] = {
        "session_id": session_id,
        "created_at": time_module.time(),
        "context_hash": _compute_context_hash(context),
        "model": model,
    }


def query_agent(query: str, user_id: str = "default", context: str = "", model: str = "", memory_context: str = "") -> str:
    """
    Send a query to the ORA Navigator agent and return the final text response.

    Reuses ADK sessions when the user's attached context hasn't changed.
    Memory data is sent via state_delta (volatile, changes often).

    Args:
        query: The user's question
        user_id: Unique user identifier
        context: Attached context — account profile and/or uploaded file content
                 (injected into session state, stable across a session)
        model: Model preference ("inav-1.0" or "inav-1.1")
        memory_context: Long-term user memory (sent via state_delta, volatile)
    """
    # Session reuse: hash the attached context for invalidation
    session_id = _get_valid_session(user_id, context, model)

    if not session_id:
        state = {}
        if context:
            state["user_context"] = context
        if memory_context:
            state["memory"] = memory_context
        if model:
            state["model_preference"] = model
        session_id = _create_session(user_id, state=state if state else None)
        if not session_id:
            return _OUTAGE_MSG
        _cache_session(user_id, session_id, context, model)

    return _run_query(query, user_id, session_id, context=context, model=model, memory_context=memory_context)


# Per-request grounding metadata. In async single-worker (uvicorn default),
# requests are interleaved but not truly parallel, so a threading.local is
# sufficient to isolate grounding state between coroutines on different threads.
# For single-thread async, the value is set right before detect_and_log reads it
# within the same coroutine, so no race occurs.
import threading
_grounding_local = threading.local()

def _set_grounding(kb_grounded: bool, chunks: int, coverage: float, citations: Optional[list] = None):
    _grounding_local.data = {
        "kb_grounded": kb_grounded,
        "grounding_chunks": chunks,
        "grounding_coverage": coverage,
        "citations": citations or [],
    }


def _run_query(message: str, user_id: str, session_id: str, retried: bool = False, context: str = "", model: str = "", memory_context: str = "") -> str:
    """Send a query to the ADK and parse the SSE response.

    Fast in-memory retrieval runs BEFORE the ADK call (<5ms) to collect
    doc_texts for the post-agent VERIFICATION gate. The agent has its own
    VertexAiSearchTool for context; retrieval results here are only for
    verification.
    """
    _set_grounding(False, 0, 0.0)  # reset; a failed call must not leak stale citations
    # Build ADK payload (no retrieval context injected; agent searches KB itself)
    try:
        payload = {
            "app_name": ADK_APP_NAME,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {
                "role": "user",
                "parts": [{"text": message}],
            },
        }
        # Send volatile data via state_delta (memory changes often, model per-request)
        state_delta = {}
        if model:
            state_delta["model_preference"] = model
        if memory_context:
            state_delta["memory"] = memory_context
        if state_delta:
            payload["state_delta"] = state_delta

        resp = requests.post(
            f"{ADK_BASE_URL}/run_sse",
            headers=_get_auth_headers(),
            json=payload,
            stream=True,
            timeout=120,
        )

        # Handle "Session not found": recreate with context + memory state and retry once
        if resp.status_code == 404 and not retried:
            print(f"   ADK session {session_id} not found, creating a new one...")
            _session_cache.pop(user_id, None)
            state = {}
            if context:
                state["user_context"] = context
            if memory_context:
                state["memory"] = memory_context
            if model:
                state["model_preference"] = model
            new_session_id = _create_session(user_id, state=state if state else None)
            if new_session_id:
                _cache_session(user_id, new_session_id, context, model)
                return _run_query(message, user_id, new_session_id, retried=True, context=context, model=model, memory_context=memory_context)
            return _OUTAGE_MSG

        resp.raise_for_status()

        # Parse SSE events and extract the final text response + grounding metadata
        final_text = ""
        grounding_chunks = 0
        grounding_coverage = 0.0
        grounding_citations: list = []
        grounded_corpus = ""
        for line in resp.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8")
            if not decoded.startswith("data: "):
                continue

            json_str = decoded[6:]  # Strip "data: " prefix
            try:
                event = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            # Extract grounding metadata (tells us if KB search returned results)
            gm = event.get("groundingMetadata")
            if gm:
                chunks = gm.get("groundingChunks", [])
                supports = gm.get("groundingSupports", [])
                grounding_chunks = len(chunks)
                if chunks:
                    grounding_citations = _extract_citations(chunks)
                    grounded_corpus = _join_chunk_texts(chunks)
                # Coverage: what fraction of the response is grounded in KB results
                if supports and final_text:
                    total_chars = len(final_text)
                    grounded_chars = sum(
                        s.get("segment", {}).get("endIndex", 0) - s.get("segment", {}).get("startIndex", 0)
                        for s in supports
                    )
                    grounding_coverage = grounded_chars / total_chars if total_chars > 0 else 0.0
                elif chunks:
                    grounding_coverage = 0.5  # Has chunks but no segment info
                    print(f"   [GROUNDING] Chunks present ({grounding_chunks}) but no segment data - using conservative 0.5 coverage")

            # Extract text from model responses (skip function_call / function_response)
            content = event.get("content", {})
            if not isinstance(content, dict):
                continue

            role = content.get("role", "")
            if role != "model":
                continue

            parts = content.get("parts", [])
            for part in parts:
                if isinstance(part, dict) and "text" in part:
                    final_text = part["text"]  # Keep last model text (the final answer)

        # Store grounding signal for research_agent to read (thread-local)
        _set_grounding(grounding_chunks > 0, grounding_chunks, grounding_coverage, citations=grounding_citations)

        if final_text:
            # Clean up citation artifacts from Gemini grounding
            final_text = re.sub(r'\s*\[cite:\s*[^\]]*\]', '', final_text).strip()

            # Strip empty code blocks (Gemini 2.0 Flash sometimes returns ``` with nothing)
            if final_text.strip() in ("```", "``` ```", "``````"):
                final_text = "I wasn't able to generate a proper response. Please try asking again."

            # Catch 429 rate limit errors leaked into response
            if "429" in final_text and "RESOURCE_EXHAUSTED" in final_text:
                final_text = "The system is busy right now. Please try again in a moment."

            # Strip self-disclosure phrases (Gemini sometimes ignores instruction)
            final_text = re.sub(r'I am programmed to be a helpful[^.]*\.', 'I can only help with Morgan State University academic questions.', final_text)
            final_text = re.sub(r'I am still under development[^.]*\.', '', final_text).strip()
            final_text = re.sub(r'I am a language model[^.]*\.', '', final_text).strip()

            # Retry once if Gemini self-reported a KB access failure (transient Vertex AI Search issue)
            if _KB_FAIL_RE.search(final_text) and not retried:
                print("   [RETRY] Gemini reported KB access failure, retrying once...")
                time_module.sleep(2)
                return _run_query(message, user_id, session_id, retried=True, context=context, model=model, memory_context=memory_context)

            # Grounding validation gate: flag low-grounded responses
            has_data = bool(context)
            final_text = _apply_grounding_gate(final_text, grounding_chunks, coverage=grounding_coverage, has_attached_context=has_data)

            # Faithfulness gate: flag responses naming non-ORA-staff "Dr./Prof. X"
            hallucinated = _check_faculty_faithfulness(final_text)
            if hallucinated:
                print(f"   [FAITHFULNESS] Unverified staff names: {hallucinated}")
                if _FAITHFULNESS_DISCLAIMER not in final_text:
                    final_text = final_text + _FAITHFULNESS_DISCLAIMER

            # Identifier faithfulness gate: flag SOP/FWA/EIN/UEI numbers and rates
            # that don't appear in the retrieved KB text
            unverified_ids = _check_identifier_faithfulness(final_text, grounded_corpus)
            if unverified_ids:
                print(f"   [FAITHFULNESS] Unverified identifiers: {unverified_ids}")
                if _IDENTIFIER_DISCLAIMER not in final_text:
                    final_text = final_text + _IDENTIFIER_DISCLAIMER

            # Inject procedure guide Drive links if the agent omitted them
            final_text = _inject_procedure_links(final_text)

            return final_text
        else:
            return "I'm sorry, I couldn't generate a response. Please try rephrasing your question."

    except requests.exceptions.ConnectionError:
        print("   [OUTAGE] ADK server not reachable")
        return _OUTAGE_MSG
    except requests.exceptions.Timeout:
        print("   [OUTAGE] ADK query timed out after 120s")
        return "The request took too long. Please try a simpler question or try again in a moment."
    except Exception as e:
        error_str = str(e)
        if "403" in error_str or "Forbidden" in error_str:
            print(f"   [OUTAGE] ADK returned 403 Forbidden: {e}")
            return _OUTAGE_MSG
        elif "API key" in error_str:
            print(f"   [OUTAGE] ADK missing API key / Vertex AI config: {e}")
            return _OUTAGE_MSG
        print(f"   ADK query error: {e}")
        return "An error occurred while processing your question. Please try again."



def get_last_grounding() -> dict:
    """Return grounding metadata from the most recent query on this thread.
    Used by research_agent to determine if the KB actually had results.

    Returns:
        kb_grounded: True if Vertex AI Search returned any documents
        grounding_chunks: Number of KB documents cited
        grounding_coverage: Fraction of response text backed by KB sources (0.0-1.0)
    """
    return getattr(_grounding_local, "data", {"kb_grounded": True, "grounding_chunks": 0, "grounding_coverage": 1.0, "citations": []})


def check_agent_health() -> dict:
    """Check if the ADK agent server is healthy.
    On Cloud Run the ADK service is deployed --no-allow-unauthenticated,
    so we must attach a Bearer ID token (same pattern as query paths)."""
    try:
        resp = requests.get(f"{ADK_BASE_URL}/list-apps", headers=_get_auth_headers(), timeout=15)
        if resp.status_code == 200:
            apps = resp.json()
            has_navigator = any(
                ADK_APP_NAME in str(app) for app in (apps if isinstance(apps, list) else [apps])
            )
            return {
                "status": "connected",
                "message": f"ADK server running, app '{ADK_APP_NAME}' {'found' if has_navigator else 'not found'}",
            }
        return {"status": "error", "message": f"ADK server returned {resp.status_code}"}
    except requests.exceptions.ConnectionError:
        return {"status": "disconnected", "message": "ADK server not reachable"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:100]}


def reset_session(user_id: str) -> None:
    """Reset the ADK session for a user (forces new session on next query)."""
    _session_cache.pop(user_id, None)


def query_agent_stream(query: str, user_id: str = "default", context: str = "", model: str = "", memory_context: str = ""):
    """
    Send a query to the ORA Navigator agent and stream text chunks as they arrive.

    Session reuse based on the attached context (stable). Memory sent via state_delta (volatile).
    """
    # Session reuse: hash the attached context for invalidation
    session_id = _get_valid_session(user_id, context, model)

    if not session_id:
        state = {}
        if context:
            state["user_context"] = context
        if memory_context:
            state["memory"] = memory_context
        if model:
            state["model_preference"] = model
        session_id = _create_session(user_id, state=state if state else None)
        if not session_id:
            yield {"type": "error", "content": _OUTAGE_MSG}
            return
        _cache_session(user_id, session_id, context, model)

    yield from _run_query_stream(query, user_id, session_id, context=context, model=model, memory_context=memory_context)


def _run_query_stream(message: str, user_id: str, session_id: str, retried: bool = False, context: str = "", model: str = "", memory_context: str = ""):
    """Stream query results from ADK, yielding text chunks as they arrive.

    Fast in-memory retrieval runs BEFORE the ADK call (<5ms) to collect
    doc_texts for the post-stream VERIFICATION gate. The agent has its own
    VertexAiSearchTool for context.
    """
    _set_grounding(False, 0, 0.0)  # reset; a failed stream must not leak stale citations
    try:
        payload = {
            "app_name": ADK_APP_NAME,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {
                "role": "user",
                "parts": [{"text": message}],
            },
        }
        state_delta = {}
        if model:
            state_delta["model_preference"] = model
        if memory_context:
            state_delta["memory"] = memory_context
        if state_delta:
            payload["state_delta"] = state_delta

        resp = requests.post(
            f"{ADK_BASE_URL}/run_sse",
            headers=_get_auth_headers(),
            json=payload,
            stream=True,
            timeout=120,
        )

        # Handle "Session not found": recreate with context + memory state and retry once
        if resp.status_code == 404 and not retried:
            print(f"   ADK session {session_id} not found, creating a new one...")
            _session_cache.pop(user_id, None)
            state = {}
            if context:
                state["user_context"] = context
            if memory_context:
                state["memory"] = memory_context
            if model:
                state["model_preference"] = model
            new_session_id = _create_session(user_id, state=state if state else None)
            if new_session_id:
                _cache_session(user_id, new_session_id, context, model)
                yield from _run_query_stream(message, user_id, new_session_id, retried=True, context=context, model=model, memory_context=memory_context)
                return
            yield {"type": "error", "content": _OUTAGE_MSG}
            return

        resp.raise_for_status()

        # Map tool/agent names to user-friendly status messages
        TOOL_STATUS_MAP = {
            "vertex_ai_search": "Searching knowledge base",
            "discovery_engine_search": "Searching knowledge base",
        }

        # Stream SSE events and yield text chunks + status updates
        full_text = ""
        grounding_chunks = 0
        grounding_coverage = 0.0
        grounding_citations: list = []
        grounded_corpus = ""
        for line in resp.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8")
            if not decoded.startswith("data: "):
                continue

            json_str = decoded[6:]  # Strip "data: " prefix
            try:
                event = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            # Extract grounding metadata
            gm = event.get("groundingMetadata")
            if gm:
                chunks = gm.get("groundingChunks", [])
                supports = gm.get("groundingSupports", [])
                grounding_chunks = len(chunks)
                if chunks:
                    grounding_citations = _extract_citations(chunks)
                    grounded_corpus = _join_chunk_texts(chunks)
                if supports and full_text:
                    total_chars = len(full_text)
                    grounded_chars = sum(
                        s.get("segment", {}).get("endIndex", 0) - s.get("segment", {}).get("startIndex", 0)
                        for s in supports
                    )
                    grounding_coverage = grounded_chars / total_chars if total_chars > 0 else 0.0
                elif chunks:
                    grounding_coverage = 0.5
                    print(f"   [GROUNDING] Chunks present ({grounding_chunks}) but no segment data - using conservative 0.5 coverage")

            content = event.get("content", {})
            if not isinstance(content, dict):
                continue

            role = content.get("role", "")
            parts = content.get("parts", [])

            # Check for tool calls and yield status updates
            for part in parts:
                if isinstance(part, dict):
                    if "functionCall" in part:
                        func_name = part["functionCall"].get("name", "")
                        args = part["functionCall"].get("args", {})
                        if func_name == "transfer_to_agent":
                            agent_name = args.get("agent_name", "specialist")
                            status = TOOL_STATUS_MAP.get(agent_name, f"Consulting {agent_name.replace('_', ' ')}")
                        else:
                            status = TOOL_STATUS_MAP.get(func_name, f"Processing {func_name.replace('_', ' ')}")
                        yield {"type": "status", "content": status}

            # Extract text from model responses
            if role != "model":
                continue

            for part in parts:
                if isinstance(part, dict) and "text" in part:
                    text = part["text"]
                    text = re.sub(r'\s*\[cite:\s*[^\]]*\]', '', text)
                    if text.strip():
                        if len(text) > len(full_text):
                            chunk = text[len(full_text):]
                            full_text = text
                            yield {"type": "chunk", "content": chunk}
                        elif text != full_text:
                            full_text = text
                            yield {"type": "chunk", "content": text}

        # Store grounding signal for research_agent (thread-local)
        _set_grounding(grounding_chunks > 0, grounding_chunks, grounding_coverage, citations=grounding_citations)

        # If Gemini self-reported a KB access failure, send a clearer error
        # (can't retry in streaming mode since broken chunks are already sent to client)
        if _KB_FAIL_RE.search(full_text):
            print("   [KB_FAIL] Gemini reported KB access failure during stream")
            yield {"type": "error", "content": _OUTAGE_MSG}
            return

        # Post-process: catch 429 errors and empty code blocks in streamed text
        cleaned = full_text.strip()
        if cleaned in ("```", "``` ```", "``````", ""):
            cleaned = "I wasn't able to generate a proper response. Please try asking again."
            yield {"type": "chunk", "content": cleaned}
        if "429" in cleaned and "RESOURCE_EXHAUSTED" in cleaned:
            cleaned = "The system is busy right now. Please try again in a moment."
            yield {"type": "chunk", "content": cleaned}

        # Grounding validation gate: append disclaimer if low-grounded
        has_data = bool(context)
        final = _apply_grounding_gate(cleaned, grounding_chunks, coverage=grounding_coverage, has_attached_context=has_data)
        if final != cleaned:
            disclaimer = final[len(cleaned):]
            yield {"type": "chunk", "content": disclaimer}

        # Identifier faithfulness gate: flag unverified SOP/FWA/EIN/UEI numbers and rates
        unverified_ids = _check_identifier_faithfulness(final, grounded_corpus)
        if unverified_ids and _IDENTIFIER_DISCLAIMER not in final:
            print(f"   [FAITHFULNESS] Unverified identifiers (stream): {unverified_ids}")
            final = final + _IDENTIFIER_DISCLAIMER
            yield {"type": "chunk", "content": _IDENTIFIER_DISCLAIMER}

        # Inject procedure guide Drive links if the agent omitted them
        before_inject = final
        final = _inject_procedure_links(final)
        if final != before_inject:
            link_chunk = final[len(before_inject):]
            yield {"type": "chunk", "content": link_chunk}

        if grounding_citations:
            yield {"type": "citations", "content": grounding_citations}
        yield {"type": "done", "content": final}

    except requests.exceptions.ConnectionError:
        print("   [OUTAGE] ADK server not reachable (stream)")
        yield {"type": "error", "content": _OUTAGE_MSG}
    except requests.exceptions.Timeout:
        print("   [OUTAGE] ADK query timed out after 120s (stream)")
        yield {"type": "error", "content": "The request took too long. Please try a simpler question or try again in a moment."}
    except Exception as e:
        error_str = str(e)
        if "403" in error_str or "Forbidden" in error_str or "API key" in error_str:
            print(f"   [OUTAGE] ADK auth/config error (stream): {e}")
            yield {"type": "error", "content": _OUTAGE_MSG}
        else:
            print(f"   ADK stream error: {e}")
            yield {"type": "error", "content": "An error occurred while processing your question. Please try again."}
