"""A rule the funder wrote as a PROHIBITION must be judged as one.

MEASURED 2026-09-01 on the AWARDED NSF EiR package, its 11 real section files:

    rule:   "Do not include voluntary committed cost sharing"
    note:   "The draft does not include any voluntary committed cost sharing."
    status: NOT FOUND            prohibition: False

The reviewer confirmed the proposal COMPLIES and we scored it as a miss --
verified independently, "cost sharing" appears ZERO times in that budget
justification. Absence means PASS for a prohibition and FAIL for everything else,
and only rows carrying `flag_if_present` are read with the first vocabulary. The
19 curated PAPPG prohibitions were marked by hand at build time; rows read out of
a SOLICITATION were never marked at all, so every one of them was judged with the
wrong vocabulary.

THE MIRROR IS THE DANGEROUS DIRECTION, and it is why this detector is tight:
marking a CONTENT rule as a prohibition makes a draft that OMITS it come back
`clear` -- reporting a missing requirement as compliance. Every guard below is a
real requirement shape that must NOT be caught.
"""
from services import solicitation_profile as sp


def _row(label, source):
    return {"id": "r", "label": label, "source": source, "kind": "semantic",
            "scored": True, "section": "budget_justification"}


def _marked(label, source):
    return sp.mark_extracted_prohibitions([_row(label, source)])[0].get("flag_if_present")


# ── the shapes a funder actually uses ───────────────────────────────────────

def test_do_not_is_a_prohibition():
    assert _marked("Do not include voluntary committed cost sharing",
                   "Do not include voluntary committed cost sharing in the budget.")


def test_is_prohibited_is_a_prohibition():
    assert _marked("Prohibit voluntary committed cost sharing",
                   "Inclusion of voluntary committed cost sharing is prohibited.")


def test_must_not_is_a_prohibition():
    assert _marked("Exclude collaborator support letters",
                   "The proposal must not include letters of support from collaborators.")


def test_may_not_is_a_prohibition():
    assert _marked("No indirect costs on participant support",
                   "Indirect costs may not be applied to participant support costs.")


# ── the mirrors: a content rule wrongly marked reports a gap as compliance ──

def test_an_ordinary_content_requirement_is_not_a_prohibition():
    assert not _marked("Provide sustainability plan in Project Description",
                       "The Project Description must include a sustainability plan.")


def test_a_page_limit_is_not_a_prohibition():
    """A limit has a deterministic check of its own; treating it as a
    prohibition would make an over-length draft read as compliant."""
    assert not _marked("Limit Letter of Institutional Support to two pages",
                       "The Letter of Institutional Support is limited to two pages.")


def test_a_negative_inside_ordinary_prose_is_not_a_prohibition():
    """'could not' is English, not a directive."""
    assert not _marked("Explain why the work could not be done elsewhere",
                       "Describe why the research could not be carried out at "
                       "another institution.")


def test_describing_what_is_not_included_is_not_a_prohibition():
    assert not _marked("Describe personnel for whom no funds are requested",
                       "Identify senior personnel for whom no funding is being "
                       "requested in this proposal.")


# ── it must not disturb anything else ───────────────────────────────────────

def test_a_row_already_marked_is_left_alone():
    row = _row("Prohibit x", "Do not do x.")
    row["flag_if_present"] = True
    assert sp.mark_extracted_prohibitions([row])[0]["flag_if_present"] is True


def test_a_rulebook_row_is_never_touched():
    """The PAPPG's rows were marked by hand at build time; re-deciding them here
    would put two authorities on one flag."""
    row = _row("Some PAPPG rule", "Do not do the thing.")
    row["rulebook"] = "the PAPPG"
    assert not sp.mark_extracted_prohibitions([row])[0].get("flag_if_present")


def test_nothing_else_about_the_row_changes():
    rows = sp.mark_extracted_prohibitions([_row("Do not x", "Do not x.")])
    assert rows[0]["label"] == "Do not x" and rows[0]["scored"] is True
