"""NSF's own table of contents is an EXTERNAL check -- the funder wrote those
counts, not us and not the model. It REPORTS a mismatch; it never overrides.
"""
import pytest

from services import page_ledger as pl
from services import pdf_sections as ps

SECTIONS = {
    "project_summary": {"label": "Project Summary", "aliases": []},
    "project_description": {"label": "Project Description", "aliases": []},
    "references_cited": {"label": "References Cited", "aliases": []},
}
TOC_PAGE = "\n".join([
    "TABLE OF CONTENTS",
    "Project Summary (not to exceed 1 page) 1",
    "Project Description (Including Results from Prior NSF Support) 3",
    "References Cited 2",
    "Special Information/Supplementary Documents 9",
])


def test_the_roster_keeps_the_name_of_a_row_it_cannot_resolve():
    rows = ps.toc_roster(TOC_PAGE, SECTIONS)
    unresolved = [r for r in rows if r[0] is None]
    assert unresolved, "expected at least one unresolved row"
    assert any("Supplementary" in (r[2] or "") for r in unresolved)


def test_a_matching_count_is_not_reported():
    ledger = ([{"page": p, "section": "project_summary", "source": "model"} for p in (1,)] +
              [{"page": p, "section": "project_description", "source": "model"} for p in (2, 3, 4)] +
              [{"page": p, "section": "references_cited", "source": "model"} for p in (5, 6)])
    pages = [TOC_PAGE] + ["body"] * 5
    assert pl.reconcile_toc(ledger, pages, SECTIONS) == []


def test_a_mismatch_is_reported_and_changes_nothing():
    """The measured real case: an auto-generated NSF filler page makes the
    biosketch count 3 where the table of contents says 2. The document and its
    own table of contents genuinely disagree; show that, do not pick a side."""
    ledger = ([{"page": 1, "section": "project_summary", "source": "model"}] +
              [{"page": p, "section": "project_description", "source": "model"} for p in (2, 3, 4, 5)] +
              [{"page": p, "section": "references_cited", "source": "model"} for p in (6, 7)])
    pages = [TOC_PAGE] + ["body"] * 6
    before = [dict(r) for r in ledger]
    out = pl.reconcile_toc(ledger, pages, SECTIONS)
    assert [r["section"] for r in out] == ["project_description"]
    assert out[0]["ledger_pages"] == 4 and out[0]["toc_pages"] == 3
    assert ledger == before, "reconcile_toc must not mutate the ledger"


def test_no_table_of_contents_means_no_cross_check():
    ledger = [{"page": 1, "section": "project_summary", "source": "model"}]
    assert pl.reconcile_toc(ledger, ["no contents here"], SECTIONS) == []
