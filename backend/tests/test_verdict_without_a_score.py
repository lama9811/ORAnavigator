"""A section with no score must not claim nothing was checked.

REPORTED BY THE PI, 2026-08-28, from a References Cited check. The panel read:

    The rules were not checked for this section, and the writing has 2 problems.

with an ADDRESSED group open directly beneath it. Both cannot be true, and the
group was the honest one -- a rule HAD been checked and had passed.

WHY IT HAPPENS. References Cited holds exactly two rules and only ONE of them is
scoreable: "Citations follow accepted scholarly practice" (model-judged, scored)
and "Avoid 'et al.'" (code-decided, advisory, deliberately unscored). When the
single scoreable rule comes back unassessed there is nothing left to count, so
`score()` withholds the number -- correctly -- and `verdict()` then described the
whole section as unchecked, because it had never been shown the findings.

Two different situations were collapsed into one sentence, and they need
different advice:

  * a scored rule EXISTS but did not come back -- transient, and re-running
    usually fixes it;
  * the section has NO scored rules at all -- permanent, and re-running changes
    nothing.

Same failure family as everything else cleared out of this modal: a summary
line contradicting the rows underneath it.
"""

from services import draft_review as dr


def _f(rid, status, *, scored=True):
    return {"id": rid, "label": rid, "status": status, "scored": scored,
            "note": "", "evidence": "", "source": "ai"}


def _summary(findings, mistakes=(), wording=()):
    return dr.verdict(None, mistakes=list(mistakes), wording=list(wording),
                      findings=findings)["summary"]


def test_a_passing_advisory_rule_is_not_described_as_unchecked():
    """The exact screen the PI reported: one advisory rule passed, one scored
    rule did not return, two wording problems."""
    findings = [_f("citations", "unclear"),
                _f("et_al", "clear", scored=False)]
    s = _summary(findings, wording=["a", "b"])
    assert "rules were not checked" not in s.lower(), s
    assert "advisory" in s.lower(), s
    assert "2 problems" in s or "2 writing problems" in s, s


def test_a_scored_rule_that_did_not_return_says_to_try_again():
    """Transient. Re-running is the actual remedy and the sentence should say so."""
    s = _summary([_f("citations", "unclear")])
    assert "again" in s.lower(), s


def test_a_section_with_no_scored_rules_does_not_promise_a_retry():
    """Permanent. Telling someone to re-run a section that can never produce a
    score sends them round a loop that cannot end."""
    s = _summary([_f("et_al", "clear", scored=False)])
    assert "again" not in s.lower(), s
    assert "no scored rules" in s.lower(), s


def test_nothing_evaluated_at_all_keeps_the_original_wording():
    """With no findings there is genuinely nothing to report, and the old
    sentence was right for that case."""
    s = _summary([])
    assert "not checked" in s.lower(), s


def test_the_findings_argument_is_optional():
    """Callers that do not pass findings must behave exactly as before."""
    before = dr.verdict(None, mistakes=[], wording=["x"])
    after = dr.verdict(None, mistakes=[], wording=["x"], findings=None)
    assert before == after, (before, after)


def test_a_real_score_is_untouched_by_this():
    """Only the no-score branch changes."""
    block = {"assessed": 6, "earned": 6.0, "percent": 100}
    v = dr.verdict(block, mistakes=[], wording=[], findings=[_f("a", "addressed")])
    assert v["summary"].startswith("Every rule was met (6 of 6)"), v


def test_an_advisory_rule_that_failed_is_not_reported_as_passing():
    """Only PASSING advisory rows earn the mention. A flagged advisory row is
    not reassurance."""
    findings = [_f("citations", "unclear"),
                _f("et_al", "flagged", scored=False)]
    s = _summary(findings)
    assert "passed" not in s.lower(), s


# ── the wiring ─────────────────────────────────────────────────────────────

def test_review_section_actually_passes_its_findings_to_the_verdict():
    """Without this the fix above is dead code -- mutation-testing showed
    removing `findings=` at the call site broke nothing. References Cited is the
    real shape: its one scoreable rule is model-judged (so `unclear` with the AI
    off, leaving no score) while its `et al.` check is deterministic, advisory,
    and passes.
    """
    from services import draft_review, solicitation_profile as sp
    profile = sp.build_generic({}, [], id="NSF 23-598", title="t")
    text = ("References Cited\n\n"
            "[1] F. A. Berezin and V. N. Tolstoy. The Group with Grassmann "
            "Structure. Communications in Mathematical Physics, 1981.\n")
    result = draft_review.review_section(
        text, section="references_cited", rulebook="the PAPPG",
        profile=profile, use_ai=False)
    assert result.get("score") is None, result.get("score")
    summary = result["verdict"]["summary"]
    assert "rules were not checked" not in summary.lower(), summary
    assert "advisory" in summary.lower(), summary
