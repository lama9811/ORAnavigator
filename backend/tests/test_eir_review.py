"""Tests for the EiR draft reviewer (services/eir_review.py + eir_solicitation.py).

conftest pins gemini_client.get_client() -> None, so the AI is OFF by default and
the offline path is what runs unless a test patches generate_json.

Run: cd backend && python3 -m pytest tests/test_eir_review.py -q
"""

import datetime as dt

import pytest

from services import eir_review as er
from services import eir_solicitation as sol


# ── recurring deadlines ─────────────────────────────────────────────────────
# The solicitation states RULES, not dates. These are the dates the rules yield.

def test_loi_deadline_is_second_thursday_in_july():
    assert sol.loi_deadline(2026) == dt.date(2026, 7, 9)
    assert sol.loi_deadline(2023) == dt.date(2023, 7, 13)   # matches the published PDF


def test_full_proposal_deadline_is_third_tuesday_in_october():
    assert sol.full_proposal_deadline(2026) == dt.date(2026, 10, 20)
    assert sol.full_proposal_deadline(2023) == dt.date(2023, 10, 17)  # published PDF


def test_computed_deadlines_land_on_the_right_weekday():
    for year in range(2024, 2031):
        assert sol.loi_deadline(year).weekday() == 3            # Thursday
        assert sol.full_proposal_deadline(year).weekday() == 1  # Tuesday


# ── locate ──────────────────────────────────────────────────────────────────

DRAFT = """Excellence in Research: Adaptive Materials for Coastal Sensing

Project Summary
This project (LOI 20260412) develops adaptive materials.

Project Description
Our overall research goal is to build a durable sensing program.
The Department of Physics has 11 faculty engaged in research with 4-course
teaching loads, 320 undergraduates and 25 graduate students.
We ask whether polymer blends resist salt fatigue; our hypothesis is that they do.
Prior work by Chen et al. established the baseline.
Success will be measured by three metrics: sensor lifetime, cost per node, and
two peer-reviewed publications.
We will disseminate results in journals and at the APS March Meeting.
A sustainability plan: this work leads directly to a submission to the NSF DMR
Ceramics core program in year three.
This award furthers my research trajectory, gives students hands-on research
opportunities, and builds lasting institutional research capacity at Morgan.

Broader Impacts
Four undergraduates per year will be trained in materials characterization.

Budget Justification
Travel: $3,200 each year for the PI to attend the two-day NSF grantee meeting in
Washington, DC.
"""


def test_heading_fallback_locates_sections_without_ai():
    spans, ai_used = er.locate_sections(DRAFT, use_ai=False)
    assert ai_used is False
    assert set(spans) >= {"project_summary", "project_description",
                          "broader_impacts", "budget_justification"}


def test_located_spans_do_not_overlap_and_follow_document_order():
    spans, _ = er.locate_sections(DRAFT, use_ai=False)
    ordered = sorted(spans.values(), key=lambda s: s["start"])
    for a, b in zip(ordered, ordered[1:]):
        assert a["end"] <= b["start"]


def test_a_mention_in_a_paragraph_is_not_a_heading():
    """'...described in the project description below' must not create a section."""
    text = "Intro\nAs described in the project description below, we will proceed.\n"
    spans, _ = er.locate_sections(text, use_ai=False)
    assert "project_description" not in spans


def test_missing_section_is_reported_as_unlocated_not_missing():
    """The load-bearing guarantee of the one-box design: a section we failed to
    find must never be reported as content the author omitted."""
    result = er.review_eir_draft("Some prose with no headings at all.", use_ai=False)
    sustainability = next(f for f in result["findings"] if f["id"] == "pd_sustainability_plan")
    assert sustainability["status"] == "could_not_locate"
    assert sustainability["status"] != "not_found"


def test_unlocated_requirements_are_excluded_from_the_score():
    findings = [
        {"id": "a", "scored": True, "status": "addressed"},
        {"id": "b", "scored": True, "status": "could_not_locate"},
        {"id": "c", "scored": True, "status": "not_checked"},
    ]
    s = er.score(findings)
    assert s["assessed"] == 1        # only the addressed row counted
    assert s["percent"] == 100


def test_broader_impacts_text_is_visible_to_project_description_requirements():
    """Broader Impacts is a labeled SUB-section of the Project Description, so its
    heading must act as a boundary (to prove the label exists) WITHOUT hiding its
    text from the Project Description's own requirements.

    Regression: a draft whose Broader Impacts read 'Four undergraduates per year
    will be trained' was told it had not addressed 'improves research
    opportunities for students' — the exact sentence that addresses it."""
    spans, _ = er.locate_sections(DRAFT, use_ai=False)
    merged = er._project_description_span(spans)
    assert "Four undergraduates per year" in merged["text"]
    # ...and the Project Description's own content is still there.
    assert "overall research goal" in merged["text"]


def test_project_description_span_survives_a_missing_broader_impacts():
    text = DRAFT.split("Broader Impacts")[0]
    spans, _ = er.locate_sections(text, use_ai=False)
    merged = er._project_description_span(spans)
    assert merged is not None
    assert "overall research goal" in merged["text"]


def test_project_description_span_is_none_when_the_section_is_absent():
    spans, _ = er.locate_sections("Just prose, no headings.", use_ai=False)
    assert er._project_description_span(spans) is None


# ── deterministic checks ────────────────────────────────────────────────────

def _spans(text=DRAFT):
    return er.locate_sections(text, use_ai=False)[0]


def _det(text=DRAFT, **kw):
    return {f["id"]: f for f in er.run_deterministic(text, _spans(text), **kw)}


def test_title_prefix_found_in_the_paste():
    assert _det()["title_prefix"]["status"] == "addressed"


def test_title_prefix_missing_is_reported_with_what_was_found():
    out = _det("Adaptive Materials\n\nProject Summary\nStuff.")
    f = out["title_prefix"]
    assert f["status"] == "not_found"
    assert "Adaptive Materials" in f["note"]


def test_loi_number_detected_in_project_summary():
    f = _det()["summary_loi_number"]
    assert f["status"] == "addressed"
    assert "20260412" in f["note"]


def test_loi_number_absent_is_not_found():
    text = DRAFT.replace("(LOI 20260412) ", "")
    assert _det(text)["summary_loi_number"]["status"] == "not_found"


def test_institutional_support_letter_is_never_flagged_as_a_banned_support_letter():
    """The required letter and the prohibited one share the word 'support'.
    Flagging the required one would tell a compliant PI to delete it."""
    text = DRAFT + "\n\nLetter of Institutional Support\nThe dean writes in support.\n"
    assert _det(text)["no_support_letters"]["status"] == "clear"


def test_collaborator_letter_of_support_is_flagged():
    text = DRAFT + "\n\nWe have enclosed letters of support from our collaborators.\n"
    f = _det(text)["no_support_letters"]
    assert f["status"] == "flagged"
    assert f["evidence"]


def test_describing_the_prohibition_is_not_a_violation():
    """A PI who writes 'no letters of support are included' is complying."""
    text = DRAFT + "\n\nNo letters of support are included, per the solicitation.\n"
    assert _det(text)["no_support_letters"]["status"] == "clear"


def test_offered_cost_sharing_is_flagged():
    text = DRAFT + "\n\nThe university will provide cost sharing of $50,000.\n"
    assert _det(text)["no_cost_sharing"]["status"] == "flagged"


def test_declining_cost_sharing_is_not_flagged():
    text = DRAFT + "\n\nNo cost sharing is included in this budget.\n"
    assert _det(text)["no_cost_sharing"]["status"] == "clear"


def test_equipment_within_cap():
    f = _det(budget={"equipment": 20000, "total": 100000})["equipment_cap"]
    assert f["status"] == "addressed"


def test_equipment_over_cap_reports_the_arithmetic():
    f = _det(budget={"equipment": 40000, "total": 100000})["equipment_cap"]
    assert f["status"] == "not_found"
    assert "40.0%" in f["note"]


def test_equipment_cap_without_a_budget_is_not_checked_not_failed():
    f = _det()["equipment_cap"]
    assert f["status"] == "not_checked"


def test_dc_meeting_travel_every_year_is_addressed():
    assert _det()["dc_meeting_travel"]["status"] == "addressed"


def test_dc_meeting_travel_without_a_recurrence_is_partial():
    text = DRAFT.replace("$3,200 each year for the PI", "$3,200 for the PI")
    assert _det(text)["dc_meeting_travel"]["status"] == "partial"


def test_dc_meeting_travel_absent_is_not_found():
    text = DRAFT.split("Budget Justification")[0]
    assert _det(text)["dc_meeting_travel"]["status"] == "not_found"


def test_collaboration_letters_absent_is_not_checked_not_failed():
    """Collaboration letters are required only if the project has collaborators."""
    assert _det()["collaboration_letter_format"]["status"] == "not_checked"


def test_collaboration_letter_with_nsfs_exact_sentence_passes():
    text = DRAFT + (
        "\n\nLetters of Collaboration\n"
        "If the proposal submitted by Dr. Jane Roe entitled Adaptive Materials is "
        "selected for funding by the NSF, it is my intent to collaborate and/or commit "
        "resources as detailed in the Project Description or the Facilities, Equipment "
        "or Other Resources section of the proposal.\n"
    )
    assert _det(text)["collaboration_letter_format"]["status"] == "addressed"


def test_chatty_collaboration_letter_is_flagged_as_wrong_format():
    text = DRAFT + (
        "\n\nLetters of Collaboration\n"
        "I am thrilled to support Dr. Roe's exciting project and will gladly provide "
        "access to my laboratory throughout the award period.\n"
    )
    assert _det(text)["collaboration_letter_format"]["status"] == "not_found"


# ── grounding (golden rule 2) ───────────────────────────────────────────────

def _fake_ai(monkeypatch, payload):
    monkeypatch.setattr(er.gemini_client, "generate_json", lambda *a, **k: payload)


def test_addressed_without_a_verifiable_quote_is_demoted(monkeypatch):
    """The anti-agreeableness guarantee: a confident 'addressed' whose quote is
    not in the author's text is worth nothing and must not be shown as coverage."""
    reqs = [sol.requirement_by_id("pd_sustainability_plan")]
    _fake_ai(monkeypatch, {"findings": [{
        "id": "pd_sustainability_plan", "status": "addressed",
        "note": "Well covered.",
        "evidence": "A sentence the author never wrote about sustainability.",
    }]})
    span = {"text": "We will study polymers. There is no sustainability plan here."}
    out = er._review_section("project_description", span, reqs)
    assert out[0]["status"] == "not_found"
    assert out[0]["evidence"] == ""
    assert "could not be verified" in out[0]["note"]


def test_addressed_with_a_real_quote_survives(monkeypatch):
    reqs = [sol.requirement_by_id("pd_sustainability_plan")]
    quote = "this work leads directly to a submission to the NSF DMR Ceramics core program"
    _fake_ai(monkeypatch, {"findings": [{
        "id": "pd_sustainability_plan", "status": "addressed",
        "note": "Names the follow-on program.", "evidence": quote,
    }]})
    out = er._review_section("project_description", {"text": DRAFT}, reqs)
    assert out[0]["status"] == "addressed"
    assert out[0]["evidence"] == quote


def test_hard_wrapped_draft_still_matches_the_quote(monkeypatch):
    """A pasted draft is line-wrapped; the model quotes it with spaces. A raw
    substring test would wrongly reject a real quote (golden rule 2)."""
    reqs = [sol.requirement_by_id("pd_success_metrics")]
    _fake_ai(monkeypatch, {"findings": [{
        "id": "pd_success_metrics", "status": "addressed", "note": "Three metrics.",
        "evidence": "Success will be measured by three metrics: sensor lifetime, cost per node,",
    }]})
    out = er._review_section("project_description", {"text": DRAFT}, reqs)
    assert out[0]["status"] == "addressed"


def test_model_cannot_invent_a_requirement(monkeypatch):
    """The requirement universe is fixed in code; a row the model made up is
    dropped rather than shown to the PI as an NSF ask."""
    reqs = [sol.requirement_by_id("pd_dissemination")]
    _fake_ai(monkeypatch, {"findings": [
        {"id": "pd_dissemination", "status": "not_found", "note": "Add one.", "evidence": ""},
        {"id": "invented_requirement", "status": "not_found",
         "note": "NSF requires a haiku.", "evidence": ""},
    ]})
    out = er._review_section("project_description", {"text": DRAFT}, reqs)
    assert [f["id"] for f in out] == ["pd_dissemination"]


def test_a_requirement_the_model_skipped_is_unclear_not_missing(monkeypatch):
    """The model omitting a row is a fact about the model, not about the draft."""
    reqs = [sol.requirement_by_id("pd_background"), sol.requirement_by_id("pd_dissemination")]
    _fake_ai(monkeypatch, {"findings": [
        {"id": "pd_background", "status": "addressed", "note": "Cited.",
         "evidence": "Prior work by Chen et al. established the baseline."},
    ]})
    out = {f["id"]: f for f in er._review_section("project_description", {"text": DRAFT}, reqs)}
    assert out["pd_dissemination"]["status"] == "unclear"


def test_an_unrecognised_status_falls_back_to_not_found(monkeypatch):
    reqs = [sol.requirement_by_id("pd_dissemination")]
    _fake_ai(monkeypatch, {"findings": [{
        "id": "pd_dissemination", "status": "looks great!", "note": "", "evidence": "",
    }]})
    out = er._review_section("project_description", {"text": DRAFT}, reqs)
    assert out[0]["status"] == "not_found"


# ── scoring (golden rule 1: computed in code, never model-assigned) ─────────

def test_score_is_arithmetic_over_coverage():
    findings = [
        {"id": "a", "scored": True, "status": "addressed"},   # 1.0
        {"id": "b", "scored": True, "status": "partial"},     # 0.5
        {"id": "c", "scored": True, "status": "not_found"},   # 0.0
        {"id": "d", "scored": True, "status": "clear"},       # 1.0 prohibition respected
    ]
    s = er.score(findings)
    assert s["earned"] == 2.5
    assert s["assessed"] == 4
    assert s["percent"] == 63


def test_unscored_conditional_requirements_do_not_count_against_the_draft():
    """'if available and appropriate' asks must not penalise a compliant draft."""
    findings = [
        {"id": "a", "scored": True, "status": "addressed"},
        {"id": "pd_preliminary_data", "scored": False, "status": "not_found"},
    ]
    assert er.score(findings)["percent"] == 100


def test_conditional_requirements_are_marked_unscored_in_the_solicitation():
    for rid in ("pd_preliminary_data", "pd_experimental_strategy"):
        assert sol.requirement_by_id(rid)["scored"] is False


def test_score_bands():
    def band(pct_findings):
        return er.score(pct_findings)["band"]
    assert band([{"id": str(i), "scored": True, "status": "addressed"} for i in range(10)]) == "green"
    mixed = ([{"id": f"a{i}", "scored": True, "status": "addressed"} for i in range(7)]
             + [{"id": f"b{i}", "scored": True, "status": "not_found"} for i in range(3)])
    assert band(mixed) == "amber"
    assert band([{"id": "x", "scored": True, "status": "not_found"}]) == "red"


def test_score_is_none_when_nothing_was_assessed():
    assert er.score([{"id": "a", "scored": True, "status": "could_not_locate"}]) is None


def test_a_flagged_prohibition_costs_points():
    clean = er.score([{"id": "a", "scored": True, "status": "clear"}])
    dirty = er.score([{"id": "a", "scored": True, "status": "flagged"}])
    assert clean["percent"] == 100
    assert dirty["percent"] == 0


# ── offline behaviour (golden rule 3) ───────────────────────────────────────

def test_offline_suppresses_the_score_but_keeps_the_rule_checks():
    """A percentage computed without the semantic half would read as a verdict on
    the draft when it is really a verdict on our own availability."""
    result = er.review_eir_draft(DRAFT, use_ai=False)
    assert result["ai"] is False
    assert result["score"] is None
    assert "unavailable" in result["message"]
    det = {f["id"]: f for f in result["findings"] if f["kind"] == "deterministic"}
    assert det["title_prefix"]["status"] == "addressed"
    assert det["summary_loi_number"]["status"] == "addressed"


def test_offline_semantic_rows_are_unclear_never_a_hard_absence_claim():
    result = er.review_eir_draft(DRAFT, use_ai=False)
    semantic = [f for f in result["findings"]
                if f["kind"] == "semantic" and f["source"] == "fallback"]
    assert semantic
    assert all(f["status"] == "unclear" for f in semantic)


def test_empty_paste_returns_a_prompt_not_a_zero_score():
    result = er.review_eir_draft("   ")
    assert result["score"] is None
    assert result["findings"] == []
    assert "Paste your EiR proposal" in result["message"]


# ── assembly ────────────────────────────────────────────────────────────────

def test_every_requirement_gets_exactly_one_finding():
    result = er.review_eir_draft(DRAFT, use_ai=False)
    ids = [f["id"] for f in result["findings"]]
    assert len(ids) == len(set(ids)), "a requirement was reported twice"
    assert set(ids) == {r["id"] for r in sol.EIR_REQUIREMENTS}


def test_findings_follow_solicitation_order():
    result = er.review_eir_draft(DRAFT, use_ai=False)
    order = [r["id"] for r in sol.EIR_REQUIREMENTS]
    got = [f["id"] for f in result["findings"]]
    assert got == sorted(got, key=order.index)


def test_every_finding_carries_its_solicitation_source_text():
    """The PI must always be able to see WHY something is required."""
    result = er.review_eir_draft(DRAFT, use_ai=False)
    assert all(f["solicitation_says"].strip() for f in result["findings"])


def test_result_reports_which_sections_were_found_and_which_were_not():
    result = er.review_eir_draft(DRAFT, use_ai=False)
    found = {s["key"] for s in result["sections_located"]}
    missing = {s["key"] for s in result["sections_missing"]}
    assert "project_description" in found
    assert "collaboration_letters" in missing
    assert not (found & missing)


def test_solicitation_metadata_points_at_the_next_open_cycle():
    meta = er.review_eir_draft(DRAFT, use_ai=False)["solicitation"]
    assert meta["id"] == "NSF 23-598"
    cycle = meta["cycle"]
    assert cycle["full_proposal_deadline"] >= dt.date.today().isoformat()


@pytest.mark.parametrize("rid", [r["id"] for r in sol.EIR_REQUIREMENTS
                                 if r["kind"] == "deterministic"])
def test_every_deterministic_requirement_has_a_real_check_function(rid):
    """A typo'd `check` key would silently drop a requirement from the report."""
    req = sol.requirement_by_id(rid)
    assert req["check"] in er._DETERMINISTIC_CHECKS
