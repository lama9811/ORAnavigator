"""Requirements the solicitation DELEGATES to a rulebook we never read.

The failure this removes, observed on a real proposal: NSF 23-598 states exactly
one rule about the Project Summary — "must include the LOI number in addition to
all the requirements outlined in the PAPPG" — so a five-line summary with no
Intellectual Merit or Broader Impacts statement was reported "Addressed". The
review had not judged the summary and approved it; it was never given the rule.
The PAPPG is where that rule lives, and nothing here reads the PAPPG.

Two shapes, and conflating them loses a real finding:

  POINTER-ONLY  "Adhere to PAPPG guidelines"  — the whole ask is "follow that
                document". We cannot assess it, so it must not be reported as a
                pass and must not sit in the score's denominator.
  RIDER         "Include the LOI number ... in addition to all the requirements
                outlined in the PAPPG" — a concrete, checkable ask that also
                cites a rulebook. Its status is REAL and must survive; it only
                gains a note that part of it defers elsewhere.
"""
import pytest

from services import delegated_rules as dr


# ── which rulebook, if any ──────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("Prepared in accordance with the PAPPG.", "the PAPPG"),
    ("as outlined in the PAPPG", "the PAPPG"),
    ("in accordance with PAPPG Chapter II.D.2", "the PAPPG"),
    ("see the NIH Grants Policy Statement for details", "the NIH Grants Policy Statement"),
    ("allowable costs are governed by 2 CFR 200", "2 CFR 200"),
    ("per the Uniform Guidance", "the Uniform Guidance"),
    # NSF's document is the Grants.gov APPLICATION Guide; solicitations write it
    # both ways, so both spellings match and the correct title is displayed.
    ("follow the Grants.gov Applicant Guide", "the Grants.gov Application Guide"),
    ("see the Grants.gov Application Guide", "the Grants.gov Application Guide"),
])
def test_a_named_rulebook_is_recognised(text, expected):
    assert dr.cited_rulebook(text) == expected


@pytest.mark.parametrize("text", [
    # Self-reference is NOT delegation — the rule is in the document we read.
    "as described in this solicitation",
    "see Section V.A of this funding opportunity",
    "described in the Project Description",
    # A plain requirement with no pointer at all.
    "The Project Description must not exceed 15 pages.",
    "Inclusion of voluntary committed cost sharing is prohibited.",
    "",
])
def test_a_rule_stated_here_is_not_delegated(text):
    assert dr.cited_rulebook(text) is None


# ── pointer-only vs rider ───────────────────────────────────────────────────

@pytest.mark.parametrize("label", [
    "Adhere to PAPPG guidelines",
    "Follow PAPPG guidelines",
    "Comply with the NIH Grants Policy Statement",
    "Prepare the budget in accordance with 2 CFR 200",
])
def test_a_pointer_only_row_is_recognised(label):
    assert dr.classify(label, label) == (dr.cited_rulebook(label), True)


def test_a_checkable_ask_that_merely_cites_a_rulebook_is_a_RIDER():
    """The row that started this. Its ask — the LOI number — is checkable, was
    checked, and came back not_found. Demoting it to "we can't assess this"
    would delete a true finding about the PI's draft."""
    label = "Include LOI number in Project Summary"
    source = ("PROJECT SUMMARY: The Project Summary must include the LOI number in "
              "addition to all the requirements outlined in the PAPPG.")
    target, pointer_only = dr.classify(label, source)
    assert target == "the PAPPG"
    assert pointer_only is False


def test_a_compliance_verb_with_no_rulebook_is_not_delegated():
    """"Comply with the 30% equipment cap" is OUR rule to check, not someone
    else's document. The rulebook is what makes a row delegated, not the verb."""
    assert dr.classify("Comply with the 30% equipment cap",
                       "No more than 30% may be equipment.") == (None, False)


# ── applying it to findings ─────────────────────────────────────────────────

def _finding(**over):
    base = {"id": "r1", "label": "Adhere to PAPPG guidelines", "section": None,
            "kind": "semantic", "scored": True, "prohibition": False,
            "status": "addressed", "note": "Looks fine.", "evidence": "",
            "solicitation_says": "Proposals must be prepared in accordance with the PAPPG.",
            "why": "", "source": "ai"}
    base.update(over)
    return base


def test_a_pointer_only_finding_stops_claiming_to_be_addressed():
    from services import draft_review as dre
    out = dre.apply_delegation([_finding()])
    assert out[0]["status"] == "delegated"
    assert out[0]["delegated_to"] == "the PAPPG"
    assert "PAPPG" in out[0]["note"]


def test_a_delegated_finding_is_OUT_of_the_score_denominator():
    """Scoring a rule we never read would be a number about our own coverage
    dressed up as a verdict on the draft — the same reason could_not_locate and
    unclear are excluded."""
    from services import draft_review as dre
    findings = dre.apply_delegation([
        _finding(id="r1"),                                   # delegated
        _finding(id="r2", label="Include a Data Management Plan",
                 solicitation_says="A Data Management Plan is required.",
                 status="addressed"),                        # normal, scored
    ])
    s = dre.score(findings)
    assert s["assessed"] == 1, "the delegated row must not be counted"
    assert s["percent"] == 100


def test_a_rider_keeps_its_real_status_and_only_gains_a_note():
    from services import draft_review as dre
    row = _finding(
        id="loi", label="Include LOI number in Project Summary", status="not_found",
        note="No LOI number found.",
        solicitation_says=("The Project Summary must include the LOI number in addition "
                           "to all the requirements outlined in the PAPPG."))
    out = dre.apply_delegation([row])
    assert out[0]["status"] == "not_found", "a real finding must not be swallowed"
    assert out[0]["delegated_to"] == "the PAPPG"


def test_a_finding_that_delegates_nowhere_is_untouched():
    from services import draft_review as dre
    row = _finding(id="eq", label="Cap equipment at 30%", status="clear",
                   solicitation_says="No more than 30% may be equipment.")
    out = dre.apply_delegation([dict(row)])
    assert out[0]["status"] == "clear"
    assert out[0]["delegated_to"] is None


def test_delegation_is_recomputed_on_every_review_not_stored():
    """Same rule as compliance_sentinel's verdicts: improving the detector must
    retroactively fix every proposal already in the database, including the ones
    whose requirements were extracted before this existed."""
    from services import draft_review as dre
    row = _finding()
    row.pop("delegated_to", None)          # a finding from a pre-existing profile
    out = dre.apply_delegation([row])
    assert out[0]["delegated_to"] == "the PAPPG"


# ── the notice the PI actually reads ────────────────────────────────────────

def test_each_rulebook_carries_a_plain_english_description():
    """This product is for PIs who have not written a proposal before. Naming
    "the PAPPG" at someone who does not know what that is explains nothing —
    the notice built on these names was read back to us as "what does this
    mean?"."""
    assert "Proposal and Award Policies" in dr.describe("the PAPPG")
    assert dr.describe("something we do not know") == ""


def test_the_summary_separates_unchecked_rows_from_riders():
    from services import draft_review as dre
    findings = dre.apply_delegation([
        _finding(id="a"),                                       # pointer-only
        _finding(id="b", label="Include LOI number", status="not_found",
                 solicitation_says="Include the LOI number in addition to all the "
                                   "requirements outlined in the PAPPG."),
    ])
    rows = dr.summarize(findings)
    assert len(rows) == 1
    assert rows[0]["name"] == "the PAPPG"
    assert rows[0]["total"] == 2
    assert rows[0]["unchecked"] == 1, "only the pointer-only row is unchecked"


def test_two_rulebooks_are_reported_separately_never_blended():
    """The accuracy bug this replaces: one sentence naming both told a PI that
    the Build America, Buy America Act sets "section structure, length,
    formatting". It governs what an award may BUY."""
    from services import draft_review as dre
    findings = dre.apply_delegation([
        _finding(id="a"),
        _finding(id="b", label="Comply with Build America, Buy America",
                 solicitation_says="All iron and steel must be produced domestically "
                                   "under Build America, Buy America."),
    ])
    rows = dr.summarize(findings)
    assert {r["name"] for r in rows} == {
        "the PAPPG", "the Build America, Buy America Act"}
    baba = next(r for r in rows if "Buy America" in r["name"])
    assert "BUY" in baba["description"] or "buy" in baba["description"]


def test_no_delegation_means_no_notice():
    assert dr.summarize([]) == []


def test_a_rulebook_link_is_only_offered_when_it_has_been_verified():
    """A 404 here is worse than no link: this notice exists BECAUSE the rules are
    somewhere the PI has to go and read, so sending them nowhere is the one
    outcome that defeats it."""
    assert dr.url_for("the PAPPG").startswith("https://www.nsf.gov/policies/pappg")
    assert dr.url_for("the Build America, Buy America Act") == ""
    assert dr.url_for("something we do not know") == ""


# ── rulebooks the whole solicitation points at ──────────────────────────────

def test_a_rulebook_linked_but_not_named_is_still_found():
    """The link is often nowhere near the sentence that imposes the rule, and a
    solicitation can point at a rulebook by URL without naming it in prose."""
    text = "See https://www.nsf.gov/publications/pub_summ.jsp?ods_key=pappg for details."
    books = dr.rulebooks_in(text)
    assert [b["name"] for b in books] == ["the PAPPG"]
    assert books[0]["cited_url"].endswith("ods_key=pappg")
    assert books[0]["named"] is False


def test_both_url_forms_resolve_to_the_same_rulebook():
    """NSF 23-598 uses the legacy pub_summ link; the current site uses
    /policies/pappg. Matching the RULEBOOK rather than one URL is what makes
    this work across both."""
    for url in ("https://www.nsf.gov/publications/pub_summ.jsp?ods_key=pappg",
                "https://www.nsf.gov/policies/pappg"):
        assert [b["name"] for b in dr.rulebooks_in(url)] == ["the PAPPG"]


def test_links_that_are_not_rulebooks_are_ignored():
    """THE guard. Measured on NSF 23-598: 13 distinct URLs, 2 rulebooks. Crawling
    the rest would turn nsf.gov's homepage and a Senate report into
    'requirements'."""
    text = ("Search awards at https://www.nsf.gov/awardsearch/advancedSearch.jsp "
            "and submit via https://www.research.gov/research-portal/ or "
            "https://www.grants.gov. See https://congress.gov/congressional-report/"
            "115th-congress/senate-report/139/1 for background.")
    assert dr.rulebooks_in(text) == []


def test_a_rulebook_named_in_prose_with_no_link_is_found():
    books = dr.rulebooks_in("Proposals must be prepared in accordance with the PAPPG.")
    assert [b["name"] for b in books] == ["the PAPPG"]
    assert books[0]["named"] is True
    assert books[0]["cited_url"] == ""


def test_an_empty_document_points_nowhere():
    assert dr.rulebooks_in("") == []


# ── what the baseline now covers ────────────────────────────────────────────

def test_the_notice_names_the_sections_the_baseline_now_checks():
    """The caveat must shrink as coverage grows. Telling a PI 'the PAPPG is not
    checked here' when four of its sections now ARE is the same dishonesty as
    the badge that read 'attached' for a proposal with rules only."""
    findings = [{"id": "r1", "label": "Adhere to PAPPG guidelines",
                 "status": "delegated",
                 "source_text": "Adhere to PAPPG guidelines."}]
    rows = dr.summarize(findings, covered={
        "the PAPPG": ["Project Summary", "Project Description"]})
    assert rows and rows[0]["name"] == "the PAPPG"
    assert rows[0]["covered_sections"] == ["Project Summary", "Project Description"]


def test_coverage_is_per_rulebook_not_stamped_on_every_row():
    """We hold the PAPPG's rules. We hold NOTHING of the Build America, Buy
    America Act — and a solicitation citing both would have claimed we check
    BABA's Project Summary rules, because the same list was stamped onto every
    rulebook row. A caveat that names the wrong document is worse than no
    caveat: it tells the PI a part of their proposal is covered when nothing
    read the law that governs it."""
    findings = [
        {"id": "r1", "label": "Adhere to PAPPG guidelines", "status": "delegated",
         "source_text": "Adhere to PAPPG guidelines."},
        {"id": "r2", "label": "Comply with the Build America, Buy America Act",
         "status": "delegated",
         "source_text": "Comply with the Build America, Buy America Act."},
    ]
    rows = {r["name"]: r for r in dr.summarize(findings, covered={
        "the PAPPG": ["Project Summary", "Project Description"]})}
    assert rows["the PAPPG"]["covered_sections"] == [
        "Project Summary", "Project Description"]
    assert rows["the Build America, Buy America Act"]["covered_sections"] == []


def test_the_covered_map_is_built_from_the_rows_own_rulebook():
    """rulebook_baseline names the sections; the mapping must come from each
    row's OWN `rulebook`, so a second rulebook's rows can never be filed under
    the first."""
    from services import rulebook_baseline
    covered = rulebook_baseline.covered_sections([
        {"rulebook": "the PAPPG", "section": "project_description"},
        {"rulebook": "the PAPPG", "section": "project_summary"},
        {"rulebook": "the NIH Grants Policy Statement", "section": "references_cited"},
        {"rulebook": None, "section": "project_summary"},        # solicitation row
        {"rulebook": "the PAPPG", "section": None},              # whole-document
    ])
    # Research.gov's order, not the alphabet — the order a PI meets them.
    assert covered["the PAPPG"] == ["Project Summary", "Project Description"]
    assert covered["the NIH Grants Policy Statement"] == ["References Cited"]


def test_with_nothing_covered_the_notice_is_unchanged():
    findings = [{"id": "r1", "label": "Adhere to PAPPG guidelines",
                 "status": "delegated",
                 "source_text": "Adhere to PAPPG guidelines."}]
    rows = dr.summarize(findings)
    assert rows[0]["covered_sections"] == []


def test_a_pointer_only_row_stays_delegated_even_when_covered():
    """We hold four sections' rules, not the whole PAPPG. 'Adhere to PAPPG
    guidelines' is still an ask we cannot assess in full."""
    from services import draft_review
    findings = [{"id": "r1", "label": "Adhere to PAPPG guidelines",
                 "status": "not_found", "note": "", "scored": True,
                 "source_text": "Adhere to PAPPG guidelines."}]
    out = draft_review.apply_delegation(findings)
    assert out[0]["status"] == "delegated"


def test_a_baseline_row_is_never_demoted_by_delegation():
    """A baseline row's own quote names the PAPPG. If apply_delegation treated
    it as pointer-only it would delete the very finding this feature adds.

    The label is deliberately phrased with a compliance verb ("Comply with...")
    so that, absent the `rulebook` guard, `classify()` would read it as
    POINTER-ONLY (pointer_only is decided purely off the LABEL's leading verb
    — see `_POINTER_ONLY_LABEL`) and demote it to `delegated`. None of the 14
    real baseline row labels happen to start that way today, so a fixture
    copying one of them verbatim would pass whether or not the guard exists —
    this phrasing is what actually exercises the risk the guard defends
    against, for any baseline row a future rulebook might add."""
    from services import draft_review
    findings = [{
        "id": "pappg_ps_headings",
        "label": "Comply with the PAPPG's three-heading requirement for the "
                  "Project Summary",
        "status": "not_found",
        "note": "None of those appears as a heading.", "scored": True,
        "source": "check", "rulebook": "the PAPPG",
        "solicitation_says": "Your file must include three separate section "
            "headers: Overview, Intellectual Merit, and Broader Impacts.",
    }]
    out = draft_review.apply_delegation(findings)
    assert out[0]["status"] == "not_found"


def test_a_baseline_row_never_lands_in_the_not_checked_summary():
    """summarize()'s own cited_rulebook(label) fallback (added above, for a
    caller that never ran apply_delegation) is exactly the mechanism that could
    misfire on a baseline row: apply_delegation's guard leaves `delegated_to`
    unset for a baseline row, so summarize() falls back to reading the
    rulebook's name out of the LABEL -- and if a baseline row's label happens
    to name its own rulebook (no current PAPPG row does; a future rulebook's
    might), it would be folded into the "not checked" summary for a rule we
    just checked and scored. summarize() must skip any row carrying `rulebook`
    outright, same guard as apply_delegation, regardless of what its label
    says."""
    findings = [{
        "id": "future_row", "label": "Comply with the PAPPG's page limit",
        "status": "addressed", "scored": True, "rulebook": "the PAPPG",
        "solicitation_says": "Some rule this baseline already checked.",
    }]
    assert dr.summarize(findings) == []
