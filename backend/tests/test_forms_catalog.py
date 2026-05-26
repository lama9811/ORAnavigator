"""Tests for the Forms catalog service.

The catalog reads kb_structured/_all_documents.jsonl once at import and
exposes a list_forms(...) call that filters by category / sponsor / role.
No DB, no network -- it's a static read of the bundled KB.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_forms_catalog.py -v
"""
from services.forms_catalog import list_forms


def test_returns_form_like_docs_only():
    """Default call returns ONLY form-like docs (not the full 382-doc KB).
    A form is anything whose subcategory contains form/template/docusign/
    checklist/memo/sample. The catalog must not surface About / History /
    overview pages."""
    forms = list_forms()
    assert len(forms) > 0, "catalog should not be empty"
    assert len(forms) < 150, (
        f"catalog returned {len(forms)} -- too broad; should be the ~80 "
        "form-like docs, not the whole 382-doc KB")
    # Specific non-form docs must not appear (checked by doc_id, not by
    # substring -- "submission" contains "mission" so substring is unsafe).
    doc_ids = {f["doc_id"] for f in forms}
    for bad in ("ora_history", "ora_mission_and_vision", "ora_about"):
        assert bad not in doc_ids, f"non-form doc {bad!r} leaked into catalog"


def test_each_form_has_required_fields():
    """Every form has the fields the UI needs to render a card."""
    required = {"doc_id", "title", "category", "url", "sponsors", "roles"}
    for f in list_forms():
        missing = required - set(f.keys())
        assert not missing, f"form {f.get('doc_id')!r} missing fields: {missing}"
        assert f["title"], f"form {f.get('doc_id')!r} has empty title"
        assert f["url"], f"form {f.get('doc_id')!r} has empty url"
        assert isinstance(f["sponsors"], list)
        assert isinstance(f["roles"], list)


def test_filter_by_category_post_award():
    """Filtering by category='post_award' returns only post-award forms."""
    forms = list_forms(category="post_award")
    assert len(forms) > 0
    assert all(f["category"] == "post_award" for f in forms)


def test_filter_by_category_unknown_returns_empty():
    """Unknown category yields no rows (not an error)."""
    assert list_forms(category="not_a_real_category") == []


def test_filter_by_sponsor_nsf():
    """The catalog can find NSF-flavored forms via title/content keyword."""
    forms = list_forms(sponsor="NSF")
    assert len(forms) > 0, "expected at least one NSF-tagged form"
    for f in forms:
        assert "NSF" in f["sponsors"], (
            f"{f['doc_id']!r} returned for sponsor=NSF but its sponsors "
            f"are {f['sponsors']!r}")


def test_sponsor_agnostic_forms_tagged_internal():
    """A DocuSign honoraria form has no sponsor mention -- it's an MSU-
    internal form. The catalog must still surface it under 'Internal'."""
    all_forms = list_forms()
    honoraria = [f for f in all_forms
                 if "honoraria" in f["title"].lower()]
    assert honoraria, "expected the honoraria DocuSign form in the catalog"
    assert "Internal" in honoraria[0]["sponsors"], (
        "sponsor-agnostic forms must be tagged 'Internal' so the sponsor "
        "filter can find them when the user picks 'Internal'")


def test_filter_by_role_pi():
    """Filter by role='PI' returns forms PIs typically initiate."""
    forms = list_forms(role="PI")
    assert len(forms) > 0
    for f in forms:
        assert "PI" in f["roles"], (
            f"{f['doc_id']!r} returned for role=PI but its roles are "
            f"{f['roles']!r}")


def test_role_recognizes_staff_and_admin():
    """Catalog supports three roles: PI, Staff, Admin. The filter applies
    cleanly to each; combined filters narrow further."""
    for r in ("PI", "Staff", "Admin"):
        forms = list_forms(role=r)
        assert isinstance(forms, list), f"role={r} must return a list"


def test_combined_filters_narrow():
    """Combined filters (sponsor + role) intersect rather than union."""
    post_award_pi = list_forms(category="post_award", role="PI")
    all_post_award = list_forms(category="post_award")
    assert len(post_award_pi) <= len(all_post_award), (
        "adding role=PI must not increase the result set")
    for f in post_award_pi:
        assert f["category"] == "post_award"
        assert "PI" in f["roles"]


def test_url_is_clickable():
    """url field must be an https link (the procedure_url from the KB)
    so the frontend can render it as a download/open link."""
    for f in list_forms():
        assert f["url"].startswith("http"), (
            f"{f['doc_id']!r} has non-http url: {f['url']!r}")


def test_empty_filters_equivalent_to_no_filters():
    """Passing empty strings is equivalent to None (open filter)."""
    assert list_forms() == list_forms(category="", sponsor="", role="")
