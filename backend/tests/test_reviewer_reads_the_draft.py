"""The reviewer must READ the section, not infer what it probably says.

WHY THIS EXISTS. The engine already gates every POSITIVE claim: `addressed` and
`partial` need a verbatim quote from the draft or they are demoted (golden rule
2). Absence was gated by nothing. `not_found` costs the model no evidence, no
search and no explanation -- so a reply that never really read past the first
paragraph is indistinguishable from one that read the whole section, and it is
the CHEAPER answer to produce.

That asymmetry points the wrong way for this app. A false `addressed` is caught
by the quote gate. A false `not_found` reaches the PI as "you did not write
this" about something they did write, and sends them rewriting it -- the same
error class CLAUDE.md records for `could_not_locate` versus `not_found`, and for
a scraper treating an unreadable page as a deleted one.

Three additions, all about ABSENCE being a considered claim rather than a
default:

  * read the whole section first, because a requirement's content can appear
    anywhere and the requirements are not in document order;
  * before returning `not_found`, search the whole text for other wordings, and
    say in the note what was looked for;
  * never infer from the requirement's own wording, the section's title, or
    what a proposal of this kind usually contains -- only from what is written.

MEASURED, NOT ASSUMED. CLAUDE.md records two prompt changes on this codebase
that were measured and reverted, one of which removed a false positive and cost
two thirds of the recall on real errors. So the effect of this one is measured
in both directions -- on a strong draft (recall must not fall) and a weak one
(false positives must not appear) -- and the numbers live in the commit message.
These tests only hold the instructions in place; they cannot prove the model
obeys them.
"""

from services import draft_review as dr


def _system():
    return dr._review_system("NSF 23-598").lower()


def test_the_reviewer_is_told_to_read_the_whole_section():
    s = _system()
    assert "read the entire draft text" in s, s


def test_absence_must_be_searched_for_before_it_is_reported():
    """`not_found` is the cheap answer; it has to cost something."""
    s = _system()
    assert "before returning 'not_found'" in s, s
    assert "search the whole" in s, s


def test_the_reviewer_is_forbidden_to_infer_from_the_requirement_wording():
    """The specific guessing this closes: answering from what the requirement
    asks for, or from what a proposal of this kind usually contains, rather
    than from the text in front of it."""
    s = _system()
    assert "never infer" in s, s
    for cue in ("requirement's own wording", "section's title", "usually contains"):
        assert cue in s, (cue, s)


def test_content_may_appear_anywhere_in_the_section():
    """Requirements are supplied in a fixed list; a draft answers them in its
    own order. Judging requirement N against paragraph N is a guess."""
    s = _system()
    assert "not in document order" in s, s


# ── the rules that must survive this change ────────────────────────────────

def test_the_verbatim_quote_rule_survives():
    """Golden rule 2. Adding pressure to justify absence must not weaken the
    gate on presence."""
    s = _system()
    assert "verbatim" in s and "no quote means the status is 'not_found'" in s, s


def test_the_presence_not_quality_floor_survives():
    """"If ANY sentence speaks to the requirement, the floor is 'partial'" is
    what stopped the reviewer reporting thin-but-present content as missing.
    Instructions to hunt harder for absence could easily undo it."""
    s = _system()
    assert "the floor is 'partial'" in s, s
    assert "never use 'not_found' to mean 'present but weak'" in s, s


def test_the_reviewer_still_may_not_write_the_authors_prose():
    """The line between this and the Drafting Coach that was deleted by product
    decision."""
    assert "never write the prose for the author" in _system()


def test_the_model_and_region_are_still_named():
    assert dr.MODEL == "gemini-3.6-flash"
    assert dr.MODEL_LOCATION == "global"
