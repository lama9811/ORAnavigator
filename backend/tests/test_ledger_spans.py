"""Span offsets must ADDRESS what they claim to. A span carrying the wrong
offsets corrupts `_project_description_span` and the section map silently."""
import pytest

from services import page_ledger as pl

SECTIONS = {
    "project_summary": {"label": "Project Summary", "aliases": []},
    "project_description": {"label": "Project Description", "aliases": []},
}
PAGES = ["Page one is the summary text here", "Page two starts the description",
         "Page three continues the description", "Page four ends it"]
ROWS = [
    {"page": 1, "section": "project_summary", "source": "model"},
    {"page": 2, "section": "project_description", "source": "model"},
    {"page": 3, "section": "project_description", "source": "model"},
    {"page": 4, "section": "project_description", "source": "model"},
]


def test_a_span_addresses_the_text_it_claims():
    joined = "\n".join(PAGES)
    spans = pl.spans_from_ledger(ROWS, PAGES, SECTIONS)
    for key, span in spans.items():
        assert joined[span["start"]:span["end"]] == span["text"], key


def test_contiguous_pages_become_one_span():
    spans = pl.spans_from_ledger(ROWS, PAGES, SECTIONS)
    assert set(spans) == {"project_summary", "project_description"}
    assert "Page two" in spans["project_description"]["text"]
    assert "Page four" in spans["project_description"]["text"]


def test_page_counts_come_from_the_ledger():
    assert pl.page_counts_from_ledger(ROWS) == {"project_summary": 1,
                                                "project_description": 3}


def test_an_unassigned_page_belongs_to_no_span():
    rows = [dict(r) for r in ROWS]
    rows[2] = {"page": 3, "section": None, "source": "unassigned"}
    spans = pl.spans_from_ledger(rows, PAGES, SECTIONS)
    assert "Page three" not in spans["project_description"]["text"]
    assert pl.page_counts_from_ledger(rows)["project_description"] == 2


def test_no_rows_means_no_spans():
    assert pl.spans_from_ledger([], PAGES, SECTIONS) == {}
