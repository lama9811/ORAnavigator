"""Length and priorities: the two things that must NOT depend on the model.

WHY DETERMINISTIC. A PI pasted a 76-word Project Summary and was told six of
eight rules were "Addressed". Nothing was wrong with the checks — those rules
are about PRESENCE, and the summary does contain an objectives sentence. What
was wrong is that the tool had no way to say the obvious thing: a one-page
Project Summary allows about 550 words and this one uses 14% of it.

The same PI's earlier run and mine disagreed on how many rows passed, so a model
answering "is this thin?" would be inconsistent on exactly the question they
already distrust. Word counts and an ordering are arithmetic. They go in code.
"""
import pytest

from services import section_guidance as sg


# ── length against the allowance ────────────────────────────────────────────

def test_a_short_section_is_reported_against_its_allowance():
    g = sg.length_guidance(76, page_limit=1)
    assert g["words"] == 76
    assert g["allowance"] == 550          # generic_checks.WORDS_PER_PAGE
    assert g["pct"] == 14
    assert "550" in g["message"] and "14" in g["message"]


def test_it_is_a_measurement_and_never_a_verdict():
    """The PAPPG sets no MINIMUM for any section. Saying a short section is
    'too short' would invent a rule; saying it uses 14% of the page is a fact
    the PI can act on. Same line the page checks already hold: an estimate is
    reported, never a pass or a fail."""
    g = sg.length_guidance(76, page_limit=1)
    low = g["message"].lower()
    for banned in ("too short", "too long", "fail", "must ", "violat", "insufficient"):
        assert banned not in low, f"{banned!r} turns a measurement into a verdict"


def test_a_section_using_its_page_is_not_nagged():
    """A summary at 480 of 550 words is fine and must not be told anything. The
    message exists to surface a gap, not to comment on every draft."""
    assert sg.length_guidance(480, page_limit=1) is None
    assert sg.length_guidance(550, page_limit=1) is None


def test_over_the_allowance_is_reported_too_and_still_not_a_verdict():
    g = sg.length_guidance(900, page_limit=1)
    assert g is not None and g["pct"] == 164
    assert "over" in g["message"].lower()
    assert "fail" not in g["message"].lower()


def test_no_page_limit_means_no_message():
    """Most sections state no page limit. Inventing an allowance for them would
    be fabricating the very rule this is careful not to state."""
    assert sg.length_guidance(76, page_limit=None) is None
    assert sg.length_guidance(76, page_limit=0) is None


def test_an_empty_section_is_not_reported_as_a_length_problem():
    """Nothing pasted is a different problem, already reported by the locate
    stage. A '0% of your page' line would bury it."""
    assert sg.length_guidance(0, page_limit=1) is None


# ── what to do first ────────────────────────────────────────────────────────

def _f(id, status, suggestion="", note="", scored=True, label=None):
    return {"id": id, "status": status, "suggestion": suggestion, "note": note,
            "scored": scored, "label": label or id}


def test_priorities_put_real_failures_before_thin_passes():
    """Ordering among the things to FIX: absent before thin.

    The met row ("a") used to be listed third. It is not any more -- a PI
    reported a Facilities plan headed "Do this first" whose second and third
    entries were rules the section already met, so a plan containing anything
    to fix now contains ONLY things to fix. See
    test_priorities_only_lists_fixes.py, which owns that behaviour and keeps the
    all-passing case intact.
    """
    out = sg.priorities([
        _f("a", "addressed", "Name who benefits."),
        _f("b", "not_found", "Add the LOI number."),
        _f("c", "partial", "State the specific advance."),
    ])
    assert [p["id"] for p in out] == ["b", "c"]


def test_a_flagged_prohibition_ranks_with_the_failures():
    out = sg.priorities([_f("a", "partial", "x"), _f("b", "flagged", "Remove the wine.")])
    assert out[0]["id"] == "b"


def test_an_advisory_row_never_outranks_a_real_one():
    """A conditional the PI may not be subject to ("if you request consultants")
    is not the first thing to do. Measured: 10 of 14 not_found rows on a clean
    Budget Justification were conditionals it was never subject to."""
    out = sg.priorities([
        _f("cond", "not_found", "Detail your consultants.", scored=False),
        _f("real", "partial", "State the advance."),
    ])
    assert out[0]["id"] == "real"


def test_rows_with_nothing_to_suggest_are_dropped():
    """A priority entry with no suggestion is a line that tells the PI to do
    something unspecified. Better absent."""
    out = sg.priorities([_f("a", "not_found", ""), _f("b", "not_found", "Add X.")])
    assert [p["id"] for p in out] == ["b"]


def test_a_row_falls_back_to_its_note_when_it_has_no_suggestion():
    """Deterministic checks return a detail, not a model suggestion, and their
    detail is already actionable ("Each must be on a line of its own")."""
    out = sg.priorities([_f("a", "not_found", "", note="Put each heading on its own line.")])
    assert out[0]["text"] == "Put each heading on its own line."


def test_statuses_nobody_can_act_on_are_excluded():
    """not_checked, could_not_locate, delegated and not_in_draft are all "nobody
    looked" or "not yours". None belongs in a to-do list."""
    out = sg.priorities([_f(s, s, "do something") for s in
                         ("not_checked", "could_not_locate", "delegated",
                          "not_in_draft", "unclear")])
    assert out == []


def test_the_list_is_capped_so_it_stays_a_plan():
    out = sg.priorities([_f(f"r{i}", "not_found", f"Fix {i}.") for i in range(20)])
    assert len(out) == 3
    assert sg.priorities([_f(f"r{i}", "not_found", f"Fix {i}.") for i in range(20)],
                         limit=5).__len__() == 5


def test_priorities_are_stable_and_ties_break_by_the_order_the_rules_arrived():
    """Two runs of one paste must not reorder the plan — the PI already
    distrusts run-to-run variance and this half must not add to it.

    Ties break by INPUT ORDER, which is the profile's requirement order and is
    itself stable. Asserted rather than assumed: an unstable sort here would be
    invisible until a PI noticed their to-do list shuffling between runs of an
    unchanged draft."""
    rows = [_f("a", "partial", "x"), _f("b", "not_found", "y"), _f("c", "partial", "z")]
    assert sg.priorities(rows) == sg.priorities(rows)
    assert [p["id"] for p in sg.priorities(rows)] == ["b", "a", "c"]
    # Reversing the input reverses the tie, and nothing else moves: the failure
    # still outranks both partials.
    assert [p["id"] for p in sg.priorities(list(reversed(rows)))] == ["b", "c", "a"]


# ── the suggestion rides on EVERY row, including the ones that pass ─────────

def test_a_passing_row_still_carries_a_suggestion():
    """THE REPORTED PROBLEM. On a row that passes, the reviewer wrote praise —
    "The draft clearly states the overarching objective" — which tells the author
    nothing and makes "Addressed" read as "you are done here". Every row must
    carry one concrete thing that would strengthen it, pass or fail."""
    from services import draft_review as dr

    req = {"id": "r1", "section": "project_summary", "label": "The Overview describes "
           "the objectives and the methods", "source": "S", "kind": "semantic", "scored": True}
    span = {"text": "The objectives are to synthesize antifouling hydrogels and validate prototypes."}

    def fake(prompt, **kw):
        assert "suggestion" in prompt, "the reviewer was never asked for a suggestion"
        return {"findings": [{"id": "r1", "status": "addressed",
                              "note": "The draft states the objectives.",
                              "evidence": "The objectives are to synthesize antifouling hydrogels",
                              "suggestion": "Add the expected outcome, not only the activity."}]}

    orig = dr.gemini_client.generate_json
    dr.gemini_client.generate_json = fake
    try:
        out = dr._review_batch("project_summary", span, [req],
                               {"project_summary": {"label": "Project Summary"}}, "X")
    finally:
        dr.gemini_client.generate_json = orig

    assert out[0]["status"] == "addressed"
    assert out[0]["suggestion"] == "Add the expected outcome, not only the activity."


def test_a_missing_suggestion_is_empty_not_invented():
    """If the model omits it, the field is empty and the UI shows nothing. A
    fabricated suggestion would be advice about a draft nobody read."""
    from services import draft_review as dr

    req = {"id": "r1", "section": "s", "label": "L", "source": "S",
           "kind": "semantic", "scored": True}

    def fake(prompt, **kw):
        return {"findings": [{"id": "r1", "status": "not_found", "note": "Missing."}]}

    orig = dr.gemini_client.generate_json
    dr.gemini_client.generate_json = fake
    try:
        out = dr._review_batch("s", {"text": "text"}, [req], {"s": {"label": "S"}}, "X")
    finally:
        dr.gemini_client.generate_json = orig
    assert out[0]["suggestion"] == ""


def test_the_reviewer_is_forbidden_to_write_the_prose():
    """Golden rule 1, and the boundary that keeps this from re-adding the
    Drafting Coach that was deleted by product decision. A suggestion says WHAT
    to add; it never hands the author sentences about their own science."""
    from services import draft_review as dr
    system = dr._review_system("NSF 23-598")
    assert "never write" in system.lower() or "never rewrite" in system.lower()


# ── end to end through review_section ───────────────────────────────────────

def test_review_section_returns_the_length_line_and_the_plan():
    """The reported case, end to end: a 76-word Project Summary. The PAPPG's
    own one-page rule supplies the allowance, so the two can never disagree."""
    from services import draft_review as dr

    text = ("Overview\nThis project develops adaptive zwitterionic polymer networks "
            "for continuous salinity sensing. The objectives are to synthesize "
            "antifouling hydrogels and validate prototypes.\n\n"
            "Intellectual Merit\nThe work advances understanding of ion partitioning.\n\n"
            "Broader Impacts\nThe project trains students at Morgan State University.")
    out = dr.review_section(text, section="project_summary", rulebook="the PAPPG",
                            use_ai=False)
    length = out["guidance"]["length"]
    assert length is not None, "the one-page rule should supply an allowance"
    assert length["allowance"] == 550
    assert length["pct"] < 60
    assert "no minimum length" in length["message"]


def test_the_allowance_comes_from_the_rule_that_enforces_it():
    """Read off RULES, never typed twice. If the PAPPG's Project Summary limit
    ever changes, the allowance moves with the check rather than drifting."""
    from services import draft_review as dr
    from services import rulebook_baseline as rb

    rows = rb.rules_for("the PAPPG", "project_summary")
    assert dr._section_page_limit(rows, "project_summary") == 1.0


def test_a_section_with_no_page_rule_gets_no_length_line():
    from services import draft_review as dr
    from services import rulebook_baseline as rb

    rows = rb.rules_for("the PAPPG", "references_cited")
    assert dr._section_page_limit(rows, "references_cited") is None
