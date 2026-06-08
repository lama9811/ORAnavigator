# Forms Catalog

**In one line:** a searchable catalog of ORA forms, derived from the knowledge base.

## What it does (plain English)
Lets users find the right ORA form (routing forms, budget templates, DocuSign PowerForms, etc.)
and open it directly. Also powers the "Open form" links on proposal tasks.

## Where it lives
- `backend/services/forms_catalog.py` (route `/forms`).

## How it works
- Reads `backend/kb_structured/_all_documents.jsonl`, filters to form-type docs, and derives
  sponsors/roles for filtering.
- `get_form(doc_id)` resolves a `doc_id` → form URL/title; the proposals tracker uses this to
  render task "Open form" links.

## API & data
- Backed by the KB index (`_all_documents.jsonl`); no separate table.

## Don't regress (load-bearing)
- Depends on stable `doc_id`s and the regenerated `_all_documents.jsonl`.

## Status
✅ Built & deployed.
