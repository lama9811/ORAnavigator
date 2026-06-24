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
    for key in ("sponsor", "deadline", "deadline_details", "page_limits",
                "required_attachments", "eligibility", "budget_cap",
                "submission_portal", "program_id", "program_name",
                "source_quotes"):
        assert key in out, f"contract key missing: {key}"


def test_coerce_deadline_details_passthrough_and_blank_to_none():
    """deadline_details (multi-category deadline summary) survives coercion;
    a blank string is normalized to None."""
    out = sx._coerce_extracted({"deadline_details": "Cat II: July 28, 2026"})
    assert out["deadline_details"] == "Cat II: July 28, 2026"
    assert sx._coerce_extracted({"deadline_details": "   "})["deadline_details"] is None
    assert sx._coerce_extracted({})["deadline_details"] is None


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
                        lambda text, **kw: json.dumps(fake_json))

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
                        lambda text, **kw: "Sorry, I cannot do that.")
    assert sx.extract_from_text("any pdf text") is None


def test_extract_from_text_handles_empty_input():
    """Empty / blank input is a fast no-op -- don't burn a Gemini call."""
    assert sx.extract_from_text("") is None
    assert sx.extract_from_text("   \n  \n") is None


# --- sponsor canonicalization + page_limits coercion (2026-05-29 accuracy work) ---

import pytest as _pytest
from services import solicitation_extractor as _se


@_pytest.mark.parametrize("raw,expected", [
    ("National Science Foundation", "NSF"),
    ("NSF", "NSF"),
    ("Department of Energy", "DoE"),
    ("DoE", "DoE"),
    ("Department of Defense", "DoD"),
    ("National Aeronautics and Space Administration", "NASA"),
    ("Alfred P. Sloan Foundation", "Alfred P. Sloan Foundation"),  # foundation keeps full name
    ("Gordon and Betty Moore Foundation", "Gordon and Betty Moore Foundation"),
])
def test_canon_sponsor(raw, expected):
    assert _se._canon_sponsor(raw) == expected


def test_coerce_canonicalizes_sponsor_fullname():
    out = _se._coerce_extracted({"sponsor": "National Science Foundation"})
    assert out["sponsor"] == "NSF"


def test_coerce_keeps_foundation_fullname():
    out = _se._coerce_extracted({"sponsor": "Alfred P. Sloan Foundation"})
    assert out["sponsor"] == "Alfred P. Sloan Foundation"


def test_parse_response_tolerates_control_chars():
    """A control char in a JSON string (pdfplumber ligature artifact) must not
    crash the parse -- regression for the NSF MRI extraction returning None."""
    raw = '{"sponsor": "NSF", "source_quotes": {"x": "all \x1fgures and charts"}}'
    out = _se._parse_response(raw)
    assert out is not None
    assert out["sponsor"] == "NSF"


# ---------- evidence verification: flag fields not backed by a real quote ----

_PDF_TEXT = (
    "Program Solicitation NSF 26-512. Full proposals are due no later than "
    "5:00 p.m. submitter's local time on March 15, 2026. The maximum budget "
    "per proposal is $500,000 total for up to three years. Project Description "
    "is limited to 15 pages."
)


def test_verify_flags_field_with_no_quote():
    extracted = {
        "deadline": "2026-03-15", "budget_cap": 500000,
        "source_quotes": {"deadline": "due no later than 5:00 p.m. submitter's local time on March 15, 2026"},
    }
    unv = sx._verify_source_quotes(extracted, _PDF_TEXT)
    assert "budget_cap" in unv          # value but no source quote
    assert "deadline" not in unv        # value + real quote present in text


def test_verify_flags_fabricated_quote():
    extracted = {
        "budget_cap": 999999,
        "source_quotes": {"budget_cap": "awards of up to $999,999 are available"},  # not in text
    }
    assert "budget_cap" in sx._verify_source_quotes(extracted, _PDF_TEXT)


def test_verify_keeps_real_quote_whitespace_insensitive():
    extracted = {
        "budget_cap": 500000,
        "source_quotes": {"budget_cap": "maximum   BUDGET per proposal is $500,000"},
    }
    assert "budget_cap" not in sx._verify_source_quotes(extracted, _PDF_TEXT)


def test_verify_ignores_null_and_empty_values():
    extracted = {
        "deadline": None, "eligibility": "", "page_limits": {},
        "required_attachments": [], "source_quotes": {},
    }
    assert sx._verify_source_quotes(extracted, _PDF_TEXT) == []


def test_verify_does_not_flag_sponsor():
    # sponsor is canonicalized (e.g. "NSF") and intentionally not verified
    extracted = {"sponsor": "NSF", "source_quotes": {}}
    assert "sponsor" not in sx._verify_source_quotes(extracted, _PDF_TEXT)


def test_extract_from_text_flags_fabricated_value_keeps_it(monkeypatch):
    """Integration: a real deadline quote + a fabricated budget quote ->
    budget_cap is FLAGGED but its value is preserved (flag, never drop)."""
    fake = {
        "sponsor": "NSF",
        "deadline": "2026-03-15",
        "budget_cap": 8000000,  # the program-total trap
        "page_limits": {}, "required_attachments": [],
        "source_quotes": {
            "deadline": "due no later than 5:00 p.m. submitter's local time on March 15, 2026",
            "budget_cap": "total program budget is approximately $8,000,000",  # NOT in _PDF_TEXT
        },
    }
    monkeypatch.setattr(sx, "_call_gemini", lambda text, **kw: json.dumps(fake))
    out = sx.extract_from_text(_PDF_TEXT)
    assert out["budget_cap"] == 8000000          # value preserved (flag, not drop)
    assert "budget_cap" in out["unverified_fields"]
    assert "deadline" not in out["unverified_fields"]


def test_lenient_list_field_accepts_leading_chunk():
    """A long required_attachments quote whose tail diverges from the PDF's
    bullet layout is accepted when its LEADING chunk is present (lenient)."""
    text = "Each full proposal MUST include the following required components: Project Summary, Project Description, Biographical Sketch."
    extracted = {
        "required_attachments": ["Project Summary", "Project Description"],
        "source_quotes": {"required_attachments":
            "Each full proposal MUST include the following required components: A; B; C; D; E; F"},
    }
    assert "required_attachments" not in sx._verify_source_quotes(extracted, text)


def test_strict_scalar_field_rejects_leading_only_match():
    """A budget_cap quote whose opening is real but whose AMOUNT is fabricated
    is still flagged -- scalar fields get strict full-quote matching."""
    text = "The maximum budget per proposal is $500,000 total for up to three years."
    extracted = {
        "budget_cap": 900000,
        "source_quotes": {"budget_cap": "The maximum budget per proposal is $900,000 total"},
    }
    assert "budget_cap" in sx._verify_source_quotes(extracted, text)


def test_list_noise_glyphs_ignored_in_match():
    """Bullet glyphs / (cid:NN) artifacts between items don't block a match."""
    text = "Required: (cid:127) Biosketch (cid:127) Data Management Plan"
    extracted = {
        "required_attachments": ["Biosketch", "Data Management Plan"],
        "source_quotes": {"required_attachments": "Required: Biosketch Data Management Plan"},
    }
    assert "required_attachments" not in sx._verify_source_quotes(extracted, text)


# ---------- budget_cap_details (per-category caps) --------------------------

def test_coerce_cap_details_normalizes_strings_and_floats():
    """Category caps arrive as strings/floats with symbols; coerce to int."""
    raw = {
        "sponsor": "NSF", "deadline": None, "page_limits": {},
        "required_attachments": [], "eligibility": None, "budget_cap": 500000,
        "submission_portal": None, "program_id": None, "program_name": None,
        "source_quotes": {},
        "budget_cap_details": [
            {"category": "Category I", "cap": "$30,000,000"},
            {"category": "Category II", "cap": 9000000.0},
            {"category": "Category III", "cap": 500000},
        ],
    }
    out = sx._coerce_extracted(raw)
    assert out["budget_cap_details"] == [
        {"category": "Category I", "cap": 30000000},
        {"category": "Category II", "cap": 9000000},
        {"category": "Category III", "cap": 500000},
    ]


def test_coerce_cap_details_drops_unusable_entries():
    """Entries with no category or no parseable cap are dropped, not kept as junk."""
    raw = {
        "sponsor": "NSF", "deadline": None, "page_limits": {},
        "required_attachments": [], "eligibility": None, "budget_cap": None,
        "submission_portal": None, "program_id": None, "program_name": None,
        "source_quotes": {},
        "budget_cap_details": [
            {"category": "Category I", "cap": "30000000"},
            {"category": "", "cap": "9000000"},        # no category -> drop
            {"category": "Category III", "cap": "n/a"}, # no number -> drop
            {"category": "Category IV"},                # no cap key -> drop
        ],
    }
    out = sx._coerce_extracted(raw)
    assert out["budget_cap_details"] == [{"category": "Category I", "cap": 30000000}]


def test_coerce_cap_details_absent_or_single_is_empty_list():
    """Missing or non-list budget_cap_details normalizes to []."""
    raw = {
        "sponsor": "NSF", "deadline": None, "page_limits": {},
        "required_attachments": [], "eligibility": None, "budget_cap": 500000,
        "submission_portal": None, "program_id": None, "program_name": None,
        "source_quotes": {},
        # budget_cap_details intentionally absent
    }
    out = sx._coerce_extracted(raw)
    assert out["budget_cap_details"] == []
