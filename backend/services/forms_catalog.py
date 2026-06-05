"""Forms & templates catalog -- the read-side of the new /forms surface.

The chatbot can answer "what's the IRB approval form?" but every such turn
is a Gemini call. 71% of the 382-doc KB is forms / templates / DocuSign
PDFs; a dedicated browse surface lets a faculty member open the right
PDF in one click without burning an LLM call (and without giving the
model a chance to hallucinate a URL).

This module reads `kb_structured/_all_documents.jsonl` once at import,
filters to the form-like rows, derives sponsor and role tags, and exposes
list_forms(category, sponsor, role) for the GET /api/forms endpoint to
call. No DB, no embeddings, no network -- everything is a static read
of bundled JSON.

The KB doesn't carry sponsor / role columns directly (the legacy schema
only has `category` + `subcategory`), so this module derives them:
  - sponsor: keyword match on title + content; "Internal" if nothing matches.
  - role:   heuristic from category + subcategory (a form can serve more
            than one role -- "Staff routes the form, PI signs it" -- so
            roles is a list, not a single value).
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

_KB_DIR = Path(__file__).resolve().parent.parent / "kb_structured"
_MANIFEST_PATH = _KB_DIR / "_all_documents.jsonl"

# A doc is a "form" if its subcategory mentions any of these tokens. We
# choose subcategory over title because subcategory is editorial metadata
# (controlled by the KB curator); title is free-form and noisy.
_FORM_SUBCATEGORY_TOKENS = (
    "form", "template", "docusign", "checklist", "memo", "sample",
)

# Sponsor keyword matching. Order matters: the catalog tags ALL matching
# sponsors, but for the "Internal" fallback we only add it when no other
# sponsor matched. "Foundation" is intentionally last + broad.
_SPONSOR_PATTERNS = (
    ("NSF",         ("nsf", "national science foundation")),
    ("NIH",         ("nih", "national institutes of health")),
    ("NASA",        ("nasa",)),
    ("DoD",         ("dod", "department of defense", "durip")),
    ("DoE",         ("doe ", "department of energy")),
    ("USDA",        ("usda",)),
    ("EPA",         ("epa ", "environmental protection agency")),
    ("NOAA",        ("noaa",)),
    ("State of Maryland", ("state of maryland", "maryland higher education")),
    ("Foundation",  ("foundation",)),
)

# Role inference. A form can map to more than one role; the catalog tags
# generously and lets the filter narrow.
_ROLE_RULES = {
    # category : roles
    "pre_award":           ("PI", "Staff"),
    "post_award":          ("PI", "Staff"),
    "research_compliance": ("PI", "Staff", "Admin"),
    "policies_and_guidelines": ("Admin", "Staff"),
    "resources":           ("PI", "Staff"),
    "trainings":           ("PI", "Staff", "Admin"),
    "funding_sources":     ("PI",),
    "about":               ("Admin",),
    "ora_announcements":   ("Admin", "Staff"),
}


def _is_form(doc: dict) -> bool:
    sub = (doc.get("subcategory") or "").lower()
    return any(tok in sub for tok in _FORM_SUBCATEGORY_TOKENS)


def _detect_sponsors(text: str) -> list[str]:
    """Return the sorted list of sponsors mentioned anywhere in `text`.
    Always returns at least one entry: 'Internal' when no external sponsor
    is mentioned, so the sponsor filter can find sponsor-agnostic forms."""
    text_lc = (text or "").lower()
    found = []
    for label, needles in _SPONSOR_PATTERNS:
        if any(n in text_lc for n in needles):
            found.append(label)
    if not found:
        found.append("Internal")
    return found


def _detect_roles(category: str, subcategory: str) -> list[str]:
    roles = list(_ROLE_RULES.get(category, ("PI", "Staff")))
    # IACUC forms -> add Admin (committee members are also Admin-flavored)
    if "iacuc" in (subcategory or "").lower():
        if "Admin" not in roles:
            roles.append("Admin")
    return roles


def _read_form_content(file_path: str) -> str:
    """Read the form's content body for sponsor detection. We open each
    form file at import time; with ~80 forms this is a few ms total."""
    full = _KB_DIR / file_path
    if not full.exists():
        return ""
    try:
        data = json.loads(full.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return str(data.get("content") or "")
    except (json.JSONDecodeError, OSError):
        return ""
    return ""


@lru_cache(maxsize=1)
def _load_catalog() -> list[dict]:
    """Read the manifest, filter to forms, derive tags. Cached so the
    file scan happens once per process."""
    forms = []
    if not _MANIFEST_PATH.exists():
        return forms

    with _MANIFEST_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not _is_form(doc):
                continue

            title = doc.get("display_label") or doc.get("title") or ""
            content = _read_form_content(doc.get("file_path", ""))
            sponsors = _detect_sponsors(title + " " + content)
            roles = _detect_roles(doc.get("category", ""),
                                  doc.get("subcategory", ""))

            procedure = doc.get("procedure_url") or ""
            source = doc.get("source_url") or ""
            forms.append({
                "doc_id": doc["doc_id"],
                "title": title,
                "category": doc.get("category", ""),
                "subcategory": doc.get("subcategory", ""),
                # The clickable "open this form" link (DocuSign / PDF / Word).
                "url": procedure or source,
                # The morgan.edu/ora page that lists this form. Shown to the
                # user as a "View on morgan.edu" link so the catalog visibly
                # cites its source -- nothing here is made up; every form is
                # something you can find on the live ORA site.
                "source_url": source,
                "summary": (content[:240] + "...") if len(content) > 240 else content,
                "sponsors": sponsors,
                "roles": roles,
            })
    # Stable order: by category, then title.
    forms.sort(key=lambda f: (f["category"], f["title"].lower()))
    return forms


def list_forms(category: Optional[str] = None,
               sponsor: Optional[str] = None,
               role: Optional[str] = None) -> list[dict]:
    """Return the forms catalog optionally narrowed by category, sponsor,
    or role. Empty-string filters are treated as None (open). Filters
    intersect: passing two narrows further; unknown filter values yield []."""
    forms = _load_catalog()
    if category:
        forms = [f for f in forms if f["category"] == category]
    if sponsor:
        forms = [f for f in forms if sponsor in f["sponsors"]]
    if role:
        forms = [f for f in forms if role in f["roles"]]
    return forms


@lru_cache(maxsize=1)
def _catalog_by_id() -> dict:
    """doc_id -> form row, built once from the cached catalog."""
    return {f["doc_id"]: f for f in _load_catalog()}


def get_form(doc_id: Optional[str]) -> Optional[dict]:
    """Return the catalog row for a single doc_id, or None if the id is
    falsy or not a form-like doc. Used to resolve a proposal task's
    kb_doc_id to an openable URL."""
    if not doc_id:
        return None
    return _catalog_by_id().get(doc_id)
