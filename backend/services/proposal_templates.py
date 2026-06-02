"""Proposal checklist templates.

When a user creates a Submission ("I'm submitting to NSF on June 15"),
the API seeds the SubmissionTasks from one of these templates so the
user lands on a populated checklist rather than an empty page. Templates
mirror Morgan State ORA's actual pre-award workflow + sponsor-specific
add-ons (NSF, NIH). The user can then add / edit / delete tasks freely.

`due_offset_days` is the recommended number of days BEFORE the
submission deadline this task should be completed. The endpoint maps
that to an absolute due_date on the SubmissionTask when seeding.

`kb_doc_id` links a task to its corresponding KB form / template so the
frontend can render an "Open form" inline link on each task row -- the
catalog already exposes the form URL.
"""

# Generic checklist used when sponsor isn't NSF/NIH (DoD, USDA, foundation,
# internal MSU funds, etc.). Mirrors the Basic Proposal Preparation
# Checklist linked from kb_structured/pre_award/proposal_submission_checklist/.
_GENERIC_CHECKLIST = [
    {
        "title": "Read the sponsor solicitation in full",
        "description": "Note the eligibility criteria, page limits, font/margin requirements, deadline (day AND time, including time zone), and any sponsor-specific forms.",
        "due_offset_days": 45,
        "kb_doc_id": None,
    },
    {
        "title": "Confirm PI / institutional eligibility",
        "description": "Match the solicitation's eligibility list against your faculty appointment and Morgan State's institutional status. If unsure, contact ORA pre-award.",
        "due_offset_days": 45,
        "kb_doc_id": None,
    },
    {
        "title": "Draft project narrative / research plan",
        "description": "Write the body of the proposal: aims, significance, approach, timeline, evaluation. Follow the sponsor's page limits and outline.",
        "due_offset_days": 21,
        "kb_doc_id": None,
    },
    {
        "title": "Build the proposal budget",
        "description": "Use the ORA budget template. Include direct costs (salary, fringe, supplies, travel, equipment, subawards) and apply Morgan State's federal F&A rate.",
        "due_offset_days": 14,
        "kb_doc_id": "form_budget_justification_template",
    },
    {
        "title": "Write the budget justification",
        "description": "Narrative explanation of every budget line. Sponsor reviewers expect this -- a budget without justification is incomplete.",
        "due_offset_days": 10,
        "kb_doc_id": "form_budget_justification_template",
    },
    {
        "title": "Collect biosketches for all senior personnel",
        "description": "Each senior person needs an up-to-date sponsor-format biosketch (NSF and NIH use different formats). Confirm format against the solicitation.",
        "due_offset_days": 14,
        "kb_doc_id": None,
    },
    {
        "title": "Gather letters of support / collaboration",
        "description": "Request letters from collaborators, subaward partners, and any required institutional officials early -- they take time.",
        "due_offset_days": 21,
        "kb_doc_id": None,
    },
    {
        "title": "Complete & sign the Internal Routing Form",
        "description": "ORA requires the Internal Routing Form (DocuSign) signed by the PI and department chair BEFORE the proposal goes to the sponsor.",
        "due_offset_days": 5,
        "kb_doc_id": "form_internal_routing_form_docusign",
    },
    {
        "title": "Final review with ORA pre-award",
        "description": "Submit the full package to ORA pre-award for institutional review at least 5 business days before the sponsor deadline.",
        "due_offset_days": 5,
        "kb_doc_id": "pre_award_proposal_submission_checklist",
    },
    {
        "title": "ORA submits to sponsor",
        "description": "ORA submits via the sponsor portal (Research.gov, eRA Commons, etc.). The PI does NOT submit directly -- only the AOR (Authorized Organization Representative) can.",
        "due_offset_days": 1,
        "kb_doc_id": None,
    },
]

_NSF_EXTRA = [
    {
        "title": "Draft the Data Management Plan (2 pages max)",
        "description": "NSF requires a 2-page Data Management Plan describing data types, storage, sharing, and retention. Required on all NSF proposals.",
        "due_offset_days": 21,
        "kb_doc_id": None,
    },
    {
        "title": "Draft Current & Pending Support for each senior person",
        "description": "NSF-format C&P listing every active and pending grant for each senior person on the proposal.",
        "due_offset_days": 14,
        "kb_doc_id": None,
    },
    {
        "title": "Draft Facilities, Equipment, and Other Resources",
        "description": "NSF-format description of Morgan State facilities, equipment, and other resources available to the project (no costs in this section).",
        "due_offset_days": 14,
        "kb_doc_id": None,
    },
    {
        "title": "Walk through the NSF EIR Proposal Preparation Checklist",
        "description": "NSF Education-related solicitations (e.g., EIR) have their own additional checklist. Confirm every item.",
        "due_offset_days": 7,
        "kb_doc_id": "form_nsf_eir_proposal_preparation_checklist",
    },
]

_NIH_EXTRA = [
    {
        "title": "Draft Specific Aims (1 page)",
        "description": "NIH requires a 1-page Specific Aims summary -- this is the most important page of the proposal. Reviewers read it first.",
        "due_offset_days": 28,
        "kb_doc_id": None,
    },
    {
        "title": "Confirm bibliography PMCID compliance",
        "description": "NIH requires PMCIDs on all NIH-funded publications cited. Run papers through PMC and fix any gaps -- missing PMCIDs delay funding.",
        "due_offset_days": 14,
        "kb_doc_id": None,
    },
    {
        "title": "Draft Authentication of Key Biological / Chemical Resources",
        "description": "NIH requires authentication for cell lines, antibodies, and other key resources used in the research. Short attachment, but required.",
        "due_offset_days": 14,
        "kb_doc_id": None,
    },
    {
        "title": "Confirm RCR training is current for all trainees",
        "description": "NIH requires Responsible Conduct of Research training for all trainees on the project. Verify with ORA / compliance before submission.",
        "due_offset_days": 14,
        "kb_doc_id": None,
    },
]


TEMPLATES = {
    "generic": _GENERIC_CHECKLIST,
    "NSF": _GENERIC_CHECKLIST + _NSF_EXTRA,
    "NIH": _GENERIC_CHECKLIST + _NIH_EXTRA,
}


def get_template(sponsor: str) -> list[dict]:
    """Return the seed checklist for a given sponsor. Unknown sponsors get
    the generic template. Returned list is a fresh copy -- callers can
    mutate it (e.g., add absolute due_date fields) without poisoning the
    module-level constant."""
    key = (sponsor or "").upper()
    if key in TEMPLATES:
        return [dict(t) for t in TEMPLATES[key]]
    return [dict(t) for t in TEMPLATES["generic"]]


def available_templates() -> list[str]:
    """Sponsor keys with their own checklist add-ons (for the UI's template
    picker)."""
    return ["generic", "NSF", "NIH"]
