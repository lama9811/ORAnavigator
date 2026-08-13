"""Requirements no DRAFT can satisfy, however well it is written.

Measured on a real proposal: of 29 requirements Draft Review assessed, SEVEN
were things a document cannot contain —

    Select secondary unit of consideration in Research.gov
    Submit collaborative proposals from separate organizations via Research.gov
    Ensure Letter of Intent is submitted by an Authorized Organizational Rep.
    List the PI as the primary point of contact on the Letter of Intent
    Limit individual PI or co-PI participation per deadline
    Limit career total HBCU-EiR awards per PI or co-PI
    Designate secondary unit of consideration

Every one came back `not_found`, counted against the draft, and appeared in
"Fix these first". A PI cannot fix a portal click by editing their Project
Description, and the score said 22% partly because of it.

They are still real requirements — the CHECKLIST is where they belong, and
`checklist_filter` already routes them there. This module only stops the DRAFT
reviewer from grading a document on them.

THE RISK IS OVER-EXCLUDING. Silently dropping a genuine content requirement is
worse than the noise it removes, because the PI would never learn it was
missing. So every rule below demands an explicit signal — a named submission
system, a named submitting officer, a registration, or an eligibility limit on a
PERSON — and prose about what the proposal must SAY is always kept.
"""
import pytest

from services import draft_scope as ds


# ── things a draft cannot satisfy ───────────────────────────────────────────

@pytest.mark.parametrize("label, source", [
    ("Select secondary unit of consideration in Research.gov",
     "Select the secondary unit of consideration in Research.gov under 'Manage Where to Apply'."),
    ("Submit collaborative proposals via Research.gov",
     "Separately submitted collaborative proposals must be submitted via Research.gov."),
    ("Ensure Letter of Intent is submitted by an Authorized Organizational Representative",
     "The Letter of Intent must be submitted by an Authorized Organizational Representative."),
    ("Register the organization in SAM.gov",
     "Organizations must complete registration in SAM.gov before submitting."),
    ("Limit individual PI or co-PI participation per deadline",
     "An individual may participate as PI or co-PI on no more than one proposal per deadline."),
    ("Limit career total HBCU-EiR awards per PI or co-PI",
     "An individual may receive no more than two awards over their career."),
])
def test_a_requirement_the_draft_cannot_satisfy_is_out_of_scope(label, source):
    assert ds.is_draft_checkable(label, source) is False


# ── things a draft CAN satisfy, which must never be excluded ────────────────

@pytest.mark.parametrize("label, source", [
    ("Include a separately labeled Broader Impacts section",
     "Broader Impacts must appear in a separately labeled section of the Project Description."),
    ("Describe how the project furthers the PI's research",
     "The project description should describe how the proposed project will further the PI's research."),
    ("Include a Data Management Plan",
     "A Data Management Plan of no more than two pages is required."),
    ("Limit the Project Description to 15 pages",
     "The Project Description is limited to 15 pages."),
    ("Cap equipment at 30% of the budget",
     "No more than 30% of the budget may be allocated to equipment."),
    ("Prohibit voluntary committed cost sharing",
     "Inclusion of voluntary committed cost sharing is prohibited."),
    ("Include a sustainability plan",
     "Include a sustainability plan describing how the work continues after the award."),
    # Names a document that IS submitted, and whose CONTENT the draft carries.
    ("Include Letter of Institutional Support",
     "The proposal must include a letter by the chair, dean, or chief academic officer."),
    # Mentions a portal in passing but asks for CONTENT.
    ("State the budget justification rationale",
     "The budget justification uploaded to Research.gov must explain each cost."),
])
def test_a_requirement_about_the_document_is_always_kept(label, source):
    assert ds.is_draft_checkable(label, source) is True


# ── applied to findings ─────────────────────────────────────────────────────

def _finding(**over):
    base = {"id": "r1", "label": "Select secondary unit in Research.gov",
            "section": None, "kind": "semantic", "scored": True,
            "prohibition": False, "status": "not_found", "note": "Not mentioned.",
            "evidence": "", "delegated_to": None,
            "solicitation_says": "Select the secondary unit of consideration in Research.gov.",
            "why": "", "source": "ai"}
    base.update(over)
    return base


def test_an_out_of_scope_row_stops_counting_against_the_draft():
    from services import draft_review as dre
    findings = dre.apply_draft_scope([
        _finding(id="portal"),
        _finding(id="real", label="Include a Data Management Plan",
                 solicitation_says="A Data Management Plan is required.",
                 status="addressed"),
    ])
    by_id = {f["id"]: f for f in findings}
    assert by_id["portal"]["status"] == "not_in_draft"
    assert by_id["real"]["status"] == "addressed", "a real content row is untouched"

    s = dre.score(findings)
    assert s["assessed"] == 1, "the portal row must leave the denominator"
    assert s["percent"] == 100


def test_the_row_says_where_it_IS_handled():
    """Excluding it silently would read as 'this does not matter'. It does — it
    is on the checklist, and the note has to say so."""
    from services import draft_review as dre
    out = dre.apply_draft_scope([_finding()])
    assert "checklist" in out[0]["note"].lower()


def test_a_row_already_delegated_is_left_alone():
    """`delegated` is a stronger statement (the rule is in a document we never
    read). Overwriting it would lose that."""
    from services import draft_review as dre
    out = dre.apply_draft_scope([_finding(status="delegated",
                                          delegated_to="the PAPPG")])
    assert out[0]["status"] == "delegated"


def test_recomputed_every_review_so_the_fix_is_retroactive():
    from services import draft_review as dre
    out = dre.apply_draft_scope([_finding()])
    assert out[0]["status"] == "not_in_draft"
