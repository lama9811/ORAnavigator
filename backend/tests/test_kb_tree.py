"""Tests for the admin KB navigation tree (kb_tree.py).

Loaded by file path rather than by package import: importing the backend package
can pull in modules that need google-adk, which isn't always installed locally
(same reason test_agent_instruction.py is excluded from the standard run).
"""

import importlib.util
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("kb_tree", _BACKEND / "kb_tree.py")
kb_tree = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb_tree)


def _doc(doc_id, kb_path="", title=None, size=10):
    return {
        "id": doc_id,
        "filename": doc_id,
        "uri": f"structured://{doc_id}",
        "title": title or doc_id,
        "size": size,
        "category": "",
        "kb_path": kb_path,
    }


# ---------------------------------------------------------------------------
# Placement derivation
# ---------------------------------------------------------------------------

def test_manifest_placements_cover_every_document():
    placements = kb_tree.manifest_placements()
    # 382 files on disk; the 383rd JSONL row (form_irb_informed_consent_template)
    # has an empty file_path and no backing file, so it has no derivable path.
    assert len(placements) == 382


def test_every_derived_placement_is_a_real_node():
    """The backfill writes these paths verbatim — an unknown one would strand a
    document in Unfiled forever."""
    valid = kb_tree.node_paths()
    bad = {d: p for d, p in kb_tree.manifest_placements().items() if p and p not in valid}
    assert bad == {}


def test_depth_one_hub_lands_on_its_section():
    placements = kb_tree.manifest_placements()
    assert placements["pre_award_overview"] == "pre_award"


def test_nested_document_keeps_full_path():
    placements = kb_tree.manifest_placements()
    assert placements["ora_history"] == "about/history"


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------

def test_counts_come_from_live_docs_not_the_frozen_manifest():
    """The manifest's own doc_count was true on 2026-05-18. If the tree ever
    reports it instead of counting real documents, this fails."""
    result = kb_tree.build_tree([_doc("only_one", "pre_award/budget_development")])
    pre_award = next(n for n in result["tree"] if n["path"] == "pre_award")
    assert pre_award["count"] == 1        # not 30


def test_counts_roll_up_through_three_levels():
    docs = [
        _doc("a", "research_compliance/animal_research/iacuc_sops"),
        _doc("b", "research_compliance/animal_research/iacuc_forms"),
        _doc("c", "research_compliance"),
    ]
    result = kb_tree.build_tree(docs)
    rc = next(n for n in result["tree"] if n["path"] == "research_compliance")
    animal = next(n for n in rc["children"] if n["slug"] == "animal_research")
    assert animal["count"] == 2
    assert rc["count"] == 3


def test_full_manifest_placement_reproduces_known_section_totals():
    docs = [_doc(d, p) for d, p in kb_tree.manifest_placements().items()]
    result = kb_tree.build_tree(docs)
    counts = {n["path"]: n["count"] for n in result["tree"]}
    assert counts == {
        "about": 19,
        "funding_sources": 15,
        "pre_award": 30,
        "post_award": 15,
        "policies_and_guidelines": 22,
        "research_compliance": 146,
        "trainings": 115,
        "resources": 17,
        "ora_announcements": 3,
    }
    assert result["unfiled"] == []
    assert sum(counts.values()) == 382


# ---------------------------------------------------------------------------
# Unfiled — the whole point is that nothing is ever invisible
# ---------------------------------------------------------------------------

def test_document_with_no_kb_path_and_no_manifest_entry_is_unfiled():
    result = kb_tree.build_tree([_doc("fresh_upload")])
    assert [d["id"] for d in result["unfiled"]] == ["fresh_upload"]
    assert result["total"] == 1


# ---------------------------------------------------------------------------
# Manifest fallback — the tree works before the backfill has run
# ---------------------------------------------------------------------------

def test_known_document_without_kb_path_falls_back_to_the_manifest():
    result = kb_tree.build_tree([_doc("ora_history")])
    assert result["unfiled"] == []
    assert result["pending_backfill"] == 1
    about = next(n for n in result["tree"] if n["path"] == "about")
    assert about["count"] == 1


def test_kb_path_always_beats_the_manifest_fallback():
    """A placement set in the UI must never be overridden by the manifest."""
    result = kb_tree.build_tree([_doc("ora_history", "post_award/reporting")])
    post_award = next(n for n in result["tree"] if n["path"] == "post_award")
    about = next(n for n in result["tree"] if n["path"] == "about")
    assert post_award["count"] == 1
    assert about["count"] == 0
    assert result["pending_backfill"] == 0


def test_pending_backfill_reaches_zero_once_every_doc_carries_its_own_path():
    docs = [_doc(d, p) for d, p in kb_tree.manifest_placements().items()]
    assert kb_tree.build_tree(docs)["pending_backfill"] == 0


def test_fallback_can_be_switched_off():
    result = kb_tree.build_tree([_doc("ora_history")], fallback_to_manifest=False)
    assert [d["id"] for d in result["unfiled"]] == ["ora_history"]


def test_document_pointing_at_a_removed_node_is_unfiled_not_dropped():
    """When ORA reorganizes and the manifest is regenerated, orphans must
    surface rather than vanish."""
    result = kb_tree.build_tree([_doc("stranded", "pre_award/section_that_no_longer_exists")])
    assert [d["id"] for d in result["unfiled"]] == ["stranded"]


def test_no_document_is_ever_lost():
    docs = [
        _doc("filed", "pre_award"),
        _doc("unplaced"),
        _doc("stranded", "nope/not/real"),
    ]
    result = kb_tree.build_tree(docs)
    placed = sum(n["count"] for n in result["tree"])
    assert placed + len(result["unfiled"]) == len(docs) == result["total"]


def test_leading_and_trailing_slashes_are_tolerated():
    result = kb_tree.build_tree([_doc("x", "/pre_award/budget_development/")])
    assert result["unfiled"] == []


# ---------------------------------------------------------------------------
# Ordering — mirrors morgan.edu, not the alphabet
# ---------------------------------------------------------------------------

def test_root_order_matches_the_live_site_nav():
    result = kb_tree.build_tree([])
    assert [n["path"] for n in result["tree"]] == [
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


def test_pre_award_children_follow_the_site_not_the_alphabet():
    result = kb_tree.build_tree([])
    pre_award = next(n for n in result["tree"] if n["path"] == "pre_award")
    slugs = [c["slug"] for c in pre_award["children"]]
    assert slugs[0] == "university_application_information"
    assert slugs[-1] == "limited_submission"
    assert slugs != sorted(slugs)


def test_curated_titles_survive():
    """Titles like 'F&A Cost Rates' aren't derivable from the slug."""
    result = kb_tree.build_tree([])
    pre_award = next(n for n in result["tree"] if n["path"] == "pre_award")
    titles = {c["title"] for c in pre_award["children"]}
    assert "F&A Cost Rates" in titles


# ---------------------------------------------------------------------------
# Dropdown paths
# ---------------------------------------------------------------------------

def test_flat_paths_covers_every_node_exactly_once():
    flat = kb_tree.flat_paths()
    paths = [p["path"] for p in flat]
    assert len(paths) == len(set(paths))
    assert set(paths) == kb_tree.node_paths()


def test_flat_paths_are_indented_by_depth():
    by_path = {p["path"]: p for p in kb_tree.flat_paths()}
    assert by_path["pre_award"]["depth"] == 0
    assert by_path["pre_award/budget_development"]["depth"] == 1
    assert by_path["research_compliance/animal_research/iacuc_sops"]["depth"] == 2


@pytest.mark.parametrize("path", ["pre_award", "research_compliance/animal_research"])
def test_node_paths_are_assignable_targets(path):
    assert path in kb_tree.node_paths()


def test_invented_path_is_not_assignable():
    assert "pre_award/made_up" not in kb_tree.node_paths()


# ---------------------------------------------------------------------------
# doc_id generation for authored documents
# ---------------------------------------------------------------------------

def test_doc_id_is_slugified_and_section_prefixed():
    assert kb_tree.suggest_doc_id("Cost Sharing Guidance", "pre_award/budget_development") \
        == "preaward_cost_sharing_guidance"


def test_doc_id_prefix_follows_the_section_not_the_subsection():
    assert kb_tree.suggest_doc_id("Animal Care", "research_compliance/animal_research/iacuc_sops") \
        == "compliance_animal_care"


def test_doc_id_strips_punctuation_and_collapses_separators():
    assert kb_tree.suggest_doc_id("F&A Rates — 2026/2027 (Final)!", "pre_award") \
        == "preaward_f_a_rates_2026_2027_final"


def test_doc_id_does_not_double_up_an_existing_prefix():
    assert kb_tree.suggest_doc_id("Preaward Spending", "pre_award") == "preaward_spending"


def test_doc_id_without_a_section_is_unprefixed():
    assert kb_tree.suggest_doc_id("Cost Sharing Guidance") == "cost_sharing_guidance"


def test_doc_id_is_empty_when_the_title_has_nothing_usable():
    assert kb_tree.suggest_doc_id("!!!  ???", "pre_award") == ""


def test_doc_id_is_length_capped():
    assert len(kb_tree.suggest_doc_id("word " * 100, "pre_award")) <= 120


def test_generated_ids_do_not_collide_with_seeded_ones():
    """A generated id landing on an existing document would be rejected at
    create time; this checks the prefix scheme keeps that rare."""
    existing = set(kb_tree.manifest_placements())
    assert kb_tree.suggest_doc_id("Cost Sharing Guidance", "pre_award") not in existing


# ---------------------------------------------------------------------------
# Pending scrape proposals — badged by joining, never by touching the document
# ---------------------------------------------------------------------------

def _proposal(what="Rate changed 26% -> 27%", draft=True):
    return {"change_id": 1, "what_changed": what, "confidence": "high",
            "has_draft": draft, "url": "https://www.morgan.edu/ora"}


def test_pending_proposal_is_attached_to_its_document():
    docs = [_doc("ora_history", "about/history")]
    result = kb_tree.build_tree(docs, pending={"ora_history": _proposal()})
    about = next(n for n in result["tree"] if n["path"] == "about")
    history = next(c for c in about["children"] if c["slug"] == "history")
    assert history["docs"][0]["pending_change"]["what_changed"].startswith("Rate changed")


def test_documents_without_a_proposal_carry_none():
    result = kb_tree.build_tree([_doc("ora_history", "about/history")], pending={})
    about = next(n for n in result["tree"] if n["path"] == "about")
    history = next(c for c in about["children"] if c["slug"] == "history")
    assert history["docs"][0]["pending_change"] is None


def test_pending_count_rolls_up_so_a_collapsed_section_still_shows_it():
    docs = [
        _doc("a", "research_compliance/animal_research/iacuc_sops"),
        _doc("b", "research_compliance/animal_research/iacuc_forms"),
        _doc("c", "research_compliance/human_subjects_research"),
    ]
    result = kb_tree.build_tree(docs, pending={"a": _proposal(), "b": _proposal()})
    rc = next(n for n in result["tree"] if n["path"] == "research_compliance")
    animal = next(c for c in rc["children"] if c["slug"] == "animal_research")
    assert animal["pending_count"] == 2
    assert rc["pending_count"] == 2          # rolls up from two different leaves
    assert result["pending_total"] == 2


def test_pending_total_ignores_proposals_for_documents_that_are_gone():
    """A proposal against a deleted document must not inflate the badge count."""
    result = kb_tree.build_tree(
        [_doc("ora_history", "about/history")],
        pending={"ora_history": _proposal(), "deleted_doc": _proposal()},
    )
    assert result["pending_total"] == 1


def test_a_proposal_does_not_alter_the_document_row_itself():
    """The badge is a join. Nothing about the document changes until approval —
    that is the whole contract of the review-then-approve design."""
    docs = [_doc("ora_history", "about/history", title="ORA History", size=1234)]
    result = kb_tree.build_tree(docs, pending={"ora_history": _proposal()})
    about = next(n for n in result["tree"] if n["path"] == "about")
    row = next(c for c in about["children"] if c["slug"] == "history")["docs"][0]
    assert row["title"] == "ORA History"
    assert row["size"] == 1234
    assert row["id"] == "ora_history"


def test_pending_backfill_and_pending_proposals_are_different_things():
    """Both were briefly called `pending` in the same function and collided."""
    result = kb_tree.build_tree(
        [_doc("ora_history")],                      # no kb_path -> manifest fallback
        pending={"ora_history": _proposal()},
    )
    assert result["pending_backfill"] == 1          # relying on the manifest
    assert result["pending_total"] == 1             # has a scrape proposal
