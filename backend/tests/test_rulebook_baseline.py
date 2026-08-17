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
