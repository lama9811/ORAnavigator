"""A section can meet every rule and still be full of errors. Say so.

WHAT WENT WRONG
---------------
Measured on a running backend, 2026-08-26, with `8-project-summary-TYPOS.txt`:

    Rules met      5 of 5   (100%)
    mistakes                    5     doubled word, 2 misspellings, wrong word,
                                      a sentence with no terminal punctuation
    wording                    10     spelling, grammar, punctuation

**Fifteen problems found, and the headline said 100%.** Every one of them was
already detected -- `mechanical_checks`, `language_slips` and the model
proofreader all ran and all reported. They are deliberately outside the score,
on the reasoning that a typo is not incompleteness against a solicitation. That
reasoning is correct and the screen it produced was wrong: a PI reading 100%
concludes the section is done.

THE DESIGN, and the line it will not cross
------------------------------------------
An ERROR is verifiable -- a doubled word is in the text or it is not. A WEIGHT
is an opinion: blending fifteen errors and five rules into one percentage means
deciding a typo is worth some fraction of a missing Broader Impacts statement,
and no fraction is defensible. So nothing is blended. Two counts, both measured,
and a VERDICT that is allowed to read both -- which is the one thing the old
screen could not do, because it only ever knew one of them.

The verdict is authored HERE, in the backend, for the same reason `score()`
authors its own `basis`: a caption that lives in a modal is a caption the other
modal can render without.
"""

import pytest

from services import draft_review


def _issues(n):
    return [{"label": "x", "quote": "y"} for _ in range(n)]


def _score(percent, assessed=5):
    return {"percent": percent, "assessed": assessed,
            "earned": assessed * percent / 100.0}


# ── the three levels ───────────────────────────────────────────────────────

def test_every_rule_met_and_no_errors_is_the_top_level():
    v = draft_review.verdict(_score(100), mistakes=[], wording=[])
    assert v["level"] == "clean"


def test_every_rule_met_but_errors_found_is_not_the_top_level():
    """THE CASE THIS EXISTS FOR. 100% of the rules, fifteen problems."""
    v = draft_review.verdict(_score(100), mistakes=_issues(5), wording=_issues(10))
    assert v["level"] == "needs_work", v
    assert v["issues"]["total"] == 15
    assert v["issues"]["mistakes"] == 5 and v["issues"]["wording"] == 10


def test_a_few_errors_is_its_own_level():
    """Three typos is not the same news as fifteen, and flattening them into
    one bucket is how a verdict stops being read."""
    assert draft_review.verdict(
        _score(100), mistakes=_issues(1), wording=_issues(2))["level"] == "minor"


def test_a_missing_rule_outranks_a_clean_proofread():
    """A section with no Broader Impacts statement is not 'minor' because its
    spelling is perfect. Rules are the floor; errors are on top of it."""
    v = draft_review.verdict(_score(60), mistakes=[], wording=[])
    assert v["level"] == "needs_work", v


# ── what it is forbidden to say ────────────────────────────────────────────

def test_the_top_level_does_not_tell_a_PI_the_section_is_finished():
    """FOURTH TIME THIS REPO HAS RENDERED PRESENCE AS APPROVAL — green ticks on
    the section map, `addressed` reading as praise, a 2.5rem 100%. The rules
    are NSF's floor and they check presence, so "no problems found" is the
    strongest true statement available. "Ready to submit" is not ours to make.
    """
    text = " ".join(str(v) for v in
                    draft_review.verdict(_score(100), mistakes=[], wording=[]).values())
    for banned in ("ready to submit", "ready for submission", "approved",
                   "excellent", "good to go", "you're done", "complete"):
        assert banned not in text.lower(), banned


def test_the_summary_states_both_counts_so_neither_can_be_read_alone():
    v = draft_review.verdict(_score(100), mistakes=_issues(5), wording=_issues(10))
    assert "5 of 5" in v["summary"], v["summary"]
    assert "15" in v["summary"], v["summary"]


# ── degradation ────────────────────────────────────────────────────────────

def test_with_no_score_the_verdict_reports_only_what_was_measured():
    """`score` is None when the AI reviewer is down and nothing scoreable was
    assessed. Reporting "clean" there would claim a rules check that never ran —
    the same lie as a completeness percentage computed with the semantic half
    missing, which is why `review_draft` withholds the number outright.
    """
    v = draft_review.verdict(None, mistakes=_issues(2), wording=[])
    assert v["level"] == "minor"
    assert "not" in v["summary"].lower() and "rule" in v["summary"].lower(), v["summary"]


def test_no_score_and_no_errors_is_not_reported_as_clean():
    v = draft_review.verdict(None, mistakes=[], wording=[])
    assert v["level"] != "clean", v


# ── wiring ─────────────────────────────────────────────────────────────────

def test_a_section_check_returns_the_verdict():
    from services import solicitation_profile as sp
    profile = sp.build_generic({}, [
        {"id": "sol_ps", "section": "project_summary",
         "section_label": "Project Summary", "label": "Include the LOI number",
         "kind": "semantic", "scored": True,
         "source": "The Project Summary must include the LOI number.",
         "why": "", "keywords": []}], id="NSF 23-598", title="t")
    result = draft_review.review_section(
        "Overview\nWe we study X.\n\nIntellectual Merit\nY.\n\nBroader Impacts\nZ.\n",
        section="project_summary", rulebook="the PAPPG", profile=profile,
        use_ai=False)
    assert "verdict" in result, sorted(result)
    assert result["verdict"]["issues"]["total"] >= 1, result["verdict"]
