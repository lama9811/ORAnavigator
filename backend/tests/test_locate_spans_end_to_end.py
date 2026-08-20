"""The rulebook checks, driven by REAL spans from locate_sections.

WHY THIS FILE EXISTS
--------------------
Every other test of the rulebook baseline hands `run_deterministic` a span dict
it built by hand — `{"project_summary": {"text": ..., "start": 0}}`. That skips
the whole of stage 1, and stage 1 is where both of the bugs below live. A green
1117-test suite saw neither of them, because nothing anywhere drove a check off
a span the locate stage actually produced.

Everything here runs with `use_ai=False`, so it is also the golden-rule-3
degraded path: no Gemini, deterministic segmentation only.
"""

from services import draft_review
from services import rulebook_baseline
from services import solicitation_profile as sp


# A package that does EXACTLY what this feature tells a PI to do: a Project
# Summary carrying all three NSF headings, and a Project Description carrying
# its own separately-labelled Broader Impacts header.
COMPLIANT_PACKAGE = """Project Summary

Overview
We will develop interpretable models for cardiac signal analysis, with the
objective of reducing false alarms in intensive care. The approach combines
multimodal sensing with a new calibration method.

Intellectual Merit
The work advances the state of the art in uncertainty calibration for
physiological time series, an area where current methods fail under distribution
shift.

Broader Impacts
Four undergraduates per year will be trained in signal processing, and the
curriculum developed here will be shared with two partner institutions.

Project Description

Introduction and Motivation
Cardiac monitoring in intensive care generates an alarm every few minutes, and
the great majority are false. This proposal addresses that directly through a
calibration method that is aware of sensor context.

Research Plan
Aim 1 develops the calibration estimator. Aim 2 validates it on retrospective
clinical recordings. Aim 3 runs a prospective evaluation with our clinical
partner.

Broader Impacts
The project will broaden participation by recruiting students through the
university's bridge program, and results will be disseminated through an open
curriculum module used by two partner institutions.

References Cited

Alvarez, M. and Chen, R. (2021). Calibration under distribution shift. Journal
of Biomedical Informatics.

Facilities, Equipment and Other Resources

The Signal Processing Laboratory provides a dedicated compute cluster and a
bench for sensor prototyping. Our clinical partner provides de-identified
recordings and a research coordinator whose effort is contributed at no cost to
this project. Dr. Alvarez contributes expertise in clinical validation and
requests no funds.
"""


def _nsf_profile():
    """A profile shaped like a real extracted NSF one.

    The solicitation's own rows cite the PAPPG (which is what earns the baseline
    rows) and one of them names a `broader_impacts` section — which is what the
    real extractor produces, and what makes the Broader Impacts heading a
    candidate locate boundary.
    """
    extracted = [
        {"id": "sol_ps_loi", "section": "project_summary",
         "label": "Include the LOI number in the Project Summary",
         "kind": "semantic", "scored": True,
         "source": ("The Project Summary must include the LOI number in addition "
                    "to all the requirements outlined in the PAPPG."),
         "why": "", "keywords": ["loi"]},
        {"id": "sol_bi_plan", "section": "broader_impacts",
         "label": "Describe the broader impacts plan",
         "kind": "semantic", "scored": True,
         "source": "The Broader Impacts section must describe the plan.",
         "why": "", "keywords": ["broader impacts"]},
    ]
    return sp.build_generic({}, extracted, id="NSF 99-999",
                            title="A generic NSF solicitation")


def _by_id(result):
    return {f["id"]: f for f in result["findings"]}


# ── B1: following our own advice must not break the check ───────────────────

def test_a_compliant_package_passes_both_broader_impacts_heading_rules():
    """The flagship rule, on a package that did exactly what we asked.

    The spans TILE: each located section runs to the next marker. The first
    "Broader Impacts" line in a compliant document is INSIDE the Project
    Summary, so as a top-level boundary it truncated the summary before its own
    third heading (`pappg_ps_headings` -> partial, "Missing Broader Impacts")
    and — the mirror case — cut the Project Description's span at its Broader
    Impacts header, so `pappg_pd_impacts_header` reported not_found on a
    compliant one. Both in the SAME run, which is why they are asserted
    together."""
    result = draft_review.review_draft(
        COMPLIANT_PACKAGE, profile=_nsf_profile(), use_ai=False)
    found = _by_id(result)

    ps = found["pappg_ps_headings"]
    assert ps["status"] == "addressed", ps["note"]
    pd = found["pappg_pd_impacts_header"]
    assert pd["status"] == "addressed", pd["note"]


def test_the_project_description_keeps_its_own_broader_impacts_header():
    """The mirror of the case above, and it is just as real.

    With no Broader Impacts heading in the Project Summary, the FIRST one in the
    document is the Project Description's own — so the boundary landed inside
    the Project Description and cut its span off immediately BEFORE the header,
    and `pappg_pd_impacts_header` reported "None of Broader Impacts appears as a
    heading" about a section whose header is right there."""
    text = COMPLIANT_PACKAGE.replace(
        "Broader Impacts\nFour undergraduates per year will be trained in signal "
        "processing, and the\ncurriculum developed here will be shared with two "
        "partner institutions.",
        "Four undergraduates per year will be trained in signal processing.", 1)
    assert text != COMPLIANT_PACKAGE      # the replace really fired
    result = draft_review.review_draft(text, profile=_nsf_profile(), use_ai=False)
    pd = _by_id(result)["pappg_pd_impacts_header"]
    assert pd["status"] == "addressed", pd["note"]


def test_a_broader_impacts_subsection_is_still_located_for_its_own_rules():
    """Not truncating the parent must not make the sub-section vanish: rows
    filed under `broader_impacts` still need a span of their own, taken from
    INSIDE the Project Description rather than from the summary's heading."""
    profile = _nsf_profile()
    spans, _ = draft_review.locate_sections(
        COMPLIANT_PACKAGE, profile["sections"], use_ai=False)
    bi = spans.get("broader_impacts")
    assert bi is not None
    pd = spans["project_description"]
    assert pd["start"] <= bi["start"] < pd["end"], "sub-section resolved outside its parent"
    assert "broaden participation" in bi["text"]
    assert "undergraduates per year" not in bi["text"], "matched the summary's heading"


def test_the_project_description_span_is_not_doubled_by_the_fold_back():
    """`_project_description_span` concatenates Broader Impacts back onto the
    Project Description. Once Broader Impacts is nested INSIDE it, that text is
    already there and concatenating would repeat it — which
    `mechanical_checks._duplicate_paragraphs` would then report as a paragraph
    pasted twice."""
    profile = _nsf_profile()
    spans, _ = draft_review.locate_sections(
        COMPLIANT_PACKAGE, profile["sections"], use_ai=False)
    pd = draft_review._project_description_span(spans, profile["sections"])
    assert pd["text"].count("broaden participation") == 1


# ── B3: the Facilities rules must be locatable with no AI at all ────────────

def test_the_facilities_section_is_located_from_its_real_nsf_heading():
    """NSF's heading carries commas — "Facilities, Equipment and Other
    Resources" — but the section label was derived by title-casing the KEY, so
    the aliases read "facilities equipment and other resources" and never
    matched it. All four Facilities rules then told the PI to "give it a clear
    heading on its own line and re-run", four times, for a heading they had
    already written. The AI locate stage rescued it, so this failed exactly on
    the deterministic path golden rule 3 protects."""
    result = draft_review.review_draft(
        COMPLIANT_PACKAGE, profile=_nsf_profile(), use_ai=False)
    found = _by_id(result)

    fe = found["pappg_fe_no_financials"]
    assert fe["status"] == "clear", fe["note"]

    labels = {s["key"]: s["label"] for s in result["sections_located"]}
    assert labels.get(rulebook_baseline.FACILITIES) == \
        "Facilities, Equipment and Other Resources"


def test_a_facilities_heading_written_without_commas_still_locates():
    """Threading the authored label must not cost the derived one: a PI who
    types the heading without NSF's commas was located before this change and
    must still be."""
    text = COMPLIANT_PACKAGE.replace(
        "Facilities, Equipment and Other Resources",
        "Facilities Equipment and Other Resources")
    result = draft_review.review_draft(text, profile=_nsf_profile(), use_ai=False)
    assert _by_id(result)["pappg_fe_no_financials"]["status"] == "clear"
