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
    # I-3: page 4 was assigned to project_description by the ledger but is
    # in NEITHER span (the section's stop-before-B rule means the span's
    # own reach cannot include it). It must be RECORDED, never silently
    # dropped -- and it's real: page_counts (3) and the span's own "pages"
    # (2) are allowed to disagree, precisely because one page is dropped.
    assert spans["project_description"]["dropped_pages"] == [4]
    assert spans["project_summary"]["dropped_pages"] == []
    assert pl.page_counts_from_ledger(rows)["project_description"] == 3
    assert spans["project_description"]["pages"] == 2


def test_an_out_of_order_interior_page_never_breaks_the_span_it_interrupts():
    """THE CRITICAL FIX. `build_ledger`'s contiguity rule was softened from
    refuse (`unassigned`) to accept-and-flag (`out_of_order`) -- correctly,
    per its own docstring, because a genuinely interleaved page (one letter
    dropped between two others) deserves to be counted rather than thrown
    away. But `spans_from_ledger` still treated ANY differently-labelled
    interior page as a hard stop, so the flag alone turned a fail-safe into
    a fail-open: a single solidly-receipted-but-out-of-order page mislabelled
    into the MIDDLE of a real run silently truncated that section's span,
    dropping every real page behind the intruder from both its text and its
    page count -- exactly the false "not_found" this whole feature exists to
    prevent.

    Ten pages: pp1-2 = project_summary, pp3-10 = project_description, except
    page 6 -- solidly receipted, but labelled project_summary and flagged
    `out_of_order` (it reappears after project_summary had already closed
    out on page 2). project_description must still come back pages 3-10,
    losing nothing -- matching the pre-softening behaviour for the section
    the intruder interrupts, while the intruder's OWN section (project_summary)
    still only gets its real, contiguous reach."""
    pages = [f"Page {i} carries its own distinct real content right here" for i in range(1, 11)]
    rows = [{"page": 1, "section": "project_summary", "source": "model"},
            {"page": 2, "section": "project_summary", "source": "model"},
            {"page": 3, "section": "project_description", "source": "model"},
            {"page": 4, "section": "project_description", "source": "model"},
            {"page": 5, "section": "project_description", "source": "model"},
            {"page": 6, "section": "project_summary", "source": "model", "out_of_order": True},
            {"page": 7, "section": "project_description", "source": "model"},
            {"page": 8, "section": "project_description", "source": "model"},
            {"page": 9, "section": "project_description", "source": "model"},
            {"page": 10, "section": "project_description", "source": "model"}]

    spans = pl.spans_from_ledger(rows, pages, SECTIONS)

    pd = spans["project_description"]
    assert pd["pages"] == 8
    assert pd["dropped_pages"] == []
    assert "Page 3 carries" in pd["text"]
    assert "Page 10 carries" in pd["text"]
    joined = "\n".join(pages)
    assert joined[pd["start"]:pd["end"]] == pd["text"]

    # The intruder's own section is unaffected by this fix -- it still only
    # gets its real, contiguous reach (pages 1-2), and page 6 is recorded as
    # dropped for it rather than silently folded in (it would reopen the
    # single-contiguous-slice problem `spans_from_ledger` exists to avoid).
    ps = spans["project_summary"]
    assert ps["pages"] == 2
    assert ps["dropped_pages"] == [6]

    # Attribution never credits the intruder to the section it claims --
    # page 6 is real content, but content whose placement is exactly what is
    # in doubt.
    counts = pl.page_counts_from_ledger(rows)
    assert counts["project_description"] == 7   # pages 3,4,5,7,8,9,10 -- never 6
    assert counts["project_summary"] == 2        # pages 1,2 -- never 6


def test_page_counts_still_count_only_assigned_pages_despite_absorption():
    """Absorption widens the SPAN's reach but must not widen the COUNT --
    page rules compare against a real page limit and must not credit a page
    nobody actually assigned to the section."""
    rows = [dict(r) for r in ROWS]
    rows[2] = {"page": 3, "section": None, "source": "blank"}
    assert pl.page_counts_from_ledger(rows)["project_description"] == 2


def test_no_rows_means_no_spans():
    assert pl.spans_from_ledger([], PAGES, SECTIONS) == {}
