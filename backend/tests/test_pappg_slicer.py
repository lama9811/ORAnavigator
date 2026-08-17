"""The PAPPG slices — boundaries, keys, and that the content is actually there.

WHY THESE TESTS ARE THE WHOLE POINT OF THE SLICER
--------------------------------------------------
Each section title appears TWICE in Chapter II: once in the table of contents and
once as the body heading. Picking the wrong one yields a ~20-character slice, and
a thin slice reads exactly like a section with few rules — no error, no warning,
just a rulebook that quietly says almost nothing. That is the same failure shape
as the truncated PDF reads CLAUDE.md records, and it is invisible unless asserted.

Two plausible discriminators were tried and BOTH are wrong (see slicer.py's
docstring): "followed by prose" breaks on Project Description and Senior/Key
Personnel, whose body headings are legitimately followed by sub-headings; "not
followed by a lettered heading" breaks on Senior/Key Personnel, whose TOC entry is
followed by "(i) Biographical Sketch(es)". What holds is that the TOC block is
contiguous and comes first, so the body is the LAST full-line match — and
monotonically increasing offsets are what catch that going wrong again.

These run against the COMMITTED slice file, so they need no PDF. The opt-in test at
the bottom re-derives from the PDF when you have it.
"""
import json
import os

import pytest

from services.solicitation_profile import section_key

SLICES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "kb_structured", "_pappg_24_1_sections.json")


@pytest.fixture(scope="module")
def slices():
    with open(SLICES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def by_key(slices):
    return {s["section_key"]: s for s in slices["sections"]}


# ── the slice file itself ───────────────────────────────────────────────────

def test_all_ten_slices_are_present(slices):
    """Nine documents a PI uploads, plus II.C which governs all of them."""
    assert slices["version"] == "NSF 24-1"
    assert len(slices["sections"]) == 10


def test_every_key_is_what_section_key_produces(by_key):
    """THE TRAP THIS GUARDS. The rest of the app files rules under
    solicitation_profile.section_key. The requirement extractor canonicalises with
    solicitation_requirements.canon_section instead, and the two DISAGREE on 6 of
    these 9 titles — most destructively `facilities_equipment_and_other_resources`
    vs `facilitie_equipment_other_resource`. A row filed under the wrong key can
    never be located in a draft, reports "Not located", and drops out of the
    score's denominator unchecked."""
    for key, sl in by_key.items():
        assert key == section_key(sl["nsf_label"]), (
            f"{key!r} != section_key({sl['nsf_label']!r})")


def test_the_four_curated_sections_use_the_keys_the_table_already_uses(by_key):
    from services import rulebook_baseline as rb
    for key in (rb.PROJECT_SUMMARY, rb.PROJECT_DESCRIPTION,
                rb.REFERENCES_CITED, rb.FACILITIES):
        assert key in by_key, f"{key} missing from the slices"


def test_no_slice_is_thin(by_key):
    """A short slice is the failure mode: it looks like a short section."""
    for key, sl in by_key.items():
        assert sl["char_count"] == len(sl["text"])
        assert sl["char_count"] > 100, f"{key} is {sl['char_count']} chars"


def test_every_slice_starts_at_its_own_body_heading(by_key):
    """Not at a table-of-contents line. A TOC entry is immediately followed by the
    next entry; a body heading is followed by the section's own content."""
    for key, sl in by_key.items():
        first = sl["text"].split("\n", 1)[0]
        assert sl["nsf_label"].split(",")[0][:18].lower() in first.lower(), (
            f"{key} does not start with its heading: {first!r}")
        assert len(sl["text"]) > len(first) + 50, (
            f"{key} is a heading with nothing under it — a TOC line was matched")


def test_the_budget_slice_is_the_largest_and_fits_one_model_call(by_key):
    """CHUNK_CHARS is 60,000. Every slice being under it is what makes this one
    read per section instead of a chunked multi-round sweep."""
    from services.solicitation_requirements import CHUNK_CHARS
    biggest = max(by_key.values(), key=lambda s: s["char_count"])
    assert biggest["section_key"] == "budget_and_budget_justification"
    for sl in by_key.values():
        assert sl["char_count"] < CHUNK_CHARS, f"{sl['section_key']} needs chunking"


def test_the_slices_exclude_the_other_proposal_types(slices):
    """§II.F — RAPID, EAGER, Ideas Lab, Conference, Equipment, Travel, Center — is
    81,470 chars that never applies to a standard research proposal, and is the
    bulk of the noise that sank the previous attempts. If the last slice lost its
    end boundary it would swallow all of it."""
    import re
    total = sum(s["char_count"] for s in slices["sections"])
    assert total < 120_000, f"slices total {total:,} — the end boundary slipped"

    # Check for §II.F's HEADINGS, not for any mention of its proposal types.
    # §II.D legitimately cross-references them in prose — "• Rapid Response
    # Research (RAPID) (see Chapter II.F.2)" — and an earlier version of this test
    # failed on exactly that, which is a wrong assertion rather than a leak.
    heading = re.compile(
        r"^[ \t]*\d+\.[ \t]+(?:Planning|Rapid Response Research|EArly-concept|"
        r"Ideas Lab|Conference|Equipment|Travel|Center|Research Infrastructure)\b"
        r"[^\n]*Proposal[ \t]*$", re.M)
    for sl in slices["sections"]:
        hit = heading.search(sl["text"])
        assert hit is None, f"§II.F heading leaked into {sl['section_key']}: {hit.group(0)!r}"


# ── the content is really there ─────────────────────────────────────────────

def test_the_project_summary_slice_states_every_rule_we_curated(by_key):
    """The validation that makes the whole approach checkable: we already hold 5
    hand-verified Project Summary rules taken from Research.gov's distillation of
    this very text. If the slice does not contain them, the boundary is wrong."""
    text = " ".join(by_key["project_summary"]["text"].split()).lower()
    for phrase in (
        "not more than one page in length",          # pappg_ps_one_page
        "an overview, a statement on the intellectual merit",   # pappg_ps_headings
        "a statement on the broader impacts",                   # ditto
        "objectives and methods to be employed",     # pappg_ps_overview
        "advance knowledge",                         # pappg_ps_merit
        "benefit society",                           # pappg_ps_impacts
    ):
        assert phrase in text, f"missing from the Project Summary slice: {phrase!r}"


def test_the_project_summary_slice_holds_rules_we_do_not_yet_have(by_key):
    """Evidence the ingestion is worth doing at all, not just a re-derivation of
    what we already hold."""
    text = " ".join(by_key["project_summary"]["text"].split()).lower()
    assert "should not be an abstract" in text
    assert "informative to other persons working in the same or related fields" in text


def test_the_project_description_slice_states_its_known_rules(by_key):
    text = " ".join(by_key["project_description"]["text"].split()).lower()
    assert "15-page" in text or "15 page" in text
    assert "url" in text                      # "URLs must not be used"
    assert "broader impacts" in text


def test_the_format_slice_carries_the_font_and_margin_rules(by_key):
    """§II.C is why this slice exists separately — it governs every section, and
    nothing in the app checks font, margins or spacing today."""
    text = " ".join(by_key["format_of_the_proposal"]["text"].split()).lower()
    assert "arial" in text and "palatino" in text
    assert "margins, in all directions, must be at least an inch" in text
    assert "no more than six lines of text within a vertical space of one inch" in text


# ── opt-in: re-derive from the PDF ──────────────────────────────────────────

PDF = os.getenv("PAPPG_PDF", os.path.expanduser("~/Desktop/nsf24_1.pdf"))


@pytest.mark.skipif(not os.path.exists(PDF), reason="PAPPG PDF not available")
def test_reslicing_the_pdf_reproduces_the_committed_file(slices):
    """The committed JSON is a build artifact; this proves it is reproducible and
    that the slicer's guards (monotonic offsets, char bands) still pass against
    the real document."""
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    spec = importlib.util.spec_from_file_location(
        "pappg_slicer", os.path.join(root, "kb_pappg", "slicer.py"))
    slicer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(slicer)

    rebuilt = slicer.build(PDF)
    assert [r["section_key"] for r in rebuilt] == \
           [s["section_key"] for s in slices["sections"]]
    for a, b in zip(rebuilt, slices["sections"]):
        assert a["text"] == b["text"], f"{a['section_key']} drifted"
