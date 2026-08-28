""""Do this first" must not list rules the draft already meets.

REPORTED BY THE PI, 2026-08-28, from a Facilities check. The plan read:

    Do this first
      Names senior/key personnel and postdocs drawing no funds   <- partial
      Written as a narrative                                     <- ADDRESSED
      Covers internal and external resources, physical/personnel <- ADDRESSED

Two of the three things they were told to do first were rules the section
already satisfies. `priorities()` ranks failures above passes and then hands the
whole mixed list to one heading, so a single `partial` renamed the list "Do this
first" and dragged two met rules under it.

CLAUDE.md already records the rule this breaks, from the other direction:
"'Do this first' over four met rules is approval rendered as failure", which is
why `priorities_heading()` exists at all. The heading was right here -- there
genuinely is one thing to fix -- but the LIST was wrong.

THE FIX: when anything needs fixing, the plan is only the things that need
fixing. When nothing does, the met-but-improvable rows stand alone under "Ways
to strengthen this", exactly as before -- that case is untouched, and there are
tests below to keep it that way.

What this costs: on a section with one failure and three improvable passes, the
three suggestions no longer appear in the plan. They are still on their own rows
in the findings list, where an author reads them in context. A three-line plan
whose first line is real and whose other two are "this passed, but" is not a
plan.
"""

from services import section_guidance as sg


def _f(rid, status, suggestion="do the thing", scored=True):
    return {"id": rid, "label": rid, "status": status, "scored": scored,
            "suggestion": suggestion, "note": "a note"}


def test_a_passing_rule_is_not_listed_under_do_this_first():
    """The exact Facilities shape: one partial, two addressed."""
    findings = [_f("clear_row", "clear"),
                _f("narrative", "addressed"),
                _f("covers", "addressed"),
                _f("names", "partial")]
    plan = sg.priorities(findings)
    assert [p["id"] for p in plan] == ["names"], plan
    assert sg.priorities_heading(findings) == "Do this first"


def test_a_flagged_prohibition_counts_as_something_to_fix():
    findings = [_f("alcohol", "flagged"), _f("narrative", "addressed")]
    plan = sg.priorities(findings)
    assert [p["id"] for p in plan] == ["alcohol"], plan


def test_with_nothing_to_fix_the_improvable_rules_are_the_plan():
    """Unchanged behaviour, and the reason this is not simply 'drop passes'."""
    findings = [_f("a", "addressed"), _f("b", "clear"), _f("c", "addressed")]
    plan = sg.priorities(findings)
    assert [p["id"] for p in plan] == ["a", "b", "c"], plan
    assert sg.priorities_heading(findings) == "Ways to strengthen this"


def test_failures_still_come_before_thin_passes():
    """Ordering within the fixes is untouched: not_found above partial."""
    findings = [_f("thin", "partial"), _f("missing", "not_found")]
    assert [p["id"] for p in sg.priorities(findings)] == ["missing", "thin"]


def test_a_conditional_is_left_out_when_something_real_failed():
    """It used to rank below the real failure and still be listed. Since
    2026-08-28 it is left out entirely while anything real needs fixing -- a
    rule the author may not even be subject to is not part of "do this first".
    It is still visible on its own row, and it IS listed once nothing real is
    failing (see test_conditional_postdocs_rule.py).
    """
    findings = [_f("conditional", "not_found", scored=False),
                _f("real", "not_found")]
    assert [p["id"] for p in sg.priorities(findings)] == ["real"]


def test_rows_nobody_can_act_on_are_still_excluded():
    findings = [_f("skipped", "not_checked"), _f("lost", "unclear"),
                _f("theirs", "delegated"), _f("real", "not_found")]
    assert [p["id"] for p in sg.priorities(findings)] == ["real"]


def test_a_failure_with_nothing_concrete_to_say_is_still_dropped():
    """"improve this" is worse than one fewer line -- and a failing row falls
    back to its note, which says what is missing."""
    findings = [_f("silent", "not_found", suggestion="")]
    findings[0]["note"] = ""
    assert sg.priorities(findings) == []
