"""Three faults a PI found on one screen, all in how a PASSING row is presented.

Reported 2026-08-26 against a Project Summary that met all four of its checkable
rules. The screen said:

    Do this first
      Overview, Intellectual Merit and Broader Impacts each on their own line
        "Found “Overview”, “Intellectual Merit”, “Broader Impacts”, each on
         its own line."
      The Overview describes the objectives and the methods
        "Clarify the precise methods or timeline ..."
      ...
      Project Summary fits on one page   [prohibited]

Their question — "it says do this first but the overview line and other things
are there too" — is three separate defects:

1. A row with NOTHING TO SUGGEST was listed anyway. `priorities()` falls back to
   the row's `note` when `suggestion` is empty, and for a PASSING row the note
   is a confirmation of what was found, not advice. Its own docstring already
   forbids this ("A row with nothing concrete to say is DROPPED rather than
   listed"); the fallback defeated it for exactly the rows where it matters. The
   deterministic heading check is binary — it passed, and there is nothing to
   suggest.

2. "Do this first" over four met rules is a to-do framing on a section with
   nothing to do. Fourth cousin of the presence-rendered-as-approval family this
   modal keeps having to unship — this one is the opposite, approval rendered as
   failure.

3. `[prohibited]` on "Project Summary fits on one page". The rule is marked
   `flag_if_present` because the PAPPG words it as "File cannot exceed one
   page", which is defensible as a rule and nonsense as a tag: the label is
   phrased POSITIVELY, so the tag reads as "fitting on one page is prohibited".
   The tag and the label say opposite things.
"""

import pytest

from services import rulebook_baseline as rb
from services import section_guidance as sg


def _f(fid, status, *, note="", suggestion="", label="L", scored=True):
    return {"id": fid, "status": status, "note": note, "suggestion": suggestion,
            "label": label, "scored": scored}


# ── 1. a passing row with no suggestion is dropped ─────────────────────────

def test_a_passing_row_with_nothing_to_suggest_is_not_listed():
    """The exact row from the report: passed, no suggestion, note is a
    confirmation. Listing it puts "you already did this" in a to-do list."""
    out = sg.priorities([
        _f("pappg_ps_headings", "addressed",
           note="Found “Overview”, “Intellectual Merit”, “Broader Impacts”.")])
    assert out == [], out


def test_a_passing_row_with_a_real_suggestion_is_still_listed():
    """Rows 2 and 3 of the report were CORRECT and must survive: a met rule
    with one concrete way to strengthen it is the whole point of this list."""
    out = sg.priorities([
        _f("pappg_ps_overview", "addressed",
           note="The draft describes the objectives.",
           suggestion="Include a brief mention of the methodology.")])
    assert [p["text"] for p in out] == ["Include a brief mention of the methodology."]


def test_a_FAILING_row_may_still_fall_back_to_its_note():
    """Asymmetric on purpose. For a failure the note says what is MISSING, which
    is actionable even when phrased as a diagnosis; for a pass it says what was
    found, which is not."""
    out = sg.priorities([
        _f("x", "not_found", note="The text lacks any mention of a data plan.")])
    assert [p["text"] for p in out] == ["The text lacks any mention of a data plan."]


def test_a_failing_row_with_neither_is_still_dropped():
    assert sg.priorities([_f("x", "not_found")]) == []


# ── 2. the heading has to match what is in the list ────────────────────────

def test_the_heading_is_a_todo_only_when_something_actually_failed():
    failing = sg.priorities_heading([
        _f("a", "not_found", note="missing"),
        _f("b", "addressed", suggestion="tighten it")])
    assert "first" in failing.lower(), failing


def test_the_heading_does_not_say_do_this_first_when_every_rule_passed():
    passing = sg.priorities_heading([_f("b", "addressed", suggestion="tighten it")])
    assert "do this first" not in passing.lower(), passing
    assert "strengthen" in passing.lower(), passing


def test_the_heading_is_authored_in_the_backend_not_in_a_modal():
    """Same rule as `score()`'s `basis` and `verdict()`'s summary: a caption
    living in one modal is a caption the other renders without."""
    assert callable(getattr(sg, "priorities_heading", None))


# ── 3. the prohibition tag ─────────────────────────────────────────────────

def test_a_page_limit_rule_is_not_marked_a_prohibition():
    """`flag_if_present` buys nothing on a row a deterministic check decides —
    it only steers the semantic reviewer, which never sees this row — and it
    renders a [prohibited] tag that contradicts the rule's own label."""
    for row in rb.rules_for("the PAPPG"):
        if row.get("check") == "rb_page_limit":
            assert not row.get("flag_if_present"), row["id"]


def test_the_real_prohibitions_are_untouched():
    """The mirror risk. A CONTENT rule wrongly marked a prohibition makes a
    draft that omits it come back `clear` — a missing requirement reported as
    compliance. Narrowing the marking must not unmark a genuine one."""
    labels = {r["label"] for r in rb.rules_for("the PAPPG")
              if r.get("flag_if_present")}
    assert any("alcoholic beverages" in l for l in labels), sorted(labels)[:5]
    assert len(labels) >= 15, len(labels)
