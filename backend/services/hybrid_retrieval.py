"""
Pinecone Re-ingest Utility
==========================
Embeds the KB documents from the Vertex AI Search datastore with
text-embedding-004 and upserts them into a Pinecone index.

This is an admin-only utility, used by the /api/admin/knowledge-base/sync-all
endpoint. It is NOT on the chat hot path -- ORA Navigator's retrieval is
agent-first (the ADK agent's VertexAiSearchTool plus the kb_prefetch layer),
with Layer 3 grounding verification in vertex_agent.py.

History: this file once also held a hybrid Pinecone/Vertex RRF retriever
(hybrid_search) from the older manual-RAG design. That path was dead code and
has been removed; only the Pinecone re-ingest remains in use.
"""

import os
import logging
from typing import Optional

log = logging.getLogger(__name__)

# -- Embedding config (same as the semantic cache in cache.py) ----------------
EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_DIMS = 256


# -- Pinecone config (read lazily so .env loads first) ------------------------
def _get_pinecone_config():
    return {
        "api_key": os.getenv("PINECONE_API_KEY"),
        "index_name": os.getenv("PINECONE_INDEX_NAME"),
        "namespace": os.getenv("PINECONE_NAMESPACE", "docs"),
    }
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "docs")


# -- Lazy Pinecone client -----------------------------------------------------

_pinecone_index = None
_pinecone_init_attempted = False


def _get_pinecone_index():
    """Lazy-init the Pinecone index. Returns None if unavailable."""
    global _pinecone_index, _pinecone_init_attempted

    if _pinecone_init_attempted:
        return _pinecone_index

    _pinecone_init_attempted = True

    cfg = _get_pinecone_config()
    if not cfg["api_key"] or not cfg["index_name"]:
        log.info("[PINECONE] env vars not set, Pinecone disabled")
        return None

    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=cfg["api_key"])
        _pinecone_index = pc.Index(cfg["index_name"])
        log.info(f"[PINECONE] index '{cfg['index_name']}' connected")
        return _pinecone_index
    except Exception as e:
        log.warning(f"[PINECONE] init failed: {e}")
        return None


def is_pinecone_available() -> bool:
    """Check if Pinecone is configured."""
    cfg = _get_pinecone_config()
    return bool(cfg["api_key"] and cfg["index_name"])


# -- Lazy embedding client ----------------------------------------------------

_genai_client = None
_genai_init_attempted = False


def _get_genai_client():
    """Lazy-init the Google genai client for embeddings."""
    global _genai_client, _genai_init_attempted

    if _genai_init_attempted:
        return _genai_client

    _genai_init_attempted = True

    try:
        from google import genai
        _genai_client = genai.Client(vertexai=True)
        log.info(f"[PINECONE] embedding client ready (model={EMBEDDING_MODEL}, dims={EMBEDDING_DIMS})")
        return _genai_client
    except Exception as e:
        log.warning(f"[PINECONE] embedding client init failed: {e}")
        return None


def _embed_query(text: str) -> Optional[list[float]]:
    """Embed a text string into a vector using text-embedding-004."""
    client = _get_genai_client()
    if client is None:
        return None
    try:
        from google import genai
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=genai.types.EmbedContentConfig(
                output_dimensionality=EMBEDDING_DIMS,
            ),
        )
        return result.embeddings[0].values
    except Exception as e:
        log.warning(f"[PINECONE] embedding failed: {e}")
        return None


# -- Pinecone re-ingest from the Vertex AI datastore -------------------------

def reingest_to_pinecone(batch_size: int = 50) -> dict:
    """
    Fetch all KB docs from the Vertex AI datastore, embed with
    text-embedding-004, and upsert them to Pinecone.

    Syncs the Pinecone index with the current state of the Vertex AI structured
    datastore so both sources hold the same documents.

    Returns: {"upserted": int, "failed": int, "total_docs": int, "errors": list}
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
