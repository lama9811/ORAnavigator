"""Check a Section holds the NSF BASICS plus the solicitation — not all 156.

WHY (product decision, 2026-08-26, reported by a PI looking at a real screen)
----------------------------------------------------------------------------
`RULES["the PAPPG"]` is two very different populations merged into one list:

  * 14 CURATED rows, hand-read off Research.gov's own per-section "Content
    Instructions" screens — NSF's own distillation of what each section must
    contain. Every deterministic check in the rulebook lives here.
  * 142 EXTRACTED rows, machine-read from PAPPG Chapter II and reviewed row by
    row. Real rules, but the long tail: fonts, margins, conditionals,
    prohibitions, per-proposal-type edge cases.

Measured on a live proposal, the mix reaching Check a Section was **138 PAPPG
rules against 33 from the solicitation** — and on Budget and Budget
Justification specifically, **45 against 6**. Six rules NSF wrote for that PI's
own program, under forty-five general ones. Their words: "I don't want the check
a section to be on PAPPG rule entirely."

So Check a Section is now BASICS + SOLICITATION. The extracted rows are not
deleted and not weakened — `review_draft` still sees every one of them, which is
where a PI who has never met NSF's font rules should meet them once. This is the
same distinction `checklist_filter` draws keeping 7 of 24 requirements as
tick-boxes while the stored profile keeps all 24, and the same one
`sections_offered` already drew for Cover Sheet.

WHAT IT COSTS, RECORDED HONESTLY
--------------------------------
Three sections lose most or all of their rules, and Senior/Key Personnel — 34
extracted, 0 curated, 0 from that solicitation — loses all of them and drops out
of the picker entirely. Those 34 are what catch a date of birth or a home
address in a biographical sketch. A PI checking a biosketch section by section
now gets nothing; they get those rules in a full Draft Review instead. That is
the accepted trade, not an oversight.
"""

import pytest

from services import draft_review
from services import rulebook_baseline as rb
from services import solicitation_profile as sp


PAPPG = "the PAPPG"


def test_every_rule_declares_which_population_it_belongs_to():
    """A caller must not have to compare against a private list to find out."""
    rows = rb.rules_for(PAPPG)
    tiers = {r.get("tier") for r in rows}
    assert tiers == {"basic", "extended"}, tiers
    # 17 since 2026-08-28: three compound rules were split into two each,
    # so a draft is told WHICH half is missing instead of reading "partial".
    assert sum(1 for r in rows if r["tier"] == "basic") == 19


def test_the_basics_carry_every_deterministic_check():
    """The reason basics-only is safe to ship.

    If a deterministic check lived in the extended set, narrowing Check a
    Section would silently turn a code-decided rule into no rule at all.
    """
    with_checks = [r for r in rb.rules_for(PAPPG) if r.get("check")
                   and r["check"] != "rb_not_in_text"]
    assert with_checks
    assert all(r["tier"] == "basic" for r in with_checks), [
        r["id"] for r in with_checks if r["tier"] != "basic"]


def test_rules_for_can_ask_for_the_basics_alone():
    basics = rb.rules_for(PAPPG, tier="basic")
    assert len(basics) == 19
    assert len(basics) < len(rb.rules_for(PAPPG))


def test_draft_review_still_receives_every_rule():
    """The extended rows are narrowed OUT of one entry point, not weakened.

    `baseline_rows` is what injects the rulebook into a stored profile, and a
    full Draft Review reads that profile. Narrowing it here would delete 142
    rules from the product rather than from one screen.
    """
    reqs = [{"id": "x", "section": "project_summary", "label": "l",
             "kind": "semantic", "scored": True,
             "source": "Follow the PAPPG for proposal preparation.",
             "why": "", "keywords": []}]
    rows = rb.baseline_rows(reqs)
    assert sum(1 for r in rows if r.get("tier") == "extended") > 100, len(rows)


# ── what the PI actually sees ──────────────────────────────────────────────

def _row(rid, section, label, *, section_label=None):
    row = {"id": rid, "section": section, "label": label, "kind": "semantic",
           "scored": True, "source": f"The solicitation requires: {label}.",
           "why": "", "keywords": []}
    if section_label:
        row["section_label"] = section_label
    return row


def _profile_with_budget_rules():
    return sp.build_generic({}, [
        _row("sol_bud_equip", "budget_justification",
             "No more than 30% of the budget may be equipment",
             section_label="Budget and Budget Justification"),
        _row("sol_bud_share", "budget_justification",
             "Voluntary committed cost sharing is prohibited",
             section_label="Budget and Budget Justification"),
    ], id="NSF 23-598", title="t")


def test_a_section_check_is_the_basics_plus_the_solicitation():
    """Budget carries 45 extracted rules and 0 basics. Only the PI's own 2 remain."""
    from services import draft_review
    profile = _profile_with_budget_rules()
    text = ("Budget Justification\n\nSenior personnel effort is 1.5 months for "
            "the PI. Equipment is a potentiostat at $18,400, under 30% of the "
            "total. No voluntary committed cost sharing is included.\n")
    result = draft_review.review_section(
        text, section="budget_and_budget_justification", rulebook=PAPPG,
        profile=profile, use_ai=False)
    ids = {f["id"] for f in result["findings"]}
    assert {"sol_bud_equip", "sol_bud_share"} <= ids, sorted(ids)
    extended = {r["id"] for r in rb.rules_for(PAPPG, tier="extended")}
    assert not (ids & extended), sorted(ids & extended)


def test_a_section_whose_rules_were_all_extended_is_no_longer_offered():
    """Senior/Key Personnel: 34 extracted, 0 basic. The accepted cost.

    Offering it would hand the PI an empty section, which is the dead-end
    `sections_offered` already refuses for Cover Sheet.
    """
    keys = [s["key"] for s in sp.sections_offered_for(_profile_with_budget_rules(),
                                                      PAPPG)]
    assert "senior_key_personnel_documents" not in keys, keys


def test_a_section_the_solicitation_fills_is_still_offered():
    """A section with 0 basics is kept alive by the solicitation alone.

    Budget was this example until 2026-08-27, when it was withheld from the
    picker outright; a Data Management Plan makes the same point without being
    withheld. The property under test is unchanged: a section the rulebook
    holds no basic rules for must still be offered when the solicitation
    fills it.
    """
    profile = sp.build_generic({}, [
        _row("sol_dmp", "data_management_plan",
             "State how data will be shared and archived",
             section_label="Data Management Plan"),
    ], id="NSF 23-598", title="t")
    keys = [s["key"] for s in sp.sections_offered_for(profile, PAPPG)]
    assert "data_management_plan" in keys, keys


def test_the_rulebook_only_picker_offers_only_sections_with_basics():
    """The auth-free route must not advertise sections the review would empty.

    Two pickers disagreeing about which sections exist is how a PI ends up
    selecting one and being told there is nothing on file for it.
    """
    keys = [s["key"] for s in rb.sections_offered(PAPPG)]
    assert set(keys) == {"project_summary", "project_description",
                         "references_cited",
                         "facilities_equipment_and_other_resources"}, keys


def _f(fid, status, rulebook=None):
    row = {"id": fid, "status": status, "scored": True, "label": "L"}
    if rulebook:
        row["rulebook"] = rulebook
    return row


def test_the_caption_does_not_credit_a_solicitation_for_a_rulebooks_rule():
    """A LIVE BUG, found by running a Project Summary through the engine.

    `basis` may name the solicitation in the COUNT only when every counted rule
    really came from that document -- and the guard for that asked whether
    `by_source` had exactly ONE entry, never whether that one entry WAS the
    solicitation. So a section whose only scored rule is the rulebook's
    (Project Summary's deterministic heading check, with every semantic row
    `unclear`) reported "100% of the 1 NSF 23-598 requirements" about a rule
    NSF 23-598 never wrote.

    The mirror of the bug this guard was added for, and it survived because the
    existing test only exercised the two-source case.
    """
    basis = draft_review.score(
        [_f("pappg_ps_headings", "addressed", "the PAPPG")],
        solicitation_id="NSF 23-598")["basis"]
    assert "NSF 23-598 requirements" not in basis, basis


def test_the_caption_still_names_the_solicitation_when_every_rule_is_its_own():
    """The case the id is FOR. Narrowing the guard must not close it."""
    basis = draft_review.score(
        [_f("sol_loi_title", "addressed")], solicitation_id="NSF 23-598")["basis"]
    assert "the 1 NSF 23-598 requirements" in basis, basis


def test_the_caption_names_only_the_authorities_that_actually_contributed():
    """`review_section` hardcoded "the PAPPG's rules for this section".

    On a Letter of Intent -- a section the rulebook holds NO rules for -- that
    put NSF's standing policy's name on a screen where every scored rule came
    from the solicitation. Same error class as the shared contract quote
    stamped on every row: a sentence that looks like provenance while being
    none. `by_source` already knows who contributed; the caption reads from it.
    """
    only_sol = draft_review.score(
        [_f("sol_loi_title", "addressed")], solicitation_id="NSF 23-598")["basis"]
    assert "PAPPG" not in only_sol, only_sol

    both = draft_review.score(
        [_f("pappg_ps_headings", "addressed", "the PAPPG"),
         _f("sol_ps_loi", "partial")], solicitation_id="NSF 23-598")["basis"]
    assert "the PAPPG" in both and "NSF 23-598" in both, both


def test_a_section_check_caption_reflects_the_sections_real_authorities():
    """End to end, through the entry point that hardcoded it.

    Project Summary genuinely draws on both, so both must be named -- and the
    count must not be attributed to either.
    """
    profile = sp.build_generic({}, [
        _row("sol_ps_loi", "project_summary", "Include the LOI number",
             section_label="Project Summary")], id="NSF 23-598", title="t")
    result = draft_review.review_section(
        "Overview\nWe study X.\n\nIntellectual Merit\nY.\n\nBroader Impacts\nZ.\n",
        section="project_summary", rulebook=PAPPG, profile=profile, use_ai=False)
    basis = (result.get("score") or {}).get("basis", "")
    assert basis
    assert "NSF 23-598 requirements" not in basis, basis


def test_the_extended_rows_do_not_arrive_through_the_profile_either():
    """THE ROWS COME IN BY TWO DOORS, and narrowing one is not narrowing.

    `review_section` looks the rulebook up directly AND merges the profile's own
    rows -- and `baseline_rows` has already injected every rulebook rule INTO
    that profile at load time, which is what makes the rulebook retroactive for
    proposals stored before it existed. So filtering only the direct lookup
    changes nothing on a real proposal.

    Measured: after the direct lookup was narrowed, a live Budget check still
    returned 51 rules, 45 of them extended. The earlier unit test missed it
    because its fixture's `source` never CITES the PAPPG, so nothing was
    injected -- the fixture, not the code, was what made the filter look like it
    worked. This one cites it, exactly as a real solicitation does.
    """
    from services import draft_review
    cite = ("Proposers must follow the PAPPG for all budget preparation "
            "requirements.")
    rows = [{"id": "sol_bud_equip", "section": "budget_justification",
             "section_label": "Budget and Budget Justification",
             "label": "Cap equipment at 30 percent", "kind": "semantic",
             "scored": True, "source": cite, "why": "", "keywords": []}]
    profile = sp.build_generic({}, rows, id="NSF 23-598", title="t")

    injected = [r for r in profile["requirements"] if r.get("tier") == "extended"]
    assert injected, "fixture is not exercising the injection path"

    result = draft_review.review_section(
        "Budget Justification\n\nThe PI requests 1.5 months of summer salary.\n",
        section="budget_and_budget_justification", rulebook=PAPPG,
        profile=profile, use_ai=False)
    seen = {f["id"] for f in result["findings"]}
    extended = {r["id"] for r in rb.rules_for(PAPPG, tier="extended")}
    assert not (seen & extended), sorted(seen & extended)[:6]
    assert "sol_bud_equip" in seen, sorted(seen)
