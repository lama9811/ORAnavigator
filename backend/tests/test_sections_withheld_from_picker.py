"""Four parts of a proposal are not offered for a one-section check.

PRODUCT DECISION, 2026-08-27, by the PI: Budget, Budget Justification, Letter of
Intent and Letter of Collaboration come out of the Check a Section picker.

WHY EACH ONE, so a future reader can judge whether the reason still holds:

  * Budget / Budget Justification -- the figures are the Budget Helper's job,
    and that tool computes them deterministically. A section checker can only
    read the prose around numbers it cannot verify.
  * Letter of Intent -- it is not a part of the proposal at all. NSF 23-598
    states an LOI deadline MONTHS before the full proposal; it is a separate
    submission. Listing it among proposal sections teaches a first-time PI
    something false about what a proposal contains. CLAUDE.md has carried this
    as an open question with three options and no decision; this is the
    decision.
  * Letter of Collaboration -- boilerplate whose wording NSF fixes and which a
    collaborator, not the PI, signs.
  * Letter of Institutional Support -- the chair or dean writes it, not the PI,
    and the only rule the app holds for it is a page limit. It is an attachment
    to chase, which the checklist already tracks, not a section to check.

THE TEST APPLIED, so a future section can be judged the same way: a part belongs
in this picker only when the PI WRITES it, getting it wrong has a consequence
they cannot see coming, and the app holds rules that can actually be checked
against text. Budget fails the first (it is the Budget Helper's job), Letter of
Intent fails it differently (a separate submission, months earlier), and Cover
Sheet and Format of the Proposal fail the third.

WITHHELD FROM THE PICKER, NOT DELETED. Every rule stays in the stored profile
and a full Draft Review still checks all of them, which is exactly what already
happens to Cover Sheet and Format of the Proposal. That distinction is the whole
safety of this change: NSF 23-598's Budget rules include the 30% equipment cap
and the ban on voluntary committed cost sharing, and losing those would be a
real compliance hole rather than a tidier menu.
"""



from services import draft_review
from services import solicitation_profile as sp


PAPPG = "the PAPPG"


def _row(rid, section, label, *, scored=True):
    return {"id": rid, "section": section, "label": label, "kind": "semantic",
            "scored": scored, "why": "", "keywords": [],
            "source": (f"{label}. Follow the PAPPG for all other requirements.")}


def _profile():
    """A solicitation that writes rules for all four withheld parts, plus two
    that must survive."""
    return sp.build_generic({}, [
        _row("bud_equip", "budget_justification",
             "No more than 30% of the budget may be equipment"),
        _row("bud_share", "budget_justification",
             "Voluntary committed cost sharing is prohibited"),
        _row("loi_number", "letter_intent", "Include the LOI number"),
        _row("loi_pi", "letter_intent", "List the PI as point of contact"),
        _row("collab", "letter_collaboration",
             "Use the single-sentence collaboration statement"),
        _row("pd_sustain", "project_description",
             "Include a sustainability plan"),
        _row("ps_loi", "project_summary", "Include the LOI number"),
    ], id="NSF 23-598", title="t")


WITHHELD = {"budget_and_budget_justification", "budget_justification",
            "letter_intent", "letter_collaboration",
            "letter_of_institutional_support"}


def _keys(profile=None):
    return {s["key"] for s in sp.sections_offered_for(profile, PAPPG)}


def test_the_picker_does_not_offer_the_four_withheld_parts():
    offered = _keys(_profile())
    assert not (offered & WITHHELD), sorted(offered & WITHHELD)


def test_the_picker_still_offers_everything_else():
    offered = _keys(_profile())
    assert {"project_summary", "project_description"} <= offered, sorted(offered)
    assert offered, "the picker was emptied"


def test_the_rulebook_only_picker_withholds_them_too(monkeypatch):
    """The auth-free fallback, served when a proposal has no solicitation. It
    must not offer what the authenticated one hides, or the menu changes shape
    depending on whether a document is attached.

    The rulebook's own list happens to contain none of the four today -- Budget
    holds only `extended` rules and the letters are not rulebook sections at all
    -- so asserting against it directly passes for free, which mutation-testing
    caught. The rulebook is stubbed to offer Budget so the filter has something
    to remove and the guard can actually fail.
    """
    from services import rulebook_baseline
    real = rulebook_baseline.sections_offered
    monkeypatch.setattr(rulebook_baseline, "sections_offered",
                        lambda book: [{"key": "budget_and_budget_justification",
                                       "label": "Budget and Budget Justification"}]
                                     + list(real(book)))
    keys = _keys(None)
    assert "budget_and_budget_justification" not in keys, sorted(keys)
    assert keys, "the picker was emptied"


def test_the_rules_are_still_stored_on_the_proposal():
    """Withheld from one menu, not deleted. This is the guard that separates
    this change from a compliance hole."""
    ids = {r["id"] for r in _profile()["requirements"]}
    assert {"bud_equip", "bud_share", "loi_number", "collab"} <= ids, sorted(ids)


def test_draft_review_still_checks_every_withheld_rule():
    """The reason withholding is safe: the full review is unchanged, so the 30%
    equipment cap and the cost-sharing ban are still enforced somewhere."""
    text = ("Budget Justification\nEquipment is a potentiostat at $18,400.\n\n"
            "Letter of Intent\nThe PI is the point of contact.\n\n"
            "Project Description\nA sustainability plan is included.\n")
    result = draft_review.review_draft(text, profile=_profile(), use_ai=False)
    ids = {f["id"] for f in result["findings"]}
    assert {"bud_equip", "bud_share", "loi_number", "collab"} <= ids, sorted(ids)


def test_the_letter_of_institutional_support_is_not_offered():
    """One rule, a page limit -- and the chair or dean writes the letter, not
    the PI. It fails the first and third parts of the test above."""
    profile = sp.build_generic({}, [
        _row("inst", "letter_of_institutional_support",
             "The letter must not exceed two pages"),
        _row("pd", "project_description", "Include a sustainability plan"),
    ], id="NSF 23-598", title="t")
    keys = _keys(profile)
    assert "letter_of_institutional_support" not in keys, sorted(keys)
    assert "project_description" in keys, sorted(keys)
