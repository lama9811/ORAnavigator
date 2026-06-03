"""Tests for the ADVISORY AI review layer on Draft Critic.

Invariants under test:
  - When the AI returns data, `ai_review` is populated AND the deterministic
    verdict/checks/counts are byte-for-byte identical to the include_ai=False run.
  - When the AI fails / returns None / raises, `ai_review` is None and the
    deterministic output is unchanged (graceful fallback).
  - include_ai=False never calls Gemini.

We monkeypatch `_extract_pdf` to feed known text (no real PDF) and
`services.gemini_client.generate_json` to control the AI output.

Run: cd backend && ../.venv/bin/python -m pytest tests/test_draft_critic_ai.py -v
"""
import pytest

from services import draft_critic as dc
from services import gemini_client


_DRAFT_TEXT = """Project Summary
This proposal studies coral reef restoration.

Project Description
We will deploy sensors and analyze data over three years.

References Cited
[1] Smith et al.

Biographical Sketch
Dr. Pat Investigator.

Budget Justification
Personnel and equipment totaling $400,000.
"""

_SOL = {
    "budget_cap": 500000,
    "page_limits": {"project_description": 15},
    "required_attachments": ["Biographical Sketch", "Project Description"],
}

# Note: the "partial" finding carries a VERBATIM quote from _DRAFT_TEXT so the
# evidence verifier keeps it; the "missing" finding needs no quote.
_VALID_AI = {
    "summary": "The narrative is on-topic but the budget justification is thin.",
    "compliance_findings": [
        {"area": "Broader Impacts", "status": "missing",
         "detail": "No broader impacts section found.", "evidence": ""},
        {"area": "Budget alignment", "status": "partial",
         "detail": "Justification lacks per-year detail.",
         "evidence": "Personnel and equipment totaling $400,000."},
    ],
    "suggestions": [
        {"section": "Budget Justification", "suggestion": "Break costs out by year."},
    ],
}


@pytest.fixture
def _text_pdf(monkeypatch):
    pages = _DRAFT_TEXT.split("\n\n")
    monkeypatch.setattr(dc, "_extract_pdf", lambda b: (_DRAFT_TEXT, len(pages), pages))


def test_ai_review_populated_and_deterministic_unchanged(_text_pdf, monkeypatch):
    monkeypatch.setattr(gemini_client, "generate_json", lambda *a, **k: dict(_VALID_AI))

    with_ai = dc.critique_pdf(b"x", "NSF", _SOL, include_ai=True)
    without_ai = dc.critique_pdf(b"x", "NSF", _SOL, include_ai=False)

    # AI section present + normalized
    assert with_ai["ai_review"] is not None
    assert with_ai["ai_review"]["summary"].startswith("The narrative")
    assert len(with_ai["ai_review"]["compliance_findings"]) == 2
    assert with_ai["ai_review"]["compliance_findings"][0]["status"] == "missing"

    # Deterministic result identical with vs without AI
    assert with_ai["verdict"] == without_ai["verdict"]
    assert with_ai["checks"] == without_ai["checks"]
    assert with_ai["counts"] == without_ai["counts"]
    assert with_ai["issues"] == without_ai["issues"]
    assert without_ai["ai_review"] is None


def test_ai_review_none_when_model_returns_none(_text_pdf, monkeypatch):
    monkeypatch.setattr(gemini_client, "generate_json", lambda *a, **k: None)
    out = dc.critique_pdf(b"x", "NSF", _SOL, include_ai=True)
    assert out["ai_review"] is None
    # deterministic still produced
    assert out["verdict"] is not None
    assert isinstance(out["checks"], list) and out["checks"]


def test_ai_review_none_when_model_raises(_text_pdf, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("gemini exploded")
    monkeypatch.setattr(gemini_client, "generate_json", boom)
    out = dc.critique_pdf(b"x", "NSF", _SOL, include_ai=True)
    assert out["ai_review"] is None
    assert out["verdict"] is not None


def test_include_ai_false_never_calls_gemini(_text_pdf, monkeypatch):
    called = {"n": 0}
    def spy(*a, **k):
        called["n"] += 1
        return dict(_VALID_AI)
    monkeypatch.setattr(gemini_client, "generate_json", spy)
    out = dc.critique_pdf(b"x", "NSF", _SOL, include_ai=False)
    assert out["ai_review"] is None
    assert called["n"] == 0


def test_ai_review_clamps_bad_status_and_caps_lists(_text_pdf, monkeypatch):
    junk = {
        "summary": "ok",
        "compliance_findings": [{"area": f"A{i}", "status": "nonsense", "detail": "d"} for i in range(10)],
        "suggestions": [{"section": f"S{i}", "suggestion": "x"} for i in range(10)],
    }
    monkeypatch.setattr(gemini_client, "generate_json", lambda *a, **k: junk)
    out = dc.critique_pdf(b"x", "NSF", _SOL, include_ai=True)
    ai = out["ai_review"]
    assert len(ai["compliance_findings"]) == 6   # capped
    assert len(ai["suggestions"]) == 6           # capped
    assert all(f["status"] == "unclear" for f in ai["compliance_findings"])  # clamped


def test_ai_review_none_when_model_returns_empty_shapes(_text_pdf, monkeypatch):
    monkeypatch.setattr(gemini_client, "generate_json",
                        lambda *a, **k: {"summary": "", "compliance_findings": [], "suggestions": []})
    out = dc.critique_pdf(b"x", "NSF", _SOL, include_ai=True)
    assert out["ai_review"] is None


# ---------- evidence verification (the hard anti-hallucination gate) --------

def test_verify_evidence_keeps_real_quote():
    findings = [{"area": "X", "status": "partial", "detail": "d",
                 "evidence": "We will deploy sensors and analyze data"}]
    kept = dc._verify_evidence(findings, _DRAFT_TEXT)
    assert len(kept) == 1


def test_verify_evidence_drops_fabricated_quote():
    findings = [{"area": "X", "status": "addressed", "detail": "d",
                 "evidence": "The proposal guarantees a 95% success rate"}]  # not in draft
    kept = dc._verify_evidence(findings, _DRAFT_TEXT)
    assert kept == []


def test_verify_evidence_drops_addressed_without_quote():
    findings = [{"area": "X", "status": "addressed", "detail": "d", "evidence": ""}]
    assert dc._verify_evidence(findings, _DRAFT_TEXT) == []


def test_verify_evidence_keeps_missing_without_quote():
    findings = [{"area": "Broader Impacts", "status": "missing", "detail": "d", "evidence": ""}]
    kept = dc._verify_evidence(findings, _DRAFT_TEXT)
    assert len(kept) == 1


def test_verify_evidence_quote_match_is_whitespace_insensitive():
    # extra spaces / different casing in the quote still match the draft
    findings = [{"area": "X", "status": "partial", "detail": "d",
                 "evidence": "we   will DEPLOY sensors"}]
    kept = dc._verify_evidence(findings, _DRAFT_TEXT)
    assert len(kept) == 1


def test_integration_fabricated_finding_dropped_real_kept(_text_pdf, monkeypatch):
    ai = {
        "summary": "ok",
        "compliance_findings": [
            {"area": "Real", "status": "partial", "detail": "d",
             "evidence": "Personnel and equipment totaling $400,000."},   # real -> kept
            {"area": "Fake", "status": "addressed", "detail": "d",
             "evidence": "This proposal won a Nobel Prize"},               # fabricated -> dropped
        ],
        "suggestions": [],
    }
    monkeypatch.setattr(gemini_client, "generate_json", lambda *a, **k: ai)
    out = dc.critique_pdf(b"x", "NSF", _SOL, include_ai=True)
    areas = [f["area"] for f in out["ai_review"]["compliance_findings"]]
    assert areas == ["Real"]   # the hallucinated finding never reaches the user
