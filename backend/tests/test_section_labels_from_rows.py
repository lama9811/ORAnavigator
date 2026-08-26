"""A section's name should be the funder's words, not a title-cased key.

WHAT WENT WRONG
---------------
`canon_section` strips filler when it builds a key, so "Letter of Intent"
becomes `letter_intent`. When no row supplies the section's proper name,
`sections_from` title-cases that key back -- and "of" is gone, so the picker
offered "Letter Intent" and "Letter Collaboration". Reported by a PI looking at
their own proposal.

`section_label` on a row is the intended source and the extraction does emit it
sometimes; these rows did not. But the funder's own wording is still right there
in the requirement labels -- "Include required title format in Letter of
Intent" -- so it can be recovered rather than guessed at.

THE SAFETY PROPERTY IS SET EQUALITY, the same one `section_signature` rests on
everywhere else: a phrase is accepted as the section's name only when its
meaning-carrying words are EXACTLY the key's. That is what stops "Project
Description Supplementary Documents" being read as the name of
`project_description`.
"""

from services import solicitation_profile as sp


def _row(rid, section, label, **kw):
    row = {"id": rid, "section": section, "label": label, "kind": "semantic",
           "scored": True, "source": f"The solicitation requires: {label}.",
           "why": "", "keywords": []}
    row.update(kw)
    return row


def test_the_funders_own_name_is_recovered_from_a_requirement_label():
    sections = sp.sections_from([
        _row("a", "letter_intent",
             "Include required title format in Letter of Intent"),
        _row("b", "letter_intent",
             "Include PI and Co-PI contact information in Letter of Intent"),
    ])
    assert sections["letter_intent"]["label"] == "Letter of Intent"


def test_a_plural_is_kept_as_the_funder_wrote_it():
    sections = sp.sections_from([
        _row("a", "letter_collaboration",
             "Restrict the content of Letters of Collaboration"),
    ])
    assert sections["letter_collaboration"]["label"] == "Letters of Collaboration"


def test_an_explicit_section_label_still_wins():
    """The intended source. Mining is the fallback, never the override."""
    sections = sp.sections_from([
        _row("a", "letter_intent", "Include the title format in Letter of Intent",
             section_label="Letter of Intent (LOI)"),
    ])
    assert sections["letter_intent"]["label"] == "Letter of Intent (LOI)"


def test_a_longer_phrase_is_not_mistaken_for_the_section_name():
    """SET EQUALITY, not containment. "Project Description Supplementary
    Documents" carries extra meaning-words, so it can never be read as the name
    of `project_description` -- the hole equality exists to close."""
    sections = sp.sections_from([
        _row("a", "project_description",
             "Do not put Project Description Supplementary Documents inline"),
    ])
    assert sections["project_description"]["label"] == "Project Description"


def test_a_key_with_no_recoverable_name_falls_back_as_before():
    sections = sp.sections_from([_row("a", "data_plan", "Provide a plan")])
    assert sections["data_plan"]["label"] == "Data Plan"


def test_mining_never_changes_the_key():
    """The key is the checklist's `source_ref` and every row points at it.
    Only the display name is recovered."""
    sections = sp.sections_from([
        _row("a", "letter_intent", "Include the title format in Letter of Intent"),
    ])
    assert "letter_intent" in sections


def test_a_name_mined_from_mid_sentence_is_capitalised():
    """Live on a real proposal: the phrase was mined from "Restrict the content
    of letters of collaboration to a single sentence" and rendered in the
    picker as "letters of collaboration", lower-case, beside properly-cased
    section names. Only a wholly lower-case phrase is touched -- a name the
    funder capitalised is left exactly as written."""
    sections = sp.sections_from([
        _row("a", "letter_collaboration",
             "Restrict the content of letters of collaboration to one sentence"),
    ])
    assert sections["letter_collaboration"]["label"] == "Letters of Collaboration"


def test_filler_words_stay_lower_case_when_capitalising():
    sections = sp.sections_from([
        _row("a", "facilitie_equipment_other_resource",
             "Do not put costs in facilities, equipment and other resources"),
    ])
    assert sections["facilitie_equipment_other_resource"]["label"] == (
        "Facilities, Equipment and Other Resources")


def test_the_funders_own_capitalisation_is_never_overwritten():
    sections = sp.sections_from([
        _row("a", "letter_intent", "Include the format in Letter of INTENT"),
    ])
    assert sections["letter_intent"]["label"] == "Letter of INTENT"
