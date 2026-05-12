"""
Vertex AI Search Structured Datastore Manager
===============================================
Manages documents in a structured Vertex AI Search datastore.
Documents are stored directly in the index as JSON (struct_data).
No GCS intermediary, no file crawling, instant updates.
"""

import os
import re
import time
import threading
import logging
from google.cloud import discoveryengine_v1 as discoveryengine
from google.api_core.client_options import ClientOptions
from google.protobuf.struct_pb2 import Struct

log = logging.getLogger(__name__)

# Configuration
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "csnavigator-vertex-ai")
DATASTORE_ID = os.getenv(
    "VERTEX_AI_DATASTORE_ID",
    "projects/csnavigator-vertex-ai/locations/us/collections/default_collection/dataStores/csnavigator-kb-v7"
)

_ds_parts = DATASTORE_ID.split("/")
LOCATION = _ds_parts[_ds_parts.index("locations") + 1] if "locations" in _ds_parts else "us"
API_ENDPOINT = f"{LOCATION}-discoveryengine.googleapis.com"
BRANCH = f"{DATASTORE_ID}/branches/default_branch"

# In-memory content cache for fast admin search
_content_cache = {}  # {doc_id: {"content": str, "title": str, ...}}
_content_cache_lock = threading.Lock()
_CONTENT_CACHE_TTL = 300  # 5 minutes
_cache_timestamp = 0


def _get_doc_client():
    options = ClientOptions(api_endpoint=API_ENDPOINT)
    return discoveryengine.DocumentServiceClient(client_options=options)


def invalidate_content_cache():
    """Clear the content cache (call after updates/deletes)."""
    global _cache_timestamp
    with _content_cache_lock:
        _content_cache.clear()
        _cache_timestamp = 0


def _get_cached_contents() -> dict:
    """Get all document contents from structured datastore, cached in memory."""
    global _cache_timestamp
    now = time.time()

    with _content_cache_lock:
        if _content_cache and now - _cache_timestamp < _CONTENT_CACHE_TTL:
            return dict(_content_cache)

    # Cache miss: fetch all docs from the datastore
    client = _get_doc_client()
    new_cache = {}
    try:
        request = discoveryengine.ListDocumentsRequest(parent=BRANCH, page_size=200)
        for doc in client.list_documents(request=request):
            doc_id = doc.name.split("/")[-1]
            data = dict(doc.struct_data) if doc.struct_data else {}
            # Content lives in raw_bytes, not struct_data
            if doc.content and doc.content.raw_bytes:
                data["content"] = doc.content.raw_bytes.decode("utf-8")
            new_cache[doc_id] = data
    except Exception as e:
        log.warning(f"Failed to fetch docs for cache: {e}")
        return {}

    with _content_cache_lock:
        _content_cache.clear()
        _content_cache.update(new_cache)
        _cache_timestamp = now

    return dict(new_cache)


def list_datastore_documents() -> list[dict]:
    """List all documents in the structured datastore."""
    start = time.time()
    client = _get_doc_client()

    docs = []
    try:
        request = discoveryengine.ListDocumentsRequest(parent=BRANCH, page_size=200)
        for doc in client.list_documents(request=request):
            doc_id = doc.name.split("/")[-1]
            data = dict(doc.struct_data) if doc.struct_data else {}
            content = doc.content.raw_bytes.decode("utf-8") if doc.content and doc.content.raw_bytes else ""
            docs.append({
                "id": doc_id,
                "filename": doc_id,
                "uri": f"structured://{doc_id}",
                "size": len(content.encode("utf-8")) if content else 0,
                "modified": "",
                "title": data.get("title", doc_id),
                "category": data.get("category", ""),
            })
    except Exception as e:
        log.error(f"Failed to list documents: {e}")

    result = sorted(docs, key=lambda d: d["filename"])
    log.info(f"list_datastore_documents: {time.time()-start:.1f}s ({len(result)} docs)")
    return result


def get_document_content(doc_id: str, max_chars: int = 50000) -> str:
    """Read document content from the datastore."""
    client = _get_doc_client()
    doc_name = f"{BRANCH}/documents/{doc_id}"

    try:
        doc = client.get_document(name=doc_name)
        # Content stored in raw_bytes (for search indexing)
        if doc.content and doc.content.raw_bytes:
            content = doc.content.raw_bytes.decode("utf-8")
            return content[:max_chars]
        # Fallback: check struct_data.content
        data = dict(doc.struct_data) if doc.struct_data else {}
        return data.get("content", "")[:max_chars]
    except Exception as e:
        return f"Error reading document: {e}"


def search_documents(query: str) -> list[dict]:
    """Advanced search across all KB documents.
    Supports: partial matches, multi-word queries, case-insensitive.
    Searches both content and metadata (title, category)."""
    cached = _get_cached_contents()
    results = []
    query_lower = query.lower().strip()
    if not query_lower:
        return results

    # Split query into individual terms for multi-word matching
    terms = query_lower.split()
    escaped_full = re.escape(query_lower)

    for doc_id, data in cached.items():
        content = data.get("content", "")
        title = data.get("title", "")
        category = data.get("category", "")
        searchable = f"{title}\n{category}\n{content}"

        # Count actual occurrences (for display) and relevance score (for sorting)
        full_pattern = re.compile(escaped_full, re.IGNORECASE)
        actual_count = len(full_pattern.findall(searchable))

        # For multi-word queries, also check individual terms
        if actual_count == 0 and len(terms) > 1:
            for term in terms:
                actual_count += len(re.findall(re.escape(term), searchable, re.IGNORECASE))

        if actual_count == 0:
            continue

        # Get snippet around first match
        snippet = ""
        match = re.search(re.escape(terms[0]), searchable, re.IGNORECASE)
        if match:
            idx = match.start()
            # Skip title/category prefix to show content context
            content_offset = len(title) + len(category) + 2
            if idx < content_offset:
                idx = content_offset
                match = re.search(re.escape(terms[0]), content, re.IGNORECASE)
                if match:
                    idx = match.start()
                else:
                    idx = 0
                snippet_src = content
            else:
                idx -= content_offset
                snippet_src = content

            start = max(0, idx - 80)
            end = min(len(snippet_src), idx + len(terms[0]) + 80)
            snippet = snippet_src[start:end].strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(snippet_src):
                snippet = snippet + "..."

        results.append({
            "filename": doc_id,
            "blob_path": doc_id,
            "uri": f"structured://{doc_id}",
            "match_count": actual_count,
            "snippet": snippet,
            "size": len(content),
        })

    return sorted(results, key=lambda r: r["match_count"], reverse=True)


def upload_document(filename: str, content: bytes, content_type: str = "text/plain") -> dict:
    """Create a new document in the structured datastore."""
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    doc_id = re.sub(r'[^a-zA-Z0-9_-]', '_', base)

    # Determine category from filename
    category = "general"
    for cat in ["academic", "career", "financial"]:
        if doc_id.startswith(cat):
            category = cat
            break

    text_content = content.decode("utf-8") if isinstance(content, bytes) else content

    struct = Struct()
    struct.update({
        "title": " ".join(base.split("_")).title(),
        "category": category,
        "subcategory": doc_id.replace(f"{category}_", ""),
    })

    client = _get_doc_client()
    doc = discoveryengine.Document(
        name=f"{BRANCH}/documents/{doc_id}",
        struct_data=struct,
        content=discoveryengine.Document.Content(
            raw_bytes=content if isinstance(content, bytes) else content.encode("utf-8"),
            mime_type="text/plain",
        ),
    )

    try:
        request = discoveryengine.UpdateDocumentRequest(document=doc, allow_missing=True)
        client.update_document(request=request)
        invalidate_content_cache()
        return {"success": True, "uri": f"structured://{doc_id}", "message": f"Created: {doc_id}"}
    except Exception as e:
        return {"success": False, "uri": "", "message": f"Failed to create document: {e}"}


def delete_document(doc_id: str, doc_uri: str = "") -> dict:
    """Delete a document from the structured datastore."""
    client = _get_doc_client()
    doc_name = f"{BRANCH}/documents/{doc_id}"

    try:
        client.delete_document(name=doc_name)
        invalidate_content_cache()
        return {"success": True, "message": f"Document {doc_id} deleted"}
    except Exception as e:
        return {"success": False, "message": f"Failed to delete: {e}"}


def update_document(doc_id: str, content: bytes, content_type: str = "text/plain") -> dict:
    """Update a document's content in the structured datastore.
    Instant. No GCS, no versioning, no crawling."""
    client = _get_doc_client()
    doc_name = f"{BRANCH}/documents/{doc_id}"

    text_content = content.decode("utf-8") if isinstance(content, bytes) else content

    # Get existing doc to preserve metadata
    try:
        existing = client.get_document(name=doc_name)
        data = dict(existing.struct_data) if existing.struct_data else {}
    except Exception:
        data = {}

    # Remove content from struct_data (it goes in content.raw_bytes for search)
    data.pop("content", None)

    struct = Struct()
    struct.update(data)

    # Use raw_bytes for searchable content + struct_data for metadata
    doc = discoveryengine.Document(
        name=doc_name,
        struct_data=struct,
        content=discoveryengine.Document.Content(
            raw_bytes=text_content.encode("utf-8") if isinstance(text_content, str) else text_content,
            mime_type="text/plain",
        ),
    )

    try:
        request = discoveryengine.UpdateDocumentRequest(document=doc, allow_missing=True)
        client.update_document(request=request)
        invalidate_content_cache()
        return {"success": True, "message": f"Updated: {doc_id} (instant)"}
    except Exception as e:
        return {"success": False, "message": f"Failed to update: {e}"}


def sync_datastore() -> dict:
    """Re-sync: just invalidate cache. No import needed for structured datastores."""
    invalidate_content_cache()
    return {"success": True, "message": "Cache cleared. Structured datastore is always in sync."}
