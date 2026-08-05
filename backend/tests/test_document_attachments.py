"""Tests for handing the actual document to whoever asked for it.

The bug this closes: a PI asked for the PF-10 form and got an accurate
description with no form. The link was in the KB document's procedure_url all
along, but the live datastore never stored that field, so the model had no link
to give. These tests cover the deterministic resolution that replaces it —
and, just as importantly, the turns that must get NO attachment.
"""

import pytest

from services import forms_catalog as fc

_DOCS = {
    "form_pf10": {
        "doc_id": "form_pf10",
        "title": "PF-10 Contractual Personnel Request Form (DocuSign)",
        "url": "https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=abc",
        "source_url": "https://www.morgan.edu/office-of-research-administration/post-award/forms",
    },
    "fa_rates": {
        "doc_id": "fa_rates",
        "title": "F&A Cost Rates",
        "url": "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/IDC/rates.pdf",
        "source_url": "https://www.morgan.edu/office-of-research-administration/pre-award/f-a-cost-rates",
    },
    "page_only": {
        "doc_id": "page_only",
        "title": "Pre-Award Overview",
        "url": "https://www.morgan.edu/office-of-research-administration/pre-award",
        "source_url": "https://www.morgan.edu/office-of-research-administration/pre-award",
    },
}


@pytest.fixture(autouse=True)
def _fake_docs(monkeypatch):
    monkeypatch.setattr(fc, "_all_docs_by_id", lambda: _DOCS)
    fc._docs_by_title.cache_clear()
    yield
    fc._docs_by_title.cache_clear()


def test_a_form_document_produces_one_attachment_with_the_exact_url():
    out = fc.attachments_for_titles(["PF-10 Contractual Personnel Request Form (DocuSign)"])
    assert out == [{
        "title": "PF-10 Contractual Personnel Request Form (DocuSign)",
        "url": "https://na2.docusign.net/Member/PowerFormSigning.aspx?PowerFormId=abc",
        "kind": "form",
    }]


def test_a_file_destination_is_attached_as_a_file():
    out = fc.attachments_for_titles(["F&A Cost Rates"])
    assert out[0]["kind"] == "file"
    assert out[0]["url"].endswith("/rates.pdf")


def test_a_web_page_destination_is_not_attached():
    """Sources already covers the page. Documents means the thing itself."""
    assert fc.attachments_for_titles(["Pre-Award Overview"]) == []


def test_unknown_titles_are_ignored():
    assert fc.attachments_for_titles(["Something Nobody Wrote"]) == []


def test_no_titles_produces_nothing():
    assert fc.attachments_for_titles([]) == []
    assert fc.attachments_for_titles(None) == []


def test_titles_match_despite_whitespace_and_case():
    assert len(fc.attachments_for_titles(["  f&a   COST rates  "])) == 1


def test_duplicates_collapse_and_order_is_preserved():
    out = fc.attachments_for_titles([
        "F&A Cost Rates",
        "PF-10 Contractual Personnel Request Form (DocuSign)",
        "F&A Cost Rates",
    ])
    assert [a["title"] for a in out] == [
        "F&A Cost Rates",
        "PF-10 Contractual Personnel Request Form (DocuSign)",
    ]


def test_the_cap_is_respected():
    out = fc.attachments_for_titles(
        ["F&A Cost Rates", "PF-10 Contractual Personnel Request Form (DocuSign)"],
        limit=1,
    )
    assert len(out) == 1
