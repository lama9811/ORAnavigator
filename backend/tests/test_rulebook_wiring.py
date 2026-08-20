"""draft_review's plumbing for the rulebook baseline.

Separate from test_rulebook_checks.py on purpose: these assert how the ENGINE
resolves and feeds the checks, not what the checks decide.
"""
FIVE_LINE_SUMMARY = """Project Summary

We propose to study trustworthy cardiac AI using multimodal physiological
sensing. The work will develop new models and validate them on clinical data.
We expect the results to be significant for the field.
"""


def test_run_deterministic_resolves_a_rulebook_check():
    """A row whose check resolves to nothing is SKIPPED, silently, with nothing
    going red. So the fall-through to rulebook_checks needs its own test."""
    from services import draft_review
    profile = {"requirements": [{
        "id": "pappg_ps_headings", "label": "headings", "section": "project_summary",
        "kind": "deterministic", "scored": True, "check": "rb_headings",
        "check_args": {"section": "project_summary",
                       "headings": ["Overview", "Intellectual Merit",
                                    "Broader Impacts"]},
        "source": "Your file must include three separate section headers.",
        "why": "", "keywords": [],
    }], "checks": {}}
    spans = {"project_summary": {"text": FIVE_LINE_SUMMARY, "marker": "Project Summary",
                                 "start": 0}}
    out = draft_review.run_deterministic(FIVE_LINE_SUMMARY, spans, profile)
    assert len(out) == 1
    assert out[0]["status"] == "not_found"


def test_run_deterministic_passes_pages_into_the_context():
    from services import draft_review
    profile = {"requirements": [{
        "id": "pappg_ps_one_page", "label": "one page", "section": "project_summary",
        "kind": "deterministic", "scored": True, "check": "rb_page_limit",
        "check_args": {"section": "project_summary", "limit": 1},
        "source": "File cannot exceed one page.", "why": "", "keywords": [],
    }], "checks": {}}
    spans = {"project_summary": {"text": "Short.", "marker": "PS", "start": 0}}
    out = draft_review.run_deterministic("Short.", spans, profile,
                                         pages={"project_summary": 3})
    assert out[0]["status"] == "flagged"
    assert "3 pages" in out[0]["note"]


# ── one section is not the whole package ────────────────────────────────────

def _profile_with_a_pointer_row():
    """A profile whose Project Summary row is a POINTER at the PAPPG.

    That is the shape `delegated_rules` exists for: the whole ask is "follow
    that document", so nothing about it was verified and it must not sit in the
    score's denominator."""
    from services import solicitation_profile as sp
    return sp.build_generic({}, [{
        "id": "sol_ps_pointer", "section": "project_summary",
        "label": "Adhere to PAPPG guidelines for the Project Summary",
        "kind": "semantic", "scored": True,
        "source": ("Prepare the Project Summary in accordance with the PAPPG."),
        "why": "", "keywords": [],
    }], id="NSF 99-999", title="A generic NSF solicitation")


CITED_SUMMARY = """Overview
We will study coastal sensing, and prior work (Alvarez 2019) frames the problem.

Intellectual Merit
The approach extends earlier results (Chen et al., 2021) to a second cohort.

Broader Impacts
Undergraduates will be trained, building on our pilot (Diallo 2022).
"""


def test_a_single_section_check_does_not_demand_the_reference_list():
    """`_missing_references` is a whole-document rule. Run over ONE section it
    told every well-cited draft to "upload it too" — in a modal that takes one
    file for one section and says the rest of the proposal is not needed."""
    from services import draft_review
    out = draft_review.review_section(CITED_SUMMARY, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    assert [m["kind"] for m in out["mistakes"]] == []


def test_both_entry_points_agree_that_a_pointer_row_is_delegated():
    """review_section's own docstring says it exists so two engines cannot
    disagree about the same section — and it was skipping `apply_delegation`,
    so the identical requirement came back `delegated` in Draft Review and
    `not_found` (counted against the draft) in the section check."""
    from services import draft_review
    profile = _profile_with_a_pointer_row()

    section = draft_review.review_section(
        CITED_SUMMARY, section="project_summary", rulebook="the PAPPG",
        profile=profile, use_ai=False)
    row = next(f for f in section["findings"] if f["id"] == "sol_ps_pointer")
    assert row["status"] == "delegated"
    assert row["delegated_to"] == "the PAPPG"

    whole = draft_review.review_draft(
        "Project Summary\n" + CITED_SUMMARY, profile=profile, use_ai=False)
    same = next(f for f in whole["findings"] if f["id"] == "sol_ps_pointer")
    assert same["status"] == row["status"]
    assert same["delegated_to"] == row["delegated_to"]


def test_a_baseline_row_is_not_demoted_by_the_section_checks_delegation():
    """apply_delegation's `rulebook` guard has to hold on this path too: a
    baseline row IS the PAPPG's rule, checked, not a pointer into it."""
    from services import draft_review
    out = draft_review.review_section(CITED_SUMMARY, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    headings = next(f for f in out["findings"] if f["id"] == "pappg_ps_headings")
    assert headings["status"] == "addressed"


# ── the section prompt must not silently drop rules ─────────────────────────

def _fake_reviewer(record):
    """A stand-in reviewer that answers every id it is asked about, and records how
    many that was. Deterministic — no model, so this runs in CI."""
    import re

    def _call(prompt, **kw):
        ids = re.findall(r"'id': '([^']+)'", prompt)
        record.append(len(ids))
        return {"findings": [{"id": i, "status": "addressed", "note": "ok",
                              "evidence": "the draft says so"} for i in ids]}
    return _call


def _reqs(n):
    return [{"id": f"r{i}", "label": f"Requirement {i}", "section": "project_summary",
             "kind": "semantic", "scored": True, "source": f"The proposal must do {i}.",
             "why": "", "keywords": []} for i in range(n)]


_SECTIONS = {"project_summary": {"label": "Project Summary",
                                 "aliases": ["project summary"]}}


def test_a_dense_section_is_reviewed_in_batches_not_one_giant_prompt():
    """THE FAILURE THIS PREVENTS. Every requirement for a section goes into ONE
    prompt capped at max_output_tokens=8192. When the reviewer cannot finish it
    OMITS rows; an omitted row becomes `unclear`; `unclear` is absent from _CREDIT,
    so it silently leaves the score's denominator and the PI is told nothing.

    Four sections of curated rules never came close to that ceiling. The PAPPG's
    Project Description alone extracts 22 rules and Budget is denser still, which
    is what makes this reachable rather than theoretical.
    """
    from unittest import mock
    from services import draft_review

    sizes = []
    span = {"text": "Project Summary\n" + ("the draft says so. " * 200),
            "marker": "Project Summary", "start": 0}
    with mock.patch.object(draft_review.gemini_client, "generate_json",
                           side_effect=_fake_reviewer(sizes)):
        out = draft_review._review_section("project_summary", span, _reqs(40),
                                           _SECTIONS, "NSF 23-598")

    assert len(out) == 40, f"{40 - len(out)} requirements vanished"
    assert not [f for f in out if f["status"] == "unclear"], \
        "a requirement came back unclear — dropped, not assessed"
    assert sizes, "the model was never called"
    assert max(sizes) <= draft_review.REVIEW_BATCH, \
        f"one prompt carried {max(sizes)} rules; the cap is {draft_review.REVIEW_BATCH}"
    assert len(sizes) > 1, "40 rules should not have fitted in one batch"


def test_a_small_section_is_still_one_call():
    """Batching must not turn today's 3-rule section into three round-trips."""
    from unittest import mock
    from services import draft_review

    sizes = []
    span = {"text": "Project Summary\nthe draft says so.", "marker": "PS", "start": 0}
    with mock.patch.object(draft_review.gemini_client, "generate_json",
                           side_effect=_fake_reviewer(sizes)):
        out = draft_review._review_section("project_summary", span, _reqs(3),
                                           _SECTIONS, "X")

    assert len(out) == 3
    assert len(sizes) == 1, f"3 rules took {len(sizes)} calls"


def test_one_failed_batch_does_not_lose_the_others():
    """A batch whose model call fails falls back to `unclear` for ITS rows only.
    Losing 40 assessments because one call timed out would be worse than the
    problem batching solves."""
    from unittest import mock
    from services import draft_review
    import re

    calls = {"n": 0}

    def flaky(prompt, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return None                     # first batch fails
        ids = re.findall(r"'id': '([^']+)'", prompt)
        return {"findings": [{"id": i, "status": "addressed", "note": "ok",
                              "evidence": "the draft says so"} for i in ids]}

    span = {"text": "Project Summary\n" + ("the draft says so. " * 200),
            "marker": "PS", "start": 0}
    with mock.patch.object(draft_review.gemini_client, "generate_json",
                           side_effect=flaky):
        out = draft_review._review_section("project_summary", span, _reqs(40),
                                           _SECTIONS, "X")

    assert len(out) == 40
    addressed = [f for f in out if f["status"] == "addressed"]
    assert addressed, "the surviving batches lost their findings too"
    assert len(addressed) < 40, "the failing batch should not have been assessed"
