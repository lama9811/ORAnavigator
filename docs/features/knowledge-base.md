# Knowledge Base (KB)

**In one line:** the 382-document library of Morgan ORA content the assistant answers from.

## What it does (plain English)
Holds everything the assistant knows — pages scraped from Morgan's ORA website, organized in
a tree that mirrors the site's navigation (about, funding, compliance, pre/post-award, forms,
trainings, etc.). The assistant searches this, never the open internet.

## Where it lives
- `backend/kb_structured/` — **382 JSON docs** in a hierarchical tree + generated indexes
  `_all_documents.jsonl` (flat) and `_manifest.json` (tree).
- `backend/kb_browser.py` — deterministic enumeration ("list pre-award", "what's in training").
- `backend/datastore_manager.py` — read/write the Vertex AI Search datastore (instant per-doc
  upsert; no slow batch re-index).
- `adk_agent/ora_navigator_unified/list_kb_tool.py` — exposes the tree to the agent.
- `scripts/restructure_kb_v2.py` — regenerates the manifests after a KB change.
- Vertex AI Search datastore: `oranavigator-kb-v8` (location `us`).

## How it works
Source of truth = the JSON files on disk. They're indexed into Vertex AI Search (the semantic
retriever the agent calls). Enumeration queries are answered deterministically from the manifest
(no LLM, ~10ms). Each doc has: `doc_id`, `title`, `category`, `subcategory`, `source_url`,
`content`, `last_scraped`, `playwright_verified`, optional `key_facts`.

## Don't regress (load-bearing)
- **`doc_id` stability** — `forms_catalog` and `proposal_templates` reference `doc_id`s; changing
  one breaks "Open form" links.
- **Manifest sync** — after editing the KB, run `restructure_kb_v2.py --manifest-only`; the ADK
  copy `_kb_manifest.json` must match the backend `_manifest.json`.
- **Known content rules** the assistant must honor: IACUC **SOP 37 is deliberately missing**
  (never hallucinate it); IRB roster/schedule live in JS accordions (Playwright-only); stub pages
  flagged `page_status: stub_*` → "not yet populated"; listserv subject line is verbatim.
- **CMS silent redirects** — Morgan redirects dead URLs to siblings; verify when scraping.
- `try_browse()` is **history-aware** (STRONG vs WEAK enumeration triggers).

## Status
✅ Built & deployed. Currently hand-curated; a KB-Sync agent (web scraper) is a planned feature.
