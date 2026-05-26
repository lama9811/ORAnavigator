"""Tests for the solicitation extractor.

The extractor takes raw text from a sponsor PDF (NSF / NIH / DoE / etc.)
and asks Gemini to pull out a structured dict: deadline, page limits,
required attachments, eligibility, budget cap, submission portal. The
PDF -> text step uses pdfplumber and is exercised in an integration
smoke test, not here. These tests pin the JSON-contract layer: parsing,
validation, and graceful failure when Gemini hallucinates or returns
non-JSON.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_solicitation_extractor.py -v
"""
import json

import pytest

from services import solicitation_extractor as sx


# ---------- _parse_response -------------------------------------------------

def test_parse_response_plain_json():
    """Bare JSON without code fences parses cleanly."""
    raw = '{"sponsor": "NSF", "deadline": "2026-06-12", "page_limits": {}, "required_attachments": ["Biosketch"], "eligibility": null, "budget_cap": null, "submission_portal": "Research.gov", "program_id": "23-573", "program_name": "CAREER", "source_quotes": {}}'
    parsed = sx._parse_response(raw)
    assert parsed["sponsor"] == "NSF"
    assert parsed["deadline"] == "2026-06-12"


def test_parse_response_strips_markdown_fences():
    """Gemini frequently wraps JSON in ```json ... ``` -- we must strip."""
    raw = '```json\n{"sponsor": "NIH", "deadline": null, "page_limits": {}, "required_attachments": [], "eligibility": null, "budget_cap": null, "submission_portal": null, "program_id": null, "program_name": null, "source_quotes": {}}\n```'
    parsed = sx._parse_response(raw)
    assert parsed["sponsor"] == "NIH"


def test_parse_response_strips_bare_fences():
    """Same handling when the fence has no language tag."""
    raw = '```\n{"sponsor": "DoD", "deadline": null, "page_limits": {}, "required_attachments": [], "eligibility": null, "budget_cap": null, "submission_portal": null, "program_id": null, "program_name": null, "source_quotes": {}}\n```'
    parsed = sx._parse_response(raw)
    assert parsed["sponsor"] == "DoD"


def test_parse_response_malformed_returns_none():
    """If Gemini returns prose instead of JSON, return None so callers
    can surface a graceful 'we couldn't read this PDF' error to the
    user, not a 500."""
    raw = "I cannot extract structured data from this document because..."
    assert sx._parse_response(raw) is None


# ---------- _coerce_extracted -----------------------------------------------

def test_coerce_fills_missing_fields_with_none():
    """Older Gemini outputs may omit fields entirely. _coerce_extracted
    must return a dict with every contract key, missing ones as None,
    so the frontend can render an empty input box and the user can
    type the value in."""
    bare = {"sponsor": "NSF"}
    out = sx._coerce_extracted(bare)
    for key in ("sponsor", "deadline", "page_limits", "required_attachments",
                "eligibility", "budget_cap", "submission_portal",
                "program_id", "program_name", "source_quotes"):
        assert key in out, f"contract key missing: {key}"


def test_coerce_normalizes_required_attachments_to_list():
    """If Gemini returns a string instead of a list (it sometimes does
    for single-attachment solicitations), coerce wraps it."""
    extracted = {"sponsor": "NSF", "required_attachments": "Biosketch"}
    out = sx._coerce_extracted(extracted)
    assert out["required_attachments"] == ["Biosketch"]


def test_coerce_keeps_none_required_attachments_as_empty_list():
    out = sx._coerce_extracted({"sponsor": "NSF"})
    assert out["required_attachments"] == []


def test_coerce_keeps_page_limits_as_dict():
    """page_limits should always be a dict (section -> int)."""
    out = sx._coerce_extracted({"sponsor": "NSF"})
    assert isinstance(out["page_limits"], dict)


def test_coerce_normalizes_budget_cap_to_int_when_possible():
    """Sponsors often format budgets as '$600,000' or '600000'. The
    extracted value should land as an int (or None if unparseable),
    so the frontend can render it as a number without re-parsing."""
    assert sx._coerce_extracted({"budget_cap": 600000})["budget_cap"] == 600000
    assert sx._coerce_extracted({"budget_cap": "600000"})["budget_cap"] == 600000
    assert sx._coerce_extracted({"budget_cap": "$600,000"})["budget_cap"] == 600000
    assert sx._coerce_extracted({"budget_cap": None})["budget_cap"] is None
    assert sx._coerce_extracted({"budget_cap": "no cap"})["budget_cap"] is None


# ---------- extract_from_text (with mocked Gemini) --------------------------

def test_extract_from_text_returns_full_contract(monkeypatch):
    """End-to-end on the extraction layer (no PDF parsing) with a faked
    Gemini call. Must return the full contract dict."""
    fake_json = {
        "sponsor": "NSF",
        "program_id": "NSF 23-573",
        "program_name": "Faculty Early Career Development",
        "deadline": "2026-06-12T17:00:00-05:00",
        "page_limits": {"project_description": 15, "data_management_plan": 2},
        "required_attachments": ["Biosketch", "C&P Support", "DMP"],
        "eligibility": "Early-career tenure-track faculty",
        "budget_cap": 600000,
        "submission_portal": "Research.gov",
        "source_quotes": {
            "deadline": "Proposals are due no later than 5:00 p.m. (proposer's local time) on June 12, 2026.",
        },
    }
    monkeypatch.setattr(sx, "_call_gemini",
                        lambda text: json.dumps(fake_json))

    out = sx.extract_from_text("any pdf text would go here")
    assert out is not None
    assert out["sponsor"] == "NSF"
    assert out["deadline"] == "2026-06-12T17:00:00-05:00"
    assert out["budget_cap"] == 600000
    assert "Biosketch" in out["required_attachments"]
    assert out["source_quotes"]["deadline"].startswith("Proposals are due")


def test_extract_from_text_returns_none_on_gemini_failure(monkeypatch):
    """If Gemini errors / returns junk, the extractor must return None
    (the API endpoint then surfaces a friendly error to the frontend
    instead of crashing)."""
    monkeypatch.setattr(sx, "_call_gemini",
                        lambda text: "Sorry, I cannot do that.")
    assert sx.extract_from_text("any pdf text") is None


def test_extract_from_text_handles_empty_input():
    """Empty / blank input is a fast no-op -- don't burn a Gemini call."""
    assert sx.extract_from_text("") is None
    assert sx.extract_from_text("   \n  \n") is None
