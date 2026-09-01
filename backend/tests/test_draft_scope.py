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


# ── TWO MORE THINGS A PROPOSAL DOCUMENT CANNOT CONTAIN (2026-09-01) ─────────
#
# Measured on the AWARDED NSF EiR package, uploaded as its 11 real section files:
# 76%, and 14 scored rules lost points. Only about two of the fourteen were fair.
# Four of them were these two families:
#
#   "Submit annual project report prior to budget period end"
#   "Submit final project report and outcomes report following expiration"
#   "Letter of Intent included"
#   "List PI as point of contact on Letter of Intent"
#
# A POST-AWARD REPORT is due after an award that does not exist yet -- no draft
# can contain one, and no PI can act on it while writing. A LETTER OF INTENT is a
# SEPARATE submission with an earlier deadline; by the time a package exists the
# LOI is months gone, so counting it means EVERY proposal to this solicitation
# loses those points permanently.
#
# Both become `not_in_draft`: absent from _CREDIT, so out of the denominator,
# with a note naming where they ARE handled. The risk in this module is
# OVER-excluding (its docstring says so), so both patterns are narrow and both
# have a mirror test below proving a real content rule still survives.

def test_a_post_award_report_is_not_scored_against_the_draft():
    assert not ds.is_draft_checkable(
        "Submit annual project report prior to budget period end",
        "Submit an annual project report 90 days prior to the end of the budget period.")


def test_a_final_report_after_expiration_is_not_scored_against_the_draft():
    assert not ds.is_draft_checkable(
        "Submit final project report and outcomes report",
        "Submit a final project report and a project outcomes report following "
        "expiration of the award.")


def test_describing_a_reporting_PLAN_in_the_draft_still_counts():
    """The mirror. A solicitation asking the Project Description to SAY how
    results will be reported is a content rule, and dropping it would hide a
    real gap -- the failure this module's docstring calls its dangerous
    direction."""
    assert ds.is_draft_checkable(
        "Describe the plan for reporting results in the Project Description",
        "The Project Description must describe how findings will be reported "
        "and disseminated.")


# The Letter of Intent is PACKAGE-scoped, not rule-scoped, and the difference is
# load-bearing: `apply_draft_scope` runs in BOTH review_draft and review_section,
# so excluding LOI rules outright would leave a PI who opens Check a Section on
# their Letter of Intent with every rule reading "Done at submission". The letter
# is perfectly checkable ON ITS OWN; it simply is not part of a package.

def test_a_letter_of_intent_rule_is_dropped_from_a_PACKAGE_review():
    rows = ds.apply_package_scope([
        {"id": "a", "section": "letter_intent", "label": "Letter of Intent included",
         "status": "not_found", "scored": True, "note": "n"},
    ])
    assert rows[0]["status"] == "not_in_draft"
    assert "separate" in rows[0]["note"].lower()


def test_the_same_rule_is_untouched_when_checking_the_letter_itself():
    """Section Check on the Letter of Intent must still assess its own rules."""
    rows = [{"id": "a", "section": "letter_intent", "label": "Letter of Intent included",
             "status": "not_found", "scored": True, "note": "n"}]
    assert ds.apply_package_scope(rows, section="letter_intent")[0]["status"] == "not_found"


def test_package_scope_leaves_every_other_section_alone():
    rows = ds.apply_package_scope([
        {"id": "b", "section": "project_summary", "label": "x",
         "status": "not_found", "scored": True, "note": "n"},
    ])
    assert rows[0]["status"] == "not_found"


def test_naming_the_LOI_number_inside_another_section_still_counts():
    """The mirror. NSF 23-598 requires the Project SUMMARY to carry the LOI
    number -- that is text in the package and must stay scored."""
    rows = ds.apply_package_scope([
        {"id": "c", "section": "project_summary",
         "label": "Include LOI number in Project Summary",
         "status": "not_found", "scored": True, "note": "n"},
    ])
    assert rows[0]["status"] == "not_found"
