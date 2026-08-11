"""The engine, driven by a profile that is NOT NSF 23-598."""
from services import draft_review, solicitation_profile as sp

DRAFT = """Project Summary
We will study salt-tolerant polymers for coastal sensing.

Research Strategy
Our specific aims are to synthesize and characterize three polymer families.
Four undergraduates per year will be trained in materials characterization.
"""

REQS = [
    {"id": "aims", "label": "Specific aims", "section": "research_strategy",
     "kind": "semantic", "scored": True, "source": "State the specific aims.",
     "why": "", "keywords": ["specific aim", "aims"]},
    {"id": "training", "label": "Student training plan", "section": "research_strategy",
     "kind": "semantic", "scored": True, "source": "Describe student training.",
     "why": "", "keywords": ["undergraduate", "train"]},
]


def _profile():
    return sp.make_profile(
        id="PAR-24-118", title="NIH Research Project Grant", url=None,
        sections=sp.sections_from(REQS + [{"section": "project_summary"}]),
        requirements=REQS)


def test_a_non_eir_profile_locates_its_own_sections_offline():
    result = draft_review.review_draft(DRAFT, profile=_profile(), use_ai=False)
    located = {s["key"] for s in result["sections_located"]}
    assert "research_strategy" in located
    assert result["solicitation"]["id"] == "PAR-24-118"


def test_the_score_is_withheld_when_the_ai_layer_is_offline():
    # Golden rule 3 + the EiR rule: a percentage computed without the semantic
    # half reads as a verdict on the draft, not on our availability.
    result = draft_review.review_draft(DRAFT, profile=_profile(), use_ai=False)
    assert result["score"] is None
    assert all(f["status"] == "unclear" for f in result["findings"])


def test_no_finding_is_ever_reported_against_a_requirement_the_profile_lacks():
    result = draft_review.review_draft(DRAFT, profile=_profile(), use_ai=False)
    assert {f["id"] for f in result["findings"]} <= {"aims", "training"}
