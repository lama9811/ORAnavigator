"""
Hybrid Retrieval Service
========================
Combines Pinecone vector search with Vertex AI Search using
Reciprocal Rank Fusion (RRF) for better recall and ranking.

Pinecone provides dense vector similarity (text-embedding-004),
Vertex AI Search provides keyword/semantic search with query expansion.
RRF merges both ranked lists without needing score normalization.

Graceful degradation: if either source is unavailable, falls back
to the other. If both are down, returns empty results.
"""

import os
import logging
import time
from typing import Optional
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

log = logging.getLogger(__name__)

# ── Embedding config (same as semantic cache in cache.py) ──────────────────────
EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_DIMS = 256

# ── Pinecone config (read lazily to allow .env to load first) ─────────────────
def _get_pinecone_config():
    return {
        "api_key": os.getenv("PINECONE_API_KEY"),
        "index_name": os.getenv("PINECONE_INDEX_NAME"),
        "namespace": os.getenv("PINECONE_NAMESPACE", "docs"),
    }
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "docs")


@dataclass
class HybridResult:
    """Result from hybrid search."""
    results: list = field(default_factory=list)       # [(doc_id, text, rrf_score), ...]
    pinecone_count: int = 0
    vertex_count: int = 0
    pinecone_ok: bool = False
    vertex_ok: bool = False
    elapsed_ms: float = 0.0
    strategy: str = "none"  # "hybrid", "pinecone_only", "vertex_only", "none"

    @property
    def doc_texts(self) -> list[str]:
        """Extract text strings from results for retrieval gate compatibility."""
        return [text for _, text, _ in self.results if text]


# ── Lazy Pinecone client ─────────────────────────────────────────────────────

_pinecone_index = None
_pinecone_init_attempted = False


def _get_pinecone_index():
    """Lazy-init Pinecone index. Returns None if unavailable."""
    global _pinecone_index, _pinecone_init_attempted

    if _pinecone_init_attempted:
        return _pinecone_index

    _pinecone_init_attempted = True

    cfg = _get_pinecone_config()
    if not cfg["api_key"] or not cfg["index_name"]:
        log.info("[HYBRID] Pinecone env vars not set, vector search disabled")
        return None

    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=cfg["api_key"])
        _pinecone_index = pc.Index(cfg["index_name"])
        log.info(f"[HYBRID] Pinecone index '{cfg['index_name']}' connected")
        return _pinecone_index
    except Exception as e:
        log.warning(f"[HYBRID] Pinecone init failed: {e}")
        return None


def is_pinecone_available() -> bool:
    """Check if Pinecone is configured and reachable."""
    cfg = _get_pinecone_config()
    return bool(cfg["api_key"] and cfg["index_name"])


# ── Lazy embedding client ────────────────────────────────────────────────────

_genai_client = None
_genai_init_attempted = False


def _get_genai_client():
    """Lazy-init Google genai client for embeddings."""
    global _genai_client, _genai_init_attempted

    if _genai_init_attempted:
        return _genai_client

    _genai_init_attempted = True

    try:
        from google import genai
        _genai_client = genai.Client(vertexai=True)
        log.info(f"[HYBRID] Embedding client ready (model={EMBEDDING_MODEL}, dims={EMBEDDING_DIMS})")
        return _genai_client
    except Exception as e:
        log.warning(f"[HYBRID] Embedding client init failed: {e}")
        return None


def _embed_query(query: str) -> Optional[list[float]]:
    """Embed a query string into a vector using text-embedding-004."""
    client = _get_genai_client()
    if client is None:
        return None
    try:
        from google import genai
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=query,
            config=genai.types.EmbedContentConfig(
                output_dimensionality=EMBEDDING_DIMS,
            ),
        )
        return result.embeddings[0].values
    except Exception as e:
        log.warning(f"[HYBRID] Embedding failed: {e}")
        return None


@lru_cache(maxsize=200)
def _embed_query_cached(query: str) -> tuple:
    """Cache embeddings for repeated queries. Returns tuple (hashable for LRU)."""
    result = _embed_query(query)
    return tuple(result) if result else ()


# ── Pinecone vector search ───────────────────────────────────────────────────

def pinecone_search(query: str, top_k: int = 10) -> list[tuple[str, str, float]]:
    """
    Search Pinecone index using text-embedding-004 vectors.

    Returns: [(doc_id, text, score), ...] sorted by similarity descending.
    """
    index = _get_pinecone_index()
    if index is None:
        return []

    cached = _embed_query_cached(query)
    if not cached:
        return []
    vector = list(cached)

    try:
        response = index.query(
            vector=vector,
            top_k=top_k,
            namespace=PINECONE_NAMESPACE,
            include_metadata=True,
        )

        results = []
        for match in response.matches:
            doc_id = match.id
            text = match.metadata.get("text", "") if match.metadata else ""
            score = match.score
            results.append((doc_id, text, score))

        log.info(f"[HYBRID] Pinecone returned {len(results)} results for '{query[:50]}'")
        return results

    except Exception as e:
        log.warning(f"[HYBRID] Pinecone search failed: {e}")
        return []


# ── Vertex AI Search (delegates to existing retrieval_gate) ──────────────────

def vertex_search(query: str, top_k: int = 10) -> list[tuple[str, str]]:
    """
    Search Vertex AI Search datastore via the existing retrieval_gate.

    Returns: [(doc_id, text), ...] in rank order.
    """
    try:
        from services.retrieval_gate import search_kb, _extract_doc_text

        raw_results = search_kb(query, num_results=top_k)
        results = []
        for r in raw_results:
            doc_id = r.document.name.split("/")[-1] if r.document and r.document.name else ""
            text = _extract_doc_text(r)
            if doc_id and text:
                results.append((doc_id, text))

        log.info(f"[HYBRID] Vertex returned {len(results)} results for '{query[:50]}'")
        return results

    except Exception as e:
        log.warning(f"[HYBRID] Vertex search failed: {e}")
        return []


# ── Reciprocal Rank Fusion ───────────────────────────────────────────────────

def rrf_merge(
    pinecone_results: list[tuple[str, str, float]],
    vertex_results: list[tuple[str, str]],
    k: int = 60,
) -> list[tuple[str, str, float]]:
    """
    Reciprocal Rank Fusion: score = sum(1/(k+rank)) across sources.

    Args:
        pinecone_results: [(doc_id, text, similarity_score), ...]
        vertex_results: [(doc_id, text), ...]
        k: RRF constant (default 60, standard in literature)

    Returns: [(doc_id, text, rrf_score), ...] sorted by RRF score descending.
    """
    scores: dict[str, float] = {}
    texts: dict[str, str] = {}

    # Score Pinecone results
    for rank, (doc_id, text, _sim_score) in enumerate(pinecone_results):
        scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank + 1)
        # Keep the longest text we have for each doc
        if doc_id not in texts or len(text) > len(texts[doc_id]):
            texts[doc_id] = text

    # Score Vertex results
    for rank, (doc_id, text) in enumerate(vertex_results):
        scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank + 1)
        if doc_id not in texts or len(text) > len(texts[doc_id]):
            texts[doc_id] = text

    # Sort by RRF score descending
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [(doc_id, texts.get(doc_id, ""), rrf_score) for doc_id, rrf_score in ranked]


# ── Main hybrid search ───────────────────────────────────────────────────────

def hybrid_search(query: str, top_k: int = 5) -> HybridResult:
    """
    Search both Pinecone and Vertex AI in parallel, merge with RRF.

    Falls back gracefully:
    - Both available: full hybrid with RRF fusion
    - Pinecone only: vector search results
    - Vertex only: keyword/semantic search results
    - Neither: empty result
    """
    start = time.time()
    pinecone_results = []
    vertex_results = []
    pinecone_ok = False
    vertex_ok = False

    # Run both searches in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(pinecone_search, query, top_k * 2): "pinecone",
            executor.submit(vertex_search, query, top_k * 2): "vertex",
        }

        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result(timeout=10)
                if source == "pinecone" and result:
                    pinecone_results = result
                    pinecone_ok = True
                elif source == "vertex" and result:
                    vertex_results = result
                    vertex_ok = True
            except Exception as e:
                log.warning(f"[HYBRID] {source} search failed in parallel exec: {e}")

    # Determine strategy and merge
    if pinecone_ok and vertex_ok:
        strategy = "hybrid"
        merged = rrf_merge(pinecone_results, vertex_results)
    elif pinecone_ok:
        strategy = "pinecone_only"
        merged = [(doc_id, text, score) for doc_id, text, score in pinecone_results]
    elif vertex_ok:
        strategy = "vertex_only"
        merged = [(doc_id, text, 1.0 / (60 + rank + 1)) for rank, (doc_id, text) in enumerate(vertex_results)]
    else:
        strategy = "none"
        merged = []

    elapsed = (time.time() - start) * 1000
    final = merged[:top_k]

    log.info(
        f"[HYBRID] strategy={strategy} pinecone={len(pinecone_results)} "
        f"vertex={len(vertex_results)} merged={len(final)} elapsed={elapsed:.0f}ms"
    )

    return HybridResult(
        results=final,
        pinecone_count=len(pinecone_results),
        vertex_count=len(vertex_results),
        pinecone_ok=pinecone_ok,
        vertex_ok=vertex_ok,
        elapsed_ms=elapsed,
        strategy=strategy,
    )


def build_hybrid_context(result: HybridResult, max_chars: int = 4000) -> str:
    """Build a context string from hybrid results for injection into the agent."""
    if not result.results:
        return ""

    ctx = f"\n\nRETRIEVED KB DOCUMENTS ({result.strategy}, {len(result.results)} docs):\n"
    total = 0
    for i, (doc_id, text, score) in enumerate(result.results):
        if total + len(text) > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                ctx += f"\n[Doc {i+1} | {doc_id}] {text[:remaining]}...\n"
            break
        ctx += f"\n[Doc {i+1} | {doc_id}] {text}\n"
        total += len(text)

    ctx += "\nIMPORTANT: Base your answer on the documents above. If the answer is not in these documents, say so.\n"
    return ctx


# ── Pinecone re-ingest from Vertex AI datastore ─────────────────────────────

def reingest_to_pinecone(batch_size: int = 50) -> dict:
    """
    Fetch all KB docs from Vertex AI datastore, embed with text-embedding-004,
    and upsert to Pinecone.

    This syncs the Pinecone index with the current state of the Vertex AI
    structured datastore so both sources have the same documents.

    Returns: {"upserted": int, "failed": int, "errors": list}
    """
    from datastore_manager import list_datastore_documents, get_document_content

    index = _get_pinecone_index()
    if index is None:
        return {"upserted": 0, "failed": 0, "errors": ["Pinecone not available"]}

    # Fetch all docs from Vertex AI datastore
    docs = list_datastore_documents()
    log.info(f"[REINGEST] Found {len(docs)} docs in Vertex AI datastore")

    upserted = 0
    failed = 0
    errors = []
    batch = []

    for doc in docs:
        doc_id = doc["id"]
        title = doc.get("title", doc_id)
        category = doc.get("category", "")

        # Get full content
        content = get_document_content(doc_id, max_chars=8000)
        if not content or content.startswith("Error"):
            log.warning(f"[REINGEST] Skipping {doc_id}: no content")
            failed += 1
            errors.append(f"{doc_id}: no content")
            continue

        # Embed the content
        embed_text = f"{title}. {content}" if title else content
        vector = _embed_query(embed_text)
        if vector is None:
            log.warning(f"[REINGEST] Skipping {doc_id}: embedding failed")
            failed += 1
            errors.append(f"{doc_id}: embedding failed")
            continue

        batch.append({
            "id": doc_id,
            "values": vector,
            "metadata": {
                "text": content[:4000],  # Pinecone metadata limit
                "title": title,
                "category": category,
            },
        })

        # Upsert in batches
        if len(batch) >= batch_size:
            try:
                index.upsert(vectors=batch, namespace=PINECONE_NAMESPACE)
                upserted += len(batch)
                log.info(f"[REINGEST] Upserted batch of {len(batch)} ({upserted} total)")
            except Exception as e:
                failed += len(batch)
                errors.append(f"Batch upsert failed: {e}")
                log.error(f"[REINGEST] Batch upsert failed: {e}")
            batch = []

    # Flush remaining
    if batch:
        try:
            index.upsert(vectors=batch, namespace=PINECONE_NAMESPACE)
            upserted += len(batch)
            log.info(f"[REINGEST] Upserted final batch of {len(batch)} ({upserted} total)")
        except Exception as e:
            failed += len(batch)
            errors.append(f"Final batch upsert failed: {e}")
            log.error(f"[REINGEST] Final batch upsert failed: {e}")

    summary = {
        "upserted": upserted,
        "failed": failed,
        "total_docs": len(docs),
        "errors": errors[:10],  # cap error list
    }
    log.info(f"[REINGEST] Complete: {summary}")
    return summary
