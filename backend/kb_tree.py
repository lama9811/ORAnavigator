"""
Admin KB navigation tree.

Assembles the browsable hierarchy behind the admin dashboard's Cloud Datastore
panel by joining two sources that each own exactly one thing:

    _manifest.json  -> SHAPE and TITLES. The 64-node nesting mirroring
                       morgan.edu/ora, plus curated display names that are not
                       derivable from slugs ("Animal Research (IACUC)",
                       "F&A Cost Rates", "Test Prep (RACC)").

    struct_data     -> PLACEMENT. Each live document's own `kb_path` field, e.g.
    .kb_path           "research_compliance/animal_research/iacuc_sops".

Counts are computed from the LIVE documents, never from the manifest's frozen
`doc_count` — those were accurate on 2026-05-18 and would start lying the moment
anything is filed or uploaded.

A document whose kb_path is empty, or points at a node that no longer exists,
lands in `unfiled` rather than disappearing. That second case is what keeps this
honest when ORA reorganizes their site and the manifest is regenerated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_MANIFEST_PATH = Path(__file__).parent / "kb_structured" / "_manifest.json"

_MANIFEST: Optional[dict] = None
_NODES: dict[str, dict] = {}        # path -> manifest node


# ---------------------------------------------------------------------------
# Display order
#
# The manifest sorts children alphabetically; morgan.edu uses a curated order
# that follows the actual workflow (you read "University Application
# Information" before "Budget Development"). Mirror the site — anything not
# listed keeps its manifest position and sorts after everything that is.
# Verified against the live nav on 2026-07-28.
# ---------------------------------------------------------------------------
_ROOT_ORDER = [
    "about",
    "funding_sources",
    "pre_award",
    "post_award",
    "policies_and_guidelines",
    "research_compliance",
    "trainings",
    "resources",
    "ora_announcements",
]

_CHILD_ORDER: dict[str, list[str]] = {
    "pre_award": [
        "university_application_information",
        "role_of_principal_investigator",
        "proposal_components",
        "proposal_submission_checklist",
        "internal_routing_form",
        "budget_development",
        "fringe_benefit_rate",
        "fanda_cost_rates",
        "proposal_and_budget_examples",
        "pre_award_spending",
        "pre_award_subawards",
        "limited_submission",
    ],
    "research_compliance": [
        "human_subjects_research",
        "animal_research",
        "conflict_of_interest",
        "responsible_conduct_of_research",
        "research_misconduct",
        "research_security",
        "state_of_maryland_ethics_and_financial_disclosure",
        "diversity_and_eeo",
        "drug_alcohol_and_tobacco_policies",
        "research_compliance_updates_and_news",
    ],
}


def _index(nodes: list[dict]) -> None:
    for n in nodes:
        _NODES[n["path"]] = n
        if n.get("children"):
            _index(n["children"])


def _load() -> dict:
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = json.loads(_MANIFEST_PATH.read_text())
        _index(_MANIFEST.get("tree", []))
    return _MANIFEST


def node_paths() -> set[str]:
    """Every assignable tree path. Placement writes are validated against this."""
    _load()
    return set(_NODES)


def _sorted_children(parent_path: str, children: list[dict]) -> list[dict]:
    order = _CHILD_ORDER.get(parent_path)
    if not order:
        return children
    rank = {slug: i for i, slug in enumerate(order)}
    return sorted(children, key=lambda c: rank.get(c["slug"], len(order)))


def manifest_placements() -> dict[str, str]:
    """doc_id -> kb_path, derived from each manifest entry's file_path directory.

    This is the seed for the one-time backfill. It is NOT used to render the
    tree — once documents carry their own kb_path, the manifest stops being the
    placement authority.

        "pre_award/budget_development/preaward_budget.json"
            -> "pre_award/budget_development"
        "pre_award/pre_award_overview.json"      (a depth-1 section hub)
            -> "pre_award"
    """
    _load()
    out: dict[str, str] = {}
    for path, node in _NODES.items():
        for doc in node.get("docs", []):
            file_path = doc.get("file_path") or ""
            if not file_path:
                continue        # e.g. form_irb_informed_consent_template
            parent = str(Path(file_path).parent)
            out[doc["doc_id"]] = "" if parent == "." else parent
    return out


def build_tree(docs: list[dict], fallback_to_manifest: bool = True) -> dict:
    """Join live documents onto the manifest shape.

    `docs` are rows from datastore_manager.list_datastore_documents().
    Returns {"tree": [...], "unfiled": [...], "total": n, "pending_backfill": n}.

    Placement precedence:
      1. the document's own kb_path            (authoritative; what the UI writes)
      2. the manifest's derived placement      (transitional, pre-backfill)
      3. unfiled

    Step 2 is why the tree works before the backfill has run, and why the
    backfill is a migration rather than a prerequisite. It never overrides a
    kb_path, so a placement set in the UI always wins. `pending_backfill` counts
    the documents still relying on it — once that reaches 0 the manifest has no
    say in placement at all.
    """
    _load()
    valid = set(_NODES)
    inherited = manifest_placements() if fallback_to_manifest else {}

    by_path: dict[str, list[dict]] = {}
    unfiled: list[dict] = []
    pending = 0
    for d in docs:
        kb_path = (d.get("kb_path") or "").strip("/")
        if not kb_path:
            kb_path = inherited.get(d["id"], "")
            if kb_path:
                pending += 1
        if kb_path and kb_path in valid:
            by_path.setdefault(kb_path, []).append(d)
        else:
            unfiled.append(d)

    def render(node: dict) -> dict:
        here = sorted(by_path.get(node["path"], []), key=lambda d: d.get("title") or d["id"])
        children = [
            render(c) for c in _sorted_children(node["path"], node.get("children", []))
        ]
        return {
            "path": node["path"],
            "slug": node["slug"],
            "title": node["title"],
            "docs": [
                {
                    "id": d["id"],
                    "filename": d.get("filename", d["id"]),
                    "uri": d.get("uri", ""),
                    "title": d.get("title") or d["id"],
                    "size": d.get("size", 0),
                    "category": d.get("category", ""),
                    "needs_review": bool(d.get("needs_review")),
                    "last_auto_updated": d.get("last_auto_updated", ""),
                    "what_changed": d.get("what_changed", ""),
                }
                for d in here
            ],
            "children": children,
            # Recursive live count — what is actually in the datastore right now.
            "count": len(here) + sum(c["count"] for c in children),
            # Recursive count of auto-updated documents awaiting review, so a
            # collapsed section still shows there is something to look at.
            "review_count": sum(1 for d in here if d.get("needs_review"))
                            + sum(c["review_count"] for c in children),
        }

    roots = _sorted_children("", _load().get("tree", []))
    rank = {slug: i for i, slug in enumerate(_ROOT_ORDER)}
    roots = sorted(roots, key=lambda n: rank.get(n["slug"], len(_ROOT_ORDER)))

    return {
        "tree": [render(r) for r in roots],
        "unfiled": sorted(
            (
                {
                    "id": d["id"],
                    "filename": d.get("filename", d["id"]),
                    "uri": d.get("uri", ""),
                    "title": d.get("title") or d["id"],
                    "size": d.get("size", 0),
                    "category": d.get("category", ""),
                    "needs_review": bool(d.get("needs_review")),
                    "last_auto_updated": d.get("last_auto_updated", ""),
                    "what_changed": d.get("what_changed", ""),
                }
                for d in unfiled
            ),
            key=lambda d: d["title"],
        ),
        "total": len(docs),
        "pending_backfill": pending,
        "needs_review_total": sum(1 for d in docs if d.get("needs_review")),
    }


# Per-section doc_id prefixes. The KB's own prefixes are inconsistent — "form_"
# leads in five of the nine sections because so many documents are forms — so
# authored documents get one predictable prefix per section instead. The admin
# sees the generated id live in the form before creating anything.
_ID_PREFIX = {
    "about": "ora",
    "funding_sources": "opportunity",
    "ora_announcements": "announcement",
    "policies_and_guidelines": "policy",
    "post_award": "postaward",
    "pre_award": "preaward",
    "research_compliance": "compliance",
    "resources": "resources",
    "trainings": "training",
}


def suggest_doc_id(title: str, kb_path: str = "") -> str:
    """Slugify a title into a doc_id, prefixed by its section.

        ("Cost Sharing Guidance", "pre_award/budget_development")
            -> "preaward_cost_sharing_guidance"
    """
    import re

    slug = re.sub(r"[^a-z0-9]+", "_", title.strip().lower()).strip("_")
    if not slug:
        return ""
    prefix = _ID_PREFIX.get(kb_path.split("/")[0], "") if kb_path else ""
    if prefix and not slug.startswith(prefix):
        slug = f"{prefix}_{slug}"
    return slug[:120]


def flat_paths() -> list[dict]:
    """Assignable paths as a flat, indented list for the 'Place in…' dropdown."""
    _load()
    out: list[dict] = []

    def walk(node: dict, depth: int) -> None:
        out.append({
            "path": node["path"],
            "title": node["title"],
            "depth": depth,
            "label": ("  " * depth) + node["title"],
        })
        for c in _sorted_children(node["path"], node.get("children", [])):
            walk(c, depth + 1)

    roots = _load().get("tree", [])
    rank = {slug: i for i, slug in enumerate(_ROOT_ORDER)}
    for r in sorted(roots, key=lambda n: rank.get(n["slug"], len(_ROOT_ORDER))):
        walk(r, 0)
    return out
