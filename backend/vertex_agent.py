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
from services import gemini_client
# Retrieval is agent-first: the ADK agent's built-in VertexAiSearchTool handles
# KB search. Answer quality is handled here by Layer 3 (_run_verified -- grounding
# verification with regenerate-then-refuse) plus the faithfulness checks.

# Latency budget for a chat turn. When a turn has already spent this long before
# the Pass-2 regeneration step, skip the (expensive) second full agent pass and
# deliver Pass-1 with a caution note instead -- so a warm turn stays under the
# ~10s target rather than stacking a second model round-trip. Env-tunable.
_LATENCY_BUDGET_S = float(os.getenv("CHAT_LATENCY_BUDGET_S", "7.0"))


def _over_latency_budget(elapsed_s: float) -> bool:
    """True once a turn has spent its latency budget (deterministic predicate)."""
    return elapsed_s >= _LATENCY_BUDGET_S

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

# =============================================================================
# LAYER 3: GROUNDING VERIFICATION (regenerate-then-refuse)
# =============================================================================
# An answer is delivered as-is when >= _GROUNDING_MIN_COVERAGE of it is backed by
# retrieved KB text (a backend-computed signal -- the fraction of the answer's
# characters covered by Gemini's groundingSupports segments). Below that bar the
# answer is NOT thrown away wholesale: Layer 3 surgically rewrites ONLY the
# sentences that aren't source-backed (reusing the KB text already retrieved),
# and falls back to a full strict-KB regeneration only when the per-sentence span
# data is missing. See _evaluate_grounding() / _unsupported_sentences() /
# _surgical_reground() / _run_verified().
#
# _GROUNDING_MIN_CHUNKS is retained for logging/back-compat but is NO LONGER a
# pass condition -- Gemini's chunk count is unreliable (often empty even for a
# correct, grounded answer), so the gate is the coverage score alone.
_GROUNDING_MIN_CHUNKS = 2          # (legacy) count of cited KB docs; not a gate
_GROUNDING_MIN_COVERAGE = 0.5      # >= 50% of the answer backed by KB text -> deliver as-is
# A sentence counts as source-backed when a grounded span overlaps at least this
# fraction of its characters (or >= 15 chars), tolerating ragged Gemini spans.
_SENTENCE_GROUNDED_MIN_OVERLAP = 0.4

# Shown instead of an ungrounded answer when regeneration also fails.
_REFUSAL_MSG = (
    "I don't have reliable information on this in my knowledge base, so I'd "
    "rather not guess. Please contact ORA at 443-885-4044 or ask.ora@morgan.edu "
    "for accurate, up-to-date guidance."
)

# Streaming (Layer-3 fast path): hold back this many chars before emitting the
# first chunk, so a leaked error (429 / KB-access failure) is caught and NOT
# streamed. Appended when a streamed answer comes back weakly grounded (the
# streamed path can't regenerate, so it cautions instead).
_STREAM_GUARD_CHARS = 60
_WEAK_NOTE = (
    "\n\n_Heads up: I couldn't fully verify this against the ORA knowledge base — "
    "please confirm with ORA (443-885-4044) before relying on it._"
)

# Prepended to the user's question on the regeneration pass.
_STRICT_PREFIX = (
    "IMPORTANT: Answer strictly from the ORA knowledge base. The knowledge base "
    "includes BOTH the knowledge-base context already provided to you AND "
    "anything from your knowledge-base search tool -- treat both as the "
    "knowledge base. You MAY also use facts the user has explicitly stated "
    "about themselves earlier in this conversation (their department, role, "
    "active grant, deadlines, preferences) -- the chat history is not 'outside "
    "knowledge'. Do not use other outside or remembered knowledge. Answer the "
    "question fully and accurately from that knowledge base. Only if the "
    "knowledge base genuinely does not contain the answer, say you do not have "
    "that information and point the user to ORA. Never guess or approximate. "
    "Question: "
)

# An answer that is already an honest "I don't have this" / "not published".
# A low grounding score on these is correct, not a hallucination, so they are
# delivered as-is -- never regenerated or refused.
_HONEST_DEFLECTION_RE = re.compile(
    r"based on the information i have access to"
    r"|i (?:don'?t|do not|cannot|can'?t) (?:have|find|provide|locate|access)"
    r"|don'?t have reliable information"
    r"|not (?:published|yet available|currently available|listed)"
    r"|isn'?t (?:published|available|listed)"
    r"|no (?:published |specific )?information",
    re.IGNORECASE,
)

# Patterns that are inherently non-KB (greetings, security refusals, outages)
# These responses don't need KB grounding so skip the gate
_SKIP_GROUNDING_RE = re.compile(
    r'^(Hey!|Hello!|ORA Navigator was developed|I can only help with Morgan State|I\'m temporarily having trouble|You\'re welcome)',
    re.IGNORECASE,
)

# Detects when Gemini self-reports a KB access failure (transient Vertex AI Search issue)
_KB_FAIL_RE = re.compile(r"having trouble (accessing|connecting to) my knowledge base", re.IGNORECASE)

# Transient Vertex/Gemini quota errors (HTTP 429 / gRPC RESOURCE_EXHAUSTED) come
# back as response *text*, not exceptions, so they must be detected by substring.
# They are retried with backoff before being surfaced as an outage.
_RATE_LIMIT_BACKOFFS = (2.0, 4.0)  # per-retry sleeps; len() == number of retries

def _is_rate_limited(text: str) -> bool:
    return bool(text) and "429" in text and "RESOURCE_EXHAUSTED" in text

# Questions that ask the bot to recall something the user said about THEMSELVES
# earlier in the conversation (their department, role, sponsor, deadline...).
# Those facts live in the chat history, not the KB, so Layer 3 must NOT
# regenerate them under the strict KB-only prompt -- doing so discards the
# correct conversational-recall answer and replies "I don't have that info."
# Matched on the question; KB-fact questions ("What is the F&A rate?") must
# NOT match -- those still need full KB grounding.
_PERSONAL_RECALL_RE = re.compile(
    r"\b(?:"
    r"am\s+i\b"                                              # "am I", "what dept am I in"
    r"|did\s+i\b"                                            # "did I tell/say/mention"
    r"|remind\s+me\b"                                        # "remind me what I"
    r"|about\s+(?:me|myself)\b"                              # "remember about me"
    r"|(?:what|who|where|when)(?:'s|s|\s+is|\s+was|\s+are)?\s+my\b"  # "what's my", "what is my"
    r"|who\s+am\s+i\b"                                       # "who am I"
    r")",
    re.IGNORECASE,
)


def _is_personal_recall(question: str) -> bool:
    """True if the question asks the bot to recall something the user said
    about themselves earlier in the conversation. Such questions are answered
    from chat history, not the KB, so Layer 3 must skip strict regeneration."""
    if not question:
        return False
    return bool(_PERSONAL_RECALL_RE.search(question))

# Greetings / pleasantries / small talk. A friendly reply to these is correct and
# needs no KB grounding, so Layer 3 must NOT grade it "weak" and regenerate it
# under the strict KB prompt (which turns "I'm doing well!" into a refusal).
# Matched on the question and kept tight: only fires on a short message that is
# essentially just a greeting, so real ORA questions never match.
_SMALLTALK_RE = re.compile(
    r"^\W*(?:(?:"
    r"hi|hey+|hello|yo|sup|howdy|hiya|greetings"
    r"|good\s+(?:morning|afternoon|evening|day)"
    r"|how\s+(?:are|r)\s+(?:you|u|ya)(?:\s+doing)?|how'?s\s+it\s+going|how\s+you\s+doing"
    r"|what'?s\s+up|whats\s+up"
    r"|thanks?(?:\s+you)?|thank\s+you|thx|ty|cheers|appreciate\s+it"
    r"|ok(?:ay)?|cool|nice|great|awesome|got\s+it"
    r"|bye+|goodbye|see\s+(?:ya|you)|take\s+care"
    r"|today|now|there|friend|buddy"
    r")\b[\s,!.?'-]*)+$",
    re.IGNORECASE,
)

def _is_smalltalk(question: str) -> bool:
    """True if the message is just a greeting / pleasantry (no ORA content).
    Such messages get a warm, KB-free reply that Layer 3 must deliver as-is
    rather than regenerate under the strict prompt and refuse."""
    if not question:
        return False
    return bool(_SMALLTALK_RE.match(question.strip()))

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


def _evaluate_grounding(text: str, chunks: int, coverage: float, has_attached_context: bool) -> str:
    """Classify an answer's KB grounding as 'ok' or 'weak'.

    'weak' means the answer is not backed by the knowledge base and should be
    regenerated (and, if still weak, refused). 'ok' means deliver it.

    An answer is 'ok' when ANY of these hold:
      - it is a greeting / security / outage reply (no KB needed),
      - it is already an honest "I don't have this" deflection,
      - it was answered from attached context (uploaded file / profile),
      - >= _GROUNDING_MIN_COVERAGE of it is backed by retrieved KB text.

    NOTE: the chunk count is deliberately NOT a pass condition -- Gemini reports
    it unreliably. The single "% backed" coverage score is the gate. A 'weak'
    verdict triggers surgical per-sentence re-grounding in _run_verified, not an
    outright refusal.
    """
    if not text:
        return "weak"
    if _SKIP_GROUNDING_RE.match(text):
        return "ok"
    if _HONEST_DEFLECTION_RE.search(text):
        return "ok"
    if has_attached_context:
        return "ok"
    if coverage >= _GROUNDING_MIN_COVERAGE:
        return "ok"
    return "weak"


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


def _extract_citations(chunks: list, supports: Optional[list] = None) -> list:
    """Turn Gemini groundingChunks into a deduped list of {title, url} citations.
    Only chunks that resolve to a real URL are kept, so every source is clickable.
    Capped at 5.

    When `supports` (groundingSupports) is provided, the Sources list is limited
    to the chunks the answer ACTUALLY cited -- each support's
    groundingChunkIndices point at the chunks that back a given answer segment.
    This drops tangential docs the retriever pulled but the answer never used
    (e.g. a staff page that surfaced as a near-match for a training question).
    If no support data is present, we fall back to retrieval order so Sources is
    never empty."""
    url_map = _get_kb_url_map()

    def _build(chunk_list: list) -> list:
        out: list = []
        seen: set = set()
        for c in chunk_list:
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
                out.append({"title": title, "url": url})
            if len(out) >= 5:
                break
        return out

    # The chunks the answer actually cited (groundingSupports -> chunk indices).
    used: list = []
    if supports:
        seen_idx: set = set()
        for s in supports:
            if not isinstance(s, dict):
                continue
            for idx in (s.get("groundingChunkIndices") or []):
                if isinstance(idx, int) and idx not in seen_idx and 0 <= idx < len(chunks):
                    seen_idx.add(idx)
                    used.append(chunks[idx])

    # Prefer the cited subset; but if it yields no resolvable (clickable) source,
    # fall back to retrieval order so the Sources list is never blanked when the
    # retriever DID return usable docs.
    citations = _build(used) if used else []
    if not citations:
        citations = _build(chunks)
    return citations


# =============================================================================
# PART C: DETERMINISTIC CITATION FALLBACK
# =============================================================================
# Gemini's native VertexAiSearch grounding returns groundingChunks/Supports
# unreliably -- a correct, KB-grounded answer frequently comes back with empty
# metadata, so the Sources block is blank even though the answer IS from the KB.
# When that happens on a real ORA content answer, we run our own live KB search
# and attach the matching docs as Sources. This mirrors the precedent in
# _extract_citations (retrieval-order fallback when supports are absent),
# extended to the zero-chunk case. Anti-hallucination is unaffected: these are
# clickable Source links resolved from the KB URL map, not factual claims.

# The unified KB datastore the agent grounds on (same value in cloudbuild.yaml).
_FALLBACK_DATASTORE_ID = os.getenv(
    "VERTEX_AI_DATASTORE_ID",
    "projects/infra-vertex-494621-v1/locations/us/collections/default_collection"
    "/dataStores/oranavigator-kb-v8",
)


def _search_kb_titles(query: str, top_k: int = 5) -> list:
    """Live Vertex AI Search over the unified KB datastore; returns the top doc
    titles. Network boundary -- returns [] on any failure so the answer still
    delivers (golden rule 3)."""
    if not query or not query.strip():
        return []
    try:
        from google.cloud import discoveryengine_v1 as discoveryengine
        from google.api_core.client_options import ClientOptions
    except Exception:
        return []
    try:
        parts = _FALLBACK_DATASTORE_ID.split("/")
        location = parts[parts.index("locations") + 1] if "locations" in parts else "us"
        endpoint = f"{location}-discoveryengine.googleapis.com"
        client = discoveryengine.SearchServiceClient(
            client_options=ClientOptions(api_endpoint=endpoint)
        )
        serving_config = f"{_FALLBACK_DATASTORE_ID}/servingConfigs/default_search"
        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query,
            page_size=top_k,
        )
        titles: list = []
        for result in client.search(request):
            doc = getattr(result, "document", None)
            if doc is None:
                continue
            title = ""
            for field in ("struct_data", "derived_struct_data"):
                data = getattr(doc, field, None)
                if data and "title" in data:
                    title = (data.get("title") or "").strip()
                    if title:
                        break
            if title:
                titles.append(title)
            if len(titles) >= top_k:
                break
        return titles
    except Exception as e:
        print(f"   [FALLBACK_CITATIONS] KB search failed: {e}")
        return []


def _fallback_citations(query: str, top_k: int = 5) -> list:
    """Run a live KB search and resolve the matching doc titles to clickable
    {title, url} Sources via the KB URL map. Deduped, capped at 5, and only
    titles with a resolvable URL are kept (an unclickable Source is no Source).
    Returns [] when nothing matches -- never a blank guess."""
    try:
        titles = _search_kb_titles(query, top_k=top_k)
    except Exception:
        return []
    if not titles:
        return []
    url_map = _get_kb_url_map()
    out: list = []
    seen: set = set()
    for title in titles:
        key = _norm_title(title)
        if key in seen:
            continue
        url = url_map.get(key)
        if not url:
            continue
        seen.add(key)
        out.append({"title": title, "url": url})
        if len(out) >= 5:
            break
    return out


def _wants_fallback_citations(question: str, result: dict) -> bool:
    """True only when fallback Sources should be attached: a real ORA content
    answer that came back uncited. Skips small talk/greetings (no source), and
    any refusal/outage/KB-failure path ("I don't have that" has no source)."""
    if result.get("citations"):
        return False
    if result.get("kb_fail") or result.get("outage") or result.get("error"):
        return False
    if _is_smalltalk(question):
        return False
    return True


# =============================================================================
# IDENTIFIER FAITHFULNESS: SOP/FWA/EIN/UEI numbers and rates must be KB-grounded
# =============================================================================
_IDENTIFIER_PATTERNS = [
    ("SOP number", re.compile(r'\bSOP\s?#?\s?\d{1,3}\b', re.IGNORECASE)),
    ("FWA number", re.compile(r'\bFWA\s?#?\s?\d{6,10}\b', re.IGNORECASE)),
    ("EIN", re.compile(r'\b\d{2}-\d{7}\b')),
    ("UEI", re.compile(r'\bUEI[:\s#]+[A-Z0-9]{12}\b', re.IGNORECASE)),
    # Dates -- "March 15, 2026", "Mar 15", "3/15/2026", "2026-03-15"
    ("date", re.compile(
        r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
        r'Jul(?:y)?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|'
        r'Dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?\b',
        re.IGNORECASE,
    )),
    ("date", re.compile(r'\b\d{1,2}/\d{1,2}/\d{2,4}\b')),
    ("date", re.compile(r'\b\d{4}-\d{2}-\d{2}\b')),
    # Dollar amounts -- only flag amounts large enough to matter for grants.
    # Skips small "$5" / "$42" mentions to avoid false positives in policy text.
    ("dollar amount", re.compile(r'\$\d{1,3}(?:,\d{3})+(?:\.\d+)?')),       # $500,000
    ("dollar amount", re.compile(r'\$\d+(?:\.\d+)?\s?(?:thousand|million|billion|[KMB])\b', re.IGNORECASE)),  # $500K, $1.5M
    ("dollar amount", re.compile(r'\$\d{4,}(?:\.\d+)?\b')),                 # $1500+ no commas
    # Email addresses
    ("email", re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')),
    # Phone numbers (US-style, 10 digits with optional formatting)
    ("phone", re.compile(r'(?<!\d)\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)')),
    # Page / section numbers -- a common hallucination ("the details are on
    # page 36") when the KB entry only says "see PI Handbook 5".
    ("page/section number", re.compile(r'\b(?:page|section|pg\.?|p\.)\s?#?\s?\d{1,4}\b', re.IGNORECASE)),
    # File names -- the model sometimes invents an official-looking filename
    # ("FringeRate-2018.pdf") that does not exist in the KB.
    ("file name", re.compile(r'\b[\w-]{2,}\.(?:pdf|docx?|xlsx?|pptx?|csv)\b', re.IGNORECASE)),
]

# A bare 4-digit year ("approved in 2017") is too common to flag unconditionally,
# so -- like the rate check -- we only scan for years when the answer frames one
# as a policy date (approved / adopted / effective / established / ...).
_DATE_CONTEXT_RE = re.compile(
    r'\b(?:approv|adopt|effective|establish|enacted|issued|dated|revised|ratified|in effect)',
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')

# Negation cues: if one of these sits just before a flagged value, the bot is
# REFUTING/denying that value (e.g. "the rate is not 99%", "there is no SOP 37"),
# which is faithful behavior -- not a hallucination. This is the fix for the
# false positive that got the old disclaimer disabled.
_NEGATION_RE = re.compile(
    r"\b(?:not|isn'?t|aren'?t|wasn'?t|weren'?t|no longer|never|rather than|"
    r"instead of|incorrect|false|there\s+(?:is|are|was|were)\s+no|"
    r"do(?:es)?n'?t|don'?t|no\s+such)\b",
    re.IGNORECASE,
)


def _in_negation_context(text: str, start: int) -> bool:
    """True if a negation cue appears in the ~60 chars before position `start`,
    i.e. the answer is denying/refuting the value rather than asserting it."""
    window = text[max(0, start - 60):start]
    return bool(_NEGATION_RE.search(window))

# Identifiers that appear in the bot's canned routing / refusal messages and
# must never be flagged as hallucinations -- the bot is allowed to give the
# ORA main phone and email without KB grounding (they are the documented
# universal fallback contacts, hard-coded in _REFUSAL_MSG / _OUTAGE_MSG).
# Stored already-normalized (lowercased, single-spaced) to match _norm_for_match.
_IDENTIFIER_WHITELIST_NORM = {
    "443-885-4044",
    "(443) 885-4044",
    "(443)885-4044",
    "4438854044",
    "ask.ora@morgan.edu",
}

_RATE_KEYWORDS_RE = re.compile(r'F&A|facilities and administrative|indirect cost|fringe', re.IGNORECASE)
_PERCENT_RE = re.compile(r'\b\d{1,3}(?:\.\d+)?\s?%')

_IDENTIFIER_DISCLAIMER = (
    "\n\n*I couldn't verify the specific date/number/file referenced above against "
    "ORA's records — please confirm it with ORA (443-885-4044 / ask.ora@morgan.edu) "
    "before relying on it.*"
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
    positives. Soft guardrail — the caller appends a disclaimer, never blocks.

    Whitelisted contacts (ORA main phone / email baked into canned routing
    messages) are always allowed, since they ship with the bot regardless of
    what the KB returned. Each (label, normalized-token) pair is reported at
    most once so a repeated identifier doesn't fill the disclaimer."""
    if not text or not grounded_corpus or len(grounded_corpus) < 50:
        return []
    corpus = _norm_for_match(grounded_corpus)
    unverified = []
    seen: set[tuple[str, str]] = set()

    def _consider(label: str, token: str, start: int) -> None:
        norm = _norm_for_match(token)
        if not norm or norm in _IDENTIFIER_WHITELIST_NORM:
            return
        key = (label, norm)
        if key in seen:
            return
        seen.add(key)
        if norm in corpus:
            return
        # The bot is refuting/denying this value (e.g. "the rate is NOT 99%") --
        # faithful behavior, not a fabrication. Don't flag it.
        if _in_negation_context(text, start):
            return
        unverified.append(f"{label} '{token.strip()}'")

    for label, pat in _IDENTIFIER_PATTERNS:
        for m in pat.finditer(text):
            _consider(label, m.group(0), m.start())
    # Rates: only when the answer frames a number as an F&A/indirect/fringe rate
    if _RATE_KEYWORDS_RE.search(text):
        for m in _PERCENT_RE.finditer(text):
            _consider("rate", m.group(0), m.start())
    # Years: only when the answer frames one as a policy date (approved/effective)
    if _DATE_CONTEXT_RE.search(text):
        for m in _YEAR_RE.finditer(text):
            _consider("year", m.group(0), m.start())
    return unverified[:6]


# =============================================================================
# SURGICAL RE-GROUNDING: when an answer is < _GROUNDING_MIN_COVERAGE backed, fix
# ONLY the sentences that aren't source-backed (reusing the KB text already
# retrieved in Pass 1) instead of regenerating the whole answer with a fresh
# search. See the plan in _run_verified().
# =============================================================================

# Sentence splitter -- mirrors services.section_coach._sentences (kept local to
# avoid importing that heavier module into the chat hot-path).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Sentences that never need KB backing: contact/transition/filler boilerplate.
# (Greeting/outage and honest-deflection sentences are handled by the existing
# _SKIP_GROUNDING_RE / _HONEST_DEFLECTION_RE checks.)
_FILLER_SENTENCE_RE = re.compile(
    r"contact ora|443-885-4044|ask\.ora@morgan\.edu|let me know|feel free|"
    r"happy to help|hope (?:this|that) helps|in summary|to summarize|"
    r"here'?s|below (?:is|are)|please (?:reach out|confirm)",
    re.IGNORECASE,
)


def _split_sentences(text: str) -> list:
    """Split text into trimmed, non-empty sentences (mirror of _sentences)."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _sentence_is_exempt(sentence: str, is_personal_recall: bool) -> bool:
    """A sentence that never needs KB backing (greeting/deflection/filler, or any
    sentence when the question is personal recall answered from chat history)."""
    if is_personal_recall:
        return True
    if _SKIP_GROUNDING_RE.match(sentence):
        return True
    if _HONEST_DEFLECTION_RE.search(sentence):
        return True
    if _FILLER_SENTENCE_RE.search(sentence):
        return True
    return False


def _unsupported_sentences(raw_text: str, grounded_spans: list,
                           is_personal_recall: bool = False):
    """Return the answer's fact-stating sentences that are NOT KB-backed.

    Uses Gemini's grounded char-spans (the same data that drives coverage) to
    decide which sentences overlap a source-backed span. A sentence is backed
    when a span covers >= _SENTENCE_GROUNDED_MIN_OVERLAP of its chars (or >= 15
    chars). Exempt sentences (greeting/deflection/filler/personal-recall) are
    never flagged.

    Returns:
      - None  -> the span data is unusable (missing, or inconsistent with the
                 text -- e.g. UTF-8 byte-vs-char drift); caller should fall back
                 to the full strict-KB regeneration.
      - []    -> every fact-stating sentence is backed; deliver as-is.
      - [str] -> the verbatim unsupported sentences, in order.
    """
    if not raw_text or not grounded_spans:
        return None
    # Sanity guard: if any span index runs well past the text, the offsets are
    # not character offsets we can trust (Gemini sometimes returns UTF-8 byte
    # indices). Bail to the proven fallback rather than mis-map sentences.
    if max(e for _, e in grounded_spans) > len(raw_text) * 1.5:
        return None

    unsupported = []
    cursor = 0
    for sentence in _split_sentences(raw_text):
        idx = raw_text.find(sentence, cursor)
        if idx < 0:
            # Couldn't locate (cleaning/splitting drift) -- skip, don't guess.
            continue
        s_start, s_end = idx, idx + len(sentence)
        cursor = s_end
        if _sentence_is_exempt(sentence, is_personal_recall):
            continue
        # How many of this sentence's chars are covered by a grounded span?
        overlap = 0
        for sp_start, sp_end in grounded_spans:
            lo, hi = max(s_start, sp_start), min(s_end, sp_end)
            if hi > lo:
                overlap += hi - lo
        s_len = max(1, s_end - s_start)
        if overlap >= 15 or (overlap / s_len) >= _SENTENCE_GROUNDED_MIN_OVERLAP:
            continue
        unsupported.append(sentence)
    return unsupported


def _surgical_reground(message: str, full_answer: str, unsupported: list,
                       grounded_corpus: str):
    """Rewrite ONLY the unsupported sentences strictly from grounded_corpus (no
    new KB search). Each unsupportable sentence is replaced with a deterministic
    honest-gap line. Returns the merged answer, or None if Gemini is unavailable
    or nothing usable remains -> caller falls back to full regeneration/refusal.
    """
    if not unsupported or not grounded_corpus or len(grounded_corpus) < 50:
        return None

    numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(unsupported))
    sys_instruction = (
        "You correct sentences so each is strictly supported by the provided ORA "
        "knowledge-base passages. Never add a fact that is not in the passages. "
        "Never change a figure that is already correct. If a sentence cannot be "
        "supported by the passages, mark it supportable=false and give a short "
        "topic phrase (3-6 words) naming what it was about."
    )
    prompt = (
        f"QUESTION:\n{message}\n\n"
        f"KNOWLEDGE BASE PASSAGES:\n{grounded_corpus}\n\n"
        f"SENTENCES TO FIX (rewrite each strictly from the passages above):\n"
        f"{numbered}\n\n"
        "Return JSON exactly as: {\"rewrites\": [{\"original\": \"<the sentence "
        "verbatim>\", \"fixed\": \"<rewritten sentence, or empty string if "
        "unsupportable>\", \"supportable\": true|false, \"topic\": \"<short topic "
        "phrase>\"}]}. Include one entry per sentence, in order."
    )
    data = gemini_client.generate_json(
        prompt, temperature=0.0, max_output_tokens=1024,
        system_instruction=sys_instruction,
    )
    if not data or not isinstance(data.get("rewrites"), list):
        return None

    answer = full_answer
    answer_norm = _norm_for_match(answer)
    any_supported_kept = False
    gap_inserted = False

    for entry in data["rewrites"]:
        if not isinstance(entry, dict):
            continue
        original = (entry.get("original") or "").strip()
        fixed = (entry.get("fixed") or "").strip()
        supportable = bool(entry.get("supportable")) and bool(fixed)
        topic = (entry.get("topic") or "").strip()
        if not original:
            continue
        # Locate the original sentence with whitespace-collapsed matching so a
        # hard-wrapped answer still matches (golden rule 2). We need the actual
        # substring in `answer` to replace, so try a direct find first, then a
        # normalized fallback that maps back to the raw span.
        target = _locate_sentence(answer, answer_norm, original)
        if target is None:
            continue  # can't locate -> leave as-is, never blind-append
        if supportable:
            # Don't ship a rewrite that introduces a NEW unverified identifier.
            if _check_identifier_faithfulness(fixed, grounded_corpus):
                continue  # revert: keep the original sentence
            replacement = fixed
            any_supported_kept = True
        else:
            if gap_inserted:
                replacement = ""  # collapse repeated gaps into one
            else:
                replacement = (
                    f"I don't have source-backed details on "
                    f"{topic or 'that point'} — please confirm with ORA "
                    f"(443-885-4044)."
                )
                gap_inserted = True
        answer = answer.replace(target, replacement, 1)
        answer_norm = _norm_for_match(answer)

    # Tidy doubled spaces / blank lines left by dropped sentences.
    answer = re.sub(r"[ \t]{2,}", " ", answer)
    answer = re.sub(r"\n{3,}", "\n\n", answer).strip()

    # If nothing source-backed survived (answer is only gap/filler), refuse
    # cleanly rather than ship a wall of "I don't have..." lines.
    if not any_supported_kept and not _has_substantive_backed_content(
            answer, full_answer):
        return None
    return answer or None


def _locate_sentence(answer: str, answer_norm: str, sentence: str):
    """Return the exact substring of `answer` matching `sentence` (direct match
    first, then whitespace-collapsed), or None if it can't be located."""
    if sentence in answer:
        return sentence
    # Whitespace-collapsed fallback: find the normalized sentence in the
    # normalized answer, then walk the raw answer to recover the raw substring.
    s_norm = _norm_for_match(sentence)
    if not s_norm or s_norm not in answer_norm:
        return None
    # Recover the raw span by matching word-by-word against the raw answer.
    words = sentence.split()
    if not words:
        return None
    pattern = re.compile(r"\s+".join(re.escape(w) for w in words))
    m = pattern.search(answer)
    return m.group(0) if m else None


def _has_substantive_backed_content(answer: str, original: str) -> bool:
    """True if the merged answer still has a real (non-gap, non-filler) sentence."""
    for s in _split_sentences(answer):
        if "source-backed details on" in s.lower():
            continue
        if _sentence_is_exempt(s, False):
            continue
        return True
    return False


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


def _ensure_session(user_id: str, context: str = "", model: str = "", memory_context: str = "") -> str:
    """Return a valid ADK session id -- a reused one when context/model are
    unchanged, otherwise a freshly created one. Returns "" if creation fails."""
    session_id = _get_valid_session(user_id, context, model)
    if session_id:
        return session_id
    state = {}
    if context:
        state["user_context"] = context
    if memory_context:
        state["memory"] = memory_context
    if model:
        state["model_preference"] = model
    session_id = _create_session(user_id, state=state if state else None)
    if session_id:
        _cache_session(user_id, session_id, context, model)
    return session_id


def query_agent(query: str, user_id: str = "default", context: str = "", model: str = "", memory_context: str = "") -> str:
    """Send a query to the ORA Navigator agent and return the final text answer.

    Buffers the answer, verifies its KB grounding, regenerates once with a strict
    prompt if it is weak, and refuses rather than return an ungrounded answer
    (Layer 3). Used by the non-streaming /chat and /chat/guest endpoints.

    Args:
        query: The user's question
        user_id: Unique user identifier
        context: Attached context -- account profile and/or uploaded file content
        model: Model preference ("inav-1.0" / "inav-1.1" / "inav-2.0")
        memory_context: Long-term user memory (sent via state_delta, volatile)
    """
    session_id = _ensure_session(user_id, context, model, memory_context)
    if not session_id:
        return _OUTAGE_MSG

    final = "I'm sorry, I couldn't generate a response. Please try rephrasing your question."
    for event in _run_verified(query, user_id, session_id, context=context,
                               model=model, memory_context=memory_context):
        etype = event.get("type")
        if etype == "done":
            final = event.get("content") or final
        elif etype == "error":
            return event.get("content") or _OUTAGE_MSG
        # 'status' / 'citations' events: not needed here -- the non-streaming
        # callers read citations separately via get_last_grounding().
    return final


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


def _clean_answer_text(text: str) -> str:
    """Strip citation artifacts, empty code blocks, leaked rate-limit errors,
    and self-disclosure phrases from a raw agent answer."""
    if not text:
        return ""
    text = re.sub(r'\s*\[cite:\s*[^\]]*\]', '', text).strip()
    if text in ("```", "``` ```", "``````"):
        return "I wasn't able to generate a proper response. Please try asking again."
    if "429" in text and "RESOURCE_EXHAUSTED" in text:
        return "The system is busy right now. Please try again in a moment."
    text = re.sub(r'I am programmed to be a helpful[^.]*\.',
                  'I can only help with Morgan State University academic questions.', text)
    text = re.sub(r'I am still under development[^.]*\.', '', text).strip()
    text = re.sub(r'I am a language model[^.]*\.', '', text).strip()
    return text


def _finalize_answer(text: str, grounded_corpus: str) -> str:
    """Apply the soft faithfulness guardrails + procedure links to a *delivered*
    answer (one that already cleared the grounding check)."""
    hallucinated = _check_faculty_faithfulness(text)
    if hallucinated and _FAITHFULNESS_DISCLAIMER not in text:
        print(f"   [FAITHFULNESS] Unverified staff names: {hallucinated}")
        text = text + _FAITHFULNESS_DISCLAIMER
    # Identifier check: we still RUN it (and log) for monitoring, but the
    # user-facing footer is DISABLED (2026-06-10, at the user's request). The
    # verbatim check structurally false-positives whenever a correct answer is
    # richer than the single retrieved passage -- e.g. an F&A answer that also
    # cites the prior fiscal year's rate, which isn't in the current-year chunk.
    # That cautionary footer was undermining trust in answers that were actually
    # correct, so it no longer ships. Correctness is still guarded upstream by
    # the grounding gate + KB-only strict regeneration; the staff-name
    # faithfulness disclaimer above stays active.
    unverified_ids = _check_identifier_faithfulness(text, grounded_corpus)
    if unverified_ids:
        print(f"   [FAITHFULNESS] Unverified identifiers (footer disabled): {unverified_ids}")
    return _inject_procedure_links(text)


def _do_agent_pass(message: str, user_id: str, session_id: str, context: str = "",
                   model: str = "", memory_context: str = "", retried: bool = False,
                   rate_limit_attempt: int = 0, stream: bool = False):
    """One round-trip to the ADK agent.

    A generator: yields {"type": "status", ...} events as the agent calls tools,
    then finally yields exactly one {"type": "_result", "data": {...}} event.

    When ``stream`` is True, also yields {"type": "chunk", "content": <delta>}
    events as the model's answer text grows -- BUT only after a short prefix
    guard confirms the text isn't a leaked error (429 / KB-access failure), so an
    error is never streamed to the user. ``result["streamed"]`` records whether
    any chunk was emitted.

    The result dict has: text, chunks, coverage, citations, grounded_corpus,
    kb_fail (bool), outage (bool), error (str or None). It is the *raw* pass --
    no grounding verdict and no faithfulness gates are applied here.
    """
    result = {
        "text": "", "chunks": 0, "coverage": 0.0, "citations": [],
        "grounded_corpus": "", "kb_fail": False, "outage": False, "error": None,
        # (startIndex, endIndex) char-spans of the answer that Gemini's
        # groundingSupports marked as KB-backed -- used for surgical per-sentence
        # re-grounding. Indices reference the RAW answer text (result["text"]).
        "grounded_spans": [],
    }
    try:
        payload = {
            "app_name": ADK_APP_NAME,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": message}]},
        }
        # Volatile data via state_delta (memory changes often, model per-request)
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

        # "Session not found": recreate the session and retry the pass once.
        if resp.status_code == 404 and not retried:
            print(f"   ADK session {session_id} not found, creating a new one...")
            _session_cache.pop(user_id, None)
            new_session_id = _ensure_session(user_id, context, model, memory_context)
            if new_session_id:
                yield from _do_agent_pass(message, user_id, new_session_id, context,
                                          model, memory_context, retried=True, stream=stream)
                return
            result["outage"] = True
            yield {"type": "_result", "data": result}
            return

        resp.raise_for_status()

        # Map tool/agent names to user-friendly status messages
        TOOL_STATUS_MAP = {
            "vertex_ai_search": "Searching knowledge base",
            "discovery_engine_search": "Searching knowledge base",
        }

        final_text = ""
        _stream_started = False     # have we begun emitting chunks?
        _stream_suppressed = False  # did the prefix look like a leaked error?
        _emitted = 0                # chars already streamed
        for line in resp.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8")
            if not decoded.startswith("data: "):
                continue
            try:
                event = json.loads(decoded[6:])  # strip "data: " prefix
            except json.JSONDecodeError:
                continue

            # Grounding metadata (how many KB docs, how much coverage)
            gm = event.get("groundingMetadata")
            if gm:
                chunks = gm.get("groundingChunks", [])
                supports = gm.get("groundingSupports", [])
                result["chunks"] = len(chunks)
                if chunks:
                    result["citations"] = _extract_citations(chunks, supports)
                    result["grounded_corpus"] = _join_chunk_texts(chunks)
                if supports and final_text:
                    total_chars = len(final_text)
                    spans = []
                    for s in supports:
                        seg = s.get("segment", {})
                        start = seg.get("startIndex", 0)
                        end = seg.get("endIndex", 0)
                        if 0 <= start < end <= total_chars:
                            spans.append((start, end))
                    result["grounded_spans"] = spans
                    grounded_chars = sum(e - st for st, e in spans)
                    result["coverage"] = grounded_chars / total_chars if total_chars > 0 else 0.0
                elif chunks:
                    result["coverage"] = 0.5  # chunks present but no segment data
                    print(f"   [GROUNDING] Chunks present ({result['chunks']}) but no segment data - using conservative 0.5 coverage")

            content = event.get("content", {})
            if not isinstance(content, dict):
                continue
            role = content.get("role", "")
            parts = content.get("parts", [])

            # Tool calls -> user-facing status updates
            for part in parts:
                if isinstance(part, dict) and "functionCall" in part:
                    func_name = part["functionCall"].get("name", "")
                    args = part["functionCall"].get("args", {})
                    if func_name == "transfer_to_agent":
                        agent_name = args.get("agent_name", "specialist")
                        status = TOOL_STATUS_MAP.get(agent_name, f"Consulting {agent_name.replace('_', ' ')}")
                    else:
                        status = TOOL_STATUS_MAP.get(func_name, f"Processing {func_name.replace('_', ' ')}")
                    yield {"type": "status", "content": status}

            # Keep the last model text part (the final answer). The ADK sends the
            # text cumulatively (each event carries the full text so far).
            if role == "model":
                for part in parts:
                    if isinstance(part, dict) and "text" in part and part["text"].strip():
                        final_text = part["text"]
                        if stream and not _stream_suppressed:
                            if not _stream_started:
                                # Wait until we have enough text to tell a real
                                # answer from a leaked error before showing anything.
                                if len(final_text) < _STREAM_GUARD_CHARS:
                                    continue
                                if ("resource_exhausted" in final_text.lower()
                                        or _KB_FAIL_RE.search(final_text)):
                                    _stream_suppressed = True   # let post-loop handling deal with it
                                    continue
                                _stream_started = True
                                yield {"type": "chunk", "content": final_text}
                                _emitted = len(final_text)
                            else:
                                delta = final_text[_emitted:]
                                if delta:
                                    yield {"type": "chunk", "content": delta}
                                    _emitted = len(final_text)

        # Retry once if Gemini self-reported a (transient) KB access failure
        if _KB_FAIL_RE.search(final_text) and not retried:
            print("   [RETRY] Gemini reported KB access failure, retrying once...")
            time_module.sleep(2)
            yield from _do_agent_pass(message, user_id, session_id, context,
                                      model, memory_context, retried=True, stream=stream)
            return

        # Retry with backoff on a transient Vertex/Gemini quota error. It arrives
        # as response *text*, so it bypasses the except handlers below; without
        # this it would be laundered into the "system is busy" string by
        # _clean_answer_text AND grade as "weak", triggering a Pass-2
        # regeneration that doubles the load on an already-throttled backend. We
        # absorb transient blips here; if still throttled after the bounded
        # retries, flag an outage so _run_verified short-circuits cleanly with
        # the standard fallback instead of amplifying the problem with Pass 2.
        if _is_rate_limited(final_text):
            raw = final_text.strip()[:300]
            if rate_limit_attempt < len(_RATE_LIMIT_BACKOFFS):
                delay = _RATE_LIMIT_BACKOFFS[rate_limit_attempt]
                print(f"   [RATE-LIMIT] Vertex/Gemini quota hit (attempt "
                      f"{rate_limit_attempt + 1}); retrying in {delay}s. Raw: {raw}")
                time_module.sleep(delay)
                yield from _do_agent_pass(message, user_id, session_id, context,
                                          model, memory_context, retried=retried,
                                          rate_limit_attempt=rate_limit_attempt + 1, stream=stream)
                return
            print(f"   [RATE-LIMIT] Still throttled after {len(_RATE_LIMIT_BACKOFFS)} "
                  f"retries; surfacing outage. Raw: {raw}")
            result["outage"] = True
            yield {"type": "_result", "data": result}
            return

        result["text"] = final_text
        result["kb_fail"] = bool(_KB_FAIL_RE.search(final_text))
        result["streamed"] = _stream_started
        yield {"type": "_result", "data": result}

    except requests.exceptions.ConnectionError:
        print("   [OUTAGE] ADK server not reachable")
        result["outage"] = True
        yield {"type": "_result", "data": result}
    except requests.exceptions.Timeout:
        print("   [OUTAGE] ADK query timed out after 120s")
        result["error"] = "The request took too long. Please try a simpler question or try again in a moment."
        yield {"type": "_result", "data": result}
    except Exception as e:
        error_str = str(e)
        if "403" in error_str or "Forbidden" in error_str or "API key" in error_str:
            print(f"   [OUTAGE] ADK auth/config error: {e}")
            result["outage"] = True
        else:
            print(f"   ADK pass error: {e}")
            result["error"] = "An error occurred while processing your question. Please try again."
        yield {"type": "_result", "data": result}


def _run_verified(message: str, user_id: str, session_id: str, context: str = "",
                  model: str = "", memory_context: str = ""):
    """Layer 3 orchestrator: run the agent, verify the answer is grounded in the
    KB, regenerate once with a strict prompt if it is weak, and refuse rather
    than deliver an ungrounded answer.

    A generator: yields {"type": "status", ...} progress events, then exactly one
    terminal event -- {"type": "done", "content": <answer>} or
    {"type": "error", "content": <message>} -- optionally preceded by a
    {"type": "citations", "content": [...]} event.

    Both /chat (via query_agent) and /chat/stream (via query_agent_stream)
    consume this. The buffered answer is delivered only after verification, so an
    ungrounded answer is never shown to the user.
    """
    _t0 = time_module.time()
    _set_grounding(False, 0, 0.0)  # reset; a failed run must not leak stale citations
    yield {"type": "status", "content": "Searching knowledge base"}

    # ---- PASS 1 ----------------------------------------------------------
    result = None
    for ev in _do_agent_pass(message, user_id, session_id, context, model, memory_context):
        if ev["type"] == "_result":
            result = ev["data"]
        else:
            yield ev

    if result is None or result.get("outage") or result.get("kb_fail"):
        yield {"type": "error", "content": _OUTAGE_MSG}
        return
    if result.get("error"):
        yield {"type": "error", "content": result["error"]}
        return

    text = _clean_answer_text(result["text"])
    if not text:
        # Empty text but KB chunks exist: the model called the search tool
        # (finding real KB docs) but failed to synthesize an answer -- common
        # for vague or typo'd queries like "also abou the preawards" that
        # confuse the model but not the search index. Fall through to Pass 2
        # so the strict regeneration can take a second shot using the KB
        # context that's already in hand. Zero chunks is a real model
        # failure -- surface the error.
        if result["chunks"] == 0:
            # The model produced no text AND found no KB chunks. Rather than a
            # dead-end "try rephrasing" error (which is what a user asking to
            # confirm a non-existent SOP/identifier used to hit), degrade to the
            # honest refusal so the reply is always useful and routes to ORA.
            yield {"type": "done", "content": _REFUSAL_MSG}
            return
        # Force a Pass-2 regeneration by marking the verdict as weak.
        has_data = bool(context)
        verdict = "weak"
    else:
        has_data = bool(context)
        verdict = _evaluate_grounding(text, result["chunks"], result["coverage"], has_data)

    # Personal-recall questions ("what department am I in?", "what's my
    # deadline?") are answered from the chat history, not the KB. The strict
    # regeneration discards the recall answer and refuses, so short-circuit
    # the gate -- deliver Pass 1 as-is even with zero KB grounding.
    if verdict == "weak" and _is_personal_recall(message):
        verdict = "ok"
    # Greetings / small talk get a warm KB-free reply -- never regenerate it.
    if verdict == "weak" and _is_smalltalk(message):
        verdict = "ok"

    # ---- SURGICAL RE-GROUNDING: fix only the unsupported sentences ----------
    # Reuse Pass-1's already-retrieved KB text to rewrite just the sentences that
    # aren't source-backed, instead of throwing the whole answer away and running
    # a second KB search. Falls through to the full Pass-2 regeneration below
    # when the per-sentence span data is missing/unusable or the surgical rewrite
    # can't produce a usable answer.
    if verdict == "weak":
        unsupported = _unsupported_sentences(
            result["text"], result.get("grounded_spans") or [],
            _is_personal_recall(message),
        )
        if unsupported == []:
            verdict = "ok"  # every fact-stating sentence was actually backed
        elif unsupported:  # non-empty -> attempt the surgical rewrite
            yield {"type": "status", "content": "Checking sources sentence by sentence"}
            merged = _surgical_reground(message, result["text"], unsupported,
                                        result["grounded_corpus"])
            if merged:
                text, verdict = _clean_answer_text(merged), "ok"
                print(f"   [LAYER3] Surgically re-grounded {len(unsupported)} "
                      f"sentence(s) ({result['coverage']:.0%} coverage) - no 2nd search")
        # unsupported is None -> spans unusable; leave verdict 'weak' for Pass 2.

    # ---- LATENCY GUARD: skip the expensive Pass-2 when over budget -------
    # If we already have a usable Pass-1 answer but it graded weak, and the turn
    # has spent its latency budget, deliver Pass-1 with a caution note instead of
    # paying for a full second agent round-trip. (Empty Pass-1 text still
    # regenerates -- there's nothing to deliver otherwise.)
    if verdict == "weak" and text and _over_latency_budget(time_module.time() - _t0):
        print(f"   [LAYER3] over latency budget ({_LATENCY_BUDGET_S}s) - "
              f"delivering Pass-1 with caution, skipping Pass-2")
        text = text + _WEAK_NOTE
        verdict = "ok"

    # ---- PASS 2: regenerate when the first answer is weakly grounded -----
    if verdict == "weak":
        print(f"   [LAYER3] Grounding unverified ({result['chunks']} chunks, "
              f"{result['coverage']:.0%} coverage) - regenerating with a strict prompt")
        yield {"type": "status", "content": "Double-checking sources"}

        reset_session(user_id)  # fresh slate so the weak answer is not in context
        regen_session = _ensure_session(user_id, context, model, memory_context)
        if regen_session:
            result2 = None
            for ev in _do_agent_pass(_STRICT_PREFIX + message, user_id, regen_session,
                                     context, model, memory_context):
                if ev["type"] == "_result":
                    result2 = ev["data"]
                else:
                    yield ev
            if (result2 and not result2.get("outage") and not result2.get("error")
                    and not result2.get("kb_fail")):
                text2 = _clean_answer_text(result2["text"])
                # The strict regeneration instructed the model to answer ONLY
                # from the KB or else reply with an explicit "I don't have
                # reliable information" deflection. Trust that outcome: a
                # non-empty answer is either KB-grounded or the model's own
                # honest deflection -- deliver it either way. We deliberately do
                # NOT re-gate on the groundingChunks count here: Gemini returns
                # that metadata unreliably (often empty even for a correct,
                # grounded answer), and gating on it is what made Layer 3 refuse
                # good answers.
                if text2:
                    result, text, verdict = result2, text2, "ok"

        # ---- REFUSE: regeneration produced no usable answer -------------
        if verdict == "weak":
            print("   [LAYER3] Regeneration produced no usable answer - refusing")
            _set_grounding(False, 0, 0.0, [])
            yield {"type": "done", "content": _REFUSAL_MSG}
            return

    # ---- DELIVER ---------------------------------------------------------
    # Part C: a correct ORA answer can come back uncited (Gemini's grounding
    # metadata is unreliable). Attach Sources from a live KB search so the
    # answer is never sourceless.
    if _wants_fallback_citations(message, result):
        result["citations"] = _fallback_citations(message)
    print(f"   [LATENCY] chat turn {(time_module.time() - _t0) * 1000:.0f}ms "
          f"(verdict={verdict}, chunks={result['chunks']})")
    _set_grounding(result["chunks"] > 0, result["chunks"], result["coverage"],
                   citations=result["citations"])
    final = _finalize_answer(text, result["grounded_corpus"])
    if result["citations"]:
        yield {"type": "citations", "content": result["citations"]}
    yield {"type": "done", "content": final}


def _run_verified_stream(message: str, user_id: str, session_id: str, context: str = "",
                         model: str = "", memory_context: str = ""):
    """Streaming Layer-3 (the fast path for /chat/stream).

    Streams Pass-1's answer token-by-token as a live preview -- but a prefix guard
    in _do_agent_pass keeps a leaked 429 / KB-access error from ever being shown.
    After the answer completes it runs the SAME grounding check, then sends the
    authoritative finalized answer in the terminal 'done' event (which the UI uses
    to replace the preview -- so faithfulness links/disclaimers still apply).

    Difference from _run_verified: it does NOT regenerate a weakly grounded answer
    (it's already on screen) -- it appends an honest caution note instead. Errors,
    outages, and empty/zero-chunk answers are never streamed (guarded), so those
    still degrade to the outage/refusal messages exactly as before.
    """
    _t0 = time_module.time()
    _set_grounding(False, 0, 0.0)
    yield {"type": "status", "content": "Searching knowledge base"}

    result = None
    for ev in _do_agent_pass(message, user_id, session_id, context, model,
                             memory_context, stream=True):
        if ev["type"] == "_result":
            result = ev["data"]
        else:
            yield ev   # status + chunk events pass straight through to the client

    if result is None or result.get("outage") or result.get("kb_fail"):
        yield {"type": "error", "content": _OUTAGE_MSG}
        return
    if result.get("error"):
        yield {"type": "error", "content": result["error"]}
        return

    text = _clean_answer_text(result["text"])
    if not text:
        # Empty answer -> nothing was streamed; refuse honestly (routes to ORA).
        yield {"type": "done", "content": _REFUSAL_MSG}
        return

    verdict = _evaluate_grounding(text, result["chunks"], result["coverage"], bool(context))
    if verdict == "weak" and _is_personal_recall(message):
        verdict = "ok"
    if verdict == "weak" and _is_smalltalk(message):
        verdict = "ok"

    # Surgical re-grounding: the streamed preview is replaced by the terminal
    # 'done' event, so we can rewrite just the unsupported sentences here too
    # (rather than only cautioning). When span data is missing or the rewrite
    # fails we fall back to the caution note -- we can't re-search mid-stream.
    if verdict == "weak":
        unsupported = _unsupported_sentences(
            result["text"], result.get("grounded_spans") or [],
            _is_personal_recall(message),
        )
        if unsupported == []:
            verdict = "ok"
        elif unsupported:
            merged = _surgical_reground(message, result["text"], unsupported,
                                        result["grounded_corpus"])
            if merged:
                text, verdict = _clean_answer_text(merged), "ok"

    # Part C: attach Sources from a live KB search when a TRUSTED answer came
    # back uncited. Skipped for weak answers -- we don't lend authority to one
    # already flagged with a caution note.
    if verdict != "weak" and _wants_fallback_citations(message, result):
        result["citations"] = _fallback_citations(message)
    print(f"   [LATENCY] chat turn (stream) {(time_module.time() - _t0) * 1000:.0f}ms "
          f"(verdict={verdict}, chunks={result['chunks']})")
    _set_grounding(result["chunks"] > 0, result["chunks"], result["coverage"],
                   citations=result["citations"])
    final = _finalize_answer(text, result["grounded_corpus"])
    if verdict == "weak":
        final = final + _WEAK_NOTE   # spans unusable / surgical failed -> caution
    if result["citations"]:
        yield {"type": "citations", "content": result["citations"]}
    yield {"type": "done", "content": final}


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
    """Send a query to the ORA Navigator agent and yield SSE-style events.

    Layer 3 (streaming fast path): the answer is streamed token-by-token as
    'chunk' events for low perceived latency, with a prefix guard that prevents a
    leaked 429 / KB-access error from being shown. After it completes, grounding
    is checked and the authoritative finalized answer is sent as the terminal
    'done' event (which replaces the preview). A weakly grounded streamed answer
    gets an honest caution note appended rather than a hidden regeneration.
    Errors / outages / empty answers are never streamed.

    Yields dicts: {"type": "status" | "chunk" | "citations" | "done" | "error", "content": ...}.
    """
    session_id = _ensure_session(user_id, context, model, memory_context)
    if not session_id:
        yield {"type": "error", "content": _OUTAGE_MSG}
        return
    yield from _run_verified_stream(query, user_id, session_id, context=context,
                                    model=model, memory_context=memory_context)
