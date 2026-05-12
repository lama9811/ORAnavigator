"""
Retrieval Gate: Pre-Agent KB Search & Quality Evaluation
========================================================
Searches the knowledge base BEFORE sending to the AI agent.
Evaluates retrieval quality and decides:
  - HIGH: 3+ relevant docs -> proceed with injected context
  - LOW: 1-2 docs -> try alternate queries, then proceed with disclaimer
  - NONE: 0 docs -> refuse gracefully, don't call agent at all

This prevents hallucination by ensuring the agent always has KB context,
and catches cases where KB has no relevant info before wasting an LLM call.
"""

import os
import logging
import re
from typing import Optional
from dataclasses import dataclass, field
from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core.client_options import ClientOptions

# Hybrid retrieval: Pinecone + Vertex AI Search (Plan B+)
try:
    from services.hybrid_retrieval import hybrid_search, is_pinecone_available
    _HYBRID_AVAILABLE = True
except ImportError:
    _HYBRID_AVAILABLE = False

log = logging.getLogger(__name__)

# Configuration
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "csnavigator-vertex-ai")
DATASTORE_ID = "csnavigator-kb-v7"
LOCATION = "us"
API_ENDPOINT = f"{LOCATION}-discoveryengine.googleapis.com"

SERVING_CONFIG = (
    f"projects/{GCP_PROJECT}/locations/{LOCATION}/collections/default_collection"
    f"/dataStores/{DATASTORE_ID}/servingConfigs/default_search"
)

# Quality thresholds
HIGH_CONFIDENCE_DOCS = 3
LOW_CONFIDENCE_DOCS = 1

# Queries that should bypass the retrieval gate (greetings, meta questions)
_BYPASS_RE = re.compile(
    r'^(h(i|ey|ello)|yo|sup|thank|bye|who (made|built|created)|good (morning|afternoon|evening))',
    re.IGNORECASE,
)

_REFUSAL_MSG = (
    "I don't have information about that in my knowledge base. "
    "For more details, contact the CS department at (443) 885-3962 or compsci@morgan.edu."
)


@dataclass
class RetrievalResult:
    quality: str  # "high", "low", "none", "bypass"
    docs: list = field(default_factory=list)
    doc_texts: list = field(default_factory=list)  # extracted text snippets
    summary: str = ""
    refusal_message: str = ""


# Cached client (initialized once)
_search_client = None


def _get_search_client():
    global _search_client
    if _search_client is None:
        options = ClientOptions(api_endpoint=API_ENDPOINT)
        _search_client = discoveryengine.SearchServiceClient(client_options=options)
    return _search_client


def _extract_doc_text(result) -> str:
    """Extract readable text from a Discovery Engine search result.
    Tries: snippets -> extractive answers -> struct_data content -> direct fetch."""
    try:
        doc = result.document
        title = ""

        # Get title from struct_data or derived
        if doc.struct_data:
            data = dict(doc.struct_data)
            title = data.get("title", "")
            content = data.get("content", "")
            if content:
                return f"[{title}] {content[:1500]}" if title else content[:1500]

        # Try derived_struct_data (snippets/extractive answers from search)
        if doc.derived_struct_data:
            derived = dict(doc.derived_struct_data)
            if not title:
                title = str(derived.get("title", ""))

            snippets = derived.get("snippets", [])
            if snippets:
                texts = []
                for s in snippets:
                    if isinstance(s, dict):
                        texts.append(s.get("snippet", ""))
                    elif hasattr(s, "snippet"):
                        texts.append(str(s.snippet))
                joined = " ".join(t for t in texts if t)
                if joined:
                    return f"[{title}] {joined}" if title else joined

            answers = derived.get("extractive_answers", [])
            if answers:
                texts = []
                for a in answers:
                    if isinstance(a, dict):
                        texts.append(a.get("content", ""))
                    elif hasattr(a, "content"):
                        texts.append(str(a.content))
                joined = " ".join(t for t in texts if t)
                if joined:
                    return f"[{title}] {joined}" if title else joined

        # Fallback: use in-memory content cache (preloaded, no API calls)
        if doc.name:
            doc_id = doc.name.split("/")[-1]
            try:
                from datastore_manager import _get_cached_contents
                cached = _get_cached_contents()
                if doc_id in cached:
                    content = cached[doc_id].get("content", "")
                    title_cached = cached[doc_id].get("title", doc_id)
                    if content:
                        return f"[{title or title_cached}] {content[:1500]}"
            except Exception:
                pass

        return ""
    except Exception as e:
        log.warning(f"[RETRIEVAL] Failed to extract doc text: {e}")
        return ""


def search_kb(query: str, num_results: int = 5) -> list:
    """Search the KB datastore using Vertex AI Search with snippet extraction."""
    try:
        client = _get_search_client()
        request = discoveryengine.SearchRequest(
            serving_config=SERVING_CONFIG,
            query=query,
            page_size=num_results,
            query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
                condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO,
            ),
            spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
                mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO,
            ),
            # NOTE: snippet_spec and extractive_content_spec require Enterprise edition.
            # We use basic search + fallback content fetch from datastore instead.
        )
        response = client.search(request)
        results = list(response.results)
        log.info(f"[RETRIEVAL] Query '{query[:50]}' returned {len(results)} results")
        return results
    except Exception as e:
        log.warning(f"[RETRIEVAL] Search failed: {e}")
        return []


def _generate_alternate_queries(query: str) -> list[str]:
    """Generate simpler/broader alternate queries for retry."""
    alternates = []
    # Remove question words
    simplified = re.sub(r'^(what|where|how|who|when|why|can|do|does|is|are)\s+(is|are|do|does|can|the|a|an|i|my)?\s*', '', query, flags=re.IGNORECASE).strip()
    if simplified and simplified != query:
        alternates.append(simplified)
    # Extract course codes
    codes = re.findall(r'[A-Z]{2,4}\s*\d{3}', query.upper())
    for code in codes:
        alternates.append(code)
    # Extract proper nouns (Dr. X, Professor Y)
    names = re.findall(r'(?:Dr\.?|Professor)\s+(\w+)', query)
    for name in names:
        alternates.append(f"faculty {name}")
    return alternates[:3]


def evaluate_retrieval(query: str) -> RetrievalResult:
    """
    Main entry point: Search KB, evaluate quality, decide next step.

    Returns RetrievalResult with:
    - quality="bypass": greeting/meta question, skip KB search
    - quality="high": 3+ docs, proceed with injected context
    - quality="low": 1-2 docs, proceed but with caution
    - quality="none": 0 docs, refuse (don't call agent)
    """
    # Bypass for greetings/meta questions
    if _BYPASS_RE.match(query.strip()):
        return RetrievalResult(quality="bypass")

    # Try hybrid retrieval first (Pinecone + Vertex AI)
    if _HYBRID_AVAILABLE and is_pinecone_available():
        try:
            hybrid = hybrid_search(query)
            if hybrid.doc_texts:
                quality = "high" if len(hybrid.doc_texts) >= HIGH_CONFIDENCE_DOCS else "low"
                log.info(f"[RETRIEVAL] Hybrid: {quality} ({len(hybrid.doc_texts)} docs: {hybrid.pinecone_count} Pinecone + {hybrid.vertex_count} Vertex)")
                return RetrievalResult(
                    quality=quality,
                    doc_texts=hybrid.doc_texts,
                    summary=f"Hybrid: {hybrid.pinecone_count} Pinecone + {hybrid.vertex_count} Vertex AI docs",
                )
        except Exception as e:
            log.warning(f"[RETRIEVAL] Hybrid search failed, falling back to Vertex-only: {e}")

    # Fallback: Vertex AI Search only (original path)
    results = search_kb(query)
    doc_texts = [_extract_doc_text(r) for r in results]
    doc_texts = [t for t in doc_texts if t]  # filter empties

    if len(doc_texts) >= HIGH_CONFIDENCE_DOCS:
        log.info(f"[RETRIEVAL] HIGH confidence ({len(doc_texts)} docs)")
        return RetrievalResult(
            quality="high",
            docs=results,
            doc_texts=doc_texts,
            summary=f"Found {len(doc_texts)} relevant KB documents.",
        )

    # Low or no results - try alternate queries
    if len(doc_texts) < LOW_CONFIDENCE_DOCS:
        alternates = _generate_alternate_queries(query)
        for alt_q in alternates:
            alt_results = search_kb(alt_q, num_results=3)
            alt_texts = [_extract_doc_text(r) for r in alt_results]
            alt_texts = [t for t in alt_texts if t]
            if alt_texts:
                doc_texts.extend(alt_texts)
                results.extend(alt_results)
                log.info(f"[RETRIEVAL] Alternate query '{alt_q}' found {len(alt_texts)} docs")
                if len(doc_texts) >= HIGH_CONFIDENCE_DOCS:
                    break

    if not doc_texts:
        log.info(f"[RETRIEVAL] NONE - no docs found for '{query[:50]}'")
        return RetrievalResult(
            quality="none",
            refusal_message=_REFUSAL_MSG,
        )

    quality = "high" if len(doc_texts) >= HIGH_CONFIDENCE_DOCS else "low"
    log.info(f"[RETRIEVAL] {quality.upper()} ({len(doc_texts)} docs after alternates)")
    return RetrievalResult(
        quality=quality,
        docs=results,
        doc_texts=doc_texts,
        summary=f"Found {len(doc_texts)} KB documents (some from alternate queries).",
    )


def build_retrieval_context(result: RetrievalResult, max_chars: int = 4000) -> str:
    """Build a context string from retrieval results to inject into the agent."""
    if not result.doc_texts:
        return ""

    ctx = "\n\nRETRIEVED KB DOCUMENTS (use these as your primary source):\n"
    total = 0
    for i, text in enumerate(result.doc_texts):
        if total + len(text) > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                ctx += f"\n[Doc {i+1}] {text[:remaining]}...\n"
            break
        ctx += f"\n[Doc {i+1}] {text}\n"
        total += len(text)

    ctx += "\nIMPORTANT: Base your answer on the documents above. If the answer is not in these documents, say so.\n"
    return ctx
