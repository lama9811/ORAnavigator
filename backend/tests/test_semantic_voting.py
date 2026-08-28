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
