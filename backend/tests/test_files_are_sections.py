"""A PI's package is one file per section. Stop guessing boundaries we were given.

MEASURED 2026-09-01, five uploads of the AWARDED 11-file NSF EiR package through
the real endpoint:

    run 1:  29%   4.0 of 14 assessed   1 section located, 8 missing
    run 2:  29%   4.0 of 14            (cached)
    run 3:  29%   4.0 of 14            (cached)
    run 4:  68%  32.5 of 48 assessed   6 sections located, 3 missing

Same bytes. Run 1's single located section was `references_cited` with a
word_count of 9503 -- it read the ENTIRE 45-page package as References Cited, so
every other rule became `could_not_locate`, left the denominator (48 assessable
rules collapsed to 14), and a FUNDED proposal scored 29%.

Run 4 got it right by reading the filename headings `combine()` inserts. That is
the whole mechanism: we glue 11 files into one string, write the filename above
each, and then pay a model call to find the seams we just wrote. The instability
is not the reviewer disagreeing about rules -- it is `locate_sections` guessing.

THIS IS A HYBRID, NEVER A REPLACEMENT. Not every filename maps, and the ones that
do not must still reach the reviewer through the existing glue-and-locate path,
which is also what pasted text and a single combined PDF depend on.
"""
import pytest

from services import document_text as dt


SECTIONS = {
    "project_summary": {"label": "Project Summary"},
    "project_description": {"label": "Project Description"},
    "references_cited": {"label": "References Cited"},
    "budget_justification": {"label": "Budget and Budget Justification"},
    "facilities_equipment_and_other_resources": {
        "label": "Facilities, Equipment and Other Resources"},
}


def _f(name, text="body text here", pages=1, **kw):
    out = {"filename": name, "text": text, "pages": pages, "chars": len(text),
           "truncated": False, "error": None}
    out.update(kw)
    return out


def test_a_numbered_filename_maps_to_its_section():
    """The shape a real package actually has."""
    files = [_f("02-Project-Description.pdf", pages=15)]
    text, spans, leftover, mapping = dt.map_files_to_sections(files, SECTIONS)
    assert list(spans) == ["project_description"]
    assert spans["project_description"]["pages"] == 15
    assert leftover == []
    assert mapping[0]["section"] == "project_description"


def test_the_span_carries_the_files_own_text_and_a_heading():
    files = [_f("01-Project-Summary.pdf", text="Overview\nWe will study things.")]
    text, spans, _, _ = dt.map_files_to_sections(files, SECTIONS)
    span = spans["project_summary"]
    assert "We will study things." in span["text"]
    assert span["marker"]
    # Offsets index the COMBINED text, not the file -- draft_review slices by them.
    assert text[span["start"]:span["end"]] == span["text"]


def test_a_file_that_maps_to_nothing_is_left_for_the_locate_stage():
    """6 of the real 11 filenames resolve to nothing. They must not be dropped --
    they go through the existing combine + locate path unchanged."""
    files = [_f("06-Biographical-Sketch.pdf"), _f("02-Project-Description.pdf")]
    text, spans, leftover, mapping = dt.map_files_to_sections(files, SECTIONS)
    assert list(spans) == ["project_description"]
    assert [f["filename"] for f in leftover] == ["06-Biographical-Sketch.pdf"]
    assert [m["section"] for m in mapping] == [None, "project_description"]


def test_an_unreadable_file_is_skipped_entirely():
    """An errored or empty file has no text to be a section OF."""
    files = [_f("01-Project-Summary.pdf", text="", error="scanned image"),
             _f("03-References-Cited.pdf")]
    text, spans, leftover, _ = dt.map_files_to_sections(files, SECTIONS)
    assert list(spans) == ["references_cited"]
    assert leftover == []


def test_two_files_claiming_one_section_do_not_silently_overwrite():
    """The second becomes leftover rather than replacing the first -- a package
    with two Project Description files is a mistake worth surfacing, and losing
    one silently is the failure mode this repo keeps getting bitten by."""
    files = [_f("02-Project-Description.pdf", text="first"),
             _f("Project Description extra.pdf", text="second")]
    text, spans, leftover, _ = dt.map_files_to_sections(files, SECTIONS)
    assert spans["project_description"]["text"].endswith("first")
    assert [f["filename"] for f in leftover] == ["Project Description extra.pdf"]


def test_a_section_outside_the_universe_is_never_invented():
    """Mapping resolves against the sections this proposal actually has."""
    files = [_f("09-Data-Management-and-Sharing-Plan.pdf")]
    text, spans, leftover, _ = dt.map_files_to_sections(files, SECTIONS)
    assert spans == {}
    assert len(leftover) == 1


def test_an_explicit_override_beats_the_guess():
    """A wrong guess must be one dropdown away from fixed."""
    files = [_f("Attachment 4.pdf", section="references_cited")]
    text, spans, leftover, mapping = dt.map_files_to_sections(files, SECTIONS)
    assert list(spans) == ["references_cited"]
    assert mapping[0]["source"] == "chosen"


def test_the_mapping_reports_how_each_file_was_decided():
    """Returned to the UI so a mis-map is visible rather than silent."""
    files = [_f("02-Project-Description.pdf"), _f("06-Biographical-Sketch.pdf")]
    _, _, _, mapping = dt.map_files_to_sections(files, SECTIONS)
    assert mapping[0]["source"] == "filename"
    assert mapping[1]["source"] is None


def test_offsets_are_correct_for_every_file_in_a_multi_file_package():
    """The risk in owning the combined text. `draft_review` slices by these
    offsets to carve Broader Impacts out of the Project Description and to order
    the section map, so an off-by-anything corrupts both -- and silently."""
    files = [_f("01-Project-Summary.pdf", text="Summary body."),
             _f("06-Biographical-Sketch.pdf", text="Unmapped body."),
             _f("02-Project-Description.pdf", text="Description body."),
             _f("03-References-Cited.pdf", text="[1] A paper.")]
    text, spans, leftover, _ = dt.map_files_to_sections(files, SECTIONS)
    assert set(spans) == {"project_summary", "project_description", "references_cited"}
    for key, span in spans.items():
        assert text[span["start"]:span["end"]] == span["text"], key
    # the unmapped file is still in the text, for locate_sections to find
    assert "Unmapped body." in text


def test_the_unmapped_files_text_is_still_reviewed():
    """Leftovers are not dropped -- they stay in the document so the existing
    locate stage can place them and their rules still get assessed."""
    files = [_f("10-Mentoring-Plan.pdf", text="Mentoring content here.")]
    text, spans, leftover, _ = dt.map_files_to_sections(files, SECTIONS)
    assert spans == {}
    assert "Mentoring content here." in text
    assert len(leftover) == 1


# ── the engine side ─────────────────────────────────────────────────────────

def test_a_file_derived_span_is_never_overwritten_by_the_locate_guess(monkeypatch):
    """The whole point. On the measured bad run, locate claimed the entire
    45-page package was one section; a file we identified must win over that."""
    from services import draft_review as dr

    def fake_locate(text, sections, use_ai=True):
        return ({"references_cited": {"text": text, "start": 0, "end": len(text),
                                      "marker": "References Cited"}}, True)
    monkeypatch.setattr(dr, "locate_sections", fake_locate)

    profile = {"id": "SOL", "sections": SECTIONS, "requirements": [], "contract": {}}
    file_spans = {"project_summary": {"text": "Overview.", "start": 0, "end": 9,
                                      "marker": "Project Summary", "pages": 1}}
    out = dr.review_draft("Overview.", profile=profile, file_spans=file_spans)
    located = {s["key"] for s in out["sections_located"]}
    assert "project_summary" in located, out["sections_located"]


def test_locate_still_places_the_sections_no_file_claimed(monkeypatch):
    """Hybrid, not replacement: the locate stage keeps doing its job for the
    ~6 of 11 filenames that resolve to nothing."""
    from services import draft_review as dr

    def fake_locate(text, sections, use_ai=True):
        return ({"references_cited": {"text": "[1] A paper.", "start": 0, "end": 12,
                                      "marker": "References Cited"}}, True)
    monkeypatch.setattr(dr, "locate_sections", fake_locate)

    profile = {"id": "SOL", "sections": SECTIONS, "requirements": [], "contract": {}}
    file_spans = {"project_summary": {"text": "Overview.", "start": 0, "end": 9,
                                      "marker": "Project Summary", "pages": 1}}
    out = dr.review_draft("Overview. [1] A paper.", profile=profile, file_spans=file_spans)
    located = {s["key"] for s in out["sections_located"]}
    assert located == {"project_summary", "references_cited"}


# ── when the document cannot be split, SAY SO ───────────────────────────────
#
# Measured 2026-09-01 on the awarded proposal as ONE combined Research.gov PDF
# (56 pages, 152,702 chars): 2 of 9 sections located on all five runs, 21 of 70
# rules assessable, and a FUNDED proposal scored 48% -- consistently.
#
# It is not a locate failure. That PDF has no section boundaries in its text:
# "Project Summary" appears ONCE in 56 pages and never on its own line, and the
# page furniture is identical throughout ("Revised Proposal Budget Revision #1
# for 2503008...") and never names the section. There is nothing to find.
#
# The same package uploaded as its 11 real section files scores 76% with 50 rules
# assessed. So the remedy is the input, and the review must say that rather than
# return a confident number built on a fifth of its rules. Same principle as the
# scraper: a silent stop reads as "we found everything" when it means "we stopped
# looking".

def _profile_with(n_sections):
    secs = {f"s{i}": {"label": f"Section {i}"} for i in range(n_sections)}
    return {"id": "SOL", "sections": secs, "requirements": [], "contract": {}}


def test_a_document_we_could_barely_split_is_flagged(monkeypatch):
    from services import draft_review as dr
    monkeypatch.setattr(dr, "locate_sections",
                        lambda text, sections, use_ai=True: (
                            {"s0": {"text": text, "start": 0, "end": len(text),
                                    "marker": "Section 0"}}, True))
    out = dr.review_draft("a long combined document", profile=_profile_with(9))
    assert out.get("coverage_warning"), out.keys()
    assert "section" in out["coverage_warning"].lower()


def test_a_well_split_package_is_not_flagged(monkeypatch):
    """The guard. A warning on every review is a warning nobody reads."""
    from services import draft_review as dr
    found = {f"s{i}": {"text": "x", "start": 0, "end": 1, "marker": f"Section {i}"}
             for i in range(7)}
    monkeypatch.setattr(dr, "locate_sections",
                        lambda text, sections, use_ai=True: (found, True))
    out = dr.review_draft("text", profile=_profile_with(9))
    assert not out.get("coverage_warning")


def test_a_proposal_with_only_a_couple_of_sections_is_not_flagged(monkeypatch):
    """Two of two is complete, not poor coverage -- the test is the FRACTION, and
    a tiny profile must not trip it."""
    from services import draft_review as dr
    found = {"s0": {"text": "x", "start": 0, "end": 1, "marker": "Section 0"}}
    monkeypatch.setattr(dr, "locate_sections",
                        lambda text, sections, use_ai=True: (found, True))
    out = dr.review_draft("text", profile=_profile_with(2))
    assert not out.get("coverage_warning")
