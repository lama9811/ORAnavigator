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


# ---------------------------------------------------------------------------
# Suppression. Attachments must be cleared on exactly the turns citations are
# cleared -- a DocuSign form offered in reply to "how are you" is the July
# Sources-on-smalltalk bug in a more embarrassing form.
# ---------------------------------------------------------------------------

def _result():
    return {"citations": [{"title": "F&A Cost Rates", "url": "https://p"}]}


def test_a_small_talk_turn_gets_no_attachments():
    import vertex_agent as va
    assert va._attachments_for_result("thanks!", "You're welcome!", _result()) == []


def test_a_refusal_gets_no_attachments():
    import vertex_agent as va
    out = va._attachments_for_result(
        "who is the president of the US?",
        "I can only help with Morgan State University Office of Research Administration questions.",
        _result(),
    )
    assert out == []


def test_a_personal_identity_turn_gets_no_attachments():
    import vertex_agent as va
    out = va._attachments_for_result(
        "what department am I in?", "You are in the Physics department.", _result()
    )
    assert out == []


def test_a_real_kb_answer_gets_its_document():
    import vertex_agent as va
    out = va._attachments_for_result(
        "what is the on-campus F&A rate?",
        "The on-campus facilities and administrative rate is 54%.",
        _result(),
    )
    assert out and out[0]["url"].endswith("rates.pdf")


def test_chunk_titles_survives_a_result_with_no_citations():
    import vertex_agent as va
    assert va._chunk_titles({}) == []
    assert va._chunk_titles({"citations": []}) == []


# ---------------------------------------------------------------------------
# Cache. Without a parallel key the second person to ask for PF-10 gets the
# answer and no form.
# ---------------------------------------------------------------------------

def test_attachments_round_trip_through_the_cache():
    from cache import query_cache
    atts = [{"title": "F&A Cost Rates", "url": "https://x/rates.pdf", "kind": "file"}]
    query_cache.set_attachments("what is the f&a rate?", atts)
    assert query_cache.get_attachments("what is the f&a rate?") == atts


def test_missing_attachments_return_an_empty_list_not_none():
    from cache import query_cache
    assert query_cache.get_attachments("never asked before xyzzy plugh") == []


def test_attachments_and_citations_do_not_collide():
    from cache import query_cache
    q = "collision check question"
    query_cache.set_citations(q, [{"title": "A page", "url": "https://p"}])
    query_cache.set_attachments(q, [{"title": "A file", "url": "https://f.pdf", "kind": "file"}])
    assert query_cache.get_citations(q)[0]["title"] == "A page"
    assert query_cache.get_attachments(q)[0]["title"] == "A file"


# ---------------------------------------------------------------------------
# A document goes by two names and chunks carry the one _all_docs_by_id drops.
# Caught against real KB data, not fixtures: the PF-10 form is "PF-10
# Contractual Personnel Request" on the /forms card (display_label) and "PF-10
# Contractual Personnel Request Form (DocuSign)" in the datastore (title).
# Indexing one alone silently resolves nothing — indistinguishable from the bug
# this feature exists to fix.
# ---------------------------------------------------------------------------

def test_the_real_pf10_form_resolves_under_both_of_its_names(monkeypatch):
    """No fixture: this runs against the committed snapshot."""
    monkeypatch.undo()
    fc._docs_by_title.cache_clear()
    try:
        for name in (
            "PF-10 Contractual Personnel Request Form (DocuSign)",
            "PF-10 Contractual Personnel Request",
        ):
            out = fc.attachments_for_titles([name])
            assert out, f"no attachment resolved for {name!r}"
            assert "docusign.net" in out[0]["url"]
            assert out[0]["kind"] == "form"
    finally:
        fc._docs_by_title.cache_clear()


def test_a_document_whose_procedure_url_is_its_own_page_is_not_attached(monkeypatch):
    """Pre-Award — F&A Cost Rates points at the page it came from, so Sources
    already covers it. The rate LETTER is a separate document and does attach."""
    monkeypatch.undo()
    fc._docs_by_title.cache_clear()
    try:
        assert fc.attachments_for_titles(["Pre-Award — F&A Cost Rates"]) == []
        letter = fc.attachments_for_titles(["Morgan State F&A Rate Agreement — 2024 to 2026"])
        assert letter and letter[0]["url"].endswith(".pdf")
    finally:
        fc._docs_by_title.cache_clear()


# ---------------------------------------------------------------------------
# The e-training gap. ORA's seven modules are hosted on Articulate, and
# classifying them as ordinary web pages meant a PI asking about Time and
# Effort got a 474-character description of a module with no way to open it.
# The rule that fixes it: does the link LEAVE the ORA page it came from.
# Sources already cites that page; it will never show the module.
# ---------------------------------------------------------------------------

def test_an_articulate_etraining_module_is_handed_over(monkeypatch):
    monkeypatch.undo()
    fc._docs_by_title.cache_clear()
    try:
        out = fc.attachments_for_titles(
            ["Travel on Sponsored Projects: Approval to Reimbursement — eTraining Module"])
        assert out, "the e-training module resolved to nothing"
        assert "articulate.com" in out[0]["url"]
        assert out[0]["kind"] == "link"
    finally:
        fc._docs_by_title.cache_clear()


def test_external_destinations_classify_as_link():
    for url in (
        "https://rise.articulate.com/share/EkNni5Bgg6Epa2bjN00d",
        "https://drive.google.com/file/d/abc/view",
        "https://morgan.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=x",
        "https://www.grants.gov/",
    ):
        assert fc._destination_kind(url) == "link", url


def test_a_morgan_page_is_still_not_attached():
    """Sources covers the ORA page. Only what leaves it is handed over."""
    assert fc._destination_kind(
        "https://www.morgan.edu/office-of-research-administration/pre-award") == "page"


def test_files_and_forms_keep_their_own_kinds():
    assert fc._destination_kind("https://www.morgan.edu/Documents/x/handbook.pdf") == "file"
    assert fc._destination_kind(
        "https://na2.docusign.net/Member/PowerFormSigning.aspx?x=1") == "form"
