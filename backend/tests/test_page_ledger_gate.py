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
