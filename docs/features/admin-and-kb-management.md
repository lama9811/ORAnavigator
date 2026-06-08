# Admin & KB Management

**In one line:** admin-only dashboard plus endpoints to edit the live knowledge base.

## What it does (plain English)
Lets an admin see research stats and add / update / remove knowledge-base documents in the live
Vertex AI Search datastore without a full redeploy.

## Where it lives
- `backend/main.py` — admin + `cloud-kb` endpoints.
- `backend/datastore_manager.py` — the Discovery Engine client (per-doc upsert/delete).

## How it works
- KB edit endpoints (admin-authed):
  - `POST /api/admin/cloud-kb/upload` — add a document.
  - `PUT /api/admin/cloud-kb/documents/{doc_id}` — update a document's content.
  - `DELETE /api/admin/cloud-kb/documents/{doc_id}` — remove a document.
  - `POST /api/admin/cloud-kb/sync` — invalidate caches so the chatbot sees changes immediately.
- Updates are **instant** (no slow batch re-index) and auto-clear the query cache.

## API & data
- Endpoints: `/api/admin/...`, `/api/admin/cloud-kb/...`.
- Tables: `feedback`, `support_tickets`, `kb_suggestions` (admin-reviewable).

## Don't regress (load-bearing)
- After a datastore mutation, **regenerate the manifests** (`restructure_kb_v2.py --manifest-only`)
  and keep the backend + ADK manifest copies in sync, or enumeration goes stale.
- This admin path is the natural "publish" target for a future **KB Gap-Filler** / **KB-Sync** agent.

## Status
✅ Built & deployed.
