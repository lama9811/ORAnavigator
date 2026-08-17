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
