"""A rule you may not be subject to must not count against you.

REPORTED BY THE PI, 2026-08-28. Their Facilities section scored 5 of 6 (83%),
and the missing rule was "Names postdoctoral scholars drawing no funds". Their
budget requests ZERO postdoctoral scholars -- all three years read
`( 0 ) POST DOCTORAL SCHOLARS` -- so there are none to name, and the tool was
asking them to "add an explicit statement confirming whether or not unfunded
postdoctoral scholars will participate": a sentence about people who are not on
the project.

NSF's own wording is conditional:

    "This section should include ANY senior/key personnel or postdoctoral
     scholars for whom no funds are being requested in the budget."

"Any" means "if there are any". Splitting the compound rule (2026-08-28) made
this stable, but stably wrong -- before the split it hid inside a row that came
back `addressed` because the senior-personnel half was met.

So the postdocs half becomes CONDITIONAL, which this engine already has a
meaning for: `scored: False` keeps the row visible and takes it out of the
score, the same treatment as "if you request consultants, detail them".

THE SENIOR/KEY PERSONNEL HALF STAYS SCORED, deliberately. Its wording is equally
conditional, but a Facilities section that names no unfunded contributors at all
is a real gap worth catching, and unfunded senior collaborators are the common
case. Postdocs are not.

WHAT IT COSTS, recorded honestly: a PI who genuinely has an unfunded postdoc and
forgets to name them now sees an advisory row rather than a score penalty. That
is the trade -- an unearned penalty on every proposal without postdocs, against
a softer signal on the few that have one.
"""

from services import rulebook_baseline as rb
from services import section_guidance as sg


def _rule(rid):
    return next(r for r in rb.rules_for("the PAPPG") if r["id"] == rid)


def test_the_postdocs_rule_is_conditional():
    assert _rule("pappg_fe_unfunded_postdocs")["scored"] is False


def test_the_senior_personnel_rule_is_still_scored():
    """A section naming no unfunded contributors at all is a real gap."""
    assert _rule("pappg_fe_unfunded_personnel")["scored"] is True


def test_it_is_still_a_real_rule_carrying_nsfs_sentence():
    """Conditional means unscored, never deleted -- a PI who DOES have an
    unfunded postdoc still gets told to name them."""
    row = _rule("pappg_fe_unfunded_postdocs")
    assert "postdoctoral" in row["source"]
    assert row["tier"] == "basic"
    assert row["section"] == "facilities_equipment_and_other_resources"


# ── a conditional must not read as something you failed ────────────────────

def _f(rid, status, scored=True):
    return {"id": rid, "label": rid, "status": status, "scored": scored,
            "suggestion": "do the thing", "note": "a note"}


def test_an_unmet_conditional_does_not_make_the_plan_say_do_this_first():
    """Otherwise the screen reads "100%" beside "Do this first" -- the same
    self-contradiction this modal has had to unship repeatedly."""
    findings = [_f("met", "addressed"),
                _f("postdocs", "not_found", scored=False)]
    assert sg.priorities_heading(findings) == "Ways to strengthen this", findings


def test_a_real_failure_still_says_do_this_first():
    findings = [_f("real", "not_found"), _f("cond", "not_found", scored=False)]
    assert sg.priorities_heading(findings) == "Do this first"


def test_a_real_failure_outranks_a_conditional_in_the_plan():
    findings = [_f("cond", "not_found", scored=False), _f("real", "not_found")]
    assert [p["id"] for p in sg.priorities(findings)] == ["real"], findings


def test_a_conditional_is_still_listed_when_nothing_real_failed():
    """Out of the score, not off the screen."""
    findings = [_f("met", "addressed"), _f("postdocs", "not_found", scored=False)]
    ids = [p["id"] for p in sg.priorities(findings)]
    assert "postdocs" in ids, ids
