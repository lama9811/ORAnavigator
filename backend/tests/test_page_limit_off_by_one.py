"""The Table of Contents is not part of the Project Description.

Measured on a real AWARDED package: the TOC page folded forward, giving
project_description 16 pages against a true 15, and the PI was told their
compliant section was over the 15-page limit and would not be reviewed.
"""
import pytest

from services import page_ledger as pl

SECTIONS = {
    "project_summary": {"label": "Project Summary", "aliases": []},
    "project_description": {"label": "Project Description", "aliases": []},
}
# p1 summary, p2 TABLE OF CONTENTS, p3-p5 description
PAGES = [
    "Project Summary\nOverview of the proposed work with enough words to quote here.",
    "TABLE OF CONTENTS\nProject Summary 1\nProject Description 3\nReferences Cited 2",
    "Project Description\nThe first page of the description with real words to quote.",
    "Continuing the description on its second page with more real words here.",
    "The final page of the project description with yet more real words here.",
]


def test_the_contents_page_is_not_counted_in_the_project_description(monkeypatch):
    def walk(page_texts, section_keys, *, furniture, known=None):
        return {1: {"section": "project_summary", "quote": "Overview of the proposed work with enough words to quote here."},
                3: {"section": "project_description", "quote": "The first page of the description with real words to quote."},
                4: {"section": "project_description", "quote": "Continuing the description on its second page with more real words here."},
                5: {"section": "project_description", "quote": "The final page of the project description with yet more real words here."}}
    monkeypatch.setattr(pl, "walk_pages", walk)
    rows = pl.build_ledger(PAGES, SECTIONS)
    assert pl.page_counts_from_ledger(rows)["project_description"] == 3
    assert next(r for r in rows if r["page"] == 2)["section"] is None
