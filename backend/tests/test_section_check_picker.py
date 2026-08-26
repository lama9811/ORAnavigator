"""The picker must offer the sections this PROPOSAL has to write.

WHAT WAS WRONG
--------------
`GET /api/me/section-check/sections` called `rulebook_baseline.sections_offered`,
which is keyed on the RULEBOOK and never sees the proposal. So Section Check
offered the PAPPG's seven sections to every proposal, whatever its solicitation
asked for.

Measured on a real NSF 23-598 proposal (#8, 2026-08-26): the solicitation
carries 53 rules and only **24** were reachable from the picker. The largest
unreachable group is a whole deliverable -- **Letter of Intent, 8 scored rules**
about title format, PI contact details and the project synopsis. For this
program the LOI is the FIRST thing NSF requires, and a PI had no way to check
it: it is not a PAPPG section, so it was not offered, and `_section_check_inputs`
would have 400'd it even if it had been.

The fix is not to swap one list for the other. Both are needed and neither
contains the other:

  solicitation only .. Letter of Intent, Letter of Institutional Support
  BOTH ............... Project Summary, Project Description, Budget,
                       Special Information/Supplementary
  PAPPG only ......... References Cited, Facilities, Senior/Key Personnel

Offering only what the solicitation names would drop 48 PAPPG rules -- including
the 34 on biographical sketches -- which NSF enforces whether or not a given
solicitation restates them. 23-598 does not restate them, because the PAPPG
already covers them. That is the normal shape of a solicitation.
"""

import pytest

from services import rulebook_baseline as rb
from services import solicitation_profile as sp


PAPPG = "the PAPPG"


def _row(rid, section, label, *, scored=True, section_label=None):
    """One extracted requirement row.

    `section_label` is the funder's OWN name for the section and is what a real
    extraction carries alongside the canonicalised `section` key. It matters
    here rather than being fixture decoration: `sections_from` falls back to
    title-casing the key when no row supplies one, so leaving it out would put
    "Letter Intent" in the universe and test a profile production never builds.
    """
    row = {"id": rid, "section": section, "label": label,
           "kind": "semantic", "scored": scored,
           "source": f"The solicitation requires: {label}.",
           "why": "", "keywords": []}
    if section_label:
        row["section_label"] = section_label
    return row


def _nsf_like_profile():
    """A profile shaped like the real NSF 23-598 one, on the same section keys.

    `letter_intent` / `budget_justification` are the spellings `canon_section`
    produces, which is what a STORED profile carries -- deliberately NOT the
    rulebook's `budget_and_budget_justification`, because the two names for one
    section are exactly what the merge has to collapse.
    """
    rows = [
        _row("sol_loi_title", "letter_intent",
             "Include required title format in Letter of Intent",
             section_label="Letter of Intent"),
        _row("sol_loi_pi", "letter_intent",
             "Include PI and Co-PI contact information in Letter of Intent",
             section_label="Letter of Intent"),
        _row("sol_ps_loi", "project_summary",
             "Include the LOI number in the Project Summary"),
        _row("sol_bud_equip", "budget_justification",
             "No more than 30% of the budget can be allocated for equipment"),
        _row("sol_collab", "letter_collaboration",
             "Letters of collaboration follow the required single sentence",
             scored=False),
    ]
    return sp.build_generic({}, rows, id="NSF 23-598", title="HBCU-EiR")


def _keys(offered):
    return [s["key"] for s in offered]


# ── the picker ─────────────────────────────────────────────────────────────

def test_the_picker_offers_a_section_only_the_solicitation_names():
    """Letter of Intent is the deliverable this whole change exists for."""
    offered = sp.sections_offered_for(_nsf_like_profile(), PAPPG)
    key = sp.resolve_section_key({s["key"]: s for s in offered}, "Letter of Intent")
    assert key is not None, (
        "the picker does not offer Letter of Intent, so its 8 solicitation "
        f"rules stay unreachable. offered: {_keys(offered)}")


def test_the_picker_still_offers_a_section_only_the_rulebook_covers():
    """A solicitation silent about a section must not remove the NSF baseline.

    The mirror risk of driving the picker from the solicitation: reading "fully
    based on the solicitation" as "drop everything the solicitation is silent
    about" would take References Cited and Facilities off a PI who is writing
    them right now.

    Senior/Key Personnel is deliberately NOT in this list — its 34 rules are all
    `extended`, and Check a Section takes basics only since 2026-08-26. See
    test_section_check_basics_only.py, which asserts that absence and records
    what it costs.
    """
    offered = _keys(sp.sections_offered_for(_nsf_like_profile(), PAPPG))
    for key in ("references_cited", "facilities_equipment_and_other_resources"):
        assert key in offered, f"{key} was dropped; offered: {offered}"


def test_a_section_named_two_ways_is_offered_once():
    """`budget_justification` (solicitation) and `budget_and_budget_justification`
    (rulebook) are one part of a proposal, and a picker showing both is the
    two-spellings bug this repo has now shipped three times."""
    offered = sp.sections_offered_for(_nsf_like_profile(), PAPPG)
    budgets = [s for s in offered
               if sp.section_signature(s["label"]) == sp.section_signature(
                   "Budget and Budget Justification")]
    assert len(budgets) == 1, f"budget appears {len(budgets)} times: {budgets}"


def test_a_solicitation_section_with_nothing_scoreable_is_not_offered():
    """Same test `sections_offered` already applies to Cover Sheet.

    `letter_collaboration` carries one CONDITIONAL row. A section whose every
    row is advisory is a dead end dressed as a tool -- the PI picks it and gets
    a page of "if this applies to you". The row still appears in a full Draft
    Review, which is where an advisory row belongs.
    """
    offered = _keys(sp.sections_offered_for(_nsf_like_profile(), PAPPG))
    assert "letter_collaboration" not in offered, offered


def test_the_solicitations_own_sections_come_first():
    """Order carries the message that the solicitation leads.

    The LOI is also genuinely the first thing a PI writes for this program, so
    the ordering is not only cosmetic.
    """
    offered = _keys(sp.sections_offered_for(_nsf_like_profile(), PAPPG))
    assert offered[0] == "letter_intent", offered


def test_each_offered_section_says_where_its_rules_come_from():
    """The picker has to be able to group without recomputing the split."""
    offered = {s["key"]: s for s in sp.sections_offered_for(_nsf_like_profile(), PAPPG)}
    assert offered["letter_intent"]["solicitation_rules"] == 2
    assert offered["letter_intent"]["rulebook_rules"] == 0
    assert offered["references_cited"]["solicitation_rules"] == 0
    assert offered["references_cited"]["rulebook_rules"] > 0
    ps = offered["project_summary"]
    assert ps["solicitation_rules"] == 1 and ps["rulebook_rules"] > 0


def test_with_no_profile_the_picker_is_exactly_the_rulebooks_own_list():
    """A proposal with no solicitation must be unchanged by this."""
    assert (_keys(sp.sections_offered_for(None, PAPPG))
            == _keys(rb.sections_offered(PAPPG)))


# ── the review itself ──────────────────────────────────────────────────────

def test_a_solicitation_only_section_can_actually_be_reviewed():
    """Offering it is worthless if the engine cannot check it.

    `review_section` reads its label from `rulebook_baseline.section_label`,
    which knows nothing of this section and returns the machine-made
    "Letter Intent". The profile has the real name.
    """
    from services import draft_review
    profile = _nsf_like_profile()
    text = ("Letter of Intent\n\n"
            "Excellence in Research: Adaptive Zwitterionic Networks\n"
            "PI: Dr. A. Rivera, arivera@morgan.edu. Co-PI: Dr. B. Osei.\n")
    result = draft_review.review_section(
        text, section="letter_intent", rulebook=PAPPG,
        profile=profile, use_ai=False)
    assert result["label"] == "Letter of Intent", result["label"]
    ids = {f["id"] for f in result["findings"]}
    assert {"sol_loi_title", "sol_loi_pi"} <= ids, (
        f"the solicitation's own LOI rules were not checked: {sorted(ids)}")
