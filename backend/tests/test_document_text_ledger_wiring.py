"""Integration coverage for the page-ledger wiring inside `extract_upload`
(`services/document_text.py`, the `if sections and page_texts and not
truncated:` block).

WHY THIS FILE EXISTS. Before this, the ONLY test in the repo calling
`extract_upload` passed no `sections`, so the wiring block never ran anywhere
in the suite -- `test_pdf_sections.py` drives `pdf_sections.split()` directly
and `test_files_are_sections.py` drives `map_files_to_sections()` directly,
neither of which reaches this block. A report claiming those suites as
evidence for the wiring's correctness was wrong: they never exercised it.

reportlab builds the synthetic packages and is NOT in requirements.txt, so
these skip rather than fail where it is absent (same convention as
test_pdf_sections.py).
"""
import io

import pytest

from services import document_text as dt
from services import solicitation_profile as sp


def _sections(*names):
    return {sp.section_key(n): {"label": n, "aliases": sp.aliases_for(n)}
            for n in names}


def _merged_pdf(blocks):
    """blocks: [(font, [page_text, page_text, ...]), ...] -- one sub-document
    per entry, each drawing its OWN full page text line by line (not just a
    first line + generic filler), so a multi-line table of contents page is
    legible the same way pdfplumber would read a real one. Each sub-document
    gets a DIFFERENT base font so it writes its own font objects -- the
    mechanism `pdf_sections.page_blocks` reads."""
    reportlab = pytest.importorskip("reportlab")          # not in requirements.txt
    from reportlab.pdfgen import canvas
    from pypdf import PdfWriter, PdfReader

    writer = PdfWriter()
    for font, pages in blocks:
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        for page_text in pages:
            c.setFont(font, 12)
            y = 760
            for line in page_text.splitlines():
                c.drawString(72, y, line)
                y -= 20
            c.showPage()
        c.save()
        writer.append(PdfReader(io.BytesIO(buf.getvalue())))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


SECTIONS = _sections("Project Summary", "References Cited", "Facilities")

# Pushed past `_PROBE_LINES` (3) with two filler lines so `_label_block`'s
# substring tier never sees "Project Summary" / "References Cited" in this
# page's own first three lines and mislabels the TOC block itself. The page
# counts here MUST match each block's real length (within `_PAGE_SLOP`) --
# `split()` bails outright on a labelled section whose actual span disagrees
# with what its own table of contents states.
_TOC_TEXT = ("TABLE OF CONTENTS\n"
            "Total No. of Pages\n"
            "Type a Description Below\n"
            "Project Summary 2\n"
            "References Cited 2\n"
            "Facilities 3")


def _successful_split_pdf():
    # 8 pages total: `pdf_sections.page_blocks` refuses anything under its own
    # MIN_PAGES (8) outright, before it even looks at font ids.
    return _merged_pdf([
        ("Helvetica-Bold", [_TOC_TEXT]),
        ("Helvetica", ["Project Summary\nOverview of the proposed research effort.",
                       "continuation body text for project summary."]),
        ("Times-Roman", ["References Cited\n[1] Smith, J. (2020). A paper."]
                        + ["continuation of the reference list."]),
        ("Courier", ["Facilities\nDescription of lab space and equipment.",
                     "continuation of the facilities description.",
                     "further continuation of the facilities description."]),
    ])


def _single_source_pdf():
    """One font throughout -- no font-object discontinuity, so
    `pdf_sections.page_blocks` returns None and `split()` bails. The common
    case for anything that isn't a Research.gov-assembled combined PDF."""
    return _merged_pdf([
        ("Helvetica", [f"Project Description\nPage {i + 1} of a single document."
                       for i in range(9)]),
    ])


def test_a_successful_structural_split_produces_a_full_ledger():
    data = _successful_split_pdf()
    out = dt.extract_upload("package.pdf", data, sections=SECTIONS)

    assert not out["error"], out["error"]
    n_pages = out["pages"]
    assert n_pages == 8, out["pages"]

    # One row per page, no page skipped -- the whole point of the module.
    ledger = out.get("page_ledger")
    assert ledger is not None
    assert sorted(r["page"] for r in ledger) == list(range(1, n_pages + 1))

    # A real object-graph split happened -- the honest flag, not the mere
    # presence of `section_spans`.
    assert out.get("spans_are_structural") is True

    # The TOC page (page 1) named nothing itself and belongs to no section;
    # the three self-naming attachments are pinned to their OWN pages, not
    # smeared across the document by the structural split.
    by_page = {r["page"]: r["section"] for r in ledger}
    assert by_page[1] is None
    assert by_page[2] == by_page[3] == "project_summary"
    assert by_page[4] == by_page[5] == "references_cited"
    assert by_page[6] == by_page[7] == by_page[8] == "facilities"
    assert all(r["source"] == "structure" for r in ledger if r["page"] != 1)

    # THE TOC PAGE MUST BE ACCOUNTED FOR, NOT `unassigned` (fixed 2026-09-03).
    # `table_of_contents` is never a real key in a real solicitation's section
    # universe -- no rulebook rule is ever filed under it -- so the model
    # walk could NEVER answer this page correctly, and it fell to
    # `unassigned` every time. That withheld the completeness score for the
    # WHOLE review on every combined Research.gov package, deterministically.
    # `pdf_sections` now reports the page it deliberately excluded, and
    # `document_text` pins it so `build_ledger` marks it `excluded` instead --
    # accounted for, same as `blank`, without pretending it is a section.
    by_page_row = {r["page"]: r for r in ledger}
    assert by_page_row[1]["source"] == "excluded"
    from services import page_ledger as pl
    ok, unaccounted = pl.completeness(ledger)
    assert ok is True
    assert unaccounted == []

    spans = out.get("section_spans") or {}
    assert set(spans) == {"project_summary", "references_cited", "facilities"}
    for key, span in spans.items():
        assert out["text"][span["start"]:span["end"]] == span["text"], key


def test_a_bailed_split_still_produces_a_ledger_with_structural_false():
    data = _single_source_pdf()
    out = dt.extract_upload("single.pdf", data, sections=SECTIONS)

    assert not out["error"], out["error"]
    n_pages = out["pages"]
    assert n_pages == 9, out["pages"]

    # `pdf_sections.split()` bails on a single-font document (below
    # MIN_BLOCKS) -- the honest flag must say so.
    assert out.get("spans_are_structural") is False

    # A bail is not a reason to skip the ledger: it still gets built, from
    # the model walk alone (which returns nothing here -- conftest.py pins
    # the client to None -- so every page comes back unassigned rather than
    # silently absent).
    ledger = out.get("page_ledger")
    assert ledger is not None
    assert sorted(r["page"] for r in ledger) == list(range(1, n_pages + 1))
    assert all(r["source"] in ("unassigned", "blank") for r in ledger)


def test_ledger_rows_never_carry_the_pi_s_manuscript_quote():
    """I-4: `quote` is the PI's own draft text, copied verbatim as a per-page
    receipt. It has no renderer and no reason to leave the backend."""
    data = _successful_split_pdf()
    out = dt.extract_upload("package.pdf", data, sections=SECTIONS)
    ledger = out.get("page_ledger") or []
    assert ledger, "expected a ledger to test against"
    assert all("quote" not in row for row in ledger)
    assert all("verified" in row for row in ledger)


def test_a_late_exception_does_not_leave_structural_true_with_no_spans(monkeypatch):
    """FINDING 3 (fix round 3): `spans_are_structural` used to be assigned
    immediately after `pdf_sections.split()` returned -- BEFORE `build_ledger`,
    `reconcile_toc`, `spans_from_ledger` and the rebase all ran inside the same
    `try`. If any of those raised, the broad `except Exception` swallowed it,
    `section_spans` was never set for this file, and `spans_are_structural`
    stayed stuck at whatever `bool(spans)` was the instant `split()` returned --
    True on a real split. `review_draft`'s `structural` flag is WHOLE-DOCUMENT
    (`any(f.get("spans_are_structural") for f in extracted)`), so one file
    failing here would silently disable the AI locate stage for an entire
    multi-file review, including for this very file, which ended up with no
    spans at all.

    Forces `reconcile_toc` to raise after a real, successful split (the same
    fixture `test_a_successful_structural_split_produces_a_full_ledger` uses)
    and asserts the flag is not left True."""
    from services import page_ledger as pl

    def _boom(*a, **k):
        raise RuntimeError("simulated failure after a successful split")

    monkeypatch.setattr(pl, "reconcile_toc", _boom)

    data = _successful_split_pdf()
    out = dt.extract_upload("package.pdf", data, sections=SECTIONS)

    assert not out.get("spans_are_structural")
    assert "section_spans" not in out
    # A raise AFTER the ledger is already in `out` is the well-handled
    # partial case (I-3) -- only the mismatch report is lost, and the
    # ledger itself must not be flagged as if it had never been built.
    assert "page_ledger_error" not in out


# ── I-1 / I-2: a MULTI-file upload must not pay for, or trust, a walk on a
# file that main.py will never select -- see extract_upload's own docstring.

def test_a_multi_file_bailed_split_skips_the_walk_entirely(monkeypatch):
    """I-2: in a multi-file upload, `single_file=False` and a bailed split
    (no real structure) must not even CALL `build_ledger` -- that is the
    expensive, model-calling part this task exists to stop paying for on a
    file that can never be selected. I-1 falls out of the same fix: with no
    call, there is no `section_spans` for `map_files_to_sections` to trust
    ahead of the locate stage either."""
    from services import page_ledger as pl

    calls = []
    real_build_ledger = pl.build_ledger

    def _spy(*a, **k):
        calls.append(1)
        return real_build_ledger(*a, **k)

    monkeypatch.setattr(pl, "build_ledger", _spy)

    data = _single_source_pdf()
    out = dt.extract_upload("data-management-plan.pdf", data,
                            sections=SECTIONS, single_file=False)

    assert not out["error"], out["error"]
    assert calls == [], "build_ledger must not run for a non-structural file in a multi-file upload"
    assert out.get("spans_are_structural") is False
    assert "page_ledger" not in out
    assert "section_spans" not in out
    assert "page_ledger_error" not in out       # a deliberate skip, not a failure


def test_a_multi_file_successful_split_still_gets_its_full_ledger(monkeypatch):
    """The other half of the same gate: a file whose OWN split() succeeds
    must be treated exactly as it would in a single-file upload -- main.py
    WILL select this ledger (`spans_are_structural` True), so building it is
    not wasted, and the TOC-page pin and every other guarantee must survive
    `single_file=False`."""
    from services import page_ledger as pl

    calls = []
    real_build_ledger = pl.build_ledger

    def _spy(*a, **k):
        calls.append(1)
        return real_build_ledger(*a, **k)

    monkeypatch.setattr(pl, "build_ledger", _spy)

    data = _successful_split_pdf()
    out = dt.extract_upload("package.pdf", data, sections=SECTIONS, single_file=False)

    assert not out["error"], out["error"]
    assert calls == [1], "the structural file's ledger must still be built"
    assert out.get("spans_are_structural") is True
    ledger = out.get("page_ledger")
    assert ledger is not None
    assert sorted(r["page"] for r in ledger) == list(range(1, out["pages"] + 1))
    spans = out.get("section_spans") or {}
    assert set(spans) == {"project_summary", "references_cited", "facilities"}


def test_a_walk_derived_label_never_reaches_map_files_to_sections_when_skipped():
    """I-1, exercised through the actual downstream consumer. Before the fix,
    a non-structural file's WALK-derived `section_spans` filed a section via
    `map_files_to_sections`'s `pdf_structure` path even though main.py would
    never select that file's ledger -- a confident, unverified label beating
    the locate stage with none of the ledger's guarantees behind it. With the
    walk skipped for this file (I-2), there is nothing for `map_files_to_sections`
    to file: the file's text stays available to the ordinary locate stage
    instead (via `leftover`), which is the honest outcome."""
    data = _single_source_pdf()
    out = dt.extract_upload("data-management-plan.pdf", data,
                            sections=SECTIONS, single_file=False)
    assert "section_spans" not in out

    text, spans, leftover, mapping = dt.map_files_to_sections([out], SECTIONS)
    assert spans == {}
    assert leftover, "the file's text must still reach the locate stage"
    row = mapping[0]
    assert row["source"] is None, row["source"]     # never filed by a walk it never ran


def test_a_ledger_that_never_gets_built_is_flagged_not_silent(monkeypatch):
    """I-3: a raise INSIDE (or before) `build_ledger` must not vanish
    silently. `main.py` selects no ledger, the score is computed exactly as
    if the feature did not exist, and the modal renders no panel -- so the
    only way to tell "we accounted for every page" from "we never tried"
    apart is this flag."""
    from services import page_ledger as pl

    def _boom(*a, **k):
        raise RuntimeError("simulated build_ledger failure")

    monkeypatch.setattr(pl, "build_ledger", _boom)

    data = _successful_split_pdf()          # a real structural split, so the
    out = dt.extract_upload("package.pdf", data, sections=SECTIONS)  # walk is reached

    assert "page_ledger" not in out
    assert "section_spans" not in out
    assert out.get("page_ledger_error") is True
