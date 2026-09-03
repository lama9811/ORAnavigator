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


def test_a_span_addresses_the_text_it_claims_with_absorbed_and_stopped_pages():
    """Re-verifies the acceptance gate under the absorb-interior /
    stop-at-a-different-section rule, not just the fully-contiguous case."""
    scenarios = []

    rows = [dict(r) for r in ROWS]
    rows[2] = {"page": 3, "section": None, "source": "blank"}
    scenarios.append((rows, PAGES))

    rows = [dict(r) for r in ROWS]
    rows[2] = {"page": 3, "section": None, "source": "unassigned"}
    scenarios.append((rows, PAGES))

    rows = [dict(r) for r in ROWS]
    rows[1] = {"page": 2, "section": None, "source": "unassigned"}
    scenarios.append((rows, PAGES))

    rows = [dict(r) for r in ROWS]
    rows[3] = {"page": 4, "section": None, "source": "unassigned"}
    scenarios.append((rows, PAGES))

    diff_section_pages = ["Page one is project description part A",
                          "Page two is project description part A too",
                          "Page three is the project summary again",
                          "Page four is project description part B"]
    diff_section_rows = [
        {"page": 1, "section": "project_description", "source": "model"},
        {"page": 2, "section": "project_description", "source": "model"},
        {"page": 3, "section": "project_summary", "source": "model"},
        {"page": 4, "section": "project_description", "source": "model"},
    ]
    scenarios.append((diff_section_rows, diff_section_pages))

    for rows, pages in scenarios:
        joined = "\n".join(pages)
        spans = pl.spans_from_ledger(rows, pages, SECTIONS)
        for key, span in spans.items():
            assert joined[span["start"]:span["end"]] == span["text"], (rows, key)


def test_contiguous_pages_become_one_span():
    spans = pl.spans_from_ledger(ROWS, PAGES, SECTIONS)
    assert set(spans) == {"project_summary", "project_description"}
    assert "Page two" in spans["project_description"]["text"]
    assert "Page four" in spans["project_description"]["text"]


def test_page_counts_come_from_the_ledger():
    assert pl.page_counts_from_ledger(ROWS) == {"project_summary": 1,
                                                "project_description": 3}


def test_an_interior_blank_page_is_absorbed():
    """A full-page figure/chart/scan inside a section is ordinary content --
    absorbing it is how a 6-20 page section with a figure on page 10 does not
    silently become "pages 6-9", reporting real content on 11-20 missing."""
    rows = [dict(r) for r in ROWS]
    rows[2] = {"page": 3, "section": None, "source": "blank"}
    spans = pl.spans_from_ledger(rows, PAGES, SECTIONS)
    assert "Page three" in spans["project_description"]["text"]
    assert "Page four" in spans["project_description"]["text"]


def test_an_interior_unassigned_page_is_absorbed():
    rows = [dict(r) for r in ROWS]
    rows[2] = {"page": 3, "section": None, "source": "unassigned"}
    spans = pl.spans_from_ledger(rows, PAGES, SECTIONS)
    assert "Page three" in spans["project_description"]["text"]
    assert "Page four" in spans["project_description"]["text"]


def test_a_leading_unassigned_page_stays_outside():
    rows = [dict(r) for r in ROWS]
    rows[1] = {"page": 2, "section": None, "source": "unassigned"}
    spans = pl.spans_from_ledger(rows, PAGES, SECTIONS)
    assert "Page two" not in spans["project_description"]["text"]
    assert "Page three" in spans["project_description"]["text"]
    assert "Page four" in spans["project_description"]["text"]


def test_a_trailing_unassigned_page_stays_outside():
    rows = [dict(r) for r in ROWS]
    rows[3] = {"page": 4, "section": None, "source": "unassigned"}
    spans = pl.spans_from_ledger(rows, PAGES, SECTIONS)
    assert "Page four" not in spans["project_description"]["text"]
    assert "Page two" in spans["project_description"]["text"]
    assert "Page three" in spans["project_description"]["text"]


def test_an_interior_page_of_a_different_section_stops_the_span():
    """A page the ledger gave to someone else is never absorbed -- only
    blank/unassigned interiors are, never another section's real page."""
    pages = ["Page one is project description part A",
             "Page two is project description part A too",
             "Page three is the project summary again",
             "Page four is project description part B"]
    rows = [
        {"page": 1, "section": "project_description", "source": "model"},
        {"page": 2, "section": "project_description", "source": "model"},
        {"page": 3, "section": "project_summary", "source": "model"},
        {"page": 4, "section": "project_description", "source": "model"},
    ]
    spans = pl.spans_from_ledger(rows, pages, SECTIONS)
    assert spans["project_description"]["text"] == "\n".join(pages[:2])
    assert "part B" not in spans["project_description"]["text"]
    assert spans["project_summary"]["text"] == pages[2]


def test_page_counts_still_count_only_assigned_pages_despite_absorption():
    """Absorption widens the SPAN's reach but must not widen the COUNT --
    page rules compare against a real page limit and must not credit a page
    nobody actually assigned to the section."""
    rows = [dict(r) for r in ROWS]
    rows[2] = {"page": 3, "section": None, "source": "blank"}
    assert pl.page_counts_from_ledger(rows)["project_description"] == 2


def test_no_rows_means_no_spans():
    assert pl.spans_from_ledger([], PAGES, SECTIONS) == {}
