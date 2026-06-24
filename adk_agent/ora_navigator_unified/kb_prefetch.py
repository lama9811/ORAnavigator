"""
KB Prefetch - Belt-and-Suspenders Grounding
============================================
Pre-fetches KB docs and searches them with TF-IDF-ish scoring so we can
inject relevant context into the system instruction via before_model_callback.

Even if Gemini skips the VertexAiSearchTool, the KB docs are already
in the prompt. Typical latency: <30ms for 382 docs (ORA corpus).

ORA tuning:
- Entity boost recognizes ORA acronyms (IRB, IACUC, COI, NCE, F&A, FWA, ...)
  and SOP numbers (e.g. "SOP 12") instead of CS course codes.
- Doc-count health warning emitted on cache load (see MIN_DOC_THRESHOLD).
"""

import os
import re
import time
import threading
import logging
from collections import Counter

from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core.client_options import ClientOptions

log = logging.getLogger(__name__)

# Config
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "infra-vertex-494621-v1")
DATASTORE_ID = os.getenv("KB_PREFETCH_DATASTORE_ID", "oranavigator-kb-v8")
LOCATION = "us"
# Recall degrades sharply below ~20 docs (TF-IDF vocabulary distribution shift).
# Current ORA corpus is 382 — well above. Warning fires on shrinkage.
MIN_DOC_THRESHOLD = 20
API_ENDPOINT = f"{LOCATION}-discoveryengine.googleapis.com"
BRANCH = (
    f"projects/{GCP_PROJECT}/locations/{LOCATION}/collections/default_collection"
    f"/dataStores/{DATASTORE_ID}/branches/default_branch"
)

# In-memory cache (same pattern as datastore_manager.py)
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
_cache_ts: float = 0
_CACHE_TTL = 300  # 5 min

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "i", "me", "my", "we",
    "our", "you", "your", "he", "she", "it", "they", "them", "this",
    "that", "what", "which", "who", "whom", "how", "when", "where",
    "why", "at", "by", "for", "from", "in", "of", "on", "to", "with",
    "and", "or", "but", "not", "if", "about", "up", "out", "so",
    "no", "just", "also", "than", "very", "too", "any", "each",
    "need", "get", "take", "make", "know", "want", "tell",
}


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"\b[a-z]{2,}\b", text.lower())
    return [w for w in words if w not in _STOPWORDS]


_bg_loading = False

def _load_cache_sync():
    """Fetch all docs from Discovery Engine into memory."""
    global _cache_ts, _bg_loading
    client = discoveryengine.DocumentServiceClient(
        client_options=ClientOptions(api_endpoint=API_ENDPOINT)
    )
    new_cache = {}
    try:
        req = discoveryengine.ListDocumentsRequest(parent=BRANCH, page_size=200)
        for doc in client.list_documents(request=req):
            doc_id = doc.name.split("/")[-1]
            data = dict(doc.struct_data) if doc.struct_data else {}
            if doc.content and doc.content.raw_bytes:
                data["content"] = doc.content.raw_bytes.decode("utf-8")
            new_cache[doc_id] = data
    except Exception as e:
        log.warning(f"[KB_PREFETCH] Failed to load docs: {e}")
        _bg_loading = False
        return

    with _cache_lock:
        _cache.clear()
        _cache.update(new_cache)
        _cache_ts = time.time()
    _bg_loading = False
    n = len(new_cache)
    if n < MIN_DOC_THRESHOLD:
        log.warning(
            f"[KB_PREFETCH] Only {n} docs cached (threshold {MIN_DOC_THRESHOLD}). "
            "TF-IDF recall may degrade. Verify datastore "
            f"{DATASTORE_ID!r} in project {GCP_PROJECT!r} is fully indexed."
        )
    else:
        log.info(f"[KB_PREFETCH] Cached {n} docs (datastore {DATASTORE_ID!r})")


def _load_cache() -> dict[str, dict]:
    """Return cached docs. If cache is cold, trigger background load and return empty.
    This ensures the first request is never blocked by the cache warm-up."""
    global _bg_loading
    now = time.time()
    with _cache_lock:
        if _cache and now - _cache_ts < _CACHE_TTL:
            return dict(_cache)

    # Cache is cold. Don't block the request. Load in background.
    if not _bg_loading:
        _bg_loading = True
        t = threading.Thread(target=_load_cache_sync, daemon=True)
        t.start()
        log.info("[KB_PREFETCH] Cache cold, loading in background...")

    # Return whatever we have (empty on first call, stale data on refresh)
    with _cache_lock:
        return dict(_cache)


def warm_cache() -> None:
    """Trigger the KB doc-cache load at startup (non-blocking).

    Without this, the cache loads lazily on the first request -- and
    prefetch_kb_context() returns nothing until that background load finishes,
    so the first user after a cold start gets no prefetched grounding context.
    Calling this at agent startup closes that gap.
    """
    _load_cache()


_ORA_ACRONYM_RE = re.compile(
    r"\b(IRB|IACUC|IBC|COI|RCR|D[- ]?RED|NCE|F&A|FWA\d{0,12}|UEI|EIN|"
    r"RACC|PCard|IDC|HHS|NIH|NSF|USDA|OLAW|OHRP|TCP|OVPRED)\b",
    re.IGNORECASE,
)
_ORA_SOP_RE = re.compile(r"\bSOP\s*0*(\d{1,3})\b", re.IGNORECASE)


def _extract_entities(query: str) -> list[str]:
    """Pull ORA-relevant tokens that survive case/spacing and signal a topic strongly.
    Tokens are returned lowercased; matching is done lowercased against doc text."""
    out: list[str] = []
    for m in _ORA_ACRONYM_RE.findall(query):
        out.append(m.lower().replace(" ", "-"))
    for n in _ORA_SOP_RE.findall(query):
        out.append(f"sop {int(n)}")
        out.append(f"sop{int(n)}")
    return out


def prefetch_kb_context(query: str, top_k: int = 5) -> str:
    """Search cached KB docs with TF-IDF scoring, return formatted context."""
    docs = _load_cache()
    if not docs:
        return ""

    query_tokens = _tokenize(query)
    entities = _extract_entities(query)

    if not query_tokens and not entities:
        return ""

    query_counter = Counter(query_tokens)
    scored = []

    for doc_id, data in docs.items():
        content = data.get("content", "")
        title = data.get("title", "")
        searchable = f"{title} {content}".lower()
        score = 0.0

        for ent in entities:
            if ent.lower() in searchable:
                score += 10.0

        doc_tokens = _tokenize(searchable)
        doc_counter = Counter(doc_tokens)
        doc_len = len(doc_tokens) or 1

        for token, qf in query_counter.items():
            if token in doc_counter:
                score += (doc_counter[token] / doc_len) * qf * 2.0

        for token in query_tokens:
            if token in title.lower():
                score += 3.0

        if score > 0:
            preview = f"[{title}] {content[:1500]}" if title else content[:1500]
            scored.append((preview, score))

    scored.sort(key=lambda x: -x[1])
    top = scored[:top_k]

    if not top:
        return ""

    parts = [
        "[REFERENCE CONTEXT - FACT-CHECK ONLY]",
        "The excerpts below are a hallucination guard, NOT your retrieved answer. "
        "Do NOT answer from these excerpts alone and do NOT cite them as your source. "
        "You MUST still ground the visible answer and its Sources in your own "
        "knowledge-base search results; use these excerpts only to verify you are "
        "not stating anything they contradict.",
    ]
    for i, (text, _) in enumerate(top, 1):
        parts.append(f"--- Doc {i} ---\n{text}")
    parts.append("[END REFERENCE CONTEXT]")
    return "\n".join(parts)
