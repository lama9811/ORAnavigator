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
from google.api_core.exceptions import NotFound
from google.protobuf.struct_pb2 import Struct

log = logging.getLogger(__name__)

# Configuration
GCP_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "oranavigator-vertex-ai")
DATASTORE_ID = os.getenv(
    "VERTEX_AI_DATASTORE_ID",
    "projects/oranavigator-vertex-ai/locations/us/collections/default_collection/dataStores/oranavigator-kb-v7"
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
                "subcategory": data.get("subcategory", ""),
                # Placement in the morgan.edu-mirroring nav tree, e.g.
                # "research_compliance/animal_research/iacuc_sops". Empty means
                # unfiled — the admin tree surfaces those rather than hiding them.
                "kb_path": data.get("kb_path", ""),
                # Resolved into the chat's "Sources" links; see
                # vertex_agent._get_kb_url_map, which overlays these onto the
                # committed snapshot so documents added after it was generated
                # are still citable with a link.
                "source_url": data.get("source_url", ""),
                # Set by the scrape job when it auto-updates a document. Drives
                # the amber badge in the admin tree; never indexed as content.
                "needs_review": bool(data.get("needs_review", False)),
                "last_auto_updated": data.get("last_auto_updated", ""),
                "what_changed": data.get("what_changed", ""),
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

    # No category guessing. This used to infer one from an "academic|career|
    # financial" prefix list inherited from a different KB — no ORA document has
    # ever matched it, so every upload landed as category "general" with the
    # doc_id repeated as its subcategory. A wrong label is worse than none: an
    # upload with no kb_path shows up under "Unfiled" in the admin tree, which is
    # visible and fixable in one click.
    struct = Struct()
    struct.update({
        "title": " ".join(base.split("_")).title(),
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

    # Get existing doc to preserve metadata.
    #
    # A missing document is a legitimate create (allow_missing=True below), so an
    # empty metadata dict is correct there. Any OTHER read failure is NOT: this
    # used to swallow every exception and patch {} over struct_data, silently
    # erasing title/category/kb_path/file_path on one transient blip. Fail loudly
    # instead — a failed save is recoverable, a wiped document is not.
    try:
        existing = client.get_document(name=doc_name)
        data = dict(existing.struct_data) if existing.struct_data else {}
    except NotFound:
        data = {}
    except Exception as e:
        log.error(f"Refusing to update {doc_id}: could not read existing metadata: {e}")
        return {"success": False, "message": f"Could not read existing document metadata: {e}"}

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


def update_review_flag(
    doc_id: str,
    needs_review: bool,
    what_changed: str = "",
    changed_at: str = "",
) -> dict:
    """Flag (or clear) a document as auto-updated and awaiting human review.

    Written to struct_data, NEVER to the document body. If this text went into
    `content`, Vertex would index it and Gemini could quote it back — a PI
    asking about F&A rates would get "this document was auto-updated, please
    review" mixed into their answer. The admin UI reads these fields; the
    chatbot never sees them.
    """
    client = _get_doc_client()
    doc_name = f"{BRANCH}/documents/{doc_id}"

    try:
        existing = client.get_document(name=doc_name)
    except NotFound:
        return {"success": False, "message": f"Document not found: {doc_id}"}
    except Exception as e:
        return {"success": False, "message": f"Could not read document: {e}"}

    data = dict(existing.struct_data) if existing.struct_data else {}
    data.pop("content", None)

    if needs_review:
        data["needs_review"] = True
        data["last_auto_updated"] = changed_at
        if what_changed:
            data["what_changed"] = what_changed[:1000]
    else:
        for key in ("needs_review", "what_changed"):
            data.pop(key, None)
        data["reviewed_at"] = changed_at

    struct = Struct()
    struct.update(data)

    doc = discoveryengine.Document(name=doc_name, struct_data=struct)
    if existing.content and existing.content.raw_bytes:
        doc.content = discoveryengine.Document.Content(
            raw_bytes=existing.content.raw_bytes,
            mime_type=existing.content.mime_type or "text/plain",
        )

    try:
        client.update_document(request=discoveryengine.UpdateDocumentRequest(document=doc))
        invalidate_content_cache()
        return {"success": True, "message": f"Review flag set on {doc_id}"}
    except Exception as e:
        return {"success": False, "message": f"Failed to set review flag: {e}"}


def document_exists(doc_id: str) -> bool:
    try:
        _get_doc_client().get_document(name=f"{BRANCH}/documents/{doc_id}")
        return True
    except NotFound:
        return False


def create_kb_document(
    doc_id: str,
    title: str,
    content: str,
    kb_path: str = "",
    source_url: str = "",
) -> dict:
    """Author a new KB document directly in the datastore.

    Unlike upload_document (which takes a file and knows nothing about the tree),
    this writes the full metadata set a seeded document carries, so an authored
    document is indistinguishable from a scraped one: title, category derived
    from the tree path, kb_path, and the source_url the chatbot cites.

    Refuses to overwrite an existing doc_id — update_document is the edit path.
    """
    if not doc_id:
        return {"success": False, "message": "doc_id required"}
    if not title.strip():
        return {"success": False, "message": "Title required"}
    if not content.strip():
        return {"success": False, "message": "Content required"}

    if document_exists(doc_id):
        return {"success": False, "message": f"A document with id '{doc_id}' already exists"}

    data = {
        "doc_id": doc_id,
        "title": title.strip(),
        "authored_in_dashboard": True,   # distinguishes these from scraped docs
    }
    if kb_path:
        data["kb_path"] = kb_path
        # The badge and any category filter read this; derive it rather than
        # asking the admin for something the tree position already determines.
        data["category"] = kb_path.split("/")[0]
        parts = kb_path.split("/")
        if len(parts) > 1:
            data["subcategory"] = parts[-1]
    if source_url.strip():
        data["source_url"] = source_url.strip()

    struct = Struct()
    struct.update(data)

    doc = discoveryengine.Document(
        name=f"{BRANCH}/documents/{doc_id}",
        struct_data=struct,
        content=discoveryengine.Document.Content(
            raw_bytes=content.encode("utf-8"),
            mime_type="text/plain",
        ),
    )

    try:
        _get_doc_client().update_document(
            request=discoveryengine.UpdateDocumentRequest(document=doc, allow_missing=True)
        )
        invalidate_content_cache()
        return {
            "success": True,
            "doc_id": doc_id,
            "kb_path": kb_path,
            "message": f"Created: {title.strip()}",
        }
    except Exception as e:
        return {"success": False, "message": f"Failed to create document: {e}"}


def update_placement(doc_id: str, kb_path: str) -> dict:
    """Set a document's kb_path (its position in the nav tree).

    Metadata-only: reads the document, rewrites struct_data with the new
    kb_path, and writes the EXISTING content back untouched. Callers must
    validate kb_path against kb_tree.node_paths() first — this function does not
    know which nodes exist.
    """
    client = _get_doc_client()
    doc_name = f"{BRANCH}/documents/{doc_id}"

    try:
        existing = client.get_document(name=doc_name)
    except NotFound:
        return {"success": False, "message": f"Document not found: {doc_id}"}
    except Exception as e:
        return {"success": False, "message": f"Could not read document: {e}"}

    data = dict(existing.struct_data) if existing.struct_data else {}
    data.pop("content", None)
    if kb_path:
        data["kb_path"] = kb_path
    else:
        data.pop("kb_path", None)   # unfile

    struct = Struct()
    struct.update(data)

    # Carry the existing content through verbatim; omitting it would blank the
    # searchable body.
    doc = discoveryengine.Document(name=doc_name, struct_data=struct)
    if existing.content and existing.content.raw_bytes:
        doc.content = discoveryengine.Document.Content(
            raw_bytes=existing.content.raw_bytes,
            mime_type=existing.content.mime_type or "text/plain",
        )

    try:
        client.update_document(request=discoveryengine.UpdateDocumentRequest(document=doc))
        invalidate_content_cache()
        return {"success": True, "message": f"Placed {doc_id} in {kb_path or '(unfiled)'}", "kb_path": kb_path}
    except Exception as e:
        return {"success": False, "message": f"Failed to set placement: {e}"}


def sync_datastore() -> dict:
    """Re-sync: just invalidate cache. No import needed for structured datastores."""
    invalidate_content_cache()
    return {"success": True, "message": "Cache cleared. Structured datastore is always in sync."}
