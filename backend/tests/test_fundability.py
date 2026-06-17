"""Unit tests for Phase 3: eligibility go/no-go (deterministic) and the
fundability reviewer lens (AI off in tests -> deterministic fallback + the
evidence-grounding helper)."""
from services import eligibility as elig
from services import fundability as fund


# ── eligibility (fully deterministic) ──────────────────────────────────────

def test_eligibility_all_yes_is_go():
    r = elig.assess_eligibility({
        "appointment_ok": "yes", "org_eligible": "yes",
        "within_limits": "yes", "limited_submission": "no",
    })
    assert r["overall"] == "go"
    assert all(i["status"] == "ok" for i in r["items"])


def test_eligibility_hard_no_is_stop():
    r = elig.assess_eligibility({"appointment_ok": "no", "org_eligible": "yes",
                                 "within_limits": "yes", "limited_submission": "no"})
    assert r["overall"] == "stop"
    assert any(i["id"] == "appointment_ok" and i["status"] == "stop" for i in r["items"])


def test_eligibility_unsure_is_caution():
    r = elig.assess_eligibility({"appointment_ok": "yes", "org_eligible": "yes",
                                 "within_limits": "unsure", "limited_submission": "no"})
    assert r["overall"] == "caution"


def test_limited_submission_yes_is_coordinate_not_stop():
    r = elig.assess_eligibility({"appointment_ok": "yes", "org_eligible": "yes",
                                 "within_limits": "yes", "limited_submission": "yes"})
    assert r["overall"] == "caution"
    item = next(i for i in r["items"] if i["id"] == "limited_submission")
    assert item["status"] == "coordinate" and "ORA" in item["message"]


def test_eligibility_surfaces_solicitation_text():
    r = elig.assess_eligibility({}, eligibility_text="Only tenure-track faculty may apply.")
    assert r["eligibility_text"] == "Only tenure-track faculty may apply."
    # No answers -> everything is a check -> caution.
    assert r["overall"] == "caution"


# ── fundability criteria sets ──────────────────────────────────────────────

def test_review_criteria_by_sponsor():
    nsf = [c["key"] for c in fund.review_criteria("NSF")]
    assert "intellectual_merit" in nsf and "broader_impacts" in nsf
    nih = [c["key"] for c in fund.review_criteria("NIH")]
    assert "significance" in nih and "approach" in nih
    generic = [c["key"] for c in fund.review_criteria("Foundation X")]
    assert generic and "approach" in generic


# ── fundability reviewer assessment (AI off -> fallback) ───────────────────

def test_reviewer_assessment_empty_draft():
    r = fund.reviewer_assessment("NSF", "")
    assert r["criteria"] == [] and r["ai"] is False


def test_reviewer_assessment_fallback_lists_criteria():
    r = fund.reviewer_assessment("NSF", "Some draft text about the project.")
    assert r["ai"] is False
    keys = [c["key"] for c in r["criteria"]]
    assert "intellectual_merit" in keys and "broader_impacts" in keys


def test_reviewer_verify_demotes_unquotable_claims():
    results = [
        {"key": "a", "label": "A", "rating": "strong", "evidence": "not in draft", "comment": "", "fix": ""},
        {"key": "b", "label": "B", "rating": "adequate", "evidence": "a real quote", "comment": "", "fix": ""},
        {"key": "c", "label": "C", "rating": "weak", "evidence": "", "comment": "", "fix": ""},
    ]
    out = fund._verify(results, "the draft has a real quote inside it")
    assert out[0]["rating"] == "unclear" and out[0]["evidence"] == ""
    assert out[1]["rating"] == "adequate"
    assert out[2]["rating"] == "weak"
