"""The reviewed PAPPG table, and the four ways wiring it in could go wrong quietly.

Every test here was observed RED before the code existed. That matters more than
usual: CLAUDE.md records that `build_generic` had NO test caller at all before
the baseline shipped, so the whole backend suite passed the moment the injection
landed — guaranteed, whether or not the injection worked.
"""
import json
import os

import pytest

from services import rulebook_baseline as rb
from services import rulebook_checks
from services import draft_review
from services.text_match import quote_in

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "kb_structured")


def _slices():
    with open(os.path.join(KB, "_pappg_24_1_sections.json"), encoding="utf-8") as fh:
        return {s["section_key"]: s["text"] for s in json.load(fh)["sections"]}


def _reviewed():
    with open(os.path.join(KB, "_pappg_24_1_rules.json"), encoding="utf-8") as fh:
        return json.load(fh)


PAPPG = "the PAPPG"


# ── the table is actually loaded ────────────────────────────────────────────

def test_the_reviewed_rules_reach_RULES():
    """Wiring a file in and not reading it is the failure this whole module had
    once already. Count the reviewed rows, then count them in RULES."""
    reviewed = _reviewed()["rules"]
    assert len(reviewed) > 100, "the reviewed table should be the PAPPG, not a stub"
    ids = {r["id"] for r in rb.RULES[PAPPG]}
    missing = [r["id"] for r in reviewed if r["id"] not in ids]
    assert not missing, f"{len(missing)} reviewed rules never reached RULES: {missing[:5]}"


def test_the_curated_rules_survive_the_merge():
    """The 14 hand-curated rows carry every deterministic check. A merge that
    replaced them with extracted look-alikes would swap code for model opinion."""
    ids = {r["id"] for r in rb.RULES[PAPPG]}
    for curated in ("pappg_ps_headings", "pappg_ps_one_page", "pappg_pd_impacts_header",
                    "pappg_pd_no_urls", "pappg_pd_page_limit", "pappg_rc_et_al",
                    "pappg_fe_no_financials", "pappg_fe_unfunded"):
        assert curated in ids, f"curated rule {curated} was lost in the merge"


def test_no_duplicate_ids():
    ids = [r["id"] for r in rb.RULES[PAPPG]]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate rule ids: {dupes}"


# ── golden rule 2 ───────────────────────────────────────────────────────────

def test_every_extracted_rule_quotes_the_pappg_verbatim():
    """The quote gate, as a REGRESSION guard rather than a filter: it passed
    161/161 before any of this. A re-extraction is exactly when it would
    silently stop passing."""
    slices = _slices()
    text = "\n".join(slices.values())
    bad = [r["label"] for r in _reviewed()["rules"]
           if not quote_in(text, r["source"], drop_list_noise=True)]
    assert not bad, f"{len(bad)} rules quote something not in the PAPPG: {bad[:5]}"


# ── the sections table ──────────────────────────────────────────────────────

def test_every_section_in_RULES_has_a_label_and_a_slot_in_the_order():
    """`sections_offered` builds from _SECTION_ORDER and looks the label up in
    _SECTION_LABELS. A section present in RULES but missing from either is
    dropped from the picker with NOTHING said — the silent degradation
    CLAUDE.md flags as the cost of adding sections."""
    have = {r["section"] for r in rb.RULES[PAPPG]}
    assert not (have - set(rb._SECTION_LABELS)), \
        f"sections with no label: {have - set(rb._SECTION_LABELS)}"
    assert not (have - set(rb._SECTION_ORDER)), \
        f"sections with no order slot: {have - set(rb._SECTION_ORDER)}"


def test_every_offered_section_actually_has_rules():
    for s in rb.sections_offered(PAPPG):
        assert rb.rules_for(PAPPG, s["key"]), f"{s['key']} is offered but has no rules"


def test_a_row_carries_the_real_section_name_not_a_title_cased_key():
    """Title-casing `facilities_equipment_and_other_resources` gives a name with
    no commas, and NSF's own heading has them. That mismatch silently disabled
    four rules once already."""
    for r in rb.RULES[PAPPG]:
        assert r["section_label"], f"{r['id']} has no section_label"
        assert r["section_label"] == rb._SECTION_LABELS[r["section"]]


# ── placement ───────────────────────────────────────────────────────────────

def test_rules_the_pappg_states_elsewhere_file_where_they_are_checked():
    """The PAPPG states a rule where the SITUATION arises; Research.gov states it
    where you NEED it. The unfunded-personnel rule is in the Budget section and
    governs Facilities; a rule filed under the slice it was read from lands in a
    section it does not govern and can never be located."""
    from services.pappg_ingest import _pappg_id

    moved = [r for r in _reviewed()["rules"] if r.get("moved_from")]
    assert moved, "no rule was re-placed; the placement pass did nothing"
    for r in moved:
        assert r["section"] != r["moved_from"]
        # The id must be the one its DESTINATION would mint. Asserted against
        # _pappg_id rather than a "pappg_<section>_" prefix, because make_id
        # canonicalises the section on the way in — `facilities_equipment_and_
        # other_resources` becomes `facilitie_equipment_other_resource`. That is
        # the canon_section/section_key divergence CLAUDE.md records; a prefix
        # test would encode the wrong one of the two.
        assert r["id"] == _pappg_id(r["section"], r["label"]), \
            f"{r['id']} still reads as belonging to {r['moved_from']}"
        assert _pappg_id(r["moved_from"], r["label"]) != r["id"]


# ── the honest status for what a paste cannot carry ─────────────────────────

def test_a_rule_a_paste_cannot_carry_is_never_reported_missing():
    """A margin, a font and a Research.gov form field are not properties of
    pasted text. Reporting them `not_found` would fail a fully compliant draft
    — presence-rendered-as-verdict, which this repo has unshipped three times."""
    row = {"check_args": {"section": "format_of_the_proposal",
                          "handled_by": "the PDF you upload"}}
    status, detail, evidence = rulebook_checks.rb_not_in_text({"text": "anything"}, row)
    assert status == "not_checked"
    assert "the PDF you upload" in detail
    assert evidence == ""


def test_not_checked_stays_out_of_the_score():
    """`not_checked` is absent from _CREDIT, so these rows leave the denominator.
    If that ever changes, 48 rules nothing looked at start moving the number."""
    assert "not_checked" not in draft_review._CREDIT


def test_every_unverifiable_rule_names_the_tool_that_does_handle_it():
    """'Handled elsewhere' with no address is the same dead end as 'Not checked'
    reading as our omission. Every one must name a real tool."""
    for r in _reviewed()["rules"]:
        if r.get("check") == "rb_not_in_text":
            by = (r.get("check_args") or {}).get("handled_by")
            assert by and len(by) > 10, f"{r['id']} does not say who handles it"


def test_the_whole_format_and_cover_sheet_sections_are_unverifiable():
    """Not one rule in either section is a property of text. If a future
    re-extraction adds one that IS scored, this goes red and a human looks."""
    for section in ("format_of_the_proposal", "cover_sheet"):
        rows = rb.rules_for(PAPPG, section)
        assert rows, f"{section} has no rules"
        assert all(r["check"] == "rb_not_in_text" for r in rows), \
            f"a {section} rule is being scored against pasted text"


# ── the source upgrade ──────────────────────────────────────────────────────

def test_the_three_derived_project_summary_rows_now_quote_nsf():
    """Their source used to read 'Derived from ... NSF's own wording is in the
    PAPPG'. It is, and this is it. Quoting NSF where NSF says it is golden
    rule 2's whole point."""
    slices = _slices()
    by_id = {r["id"]: r for r in rb.RULES[PAPPG]}
    for rid in ("pappg_ps_overview", "pappg_ps_merit", "pappg_ps_impacts"):
        src = by_id[rid]["source"]
        assert not src.startswith("Derived from"), f"{rid} still carries a derived line"
        assert quote_in(slices["project_summary"], src, drop_list_noise=True), \
            f"{rid}'s new source is not verbatim in the PAPPG Project Summary slice"


# ── the duplicates really are gone ──────────────────────────────────────────

@pytest.mark.parametrize("label", [
    "Include overview, intellectual merit, and broader impacts in Project Summary",
    "Do not include URLs in Project Description",
    "Limit Project Description to 15 pages",
    "Do not include quantifiable financial information in Facilities section",
])
def test_a_rule_the_curated_table_decides_in_code_is_not_also_a_model_opinion(label):
    """Two rows for one rule, one decided by code and one by the model, can
    disagree on screen in front of the PI."""
    assert not [r for r in rb.RULES[PAPPG] if r["label"] == label]


# ── one part of a proposal, named two ways ──────────────────────────────────

def test_a_rule_files_under_the_section_key_that_survived_the_merge():
    """`sections_from` already merges two names for one part of a proposal —
    that is what stopped a present Budget Justification being reported missing.
    But it merges the SECTION UNIVERSE, keeping whichever key was already in
    use, and the ROWS pointing at the key that lost were left behind.

    Live consequence, measured on a real proposal before this was fixed: the
    solicitation's own rows canonicalise to `budget_justification` (canon_section
    strips "and"), the PAPPG's rows to `budget_and_budget_justification`
    (section_key does not). The universe merged to the first; 45 PAPPG rules kept
    pointing at the second, which was no longer a section at all — so every one
    of them reported "Not located" and left the score's denominator. Recovered
    and then never checked, which is worse than not having them, because the row
    looks handled.

    Same failure family as the Budget Justification attachment declared missing
    while sitting in the draft. Fixed once, at the profile level, so it holds for
    the next rulebook too.
    """
    from services.solicitation_profile import build_generic

    # A solicitation whose own row uses the canon_section spelling, PLUS a row
    # citing the PAPPG — without the citation no baseline rows are injected at
    # all and the clash cannot happen.
    extracted = [{
        "id": "sol_bj_1", "section": "budget_justification",
        "label": "Explain each cost", "source": "The budget justification must "
        "explain each cost.", "kind": "semantic", "scored": True,
    }, {
        "id": "sol_pappg", "section": "", "label": "Follow the PAPPG",
        "source": "Proposals must be prepared in accordance with the NSF "
                  "Proposal & Award Policies & Procedures Guide (PAPPG).",
        "kind": "semantic", "scored": True,
    }]
    profile = build_generic(
        id="TEST-1", title="t", url="",
        requirements=extracted,
        contract={"required_attachments": ["Budget and Budget Justification"]},
    )
    sections = profile["sections"]
    budgetish = [k for k in sections if "budget" in k]
    assert len(budgetish) == 1, f"one part of the proposal became {len(budgetish)} sections: {budgetish}"
    survivor = budgetish[0]

    orphans = [r["id"] for r in profile["requirements"]
               if r.get("section") and "budget" in r["section"]
               and r["section"] != survivor]
    assert not orphans, (
        f"{len(orphans)} rules point at a section key that lost the merge and "
        f"therefore can never be located: {orphans[:3]}")


def test_the_remap_never_invents_a_section_that_is_not_in_the_universe():
    """The remap must only ever move a row ONTO an existing section. A row whose
    section is genuinely unknown keeps what it had — silently re-filing it would
    be the over-reach `canon_section`'s deny-list was judged too risky for."""
    from services.solicitation_profile import build_generic

    profile = build_generic(
        id="TEST-2", title="t", url="",
        requirements=[{"id": "x", "section": "a_section_no_draft_has",
                       "label": "L", "source": "S", "kind": "semantic", "scored": True}],
        contract={},
    )
    row = next(r for r in profile["requirements"] if r["id"] == "x")
    assert row["section"] in profile["sections"] or row["section"] == "a_section_no_draft_has"


# ── what the section picker offers ──────────────────────────────────────────

def test_the_picker_never_offers_a_section_where_nothing_can_be_checked():
    """Section Check asks a PI to paste one section and get it checked. Cover
    Sheet and Format of the Proposal hold nine rules each and not one is a
    property of text, so picking either returns nine rows of "not checked here"
    and nothing else — a dead end dressed up as a tool.

    They are NOT dropped from RULES: in a full Draft Review they are worth
    stating, because a PI who has never seen the font rules should meet them
    once. This filters the PICKER, not the rulebook — the same distinction
    `checklist_filter` draws when it keeps 7 of 24 requirements as tick-boxes
    while the stored profile keeps all 24.
    """
    offered = {s["key"] for s in rb.sections_offered(PAPPG)}
    for dead in ("cover_sheet", "format_of_the_proposal"):
        assert rb.rules_for(PAPPG, dead), f"{dead} should still hold rules"
        assert dead not in offered, \
            f"{dead} is offered, but every one of its rules reports not_checked"


def test_the_picker_offers_every_section_with_a_checkable_BASIC_rule():
    """Check a Section reviews the rulebook's BASIC rows plus the solicitation
    (product decision 2026-08-26), so its rulebook-only picker offers exactly
    the sections holding a checkable basic rule.

    The four absent here -- budget, senior/key personnel, special information,
    and the three already excluded -- keep every one of their extended rules
    for a full Draft Review, and reappear in THIS picker the moment a
    solicitation gives them rules of its own. See
    test_section_check_basics_only.py.
    """
    offered = {s["key"] for s in rb.sections_offered(PAPPG)}
    for live in ("project_summary", "project_description", "references_cited",
                 "facilities_equipment_and_other_resources"):
        assert live in offered, f"{live} has checkable basic rules but is not offered"
    for extended_only in ("budget_and_budget_justification",
                          "senior_key_personnel_documents",
                          "special_information_and_supplementary_documentation"):
        assert extended_only not in offered, (
            f"{extended_only} holds no basic rules, so offering it on the "
            f"rulebook's own account would hand the PI an empty section")


# ── prohibitions ────────────────────────────────────────────────────────────

def test_prohibitions_are_marked_as_prohibitions():
    """Found by running the app, not by a test. A live Section Check of a clean
    Budget Justification put "Do not request NSF funds for alcoholic beverages"
    in the FIX LIST — the draft never mentions alcohol, which is precisely
    compliance, reported as a gap. Nine of the twenty-three items it told the PI
    to fix were rules the draft already obeyed.

    Absence means PASS for a prohibition and FAIL for a content rule, and the
    semantic reviewer only had the vocabulary for the second. The engine already
    had the right one — `clear`/`flagged`, in _CREDIT, used by the deterministic
    rows — it just was not reachable from a model-judged row."""
    rows = [r for r in _reviewed()["rules"]
            if r.get("check") != "rb_not_in_text" and r.get("flag_if_present")]
    labels = {r["label"] for r in rows}
    for must in ("Do not request NSF funds for alcoholic beverages",
                 "Prohibit voluntary committed cost sharing",
                 "Omit personal information from biographical sketch",
                 "Do Not Submit Unauthorized Letters of Support"):
        assert must in labels, f"{must!r} is a prohibition and is not marked as one"


def test_a_content_rule_is_not_marked_as_a_prohibition():
    """The mirror risk. Marking a content rule as a prohibition makes a draft
    that OMITS it come back `clear` — a missing requirement reported as
    compliance, which is worse than the bug being fixed."""
    rows = {r["label"] for r in _reviewed()["rules"] if r.get("flag_if_present")}
    for must_not in ("State objectives and expected significance in Project Description",
                     "Include Data Management and Sharing Plan",
                     "Outline general plan of work and experimental methods"):
        assert must_not not in rows, f"{must_not!r} is a content rule, not a prohibition"


def test_a_clean_draft_passes_a_prohibition_instead_of_failing_it():
    """End to end through the real semantic path, with the model stubbed: a
    prohibition the draft does not violate must come back `clear`, not
    `not_found`, and must never reach the fix-list."""
    from services import draft_review as dr

    req = {"id": "p1", "section": "budget_and_budget_justification",
           "label": "Do not request NSF funds for alcoholic beverages",
           "source": "No NSF funds may be spent on alcoholic beverages.",
           "kind": "semantic", "scored": True, "flag_if_present": True}
    span = {"text": "Materials and supplies: monomers and sensor housings, $18,000."}
    sections = {"budget_and_budget_justification": {"label": "Budget and Budget Justification"}}

    captured = {}

    def fake(prompt, **kw):
        captured["prompt"] = prompt
        return {"findings": [{"id": "p1", "status": "clear", "note": "No alcohol requested.",
                              "evidence": ""}]}

    orig = dr.gemini_client.generate_json
    dr.gemini_client.generate_json = fake
    try:
        out = dr._review_batch("budget_and_budget_justification", span, [req], sections, "X")
    finally:
        dr.gemini_client.generate_json = orig

    assert "prohibition" in captured["prompt"].lower(), \
        "the reviewer was never told this row is a prohibition"
    assert out[0]["status"] == "clear", f"got {out[0]['status']}, expected clear"
    assert dr._CREDIT["clear"] == 1.0


def test_a_violated_prohibition_must_quote_the_offending_text():
    """`flagged` is a POSITIVE claim about the draft — "you did the forbidden
    thing" — so golden rule 2 applies to it exactly as it does to `addressed`.
    An unquotable flag would send a PI hunting for text that is not there."""
    from services import draft_review as dr

    req = {"id": "p1", "section": "s", "label": "Do not request funds for alcohol",
           "source": "No NSF funds may be spent on alcoholic beverages.",
           "kind": "semantic", "scored": True, "flag_if_present": True}
    span = {"text": "Materials and supplies: monomers and sensor housings."}

    def fake(prompt, **kw):
        return {"findings": [{"id": "p1", "status": "flagged",
                              "note": "Wine is budgeted.",
                              "evidence": "reception wine, $400"}]}

    orig = dr.gemini_client.generate_json
    dr.gemini_client.generate_json = fake
    try:
        out = dr._review_batch("s", span, [req], {"s": {"label": "S"}}, "X")
    finally:
        dr.gemini_client.generate_json = orig

    assert out[0]["status"] == "clear", (
        "a flag whose quote is not in the draft must not stand; it was reported "
        f"as {out[0]['status']}")


# ── the model, on both entry points ─────────────────────────────────────────

def test_every_call_on_both_review_paths_names_the_model_and_the_region():
    """`gemini_client.DEFAULT_MODEL` is gemini-2.5-flash, so a call that FORGETS
    `model=` silently downgrades — nothing goes red, the answer just comes from a
    different model. And gemini-3.6-flash is region-locked: it 404s in
    us-central1 and answers only on `global`, so the pair must travel together.

    CLAUDE.md says to re-check this by hand whenever a Gemini call is added.
    This asserts it instead, at the boundary where the model name actually
    reaches Vertex, for BOTH entry points — review_draft (Draft Review) and
    review_section (Section Check), which share an engine but not a call graph:
    review_draft also runs locate_sections and _reviewer_notes.
    """
    from unittest import mock

    from services import draft_review as dr

    seen = []

    def fake_generate(prompt, **kw):
        seen.append((kw.get("model"), kw.get("location")))
        return None          # every caller falls back deterministically

    profile = {
        "id": "T", "title": "t", "url": "",
        "sections": {"project_summary": {"label": "Project Summary", "aliases": ["project summary"]}},
        "requirements": [{"id": "r1", "section": "project_summary", "label": "L",
                          "source": "S", "kind": "semantic", "scored": True}],
        "checks": {}, "merit_criteria": [], "eligibility_notes": [],
    }
    with mock.patch.object(dr.gemini_client, "_generate", side_effect=fake_generate):
        dr.review_draft("Project Summary\nSome text.", profile=profile)
        dr.review_section("Some text.", section="project_summary",
                          rulebook="the PAPPG", profile=profile)

    assert seen, "no Gemini call was made; this test would pass vacuously"
    assert dr.MODEL == "gemini-3.6-flash"
    assert dr.MODEL_LOCATION == "global"
    wrong = [(m, l) for m, l in seen if m != dr.MODEL or l != dr.MODEL_LOCATION]
    assert not wrong, (
        f"{len(wrong)} of {len(seen)} calls did not name "
        f"{dr.MODEL}@{dr.MODEL_LOCATION}: {sorted(set(wrong))}")


# ── the picker's section key is not the profile's section key ───────────────

def test_section_check_finds_the_solicitations_rows_when_it_spells_the_section_differently():
    """The picker sends a RULEBOOK key; a profile is keyed in its own vocabulary.

    `sections_from` merges two names for one part of a proposal and keeps
    whichever key was already in use, so a solicitation that canonicalises to
    `budget_justification` (canon_section strips "and") leaves the section
    universe with THAT key while the picker still sends
    `budget_and_budget_justification`. `requirements_for` is an exact-key match,
    so the solicitation's own budget rules come back empty and the PI is shown
    the PAPPG's rows alone -- with nothing on screen saying six were skipped.

    Measured on a real proposal (NSF 23-598): 6 scored rows lost, including
    "No more than 30% of the budget can be allocated for equipment" and
    "Inclusion of voluntary committed cost sharing is prohibited". Exactly the
    program-specific asks the PAPPG cannot tell anyone about.

    `_refile_rows` already fixed this for Draft Review, which reads the whole
    profile. Section Check reads around it by keying off the picker.
    """
    from services import draft_review as dr

    own = {"id": "sol_equip_cap", "section": "budget_justification",
           "label": "Cap equipment purchases at 30 percent of total budget",
           "source": "No more than 30% of the budget can be allocated for equipment.",
           "kind": "semantic", "scored": True}
    profile = {
        "id": "NSF 23-598", "title": "t", "url": "",
        # the universe kept the solicitation's spelling, as sections_from does
        "sections": {"budget_justification": {
            "label": "Budget and Budget Justification",
            "aliases": ["budget justification", "budget and budget justification"]}},
        "requirements": [own],
        "checks": {}, "merit_criteria": [], "eligibility_notes": [],
    }

    out = dr.review_section(
        "Budget Justification\nSenior personnel: the PI requests two summer months.",
        section="budget_and_budget_justification", rulebook=PAPPG,
        profile=profile, use_ai=False)

    ids = {f["id"] for f in out["findings"]}
    assert "sol_equip_cap" in ids, (
        "the solicitation's own budget rule never reached the review; "
        f"{len(ids)} findings came back, all from the rulebook")


# ── the section gets a score too ────────────────────────────────────────────

_GOOD_SUMMARY = """Overview
This project develops adaptive zwitterionic polymer networks for salinity
sensing. The objectives are to synthesize antifouling hydrogels and validate
prototypes in the Chesapeake Bay. The methods combine controlled radical
polymerization and impedance spectroscopy.

Intellectual Merit
The work advances understanding of ion partitioning in zwitterionic networks,
a regime where existing Donnan models fail.

Broader Impacts
The project trains four undergraduates per year at an HBCU and delivers a
summer module to Baltimore City high school students.
"""


def test_a_section_check_returns_a_score():
    """Section Check computed no percentage at all — the PI was shown a list of
    statuses and left to total it themselves. Draft Review already scores the
    same statuses with the same arithmetic; there is no second scorer here, the
    entry point simply never called the one that exists.
    """
    from services import draft_review as dr

    out = dr.review_section(_GOOD_SUMMARY, section="project_summary",
                            rulebook=PAPPG, use_ai=False)

    assert out["score"] is not None, "the section was reviewed but never scored"
    assert 0 <= out["score"]["percent"] <= 100


def test_the_score_says_how_much_came_from_the_rulebook_and_how_much_from_the_solicitation():
    """A section is judged against TWO authorities at once — NSF's standing
    rulebook and this program's own asks — and one number cannot say which half
    a draft is failing. A PI who meets every PAPPG rule and misses every
    solicitation rule reads the same 50% as one who did the reverse, though the
    second is far more likely to be returned without review.
    """
    from services import draft_review as dr

    findings = [
        {"id": "a", "scored": True, "status": "addressed", "rulebook": "the PAPPG"},
        {"id": "b", "scored": True, "status": "addressed", "rulebook": "the PAPPG"},
        {"id": "c", "scored": True, "status": "not_found", "rulebook": None},
        {"id": "d", "scored": True, "status": "unclear",   "rulebook": None},
    ]
    s = dr.score(findings, solicitation_id="NSF 23-598")

    by = s.get("by_source")
    assert by, "the score does not say where its rules came from"
    assert by["the PAPPG"] == {"percent": 100, "assessed": 2, "earned": 2.0}, by
    assert by["NSF 23-598"] == {"percent": 0, "assessed": 1, "earned": 0.0}, by


def test_the_basis_does_not_credit_every_rule_to_the_solicitation():
    """`basis` is the sentence printed under the number, and it named the
    solicitation as the source of the whole count — "100% of the 7 NSF 23-598
    requirements" on a section where 6 of the 7 came from the PAPPG.

    Harmless in Draft Review, where every row really did come from the
    solicitation profile. Wrong the moment a rulebook contributes rows, and
    wrong in the direction that matters: it attributes NSF's standing rules to a
    document that never stated them, which is the same class of error as a
    shared contract quote stamped on every row.
    """
    from services import draft_review as dr

    findings = [
        {"id": "a", "scored": True, "status": "addressed", "rulebook": "the PAPPG"},
        {"id": "b", "scored": True, "status": "addressed", "rulebook": None},
    ]
    basis = dr.score(findings, solicitation_id="NSF 23-598")["basis"]
    assert "2 NSF 23-598 requirements" not in basis, basis

    # a single-source score may still name it -- that claim is true
    only = [{"id": "a", "scored": True, "status": "addressed", "rulebook": None}]
    assert "1 NSF 23-598 requirement" in dr.score(
        only, solicitation_id="NSF 23-598")["basis"]


def test_the_score_caption_says_the_rules_check_presence_not_strength():
    """A 152-word Project Summary scored 100%, and every one of the seven rules
    behind it is a PRESENCE check — has a heading, mentions objectives, speaks to
    broader impacts. The arithmetic is right and the screen still lied: a large
    green 100% reads as "excellent" beside a grey line saying the section uses
    28% of the page it is allowed.

    Reported by a PI: "why would you rate it 100". Third time this repo has
    rendered presence as approval — the green ticks came off the section map and
    "Addressed" became "Meets the rule" for the same reason.

    The caption is the one guardrail that travels with the number into BOTH
    modals, so it is authored here rather than in either one.
    """
    from services import draft_review as dr

    basis = dr.score(
        [{"id": "a", "scored": True, "status": "addressed", "rulebook": "the PAPPG"}],
    )["basis"]
    low = basis.lower()
    assert "present" in low, basis
    assert "not how" in low or "not a measure of how" in low, basis


# ── section names that mean the same part of a proposal ─────────────────────

def test_supplementary_documents_resolves_to_the_section_the_picker_offers():
    """`section_signature` matches on the SET of meaning-carrying words, and set
    equality is its safety property — containment would fold "Project Description
    Supplementary Documents" into "Project Description" and lose a real section.

    But NSF calls one upload slot "Special Information and Supplementary
    Documentation" while a solicitation writing about the same slot says
    "Supplementary Documents". {supplementary, document} and {special,
    information, supplementary, documentation} share no equal set, so the two
    never merged and NSF 23-598's own rules about letters of support sat in a
    section Section Check does not offer.

    The fix is a NAMED equivalence, not a looser matcher.
    """
    from services import solicitation_profile as sp

    sections = {"supplementary_document": {"label": "Supplementary Documents",
                                           "aliases": ["supplementary documents"]}}
    assert sp.resolve_section_key(
        sections, "Special Information and Supplementary Documentation"
    ) == "supplementary_document"


def test_the_equivalence_is_symmetric():
    """It has to work whichever name the profile happened to keep."""
    from services import solicitation_profile as sp

    sections = {"special_information_and_supplementary_documentation": {
        "label": "Special Information and Supplementary Documentation", "aliases": []}}
    assert sp.resolve_section_key(sections, "Supplementary Documents") == \
        "special_information_and_supplementary_documentation"


def test_the_equivalence_does_not_reopen_the_containment_hole():
    """The exact case set-equality exists to prevent. If this ever passes by
    resolving, a real section is being swallowed."""
    from services import solicitation_profile as sp

    sections = {"project_description": {"label": "Project Description", "aliases": []}}
    assert sp.resolve_section_key(
        sections, "Project Description Supplementary Documents") is None


# ── nothing may go unreachable ──────────────────────────────────────────────

def _coverage(profile, rulebook=PAPPG):
    """Every requirement row, bucketed by whether SOMETHING can reach it."""
    from services import draft_review as dr
    from services import solicitation_profile as sp

    universe = profile.get("sections") or {}
    pickable = set()
    # THE PICKER THIS PROPOSAL ACTUALLY GETS. `rb.sections_offered` answers for
    # the rulebook alone and has not been the shipped picker since 2026-08-26 —
    # a section the rulebook holds no BASIC rules for is still offered when the
    # solicitation fills it. Measuring reachability against the wrong picker
    # would report a reachable row as Draft-Review-only, which is the same
    # class of wrong answer this whole test exists to catch.
    for s in sp.sections_offered_for(profile, rulebook):
        key = s["key"] if s["key"] in universe else (
            sp.resolve_section_key(universe, s["label"]) or s["key"])
        pickable.add(key)

    buckets = {"section_check": [], "draft_review_only": [], "whole_document": [],
               "ORPHANED": []}
    for r in profile.get("requirements", []):
        sec = r.get("section")
        if sec is None:
            buckets["whole_document"].append(r)
        elif sec in pickable:
            buckets["section_check"].append(r)
        elif sec in universe:
            buckets["draft_review_only"].append(r)
        else:
            buckets["ORPHANED"].append(r)      # points at a section that does not exist
    return buckets


def test_no_requirement_can_point_at_a_section_that_does_not_exist():
    """AN ORPHANED ROW IS THE WORST OUTCOME IN THIS ENGINE, and it has already
    happened twice: 45 PAPPG rules kept pointing at a key that lost a merge, and
    6 solicitation rules were unreachable because the picker's key differed from
    the profile's. Both times the row was recovered, rendered, and never checked
    — which is worse than not having it, because it looks handled.

    A row must be reachable by SOMETHING: the section picker, Draft Review's
    wider universe, or whole-document scope. There is no fourth option.
    """
    from services import proposals_service as ps
    from services import solicitation_profile as sp

    reqs = [
        {"id": "a", "section": None, "label": "L", "source": "S",
         "kind": "semantic", "scored": True},
        {"id": "b", "section": "project_summary", "label": "L", "source": "S",
         "kind": "semantic", "scored": True},
        {"id": "c", "section": "supplementary_document", "label": "L", "source": "S",
         "kind": "semantic", "scored": True},
    ]
    profile = sp.make_profile(
        id="X", title="X",
        sections={"project_summary": {"label": "Project Summary", "aliases": []},
                  "supplementary_document": {"label": "Supplementary Documents",
                                             "aliases": []}},
        requirements=reqs)
    buckets = _coverage(profile)
    assert not buckets["ORPHANED"], [r["id"] for r in buckets["ORPHANED"]]
    # and the supplementary row is now reachable from the picker, not only from
    # Draft Review -- that is what the named equivalence bought.
    assert [r["id"] for r in buckets["section_check"]] == ["b", "c"]


def test_every_rulebook_rule_lands_in_a_section_the_picker_or_a_review_can_reach():
    """The rulebook's own 156 rows. If one is filed under a section string that
    `section_label` does not know, it renders under a title nobody recognises and
    can never be located in a draft."""
    rows = rb.RULES[PAPPG]
    assert rows, "the rulebook is empty"
    by_section = {}
    for r in rows:
        by_section.setdefault(r["section"], []).append(r["id"])

    # Every section the rules are filed under must have a real label. A rule
    # under an unknown key renders under a title nobody recognises.
    for section in by_section:
        label = rb.section_label(section)
        assert label and label != section, (
            f"{section!r} has no human label; {len(by_section[section])} rules "
            "would render under a raw key")

    # And every section the PICKER offers must actually hold rules -- offering an
    # empty section is a dead end dressed as a tool.
    for s in rb.sections_offered(PAPPG):
        assert by_section.get(s["key"]), f"{s['key']} is offered but holds no rules"


def test_the_two_names_for_the_supplementary_section_merge_into_ONE_section():
    """The equivalence has to bite where the UNIVERSE is built, not only at
    lookup. Measured on a live proposal: `sections_from` produced BOTH
    `special_information_and_supplementary_documentation` and
    `supplementary_document` as separate sections, so `resolve_section_key` took
    the exact-key hit on the first and never consulted the equivalence — and the
    solicitation's two rules about letters of support stayed unreachable from the
    picker even after the equivalence existed.

    A fix at the lookup was not wrong, it was not ENOUGH. Both layers matter: the
    universe must not split them, and a lookup by either name must land.
    """
    from services import solicitation_profile as sp

    universe = sp.sections_from([
        {"id": "a", "section": "supplementary_document", "label": "L",
         "source": "S", "kind": "semantic", "scored": True},
        {"id": "b", "section": "special_information_and_supplementary_documentation",
         "label": "L", "source": "S", "kind": "semantic", "scored": True},
    ])
    supp = [k for k in universe if "supplement" in k or "special" in k]
    assert len(supp) == 1, f"one section, two entries: {supp}"
    # and BOTH spellings still locate a heading in a draft
    aliases = " ".join(universe[supp[0]]["aliases"]).lower()
    assert "supplementary document" in aliases, universe[supp[0]]["aliases"]
    assert "special information" in aliases, universe[supp[0]]["aliases"]
