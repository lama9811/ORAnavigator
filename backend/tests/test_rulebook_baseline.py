"""The rules NSF states on Research.gov, held as data keyed by RULEBOOK.

Captured 2026-08-17 from a live Morgan proposal (#329981, NSF 23-601). Those
pages are behind NSF login, so this table can never be scraped and is curated by
hand — the same shape as compliance_sentinel's rules and budget_helper's rates.
"""
import pytest

from services import rulebook_baseline as rb


def test_the_project_summary_heading_rule_exists_and_quotes_nsf():
    """The rule whose absence let a five-line summary come back 'Addressed'."""
    rows = rb.rules_for("the PAPPG", "project_summary")
    row = next(r for r in rows if r["check"] == "rb_headings")
    assert row["check_args"]["headings"] == [
        "Overview", "Intellectual Merit", "Broader Impacts"]
    assert "on its own line with no other text on that line" in row["source"]


def test_every_row_carries_a_verbatim_quote_and_a_source_url():
    """Golden rule 2 by construction: a row with no quote cannot be shown."""
    for name, rows in rb.RULES.items():
        for r in rows:
            assert r["source"].strip(), f"{r['id']} has no quote"
            assert r["source_url"].startswith("https://"), r["id"]
            assert r["rulebook"] == name


def test_a_semantic_row_names_no_check():
    """The deterministic rows' checks are verified in test_rulebook_checks.py,
    once the module they name exists."""
    for rows in rb.RULES.values():
        for r in rows:
            if r["kind"] != "deterministic":
                assert r.get("check") is None, r["id"]


def test_the_et_al_row_is_not_scored():
    """NSF's own sentence carries '(except for large consortia papers)'. A
    conditional ask is advisory and never counted against a compliant draft."""
    row = next(r for r in rb.rules_for("the PAPPG", "references_cited")
               if r["check"] == "rb_et_al")
    assert row["scored"] is False


def test_an_unknown_rulebook_yields_nothing():
    """Fails safe: a solicitation citing something we hold no rules for behaves
    exactly as it does today."""
    assert rb.rules_for("the Hitchhiker's Guide") == []


def test_a_solicitation_quoting_the_pappg_is_detected():
    reqs = [{"source": "Adhere to the requirements outlined in the PAPPG."}]
    assert rb.rulebooks_cited_by(reqs) == ["the PAPPG"]


def test_a_solicitation_naming_no_rulebook_is_detected_as_none():
    reqs = [{"source": "The Project Description is limited to 15 pages."}]
    assert rb.rulebooks_cited_by(reqs) == []


def test_the_sponsor_substring_bug_cannot_fire_here():
    """'Maryland Technology Transfer Fund' contains 'nsf'. This module never
    looks at a sponsor string — only at what the document CITES."""
    reqs = [{"source": "Funded by the Maryland Technology Transfer Fund."}]
    assert rb.rulebooks_cited_by(reqs) == []


def test_sections_offered_lists_the_four_covered_parts():
    keys = [s["key"] for s in rb.sections_offered("the PAPPG")]
    assert keys == ["project_summary", "project_description",
                    "references_cited", "facilities_equipment_and_other_resources"]


def test_every_section_constant_is_what_section_key_actually_produces():
    """The bug this caught: FACILITIES was written without the "and", so
    section_key("Facilities, Equipment and Other Resources") produced a key the
    constant did not equal. Nothing would have gone red — the four Facilities
    rules would simply have been filed under a key no real draft can produce,
    reported "Not located", and dropped out of the score's denominator,
    silently unchecked. Assert against the LIVE function, never against a
    string we typed twice."""
    from services.solicitation_profile import section_key
    for key, label in rb._SECTION_LABELS.items():
        assert key == section_key(label), f"{key} != section_key({label!r})"


# ── injection into the profile ──────────────────────────────────────────────

from services import solicitation_profile as sp

_PAPPG_ROW = {
    "id": "r1", "label": "Adhere to PAPPG guidelines", "section": "project_summary",
    "kind": "semantic", "scored": True,
    "source": "The Project Summary must include the LOI number in addition to "
              "all the requirements outlined in the PAPPG.",
    "why": "", "keywords": [],
}
_PLAIN_ROW = {
    "id": "r1", "label": "Limit the Project Description to 15 pages",
    "section": "project_description", "kind": "semantic", "scored": True,
    "source": "The Project Description is limited to 15 pages.",
    "why": "", "keywords": [],
}


def test_a_solicitation_citing_the_pappg_gains_the_baseline_rows():
    rows = rb.baseline_rows([_PAPPG_ROW])
    assert any(r["id"] == "pappg_ps_headings" for r in rows)


def test_a_solicitation_citing_nothing_gains_no_rows():
    """Fails safe: no rows added, no score moved, no finding lost."""
    assert rb.baseline_rows([_PLAIN_ROW]) == []


def test_build_generic_includes_the_baseline_for_a_pappg_solicitation():
    profile = sp.build_generic({}, [_PAPPG_ROW], id="NSF 23-598", title="T")
    ids = {r["id"] for r in profile["requirements"]}
    assert "pappg_ps_headings" in ids
    assert "pappg_pd_no_urls" in ids


def test_the_baseline_creates_the_sections_its_rows_need():
    """sections_from builds the universe from the rows, so a baseline row for a
    section the solicitation never named must still get a section to be located
    in -- otherwise it reports 'Not located' and drops out of the score."""
    profile = sp.build_generic({}, [_PAPPG_ROW], id="NSF 23-598", title="T")
    assert "facilities_equipment_and_other_resources" in profile["sections"]


def test_a_solicitation_stating_its_own_page_limit_suppresses_the_baseline_one():
    """The solicitation's number beats NSF's 15-page default -- which is what
    NSF's own instruction says: 'The system will enforce the page limit
    requirements listed in the funding opportunity.'"""
    contract = {"page_limits": {"Project Description": 12}}
    profile = sp.build_generic(contract, [_PAPPG_ROW], id="NSF 23-598", title="T")
    ids = [r["id"] for r in profile["requirements"]]
    assert "pappg_pd_page_limit" not in ids
    assert "page_limit_project_description" in ids


def test_a_solicitation_stating_a_summary_page_limit_suppresses_the_one_page_row():
    contract = {"page_limits": {"Project Summary": 1}}
    profile = sp.build_generic(contract, [_PAPPG_ROW], id="NSF 23-598", title="T")
    ids = [r["id"] for r in profile["requirements"]]
    assert "pappg_ps_one_page" not in ids


def test_only_page_rules_dedup():
    """Semantic rows deliberately do not dedup. Quote-based dedup is already
    known not to work here (rule 4 splits compound sentences, so several
    legitimate rows share one quote), and a visible duplicate is better than an
    invisible dropped rule."""
    contract = {"page_limits": {"Project Description": 12}}
    rows = rb.baseline_rows([_PAPPG_ROW], page_limits=contract["page_limits"])
    assert any(r["id"] == "pappg_pd_impacts_header" for r in rows)


def test_a_stored_profile_gains_the_rows_on_load_with_no_re_extraction():
    """load_solicitation_profile rebuilds on EVERY load, which is what makes
    this retroactive -- the same reason compliance_sentinel recomputes verdicts
    and canon_section re-canonicalises stored keys."""
    import json
    from services import proposals_service as ps

    class _Sub:
        solicitation_json = json.dumps({
            "id": "NSF 23-598", "title": "T", "contract": {},
            "requirements": [_PAPPG_ROW],
        })

    profile = ps.load_solicitation_profile(_Sub())
    assert any(r["id"] == "pappg_ps_headings" for r in profile["requirements"])


# ── the per-section entry point ─────────────────────────────────────────────

from services import draft_review

FIVE_LINE = """We propose to study trustworthy cardiac AI using multimodal
physiological sensing. The work will develop new models and validate them on
clinical data. We expect the results to be significant.
"""


def test_a_section_check_needs_no_solicitation():
    """The rules are NSF's, not the solicitation's. Draft Review's 409 exists so
    a percentage is never computed against zero requirements; this returns no
    percentage, so the guard has nothing to protect."""
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    assert out["score"] is None
    assert any(f["id"] == "pappg_ps_headings" and f["status"] == "not_found"
               for f in out["findings"])


def test_a_section_check_returns_only_that_sections_rules():
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    assert {f["id"] for f in out["findings"]} <= {
        r["id"] for r in rb.rules_for("the PAPPG", "project_summary")}


def test_a_section_check_carries_the_skeleton():
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    assert "Intellectual Merit" in out["skeleton"]["body"]
    assert "not a real proposal" in out["skeleton"]["note"]


def test_a_real_page_count_reaches_the_page_rule():
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", pages=3, use_ai=False)
    row = next(f for f in out["findings"] if f["id"] == "pappg_ps_one_page")
    assert row["status"] == "flagged"


def test_a_paste_gets_an_estimate_not_a_verdict():
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    row = next(f for f in out["findings"] if f["id"] == "pappg_ps_one_page")
    assert row["status"] == "not_checked"


def test_mechanical_mistakes_run_on_the_section():
    out = draft_review.review_section(
        "Overview\nWe will study TBD in year one.\n", section="project_summary",
        rulebook="the PAPPG", use_ai=False)
    assert any("TBD" in (m.get("evidence") or "") for m in out["mistakes"])


def test_empty_text_returns_a_message_not_a_review():
    out = draft_review.review_section("", section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    assert out["findings"] == []
    assert out["message"]


def test_an_unknown_section_returns_no_findings():
    out = draft_review.review_section(FIVE_LINE, section="not_a_section",
                                      rulebook="the PAPPG", use_ai=False)
    assert out["findings"] == []


def test_the_section_check_agrees_with_the_whole_package_review():
    """One engine, two entry points. If these ever disagree for the same text,
    a second engine has grown."""
    profile = sp.build_generic({}, [_PAPPG_ROW], id="NSF 23-598", title="T")
    spans = {"project_summary": {"text": FIVE_LINE, "marker": "Project Summary",
                                 "start": 0}}
    whole = draft_review.run_deterministic(FIVE_LINE, spans, profile)
    whole_ps = {f["id"]: f["status"] for f in whole
                if f["id"] == "pappg_ps_headings"}
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    section_ps = {f["id"]: f["status"] for f in out["findings"]
                  if f["id"] == "pappg_ps_headings"}
    assert whole_ps == section_ps


def test_a_supplied_profiles_own_semantic_row_reaches_the_section_check():
    """Verifies the profile's own semantic requirement for this section actually
    reaches _review_section rather than being silently dropped when a rulebook
    is also supplied -- a filtered universe that drops the solicitation's own
    row would be worse than not accepting a profile at all.

    use_ai=True here, not False: a semantic row is only assessed at all when
    the semantic stage runs (see review_section's `if semantic and use_ai`
    gate), and the AI layer is disabled process-wide by conftest's autouse
    fixture (get_client() -> None), so this exercises the real fallback path
    -- _semantic_fallback -- with no network call, not a mocked shortcut."""
    profile = sp.build_generic({}, [_PAPPG_ROW], id="NSF 23-598", title="T")
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", profile=profile,
                                      use_ai=True)
    ids = {f["id"] for f in out["findings"]}
    assert _PAPPG_ROW["id"] in ids
    assert "pappg_ps_headings" in ids
    assert out["score"] is None


def test_a_profiles_own_check_callable_reaches_run_deterministic():
    """The mini profile review_section assembles must carry the caller's own
    check callables through to run_deterministic, not just its requirement
    rows -- a deterministic row whose check lives only in the profile's
    `checks` dict (not generic_checks or rulebook_checks) must still run."""
    def _always_flagged(ctx, req):
        return "flagged", "the custom check ran", "evidence"

    custom_row = {
        "id": "custom1", "label": "A custom solicitation rule",
        "section": "project_summary", "kind": "deterministic", "scored": True,
        "check": "custom_check_xyz", "source": "A custom solicitation rule.",
        "why": "", "keywords": [],
    }
    profile = sp.make_profile(id="X", title="T", sections={},
                              requirements=[custom_row],
                              checks={"custom_check_xyz": _always_flagged})
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", profile=profile,
                                      use_ai=False)
    row = next(f for f in out["findings"] if f["id"] == "custom1")
    assert row["status"] == "flagged"
    assert out["score"] is None


def test_a_profile_row_already_in_the_baseline_is_not_duplicated():
    """profile rows are deduped against the baseline by id, so a solicitation
    that happens to reuse a baseline id does not produce two findings for it."""
    dup_row = {**_PAPPG_ROW, "id": "pappg_ps_headings"}
    profile = sp.build_generic({}, [dup_row], id="NSF 23-598", title="T")
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", profile=profile,
                                      use_ai=False)
    ids = [f["id"] for f in out["findings"]]
    assert ids.count("pappg_ps_headings") == 1


def test_a_baseline_finding_carries_its_rulebook_through_the_real_pipeline():
    """Regression for a bug a hand-built-dict test could not catch. Every
    finding on the real pipeline is built through draft_review._finding() --
    run_deterministic, _review_section and _semantic_fallback all go through
    it -- and it used to construct the finding dict key-by-key without ever
    copying req["rulebook"]. So a REAL baseline row reached apply_delegation's
    `if f.get("rulebook"): continue` guard with no `rulebook` key at all, and
    the guard silently never fired for any finding the app actually produces --
    baseline or not. Nothing broke in production only because classify()
    independently returns pointer_only=False for all 14 current baseline
    labels (none starts with a compliance verb like "Comply with..."); a
    future rulebook row phrased that way would have been demoted to
    `delegated` -- deleted from the score's denominator -- with the guard
    doing nothing to stop it.

    This goes through sp.build_generic() -> run_deterministic(), the actual
    construction path, rather than a dict built by hand."""
    profile = sp.build_generic({}, [_PAPPG_ROW], id="NSF 23-598", title="T")
    spans = {"project_summary": {"text": FIVE_LINE, "marker": "Project Summary",
                                 "start": 0}}
    findings = draft_review.run_deterministic(FIVE_LINE, spans, profile)
    row = next(f for f in findings if f["id"] == "pappg_ps_headings")

    # The propagation itself: a real finding must carry the rulebook its
    # requirement row carried, not None.
    assert row["rulebook"] == "the PAPPG"

    # And the guard must actually protect it: apply_delegation must not touch
    # a real baseline finding's status, whatever that status is.
    before = row["status"]
    out = draft_review.apply_delegation([dict(row)])
    assert out[0]["status"] == before


def test_use_ai_false_does_not_silently_drop_semantic_rules():
    """Before this fix, `if semantic and use_ai:` meant a section WITH semantic
    rules (project_summary has three: pappg_ps_overview/merit/impacts) produced
    NO findings at all for them when use_ai=False, and `message` stayed None —
    a caller could not tell "this section has no semantic rules" from
    "semantic rules were not assessed". They must now appear as `unclear`
    fallback rows (the same shape review_draft produces on an AI outage), and
    the message must say coverage was reduced."""
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    ids = {f["id"] for f in out["findings"]}
    for semantic_id in ("pappg_ps_overview", "pappg_ps_merit", "pappg_ps_impacts"):
        assert semantic_id in ids, f"{semantic_id} missing from findings"
        row = next(f for f in out["findings"] if f["id"] == semantic_id)
        assert row["status"] == "unclear"
        assert row["source"] == "fallback"
    assert out["message"], "message must explain coverage was reduced"


def test_use_ai_false_leaves_message_none_when_there_are_no_semantic_rules():
    """The other half of the same distinction: a section with ONLY
    deterministic rules must still report message=None on use_ai=False —
    nothing was skipped, so there is nothing to say."""
    profile = sp.make_profile(id="X", title="T", sections={}, requirements=[
        {"id": "custom_det", "label": "A custom deterministic rule",
         "section": "project_summary", "kind": "deterministic", "scored": True,
         "check": "custom_check_xyz", "source": "A custom rule.",
         "why": "", "keywords": []},
    ], checks={"custom_check_xyz": lambda ctx, req: ("flagged", "ran", "ev")})
    # rulebook="" -> rulebook_baseline.rules_for returns [] (unknown rulebook),
    # so the profile's one deterministic row is the whole universe.
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="", profile=profile, use_ai=False)
    assert out["findings"], "expected the profile's own deterministic row"
    assert all(f["kind"] == "deterministic" for f in out["findings"])
    assert out["message"] is None


# ── golden rule 2 inside the module that claims it holds by construction ────

def test_no_row_quotes_a_sentence_that_does_not_state_its_rule():
    """The module docstring says every row's `source` is NSF's verbatim sentence,
    "so golden rule 2 holds by construction". Three Project Summary rows broke
    that: `pappg_ps_overview`, `_merit` and `_impacts` all quoted "Your file must
    include three separate section headers: Overview, Intellectual Merit, and
    Broader Impacts" — a sentence about HEADINGS, which says nothing about
    objectives, methods, or whether what sits under a heading is substantive.

    That is the exact failure `generic_checks._quote_if_it_names` was written
    for: a quote that does not support its row looks like evidence while being
    none. The honest form is a stated DERIVATION, never an invented NSF
    sentence.
    """
    from services import rulebook_baseline as rb
    headings_quote = next(r["source"] for r in rb.rules_for("the PAPPG")
                          if r["id"] == "pappg_ps_headings")
    for row_id in ("pappg_ps_overview", "pappg_ps_merit", "pappg_ps_impacts"):
        row = next(r for r in rb.rules_for("the PAPPG") if r["id"] == row_id)
        assert row["source"] != headings_quote, (
            f"{row_id} reuses the headings quote, which does not state its rule")
        assert row["source"].lower().startswith("derived from"), (
            f"{row_id} must say it is derived rather than pose as NSF's sentence")
