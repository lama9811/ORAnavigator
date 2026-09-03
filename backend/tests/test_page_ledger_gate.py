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
from services import draft_review as dr
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


@pytest.fixture(autouse=True)
def _live_gemini(monkeypatch):
    """conftest pins the AI layer OFF for every test (`_no_live_gemini`). This one
    needs it live. Without this, the walk returns {} and the gate passes
    vacuously — the exact failure it exists to prevent."""
    monkeypatch.undo()


@pytest.fixture(scope="module")
def walked():
    path = os.getenv("PAGE_LEDGER_PDF")
    if not path or not os.path.exists(path):
        pytest.skip("set PAGE_LEDGER_PDF to a combined Research.gov package")
    _t, _p, _tr, page_texts = dt._extract_pdf(open(path, "rb").read())
    rows = pl.build_ledger(page_texts, SECTIONS)
    return page_texts, rows


def test_every_page_is_accounted_for(walked):
    """Aligned with PRODUCTION, not a second, looser number of its own
    (fixed 2026-09-03). `<= 2` used to be hardcoded here while
    `draft_review._too_many_unaccounted` -- the function that actually
    decides whether a real review withholds its score -- demanded ZERO. That
    mismatch is why seven consecutive green gate runs hid a review that
    would have withheld the score every single time: the gate was measuring
    a bar the code did not use. Deriving the threshold from the same
    function the code calls means this can never drift from production
    again -- change `_too_many_unaccounted` and this gate re-measures
    against whatever it now means, rather than a number someone has to
    remember to update by hand."""
    page_texts, rows = walked
    assert len(rows) == len(page_texts)
    ok, unaccounted = pl.completeness(rows)
    print(f"\naccounted: {len(rows) - len(unaccounted)}/{len(rows)}  unassigned={unaccounted}")
    assert not dr._too_many_unaccounted(unaccounted, len(rows)), (
        f"pages unaccounted for: {unaccounted} -- this would withhold "
        f"the real review's score")


def test_no_page_can_pass_another_pages_receipt(walked):
    """THE SECURITY GATE. Must be exactly zero.

    ALL-PAIRS, not one neighbour (fixed 2026-09-03). Shifting by one page
    (`page_texts[row["page"] % len(page_texts)]`) is the exact probe that
    MISSED the original hole -- NSF's own budget-form header recurs on
    several NON-adjacent budget-year pages, and a near-identical letter
    opener crossed three pages, none of them the row's neighbour. Worse,
    since the `_receipt_is_solid` fix (d61a083) a row only ever carries a
    quote once that quote has already been checked unique against EVERY
    other page -- so a single-neighbour check here was entailed by the code
    under test and could never fail regardless of what the code did. This
    re-derives what `_receipt_is_solid` itself checks, independently, rather
    than trusting the production code to have checked itself correctly."""
    page_texts, rows = walked
    furniture = pl.document_furniture(page_texts)
    bodies = [pl.body_text(t, furniture) for t in page_texts]
    accepted = []
    for row in rows:
        if not row.get("quote"):
            continue
        for i, other in enumerate(bodies):
            if i == row["page"] - 1:
                continue
            if pl.receipt_ok(other, row["quote"]):
                accepted.append((row["page"], i + 1))
    assert accepted == [], f"wrong-page quotes accepted (page, also-matches): {accepted}"


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


# ── A REALISTIC PROFILE, not the hand-curated superset above ───────────────
#
# `SECTIONS` above is a Research.gov section list someone wrote by hand, and
# it includes `table_of_contents` -- a key NO real solicitation profile ever
# has (`rulebook_baseline` files no rule under it, so `sections_from` never
# creates it). That is exactly why this gate never caught C-1: the model
# walk COULD answer `table_of_contents` here, because the fixture handed it
# the option a real review never offers. `REAL_SECTIONS` below is the actual
# `sections` dict a live NSF 23-598 attach produced (read from a real
# submission's loaded profile, 2026-09-03) -- eleven keys, no
# `table_of_contents`, and it drives the REAL ingestion path
# (`document_text.extract_upload` -> `pdf_sections.split()`), not
# `build_ledger` called directly. That is the path C-1 was actually in.
REAL_SECTIONS = {k: {"label": k.replace("_", " ").title(), "aliases": [k.replace("_", " ")]}
                 for k in ("budget_justification", "cover_sheet",
                           "facilities_equipment_and_other_resources",
                           "format_of_the_proposal", "letter_collaboration",
                           "letter_intent", "letter_of_institutional_support",
                           "project_description", "project_summary",
                           "references_cited", "senior_key_personnel_documents",
                           "supplementary_document")}


@pytest.fixture(scope="module")
def extracted():
    path = os.getenv("PAGE_LEDGER_PDF")
    if not path or not os.path.exists(path):
        pytest.skip("set PAGE_LEDGER_PDF to a combined Research.gov package")
    return dt.extract_upload("package.pdf", open(path, "rb").read(),
                             sections=REAL_SECTIONS)


def test_the_toc_page_is_accounted_for_on_a_real_profile(extracted):
    """C-1. `table_of_contents` is not a key `REAL_SECTIONS` offers, so the
    model walk can never label the TOC page correctly -- it fell straight to
    `unassigned` and withheld the score on EVERY combined Research.gov
    upload, deterministically, until `pdf_sections.split()` started
    reporting the page it excluded and `document_text` pinned it to
    `page_ledger.NOT_A_SECTION`. Driving the real `extract_upload` path
    (unlike the rest of this file) is the point: that pin lives there, not
    in `build_ledger` called directly."""
    ledger = extracted.get("page_ledger")
    assert ledger, "expected a ledger from a real structural split"
    ok, unaccounted = pl.completeness(ledger)
    print(f"\naccounted: {len(ledger) - len(unaccounted)}/{len(ledger)}  "
         f"unassigned={unaccounted}")
    assert ok, f"a real profile's ledger left pages unaccounted: {unaccounted}"
    excluded = [r["page"] for r in ledger if r["source"] == "excluded"]
    assert excluded, "expected at least one page pinned NOT_A_SECTION (the TOC page)"
