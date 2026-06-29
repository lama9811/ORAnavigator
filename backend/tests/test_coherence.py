"""Unit tests for the cross-section coherence check.

The conftest autouse fixture forces Gemini OFFLINE, so these exercise the
deterministic paths: candidate-pair selection, the ready/not-ready gates, the
offline fallback (incl. the aims<->strategy keyword pass), and the shared
whitespace-collapse grounding helper that drops unverifiable 'aligned' quotes.
"""
from services import section_coach as sc


# ── gates ───────────────────────────────────────────────────────────────────

def test_needs_two_sections():
    r = sc.coherence_check("NIH", {"specific_aims": "Aim 1: do X."})
    assert r["ready"] is False and r["pairs"] == []


def test_no_applicable_checks_is_not_ready():
    # Two saved sections, but no pair we know how to cross-check.
    r = sc.coherence_check("NSF", {"broader_impacts": "We will broaden participation.",
                                   "data_management_plan": "Data shared via repo."})
    assert r["ready"] is False


# ── candidate pairs ──────────────────────────────────────────────────────────

def test_aims_strategy_pair_is_built():
    checks = sc._coherence_candidate_pairs(
        {"specific_aims": "Aim 1: A. Aim 2: B.", "research_strategy": "Approach for Aim 1..."},
        None, None)
    ids = {c["id"] for c in checks}
    assert "aims_strategy" in ids


def test_scope_eligibility_pair_uses_context():
    checks = sc._coherence_candidate_pairs(
        {"project_summary": "Summary.", "project_description": "We serve K-12 schools."},
        {"eligibility": "Open only to minority-serving institutions."}, None)
    ids = {c["id"] for c in checks}
    assert "scope_eligibility" in ids and "summary_description" in ids


def test_timeline_staffing_pair_needs_budget():
    drafts = {"project_summary": "S", "project_description": "Three-year plan."}
    no_budget = {c["id"] for c in sc._coherence_candidate_pairs(drafts, None, None)}
    assert "timeline_staffing" not in no_budget
    budget = {"multi_year": {"project_years": 3}, "personnel": [{"name": "Dr. X"}]}
    with_budget = {c["id"] for c in sc._coherence_candidate_pairs(drafts, None, budget)}
    assert "timeline_staffing" in with_budget


# ── offline fallback ─────────────────────────────────────────────────────────

def test_offline_fallback_flags_missing_aim():
    r = sc.coherence_check(
        "NIH",
        {"specific_aims": "Aim 1: do X. Aim 2: do Y. Aim 3: do Z.",
         "research_strategy": "We describe the approach for Aim 1 and Aim 2 in detail."},
    )
    assert r["ai"] is False and r["ready"] is True
    pair = next(p for p in r["pairs"] if p["a"] == "Specific Aims")
    assert pair["status"] == "gap"
    assert "Aim 3" in pair["note"]


def test_offline_fallback_no_quotes_fabricated():
    r = sc.coherence_check(
        "NIH",
        {"specific_aims": "Aim 1: do X.", "research_strategy": "Approach for Aim 1."},
    )
    for p in r["pairs"]:
        assert p["evidence_a"] == "" and p["evidence_b"] == ""


# ── grounding helper (shared with _verify_evidence) ──────────────────────────

def test_quote_in_collapses_whitespace():
    text = "the work links environmental,\nhealth, and social data"
    assert sc._quote_in(text, "links environmental, health, and social data")
    assert not sc._quote_in(text, "a sentence that is not present")
    assert not sc._quote_in(text, "")
