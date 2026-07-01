"""Unit tests for the solicitation responsiveness matrix.

The conftest autouse fixture forces Gemini OFFLINE, so most tests exercise the
deterministic paths: requirement assembly (from grounded state ONLY), the
ready/not-ready gates, and the offline fallback. Two tests monkeypatch
generate_json to drive the AI grounding/demotion path (a fabricated evidence
quote must demote 'addressed' -> 'check_by_hand').
"""
from services import section_coach as sc


# ── gates ────────────────────────────────────────────────────────────────────

def test_not_ready_with_no_saved_sections():
    r = sc.responsiveness_matrix("NSF", {})
    assert r["ready"] is False and r["rows"] == []


def test_blank_sections_are_ignored():
    r = sc.responsiveness_matrix("NSF", {"project_summary": "   "})
    assert r["ready"] is False and r["rows"] == []


# ── requirement assembly (anti-hallucination by construction) ────────────────

def test_requirements_span_all_grounded_sources():
    ctx = {
        "required_attachments": ["Biosketch", "Current & Pending Support"],
        "page_limits": {"project_description": 15},
        "eligibility": "Open only to minority-serving institutions.",
    }
    reqs = sc._responsiveness_requirements("NIH", ctx)
    assert {"attachment", "section", "criterion", "eligibility"} <= {r["source"] for r in reqs}


def test_no_requirement_outside_grounded_sources():
    # Every requirement id must trace to context or the deterministic rubric.
    ctx = {"required_attachments": ["Biosketch"], "page_limits": {"project_description": 15},
           "eligibility": "MSIs only."}
    reqs = sc._responsiveness_requirements("NSF", ctx)
    for r in reqs:
        assert r["id"].split(":", 1)[0] in {"section", "attachment", "criterion", "eligibility"}


def test_rubric_criteria_present_even_without_context():
    # The review criteria are always available, so a proposal with no solicitation
    # context still has requirements to check against.
    reqs = sc._responsiveness_requirements("NSF", None)
    names = {r["requirement"] for r in reqs if r["source"] == "criterion"}
    assert {"Intellectual Merit", "Broader Impacts"} <= names


def test_dmp_deduped_across_attachment_and_section():
    ctx = {"required_attachments": ["Data Management Plan"],
           "page_limits": {"data_management_plan": 2}}
    reqs = sc._responsiveness_requirements("NSF", ctx)
    dmp = [r for r in reqs if "data management plan" in r["requirement"].lower()]
    assert len(dmp) == 1
    assert dmp[0]["source"] == "section"   # section wins: has a section_key + page limit


def test_attachment_maps_to_section_key():
    ctx = {"required_attachments": ["Data Management Plan"]}
    reqs = sc._responsiveness_requirements("NIH", ctx)
    att = next(r for r in reqs if r["source"] == "attachment")
    assert att["section_key"] == "data_management_plan"


# ── offline fallback ─────────────────────────────────────────────────────────

def test_offline_fallback_marks_check_by_hand():
    r = sc.responsiveness_matrix("NSF", {"project_summary": "We build an open data system."})
    assert r["ai"] is False and r["ready"] is True
    assert r["rows"]
    for row in r["rows"]:
        assert row["status"] == "check_by_hand"
        assert row["evidence"] == ""        # never fabricates a quote offline


def test_status_vocabulary_is_restricted():
    r = sc.responsiveness_matrix("NIH", {"specific_aims": "Aim 1: do X."})
    allowed = {"addressed", "partial", "not_found", "check_by_hand"}
    assert all(row["status"] in allowed for row in r["rows"])


# ── AI grounding / demotion (monkeypatched generate_json) ────────────────────

def test_verified_quote_survives_and_is_attributed(monkeypatch):
    drafts = {"project_summary": "Our broader impacts include training 20 HBCU undergraduates."}
    bi_id = "criterion:broader impacts"

    def fake(*a, **k):
        return {"summary": "ok", "suggestions": [], "rows": [
            {"id": bi_id, "status": "addressed",
             "evidence": "training 20 HBCU undergraduates", "note": ""},
        ]}

    monkeypatch.setattr(sc.gemini_client, "generate_json", fake)
    r = sc.responsiveness_matrix("NSF", drafts)
    assert r["ai"] is True
    row = next(x for x in r["rows"] if x["requirement"] == "Broader Impacts")
    assert row["status"] == "addressed"
    assert row["evidence"] == "training 20 HBCU undergraduates"
    assert row["where"] == "Project Summary (NSF)"   # attributed authoritatively


def test_fabricated_quote_is_demoted(monkeypatch):
    drafts = {"project_summary": "Our broader impacts include training 20 HBCU undergraduates."}
    im_id = "criterion:intellectual merit"

    def fake(*a, **k):
        return {"summary": "ok", "suggestions": [], "rows": [
            {"id": im_id, "status": "addressed",
             "evidence": "this exact sentence is nowhere in the draft", "note": ""},
        ]}

    monkeypatch.setattr(sc.gemini_client, "generate_json", fake)
    r = sc.responsiveness_matrix("NSF", drafts)
    row = next(x for x in r["rows"] if x["requirement"] == "Intellectual Merit")
    assert row["status"] == "check_by_hand"   # unverifiable -> demoted
    assert row["evidence"] == ""
