"""A Section Check whose semantic half never ran must not report a score.

MEASURED, NOT HYPOTHETICAL (2026-08-28). Ten uploads of the awarded NSF EiR
Project Summary through the real endpoint: seven runs reached the model and
scored 93%/"Needs work"; three runs lost the model call and reported

    score 100%, band green, verdict "No problems found"

on the SAME file. Five of the seven rules came back `unclear`, `unclear` is
absent from `_CREDIT`, so the denominator collapsed to the two deterministic
rules -- both of which passed -- and the outage rendered as a perfect draft.

`verdict()` already refuses to call anything "clean" without a score, and
`review_draft` already withholds the number when the AI layer is down for
exactly this reason ("a percentage computed with the semantic half missing
reads as a verdict on the draft when it's a verdict on our availability").
`review_section` was the one entry point that computed it anyway.

The discrimination that matters: ONE row the model skipped is not an outage --
it is one unassessed rule among many assessed ones, and the score is still the
honest share of what was checked. The outage is when NO semantic row came back.
"""
import pytest

from services import draft_review


PROFILE = None
SECTION = "project_summary"
RULEBOOK = "the PAPPG"

DRAFT = """Overview
This project studies reduction superalgebras. The objectives are to classify
decomposition bases and to train four undergraduates in algebraic methods.

Intellectual Merit
The work advances knowledge of supermathematics and representation theory by
mapping U(osp(1|2n)) into Clifford-Weyl superalgebras.

Broader Impacts
Students at an HBCU will be mentored, present at conferences, and take part in
active-learning initiatives across the department.
"""


def test_the_score_is_withheld_when_the_semantic_half_never_ran(monkeypatch):
    """No semantic row came back -> no number, and a message saying why."""
    # conftest already pins get_client() -> None, so the AI layer is down.
    res = draft_review.review_section(DRAFT, section=SECTION, rulebook=RULEBOOK,
                                      profile=PROFILE, pages=1)
    sem = [f for f in res["findings"] if f["status"] == "unclear"]
    assert sem, "this section must have semantic rules for the test to mean anything"

    assert res["score"] is None, (
        "the semantic half did not run, so a percentage computed from the "
        "deterministic rules alone is a verdict on our availability")
    assert res.get("message"), "the PI must be told the reviewer did not run"
    assert res["verdict"]["level"] != "clean"
    assert "No problems found" not in res["verdict"]["summary"]


def test_use_ai_false_is_deliberate_and_still_scores(monkeypatch):
    """A caller who ASKED for rule-based checks only still gets its number.

    Deliberately NOT the same as the outage above, and the difference is who
    chose it. `use_ai=False` is a caller saying "run the rules, skip the
    judgement"; it is scored on them by an explicit product decision
    (2026-08-20, "by request"), no endpoint passes it, and its message already
    says the judgement rules were not assessed. The outage is the case nobody
    chose -- the reviewer was asked for and never answered -- and there the
    number would describe our availability rather than the draft.
    """
    res = draft_review.review_section(DRAFT, section=SECTION, rulebook=RULEBOOK,
                                      profile=PROFILE, pages=1, use_ai=False)
    assert res["score"] is not None
    assert res.get("message"), "it must still say the judgement rules were skipped"


def test_one_skipped_row_is_not_an_outage_and_still_scores(monkeypatch):
    """A single `unclear` row must NOT empty the score.

    The model omitting one requirement is a fact about the model, not an
    outage -- the other rows were genuinely assessed and the share of them
    that passed is an honest number. Withholding it here would throw away a
    real answer, which is the opposite failure.
    """
    real = draft_review._review_section

    def _one_short(section_key, span, reqs, sections, solicitation_id, votes=1):
        out = real(section_key, span, reqs, sections, solicitation_id, votes)
        # Everything assessed except the first row, which the model "skipped".
        for i, f in enumerate(out):
            f["status"] = "unclear" if i == 0 else "addressed"
            f["source"] = "ai"
        return out

    monkeypatch.setattr(draft_review, "_review_section", _one_short)
    res = draft_review.review_section(DRAFT, section=SECTION, rulebook=RULEBOOK,
                                      profile=PROFILE, pages=1)
    assert res["score"] is not None, (
        "one skipped row is not an outage -- the rest were assessed")
    assert res["score"]["assessed"] >= 1
