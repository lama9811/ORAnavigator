"""
list_kb_topics — ADK FunctionTool for browseable KB navigation.

Loads `_kb_manifest.json` once (lazy, at first call). Zero Discovery Engine
calls — the manifest is bundled with the agent code.

The KB is structured to mirror morgan.edu/ora's left-sidebar navigation
hierarchy exactly. Folders map 1:1 to the URL path under
/office-of-research-administration/. This tool exposes that hierarchy so the
agent can answer enumeration questions deterministically.

Examples:
  list_kb_topics()                                          → top-level sections (about, pre_award, …)
  list_kb_topics(path="pre_award")                          → 12 pre-award sub-pages + counts
  list_kb_topics(path="pre_award/budget_development")       → docs at that page
  list_kb_topics(path="research_compliance/animal_research/iacuc_sops") → all 50 SOPs

Back-compat: also accepts (category, subcategory) — these are joined with "/"
to form a path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

MAX_DOCS_INLINE = 100   # cap items returned in a single subcategory call

_MANIFEST: Optional[dict] = None
_INDEX: Optional[dict] = None   # path -> node


def _resolve_manifest_path() -> Path:
    explicit = os.getenv("KB_MANIFEST_PATH")
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        raise FileNotFoundError(f"KB_MANIFEST_PATH set to {explicit!r} but file not found")

    here = Path(__file__).resolve()
    candidates = [
        here.parent / "_kb_manifest.json",                                          # bundled with agent
        here.parent.parent.parent / "backend" / "kb_structured" / "_manifest.json", # repo root
        Path("/app/ora_navigator_unified/_kb_manifest.json"),                       # Cloud Run absolute
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "Could not locate _kb_manifest.json. Set KB_MANIFEST_PATH env var or "
        "ensure the file is bundled with the agent."
    )


def _index_tree(nodes: list[dict], idx: dict) -> None:
    """Recursively index every node by its path."""
    for node in nodes:
        idx[node["path"]] = node
        if node.get("children"):
            _index_tree(node["children"], idx)


def _load_manifest() -> dict:
    global _MANIFEST, _INDEX
    if _MANIFEST is None:
        _MANIFEST = json.loads(_resolve_manifest_path().read_text())
        _INDEX = {}
        _index_tree(_MANIFEST.get("tree", []), _INDEX)
    return _MANIFEST


def _normalize_path(category: Optional[str], subcategory: Optional[str], path: Optional[str]) -> str:
    """Build a normalized path string from the various input forms."""
    if path is not None:
        return path.strip("/").lower()
    parts: list[str] = []
    if category:
        parts.append(category.strip("/").lower())
    if subcategory:
        parts.append(subcategory.strip("/").lower())
    return "/".join(parts)


def list_kb_topics(category: Optional[str] = None,
                   subcategory: Optional[str] = None,
                   path: Optional[str] = None) -> dict[str, Any]:
    """Browse the ORA Navigator knowledge base hierarchically.

    The KB mirrors morgan.edu/ora's nav: each top-level section (about,
    pre_award, post_award, policies_and_guidelines, research_compliance,
    trainings, resources, funding_sources, ora_announcements) contains
    sub-pages, which in turn may contain their own sub-pages and docs.

    Use this tool WHENEVER the user asks:
      - "What do you have on X?"  /  "List all X"  /  "Show me your X"
      - Any enumeration question that needs a deterministic list of docs.
      - Before broad questions, to ground in the actual KB inventory.

    Args:
        category: Top-level section (e.g. "pre_award", "research_compliance").
            If omitted (along with path), returns the top-level overview.
        subcategory: Sub-page slug under the category (e.g. "budget_development",
            "animal_research"). Combined with category to form a path.
        path: Full slash-delimited path (e.g. "research_compliance/animal_research/iacuc_sops").
            Use this for deeper drill-downs. Takes precedence over
            category/subcategory if both are given.

    Returns:
        dict with:
          level     — "root" | "node" | "error"
          path      — the path navigated to
          title     — human-readable title
          children  — list of {slug, path, title, doc_count} for sub-pages
          docs      — list of {doc_id, title, source_url, file_path} for docs at this level
          total     — total docs at this level (this folder + all descendants)
          direct    — count of docs directly at this folder (not in sub-pages)
          truncated — true if docs list was capped at MAX_DOCS_INLINE
          hint      — guidance on the next call
    """
    m = _load_manifest()

    p = _normalize_path(category, subcategory, path)

    # --- Root level: list top-level sections ---
    if not p:
        items = [
            {"slug": n["slug"], "path": n["path"], "title": n["title"], "doc_count": n["doc_count"]}
            for n in m.get("tree", [])
        ]
        return {
            "level": "root",
            "path": "",
            "title": "ORA Knowledge Base — Top-Level Sections (mirrors morgan.edu/ora nav)",
            "children": items,
            "docs": [],
            "total": m.get("total_docs", 0),
            "direct": 0,
            "truncated": False,
            "hint": "Call list_kb_topics(path='<slug>') to drill into a section. "
                    "Example: list_kb_topics(path='pre_award').",
        }

    node = _INDEX.get(p) if _INDEX is not None else None
    if not node:
        valid = sorted([k for k in (_INDEX or {}).keys() if "/" not in k])
        return {
            "level": "error",
            "path": p,
            "title": f"Unknown path: {p!r}",
            "children": [],
            "docs": [],
            "total": 0,
            "direct": 0,
            "truncated": False,
            "hint": f"Valid top-level paths: {valid}. For deeper paths, first call "
                    "list_kb_topics(path='<top>') to see its children.",
        }

    children = [
        {"slug": c["slug"], "path": c["path"], "title": c["title"], "doc_count": c["doc_count"]}
        for c in node.get("children", [])
    ]
    docs = node.get("docs", [])
    truncated = len(docs) > MAX_DOCS_INLINE
    shown = docs[:MAX_DOCS_INLINE] if truncated else docs

    hint_parts = []
    if children:
        hint_parts.append(f"Call list_kb_topics(path='{p}/<slug>') to drill into a sub-page.")
    if docs:
        hint_parts.append(
            "Use the KB search tool to retrieve the full content of any listed doc by its title."
        )
    if truncated:
        hint_parts.insert(0, f"Showing first {MAX_DOCS_INLINE} of {len(docs)} docs.")

    return {
        "level": "node",
        "path": p,
        "title": node["title"],
        "children": children,
        "docs": shown,
        "total": node["doc_count"],
        "direct": node.get("direct_doc_count", len(docs)),
        "truncated": truncated,
        "hint": " ".join(hint_parts) if hint_parts else "",
    }
