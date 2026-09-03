"""The roll call. A page cannot be skipped -- only left unanswered, visibly."""
import pytest

from services import page_ledger as pl

SECTIONS = {
    "project_summary": {"label": "Project Summary", "aliases": []},
    "project_description": {"label": "Project Description", "aliases": []},
    "references_cited": {"label": "References Cited", "aliases": []},
}
PAGES = [f"Stamp line\nPage {i} of 6\nContent of page {i} with plenty of real words here."
         for i in range(1, 7)]


def _fake_walk(answers):
    """A stand-in for the model: returns exactly the rows it is told to."""
    def walk(page_texts, section_keys, *, furniture, known=None):
        return {p: dict(r) for p, r in answers.items()}
    return walk


def test_every_page_gets_exactly_one_row(monkeypatch):
    monkeypatch.setattr(pl, "walk_pages", _fake_walk({
        p: {"section": "project_description", "quote": f"Content of page {p} with plenty of real words here."}
        for p in range(1, 7)}))
    rows = pl.build_ledger(PAGES, SECTIONS)
    assert [r["page"] for r in rows] == [1, 2, 3, 4, 5, 6]
    assert all(r["source"] == "model" for r in rows)
    assert all(r["verified"] for r in rows)


def test_a_page_the_model_omitted_is_unassigned_not_absent(monkeypatch):
    answers = {p: {"section": "project_description",
                   "quote": f"Content of page {p} with plenty of real words here."}
               for p in range(1, 7)}
    del answers[4]
    monkeypatch.setattr(pl, "walk_pages", _fake_walk(answers))
    rows = pl.build_ledger(PAGES, SECTIONS)
    assert len(rows) == 6
    row4 = next(r for r in rows if r["page"] == 4)
    assert row4["source"] == "unassigned"
    assert row4["section"] is None


def test_a_page_whose_receipt_fails_is_unassigned(monkeypatch):
    answers = {p: {"section": "project_description",
                   "quote": f"Content of page {p} with plenty of real words here."}
               for p in range(1, 7)}
    answers[3]["quote"] = "This sentence appears nowhere in the document at all"
    monkeypatch.setattr(pl, "walk_pages", _fake_walk(answers))
    rows = pl.build_ledger(PAGES, SECTIONS)
    row3 = next(r for r in rows if r["page"] == 3)
    assert row3["source"] == "unassigned"
    assert row3["verified"] is False


def test_a_blank_page_is_blank_not_unassigned(monkeypatch):
    pages = list(PAGES)
    pages[4] = "Stamp line\nPage 5 of 6"
    monkeypatch.setattr(pl, "walk_pages", _fake_walk({}))
    rows = pl.build_ledger(pages, SECTIONS)
    row5 = next(r for r in rows if r["page"] == 5)
    assert row5["source"] == "blank"


def test_structure_outranks_the_model(monkeypatch):
    monkeypatch.setattr(pl, "walk_pages", _fake_walk({
        p: {"section": "references_cited",
            "quote": f"Content of page {p} with plenty of real words here."}
        for p in range(1, 7)}))
    rows = pl.build_ledger(PAGES, SECTIONS, structure={1: "project_summary"})
    row1 = next(r for r in rows if r["page"] == 1)
    assert row1["source"] == "structure"
    assert row1["section"] == "project_summary"
    assert row1["disagreed_with_model"] == "references_cited"


def test_a_section_cannot_reappear_after_it_ended(monkeypatch):
    """Sections in a proposal are contiguous. Page 6 cannot be Project Summary
    once Project Description has started."""
    answers = {1: {"section": "project_summary", "quote": "Content of page 1 with plenty of real words here."}}
    for p in range(2, 6):
        answers[p] = {"section": "project_description",
                      "quote": f"Content of page {p} with plenty of real words here."}
    answers[6] = {"section": "project_summary", "quote": "Content of page 6 with plenty of real words here."}
    monkeypatch.setattr(pl, "walk_pages", _fake_walk(answers))
    rows = pl.build_ledger(PAGES, SECTIONS)
    row6 = next(r for r in rows if r["page"] == 6)
    assert row6["source"] == "unassigned"
    assert row6["section"] is None


def test_an_unknown_section_name_is_refused(monkeypatch):
    monkeypatch.setattr(pl, "walk_pages", _fake_walk({
        1: {"section": "a_section_nobody_declared", "quote": "Content of page 1 with plenty of real words here."}}))
    rows = pl.build_ledger(PAGES, SECTIONS)
    assert rows[0]["source"] == "unassigned"


def test_the_ledger_is_complete_when_the_model_is_unavailable(monkeypatch):
    """Golden rule 3. No AI must never mean no ledger."""
    monkeypatch.setattr(pl, "walk_pages", _fake_walk({}))
    rows = pl.build_ledger(PAGES, SECTIONS, structure={2: "project_summary"})
    assert len(rows) == 6
    assert {r["source"] for r in rows} <= {"structure", "blank", "unassigned"}


def test_complete_reports_what_is_missing():
    rows = [{"page": 1, "source": "model"}, {"page": 2, "source": "unassigned"},
            {"page": 3, "source": "blank"}]
    ok, unaccounted = pl.completeness(rows)
    assert ok is False
    assert unaccounted == [2]
    ok, unaccounted = pl.completeness([{"page": 1, "source": "structure"},
                                       {"page": 2, "source": "blank"}])
    assert ok is True
    assert unaccounted == []


def test_the_walk_names_the_model_and_the_region(monkeypatch):
    """`gemini_client.DEFAULT_MODEL` is gemini-2.5-flash, so a call that forgets
    silently downgrades. And 3.6-flash 404s outside `global`."""
    seen = {}

    def spy(prompt, **kw):
        seen.update(kw)
        return {"pages": []}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", spy)
    pl.walk_pages(PAGES, ["project_summary"], furniture=frozenset())
    assert seen.get("model") == "gemini-3.6-flash"
    assert seen.get("location") == "global"
    assert seen.get("thinking_budget") == 1024
    assert seen.get("list_key") == "pages"


# ---------------------------------------------------------------------------
# Fix round 1: the guarantee is only real if it survives a badly-behaved
# model, not just a well-behaved fake. Every test above monkeypatches
# `walk_pages` itself, so none of them exercise `_ask_window`, `walk_pages`'s
# own error handling, or `build_ledger`'s guard around `structure`/`walk_pages`.
# ---------------------------------------------------------------------------

def test_build_ledger_still_returns_n_rows_when_walk_pages_raises(monkeypatch):
    """IMP-8(a). `walk_pages` documents 'never raises' -- this is the roll
    call's OWN guarantee (golden rule 3), not a promise borrowed from a
    callee. A bug in the walk must not become a bug in build_ledger."""
    def boom(*a, **kw):
        raise RuntimeError("the walk blew up")
    monkeypatch.setattr(pl, "walk_pages", boom)
    rows = pl.build_ledger(PAGES, SECTIONS)
    assert len(rows) == 6
    assert {r["source"] for r in rows} <= {"blank", "unassigned"}


def test_a_malformed_window_costs_only_its_own_pages(monkeypatch):
    """IMP-8(b) / IMP-1. `Executor.map` abandons every result queued behind a
    raising future -- a single malformed window used to take down every
    LATER window's successful answers too. 12 pages, 3 windows of 4; only
    the window carrying page 5 is malformed."""
    import re
    pages = [f"Content of page {i} with plenty of real words here." for i in range(1, 13)]
    section_keys = ["project_description"]

    def fake_generate_json(prompt, **kw):
        nums = [int(x) for x in re.findall(r"=== PAGE (\d+) ===", prompt)]
        if 5 in nums:
            # A non-dict row and a numeric quote -- exactly what escaped the
            # old narrow `except (TypeError, ValueError)`.
            return {"pages": ["not-a-dict", {"page": 5, "section": "x", "quote": 1}]}
        return {"pages": [
            {"page": n, "section": "project_description",
             "quote": f"Content of page {n} with plenty of real words here."}
            for n in nums
        ]}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", fake_generate_json)
    got = pl.walk_pages(pages, section_keys, furniture=frozenset())
    # Every page outside the bad window's own pages must still be answered --
    # window 3 (pages 9-12) must not be collateral damage of window 2's bug.
    assert set(range(9, 13)) <= set(got.keys())
    assert set(range(1, 5)) <= set(got.keys())


def test_a_non_numeric_structure_key_does_not_raise():
    """IMP-8(c) / IMP-7."""
    rows = pl.build_ledger(PAGES, SECTIONS, structure={"not-a-page-number": "project_summary"})
    assert len(rows) == 6


def test_the_re_ask_asks_exactly_the_missing_pages(monkeypatch):
    """IMP-8(d). A partial miss (a few pages out of an otherwise-working
    pass) must re-ask precisely those pages -- not the whole document, and
    not skip the re-ask (that guard is only for a TOTAL first-pass miss,
    IMP-4)."""
    pages = [f"Content of page {i} with plenty of real words here." for i in range(1, 9)]
    section_keys = ["project_description"]
    reasked = []

    def fake_generate_json(prompt, **kw):
        import re
        nums = [int(x) for x in re.findall(r"=== PAGE (\d+) ===", prompt)]
        if nums == [3]:
            reasked.append(3)
            return {"pages": [{"page": 3, "section": "project_description",
                               "quote": "Content of page 3 with plenty of real words here."}]}
        if 3 in nums:
            # page 3's WINDOW omits page 3 from the reply -- a partial miss.
            return {"pages": [
                {"page": n, "section": "project_description",
                 "quote": f"Content of page {n} with plenty of real words here."}
                for n in nums if n != 3
            ]}
        return {"pages": [
            {"page": n, "section": "project_description",
             "quote": f"Content of page {n} with plenty of real words here."}
            for n in nums
        ]}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", fake_generate_json)
    got = pl.walk_pages(pages, section_keys, furniture=frozenset())
    assert reasked == [3]
    assert 3 in got


def test_an_out_of_window_page_id_is_dropped(monkeypatch):
    """IMP-8(e). A row claiming a page number outside the window it was asked
    about is not trusted -- reconciliation is by id, never by count."""
    pages = ["Content of page 1 with plenty of real words here.",
             "Content of page 2 with plenty of real words here."]

    def fake_generate_json(prompt, **kw):
        return {"pages": [{"page": 99, "section": "project_description",
                           "quote": "Content of page 1 with plenty of real words here."}]}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", fake_generate_json)
    got = pl.walk_pages(pages, ["project_description"], furniture=frozenset())
    assert 99 not in got


def test_a_page_answered_twice_with_different_sections_ends_unassigned(monkeypatch):
    """IMP-8(f) / IMP-3. A wrong label is ~6x more damaging than a missing
    one, and here it would be decided by array position -- refuse it."""
    pages = ["Content of page 1 with plenty of real words here."]

    def fake_generate_json(prompt, **kw):
        return {"pages": [
            {"page": 1, "section": "project_summary",
             "quote": "Content of page 1 with plenty of real words here."},
            {"page": 1, "section": "references_cited",
             "quote": "Content of page 1 with plenty of real words here."},
        ]}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", fake_generate_json)
    got = pl.walk_pages(pages, ["project_summary", "references_cited"], furniture=frozenset())
    assert 1 not in got
