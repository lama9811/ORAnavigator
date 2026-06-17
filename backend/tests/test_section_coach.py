"""Unit tests for the Section Drafting Coach (Phase 2).

The conftest autouse fixture forces Gemini OFFLINE, so these exercise the
deterministic paths: section catalog, outline skeleton, the keyword-based review
fallback, and the anti-hallucination evidence check. The AI path is layered on
top of these and falls back to them when the LLM is unavailable."""
from services import section_coach as sc


# ── catalog ────────────────────────────────────────────────────────────────

def test_available_sections_by_sponsor():
    nsf = [s["key"] for s in sc.available_sections("NSF")]
    assert "project_summary" in nsf and "broader_impacts" in nsf
    nih = [s["key"] for s in sc.available_sections("NIH")]
    assert "specific_aims" in nih and "research_strategy" in nih
    # Unknown sponsor falls back to the generic set.
    assert [s["key"] for s in sc.available_sections("Some Foundation")] == \
        ["abstract", "narrative", "data_management_plan"]


# ── outline ──────────────────────────────────────────────────────────────--

def test_outline_returns_deterministic_structure():
    o = sc.outline_section("NSF", "project_summary")  # no topic, AI off -> ai False
    assert o["mode"] == "outline" and o["ai"] is False
    headings = [x["heading"] for x in o["outline"]]
    assert "A labeled 'Intellectual Merit' statement" in headings
    assert "A labeled 'Broader Impacts' statement" in headings
    assert o["pitfalls"] and o["target_words"] and o["purpose"]


def test_outline_unknown_section_is_none():
    assert sc.outline_section("NSF", "nope") is None


# ── review (deterministic keyword fallback, AI off) ────────────────────────

def test_review_flags_missing_elements():
    draft = ("Overview: this project studies X. Intellectual Merit: it advances "
             "theory in the field with rigorous methods.")
    r = sc.review_section("NSF", "project_summary", draft)
    assert r["ai"] is False
    statuses = {c["item"]: c["status"] for c in r["checklist"]}
    # Overview + Intellectual Merit present; Broader Impacts missing -> unclear.
    assert statuses["A labeled 'Broader Impacts' statement"] == "unclear"
    assert any("Broader Impacts" in s for s in r["suggestions"])
    assert r["word_count"] > 0


def test_review_all_covered_when_keywords_present():
    draft = ("Overview of the work. Intellectual Merit: advances the field. "
             "Broader Impacts: trains underrepresented students and broadens participation.")
    r = sc.review_section("NSF", "project_summary", draft)
    assert all(c["status"] == "covered" for c in r["checklist"])
    assert r["suggestions"] == []


def test_review_empty_draft_prompts_for_text():
    r = sc.review_section("NSF", "project_summary", "")
    assert r["word_count"] == 0 and r["checklist"] == []


def test_review_unknown_section_is_none():
    assert sc.review_section("NSF", "nope", "text") is None


# ── anti-hallucination: drop 'covered' claims not quotable in the draft ─────

def test_verify_evidence_demotes_unquotable_covered_claims():
    checklist = [
        {"item": "X", "status": "covered", "evidence": "phrase not in the draft", "note": ""},
        {"item": "Y", "status": "covered", "evidence": "a real quote", "note": ""},
        {"item": "Z", "status": "missing", "evidence": "", "note": "absent"},
    ]
    out = sc._verify_evidence(checklist, "the draft includes a real quote right here")
    assert out[0]["status"] == "unclear" and out[0]["evidence"] == ""   # unquotable -> demoted
    assert out[1]["status"] == "covered"                                # quotable -> kept
    assert out[2]["status"] == "missing"                                # untouched
