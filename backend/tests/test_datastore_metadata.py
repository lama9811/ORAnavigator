"""Metadata-safety tests for datastore_manager.

These pin two behaviours that are easy to regress and expensive when they go
wrong, because both fail SILENTLY: a wiped document still saves successfully and
still answers questions — it just loses its title, category and tree placement.
"""

import importlib.util
from pathlib import Path

import pytest
from google.api_core.exceptions import NotFound, ServiceUnavailable

_BACKEND = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("datastore_manager", _BACKEND / "datastore_manager.py")
dm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dm)


class FakeContent:
    def __init__(self, raw_bytes=b"body", mime_type="text/plain"):
        self.raw_bytes = raw_bytes
        self.mime_type = mime_type


class FakeDoc:
    def __init__(self, struct_data=None, content=None):
        self.struct_data = struct_data or {}
        self.content = content


class FakeClient:
    """Records what would be written, so tests can assert on struct_data."""

    def __init__(self, existing=None, get_error=None):
        self._existing = existing
        self._get_error = get_error
        self.written = []

    def get_document(self, name):
        if self._get_error:
            raise self._get_error
        if self._existing is None:
            raise NotFound("missing")
        return self._existing

    def update_document(self, request):
        self.written.append(request.document)
        return request.document


@pytest.fixture(autouse=True)
def _no_cache_side_effects(monkeypatch):
    monkeypatch.setattr(dm, "invalidate_content_cache", lambda: None)


def _install(monkeypatch, client):
    monkeypatch.setattr(dm, "_get_doc_client", lambda: client)
    return client


FULL_METADATA = {
    "doc_id": "preaward_budget_development",
    "title": "Budget Development",
    "category": "pre_award",
    "subcategory": "budget",
    "kb_path": "pre_award/budget_development",
    "file_path": "pre_award/budget_development/preaward_budget_development.json",
}


# ---------------------------------------------------------------------------
# update_document — the read-modify-write that used to erase metadata
# ---------------------------------------------------------------------------

def test_update_preserves_all_existing_metadata(monkeypatch):
    client = _install(monkeypatch, FakeClient(existing=FakeDoc(dict(FULL_METADATA))))
    result = dm.update_document("preaward_budget_development", b"new body")

    assert result["success"] is True
    written = dict(client.written[0].struct_data)
    for key, value in FULL_METADATA.items():
        assert written[key] == value


def test_read_failure_refuses_to_write_rather_than_wiping_metadata(monkeypatch):
    """A transient read error used to be swallowed into `data = {}`, which was
    then patched over struct_data — silently erasing title, category, kb_path
    and file_path. A failed save is recoverable; a wiped document is not."""
    client = _install(monkeypatch, FakeClient(get_error=ServiceUnavailable("backend blip")))
    result = dm.update_document("preaward_budget_development", b"new body")

    assert result["success"] is False
    assert client.written == [], "must not write when existing metadata could not be read"


def test_missing_document_is_still_a_legitimate_create(monkeypatch):
    """NotFound is different from a read failure: allow_missing=True means this
    path doubles as create, so empty metadata is correct here."""
    client = _install(monkeypatch, FakeClient(existing=None))
    result = dm.update_document("brand_new_doc", b"body")

    assert result["success"] is True
    assert len(client.written) == 1


def test_content_is_stripped_from_struct_data(monkeypatch):
    existing = FakeDoc({**FULL_METADATA, "content": "stale copy"})
    client = _install(monkeypatch, FakeClient(existing=existing))
    dm.update_document("preaward_budget_development", b"new body")

    assert "content" not in dict(client.written[0].struct_data)


# ---------------------------------------------------------------------------
# update_placement
# ---------------------------------------------------------------------------

def test_placement_sets_kb_path_and_keeps_everything_else(monkeypatch):
    existing = FakeDoc(dict(FULL_METADATA), FakeContent(b"the body"))
    client = _install(monkeypatch, FakeClient(existing=existing))

    result = dm.update_placement("preaward_budget_development", "post_award/reporting")
    assert result["success"] is True

    written = dict(client.written[0].struct_data)
    assert written["kb_path"] == "post_award/reporting"
    assert written["title"] == "Budget Development"
    assert written["category"] == "pre_award"


def test_placement_carries_existing_content_through_untouched(monkeypatch):
    """This is a metadata-only write; omitting content would blank the
    searchable body and quietly break the document in chat."""
    existing = FakeDoc(dict(FULL_METADATA), FakeContent(b"the body", "text/plain"))
    client = _install(monkeypatch, FakeClient(existing=existing))

    dm.update_placement("preaward_budget_development", "post_award/reporting")
    assert client.written[0].content.raw_bytes == b"the body"


def test_empty_path_unfiles_the_document(monkeypatch):
    existing = FakeDoc(dict(FULL_METADATA), FakeContent())
    client = _install(monkeypatch, FakeClient(existing=existing))

    dm.update_placement("preaward_budget_development", "")
    written = dict(client.written[0].struct_data)
    assert "kb_path" not in written
    assert written["title"] == "Budget Development"


def test_placement_on_missing_document_fails_without_creating_one(monkeypatch):
    client = _install(monkeypatch, FakeClient(existing=None))
    result = dm.update_placement("ghost", "pre_award")

    assert result["success"] is False
    assert client.written == []


# ---------------------------------------------------------------------------
# upload_document — no more guessed categories
# ---------------------------------------------------------------------------

def test_upload_does_not_invent_a_category(monkeypatch):
    """It used to guess from an academic|career|financial prefix list that no
    ORA document matches, labelling every upload 'general'. Better to write no
    placement and let it surface under Unfiled."""
    client = _install(monkeypatch, FakeClient(existing=None))
    dm.upload_document("nsf_2026_guidance.txt", b"content")

    written = dict(client.written[0].struct_data)
    assert "category" not in written
    assert "kb_path" not in written
    assert written["title"] == "Nsf 2026 Guidance"


def test_authored_document_carries_a_full_metadata_set(monkeypatch):
    """An authored document must be indistinguishable from a scraped one, or it
    will render without a category badge and sit outside the tree."""
    client = _install(monkeypatch, FakeClient(existing=None))
    result = dm.create_kb_document(
        "preaward_cost_sharing",
        "Cost Sharing Guidance",
        "Morgan requires documented cost sharing approval.",
        kb_path="pre_award/budget_development",
        source_url="https://www.morgan.edu/ora/cost-sharing",
    )

    assert result["success"] is True
    written = dict(client.written[0].struct_data)
    assert written["doc_id"] == "preaward_cost_sharing"
    assert written["title"] == "Cost Sharing Guidance"
    assert written["kb_path"] == "pre_award/budget_development"
    assert written["category"] == "pre_award"          # derived from the path
    assert written["subcategory"] == "budget_development"
    assert written["source_url"] == "https://www.morgan.edu/ora/cost-sharing"
    assert written["authored_in_dashboard"] is True


def test_authored_content_is_the_searchable_body(monkeypatch):
    client = _install(monkeypatch, FakeClient(existing=None))
    dm.create_kb_document("d", "T", "the body text", kb_path="pre_award")
    assert client.written[0].content.raw_bytes == b"the body text"


def test_create_refuses_to_overwrite_an_existing_document(monkeypatch):
    """Creation must never clobber a seeded document that happens to slugify to
    the same id — update_document is the edit path."""
    client = _install(monkeypatch, FakeClient(existing=FakeDoc(dict(FULL_METADATA))))
    result = dm.create_kb_document("preaward_budget_development", "Budget", "body")

    assert result["success"] is False
    assert "already exists" in result["message"]
    assert client.written == []


def test_create_without_a_section_leaves_it_unfiled(monkeypatch):
    client = _install(monkeypatch, FakeClient(existing=None))
    dm.create_kb_document("loose_doc", "Loose Doc", "body")

    written = dict(client.written[0].struct_data)
    assert "kb_path" not in written
    assert "category" not in written


@pytest.mark.parametrize(
    "title,content",
    [("", "body"), ("   ", "body"), ("Title", ""), ("Title", "   ")],
)
def test_create_rejects_empty_title_or_content(monkeypatch, title, content):
    client = _install(monkeypatch, FakeClient(existing=None))
    result = dm.create_kb_document("d", title, content, kb_path="pre_award")

    assert result["success"] is False
    assert client.written == []


def test_uploaded_binary_content_does_not_crash_on_decode(monkeypatch):
    """A .pdf is an allowed upload type; the old code eagerly utf-8 decoded the
    bytes into an unused variable and raised before it ever reached the handler's
    error path."""
    client = _install(monkeypatch, FakeClient(existing=None))
    result = dm.upload_document("scanned.pdf", b"%PDF-1.4\x00\xff\xfe binary")

    assert result["success"] is True


# ---------------------------------------------------------------------------
# procedure_url — the download link. Without it a document is created, answered
# from, and the file it came from cannot be reached.
# ---------------------------------------------------------------------------

def test_create_kb_document_stores_the_download_link(monkeypatch):
    captured = {}

    class _Client:
        def update_document(self, request):
            captured["struct"] = dict(request.document.struct_data)
            return object()

    import datastore_manager as dm
    monkeypatch.setattr(dm, "_get_doc_client", lambda: _Client())
    monkeypatch.setattr(dm, "document_exists", lambda doc_id: False)
    monkeypatch.setattr(dm, "invalidate_content_cache", lambda: None)

    result = dm.create_kb_document(
        doc_id="form_x", title="Form X", content="body",
        kb_path="post_award/forms",
        source_url="https://www.morgan.edu/office-of-research-administration/post-award/forms",
        procedure_url="https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/x.pdf",
    )
    assert result["success"]
    assert captured["struct"]["procedure_url"].endswith("/x.pdf")
    assert captured["struct"]["source_url"].endswith("/post-award/forms")


def test_create_kb_document_omits_procedure_url_when_absent(monkeypatch):
    captured = {}

    class _Client:
        def update_document(self, request):
            captured["struct"] = dict(request.document.struct_data)
            return object()

    import datastore_manager as dm
    monkeypatch.setattr(dm, "_get_doc_client", lambda: _Client())
    monkeypatch.setattr(dm, "document_exists", lambda doc_id: False)
    monkeypatch.setattr(dm, "invalidate_content_cache", lambda: None)

    dm.create_kb_document(doc_id="d", title="T", content="c")
    assert "procedure_url" not in captured["struct"]
