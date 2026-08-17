"""Tests for the draft reviewer ENGINE (services/draft_review.py).

conftest pins gemini_client.get_client() -> None, so the AI is OFF by default and
the offline path is what runs unless a test patches generate_json.

The subject is always a PROFILE, never a hardcoded solicitation. These tests
build one from tests/fixtures/nsf_23_598.py — real, human-verified requirement
rows, because synthetic one-line requirements would not exercise the locate stage
the way a real heading does. The engine cannot tell them from extractor output.

The deterministic-check tests that used to live here went with the eight
NSF-specific `_check_*` functions; the shared check library is tested separately.

Run: cd backend && python3 -m pytest tests/test_draft_review.py -q
"""

from tests.fixtures import nsf_23_598 as fx
from services import draft_review, solicitation_profile as sp


def fixture_profile():
    """A realistic profile from human-verified NSF 23-598 rows.

    Test DATA only: nothing under services/ imports this, and the engine cannot
    tell these rows from ones the extractor produced. Using real requirement
    text keeps these tests honest — synthetic one-line requirements would not
    exercise the locate stage the way a real heading does."""
    rows = [r for r in fx.EIR_REQUIREMENTS if r["kind"] == "semantic"]
    return sp.make_profile(
        id=fx.SOLICITATION_ID, title=fx.SOLICITATION_TITLE, url=fx.SOLICITATION_URL,
        sections=dict(fx.SECTIONS), requirements=rows,
        merit_criteria=list(fx.MERIT_CRITERIA))


PROFILE = fixture_profile()
SECTIONS = PROFILE["sections"]


def _req(req_id):
    return next(r for r in PROFILE["requirements"] if r["id"] == req_id)


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
    spans, ai_used = draft_review.locate_sections(DRAFT, SECTIONS, use_ai=False)
    assert ai_used is False
    assert set(spans) >= {"project_summary", "project_description",
                          "broader_impacts", "budget_justification"}


def test_top_level_spans_do_not_overlap_and_follow_document_order():
    """The TOP-LEVEL sections still tile. A SUB-section deliberately does not —
    see the next test and draft_review._SUBSECTIONS: making the Project
    Description's Broader Impacts heading a boundary is what truncated a
    compliant Project Summary before its own third heading."""
    spans, _ = draft_review.locate_sections(DRAFT, SECTIONS, use_ai=False)
    top = sorted((s for k, s in spans.items() if k not in draft_review._SUBSECTIONS),
                 key=lambda s: s["start"])
    assert len(top) >= 3
    for a, b in zip(top, top[1:]):
        assert a["end"] <= b["start"]


def test_a_subsection_span_sits_inside_its_parent():
    spans, _ = draft_review.locate_sections(DRAFT, SECTIONS, use_ai=False)
    bi, pd = spans["broader_impacts"], spans["project_description"]
    assert pd["start"] <= bi["start"] < bi["end"] <= pd["end"]


def test_a_mention_in_a_paragraph_is_not_a_heading():
    """'...described in the project description below' must not create a section."""
    text = "Intro\nAs described in the project description below, we will proceed.\n"
    spans, _ = draft_review.locate_sections(text, SECTIONS, use_ai=False)
    assert "project_description" not in spans


def test_missing_section_is_reported_as_unlocated_not_missing():
    """The load-bearing guarantee of the one-box design: a section we failed to
    find must never be reported as content the author omitted."""
    result = draft_review.review_draft("Some prose with no headings at all.",
                                       profile=PROFILE, use_ai=False)
    sustainability = next(f for f in result["findings"] if f["id"] == "pd_sustainability_plan")
    assert sustainability["status"] == "could_not_locate"
    assert sustainability["status"] != "not_found"


def test_unlocated_requirements_are_excluded_from_the_score():
    findings = [
        {"id": "a", "scored": True, "status": "addressed"},
        {"id": "b", "scored": True, "status": "could_not_locate"},
        {"id": "c", "scored": True, "status": "not_checked"},
    ]
    s = draft_review.score(findings)
    assert s["assessed"] == 1        # only the addressed row counted
    assert s["percent"] == 100


def test_broader_impacts_text_is_visible_to_project_description_requirements():
    """Broader Impacts is a labeled SUB-section of the Project Description, so its
    heading must act as a boundary (to prove the label exists) WITHOUT hiding its
    text from the Project Description's own requirements.

    Regression: a draft whose Broader Impacts read 'Four undergraduates per year
    will be trained' was told it had not addressed 'improves research
    opportunities for students' — the exact sentence that addresses it."""
    spans, _ = draft_review.locate_sections(DRAFT, SECTIONS, use_ai=False)
    merged = draft_review._project_description_span(spans, SECTIONS)
    assert "Four undergraduates per year" in merged["text"]
    # ...and the Project Description's own content is still there.
    assert "overall research goal" in merged["text"]


def test_project_description_span_survives_a_missing_broader_impacts():
    text = DRAFT.split("Broader Impacts")[0]
    spans, _ = draft_review.locate_sections(text, SECTIONS, use_ai=False)
    merged = draft_review._project_description_span(spans, SECTIONS)
    assert merged is not None
    assert "overall research goal" in merged["text"]


def test_project_description_span_is_none_when_the_section_is_absent():
    spans, _ = draft_review.locate_sections("Just prose, no headings.", SECTIONS,
                                            use_ai=False)
    assert draft_review._project_description_span(spans, SECTIONS) is None


# ── grounding (golden rule 2) ───────────────────────────────────────────────

def _fake_ai(monkeypatch, payload):
    monkeypatch.setattr(draft_review.gemini_client, "generate_json", lambda *a, **k: payload)


def _review(section_key, span, reqs):
    return draft_review._review_section(section_key, span, reqs, SECTIONS, PROFILE["id"])


def test_addressed_without_a_verifiable_quote_is_demoted(monkeypatch):
    """The anti-agreeableness guarantee: a confident 'addressed' whose quote is
    not in the author's text is worth nothing and must not be shown as coverage."""
    reqs = [_req("pd_sustainability_plan")]
    _fake_ai(monkeypatch, {"findings": [{
        "id": "pd_sustainability_plan", "status": "addressed",
        "note": "Well covered.",
        "evidence": "A sentence the author never wrote about sustainability.",
    }]})
    span = {"text": "We will study polymers. There is no sustainability plan here."}
    out = _review("project_description", span, reqs)
    assert out[0]["status"] == "not_found"
    assert out[0]["evidence"] == ""
    assert "could not be verified" in out[0]["note"]


def test_addressed_with_a_real_quote_survives(monkeypatch):
    reqs = [_req("pd_sustainability_plan")]
    quote = "this work leads directly to a submission to the NSF DMR Ceramics core program"
    _fake_ai(monkeypatch, {"findings": [{
        "id": "pd_sustainability_plan", "status": "addressed",
        "note": "Names the follow-on program.", "evidence": quote,
    }]})
    out = _review("project_description", {"text": DRAFT}, reqs)
    assert out[0]["status"] == "addressed"
    assert out[0]["evidence"] == quote


def test_hard_wrapped_draft_still_matches_the_quote(monkeypatch):
    """A pasted draft is line-wrapped; the model quotes it with spaces. A raw
    substring test would wrongly reject a real quote (golden rule 2)."""
    reqs = [_req("pd_success_metrics")]
    _fake_ai(monkeypatch, {"findings": [{
        "id": "pd_success_metrics", "status": "addressed", "note": "Three metrics.",
        "evidence": "Success will be measured by three metrics: sensor lifetime, cost per node,",
    }]})
    out = _review("project_description", {"text": DRAFT}, reqs)
    assert out[0]["status"] == "addressed"


def test_model_cannot_invent_a_requirement(monkeypatch):
    """The requirement universe is fixed in the profile; a row the model made up
    is dropped rather than shown to the PI as a funder's ask."""
    reqs = [_req("pd_dissemination")]
    _fake_ai(monkeypatch, {"findings": [
        {"id": "pd_dissemination", "status": "not_found", "note": "Add one.", "evidence": ""},
        {"id": "invented_requirement", "status": "not_found",
         "note": "NSF requires a haiku.", "evidence": ""},
    ]})
    out = _review("project_description", {"text": DRAFT}, reqs)
    assert [f["id"] for f in out] == ["pd_dissemination"]


def test_a_requirement_the_model_skipped_is_unclear_not_missing(monkeypatch):
    """The model omitting a row is a fact about the model, not about the draft."""
    reqs = [_req("pd_background"), _req("pd_dissemination")]
    _fake_ai(monkeypatch, {"findings": [
        {"id": "pd_background", "status": "addressed", "note": "Cited.",
         "evidence": "Prior work by Chen et al. established the baseline."},
    ]})
    out = {f["id"]: f for f in _review("project_description", {"text": DRAFT}, reqs)}
    assert out["pd_dissemination"]["status"] == "unclear"


def test_an_unrecognised_status_falls_back_to_not_found(monkeypatch):
    reqs = [_req("pd_dissemination")]
    _fake_ai(monkeypatch, {"findings": [{
        "id": "pd_dissemination", "status": "looks great!", "note": "", "evidence": "",
    }]})
    out = _review("project_description", {"text": DRAFT}, reqs)
    assert out[0]["status"] == "not_found"


# ── scoring (golden rule 1: computed in code, never model-assigned) ─────────

def test_score_is_arithmetic_over_coverage():
    findings = [
        {"id": "a", "scored": True, "status": "addressed"},   # 1.0
        {"id": "b", "scored": True, "status": "partial"},     # 0.5
        {"id": "c", "scored": True, "status": "not_found"},   # 0.0
        {"id": "d", "scored": True, "status": "clear"},       # 1.0 prohibition respected
    ]
    s = draft_review.score(findings)
    assert s["earned"] == 2.5
    assert s["assessed"] == 4
    assert s["percent"] == 63


def test_unscored_conditional_requirements_do_not_count_against_the_draft():
    """'if available and appropriate' asks must not penalise a compliant draft."""
    findings = [
        {"id": "a", "scored": True, "status": "addressed"},
        {"id": "pd_preliminary_data", "scored": False, "status": "not_found"},
    ]
    assert draft_review.score(findings)["percent"] == 100


def test_score_bands():
    def band(pct_findings):
        return draft_review.score(pct_findings)["band"]
    assert band([{"id": str(i), "scored": True, "status": "addressed"}
                 for i in range(10)]) == "green"
    mixed = ([{"id": f"a{i}", "scored": True, "status": "addressed"} for i in range(7)]
             + [{"id": f"b{i}", "scored": True, "status": "not_found"} for i in range(3)])
    assert band(mixed) == "amber"
    assert band([{"id": "x", "scored": True, "status": "not_found"}]) == "red"


def test_score_is_none_when_nothing_was_assessed():
    assert draft_review.score([{"id": "a", "scored": True,
                                "status": "could_not_locate"}]) is None


def test_a_flagged_prohibition_costs_points():
    clean = draft_review.score([{"id": "a", "scored": True, "status": "clear"}])
    dirty = draft_review.score([{"id": "a", "scored": True, "status": "flagged"}])
    assert clean["percent"] == 100
    assert dirty["percent"] == 0


def test_the_score_basis_names_the_solicitation_it_measured_against():
    """The caption is load-bearing UI: it says what the number IS, so it cannot
    be read as a funding prediction."""
    s = draft_review.score([{"id": "a", "scored": True, "status": "addressed"}],
                           solicitation_id="PAR-24-118")
    assert "PAR-24-118" in s["basis"]
    assert "likelihood of an award" in s["basis"]


# ── offline behaviour (golden rule 3) ───────────────────────────────────────

def test_offline_suppresses_the_score_but_keeps_the_rule_checks():
    """A percentage computed without the semantic half would read as a verdict on
    the draft when it is really a verdict on our own availability.

    The "rule checks" half of this assertion is thin here on purpose: this
    fixture profile carries only semantic rows, so run_deterministic returns [].
    The shared checks it would resolve against live in services/generic_checks.py
    and are tested there."""
    result = draft_review.review_draft(DRAFT, profile=PROFILE, use_ai=False)
    assert result["ai"] is False
    assert result["score"] is None
    assert "unavailable" in result["message"]
    assert result["findings"], "the offline path must still report every requirement"


def test_offline_semantic_rows_are_unclear_never_a_hard_absence_claim():
    result = draft_review.review_draft(DRAFT, profile=PROFILE, use_ai=False)
    semantic = [f for f in result["findings"]
                if f["kind"] == "semantic" and f["source"] == "fallback"]
    assert semantic
    assert all(f["status"] == "unclear" for f in semantic)


def test_empty_paste_returns_a_prompt_not_a_zero_score():
    result = draft_review.review_draft("   ", profile=PROFILE)
    assert result["score"] is None
    assert result["findings"] == []
    assert "Paste your proposal" in result["message"]


# ── assembly ────────────────────────────────────────────────────────────────

def test_every_requirement_gets_exactly_one_finding():
    result = draft_review.review_draft(DRAFT, profile=PROFILE, use_ai=False)
    ids = [f["id"] for f in result["findings"]]
    assert len(ids) == len(set(ids)), "a requirement was reported twice"
    assert set(ids) == {r["id"] for r in PROFILE["requirements"]}


def test_findings_follow_solicitation_order():
    result = draft_review.review_draft(DRAFT, profile=PROFILE, use_ai=False)
    order = [r["id"] for r in PROFILE["requirements"]]
    got = [f["id"] for f in result["findings"]]
    assert got == sorted(got, key=order.index)


def test_every_finding_carries_its_solicitation_source_text():
    """The PI must always be able to see WHY something is required."""
    result = draft_review.review_draft(DRAFT, profile=PROFILE, use_ai=False)
    assert all(f["solicitation_says"].strip() for f in result["findings"])


def test_result_reports_which_sections_were_found_and_which_were_not():
    result = draft_review.review_draft(DRAFT, profile=PROFILE, use_ai=False)
    found = {s["key"] for s in result["sections_located"]}
    missing = {s["key"] for s in result["sections_missing"]}
    assert "project_description" in found
    assert "collaboration_letters" in missing
    assert not (found & missing)


def test_solicitation_metadata_comes_from_the_profile():
    meta = draft_review.review_draft(DRAFT, profile=PROFILE, use_ai=False)["solicitation"]
    assert meta["id"] == PROFILE["id"]
    assert meta["title"] == PROFILE["title"]
    assert meta["url"] == PROFILE["url"]


# ---------------------------------------------------------------------------
# Gemini thinking is CAPPED here, not disabled — and the cap is the measured
# middle ground, not a round number someone liked.
#
# gemini-3.6-flash thinks by default. Turning it off entirely is the fastest
# option and the wrong one for THIS caller: over paired runs the review went
# 15.4s -> 8.0s but `assessed` fell 38.7 -> 35.3, with one run collapsing to 27.
# The reviewer omits rows under no thinking, an omitted row becomes `unclear`,
# and `unclear` drops out of the score's denominator — so speed there is bought
# with coverage, quietly.
#
# A capped budget gets the latency without the loss. Measured over six runs at
# 1024: mean 8.0s and assessed=37 EVERY time, against ~17s / 37 for the default.
# Faster than thinking-on, and more deterministic than thinking-off.
#
# Contrast with services/solicitation_requirements.py, which disables thinking
# outright: there it made recall BETTER (79% -> 83%/92%), because that module
# spends a wall-clock budget it was wasting on thinking.
# ---------------------------------------------------------------------------

def test_the_review_caps_thinking_rather_than_disabling_it(monkeypatch):
    seen = []

    def spy(prompt, **kw):
        seen.append(kw.get("thinking_budget"))
        return {}

    monkeypatch.setattr(draft_review.gemini_client, "generate_json", spy)
    draft_review.locate_sections("Project Description\nsome text",
                                 {"project_description": {"label": "Project Description"}})
    draft_review._reviewer_notes({}, {"id": "x", "title": "T", "sections": {}})
    draft_review._review_section(
        "project_description", {"text": "some text"},
        [{"id": "r1", "label": "L", "source": "q", "kind": "semantic", "scored": True}],
        {"project_description": {"label": "Project Description"}}, "x")

    assert seen, "no model calls were made — the spy never ran"
    assert all(b == 1024 for b in seen), (
        f"every draft-review call must cap thinking at 1024, got {seen}. "
        "0 costs coverage (assessed 38.7 -> 35.3); the default costs ~9s."
    )
