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


def test_a_reappearing_section_is_accepted_but_flagged_out_of_order(monkeypatch):
    """FIX ROUND 2. Sections in a proposal are usually contiguous, and a
    reappearance used to be refused outright -- discarded, unassigned, no
    matter how solid the receipt. That rule predates `_receipt_is_solid`:
    when a receipt only proved a quote was SOMEWHERE on the page, a
    coincidental match reappearing under an already-closed section was the
    model's best guess, not a verified fact, and refusing it was the right
    caution. `_receipt_is_solid` is a STRONGER guarantee now -- the quote is
    unique to this page, document-wide -- so a real, solidly-receipted
    reappearance is real content, not noise, and throwing it away costs a
    genuinely-read page for nothing but its position. Measured on a real
    56-page awarded package: NSF's own Supplementary Documents interleave
    one collaboration letter between two institutional-support pages (a PI
    concatenating individually-authored letters in upload order), and the
    old rule left that verified page unaccounted for every run.
    `spans_from_ledger` already has `dropped_pages` for exactly this shape --
    a key whose pages are not one contiguous run -- so refusing here was
    fighting machinery this module already has. It is still recorded
    (`out_of_order`), not silently accepted, so a reviewer can see the
    document interleaved this section."""
    answers = {1: {"section": "project_summary", "quote": "Content of page 1 with plenty of real words here."}}
    for p in range(2, 6):
        answers[p] = {"section": "project_description",
                      "quote": f"Content of page {p} with plenty of real words here."}
    answers[6] = {"section": "project_summary", "quote": "Content of page 6 with plenty of real words here."}
    monkeypatch.setattr(pl, "walk_pages", _fake_walk(answers))
    rows = pl.build_ledger(PAGES, SECTIONS)
    row6 = next(r for r in rows if r["page"] == 6)
    assert row6["source"] == "model"
    assert row6["section"] == "project_summary"
    assert row6["verified"] is True
    assert row6["out_of_order"] is True
    # An in-order page never carries the flag.
    row2 = next(r for r in rows if r["page"] == 2)
    assert "out_of_order" not in row2


def test_a_reappearance_still_needs_a_SOLID_receipt(monkeypatch):
    """Softening contiguity to `out_of_order` must not also soften the
    security gate. A page whose quote also receipts elsewhere is refused
    regardless of order -- reappearing is not a licence to skip
    `_receipt_is_solid`."""
    pages = list(PAGES)
    # A sentence pasted onto BOTH page 1 and page 6 -- receipts on both, so
    # it can never be solid, however page 6 is labelled. Each page keeps its
    # own distinct "Content of page N..." line too, so page 1's own
    # verification is untouched by this.
    shared = "This exact sentence was pasted onto two different pages by mistake"
    pages[0] = pages[0] + "\n" + shared
    pages[5] = pages[5] + "\n" + shared
    answers = {1: {"section": "project_summary", "quote": "Content of page 1 with plenty of real words here."}}
    for p in range(2, 6):
        answers[p] = {"section": "project_description",
                      "quote": f"Content of page {p} with plenty of real words here."}
    answers[6] = {"section": "project_summary", "quote": shared}
    monkeypatch.setattr(pl, "walk_pages", _fake_walk(answers))
    rows = pl.build_ledger(pages, SECTIONS)
    row6 = next(r for r in rows if r["page"] == 6)
    assert row6["source"] == "unassigned"
    assert row6["section"] is None
    # Page 1 is unaffected -- verified normally on its own distinct quote.
    row1 = next(r for r in rows if r["page"] == 1)
    assert row1["source"] == "model"
    assert row1["section"] == "project_summary"


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


def test_completeness_never_raises_on_a_malformed_ledger():
    """Golden rule 3, and it is load-bearing now: `review_draft` calls this
    directly and must never raise out of a public function. An unassigned
    row missing `page` is not skipped -- that IS the "we cannot confirm we
    read this" case, so it counts as a gap (reported as `None`) rather than
    silently passing."""
    ok, unaccounted = pl.completeness([{"source": "unassigned"}])
    assert ok is False
    assert unaccounted == [None]

    ok, unaccounted = pl.completeness(["not a dict"])
    assert ok is True
    assert unaccounted == []

    ok, unaccounted = pl.completeness([None])
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


# ---------------------------------------------------------------------------
# Determinism: the FIRST pass must never move off temperature 0.0. Nothing
# short of a test stops a future edit from making the whole ledger
# nondeterministic with a fully green suite -- this repo's own recorded goal
# is "same file = same answer".
# ---------------------------------------------------------------------------

def test_the_first_pass_uses_temperature_zero(monkeypatch):
    pages = ["Content of page 1 with plenty of real words here.",
             "Content of page 2 with plenty of real words here."]
    seen = []

    def spy(prompt, **kw):
        seen.append(kw.get("temperature"))
        return {"pages": [
            {"page": 1, "section": "project_description",
             "quote": "Content of page 1 with plenty of real words here."},
            {"page": 2, "section": "project_description",
             "quote": "Content of page 2 with plenty of real words here."},
        ]}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", spy)
    pl.walk_pages(pages, ["project_description"], furniture=frozenset())
    assert seen == [0.0]


# ---------------------------------------------------------------------------
# The retry path itself -- ~90 lines that only the opt-in live gate ever
# exercised. These drive it with a fake model, deterministically, in CI.
# ---------------------------------------------------------------------------

def test_a_colliding_receipt_resolves_within_one_retry_round(monkeypatch):
    """A model that returns the SAME quote for two pages must be corrected --
    `_receipt_is_solid` refuses both until each is re-asked with the specific
    colliding page named. Uses 6 pages (not 4) so the shared sentence -- on
    exactly 2 of them -- stays under `document_furniture`'s repetition floor
    (`max(2, int(0.5 * n))`, which would otherwise strip it as boilerplate
    before it could ever collide)."""
    import re

    pages = [f"Stamp\nPage {i} of 6\nUnique real content for page {i} right here."
             for i in range(1, 7)]
    shared = "This paragraph appears identically on two separate pages by mistake"
    pages[1] = pages[1] + "\n" + shared     # page 2
    pages[2] = pages[2] + "\n" + shared     # page 3
    calls = {"n": 0}

    def fake_generate_json(prompt, **kw):
        calls["n"] += 1
        asked = [int(x) for x in re.findall(r"=== PAGE (\d+) ===", prompt)]
        if asked == [1, 2, 3, 4]:
            return {"pages": [
                {"page": 1, "section": "project_summary",
                 "quote": "Unique real content for page 1 right here."},
                {"page": 2, "section": "project_description", "quote": shared},
                {"page": 3, "section": "project_description", "quote": shared},
                {"page": 4, "section": "project_description",
                 "quote": "Unique real content for page 4 right here."},
            ]}
        if asked == [5, 6]:
            return {"pages": [
                {"page": 5, "section": "project_description",
                 "quote": "Unique real content for page 5 right here."},
                {"page": 6, "section": "project_description",
                 "quote": "Unique real content for page 6 right here."},
            ]}
        # A single-page re-ask -- give that page its own real, unique line.
        (p,) = asked
        return {"pages": [{"page": p, "section": "project_description",
                           "quote": f"Unique real content for page {p} right here."}]}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", fake_generate_json)
    result = pl.walk_pages(pages, ["project_summary", "project_description"],
                           furniture=frozenset())
    assert result[2]["quote"] != shared
    assert result[3]["quote"] != shared
    assert result[2]["quote"] == "Unique real content for page 2 right here."
    assert result[3]["quote"] == "Unique real content for page 3 right here."
    # 2 initial-pass windows ([1-4], [5-6]) + one re-ask each for pages 2 and
    # 3 = 4 calls total. A SECOND retry round would add 2 more -- its absence
    # is what proves the collision resolved within round 1.
    assert calls["n"] == 4


def test_the_retry_loop_gives_up_after_max_retry_rounds(monkeypatch):
    """A page that can never be verified must not loop forever. The walk
    exhausts `_MAX_RETRY_ROUNDS` and stops calling the model -- it does not
    keep re-asking indefinitely, and it does not silently invent a verified
    answer."""
    import re

    pages = [f"Stamp\nPage {i} of 2\nUnique real content for page {i} right here."
             for i in range(1, 3)]
    calls = {"n": 0}
    bad_quote = "This exact sentence does not appear on any page of the document"

    def always_unverifiable(prompt, **kw):
        calls["n"] += 1
        asked = [int(x) for x in re.findall(r"=== PAGE (\d+) ===", prompt)]
        return {"pages": [{"page": p, "section": "project_description",
                           "quote": bad_quote} for p in asked]}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", always_unverifiable)
    result = pl.walk_pages(pages, ["project_description"], furniture=frozenset())
    # Still carries the (unverifiable) last answer -- `build_ledger` is what
    # turns this into `unassigned`, not `walk_pages` itself.
    assert result[1]["quote"] == bad_quote
    assert result[2]["quote"] == bad_quote
    # 1 initial window call ([1, 2]) + `_MAX_RETRY_ROUNDS` rounds of 2
    # single-page re-asks each. If the loop failed to stop, this would keep
    # growing without bound.
    assert calls["n"] == 1 + pl._MAX_RETRY_ROUNDS * 2

    rows = pl.build_ledger(pages, SECTIONS)
    assert all(r["source"] == "unassigned" for r in rows)


# ---------------------------------------------------------------------------
# `_receipt_is_solid`'s uniqueness semantics, tested directly -- previously
# reachable only through `build_ledger`.
# ---------------------------------------------------------------------------

def test_receipt_is_solid_requires_uniqueness_across_the_whole_document():
    bodies = [
        "This project addresses distinct challenges in coastal water quality management",
        "A completely different discussion of estuarine chemistry appears here as well",
        "This project addresses distinct challenges in coastal water quality management",
    ]
    shared = "This project addresses distinct challenges in coastal water quality management"
    unique = "A completely different discussion of estuarine chemistry appears here as well"

    # On the page it claims, but the SAME quote also receipts another page --
    # not solid, symmetrically, for either page carrying it.
    assert pl._receipt_is_solid(bodies, 0, shared) is False
    assert pl._receipt_is_solid(bodies, 2, shared) is False
    # Unique to its one page -- solid.
    assert pl._receipt_is_solid(bodies, 1, unique) is True
    # Not even on the page it claims -- not solid, no other-page check needed.
    assert pl._receipt_is_solid(bodies, 1, "Nothing like this appears anywhere in this document") is False
    # Out-of-range page index refuses rather than raising.
    assert pl._receipt_is_solid(bodies, 99, shared) is False
    assert pl._receipt_is_solid(bodies, -1, shared) is False


def test_receipt_is_solid_safe_never_raises_and_never_loses_other_rows(monkeypatch):
    """The minor fix: `_receipt_is_solid` sits in `build_ledger`'s per-page
    loop with no guard of its own, so a raise there used to lose EVERY row,
    not just the one page that triggered it -- the opposite of the per-page
    containment the rest of this module already guarantees. Forcing a raise
    here must demote only the one page to `unassigned`, and every other page
    must still get its normal row."""
    def boom(bodies, page_idx, quote):
        if page_idx == 2:          # page 3
            raise RuntimeError("boom")
        return True                 # every other page "verified"

    monkeypatch.setattr(pl, "_receipt_is_solid", boom)
    monkeypatch.setattr(pl, "walk_pages", _fake_walk({
        p: {"section": "project_description",
            "quote": f"Content of page {p} with plenty of real words here."}
        for p in range(1, 7)}))
    rows = pl.build_ledger(PAGES, SECTIONS)
    assert len(rows) == 6
    row3 = next(r for r in rows if r["page"] == 3)
    assert row3["source"] == "unassigned"
    for r in rows:
        if r["page"] != 3:
            assert r["source"] == "model"
            assert r["verified"] is True


# ---------------------------------------------------------------------------
# Minor fix: an "unsure" page (the exact escape hatch `_WALK_SYSTEM` invites)
# used to be skipped by the retry check entirely, so it was never re-asked.
# ---------------------------------------------------------------------------

def test_an_unsure_page_is_re_asked_not_stranded(monkeypatch):
    import re

    pages = ["Content of page 1 with plenty of real words here.",
             "Content of page 2 with plenty of real words here."]
    calls = {"n": 0}

    def fake_generate_json(prompt, **kw):
        calls["n"] += 1
        asked = [int(x) for x in re.findall(r"=== PAGE (\d+) ===", prompt)]
        if asked == [1, 2]:
            return {"pages": [
                {"page": 1, "section": "project_description",
                 "quote": "Content of page 1 with plenty of real words here."},
                {"page": 2, "section": "unsure", "quote": ""},
            ]}
        # the re-ask for page 2 alone
        return {"pages": [{"page": 2, "section": "project_description",
                           "quote": "Content of page 2 with plenty of real words here."}]}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", fake_generate_json)
    result = pl.walk_pages(pages, ["project_description"], furniture=frozenset())
    assert result[2]["section"] == "project_description"
    assert calls["n"] == 2       # the initial pass, plus exactly one re-ask


# ---------------------------------------------------------------------------
# Two more findings from the final whole-branch review, same shape as the
# "unsure" fix above: a hallucinated section name is the COMMONEST shaky
# answer and was the only one with no second chance, and the retry loop's
# own uniqueness check must not be able to lose every OTHER page's answer.
# ---------------------------------------------------------------------------

def test_a_section_not_in_the_allowed_list_is_re_asked_not_stranded(monkeypatch):
    """Before this fix, `ans.get("section") not in section_keys` was simply
    `continue`d past in the retry loop -- never popped, never given a
    breadcrumb, and the wrong value rode all the way into `build_ledger`,
    which discards it there too (`guess in sections` fails) and reports the
    page `unassigned`. So the page paid for a call and still lost the
    ledger's own second-chance courtesy that "unsure" already gets."""
    import re

    pages = ["Content of page 1 with plenty of real words here.",
             "Content of page 2 with plenty of real words here."]
    calls = {"n": 0}

    def fake_generate_json(prompt, **kw):
        calls["n"] += 1
        asked = [int(x) for x in re.findall(r"=== PAGE (\d+) ===", prompt)]
        if asked == [1, 2]:
            return {"pages": [
                {"page": 1, "section": "project_description",
                 "quote": "Content of page 1 with plenty of real words here."},
                # a section name NOT in the allowed list -- the commonest
                # hallucination
                {"page": 2, "section": "budget_narrative", "quote": ""},
            ]}
        # the re-ask for page 2 alone
        return {"pages": [{"page": 2, "section": "project_description",
                           "quote": "Content of page 2 with plenty of real words here."}]}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", fake_generate_json)
    result = pl.walk_pages(pages, ["project_description"], furniture=frozenset())
    assert result[2]["section"] == "project_description"
    assert calls["n"] == 2       # the initial pass, plus exactly one re-ask


def test_a_bad_page_in_the_retry_loops_own_check_cannot_lose_every_other_page(monkeypatch):
    """`build_ledger` wraps its `walk_pages` call in a try/except SPECIFICALLY
    so a bug in the walk cannot cost the whole roll call -- but the retry
    loop used to call `_receipt_is_solid` UNGUARDED, so a raise there escaped
    `walk_pages` entirely (nothing inside it catches this call) and was only
    caught one level up, in `build_ledger`'s wrapper -- which then set
    `answers = {}`, unassigning EVERY page, not just the one whose check
    failed. Same containment argument as `_receipt_is_solid_safe`'s own
    docstring, exercised end to end through `build_ledger` rather than
    assumed from reading the code."""
    pages = ["Content of page 1 with plenty of real words here.",
             "Content of page 2 with plenty of real words here."]

    def fake_walk_generate_json(prompt, **kw):
        import re
        asked = [int(x) for x in re.findall(r"=== PAGE (\d+) ===", prompt)]
        return {"pages": [
            {"page": p, "section": "project_description",
             "quote": f"Content of page {p} with plenty of real words here."}
            for p in asked]}

    from services import gemini_client as gc
    monkeypatch.setattr(gc, "generate_json", fake_walk_generate_json)

    def _boom(*a, **k):
        raise RuntimeError("simulated failure inside the retry loop's own check")

    monkeypatch.setattr(pl, "_receipt_is_solid", _boom)

    rows = pl.build_ledger(pages, {"project_description": {"label": "Project Description", "aliases": []}})
    # `_receipt_is_solid` always raising also makes `build_ledger`'s OWN final
    # verification (`_receipt_is_solid_safe`, unaffected by this fix and
    # already guarded) return False for every row -- so neither page can
    # reach `source == "model"` in this test, by construction. The
    # discriminator is `refused`: it is only ever set for a page the model
    # DID answer (a valid section plus a quote) whose receipt did not hold.
    # Pre-fix, the retry loop's unguarded call raises OUT of `walk_pages`
    # entirely, `build_ledger`'s wrapper around that call catches it and
    # resets `answers = {}` -- so BOTH pages' real answers are lost and
    # neither row ever reaches the `refused` branch at all. Post-fix,
    # `walk_pages` returns the model's real answers despite the retry loop's
    # own check failing on every one of them, and both rows are marked
    # `refused` rather than silently emptied.
    assert [r.get("refused") for r in rows] == ["project_description", "project_description"], rows
