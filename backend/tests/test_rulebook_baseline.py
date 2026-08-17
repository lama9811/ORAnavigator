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
