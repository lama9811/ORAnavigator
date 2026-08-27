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

    `supplementary_document` is the spelling `canon_section` produces, which is
    what a STORED profile carries -- deliberately NOT the rulebook's
    `special_information_and_supplementary_documentation`, because the two names
    for one section are exactly what the merge has to collapse.

    Budget and Letter of Intent used to be this fixture's solicitation-only
    examples. Both are withheld from the picker since 2026-08-27, so a Data
    Management Plan stands in: still a real part of a proposal, still one the
    rulebook holds no basics for, still not withheld.
    """
    rows = [
        _row("sol_dmp_share", "data_management_plan",
             "State how data will be shared and archived",
             section_label="Data Management Plan"),
        _row("sol_dmp_format", "data_management_plan",
             "Name the formats and repositories to be used",
             section_label="Data Management Plan"),
        _row("sol_ps_loi", "project_summary",
             "Include the LOI number in the Project Summary"),
        _row("sol_supp", "supplementary_document",
             "Letters of support follow the required format",
             section_label="Supplementary Documents"),
        _row("sol_mentor", "postdoctoral_mentoring_plan",
             "Describe mentoring activities if postdocs are requested",
             scored=False),
    ]
    return sp.build_generic({}, rows, id="NSF 23-598", title="HBCU-EiR")


def _keys(offered):
    return [s["key"] for s in offered]


# ── the picker ─────────────────────────────────────────────────────────────

def test_the_picker_offers_a_section_only_the_solicitation_names():
    """A section the rulebook has never heard of must still be checkable."""
    offered = sp.sections_offered_for(_nsf_like_profile(), PAPPG)
    key = sp.resolve_section_key({s["key"]: s for s in offered},
                                 "Data Management Plan")
    assert key is not None, (
        "the picker does not offer the Data Management Plan, so its "
        f"solicitation rules stay unreachable. offered: {_keys(offered)}")


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
    """`supplementary_document` (solicitation) and
    `special_information_and_supplementary_documentation` (rulebook) are one
    part of a proposal, and a picker showing both is the two-spellings bug this
    repo has now shipped three times. Budget used to be this example and is now
    withheld, so the other named equivalence carries the test."""
    offered = sp.sections_offered_for(_nsf_like_profile(), PAPPG)
    wanted = sp._equivalent_signatures(sp.section_signature("Supplementary Documents"))
    hits = [s for s in offered if sp.section_signature(s["label"]) in wanted]
    assert len(hits) == 1, f"supplementary appears {len(hits)} times: {hits}"


def test_a_solicitation_section_with_nothing_scoreable_is_not_offered():
    """Same test `sections_offered` already applies to Cover Sheet.

    `postdoctoral_mentoring_plan` carries one CONDITIONAL row. A section whose
    every row is advisory is a dead end dressed as a tool -- the PI picks it and
    gets a page of "if this applies to you". The row still appears in a full
    Draft Review, which is where an advisory row belongs.

    Deliberately NOT `letter_collaboration` any more: that is now withheld
    outright, so it would pass this test for the wrong reason and stop guarding
    the unscoreable rule at all.
    """
    offered = _keys(sp.sections_offered_for(_nsf_like_profile(), PAPPG))
    assert "postdoctoral_mentoring_plan" not in offered, offered


def test_the_solicitations_own_sections_come_first():
    """Order carries the message that the solicitation leads.

    A section the rulebook has never heard of has no place in the rulebook's
    order, so the front is where it belongs rather than the tail.
    """
    offered = _keys(sp.sections_offered_for(_nsf_like_profile(), PAPPG))
    assert offered[0] == "data_management_plan", offered


def test_each_offered_section_says_where_its_rules_come_from():
    """The picker has to be able to group without recomputing the split."""
    offered = {s["key"]: s for s in sp.sections_offered_for(_nsf_like_profile(), PAPPG)}
    assert offered["data_management_plan"]["solicitation_rules"] == 2
    assert offered["data_management_plan"]["rulebook_rules"] == 0
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
    which knows nothing of this section and would return a machine-made name.
    The profile has the real one.
    """
    from services import draft_review
    profile = _nsf_like_profile()
    text = ("Data Management Plan\n\n"
            "Datasets are deposited in a public repository in open formats.\n")
    result = draft_review.review_section(
        text, section="data_management_plan", rulebook=PAPPG,
        profile=profile, use_ai=False)
    assert result["label"] == "Data Management Plan", result["label"]
    ids = {f["id"] for f in result["findings"]}
    assert {"sol_dmp_share", "sol_dmp_format"} <= ids, (
        f"the solicitation's own rules were not checked: {sorted(ids)}")


# ── ONE FLAT LIST, in the order a proposal is written ──────────────────────
#
# The picker grouped its sections under three headings naming where each
# section's rules came from. Every section is checked against BOTH sources, so
# the headings described provenance, not behaviour -- and a PI read them as
# "these are checked differently". Their words: "IF IT CHECKS BOTH THEN DON'T
# GROUP IT."
#
# Removing the headings makes the ORDER carry the whole message, and the order
# the groups were hiding was arbitrary once flattened: solicitation-named
# sections came first, so Budget led the list and Project Summary sat fifth.

def _late_section_first_profile():
    """A profile whose FIRST extracted row belongs to a LATE section.

    The live proposal was shaped this way and it is what exposed the ordering:
    sections were emitted in the order the solicitation happened to mention
    them, so a late part led the picker and Project Summary sat fifth. Budget
    was the original example and is withheld now, so Supplementary Documents --
    which the rulebook orders near the end -- carries it.
    """
    rows = [
        _row("sol_supp", "supplementary_document",
             "Letters of support follow the required format",
             section_label="Supplementary Documents"),
        _row("sol_dmp", "data_management_plan", "State how data will be shared",
             section_label="Data Management Plan"),
        _row("sol_ps", "project_summary", "Include the LOI number"),
    ]
    return sp.build_generic({}, rows, id="NSF 23-598", title="t")


def test_a_late_section_does_not_jump_the_queue_just_because_it_was_first():
    offered = _keys(sp.sections_offered_for(_late_section_first_profile(), PAPPG))
    late = sp.resolve_section_key({k: {"label": k} for k in offered},
                                  "Supplementary Documents")
    assert late in offered, offered
    assert offered.index("project_summary") < offered.index(late), offered


def test_the_rulebook_sections_keep_the_order_a_proposal_is_written_in():
    """Research.gov's own order, which is the order a PI meets these parts."""
    offered = _keys(sp.sections_offered_for(_nsf_like_profile(), PAPPG))
    known = [k for k in offered if k in
             {s["key"] for s in rb.sections_offered(PAPPG)}]
    assert known == [s["key"] for s in rb.sections_offered(PAPPG)
                     if s["key"] in known], known


def test_a_section_only_the_solicitation_names_comes_first():
    """A Data Management Plan is not in the rulebook's order because the
    rulebook has never heard of it, so the front is where it belongs rather
    than the tail."""
    offered = _keys(sp.sections_offered_for(_nsf_like_profile(), PAPPG))
    assert offered[0] == "data_management_plan", offered
    assert offered.index("data_management_plan") < offered.index("project_summary")
