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
