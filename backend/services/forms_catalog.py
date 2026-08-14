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
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
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
    kb_doc_id to an openable URL.

    Falls back to the full KB document index for non-form docs (e.g. the
    Compliance Sentinel links to compliance hub pages and the CITI training
    doc, which aren't form-like and so aren't in the forms catalog) -- this
    keeps every linked task openable instead of silently dead."""
    if not doc_id:
        return None
    # Shallow-copy so callers can't mutate the lru-cached catalog row.
    row = _catalog_by_id().get(doc_id)
    if row:
        return dict(row)
    return resolve_kb_doc(doc_id)


@lru_cache(maxsize=1)
def _all_docs_by_id() -> dict:
    """doc_id -> {doc_id, title, url, source_url} for EVERY doc in the KB
    manifest (not just forms). Read once. Powers resolve_kb_doc()."""
    out: dict = {}
    if not _MANIFEST_PATH.exists():
        return out
    with _MANIFEST_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            did = doc.get("doc_id")
            if not did:
                continue
            title = doc.get("display_label") or doc.get("title") or ""
            source = doc.get("source_url") or ""
            url = doc.get("procedure_url") or source
            out[did] = {
                "doc_id": did,
                "title": title,
                "url": url,
                "source_url": source,
                # Mirrored eTraining screenshots, [{url, caption}]. Present only
                # on lesson documents; everything else carries an empty list.
                "images": doc.get("images") or [],
            }

    # Overlay the datastore's own procedure_url values. Required, not cosmetic:
    # a document created by the scrape's file phase exists ONLY in the
    # datastore, so a snapshot-only lookup would give every newly-added document
    # no download link -- the exact failure the attachment feature exists to
    # prevent. Same lazy once-per-process overlay _get_kb_url_map applies to
    # source_url.
    try:
        from datastore_manager import list_datastore_documents

        for doc in list_datastore_documents():
            did = doc.get("id") or doc.get("doc_id")
            live = (doc.get("procedure_url") or "").strip()
            if not did or not live:
                continue
            row = out.setdefault(did, {
                "doc_id": did,
                "title": doc.get("title") or did,
                "url": "",
                "source_url": doc.get("source_url") or "",
            })
            row["url"] = live
    except Exception:
        # The snapshot alone is a usable answer; a datastore blip must not take
        # the forms catalog down with it.
        pass

    return out


def resolve_kb_doc(doc_id: Optional[str]) -> Optional[dict]:
    """Resolve ANY KB doc_id (form or not) to {doc_id, title, url, source_url},
    or None. Unlike get_form(), this is not restricted to form-like docs, so it
    can resolve compliance hub pages / training docs to a live URL."""
    if not doc_id:
        return None
    row = _all_docs_by_id().get(doc_id)
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Download links for chat answers
#
# The chatbot could describe the PF-10 form and had no way to hand it over: the
# live datastore stores only category/doc_id/file_path/playwright_verified/
# source_file/subcategory/title, so procedure_url never reached the model. This
# resolves it in code instead.
#
# Deterministic on purpose. A DocuSign PowerForm URL is ~150 characters of
# opaque GUIDs; a model reproducing one will eventually corrupt a character and
# produce a plausible link to a dead page -- an error no grounding check catches,
# because the sentence around it is true. The model describes the form, this
# function supplies the URL.
# ---------------------------------------------------------------------------

_FILE_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
_FORM_HOSTS = ("docusign.net", "forms.gle", "docs.google.com/forms")


def _norm_title(s: str) -> str:
    return " ".join((s or "").split()).lower()


def _destination_kind(url: str) -> str:
    """file | form | link | page. Everything but "page" is worth handing over.

    The rule is "does this leave the ORA page it came from". Sources already
    cites that page, so a morgan.edu destination adds nothing -- but an external
    one is the thing itself and Sources will never show it. That distinction
    matters more than the file/form split: the seven e-training modules are
    hosted on Articulate, and treating them as ordinary web pages meant a PI
    asking about Time and Effort got a 474-character description of a module
    with no way to reach it.

    Also caught by the same rule: Google Drive documents, the Google Docs IRB
    form, the Panopto and YouTube recordings, and the five funding databases.
    """
    low = (url or "").lower()
    if any(h in low for h in _FORM_HOSTS):
        return "form"
    if low.split("?")[0].split("#")[0].endswith(_FILE_EXT):
        return "file"
    host = urlsplit(low).netloc
    if host and not host.endswith("morgan.edu"):
        return "link"
    return "page"


@lru_cache(maxsize=1)
def _docs_by_title() -> dict:
    """normalized title -> resolved doc row. Chunks carry titles, not doc_ids.

    Indexed under BOTH names a document can go by. `_all_docs_by_id` prefers
    `display_label`, but a retrieved chunk carries the datastore's `title`, and
    the two differ in practice — the PF-10 form is "PF-10 Contractual Personnel
    Request" on the /forms card and "PF-10 Contractual Personnel Request Form
    (DocuSign)" in the datastore. Keying on one alone silently resolves nothing,
    which looks exactly like the bug this feature exists to fix.
    """
    out = {}
    rows = _all_docs_by_id()

    def _add(name, row):
        key = _norm_title(name)
        if key and key not in out:
            out[key] = row

    for row in rows.values():
        _add(row.get("title"), row)

    # The snapshot's raw `title`, which _all_docs_by_id may have replaced with
    # display_label.
    if _MANIFEST_PATH.exists():
        with _MANIFEST_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row = rows.get(doc.get("doc_id"))
                if row:
                    _add(doc.get("title"), row)
                    _add(doc.get("display_label"), row)
    return out


def attachments_for_titles(titles, limit: int = 3) -> list:
    """The documents behind these retrieved chunk titles, as download links."""
    by_title = _docs_by_title()
    out, seen = [], set()
    for title in titles or []:
        row = by_title.get(_norm_title(title))
        if not row:
            continue
        url = (row.get("url") or "").strip()
        kind = _destination_kind(url)
        if kind == "page" or not url or url in seen:
            continue
        seen.add(url)
        out.append({"title": row.get("title") or title, "url": url, "kind": kind})
        if len(out) >= limit:
            break
    return out


_IMAGE_MATCH_STOPWORDS = {
    "about", "after", "also", "and", "are", "can", "does", "for", "from",
    "have", "how", "into", "morgan", "office", "ora", "project", "research",
    "should", "show", "sponsored", "state", "that", "the", "their", "this",
    "through", "university", "what", "when", "where", "which", "with", "would",
}


def _image_match_tokens(value: str) -> set[str]:
    """Small deterministic vocabulary for screenshot relevance checks.

    This is deliberately conservative. Missing a screenshot is harmless; showing
    a Banner screen under an unrelated answer makes the answer look ungrounded.
    """
    out = set()
    for raw in re.findall(r"[a-z0-9]+", (value or "").lower()):
        if len(raw) < 3 or raw in _IMAGE_MATCH_STOPWORDS:
            continue
        if raw.endswith("ies") and len(raw) > 5:
            raw = raw[:-3] + "y"
        elif raw.endswith("s") and not raw.endswith("ss") and len(raw) > 4:
            raw = raw[:-1]
        out.add(raw)
    return out


def images_for_titles(titles, limit: int = 4, query: str = "") -> list:
    """Screenshots belonging to the lessons behind these retrieved chunk titles.

    Same shape and the same call site as attachments_for_titles: the caller
    resolves once, from the documents the turn actually retrieved, and the model
    never sees a URL. Capped because a lesson can carry ten screenshots and an
    answer that dumps all of them is worse than one that shows the first few.

    When `query` is supplied (the chat path always supplies it), a cited lesson
    is not enough on its own. The lesson title must match at least two meaningful
    query terms before its whole screenshot sequence is eligible. Otherwise we
    return only individual images whose captions match the question. This keeps
    a broadly retrieved eTraining lesson from contributing random screenshots.
    The optional query preserves the catalog helper's non-chat callers.
    """
    by_title = _docs_by_title()
    out, seen = [], set()
    query_tokens = _image_match_tokens(query)
    for title in titles or []:
        row = by_title.get(_norm_title(title))
        images = (row or {}).get("images") or []
        if query_tokens:
            title_tokens = _image_match_tokens((row or {}).get("title") or title)
            whole_lesson_matches = len(query_tokens & title_tokens) >= 2
            if whole_lesson_matches:
                images = sorted(
                    enumerate(images),
                    key=lambda pair: (
                        -len(query_tokens & _image_match_tokens(pair[1].get("caption") or "")),
                        pair[0],
                    ),
                )
                images = [img for _, img in images]
            else:
                images = [
                    img for img in images
                    if query_tokens & _image_match_tokens(img.get("caption") or "")
                ]
        for img in images:
            url = (img.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"url": url, "caption": img.get("caption") or ""})
            if len(out) >= limit:
                return out
    return out
