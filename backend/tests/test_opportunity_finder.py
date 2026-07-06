"""Tests for the Opportunity Finder service.

Deterministic core (the live Grants.gov API decides what exists; the
institution-eligibility gate is pure code) is authoritative; Gemini only
re-ranks/explains what the API already returned, and degrades gracefully.

Run from backend/:
    python -m pytest tests/test_opportunity_finder.py -v
"""
from services import opportunity_finder as of


# ---------------------------------------------------------------------------
# eligibility_gate -- deterministic institution STOP-gate (Morgan State = public IHE)
# ---------------------------------------------------------------------------

def _types(*descs):
    return [{"id": str(i), "description": d} for i, d in enumerate(descs)]


def test_public_ihe_is_eligible():
    at = _types("Public and State controlled institutions of higher education")
    assert of.eligibility_gate(at) == "eligible"


def test_unrestricted_is_unrestricted():
    at = _types("Unrestricted (i.e., open to any type of entity above)")
    assert of.eligibility_gate(at) == "unrestricted"


def test_others_see_text_defers():
    at = _types('Others (see text field entitled "Additional Information on Eligibility")')
    assert of.eligibility_gate(at) == "see_text"


def test_private_ihe_only_is_ineligible_for_public_school():
    at = _types("Private institutions of higher education")
    assert of.eligibility_gate(at) == "ineligible"


def test_governments_only_is_ineligible():
    at = _types("City or township governments", "County governments")
    assert of.eligibility_gate(at) == "ineligible"


def test_mixed_list_with_public_ihe_is_eligible():
    at = _types("Private institutions of higher education",
                "Public and State controlled institutions of higher education")
    assert of.eligibility_gate(at) == "eligible"


def test_empty_applicant_types_defers_to_text():
    assert of.eligibility_gate([]) == "see_text"


# ---------------------------------------------------------------------------
# extract_query -- description (+ profile hints) -> search keyword string
# ---------------------------------------------------------------------------

def test_extract_query_uses_description():
    q = of.extract_query("machine learning for cybersecurity education")
    assert "cybersecurity" in q.lower()


def test_extract_query_blends_profile_interests():
    q = of.extract_query("new imaging method",
                         profile={"interests": "biomedical imaging, MRI"})
    low = q.lower()
    assert "imaging" in low


def test_extract_query_empty_description_is_empty():
    assert of.extract_query("") == ""


# ---------------------------------------------------------------------------
# rank_and_explain -- Gemini advisory, with deterministic fallback
# ---------------------------------------------------------------------------

_OPPS = [
    {"id": "1", "title": "AI Cyber Scholarships", "synopsisDesc": "Supports AI and cybersecurity education.",
     "applicant_types": [{"id": "6", "description": "Public and State controlled institutions of higher education"}]},
    {"id": "2", "title": "Ocean Studies", "synopsisDesc": "Funds deep ocean research.",
     "applicant_types": [{"id": "6", "description": "Public and State controlled institutions of higher education"}]},
]


def test_rank_and_explain_falls_back_when_gemini_unavailable(monkeypatch):
    monkeypatch.setattr(of.gemini_client, "generate_json", lambda *a, **k: None)
    out = of.rank_and_explain("cybersecurity education", _OPPS)
    # API order preserved, no fabricated explanation
    assert [o["id"] for o in out] == ["1", "2"]
    assert out[0].get("fit_explanation", "") == ""


def test_rank_and_explain_keeps_only_grounded_quotes(monkeypatch):
    # Gemini ranks opp 1 first and quotes real source text; opp 2 gets a
    # fabricated quote that must be dropped.
    def fake_json(*a, **k):
        return {"ranking": ["1", "2"], "items": {
            "1": {"fit": "Directly matches your topic.", "quote": "AI and cybersecurity education"},
            "2": {"fit": "Also relevant.", "quote": "this phrase is not in the source"},
        }}
    monkeypatch.setattr(of.gemini_client, "generate_json", fake_json)
    out = of.rank_and_explain("cybersecurity education", _OPPS)
    assert out[0]["id"] == "1"
    assert out[0]["fit_quote"] == "AI and cybersecurity education"   # grounded -> kept
    assert out[1]["fit_quote"] == ""                                 # unquotable -> dropped


# ---------------------------------------------------------------------------
# find_opportunities -- orchestration with external calls mocked
# ---------------------------------------------------------------------------

def test_find_opportunities_builds_rows_with_deadline_and_eligibility(monkeypatch):
    monkeypatch.setattr(of, "search_grantsgov", lambda kw, rows=12: [
        {"id": "1", "title": "AI Cyber Scholarships", "agency": "NSF", "closeDate": "07/21/2026"},
    ])
    monkeypatch.setattr(of, "fetch_opportunity", lambda oid: {
        "id": "1", "title": "AI Cyber Scholarships", "agency": "NSF", "closeDate": "07/21/2026",
        "synopsisDesc": "Supports AI and cybersecurity education.",
        "applicant_types": [{"id": "6", "description": "Public and State controlled institutions of higher education"}],
        "applicantEligibilityDesc": "Open to accredited universities.",
        "solicitation_url": "https://example.gov/opp/1",
    })
    monkeypatch.setattr(of.gemini_client, "generate_json", lambda *a, **k: None)  # deterministic path

    rows = of.find_opportunities("cybersecurity education")
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "1"
    assert r["institution_eligibility"] == "eligible"
    assert r["solicitation_url"] == "https://example.gov/opp/1"
    # internal routing deadline is 5 business days before the 07/21/2026 sponsor close
    assert r["internal_deadline"].startswith("2026-07-14")


def test_search_grantsgov_returns_empty_on_api_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(of.requests, "post", boom)
    assert of.search_grantsgov("anything") == []


def test_fetch_opportunity_returns_none_on_api_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(of.requests, "post", boom)
    assert of.fetch_opportunity("1") is None


# ---------------------------------------------------------------------------
# _is_open -- drop expired opportunities; keep rolling/future ones
# ---------------------------------------------------------------------------

def test_is_open_keeps_future_and_rolling_drops_past():
    from datetime import datetime, timedelta
    future = (datetime.now() + timedelta(days=30)).strftime("%m/%d/%Y")
    past = (datetime.now() - timedelta(days=30)).strftime("%m/%d/%Y")
    assert of._is_open(future) is True          # still open
    assert of._is_open("") is True              # rolling / continuous submission
    assert of._is_open("not a date") is True    # unparseable -> don't hide it
    assert of._is_open(past) is False           # expired -> filtered out
