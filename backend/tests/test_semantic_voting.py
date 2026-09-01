"""The same section, checked twice, must not give two different answers.

REPORTED BY THE PI, 2026-08-28: "i check same paragraph and different time it
gave me different results."

MEASURED before the fix, on a real awarded Project Summary, five identical
uploads: four runs scored 100% and one scored 92%. Five of the six rules were
stable every time; exactly one moved -- "The Overview describes the objectives
and the methods" came back `addressed` four times and `partial` once. A weak
47-word summary was stable across all five runs.

So this is not noise everywhere. Rules decided by CODE never move, and a
clear-cut judgement does not move either. What moves is a genuinely BORDERLINE
call: that Overview names the problem and the objectives but not the methods,
which sits on the line between two statuses, and the model lands on either side
of it. Temperature is already 0, thinking is already capped, and CLAUDE.md
records two measured attempts to fix this class by prompt tuning -- one of which
cost two thirds of the recall on real errors and was reverted.

THE FIX IS TO ASK MORE THAN ONCE. The semantic batch runs `SEMANTIC_VOTES`
times concurrently and each requirement takes the MEDIAN of its votes by credit.
The median is one rule that handles both cases: with a 2-1 majority the median
IS the majority, and with three different answers it is the middle one rather
than whichever arrived first.

The finding RETURNED is the whole row from the run that produced the winning
status -- note, evidence and suggestion together -- never a synthesised row.
A note explaining `partial` under a status of `addressed` would be a new kind
of wrong.

SCOPED TO CHECK A SECTION, deliberately. That is the entry point a PI re-runs
on one paragraph, where the inconsistency is visible and infuriating. A full
Draft Review spans ~14 sections, and tripling its model calls is the 429 storm
the section fan-out cap already exists to prevent. `review_draft` keeps one call
per batch, and there is a test.
"""

from services import draft_review as dr


def _finding(rid, status, note):
    return {"id": rid, "status": status, "note": note, "evidence": "q",
            "suggestion": "s", "source": "ai", "scored": True}


# ── the merge rule ─────────────────────────────────────────────────────────

def test_a_two_to_one_majority_wins():
    """A resolve-DOWN rule replaced this for one afternoon and was reverted when
    it measured 4 distinct scores against the median's 2 -- see merge_votes."""
    votes = [[_finding("r", "addressed", "A")],
             [_finding("r", "addressed", "B")],
             [_finding("r", "partial", "C")]]
    out = dr.merge_votes(votes)
    assert [f["status"] for f in out] == ["addressed"], out


def test_three_different_answers_take_the_middle_one():
    """Not whichever arrived first, and not the most generous."""
    votes = [[_finding("r", "addressed", "A")],
             [_finding("r", "not_found", "B")],
             [_finding("r", "partial", "C")]]
    out = dr.merge_votes(votes)
    assert [f["status"] for f in out] == ["partial"], out


def test_a_prohibition_takes_its_majority_too():
    """UNCHANGED by the resolve-down rule, deliberately -- `flagged` is a
    positive accusation needing a quote, so it must not win on one vote."""
    votes = [[_finding("p", "clear", "A")],
             [_finding("p", "flagged", "B")],
             [_finding("p", "clear", "C")]]
    out = dr.merge_votes(votes)
    assert [f["status"] for f in out] == ["clear"], out


def test_the_winning_row_is_returned_whole():
    """Its note must belong to its status. A note arguing `partial` printed
    under `addressed` would be a new kind of wrong."""
    votes = [[_finding("r", "partial", "the methods are not named")],
             [_finding("r", "partial", "no methods stated")],
             [_finding("r", "addressed", "objectives and methods are both there")]]
    out = dr.merge_votes(votes)
    assert out[0]["status"] == "partial"
    assert "methods" in out[0]["note"] and "both there" not in out[0]["note"], out[0]


def test_a_run_that_could_not_assess_does_not_outvote_two_that_could():
    """`unclear` means nobody looked, so it is not an opinion to count."""
    votes = [[_finding("r", "unclear", "no result")],
             [_finding("r", "addressed", "A")],
             [_finding("r", "addressed", "B")]]
    out = dr.merge_votes(votes)
    assert [f["status"] for f in out] == ["addressed"], out


def test_when_every_run_failed_the_row_stays_unclear():
    votes = [[_finding("r", "unclear", "no result")]] * 3
    out = dr.merge_votes(votes)
    assert [f["status"] for f in out] == ["unclear"], out


def test_a_single_vote_is_returned_untouched():
    """votes=1 must be byte-identical to the old behaviour."""
    one = [_finding("r", "partial", "A")]
    assert dr.merge_votes([one]) == one


def test_rows_missing_from_some_runs_are_still_returned():
    """A run that omitted a row must not delete it from the result."""
    votes = [[_finding("a", "addressed", "A"), _finding("b", "partial", "B")],
             [_finding("a", "addressed", "A2")],
             [_finding("a", "addressed", "A3"), _finding("b", "partial", "B3")]]
    out = {f["id"]: f["status"] for f in dr.merge_votes(votes)}
    assert out == {"a": "addressed", "b": "partial"}, out


# ── the wiring ─────────────────────────────────────────────────────────────

def test_check_a_section_asks_more_than_once(monkeypatch):
    calls = []

    def fake_batch(section_key, span, reqs, sections, solicitation_id):
        calls.append(1)
        # third run disagrees, exactly the shape measured on the real draft
        status = "partial" if len(calls) == 3 else "addressed"
        return [dr._finding(r, status, "n", "q", source="ai") for r in reqs]

    monkeypatch.setattr(dr, "_review_batch", fake_batch)
    out = dr._review_section("project_summary",
                             {"text": "Overview\nWe will do the work.", "marker": "x",
                              "start": 0},
                             [{"id": "r", "label": "l", "kind": "semantic",
                               "scored": True, "source": "s", "why": "",
                               "keywords": []}],
                             {"project_summary": {"label": "Project Summary"}},
                             "NSF 23-598", votes=3)
    assert len(calls) == 3, calls
    assert [f["status"] for f in out] == ["addressed"], out


def test_a_full_draft_review_still_makes_one_call_per_batch(monkeypatch):
    """Tripling ~14 sections is the 429 storm the fan-out cap exists to stop."""
    calls = []

    def fake_batch(section_key, span, reqs, sections, solicitation_id):
        calls.append(1)
        return [dr._finding(r, "addressed", "n", "q", source="ai") for r in reqs]

    monkeypatch.setattr(dr, "_review_batch", fake_batch)
    dr._review_section("project_summary",
                       {"text": "Overview\nWe will do the work.", "marker": "x",
                        "start": 0},
                       [{"id": "r", "label": "l", "kind": "semantic",
                         "scored": True, "source": "s", "why": "", "keywords": []}],
                       {"project_summary": {"label": "Project Summary"}},
                       "NSF 23-598")
    assert len(calls) == 1, calls


# ── BORDERLINE: saying the votes disagreed, rather than hiding it ────────────
#
# Reported by a PI who ran the SAME Project Summary PDF twice and got two
# different answers. Measured 2026-08-31, 12 runs of that identical file: six of
# the seven rules returned the same status EVERY run, and one --
# `pappg_ps_overview_methods`, "The Overview states the methods to be employed"
# -- came back `not_found` 6, `partial` 5, `addressed` 1. That single rule is
# the whole 86%/93%/100% score range.
#
# It is not noise: that Overview states what the research addresses and never
# names a method, so the rule genuinely sits on the line. Forcing a stable
# answer would not make it right, only CONFIDENT -- and the PI would lose the
# one signal that says "this is a close call, go and look". So the median still
# decides the status and the score is untouched; the disagreement is REPORTED.

def test_a_row_the_votes_disagreed_on_is_marked_borderline():
    votes = [[_finding("r", "not_found", "A")],
             [_finding("r", "partial", "B")],
             [_finding("r", "not_found", "C")]]
    out = dr.merge_votes(votes)
    assert out[0]["status"] == "not_found"      # the median is unchanged
    assert out[0]["borderline"] is True


def test_a_row_the_votes_agreed_on_is_not_marked_borderline():
    """The guard that keeps the mark meaningful. If every row carried it the
    flag would say nothing, and this repo has already had to cut a caveat that
    appeared in four places at once."""
    votes = [[_finding("r", "addressed", "A")],
             [_finding("r", "addressed", "B")],
             [_finding("r", "addressed", "C")]]
    out = dr.merge_votes(votes)
    assert out[0].get("borderline") is False


def test_a_single_vote_is_never_borderline():
    """With one vote there is no disagreement to report -- absence of a second
    opinion is not the same fact as two opinions differing."""
    out = dr.merge_votes([[_finding("r", "partial", "A")]])
    assert out[0].get("borderline", False) is False


def test_unclear_rows_do_not_make_a_row_borderline():
    """`unclear` means a run returned nothing for that row. It is not an
    opinion, so it does not get a vote -- and it must not manufacture a
    disagreement with the runs that did answer."""
    votes = [[_finding("r", "addressed", "A")],
             [_finding("r", "unclear", "B")],
             [_finding("r", "addressed", "C")]]
    out = dr.merge_votes(votes)
    assert out[0]["status"] == "addressed"
    assert out[0].get("borderline") is False


# ── A NOTE THAT ARRIVES BROKEN IS NOT SHOWN ─────────────────────────────────
#
# Measured 2026-08-31: 15 of 210 notes across 30 real uploads began mid-sentence
# -- " recorded explicitly in the Overview section.", " requirements are
# addressed in the Overview section.", "reach clear research and broader goals".
# Instrumented at the wire: the MODEL emits them that way, leading space and
# missing first word; our code only calls .strip(). Nearly all landed on one
# rule, and on a screen ORA staff read they simply look broken.
#
# The fix invents nothing. Several readers answer every row, so when the winning
# status has more than one row available, a well-formed note is preferred over a
# broken one -- the author's own alternative, already paid for.

def _f(rid, status, note):
    return {"id": rid, "status": status, "note": note, "evidence": "q",
            "suggestion": "s", "source": "ai", "scored": True}


def test_a_well_formed_note_is_preferred_over_a_broken_one():
    """The good note is deliberately LAST: the median picks the middle row, so a
    version of this test with it in the middle passes without the fix and proves
    nothing. It was written that way first and caught here."""
    votes = [[_f("r", "addressed", " recorded explicitly in the Overview.")],
             [_f("r", "addressed", " requirements are addressed here.")],
             [_f("r", "addressed", "The Overview states the objectives.")]]
    out = dr.merge_votes(votes)
    assert out[0]["status"] == "addressed"
    assert out[0]["note"] == "The Overview states the objectives."


def test_the_status_is_never_changed_to_get_a_better_note():
    """The safety property. Preferring a readable sentence must not promote a
    row -- only rows that already WON the vote are considered."""
    votes = [[_f("r", "not_found", " missing from the Overview.")],
             [_f("r", "not_found", " absent from the Overview.")],
             [_f("r", "addressed", "The Overview states the objectives clearly.")]]
    out = dr.merge_votes(votes)
    assert out[0]["status"] == "not_found"
    assert out[0]["note"].startswith(" ") or out[0]["note"][0].islower()


def test_a_broken_note_stands_when_every_reader_broke_it():
    """Last resort: keep what was said rather than fabricate a sentence about a
    draft. Rare by construction -- five readers now answer each row."""
    broken = [" recorded explicitly.", " requirements are met.",
              " noted in the Overview."]
    votes = [[_f("r", "addressed", n)] for n in broken]
    out = dr.merge_votes(votes)
    assert out[0]["status"] == "addressed"
    assert out[0]["note"] in broken, "a real note must survive, not an invented one"
