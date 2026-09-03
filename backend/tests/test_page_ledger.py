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
