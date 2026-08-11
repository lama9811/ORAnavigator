"""NSF 23-598 (HBCU Excellence in Research) as structured, quotable data.

TEST DATA. **No module under `backend/services/` imports this**, and none may —
it used to be `services/eir_solicitation.py`, the hardcoded solicitation that
made the draft reviewer work for exactly one funder. The engine now takes a
`solicitation_profile` dict as an argument, so this file survives only as a
fixture, and the engine cannot tell these rows from ones the extractor produced.

WHY IT IS KEPT AT ALL
---------------------
It is the only HUMAN-VERIFIED requirement list in the repo: every row was read
out of the published solicitation by a person and carries the VERBATIM sentence
it came from (`source`). That makes it the measuring stick for the extractor —
Task 8's recall test drives `solicitation_extractor` over the real NSF 23-598
PDF and checks how many of these requirements it recovers. Synthetic rows could
not do that job, because the question being asked is "does the model find what a
human found?".

It is also realistic input for the engine's own tests: real headings and real
requirement prose exercise the locate stage the way one-line stubs cannot.

Requirement rows are the fixed universe of asks. The model NEVER adds to it — it
only assigns coverage to rows defined here, so the AI can never invent an ask.

SOURCE: NSF 23-598, posted June 12 2023, replaces NSF 20-542. Verified against
https://www.nsf.gov/funding/opportunities/hbcu-eir-historically-black-colleges-universities-excellence-research/nsf23-598/solicitation
on 2026-08-10 (still the active solicitation; nothing has superseded it).
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

SOLICITATION_ID = "NSF 23-598"
SOLICITATION_TITLE = (
    "Historically Black Colleges and Universities - Excellence in Research (HBCU-EiR)"
)
SOLICITATION_URL = (
    "https://www.nsf.gov/funding/opportunities/"
    "hbcu-eir-historically-black-colleges-universities-excellence-research/"
    "nsf23-598/solicitation"
)
REPLACES = "NSF 20-542"
POSTED = "2023-06-12"

# Hard numbers, straight from the solicitation. Deterministic checks read these
# rather than repeating literals inline.
EQUIPMENT_MAX_PCT = 30.0          # "No more than 30% of the budget can be allocated for equipment."
INSTITUTIONAL_LETTER_MAX_PAGES = 2  # "This letter should be no more than 2 pages in length."
LOI_SYNOPSIS_MAX_CHARS = 2500     # "A project synopsis (no more than 2500 characters)"
TITLE_PREFIX = "Excellence in Research:"
COST_SHARING_PROHIBITED = True    # "Inclusion of voluntary committed cost sharing is prohibited."

# The exact wording NSF mandates for a letter of collaboration. Reproduced with
# the bracketed placeholders intact; the checker matches the invariant spine of
# the sentence, not this string literally (a real letter fills the brackets in).
COLLABORATION_LETTER_TEMPLATE = (
    "If the proposal submitted by Dr. [insert the full name of the Principal Investigator] "
    "entitled [insert the proposal title] is selected for funding by the NSF, it is my "
    "intent to collaborate and/or commit resources as detailed in the Project Description "
    "or the Facilities, Equipment or Other Resources section of the proposal."
)


# ── RECURRING DEADLINES ─────────────────────────────────────────────────────
# The solicitation states deadlines as RULES, not as a list of dates:
#   LOI  : "Second Thursday in July, Annually Thereafter"
#   Full : "Third Tuesday in October, Annually Thereafter"
# Computing them is the only way to be right in a year the published PDF never
# names. Worth having as code: the 2026 dates were misquoted in the request that
# prompted this feature (Oct 6 was given; the rule yields Oct 20).

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """The nth `weekday` (Mon=0 … Sun=6) of a month. n is 1-based."""
    first = _dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + _dt.timedelta(days=offset + 7 * (n - 1))


def loi_deadline(year: int) -> _dt.date:
    """Second Thursday in July of `year`. Required — no LOI, no full proposal."""
    return _nth_weekday(year, 7, 3, 2)   # Thursday = 3


def full_proposal_deadline(year: int) -> _dt.date:
    """Third Tuesday in October of `year`."""
    return _nth_weekday(year, 10, 1, 3)  # Tuesday = 1


def cycle_dates(year: int) -> dict:
    """Both deadlines for one competition year, ISO-formatted for the API."""
    return {
        "year": year,
        "loi_deadline": loi_deadline(year).isoformat(),
        "full_proposal_deadline": full_proposal_deadline(year).isoformat(),
        "loi_rule": "Second Thursday in July, annually",
        "full_proposal_rule": "Third Tuesday in October, annually",
    }


# ── SECTIONS THE REVIEWER TRIES TO LOCATE ───────────────────────────────────
# `aliases` are the headings a PI plausibly types. Used by the locate stage's
# deterministic fallback and to validate the model's section labels.
SECTIONS = {
    "project_summary": {
        "label": "Project Summary",
        "aliases": ["project summary", "summary", "abstract", "overview"],
    },
    "project_description": {
        "label": "Project Description",
        "aliases": ["project description", "project narrative", "research plan",
                    "narrative", "description"],
    },
    "broader_impacts": {
        "label": "Broader Impacts",
        "aliases": ["broader impacts", "broader impact"],
    },
    "institutional_support_letter": {
        "label": "Letter of Institutional Support",
        "aliases": ["letter of institutional support", "institutional support",
                    "letter of support from the dean", "departmental support"],
    },
    "collaboration_letters": {
        "label": "Letters of Collaboration",
        "aliases": ["letters of collaboration", "letter of collaboration",
                    "collaboration letters"],
    },
    "budget_justification": {
        "label": "Budget Justification",
        "aliases": ["budget justification", "budget narrative", "budget"],
    },
    "references": {
        "label": "References Cited",
        "aliases": ["references cited", "references", "bibliography",
                    "literature cited", "works cited"],
    },
}


# ── THE REQUIREMENT SET ─────────────────────────────────────────────────────
# kind:
#   "semantic"      -> a model reads the section and assigns coverage (must quote)
#   "deterministic" -> code decides; the model never sees it
# scored:
#   True  -> counts toward the completeness figure
#   False -> advisory only. Used for asks the solicitation itself makes
#            CONDITIONAL ("if available and appropriate", "if applicable").
#            Counting those against, say, a theory proposal with no wet-lab
#            preliminary data would penalise a compliant proposal.
# flag_if_present:
#   True  -> this is a PROHIBITION. Finding it is the failure, not missing it.

EIR_REQUIREMENTS: list[dict] = [
    # ---- Project Description: the ten enumerated elements (§V.A) ----
    {
        "id": "pd_research_goals",
        "label": "PI's overall research goals",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": "A brief description of the PI's overall research goals;",
        "why": "Reviewers want the arc of your research career, not just this one project.",
        "keywords": ["research goal", "long-term goal", "overall goal", "research program"],
    },
    {
        "id": "pd_institutional_context",
        "label": "Institutional context (faculty, teaching loads, students)",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": ("Information on the institutional context in which the work is being done "
                   "(e.g. number of faculty engaged in research, faculty teaching loads, "
                   "number of undergrads or/and graduate students);"),
        "why": ("EiR is a capacity-building program. The panel needs concrete numbers about "
                "your department to judge what this award would change. This is the element "
                "PIs most often skip entirely."),
        "keywords": ["teaching load", "faculty", "undergraduate", "graduate student",
                     "department", "course load"],
    },
    {
        "id": "pd_plan_of_work",
        "label": "General plan of work",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": ("An outline of the general plan of work, including the research questions "
                   "or hypotheses, the broad design of activities to be undertaken, and, "
                   "where appropriate, a description of experimental methods and procedures;"),
        "why": "The overall shape of what you will actually do.",
        "keywords": ["plan of work", "approach", "methods", "activities", "design"],
    },
    {
        "id": "pd_background",
        "label": "Background grounded in the literature",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": ("Background information that is firmly grounded in the literature with "
                   "enough information to understand how the proposed work will advance the field;"),
        "why": "Cited prior work showing where your project sits in the field.",
        "keywords": ["prior work", "literature", "previous studies", "et al", "background"],
    },
    {
        "id": "pd_research_questions",
        "label": "Fully articulated research questions or hypotheses",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": ("Fully articulated research questions and rationale or clearly stated "
                   "hypothesis the project will test;"),
        "why": "Stated explicitly, with the reasoning behind them — not implied by the methods.",
        "keywords": ["hypothesis", "hypotheses", "research question", "we ask", "rationale"],
    },
    {
        "id": "pd_preliminary_data",
        "label": "Preliminary data or proof of concept",
        "section": "project_description",
        # CONDITIONAL in the solicitation -> advisory, never scored.
        "kind": "semantic", "scored": False,
        "source": "Preliminary data or proof of concept work if available and appropriate;",
        "why": ("The solicitation says 'if available and appropriate', so this is not counted "
                "against you — but preliminary data strengthens a capacity-building proposal."),
        "keywords": ["preliminary data", "pilot", "proof of concept", "preliminary results"],
    },
    {
        "id": "pd_experimental_strategy",
        "label": "Experimental strategy with a timeline",
        "section": "project_description",
        # "if applicable" -> conditional. But the TIMELINE half is not optional in
        # practice, so it is called out in `why` rather than silently dropped.
        "kind": "semantic", "scored": False,
        "source": ("Experimental strategy, if applicable, that includes enough detail to "
                   "determine feasibility and scientific merit as well as a timeline of activities;"),
        "why": ("Marked 'if applicable', so not counted against you — but a timeline of "
                "activities is what lets a panel judge feasibility. Include one if you can."),
        "keywords": ["timeline", "year 1", "year one", "schedule", "milestone", "phase"],
    },
    {
        "id": "pd_sustainability_plan",
        "label": "Sustainability plan (path to an NSF core program)",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": ("A sustainability plan that describes how the proposed work could lead to "
                   "future submission to a current NSF core discipline specific research "
                   "funding opportunity;"),
        "why": ("EiR exists to get you to a core NSF program. This is the element that most "
                "distinguishes an EiR proposal from a generic one, and the one most often "
                "missing. Name the specific program you would submit to next."),
        "keywords": ["sustainability", "future submission", "core program", "next proposal",
                     "subsequent funding", "follow-on"],
    },
    {
        "id": "pd_success_metrics",
        "label": "How success will be determined (specific metrics)",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": ("Details on how success of the project will be determined, including "
                   "specific achievements or metrics; and"),
        "why": "Named, checkable outcomes — not 'the project will be successful if it works'.",
        "keywords": ["metric", "success", "evaluation", "assess", "measure", "milestone"],
    },
    {
        "id": "pd_dissemination",
        "label": "Plan for scholarly dissemination",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": "A plan for scholarly dissemination of this research.",
        "why": "Where the work goes: journals, conferences, public engagement.",
        "keywords": ["disseminat", "publish", "publication", "conference", "journal",
                     "present", "manuscript"],
    },

    # ---- The §II triple: what the Project Description must ADVANCE ----
    # Distinct from pd_research_goals. PIs routinely cover the first third and
    # assume the other two are implied.
    {
        "id": "pd_furthers_pi_research",
        "label": "How the project furthers the PI's own research",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": ("the project description should describe how the proposed project will "
                   "further the PI's research, improve research opportunities for students "
                   "and serve to improve research capacity at the institution."),
        "why": "One of three things §II requires you to state explicitly. This is the first.",
        "keywords": ["my research", "PI's research", "research trajectory", "career"],
    },
    {
        "id": "pd_student_opportunities",
        "label": "How it improves research opportunities for students",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": ("the project description should describe how the proposed project will "
                   "further the PI's research, improve research opportunities for students "
                   "and serve to improve research capacity at the institution."),
        "why": ("The second of the §II triple. Naming how many students, at what level, "
                "doing what, is much stronger than 'students will be involved'."),
        "keywords": ["student", "undergraduate", "graduate", "mentor", "train", "REU"],
    },
    {
        "id": "pd_institutional_capacity",
        "label": "How it improves research capacity at the institution",
        "section": "project_description",
        "kind": "semantic", "scored": True,
        "source": ("the project description should describe how the proposed project will "
                   "further the PI's research, improve research opportunities for students "
                   "and serve to improve research capacity at the institution."),
        "why": ("The third of the §II triple, and the reason the program exists. What is "
                "permanently different at Morgan after this award ends?"),
        "keywords": ["capacity", "institution", "infrastructure", "sustainable", "capability"],
    },

    # ---- Broader Impacts: a SEPARATELY LABELED section ----
    {
        "id": "broader_impacts_labeled",
        "label": "Broader Impacts as a separately labeled section",
        "section": "broader_impacts",
        "kind": "semantic", "scored": True,
        "source": ("The Project Description must include requirements outlined in the PAPPG, "
                   "including a separate section labeled Broader Impacts."),
        "why": ("'Separate' and 'labeled' are both literal. Broader impacts woven through the "
                "narrative without a heading is a compliance problem, not a style choice."),
        "keywords": ["broader impacts", "broader impact"],
    },

    # ---- Project Summary ----
    {
        "id": "summary_loi_number",
        "label": "Project Summary includes the LOI number",
        "section": "project_summary",
        "kind": "deterministic", "scored": True,
        "check": "loi_number",
        "source": ("The Project Summary must include the LOI number in addition to all the "
                   "requirements outlined in the PAPPG."),
        "why": ("An EiR-specific requirement that does not exist for most NSF programs, so it "
                "is easy to miss. The Letter of Intent is required, and its number belongs here."),
    },
    {
        "id": "title_prefix",
        "label": 'Title begins "Excellence in Research:"',
        "section": "project_summary",
        "kind": "deterministic", "scored": True,
        "check": "title_prefix",
        "source": ('The project title, beginning with "Excellence in Research:" followed by '
                   "the title."),
        "why": ("The solicitation states this for the Letter of Intent. Carrying the same "
                "prefix onto the full proposal is the convention EiR reviewers expect."),
    },

    # ---- Supplementary documents ----
    {
        "id": "institutional_support_letter",
        "label": "Letter of Institutional Support (chair, dean, or CAO)",
        "section": "institutional_support_letter",
        "kind": "semantic", "scored": True,
        "source": ("The proposal must include a letter by the chair, dean, or chief academic "
                   "officer of the primary PI. The letter ... should convey how the PI's "
                   "research and activities are supported by and advance research goals of the "
                   "department and the institution, and that the institution is committed to "
                   "the support, research progression, and professional development of the PI."),
        "why": ("Required, and graded: it is 'included as part of the consideration of the "
                "overall merits'. It must speak to departmental goals AND commitment to you."),
        "keywords": ["dean", "chair", "provost", "chief academic officer", "department head"],
    },
    {
        "id": "institutional_letter_length",
        "label": "Institutional support letter is 2 pages or fewer",
        "section": "institutional_support_letter",
        "kind": "deterministic", "scored": True,
        "check": "institutional_letter_length",
        "source": "This letter should be no more than 2 pages in length.",
        "why": "A hard cap. Estimated from word count when you paste text rather than a PDF.",
    },
    {
        "id": "collaboration_letter_format",
        "label": "Collaboration letters use NSF's exact single sentence",
        "section": "collaboration_letters",
        "kind": "deterministic", "scored": True,
        "check": "collaboration_letter_format",
        "source": ("Letters of collaboration should follow the single-sentence format: "
                   f'"{COLLABORATION_LETTER_TEMPLATE}"'),
        "why": ("NSF means single-sentence literally. A warm, detailed letter from a "
                "collaborator is a deviation, and reviewers do notice."),
    },
    {
        "id": "no_support_letters",
        "label": "No letters of support from collaborators",
        "section": None,   # scanned across the whole paste
        "kind": "deterministic", "scored": True,
        "check": "no_support_letters",
        "flag_if_present": True,
        "source": "Letters of support from collaborators, as defined in the NSF PAPPG, are not allowed.",
        "why": ("A prohibition, not a checklist item. Including an enthusiastic letter of "
                "support is a compliance violation — the single-sentence collaboration letter "
                "is the only permitted form."),
    },

    # ---- Budget ----
    {
        "id": "no_cost_sharing",
        "label": "No voluntary committed cost sharing offered",
        "section": None,
        "kind": "deterministic", "scored": True,
        "check": "no_cost_sharing",
        "flag_if_present": True,
        "source": "Inclusion of voluntary committed cost sharing is prohibited.",
        "why": ("Offering institutional match to look competitive is prohibited here and can "
                "get a proposal returned without review."),
    },
    {
        "id": "equipment_cap",
        "label": "Equipment is 30% or less of the budget",
        "section": "budget_justification",
        "kind": "deterministic", "scored": True,
        "check": "equipment_cap",
        "source": "No more than 30% of the budget can be allocated for equipment.",
        "why": ("Checked against this proposal's saved budget when one exists. EiR raised this "
                "cap to 30% — older EiR guidance said less."),
    },
    {
        "id": "dc_meeting_travel",
        "label": "Travel budgeted for the annual DC grantee meeting",
        "section": "budget_justification",
        "kind": "deterministic", "scored": True,
        "check": "dc_meeting_travel",
        "source": ("All proposals should budget for the PI to attend a two-day grantee meeting "
                   "in the Washington, DC area every year of the project."),
        "why": ("Every year of the project, not once. This is the most commonly omitted line "
                "in an EiR budget."),
    },
    {
        "id": "non_hbcu_subaward",
        "label": "Non-HBCU collaborators are subawards, not a substantial share",
        "section": "budget_justification",
        "kind": "semantic", "scored": False,
        "source": ("If the project involves a collaboration with a non-HBCU institution(s), the "
                   "budget for all non-HBCU partners must be well justified, may not total a "
                   "substantial portion of the overall budget, and must be in the form of a "
                   "subaward(s)."),
        "why": ("Advisory only: 'substantial portion' is not a number, so no honest automated "
                "check can call it. Flagged for a human — this is the question ORA's contracts "
                "manager will ask. HBCU-to-HBCU partners go as separate simultaneous "
                "submissions instead, NOT as subawards."),
        "keywords": ["subaward", "subcontract", "consortium", "partner institution"],
    },
]

# NSF's two National Science Board merit review criteria, verbatim. Used for the
# advisory reviewer notes — opinions, never coverage claims.
MERIT_CRITERIA = [
    {
        "criterion": "Intellectual Merit",
        "asks": "the potential to advance knowledge",
    },
    {
        "criterion": "Broader Impacts",
        "asks": ("the potential to benefit society and contribute to the achievement of "
                 "specific, desired societal outcomes"),
    },
]

# Eligibility facts a PI should be reminded of but that no draft text can prove.
ELIGIBILITY_NOTES = [
    "Proposals may only be submitted by accredited HBCUs. Morgan State qualifies.",
    "The PI must be a full-time faculty member or researcher at the submitting HBCU.",
    "An individual may serve as PI or co-PI on only ONE proposal per deadline.",
    "An individual may serve as PI or co-PI on a maximum of TWO HBCU-EiR awards in their career.",
    "A Letter of Intent is REQUIRED and is due months before the full proposal. "
    "Without one, the full proposal cannot be submitted.",
    "PIs with an established history of successful research funding should apply directly to "
    "NSF core programs rather than to EiR.",
]


def requirements_for(section: Optional[str]) -> list[dict]:
    """Requirement rows belonging to `section` (None -> whole-document checks)."""
    return [r for r in EIR_REQUIREMENTS if r.get("section") == section]


def scored_requirements() -> list[dict]:
    """Rows that count toward the completeness figure."""
    return [r for r in EIR_REQUIREMENTS if r.get("scored")]


def requirement_by_id(req_id: str) -> Optional[dict]:
    return next((r for r in EIR_REQUIREMENTS if r["id"] == req_id), None)
