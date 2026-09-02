"""A filename that NAMES a section should file the file under it, deterministically.

WHY THIS EXISTS. Measured over five identical whole-package reviews of the awarded NSF EiR
proposal: the score moved 78/77/79/76/78% and **9 of 76 rules moved**. Four of those nine are
`supplementary_document` rules that came back `could_not_locate` in FOUR runs and were judged
in ONE, taking the score's denominator `assessed` 45 <-> 49 with them. A whole section of the
proposal appeared and disappeared between runs.

The cause is not the reviewer. `map_files_to_sections` reads
`11-Letters-and-Supplementary-Documents.pdf` as the heading "Letters and Supplementary
Documents", WRITES that line into the combined document itself, and then
`resolve_section_key` refuses it because it demands SET EQUALITY of meaning-carrying words:

    filename  {letter, supplementary, document}
    section   {supplementary, document}      <- supplementary_document, really in the universe

One extra word. `_locate_fallback` misses it too (it matches only aliases, whole-line), so the
heading we authored is legible to a language model -- which is why Gemini found it 1 run in 5
-- and invisible to both deterministic paths. We hold the answer in the filename and pay a
model to guess it back.

WHY THE SHARED MATCHER IS NOT LOOSENED. `resolve_section_key`'s set-equality is what stops
"Project Description Supplementary Documents" folding into `project_description` and losing a
real section. It is called by the locate stage and by `generic_checks`; loosening it there
would change all of them. So the widening lives in a filename-only resolver, the same way
`rulebook_checks` normalises its own probe rather than widening the shared `heading_regex`.

THE COVERAGE GUARD IS NOT OPTIONAL, and "exactly one subset match" is NOT sound without it.
That rule's safety in the Project-Description case comes from the universe happening to hold
BOTH sections. A universe holding a section signed {plan} would swallow
`Data-Management-and-Sharing-Plan.pdf` outright, with no ambiguity to stop it. Requiring the
match to account for at least half the filename's meaning-words is what makes it sound, and
`test_a_one_word_section_does_not_swallow_a_longer_filename` is the test that proves the guard
rather than the ambiguity is doing the work.
"""
import pytest

from services import document_text as dt
from services import solicitation_profile as sp


def _sections(*names):
    """A section universe keyed the way `sections_from` keys one."""
    return {sp.section_key(n): {"label": n, "aliases": sp.aliases_for(n)}
            for n in names}


def _pdf(filename, text="Body text of the document goes here."):
    return {"filename": filename, "text": text, "pages": 1,
            "chars": len(text), "truncated": False, "error": None}


# ── the measured case ───────────────────────────────────────────────────────

def test_letters_and_supplementary_documents_finds_its_section():
    """The exact file whose section vanished in 4 of 5 real runs.

    Keyed `supplementary_document` (singular) as a live profile really is:
    requirement rows arrive through `canon_section`, which singularises, while
    `section_key` does not. The mismatch is real and worth encoding here."""
    sections = {"supplementary_document": {"label": "Supplementary Documents",
                                           "aliases": sp.aliases_for("Supplementary Documents")},
                "project_description": {"label": "Project Description",
                                        "aliases": sp.aliases_for("Project Description")}}
    _text, spans, leftover, mapping = dt.map_files_to_sections(
        [_pdf("11-Letters-and-Supplementary-Documents.pdf")], sections)

    assert "supplementary_document" in spans, list(spans)
    assert leftover == []
    assert mapping[0]["section"] == "supplementary_document"


def test_a_wordier_filename_finds_the_plan_it_names():
    sections = _sections("Data Management Plan", "Project Description")
    _t, spans, leftover, _m = dt.map_files_to_sections(
        [_pdf("09-Data-Management-and-Sharing-Plan.pdf")], sections)

    assert "data_management_plan" in spans, list(spans)
    assert leftover == []


# ── the guards, which matter more than the happy paths ──────────────────────

def test_a_filename_that_could_be_two_sections_is_refused():
    """The containment hole, at the filename layer.

    "Project Description Supplementary Documents" contains BOTH sections, so
    matching it to either would silently lose the other."""
    sections = _sections("Project Description", "Supplementary Documents")
    _t, spans, leftover, mapping = dt.map_files_to_sections(
        [_pdf("Project-Description-Supplementary-Documents.pdf")], sections)

    assert spans == {}, spans
    assert len(leftover) == 1
    assert mapping[0]["section"] is None


def test_a_one_word_section_does_not_swallow_a_longer_filename():
    """THE COVERAGE GUARD, isolated so nothing else can be doing the work.

    Only ONE section in this universe, so there is no ambiguity to refuse on:
    if `Data-Management-and-Sharing-Plan.pdf` lands under `plan`, the rule is
    unsound and this test is the only thing that says so."""
    sections = _sections("Plan")
    _t, spans, leftover, _m = dt.map_files_to_sections(
        [_pdf("09-Data-Management-and-Sharing-Plan.pdf")], sections)

    assert spans == {}, f"a one-word section swallowed a four-word filename: {spans}"
    assert len(leftover) == 1


def test_a_single_shared_word_never_matches():
    """`Current and Pending Support` must not land under a `Support` section."""
    sections = _sections("Support")
    _t, spans, _l, _m = dt.map_files_to_sections(
        [_pdf("07-Current-and-Pending-Support.pdf")], sections)
    assert spans == {}, spans


def test_a_section_the_solicitation_never_named_is_not_invented():
    """NSF folds biosketch / current-and-pending / synergistic activities into
    one upload slot, so there is no section for these three to land in. Falling
    through to the locate stage is the correct outcome, not a failure."""
    sections = _sections("Project Description", "Supplementary Documents")
    for name in ("06-Biographical-Sketch.pdf", "07-Current-and-Pending-Support.pdf",
                 "08-Synergistic-Activities.pdf"):
        _t, spans, leftover, _m = dt.map_files_to_sections([_pdf(name)], sections)
        assert spans == {}, f"{name} invented {list(spans)}"
        assert len(leftover) == 1


# ── narrowing: the one tier that can produce a WRONG verdict ────────────────

def test_a_narrower_filename_resolves_to_the_only_section_that_extends_it():
    """`Mentoring Plan` -> `postdoctoral_mentoring_plan`.

    Reported distinctly, because this is the ONLY match in this module that can
    put a document under a section it is not: a graduate mentoring plan filed
    under a postdoctoral one is then JUDGED there. Every other tier can only
    move a rule from unassessed to assessed against text that really is that
    section."""
    sections = _sections("Postdoctoral Mentoring Plan", "Project Description")
    _t, spans, leftover, mapping = dt.map_files_to_sections(
        [_pdf("10-Mentoring-Plan.pdf")], sections)

    assert "postdoctoral_mentoring_plan" in spans, list(spans)
    assert leftover == []
    assert mapping[0]["source"] == "filename_narrowed", mapping[0]["source"]


def test_a_narrowing_match_is_refused_when_two_sections_extend_it():
    sections = _sections("Postdoctoral Mentoring Plan", "Graduate Mentoring Plan")
    _t, spans, _l, _m = dt.map_files_to_sections(
        [_pdf("10-Mentoring-Plan.pdf")], sections)
    assert spans == {}, spans


def test_a_narrowing_match_is_refused_when_more_than_one_word_is_missing():
    sections = _sections("Postdoctoral Faculty Mentoring Plan")
    _t, spans, _l, _m = dt.map_files_to_sections(
        [_pdf("10-Mentoring-Plan.pdf")], sections)
    assert spans == {}, spans


def test_a_one_word_filename_never_narrows():
    """A single word is too little to claim a longer, more specific section."""
    sections = _sections("Postdoctoral Mentoring Plan")
    _t, spans, _l, _m = dt.map_files_to_sections([_pdf("Plan.pdf")], sections)
    assert spans == {}, spans


# ── the boundary is recorded where the loosening lives ──────────────────────

def test_the_shared_matcher_is_untouched():
    """Re-asserted HERE, in the file that widens filename matching, so the
    boundary is visible to whoever changes it next. The three existing guards
    (test_pappg_rules_wiring, test_solicitation_profile, test_section_labels_from_rows)
    must also keep passing unmodified."""
    sections = _sections("Project Description")
    assert sp.resolve_section_key(
        sections, "Project Description Supplementary Documents") is None


def test_an_explicit_choice_still_beats_every_guess():
    sections = _sections("Project Description", "Supplementary Documents")
    f = _pdf("11-Letters-and-Supplementary-Documents.pdf")
    f["section"] = "project_description"
    _t, spans, _l, mapping = dt.map_files_to_sections([f], sections)
    assert list(spans) == ["project_description"]
    assert mapping[0]["source"] == "chosen"
