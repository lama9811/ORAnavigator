"""Splitting an assembled proposal PDF by its object graph, with no model.

WHY THIS EXISTS. A PI who uploads their proposal as the ONE combined PDF
Research.gov hands them scored a steady 48% -- 2 of 9 sections located, 21 of 70
rules assessable -- against 76-79% for the same proposal as 11 separate section
files. CLAUDE.md recorded that such a PDF "CANNOT be split". That is true of the
TEXT and false of the OBJECT GRAPH: Research.gov concatenates independently
produced attachments, and each source document's font dictionaries land as a
contiguous run of indirect object ids.

Measured on the awarded NSF EiR package: 28 boundaries covering ALL 11 true
section starts, 29 atomic blocks, none straddling a section. End to end the
combined PDF went 48% -> 73-79% with 6 sections split deterministically.

THE FAIL-SAFES ARE THE POINT, not the signal. Evidence is n=1 -- one proposal,
one merger -- so every bail below must fall back to today's behaviour rather
than trust a bad split. Verified against every other PDF to hand (two
solicitations, an NSF budget form, a single section file, a 94-page guide): all
five return zero spans with a stated reason. A fail-safe that never fires has
not been tested.

reportlab builds the synthetic packages and is NOT in requirements.txt, so those
tests skip rather than fail where it is absent. Everything that does not need a
PDF is tested unconditionally.
"""
import io

import pytest

from services import pdf_sections as ps
from services import solicitation_profile as sp


def _sections(*names):
    return {sp.section_key(n): {"label": n, "aliases": sp.aliases_for(n)}
            for n in names}


def _merged_pdf(specs):
    """One PDF per (pages, font), concatenated — a synthetic Research.gov package.

    Each source gets a DIFFERENT base font so it writes its own font objects,
    which is the whole mechanism being tested."""
    reportlab = pytest.importorskip("reportlab")           # not in requirements.txt
    from reportlab.pdfgen import canvas
    from pypdf import PdfWriter, PdfReader

    writer = PdfWriter()
    for pages, font, first_line in specs:
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        for i in range(pages):
            c.setFont(font, 12)
            c.drawString(72, 760, first_line if i == 0 else "continuation body text")
            c.drawString(72, 700, "Body prose for this attachment, page %d." % (i + 1))
            c.showPage()
        c.save()
        writer.append(PdfReader(io.BytesIO(buf.getvalue())))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ── the mechanism ───────────────────────────────────────────────────────────

def test_a_merged_package_splits_at_its_source_documents():
    data = _merged_pdf([(4, "Helvetica", "Project Summary"),
                        (3, "Times-Roman", "References Cited"),
                        (3, "Courier", "Facilities")])
    blocks = ps.page_blocks(data)
    assert blocks is not None
    assert [b[0] for b in blocks] == [0, 4, 7], blocks


def test_a_single_source_pdf_has_no_sub_document_structure():
    """The bail that matters most: a normal one-piece PDF must not be split."""
    data = _merged_pdf([(12, "Helvetica", "Project Description")])
    assert ps.page_blocks(data) is None


def test_a_short_pdf_is_left_alone():
    data = _merged_pdf([(2, "Helvetica", "A"), (2, "Times-Roman", "B")])
    assert ps.page_blocks(data) is None


def test_unreadable_bytes_never_raise():
    """Golden rule 3: a bad file falls back, it does not take the upload down."""
    assert ps.page_blocks(b"not a pdf at all") is None
    spans, report = ps.split(b"not a pdf at all", ["x"] * 10, _sections("Project Summary"))
    assert spans == {}
    assert report["reason"]


# ── NSF's table of contents, which uses TWO row shapes on one page ──────────

TOC_PAGE = """TABLE OF CONTENTS
Total No. of Page No.*
Cover Sheet for Proposal to the National Science Foundation
Project Summary (not to exceed 1 page) 1
Table of Contents 1
References Cited 6
Budget 10
2
Current and Pending (Other) Support
1
Facilities, Equipment and Other Resources
"""


def test_the_roster_reads_both_row_shapes():
    """NSF puts the count BESIDE the label for some rows and on the line ABOVE
    it for others, on the same page. Parsing one shape read 5 rows of 11 and
    resolved one of them, which throttled the whole split."""
    sections = _sections("Project Summary", "References Cited",
                         "Facilities, Equipment and Other Resources")
    rows = ps.toc_roster(TOC_PAGE, sections)
    got = {k: n for k, n, _ in rows if k}

    assert got.get("project_summary") == 1, rows          # same-line, parenthetical
    assert got.get("references_cited") == 6, rows         # same-line, plain
    assert got.get("facilities_equipment_and_other_resources") == 1, rows  # count above


def test_a_parenthetical_never_swallows_the_count():
    """The CLOSED-parenthetical case -- regression guard, must stay green."""
    sections = _sections("Project Summary")
    rows = ps.toc_roster("Project Summary (not to exceed 1 page) 1", sections)
    assert ("project_summary", 1) in [(k, n) for k, n, _ in rows]


def test_a_wrapped_parenthetical_does_not_swallow_the_count():
    """The measured real case, page 5 of the awarded NSF EiR package: NSF's
    descriptive parenthetical for Project Description is long enough to wrap
    onto the PDF's next physical line, so the aside never closes on the line
    that carries the page count. A bare strip-to-end-of-line used to throw
    the "15" away with the rest of the aside and the row vanished from the
    roster entirely -- the section whose count matters most (it is the one
    the false "16 pages, over the 15-page limit" finding came from) was
    invisible to the one check meant to catch a wrong label there."""
    sections = _sections("Project Description")
    page = "\n".join([
        "Project Description (Including Results from Prior 15",
        "NSF Support) (not to exceed 15 pages) (Exceed only if allowed by a",
    ])
    rows = ps.toc_roster(page, sections)
    assert ("project_description", 15) in [(k, n) for k, n, _ in rows]


def test_an_unclosed_parenthetical_with_no_trailing_digit_still_strips_cleanly():
    """The continuation half of the same wrapped aside carries no count of its
    own and must not fabricate one -- it should simply disappear rather than
    surface as a spurious roster row."""
    sections = _sections("Project Description")
    rows = ps.toc_roster(
        "NSF Support) (not to exceed 15 pages) (Exceed only if allowed by a",
        sections)
    assert rows == []


# ── labelling ───────────────────────────────────────────────────────────────

def test_a_block_is_labelled_by_the_name_it_states():
    sections = _sections("References Cited", "Project Description")
    assert ps._label_block("References Cited\n[1] Alvarez, 2019.", sections) \
        == "references_cited"


def test_page_furniture_does_not_hide_the_name():
    """The Research.gov stamp is the FIRST line of every page, so without
    dropping it a three-line probe never reaches the line that names the
    section."""
    sections = _sections("Synergistic Activities")
    stamp = "Submitted/PI: A Person /Proposal No: 2503008"
    # Three furniture lines, which is exactly what the real document stamps on
    # every page: the name is pushed to line 4, past the probe window.
    probe = (f"{stamp}\nPage 45 of 56\nRevised Proposal Budget Revision #1\n"
             "Synergistic Activities\nBody text.")
    assert ps._label_block(probe, sections) is None, \
        "the probe reached the name without the furniture being dropped"
    furniture = {stamp, "Page 45 of 56", "Revised Proposal Budget Revision #1"}
    assert ps._label_block(probe, sections, furniture=furniture) \
        == "synergistic_activities"


def test_furniture_is_recognised_by_repetition_not_by_wording():
    """No funder's wording is hardcoded — a line stamped on most pages is
    furniture whatever it says."""
    stamp = "Submitted/PI: A Person /Proposal No: 2503008"
    pages = [f"{stamp}\nreal content {i}" for i in range(10)]
    assert stamp in ps._furniture(pages)
    assert "real content 3" not in ps._furniture(pages)


def test_a_block_naming_two_sections_is_left_unlabelled():
    """Ambiguity folds into the preceding section rather than picking one."""
    sections = _sections("Project Description", "References Cited")
    probe = "Project Description\nReferences Cited\n"
    assert ps._label_block(probe, sections) is None


# ── the fail-safes, exercised through `split` ───────────────────────────────

def test_a_page_count_disagreement_bails():
    """MAX_CHARS truncation silently invalidates every offset past the cut, so
    the two reads disagreeing is a refusal, not a warning."""
    data = _merged_pdf([(4, "Helvetica", "Project Summary"),
                        (4, "Times-Roman", "References Cited"),
                        (4, "Courier", "Facilities")])
    spans, report = ps.split(data, ["short"] * 3, _sections("Project Summary"))
    assert spans == {}
    assert "page count" in (report["reason"] or "")


def test_no_table_of_contents_means_no_split():
    """Deliberate: n=1 evidence, so require the artefact that produced it. A
    combined PDF exported from Word carries no NSF TOC and is left to the
    existing path rather than split on an untested signal."""
    data = _merged_pdf([(4, "Helvetica", "Project Summary"),
                        (4, "Times-Roman", "References Cited"),
                        (4, "Courier", "Facilities")])
    spans, report = ps.split(data, ["body text"] * 12,
                             _sections("Project Summary", "References Cited"))
    assert spans == {}
    assert "table of contents" in (report["reason"] or "")


def test_every_reported_bail_names_a_reason():
    """A degenerate split must be loud. A bail with no reason reads as a bug."""
    for pages in (["x"] * 3, ["y"] * 40):
        _spans, report = ps.split(b"%PDF-1.4 broken", pages, _sections("Project Summary"))
        assert report["reason"], report


# ── NSF's own table of contents spells the slot a THIRD way ─────────────────

def test_the_table_of_contents_spelling_of_the_supplementary_slot_resolves():
    """Found on a real awarded proposal, by a PI reading the section map.

    Pages 46-54 of the package -- the Data Management Plan, the Mentoring Plan,
    the institutional support letter -- were never checked, and the reason was
    one word. NO PAGE OF THAT BLOCK NAMES THE SLOT: every attachment names
    ITSELF ("Mentoring Plan", "Data Management and Sharing Plan", a letter's
    date), so the table of contents is the only place the slot is written down
    at all. That row read:

        "Special Information/Supplementary Documents"
            -> {special, information, supplementary, document}

    while the equivalence table carried

        {special, information, supplementary, documentation}

    NSF's rulebook says "Documentation", NSF's generated table of contents says
    "Documents". Different word, no match, so the row resolved to nothing, never
    entered the roster, and the elimination rule written specifically to recover
    an unlabelled trailing block never fired.
    """
    sections = _sections("Supplementary Documents")
    assert sp.resolve_section_key(
        sections, "Special Information/Supplementary Documents") == "supplementary_documents"


def test_the_rulebook_spelling_still_resolves():
    """The entry this one sits beside must keep working."""
    sections = _sections("Supplementary Documents")
    assert sp.resolve_section_key(
        sections, "Special Information and Supplementary Documentation") == "supplementary_documents"


def test_the_containment_hole_stays_closed():
    """Named equivalences only. A longer phrase that merely CONTAINS a section
    name must still refuse, or "Project Description Supplementary Documents"
    swallows a real section."""
    sections = _sections("Project Description")
    assert sp.resolve_section_key(
        sections, "Project Description Supplementary Documents") is None


# ── a fixed denominator: the model is not asked to fill structural gaps ─────

def test_a_structural_split_does_not_ask_the_model_to_name_the_rest():
    """Product decision: when the PDF's own structure named the sections, a
    section it could NOT name is reported unlocated rather than guessed at.

    The score is a fraction, so a section the model finds on one run and misses
    on the next moves the BOTTOM of it: measured on one unchanged 56-page
    package, 6 sections located on some runs and 9 on others, `assessed`
    swinging 45 <-> 49 on identical input."""
    from unittest import mock
    from services import draft_review

    sections = _sections("Project Summary", "References Cited")
    spans = {"project_summary": {"text": "Project Summary\nWork.", "start": 0,
                                 "end": 21, "marker": "Project Summary"}}
    with mock.patch.object(draft_review.gemini_client, "generate_json") as gj:
        draft_review.review_draft("Project Summary\nWork.",
                                  profile={"sections": sections, "requirements": [],
                                           "checks": {}},
                                  file_spans=spans, structural=True)
        locate_calls = [c for c in gj.call_args_list
                        if "Segment this grant proposal" in str(c)]
    assert locate_calls == [], "the model was asked to name sections anyway"


def test_without_a_structural_split_the_model_is_still_asked():
    """The mirror. Pasted text and single section files have no PDF structure
    to read, so the locate call must stay for them."""
    from unittest import mock
    from services import draft_review

    sections = _sections("Project Summary", "References Cited")
    with mock.patch.object(draft_review.gemini_client, "generate_json",
                           return_value=None) as gj:
        draft_review.review_draft("Project Summary\nWork.",
                                  profile={"sections": sections, "requirements": [],
                                           "checks": {}},
                                  structural=False)
        locate_calls = [c for c in gj.call_args_list
                        if "Segment this grant proposal" in str(c)]
    assert locate_calls, "the locate call was lost for the paste path"
