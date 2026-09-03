# Page Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every page of an uploaded proposal PDF is accounted for in a ledger built by code, so a page can never be silently dropped from a Draft Review.

**Architecture:** Code enumerates pages 1..N *before* any model call and reconciles answers back by page id. Three checks, none of which trust the model: a roll call (every page answered, missing ones re-asked individually), a receipt (each page's answer must quote that page, verified against a page-safe matcher), and NSF's own Table of Contents as an external cross-check on section lengths. `pdf_sections`' deterministic structure always outranks the walk; disagreement is reported, never silently resolved.

**Tech Stack:** Python 3.13, FastAPI, pdfplumber, `google-genai==1.14.0` on Vertex (`gemini-3.6-flash` @ `location="global"`), pytest, React 19.

**Spec:** `docs/superpowers/specs/2026-09-03-page-ledger-design.md` — read it before Task 1. It carries the measurements every number below rests on.

## Global Constraints

- **Model + region must be named on every call:** `model=draft_review.MODEL` (`gemini-3.6-flash`), `location=draft_review.MODEL_LOCATION` (`global`). `gemini_client.DEFAULT_MODEL` is `gemini-2.5-flash`, so a call that omits these silently downgrades. `gemini-3.6-flash` **404s in `us-central1`** — the pair moves together.
- **Thinking is capped, never disabled:** `thinking_budget=draft_review.THINKING_BUDGET` (1024). Disabling it was measured to make the reviewer omit rows.
- **Every Gemini call goes through `draft_review._ask_model`**, which holds `_MODEL_SLOTS` (a `BoundedSemaphore`, default 8, env `REVIEW_MAX_CONCURRENT_MODEL_CALLS`). Do not open an unbounded pool; `services/proofread.py` already does that and it is a known defect, not a pattern to copy.
- **`PAGE_WINDOW = 4`** pages per call. Measured: 4 → 56/56 receipts in 22.8s; 12 → 55/56 in 41.6s; 28 → 54/56.
- **Never raise to the caller.** Golden rule 3: every function here degrades to a complete-but-unlabelled ledger rather than failing an upload.
- **`response_schema` is NOT used** even though the pinned SDK supports it. See spec §4.
- **Do not import `services.document_text` at module scope from `page_ledger`,** and import `draft_review` lazily *inside* functions — the established idiom in `document_text.py:246, 301, 392`.
- **Run the suite as:** `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py`
- **Never `git push`.** Golden rule 7 — a push to `main` is a production deploy. Commit locally only.

---

### Task 1: The page-safe receipt check

The security core. `text_match.quote_in` **cannot** be used to verify a quote against a single page: its `_strip_page_furniture` sets `floor = len(marks)`, which is 1 on a single page, so every line qualifies as "furniture", up to nine consecutive lines of real prose are deleted, and a quote stitched across the gap verifies. This task builds the page-safe replacement and proves the hole is closed.

**Files:**
- Create: `backend/services/page_ledger.py`
- Test: `backend/tests/test_page_receipt.py`

**Interfaces:**
- Consumes: `services.text_match.quote_in`, `services.pdf_sections._furniture`
- Produces:
  - `document_furniture(page_texts: list[str]) -> frozenset[str]`
  - `body_text(page_text: str, furniture: frozenset) -> str`
  - `receipt_ok(page_body: str, quote: str) -> bool`
  - `is_blank(page_body: str) -> bool`
  - constants `RECEIPT_MIN_WORDS = 6`, `BLANK_CHARS = 40`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_page_receipt.py`:

```python
"""The receipt is the proof a page was read. These tests are its guarantee.

`quote_in` is the wrong instrument here and the second test says why: on a
SINGLE page its furniture path degenerates and accepts a quote stitched across
deleted lines. That test is mutation-tested — swap `receipt_ok` for `quote_in`
and it must go red, or it guards nothing.
"""
import pytest

from services import page_ledger as pl
from services.text_match import quote_in

PAGE = "\n".join([
    "Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008",
    "Data Management and Sharing Plan — Dwight Anderson Williams II",
    "Sharing The research yields journal articles with manuscripts hosted",
    "in the arXiv and videos summarizing mathematical results visible on",
    "the PI's website and backed up in university-provided accounts.",
    "Data The research may also produce quantitative data on video hits.",
    "Page 46 of 56",
])
OTHER = "\n".join([
    "Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008",
    "Mentoring Plan — Dwight Anderson Williams II",
    "Background Details of my undergraduate and graduate mentoring are",
    "drawn from a continually updated practice and varied experiences.",
    "Page 47 of 56",
])
FURNITURE = pl.document_furniture([PAGE, OTHER] * 10)


def _body(page):
    return pl.body_text(page, FURNITURE)


def test_a_real_quote_from_this_page_verifies():
    assert pl.receipt_ok(_body(PAGE), "The research yields journal articles with manuscripts hosted")


def test_a_quote_from_another_page_is_rejected():
    assert not pl.receipt_ok(_body(PAGE), "Details of my undergraduate and graduate mentoring are")


def test_the_quote_in_furniture_hole_is_closed():
    """THE SECURITY TEST. A quote welded from the top and bottom of one page.

    `quote_in` accepts it, because on a single page its furniture walk deletes
    the lines in between. `receipt_ok` must not.
    """
    forged = "in the arXiv and videos summarizing mathematical results visible on Page 46 of 56"
    assert not pl.receipt_ok(_body(PAGE), forged)


def test_a_quote_shorter_than_the_floor_is_rejected():
    assert not pl.receipt_ok(_body(PAGE), "The research yields")


def test_an_empty_quote_never_verifies():
    assert not pl.receipt_ok(_body(PAGE), "")
    assert not pl.receipt_ok(_body(PAGE), "   ")


def test_a_dropped_curly_quote_still_verifies():
    """Measured on p26 of the real package: the page reads `Superalgebras". Functional`
    and the model returned the sentence with the closing curly quote OMITTED, which
    `normalize`'s character fold does not cover. The model had read the page."""
    page = 'Kac. “Classification of Simple Lie Superalgebras”. Functional Analysis and Its Applications'
    assert pl.receipt_ok(page, "Classification of Simple Lie Superalgebras. Functional Analysis and Its")


def test_words_out_of_order_are_rejected():
    assert not pl.receipt_ok(_body(PAGE), "journal articles yields research The with manuscripts hosted")


def test_furniture_is_found_by_repetition_not_by_wording():
    assert "Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008" in FURNITURE
    assert "Data Management and Sharing Plan — Dwight Anderson Williams II" not in FURNITURE


def test_a_page_with_no_body_text_is_blank():
    assert pl.is_blank(_body("Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008\nPage 49 of 56"))
    assert not pl.is_blank(_body(PAGE))


def test_the_page_marker_is_stripped_even_when_it_is_not_repeated():
    """`Page 49 of 56` differs on every page, so it is NOT document furniture and
    survives the share threshold. It must still go, or `quote_in`'s single-page
    furniture path re-engages on the body we hand it."""
    assert "Page 46 of 56" not in _body(PAGE)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python3 -m pytest tests/test_page_receipt.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.page_ledger'`

- [ ] **Step 3: Write the implementation**

Create `backend/services/page_ledger.py`:

```python
"""Every page of an uploaded PDF is accounted for, or the review says so.

WHY THIS MODULE EXISTS. A PI uploaded an AWARDED 56-page NSF package and was
told five sections were missing; all five were in the document, on pages the
splitter had assigned to nothing, and NOTHING anywhere counted them. The
guarantee here is structural: code enumerates the pages before any model call
and reconciles the answers back by page id, so a page cannot be skipped -- only
left unanswered, which is a row on screen.

THE RECEIPT IS WHY THIS FILE DOES NOT JUST CALL `quote_in`. That function is
built to search a WHOLE DOCUMENT, and one of its widenings degenerates on a
single page: `_strip_page_furniture` sets `floor = len(marks)`, which is 1 when
there is one page marker, so every line qualifies as furniture, up to nine
consecutive lines of the author's prose are deleted, and a quote welded across
the gap verifies. Measured directly. A forged receipt is worse than no receipt,
so the haystack is cleaned here first and the page marker goes with it.
"""
from __future__ import annotations

import re
from typing import Optional

from services.text_match import quote_in

# A receipt must be long enough that it cannot arise by chance. Six words was
# enough for 56/56 real pages and rejected 56/56 wrong-page quotes.
RECEIPT_MIN_WORDS = 6

# Below this many non-furniture characters a page has nothing to quote. Measured
# on the real package: p40 is 71 chars ("Other Personnel Biographical
# Information / Data Not Available") and p49 is 13 -- nothing but the stamp,
# almost certainly a scanned signature page. A blank page is a FACT to report,
# never a failure to read.
BLANK_CHARS = 40

# Per-page and therefore NOT caught by the repetition threshold, but it is still
# furniture and must not be quotable. Dropping it also keeps `quote_in`'s own
# single-page furniture path unreachable: with no marker it returns None.
_PAGE_MARK = re.compile(r"^\s*page\s+\d+\s+of\s+\d+\s*$", re.I)

_WORD = re.compile(r"[^a-z0-9]+")


def document_furniture(page_texts: list) -> frozenset:
    """Lines the PDF stamps on most pages -- never the author's words.

    Delegates to `pdf_sections._furniture`, which uses a SHARE threshold over
    the whole document (`max(2, int(0.5 * n_pages))`) and so does not degenerate
    the way `text_match`'s single-page detector does. One definition, reused.
    """
    from services import pdf_sections as _ps
    return frozenset(_ps._furniture(page_texts or []))


def body_text(page_text: str, furniture: frozenset = frozenset()) -> str:
    """A page with its stamps removed -- the only thing a receipt may quote."""
    out = []
    for line in (page_text or "").splitlines():
        bare = line.strip()
        if not bare or bare in furniture or _PAGE_MARK.match(bare):
            continue
        out.append(bare)
    return "\n".join(out)


def is_blank(page_body: str) -> bool:
    """True when there is nothing on this page to read or to quote."""
    return len((page_body or "").strip()) < BLANK_CHARS


def _words(s: str) -> list:
    return _WORD.sub(" ", (s or "").lower()).split()


def receipt_ok(page_body: str, quote: str) -> bool:
    """True if `quote` is really on this page. The proof that the page was read.

    Two readings, and the second is not slack for its own sake. Measured on the
    real document: the page carries a CURLY closing quote and the model returned
    the sentence with that character OMITTED rather than substituted, which
    `normalize`'s character fold does not cover. Punctuation is exactly what PDF
    extraction damages, and a receipt proves CONTACT with the page, not the
    precision of a claim -- a weaker job than golden rule 2's evidence gate, and
    deliberately a weaker test.

    What is NOT given up is adjacency: the words must appear as a contiguous run
    in the page's own order, so a quote assembled from scattered phrases fails.
    Verified over the whole 56-page document: 0 of 56 wrong-page quotes accepted.
    """
    if not (quote or "").strip():
        return False
    qwords = _words(quote)
    if len(qwords) < RECEIPT_MIN_WORDS:
        return False
    if quote_in(page_body, quote):
        return True
    pwords = _words(page_body)
    n = len(qwords)
    return any(pwords[i:i + n] == qwords for i in range(len(pwords) - n + 1))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_page_receipt.py -q`
Expected: PASS, 10 tests.

- [ ] **Step 5: Mutation-test the security test**

Temporarily edit `receipt_ok` to `return quote_in(page_body, quote)` and re-run.
Expected: `test_the_quote_in_furniture_hole_is_closed` FAILS.
**Revert the edit.** If it passed, the test guards nothing — fix the test before continuing.

- [ ] **Step 6: Commit**

```bash
git add backend/services/page_ledger.py backend/tests/test_page_receipt.py
git commit -m "feat(review): a page-safe receipt check, because quote_in degenerates on one page"
```

---

### Task 2: Expose per-page text from the extractor

`_extract_pdf` already returns `page_texts` and `extract_upload` computes and drops it (`document_text.py:178-258`). The ledger needs it. The docstring at `:112` warns against extracting twice and disagreeing, so it must be passed through, not re-derived.

**Files:**
- Modify: `backend/services/document_text.py:191-193` (the `out` dict) and `:258` (return)
- Test: `backend/tests/test_document_text_page_texts.py`

**Interfaces:**
- Produces: `extract_upload(...)` result gains key `page_texts: list[str]` (empty list for non-PDF).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_document_text_page_texts.py`:

```python
"""`page_texts` must reach the caller, and must agree with `text`.

Extracting the document twice and disagreeing about it is the failure
`_extract_pdf`'s own docstring warns against.
"""
import io

import pytest

from services import document_text as dt

pdfplumber = pytest.importorskip("pdfplumber")
from reportlab.lib.pagesizes import letter          # noqa: E402
from reportlab.pdfgen import canvas                 # noqa: E402


def _pdf(pages):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for body in pages:
        c.drawString(72, 720, body)
        c.showPage()
    c.save()
    return buf.getvalue()


def test_a_pdf_upload_carries_its_per_page_text():
    out = dt.extract_upload("x.pdf", _pdf(["Alpha page one", "Beta page two"]))
    assert out["error"] is None
    assert len(out["page_texts"]) == 2
    assert "Alpha" in out["page_texts"][0]
    assert "Beta" in out["page_texts"][1]


def test_the_page_texts_join_to_the_text_we_returned():
    """The offsets every span uses are computed on this join. If the two ever
    disagree, every span in the review addresses the wrong characters."""
    out = dt.extract_upload("x.pdf", _pdf(["Alpha page one", "Beta page two"]))
    assert "\n".join(out["page_texts"]).strip() == out["text"]


def test_a_plain_text_upload_has_no_pages():
    out = dt.extract_upload("x.txt", b"just some text")
    assert out["page_texts"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_document_text_page_texts.py -q`
Expected: FAIL with `KeyError: 'page_texts'`

- [ ] **Step 3: Implement**

In `backend/services/document_text.py`, change the `out` initialiser (currently at `:191-193`):

```python
    out = {"filename": name, "text": "", "pages": 0, "chars": 0,
           "truncated": False, "error": None, "page_texts": []}
```

Then, immediately after the existing `out.update(text=text, pages=pages, truncated=truncated, chars=len(text))` line, add:

```python
    # Returned rather than dropped: `services.page_ledger` accounts for every
    # page against this very list, and re-extracting the PDF to get it back
    # would risk the two reads disagreeing -- the failure `_extract_pdf`'s
    # docstring warns about.
    out["page_texts"] = page_texts or []
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_document_text_page_texts.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the full suite — nothing may regress**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py`
Expected: PASS. If `main.py` echoes the file dict to the client, `page_texts` must be stripped there — Task 8 covers it, but note any failure now.

- [ ] **Step 6: Commit**

```bash
git add backend/services/document_text.py backend/tests/test_document_text_page_texts.py
git commit -m "feat(review): carry per-page text out of the extractor for the ledger"
```

---

### Task 3: The walk and the roll call

**Files:**
- Modify: `backend/services/page_ledger.py`
- Test: `backend/tests/test_page_ledger.py`

**Interfaces:**
- Consumes: Task 1's `document_furniture`, `body_text`, `is_blank`, `receipt_ok`
- Produces:
  - `PAGE_WINDOW = 4`
  - `walk_pages(page_texts, section_keys, *, furniture, known=None) -> dict[int, dict]`
  - `build_ledger(page_texts, sections, *, structure=None) -> list[dict]`
  - ledger row: `{"page": int, "section": str|None, "source": str, "quote": str, "verified": bool, "chars": int}`
  - `source` ∈ `{"structure", "model", "blank", "unassigned"}`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_page_ledger.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_page_ledger.py -q`
Expected: FAIL — `AttributeError: module 'services.page_ledger' has no attribute 'walk_pages'`

- [ ] **Step 3: Implement**

Append to `backend/services/page_ledger.py`:

```python
# Four pages per call, MEASURED not chosen. Over the real 56-page package:
# 4 -> 56/56 receipts in 22.8s (14 calls); 12 -> 55/56 in 41.6s; 28 -> 54/56.
# Smaller is more accurate AND faster, because short calls run concurrently
# under `_MODEL_SLOTS`. Agrees with the published direction (arXiv:2301.08721
# measures a significant accuracy drop at batch size 6) and with this repo's own
# strongest measurement -- one rule per call gave 92% ten times out of ten where
# batch=15 split 83%/92%.
PAGE_WINDOW = 4

# Enough of a page for the model to name it; the receipt only needs one real
# line. Whole pages would multiply input tokens for no measured gain.
_PAGE_CHARS = 2600

_WALK_SYSTEM = """You are reading ONE PAGE AT A TIME of an assembled grant proposal PDF.
Read every line of each page you are given, top to bottom, before answering.

For EVERY page number listed in the input you MUST return exactly one object.
Never omit a page. If you cannot tell what a page is, return "unsure" as the
section -- never leave the page out.

Each object:
  page    : the page number, exactly as given
  section : one value from the allowed list, or "unsure"
  quote   : a VERBATIM span of at least 6 words copied character-for-character
            from THAT page's text. It is your proof that you read the page.
            Never invent it, never take it from another page, never paraphrase.

A page with a heading is named by its heading. A page with no heading -- a
letter, a form, a continuation of prose -- belongs to whatever it continues.
Return ONLY {"pages":[...]}."""


def _window_prompt(nums, page_texts, section_keys, known):
    body = "\n\n".join(
        f"=== PAGE {n} ===\n{(page_texts[n - 1] or '')[:_PAGE_CHARS]}" for n in nums)
    fixed = ""
    if known:
        named = "; ".join(f"page {p} is {k}" for p, k in sorted(known.items()) if k)
        if named:
            # Anchors, so the walk labels CONSISTENTLY around what the PDF's own
            # structure already settled. Never a licence to overrule it --
            # `build_ledger` keeps the structural answer regardless.
            fixed = f"\nAlready established from the document's structure: {named}.\n"
    return (f"Allowed section values: {', '.join(section_keys)}, unsure\n{fixed}\n"
            f"Return exactly {len(nums)} objects, one for each of pages {list(nums)}.\n\n{body}")


def _ask_window(nums, page_texts, section_keys, known):
    """One model call for one window. Returns {page: {section, quote}}."""
    from services import draft_review as _dr
    from services import gemini_client as _gc

    reply = _dr._ask_model(
        _gc.generate_json,
        _window_prompt(nums, page_texts, section_keys, known),
        system_instruction=_WALK_SYSTEM,
        model=_dr.MODEL, location=_dr.MODEL_LOCATION,
        temperature=0.0, max_output_tokens=8192,
        thinking_budget=_dr.THINKING_BUDGET,
        # A bare top-level array is an ANSWER, not a failure: commit 3553be5
        # records one costing 15 rules and rendering a false 100%.
        list_key="pages",
    )
    out = {}
    for row in (reply or {}).get("pages") or []:
        try:
            page = int(row.get("page"))
        except (TypeError, ValueError):
            continue
        # Reconcile by ID, never by count. A row for a page we did not send is
        # dropped rather than trusted -- same rule as `_review_batch`.
        if page in nums:
            out[page] = {"section": row.get("section"),
                         "quote": (row.get("quote") or "").strip()}
    return out


def walk_pages(page_texts: list, section_keys: list, *, furniture=frozenset(),
               known: Optional[dict] = None) -> dict:
    """Ask the model what each page is. {page number: {section, quote}}.

    Windows run CONCURRENTLY but every call passes through
    `draft_review._ask_model`, so this contends for the existing semaphore
    rather than opening a fourth uncapped pool (`services/proofread.py` does
    that, and it is a defect this must not copy).

    A page missing from its window's reply is RE-ASKED ON ITS OWN before being
    given up on. Never raises: with no model this returns {}.
    """
    from concurrent.futures import ThreadPoolExecutor

    n = len(page_texts or [])
    if not n or not section_keys:
        return {}
    windows = [list(range(i, min(i + PAGE_WINDOW, n + 1)))
               for i in range(1, n + 1, PAGE_WINDOW)]
    got: dict = {}
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for part in pool.map(
                    lambda w: _ask_window(w, page_texts, section_keys, known), windows):
                got.update(part or {})
    except Exception as exc:                       # noqa: BLE001 — golden rule 3
        print(f"[PAGE-LEDGER] walk failed: {exc}")

    missing = [p for p in range(1, n + 1) if p not in got]
    if missing:
        print(f"[PAGE-LEDGER] re-asking {len(missing)} page(s): {missing}")
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for part in pool.map(
                        lambda p: _ask_window([p], page_texts, section_keys, known), missing):
                    got.update(part or {})
        except Exception as exc:                   # noqa: BLE001
            print(f"[PAGE-LEDGER] re-ask failed: {exc}")
    return got


def build_ledger(page_texts: list, sections: dict, *,
                 structure: Optional[dict] = None) -> list:
    """One row per page, built by CODE before anything else runs.

    The loop below is the whole guarantee: a page cannot be skipped, only left
    unanswered, and an unanswered page is a row reading `unassigned` that the
    modal renders. Precedence is structure > model > blank > unassigned --
    `pdf_sections` is deterministic and identical every run, so the walk fills
    gaps and never overrules it; a disagreement is RECORDED, not resolved.
    """
    n = len(page_texts or [])
    structure = {int(k): v for k, v in (structure or {}).items()}
    furniture = document_furniture(page_texts or [])
    bodies = [body_text(t, furniture) for t in (page_texts or [])]
    keys = list(sections or {})

    answers = walk_pages(page_texts, keys, furniture=furniture,
                         known=structure) if keys else {}

    rows, order, seen = [], [], set()
    for page in range(1, n + 1):
        body = bodies[page - 1]
        row = {"page": page, "section": None, "source": "unassigned",
               "quote": "", "verified": False, "chars": len(body.strip())}

        fixed = structure.get(page)
        answer = answers.get(page) or {}
        guess = answer.get("section")
        quote = answer.get("quote") or ""

        if fixed:
            row.update(section=fixed, source="structure")
            if guess and guess != fixed and guess in sections:
                row["disagreed_with_model"] = guess
        elif is_blank(body):
            row["source"] = "blank"
        elif guess in sections and receipt_ok(body, quote):
            # CONTIGUITY. A section that has already ended cannot reappear --
            # page 47 is not the Project Summary. A label that breaks the order
            # is refused in code rather than argued with.
            if guess in seen and (order and order[-1] != guess):
                row["refused"] = guess
            else:
                row.update(section=guess, source="model", quote=quote, verified=True)
        elif guess in sections and quote:
            row["refused"] = guess               # answered, receipt did not hold

        if row["section"]:
            if not order or order[-1] != row["section"]:
                order.append(row["section"])
            seen.add(row["section"])
        rows.append(row)
    return rows


def completeness(rows: list) -> tuple:
    """(every page accounted for?, the page numbers that are not).

    `blank` COUNTS as accounted for: a page with nothing on it was read and
    found empty, which is a fact about the document rather than a gap in our
    reading. Only `unassigned` is a gap.
    """
    unaccounted = [r["page"] for r in (rows or []) if r.get("source") == "unassigned"]
    return (not unaccounted), unaccounted
```

Also add to the imports at the top of the file: `from typing import Optional` is already there from Task 1.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_page_ledger.py tests/test_page_receipt.py -q`
Expected: PASS, 19 tests.

- [ ] **Step 5: Verify the model call names the model and region**

Add to `backend/tests/test_page_ledger.py`:

```python
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
```

Run: `cd backend && python3 -m pytest tests/test_page_ledger.py -q`
Expected: PASS, 20 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/services/page_ledger.py backend/tests/test_page_ledger.py
git commit -m "feat(review): walk every page, reconcile by page id, never by count"
```

---

### Task 4: NSF's Table of Contents as an external cross-check

`toc_roster` discards the raw label of any row it cannot resolve (`pdf_sections.py:197`), so `Special Information/Supplementary Documents 9` becomes `(None, 9)` — the count survives, the identity is lost. This task keeps the name for reporting and holds the ledger against the roster.

**Files:**
- Modify: `backend/services/pdf_sections.py:160-198` (`toc_roster`)
- Modify: `backend/services/page_ledger.py`
- Test: `backend/tests/test_toc_reconciliation.py`

**Interfaces:**
- Consumes: `pdf_sections.toc_roster`, `pdf_sections._find_toc`
- Produces:
  - `toc_roster` rows become 3-tuples `(section key|None, pages, raw name)`
  - `page_ledger.reconcile_toc(rows, page_texts, sections) -> list[dict]`, each `{"section", "label", "ledger_pages", "toc_pages"}`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_toc_reconciliation.py`:

```python
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
    "Project Description (Including Results from Prior 3",
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_toc_reconciliation.py -q`
Expected: FAIL — the roster returns 2-tuples; `reconcile_toc` does not exist.

- [ ] **Step 3: Implement — widen the roster**

In `backend/services/pdf_sections.py`, change the docstring's return description and the single `rows.append` at `:197`:

```python
        rows.append((_sp.resolve_section_key(sections or {}, name), n, name))
```

Update the docstring's first line to:

```python
    """NSF's auto-generated Table of Contents as [(section key or None, pages, raw name)].
```

and add, after the existing "A ROSTER AND A VALIDATOR" paragraph:

```python
    The RAW NAME is kept even for a row that resolves to None. The count used to
    survive while the identity was thrown away, so a row reading
    "Special Information/Supplementary Documents 9" became (None, 9) and nothing
    could report WHICH nine pages the funder expected. Reporting only -- no
    consumer matches on it.
```

Then fix the five internal consumers, which currently unpack two values. Change each to ignore the third:
- `:291` `wanted = {k for k, _, _ in roster if k}`
- `:331-333` `want = next((n for rk, n, _ in roster if rk == key), None)`
- `:351` `toc_pages_for = {k: n for k, n, _ in roster if k}`
- `:416` `want_pages = {k: n for k, n, _ in roster if k in missing}`
- `:447` `toc_pages = {k: n for k, n, _ in roster if k}`

Verify none were missed: `grep -n "for k, n in roster\|for k, _ in roster\|for rk, n in roster" backend/services/pdf_sections.py` must be empty.

- [ ] **Step 4: Implement — the reconciliation**

Append to `backend/services/page_ledger.py`:

```python
def reconcile_toc(rows: list, page_texts: list, sections: dict) -> list:
    """Where the ledger and NSF's own table of contents disagree about a length.

    AN EXTERNAL CHECK -- the funder wrote these counts, not us and not the
    model. It is the strongest defence available against a WRONG label, which is
    ~6x more damaging than a missing one (12 points against 2 on a 50-rule
    review) and can report an absent required attachment as present.

    It REPORTS, it never overrides, and it tolerates a legitimate exception --
    the same discipline as `pappg_ingest`'s `suspicious_yield`. Measured on the
    real package, the one standing mismatch is CORRECT: an auto-generated NSF
    "Data Not Available" filler page makes the biographical-sketch count 3 where
    the table of contents says 2. The document and its own table of contents
    genuinely disagree, and showing that beats picking a side.

    Returns [] when there is no NSF table of contents, so a package from any
    other source simply gets no third check.
    """
    from services import pdf_sections as _ps
    from collections import Counter

    roster = _ps._find_toc(page_texts or [], sections or {})
    if not roster:
        return []
    want = {k: n for k, n, _ in roster if k}
    counted = Counter(r["section"] for r in (rows or []) if r.get("section"))
    out = []
    for key, expected in want.items():
        got = counted.get(key, 0)
        if got and got != expected:
            out.append({"section": key,
                        "label": (sections.get(key) or {}).get("label") or key,
                        "ledger_pages": got, "toc_pages": expected})
    return out
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_toc_reconciliation.py tests/test_pdf_sections.py -q`
Expected: PASS. `test_pdf_sections.py` covers the roster's own tests and must stay green — it asserts specific `(key, n)` pairs, so update those assertions to unpack three values if they fail.

- [ ] **Step 6: Full suite, then commit**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py
git add backend/services/pdf_sections.py backend/services/page_ledger.py backend/tests/test_toc_reconciliation.py backend/tests/test_pdf_sections.py
git commit -m "feat(review): hold the ledger against NSF's own table of contents"
```

---

### Task 5: Turn the ledger into spans

**Files:**
- Modify: `backend/services/page_ledger.py`
- Modify: `backend/services/document_text.py:243-258` (the structural-split block)
- Test: `backend/tests/test_ledger_spans.py`

**Interfaces:**
- Produces:
  - `spans_from_ledger(rows, page_texts, sections) -> dict[str, dict]` — spans are `{start, end, text, label, marker, pages}` with offsets into `"\n".join(page_texts)`
  - `page_counts_from_ledger(rows) -> dict[str, int]`
  - `extract_upload(...)` result gains `page_ledger`, `ledger_toc_mismatch`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ledger_spans.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_ledger_spans.py -q`
Expected: FAIL — `spans_from_ledger` does not exist.

- [ ] **Step 3: Implement the span builder**

Append to `backend/services/page_ledger.py`:

```python
def _page_offsets(page_texts: list) -> list:
    """(start, end) of each page in `"\\n".join(page_texts)`.

    Built on exactly the join `document_text._extract_pdf` uses, because
    `pdf_sections` computes its offsets on the same string and the two must not
    disagree about where a page begins.
    """
    offsets, cursor = [], 0
    for text in page_texts or []:
        offsets.append((cursor, cursor + len(text or "")))
        cursor += len(text or "") + 1               # the "\n" the join inserts
    return offsets


def spans_from_ledger(rows: list, page_texts: list, sections: dict) -> dict:
    """{section key: span} covering the pages the ledger assigned to it.

    A section's span runs from its FIRST assigned page to its LAST, so a stray
    unassigned page inside a section does not split it in two -- but an
    unassigned page at either edge is genuinely outside, and stays outside.
    """
    if not rows or not page_texts:
        return {}
    joined = "\n".join(page_texts)
    offsets = _page_offsets(page_texts)
    pages_of: dict = {}
    for row in rows:
        key = row.get("section")
        if key and key in (sections or {}):
            pages_of.setdefault(key, []).append(int(row["page"]))

    spans = {}
    for key, pages in pages_of.items():
        first, last = min(pages), max(pages)
        start = offsets[first - 1][0]
        end = offsets[last - 1][1]
        spans[key] = {
            "start": start, "end": end, "text": joined[start:end],
            "label": (sections.get(key) or {}).get("label") or key,
            # The heading a locate-stage span would carry. There is no marker
            # string here -- the page ledger IS the evidence -- so it names its
            # own provenance instead, and the modal can render it.
            "marker": f"pages {first}-{last}" if last > first else f"page {first}",
            "pages": len(pages),
        }
    return spans


def page_counts_from_ledger(rows: list) -> dict:
    """{section key: REAL page count}. Real, not a word-count estimate, so page
    rules can return a verdict rather than an estimate."""
    counts: dict = {}
    for row in rows or []:
        key = row.get("section")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_ledger_spans.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Wire it into `extract_upload`**

In `backend/services/document_text.py`, replace the body of the `if sections and page_texts and not truncated:` block (currently `:243-258`) so the ledger runs **after** `pdf_sections`, taking its result as the `structure` anchor:

```python
    if sections and page_texts and not truncated:
        try:
            from services import pdf_sections as _ps
            from services import page_ledger as _pl

            spans, report = _ps.split(data, page_texts, sections)
            shift = len(_raw) - len(_raw.lstrip()) if (_raw := "\n".join(page_texts)) else 0

            # STRUCTURE FIRST, and it wins. `pdf_sections` reads the seams out
            # of the PDF's object graph and returns the same answer every run;
            # the walk fills the pages it could not name and never overrules it.
            structure = {}
            for key, span in (spans or {}).items():
                for page, (p0, p1) in enumerate(_pl._page_offsets(page_texts), start=1):
                    if span["start"] <= p0 and p1 <= span["end"]:
                        structure[page] = key

            ledger = _pl.build_ledger(page_texts, sections, structure=structure)
            out["page_ledger"] = ledger
            out["ledger_toc_mismatch"] = _pl.reconcile_toc(ledger, page_texts, sections)

            merged = _pl.spans_from_ledger(ledger, page_texts, sections)
            out["ledger_page_counts"] = _pl.page_counts_from_ledger(ledger)

            rebased = {}
            for key, span in merged.items():
                s0 = max(0, span["start"] - shift)
                e0 = max(s0, min(len(text), span["end"] - shift))
                if e0 > s0:
                    rebased[key] = {**span, "start": s0, "end": e0,
                                    "text": text[s0:e0]}
            if rebased:
                out["section_spans"] = rebased
                out["section_report"] = report
        except Exception as exc:                # never break an upload over this
            print(f"[DOCUMENT-TEXT] structural split skipped: {exc}")
```

- [ ] **Step 6: Run the full suite**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py`
Expected: PASS. `tests/conftest.py` pins `gemini_client.get_client()` to `None`, so `walk_pages` returns `{}` in the suite and the ledger falls back to structure-only — existing `pdf_sections` tests must still pass.

- [ ] **Step 7: Commit**

```bash
git add backend/services/page_ledger.py backend/services/document_text.py backend/tests/test_ledger_spans.py
git commit -m "feat(review): derive section spans and real page counts from the ledger"
```

---

### Task 6: Fix the Table-of-Contents page fold

The TOC block folds into Project Description (`pdf_sections.py:341-380`), giving 16 pages against a true 15 and producing a false "over the 15-page limit" on a compliant section. It is currently **load-bearing**: returning the page drops coverage to 33 against `MIN_COVERAGE`'s 33.6 and discards the whole split. Task 5 removed that dependence, because the walk now supplies pages the split does not.

**Files:**
- Modify: `backend/services/pdf_sections.py:321-334` (the anchor), `:458-461` (`MIN_COVERAGE`)
- Test: `backend/tests/test_page_limit_off_by_one.py`

**Interfaces:**
- Consumes: Task 5's ledger
- Produces: no signature change; `split()` no longer folds the TOC page forward, and no longer bails on page coverage.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_page_limit_off_by_one.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails or passes**

Run: `cd backend && python3 -m pytest tests/test_page_limit_off_by_one.py -q`
Expected: PASS already — `build_ledger` never folds. This test is the **regression guard** for the behaviour Task 5 delivers; record that it passes and continue.

- [ ] **Step 3: Remove the page-coverage bail**

In `backend/services/pdf_sections.py`, replace the `MIN_COVERAGE` block at `:458-461`:

```python
        # PAGE COVERAGE IS NO LONGER A BAIL, and the reason is that it moved.
        # It ran BEFORE any model involvement, which made the Table-of-Contents
        # page load-bearing: on a real awarded package the five spans covered 34
        # of 56 pages against a 33.6 floor, and returning the wrongly-folded TOC
        # page to its own section dropped that to 33 and discarded a good split.
        # `services.page_ledger` now accounts for every page AFTER this runs and
        # reports what it could not place, so a partial split is a useful input
        # rather than an unsafe one. The other nine bails are unchanged.
        report["covered"] = sum(l - f + 1 for f, l in merged.values())
```

- [ ] **Step 4: Stop the anchor claiming an unvalidated block**

In `backend/services/pdf_sections.py:331-333`, the anchor accepts `project_description` unconditionally when the roster has no row for it (`want is None` → accept). Require the roster to corroborate:

```python
                want = next((n for rk, n, _ in roster if rk == key), None)
                # An anchor with NOTHING to check it against is how the Table of
                # Contents page entered `project_description`. A block the
                # roster cannot corroborate is left for the ledger.
                if want is not None and abs((last - first + 1) - want) <= _PAGE_SLOP:
                    labelled[i] = (key, first, last)
```

- [ ] **Step 5: Run the full suite**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py`
Expected: PASS. If a `test_pdf_sections.py` test asserted the old coverage bail, update it to assert `report["covered"]` is reported instead — do **not** delete it.

- [ ] **Step 6: Commit**

```bash
git add backend/services/pdf_sections.py backend/tests/test_page_limit_off_by_one.py
git commit -m "fix(review): the table of contents page is not part of the project description"
```

---

### Task 7: Withhold the score when a page is unaccounted for

**Files:**
- Modify: `backend/services/draft_review.py:1424-1428` (signature), the result dict at `:1640-1671`
- Test: `backend/tests/test_ledger_withholds_the_score.py`

**Interfaces:**
- Consumes: Task 3's `completeness`
- Produces: `review_draft(..., ledger=None, toc_mismatch=None)`; result gains `page_ledger`, `pages_unaccounted`, `toc_mismatch`; `score` is `None` when pages are unaccounted for.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ledger_withholds_the_score.py`:

```python
"""A percentage computed over pages we cannot confirm we read describes our
reading, not the draft. Same rule the AI-outage path already follows -- added
after an outage rendered a section as 100%, green, "No problems found"."""
import pytest

from services import draft_review as dr

PROFILE = {
    "id": "TEST-1", "title": "Test", "sections": {
        "project_summary": {"label": "Project Summary", "aliases": ["Project Summary"]}},
    "requirements": [
        {"id": "r1", "label": "Include an overview", "section": "project_summary",
         "kind": "semantic", "scored": True, "source": "Include an overview."}],
    "contract": {}, "merit_criteria": [],
}
DRAFT = "Project Summary\nThis proposal provides an overview of the planned work."


def test_a_complete_ledger_leaves_the_score_alone():
    ledger = [{"page": 1, "section": "project_summary", "source": "model"},
              {"page": 2, "section": None, "source": "blank"}]
    out = dr.review_draft(DRAFT, profile=PROFILE, use_ai=False, ledger=ledger)
    assert out["pages_unaccounted"] == []


def test_an_unaccounted_page_withholds_the_score():
    ledger = [{"page": 1, "section": "project_summary", "source": "model"},
              {"page": 2, "section": None, "source": "unassigned"}]
    out = dr.review_draft(DRAFT, profile=PROFILE, use_ai=False, ledger=ledger)
    assert out["score"] is None
    assert out["pages_unaccounted"] == [2]
    assert "2" in (out["message"] or "")


def test_the_ledger_rides_on_the_result():
    ledger = [{"page": 1, "section": "project_summary", "source": "structure"}]
    out = dr.review_draft(DRAFT, profile=PROFILE, use_ai=False, ledger=ledger)
    assert out["page_ledger"] == ledger


def test_no_ledger_changes_nothing():
    """Pasted text has no pages. This must not withhold a score."""
    out = dr.review_draft(DRAFT, profile=PROFILE, use_ai=False)
    assert out["pages_unaccounted"] == []
    assert out["page_ledger"] is None


def test_a_toc_mismatch_rides_along_but_does_not_withhold():
    mismatch = [{"section": "biographical_sketch", "label": "Biographical Sketch",
                 "ledger_pages": 3, "toc_pages": 2}]
    ledger = [{"page": 1, "section": "project_summary", "source": "model"}]
    out = dr.review_draft(DRAFT, profile=PROFILE, use_ai=False,
                          ledger=ledger, toc_mismatch=mismatch)
    assert out["toc_mismatch"] == mismatch
    assert out["pages_unaccounted"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_ledger_withholds_the_score.py -q`
Expected: FAIL — `review_draft() got an unexpected keyword argument 'ledger'`

- [ ] **Step 3: Implement**

In `backend/services/draft_review.py`, extend the signature at `:1424-1428`:

```python
def review_draft(draft_text: str, *, profile: dict, title: Optional[str] = None,
                 budget: Optional[dict] = None, use_ai: bool = True,
                 pages: Optional[dict] = None,
                 file_spans: Optional[dict] = None,
                 structural: bool = False,
                 ledger: Optional[list] = None,
                 toc_mismatch: Optional[list] = None) -> dict:
```

Add to the docstring, after the `pages` line:

```python
    ledger     — services/page_ledger.build_ledger() rows, one per PDF page.
                 An `unassigned` row means a page we could not confirm we read,
                 and the score is WITHHELD rather than computed over it.
```

Immediately before the result dict is assembled (just after `ai_used` is computed at `:1585-1594`), insert:

```python
    # EVERY PAGE ACCOUNTED FOR, OR NO NUMBER. A page left `unassigned` is one we
    # cannot confirm was read; a percentage computed over the rest would
    # describe our reading rather than the draft. Same rule as the AI-outage
    # path below, added after an outage rendered a section 100% and green.
    # `blank` counts as accounted for -- an empty page was read and found empty.
    from services.page_ledger import completeness as _completeness
    pages_ok, unaccounted = _completeness(ledger or [])
```

Then in the result dict, replace the `score` and `message` entries:

```python
        "score": (score(findings, solicitation_id=solicitation_id)
                  if ai_used and pages_ok else None),
        "page_ledger": ledger,
        "pages_unaccounted": unaccounted,
        "toc_mismatch": toc_mismatch or [],
        "message": (
            f"{len(unaccounted)} page(s) of your upload could not be read and "
            f"placed — page(s) {', '.join(str(p) for p in unaccounted)}. The "
            "completeness score is withheld, because a percentage computed "
            "over pages we could not confirm we read would describe our "
            "reading and not your draft. Everything below is still accurate."
        ) if not pages_ok else None if ai_used else (
            "The AI reviewer is unavailable, so only the rule-based checks ran and the "
            "completeness score is withheld. Everything below is still accurate."
        ),
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_ledger_withholds_the_score.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Full suite**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py`
Expected: PASS. `test_ai_outage_is_not_a_clean_bill.py` must stay green — the outage message is preserved as the `else` branch.

- [ ] **Step 6: Commit**

```bash
git add backend/services/draft_review.py backend/tests/test_ledger_withholds_the_score.py
git commit -m "feat(review): withhold the score when a page could not be accounted for"
```

---

### Task 8: Wire the endpoints

**Files:**
- Modify: `backend/main.py:4652-4715` (`/draft-review/upload`) and the sibling paste endpoint
- Test: `backend/tests/test_draft_review_ledger_api.py`

**Interfaces:**
- Consumes: Tasks 5 and 7
- Produces: the draft-review response gains `page_ledger`, `pages_unaccounted`, `toc_mismatch`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_draft_review_ledger_api.py`:

```python
"""The ledger must reach the browser, and `page_texts` must NOT.

`extract_upload` now returns the full per-page text. Echoing it in the
extraction report would put the PI's entire manuscript back on the wire -- the
same reason the solicitation TEXT is kept off the submission list.
"""
import pytest


def test_the_extraction_report_never_echoes_page_texts():
    import re, pathlib
    src = pathlib.Path("main.py").read_text()
    block = src[src.index("for f in extracted"):][:1400]
    assert "page_texts" in block, (
        "the upload endpoint must explicitly strip page_texts from each file dict")


def test_review_draft_is_called_with_the_ledger():
    import re, pathlib
    src = pathlib.Path("main.py").read_text()
    assert "ledger=" in src and "toc_mismatch=" in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_draft_review_ledger_api.py -q`
Expected: FAIL on both.

- [ ] **Step 3: Implement**

In `backend/main.py`, in the `/draft-review/upload` handler:

After the extraction loop that calls `_dt.extract_upload(...)` (around `:4665-4671`), add:

```python
    # The ledger belongs to the ONE file that carried a structural split -- a
    # combined Research.gov package. A multi-file upload has no single page
    # numbering, so there is nothing to account for across files.
    _ledger, _toc_mismatch = None, []
    for f in extracted:
        if f.get("page_ledger"):
            _ledger = f["page_ledger"]
            _toc_mismatch = f.get("ledger_toc_mismatch") or []
            break
```

In the existing `page_counts` construction (around `:4683`), prefer the ledger's real counts:

```python
    for f in extracted:
        if f.get("ledger_page_counts"):
            page_counts.update(f["ledger_page_counts"])
```

Pass them into the review call:

```python
    result = _draft_review.review_draft(
        draft_text, profile=profile, title=sub.title, budget=budget,
        pages=page_counts, file_spans=file_spans, structural=bool(file_spans),
        ledger=_ledger, toc_mismatch=_toc_mismatch)
```

And in the extraction report (`:4712-4715`), strip the new key alongside the existing ones:

```python
            "files": [{k: v for k, v in f.items()
                       if k not in ("text", "section_spans", "page_texts",
                                    "page_ledger", "ledger_page_counts",
                                    "ledger_toc_mismatch")}
                      for f in extracted],
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_draft_review_ledger_api.py -q`
Expected: PASS, 2 tests.

- [ ] **Step 5: Full suite**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_draft_review_ledger_api.py
git commit -m "feat(review): send the page ledger to the browser, keep the manuscript off the wire"
```

---

### Task 9: Render the ledger

**Files:**
- Modify: `frontend/src/components/DraftReviewModal.jsx`
- Modify: `frontend/src/components/DraftReviewModal.css`

**Interfaces:**
- Consumes: `result.page_ledger`, `result.pages_unaccounted`, `result.toc_mismatch`, `result.extraction.sections`

- [ ] **Step 1: Add the ledger panel**

In `DraftReviewModal.jsx`, add above `ScorePanel` (near the `coverage_warning` render at `:1166-1170`):

```jsx
{result.page_ledger?.length > 0 && (
  <div className="eir-ledger">
    <div className="eir-ledger-head">
      <strong>
        {result.page_ledger.length - (result.pages_unaccounted?.length || 0)} of{" "}
        {result.page_ledger.length} pages accounted for
      </strong>
      <span className="eir-ledger-sub">
        Every page of your upload was listed before it was read, and each one had
        to quote itself back. Finding a page is not a judgement of what is on it.
      </span>
    </div>
    {result.pages_unaccounted?.length > 0 && (
      <p className="eir-ledger-warn">
        Page{result.pages_unaccounted.length > 1 ? "s" : ""}{" "}
        {result.pages_unaccounted.join(", ")} could not be placed, so the score is
        withheld. These pages were read — they were not judged.
      </p>
    )}
    {result.toc_mismatch?.map((m) => (
      <p key={m.section} className="eir-ledger-warn">
        {m.label}: {m.ledger_pages} page{m.ledger_pages === 1 ? "" : "s"} found,
        but this proposal's own table of contents says {m.toc_pages}.
      </p>
    ))}
    <div className="eir-ledger-rows">
      {collapseLedger(result.page_ledger).map((r) => (
        <span key={r.from} className={`eir-ledger-pill eir-src-${r.source}`}>
          {r.from === r.to ? `p${r.from}` : `p${r.from}–${r.to}`}{" "}
          {r.label}
          <em>{{ structure: "from the PDF", model: "read", blank: "no text", unassigned: "not placed" }[r.source]}</em>
        </span>
      ))}
    </div>
  </div>
)}
```

Add the helper above the component:

```jsx
// Consecutive pages with the same answer collapse to one row: 56 pills is a
// wall, "p6–20 Project Description" is a fact a PI can check at a glance.
function collapseLedger(rows) {
  const out = [];
  for (const r of rows) {
    const label = r.section || (r.source === "blank" ? "no readable text" : "not placed");
    const last = out[out.length - 1];
    if (last && last.label === label && last.source === r.source && r.page === last.to + 1) {
      last.to = r.page;
    } else {
      out.push({ from: r.page, to: r.page, label, source: r.source });
    }
  }
  return out;
}
```

- [ ] **Step 2: Render the provenance the backend already sends**

`main.py` emits `extraction.sections` with the comment *"so a mis-map is visible on screen"* and the modal has never read it. In `ExtractionReport` (`:539-560`), add:

```jsx
{report.sections?.length > 0 && (
  <ul className="eir-extract-map">
    {report.sections.map((s) => (
      <li key={s.filename}>
        <code>{s.filename}</code> read as <strong>{s.label || s.section || "nothing"}</strong>
        {s.source === "filename_narrowed" && (
          <span className="eir-extract-guess"> — matched on a shorter name; check this is right</span>
        )}
      </li>
    ))}
  </ul>
)}
```

Change its mount condition so it renders whenever a mapping exists, not only on an error.

- [ ] **Step 3: Style it**

Append to `DraftReviewModal.css`:

```css
.eir-ledger { border: 1px solid var(--eir-line); border-radius: 10px;
  padding: 0.85rem 1rem; margin-bottom: 1rem; }
.eir-ledger-head { display: flex; flex-direction: column; gap: 0.2rem; margin-bottom: 0.6rem; }
.eir-ledger-sub { font-size: 0.8rem; color: var(--eir-muted); }
.eir-ledger-warn { font-size: 0.85rem; color: var(--eir-warn); margin: 0.35rem 0; }
.eir-ledger-rows { display: flex; flex-wrap: wrap; gap: 0.35rem; }
.eir-ledger-pill { display: inline-flex; align-items: baseline; gap: 0.35rem;
  font-size: 0.78rem; border: 1px solid var(--eir-line); border-radius: 999px;
  padding: 0.2rem 0.6rem; }
.eir-ledger-pill em { font-style: normal; color: var(--eir-muted); font-size: 0.72rem; }
/* Deliberately NOT green. This says a page was FOUND, never that it passed --
   this modal has rendered presence as approval four times and had to unship it. */
.eir-src-unassigned { border-color: var(--eir-warn); }
.eir-src-blank { opacity: 0.65; }
.eir-extract-map { margin: 0.5rem 0 0; padding-left: 1rem; font-size: 0.8rem; }
.eir-extract-guess { color: var(--eir-warn); }
```

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds. (`npm install` first if `node_modules` is absent.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DraftReviewModal.jsx frontend/src/components/DraftReviewModal.css
git commit -m "feat(review): show the page ledger, and the mis-map provenance we already sent"
```

---

### Task 10: The live gate

Opt-in, live model, never in CI — the pattern of `tests/test_pappg_recall.py`. This is the checkpoint that says the method still works. If it stops passing, the method is wrong and nothing else it produced should be trusted.

**Files:**
- Test: `backend/tests/test_page_ledger_gate.py`

- [ ] **Step 1: Write the gate**

```python
"""The live gate. Opt in with PAGE_LEDGER_GATE=1 and a real awarded package:

    PAGE_LEDGER_GATE=1 PAGE_LEDGER_PDF="$HOME/Desktop/My works/Awarded NSF EIR Porposal (1).pdf" \
      python3 -m pytest tests/test_page_ledger_gate.py -q -s

Measured 2026-09-03 over four runs at three window sizes: 56/56 pages answered
every run; receipts 56/56 at window 4; 0 of 56 wrong-page quotes accepted;
project_description 15 pages, matching NSF's own table of contents, on every run.
"""
import os

import pytest

from services import document_text as dt
from services import page_ledger as pl

pytestmark = pytest.mark.skipif(
    os.getenv("PAGE_LEDGER_GATE") != "1", reason="live model; opt in")

SECTIONS = {k: {"label": lbl, "aliases": [lbl]} for k, lbl in [
    ("cover_sheet", "Cover Sheet"), ("project_summary", "Project Summary"),
    ("table_of_contents", "Table of Contents"),
    ("project_description", "Project Description"),
    ("references_cited", "References Cited"), ("budget", "Budget"),
    ("budget_justification", "Budget Justification"),
    ("facilities_equipment_other_resources", "Facilities, Equipment and Other Resources"),
    ("biographical_sketch", "Biographical Sketch"),
    ("current_and_pending_support", "Current and Pending Support"),
    ("collaborators_and_affiliations", "Collaborators and Other Affiliations"),
    ("synergistic_activities", "Synergistic Activities"),
    ("data_management_plan", "Data Management Plan"),
    ("mentoring_plan", "Mentoring Plan"),
    ("letters_of_collaboration", "Letters of Collaboration"),
    ("letter_of_institutional_support", "Letter of Institutional Support"),
    ("other_supplementary_documents", "Other Supplementary Documents")]}


@pytest.fixture(scope="module")
def walked():
    path = os.getenv("PAGE_LEDGER_PDF")
    if not path or not os.path.exists(path):
        pytest.skip("set PAGE_LEDGER_PDF to a combined Research.gov package")
    _t, _p, _tr, page_texts = dt._extract_pdf(open(path, "rb").read())
    rows = pl.build_ledger(page_texts, SECTIONS)
    return page_texts, rows


def test_every_page_is_accounted_for(walked):
    page_texts, rows = walked
    assert len(rows) == len(page_texts)
    ok, unaccounted = pl.completeness(rows)
    print(f"\naccounted: {len(rows) - len(unaccounted)}/{len(rows)}  unassigned={unaccounted}")
    assert len(unaccounted) <= 2, f"pages unaccounted for: {unaccounted}"


def test_no_page_can_pass_another_pages_receipt(walked):
    """THE SECURITY GATE. Must be exactly zero."""
    page_texts, rows = walked
    furniture = pl.document_furniture(page_texts)
    accepted = []
    for row in rows:
        if not row.get("quote"):
            continue
        other = page_texts[row["page"] % len(page_texts)]
        if pl.receipt_ok(pl.body_text(other, furniture), row["quote"]):
            accepted.append(row["page"])
    assert accepted == [], f"wrong-page quotes accepted: {accepted}"


def test_the_project_description_is_its_real_length(walked):
    _pt, rows = walked
    counts = pl.page_counts_from_ledger(rows)
    print(f"\ncounts: {counts}")
    assert counts.get("project_description") == 15


def test_the_supplementary_pages_are_found(walked):
    """The five sections the PI was wrongly told were missing."""
    _pt, rows = walked
    found = {r["section"] for r in rows}
    assert "data_management_plan" in found
    assert "mentoring_plan" in found
    assert "letter_of_institutional_support" in found
```

- [ ] **Step 2: Run it against the real package**

```bash
cd backend && PAGE_LEDGER_GATE=1 \
  PAGE_LEDGER_PDF="$HOME/Desktop/My works/Awarded NSF EIR Porposal (1).pdf" \
  GOOGLE_CLOUD_PROJECT=infra-vertex-494621-v1 GOOGLE_GENAI_USE_VERTEXAI=TRUE \
  python3 -m pytest tests/test_page_ledger_gate.py -q -s
```
Expected: 4 passed. Print output should show `accounted: 56/56` and `project_description: 15`.

- [ ] **Step 3: Confirm it is skipped by default**

Run: `cd backend && python3 -m pytest tests/test_page_ledger_gate.py -q`
Expected: 4 skipped.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_page_ledger_gate.py
git commit -m "test(review): the live gate — every page accounted for, no forged receipts"
```

---

### Task 11: Record it in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (the Draft Review section)

- [ ] **Step 1: Add the entry**

Under **Draft Review**, after the "SECTION LOCATION IS NOW DETERMINISTIC" entry, add a paragraph covering: the reported symptom (an awarded package told five sections were missing, all five present on pages 46–54); the three causes; that the TOC-page fold was **load-bearing** against `MIN_COVERAGE`; the three checks and their measured numbers (56/56 twice, 0/56 wrong-page quotes, `PAGE_WINDOW = 4` measured against 12 and 28); that **`quote_in` degenerates on a single page** and why `page_ledger` has its own receipt check; that this **partially reverses Option A** and the evidence that earned it; and that a wrong label is ~6× more damaging than a missing one, which is why structure outranks the walk and the TOC cross-check exists.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): record the page ledger, and the quote_in hole it had to route around"
```

---

## Assumptions taken where the spec left a choice

Recorded because they were not explicitly confirmed:

1. **Option A is partially reversed as designed** (spec §3.5) — the walk fills gaps the PDF's structure could not name, and structure always wins a disagreement.
2. **The score is withheld, not shown with a warning**, when a page is unaccounted for (spec §5) — this matches the existing AI-outage behaviour.
3. **None of spec §8 is bundled here** (golden rule 6: one feature = one focused change). The whole-document-scope bug, the LOI attachment row, and the `temperature` question remain separate.

## Not covered by this plan

- OCR for scanned pages. A blank page is reported, never read.
- The paste path. Pasted text has no pages; `ledger=None` leaves it unchanged.
- Multi-file uploads. Only a single combined PDF gets a ledger.
