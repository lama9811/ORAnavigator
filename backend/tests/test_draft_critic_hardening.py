"""Hardening regression tests for the Draft Critic.

These pin the fixes for the 2026-05-29 correctness audit. The Draft Critic is
the one tool faculty trust before submitting a real grant, so the dominant
design rule is: a FALSE PASS (saying a non-compliant draft is fine) is the
worst outcome, a FALSE FAIL is annoying but safe.

Run from backend/:
    ../.venv/bin/python -m pytest tests/test_draft_critic_hardening.py -v
"""
import pytest

from services import draft_critic as dc
from services import proposals_service as ps
from services import solicitation_extractor as se


# =====================================================================
# A. Budget parsing: word magnitudes / abbreviations (CRITICAL false pass)
# =====================================================================

@pytest.mark.parametrize("text,expected", [
    ("Total requested: $2.5 million", 2_500_000),
    ("Budget: $1.2MM total", 1_200_000),
    ("Award up to $0.6B", 600_000_000),
    ("Project total of $2.5 billion", 2_500_000_000),
    ("We request $750 thousand", 750_000),
    # existing behavior must be preserved
    ("$2.5M", 2_500_000),
    ("ask $0.5M", 500_000),
    ("$500K", 500_000),
    ("lowercase $2m", 2_000_000),
    ("$500,000", 500_000),
    ("$1,234,567.89", 1_234_567),
])
def test_largest_dollar_amount_understands_magnitudes(text, expected):
    assert dc._largest_dollar_amount(text) == expected


def test_over_cap_worded_budget_fails_not_passes():
    """$2.5 million against a $500K cap must FAIL (was a false pass)."""
    out = dc.check_budget_cap("Total requested funding is $2.5 million", 500_000)
    assert out["status"] == "fail", out


def test_largest_dollar_amount_no_overflow_on_long_digit_run():
    """A pathological digit run must not raise OverflowError (was HTTP 500)."""
    # Should simply ignore the implausible value, not crash.
    assert dc._largest_dollar_amount("$" + "9" * 400) is None


def test_check_budget_cap_non_numeric_cap_is_safe():
    out = dc.check_budget_cap("Total $1,000,000", "not-a-number")
    assert out["status"] in ("skipped", "warn")  # must not raise


# =====================================================================
# B. Section detection: header-aware, boundary-correct
# =====================================================================

@pytest.mark.parametrize("line", [
    "Project Summary",
    "PROJECT SUMMARY",
    "1. Project Summary",
    "(2) Project Summary",
    "A. Project Summary",
    "B) Project Summary",
    "IV. Project Summary",
    "Section 1: Project Summary",
    "Project Summary:",
    "Project Summary (1 page max)",
    "• Project Summary",
])
def test_section_present_true_for_real_headers(line):
    assert dc._section_present(line + "\nbody text here", "Project Summary") is True


@pytest.mark.parametrize("text,name", [
    # prefix collision: "Budget" must NOT match a "Budget Justification" header
    ("Budget Justification\nText about the budget.", "Budget"),
    # prefix-superset header is a different section
    ("Data Management Plan Compliance Statement\nbody", "Data Management Plan"),
    # colon-in-body prose must NOT match (the CRITICAL false pass)
    ("Project Description\nOur approach is described here: data management plan "
     "details will follow in a later revision.", "Data Management Plan"),
    # table-of-contents line with trailing page number must NOT match
    ("Table of Contents\nSection 4: Data Management Plan 12", "Data Management Plan"),
])
def test_section_present_false_for_non_headers(text, name):
    assert dc._section_present(text, name) is False


@pytest.mark.parametrize("name", ["", "   ", None])
def test_section_present_false_for_empty_name(name):
    assert dc._section_present("Anything\nhere", name) is False


def test_required_attachments_found_with_lettered_headers():
    """Lettered/roman section numbering must not cause false 'missing'."""
    text = ("A. Project Summary\nx\nB. Project Description\ny\n"
            "C. Data Management Plan\nz\n")
    out = dc.check_required_attachments(
        text, ["Project Summary", "Project Description", "Data Management Plan"])
    assert out["status"] == "ok", out
    assert out["missing"] == []


def test_required_attachments_synonym_and_ampersand_match():
    """Biosketch vs Biographical Sketch and '&' vs 'and' must still match."""
    text = ("Biographical Sketch\nx\nCurrent and Pending Support\ny\n"
            "Data Management Plan\nz\n")
    out = dc.check_required_attachments(
        text, ["Biosketch", "Current & Pending Support", "Data Management Plan"])
    assert out["status"] == "ok", out
    assert out["missing"] == []


def test_required_attachment_truly_missing_still_fails():
    """Guard against over-loosening: a genuinely absent attachment must FAIL."""
    text = "Project Summary\nx\nProject Description\ny\n"
    out = dc.check_required_attachments(text, ["Project Summary", "Data Management Plan"])
    assert out["status"] == "fail"
    assert "Data Management Plan" in out["missing"]


# =====================================================================
# C. check_page_count: no whole-doc-vs-per-section false fail
# =====================================================================

def test_page_count_skips_when_only_per_section_caps():
    """Only DMP/biosketch caps stated -> no document-wide limit -> SKIP,
    never compare the 8-page doc against the 2-page DMP cap (false fail)."""
    out = dc.check_page_count(8, {"data_management_plan": 2, "biosketch": 2})
    assert out["status"] == "skipped", out


def test_page_count_uses_document_level_cap():
    assert dc.check_page_count(8, {"project_description": 15})["status"] == "ok"
    assert dc.check_page_count(16, {"project_description": 15})["status"] == "fail"


def test_page_count_recognizes_narrative_synonyms():
    assert dc.check_page_count(20, {"research narrative": 15})["status"] == "fail"


def test_page_count_ignores_nonpositive_cap():
    out = dc.check_page_count(8, {"project_description": -5})
    assert out["status"] == "skipped", out


# =====================================================================
# D. Image-only / empty-text PDF -> None (endpoint -> friendly 422)
# =====================================================================

def test_image_only_pdf_returns_none(monkeypatch):
    monkeypatch.setattr(dc, "_extract_pdf", lambda b: ("", 5, [""] * 5))
    out = dc.critique_pdf(b"x", "NSF", {"required_attachments": ["Biographical Sketch"]})
    assert out is None


def test_whitespace_only_text_returns_none(monkeypatch):
    monkeypatch.setattr(dc, "_extract_pdf", lambda b: ("   \n  \n", 3, ["  ", " ", ""]))
    assert dc.critique_pdf(b"x", "NSF", {}) is None


# =====================================================================
# E. Extractor page_limits coercion + key sanitization
# =====================================================================

def test_coerce_page_limits_values_to_int():
    out = se._coerce_extracted({
        "page_limits": {"project_description": "15 pages", "data_management_plan": 2,
                        "biosketch": "15-20"},
    })
    pl = out["page_limits"]
    assert pl["project_description"] == 15
    assert pl["data_management_plan"] == 2
    assert pl["biosketch"] == 15  # first integer of a range


def test_coerce_page_limits_sanitizes_comma_keys():
    out = se._coerce_extracted({
        "page_limits": {"facilities, equipment and other resources": 5,
                        "project_description": 15},
    })
    pl = out["page_limits"]
    assert all("," not in k for k in pl), pl
    assert pl["project_description"] == 15  # not corrupted


def test_coerce_page_limits_drops_uncoercible():
    out = se._coerce_extracted({"page_limits": {"x": "several", "y": 0}})
    assert out["page_limits"] == {}  # "several" has no int; 0 is not positive


# =====================================================================
# F. Round-trip: comma key + decoy budget line
# =====================================================================

def test_roundtrip_comma_pagelimit_key_does_not_corrupt_others(db_session):
    extracted = {
        "sponsor": "NSF",
        "page_limits": {"facilities, equipment and other resources": 5,
                        "project_description": 15},
        "required_attachments": [],
    }
    sub = ps.create_submission_from_solicitation(db_session, user_id=db_session.user_id,
                                                 extracted=extracted)
    ctx = ps.reconstruct_solicitation_context(sub)
    assert ctx["page_limits"].get("project_description") == 15


def test_reconstruct_budget_ignores_decoy_midline():
    class _Sub:
        notes = ("Eligibility: PIs whose Budget cap: 99 is waived this cycle\n"
                 "Budget cap: $500,000")
        tasks = []
    ctx = ps.reconstruct_solicitation_context(_Sub())
    assert ctx["budget_cap"] == 500_000


# ---- fixtures -------------------------------------------------------

@pytest.fixture
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from db import Base
    from models import User
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    u = User(email="pi@morgan.edu", password_hash="x", role="user")
    s.add(u)
    s.commit()
    s.user_id = u.id
    try:
        yield s
    finally:
        s.close()
