"""Accuracy guarantees for the solicitation extractor (2026-09-03 rewrite).

These pin the three properties the whole rewrite exists to provide:

  1. COVERAGE -- every page of the uploaded PDF reaches the model. The old
     pipeline truncated at 250,000 characters, which on NSF 24-1 (748,400
     chars) silently discarded 67% of the document, including 11 of its 20
     page-limit rules and the entire Proposal Preparation Checklist. Coverage
     must be a property of the CODE (slice the pages, send them all), never
     of the model's willingness to say it read everything -- gemini-3.6-flash
     reported "pages_examined: 216" on a single-pass run and still missed 25
     page limits.

  2. NO CRYING WOLF -- the "double-check this" flag must fire on genuinely
     unsupported values and stay quiet otherwise. It used to flag page_limits
     and eligibility on 100% of runs even when every quote was verbatim from
     the PDF, which trains the user to ignore the flags entirely.

  3. NO SILENT DEGRADATION -- if the primary model/location is unavailable
     the extractor falls back rather than failing the user's upload.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_solicitation_accuracy.py -v
"""
import json

import pytest

from services import solicitation_extractor as sx


# ---------------------------------------------------------------------------
# 1. Coverage: slicing must include every page, exactly once
# ---------------------------------------------------------------------------

def test_every_page_lands_in_exactly_one_slice():
    """The coverage guarantee. Each page appears in one slice and no page is
    dropped, whatever the slice budget works out to."""
    pages = [(i, f"page {i} " + "x" * 5000) for i in range(1, 121)]
    slices = sx._build_slices(pages)
    seen = [pageno for sl in slices for pageno, _ in sl]
    assert seen == list(range(1, 121))          # every page, in order, once


def test_slices_respect_the_character_budget():
    """A slice stops growing once it would exceed _SLICE_CHARS, so no single
    request carries so much text that the model starts skimming."""
    pages = [(i, "y" * 10000) for i in range(1, 41)]
    slices = sx._build_slices(pages)
    assert len(slices) > 1
    for sl in slices[:-1]:
        assert sum(len(t) for _, t in sl) <= sx._SLICE_CHARS


def test_a_single_oversized_page_is_not_split():
    """A page larger than the whole slice budget still gets its own slice --
    splitting mid-page would put a [[PAGE n]] marker on a fragment and break
    the page attribution of every quote inside it."""
    big = "z" * (sx._SLICE_CHARS + 50_000)
    slices = sx._build_slices([(1, "small"), (2, big), (3, "small")])
    holding = [sl for sl in slices if any(p == 2 for p, _ in sl)]
    assert len(holding) == 1
    assert len(holding[0]) == 1                 # page 2 alone, intact


def test_blank_pages_are_skipped_not_counted():
    """Blank pages carry no rules; including them only dilutes attention."""
    slices = sx._build_slices([(1, "real"), (2, "   \n "), (3, ""), (4, "real")])
    assert [p for sl in slices for p, _ in sl] == [1, 4]


def test_no_truncation_constant_survives():
    """Regression guard for the original bug: there must be no character cap
    that silently discards the tail of a long solicitation."""
    assert not hasattr(sx, "_MAX_PROMPT_CHARS")


def test_coverage_is_reported_honestly(monkeypatch):
    """extract_from_pages must report how many pages were actually read, so
    the UI can say '216 of 216' instead of asking the user to take it on
    faith."""
    monkeypatch.setattr(sx, "_call_gemini", _stage_fake(
        sweep={"page_limits": [{"section": "project_description", "pages": 15,
                                "applies_to": "standard", "page": 3,
                                "quote": "may not exceed 15 pages"}]},
        contract={"sponsor": "NSF", "page_limits": {"project_description": 15},
                  "source_quotes": {"page_limits": "may not exceed 15 pages"}},
    ))
    pages = [f"page {i} may not exceed 15 pages" for i in range(30)]
    out = sx.extract_from_pages(pages)
    assert out["coverage"]["pages_total"] == 30
    assert out["coverage"]["pages_read"] == 30
    assert out["coverage"]["slices_failed"] == 0


def test_partial_sweep_failure_is_reported_not_hidden(monkeypatch):
    """If some slices fail we still return what we have -- but the coverage
    block says so, so nobody mistakes a partial read for a complete one."""
    pages = [("q" * 50_000) for _ in range(8)]   # forces several slices
    calls = {"n": 0}

    def _fake(text, **kw):
        if kw.get("system_instruction") is sx._SWEEP_SYSTEM:
            calls["n"] += 1
            if calls["n"] == 1:
                return "not json at all"          # one slice fails
            return json.dumps({"page_limits": [], "required_attachments": [],
                               "deadlines": [], "budget_caps": [],
                               "eligibility": [], "formatting": [],
                               "identity": {"sponsor": "NSF"}})
        return json.dumps({"sponsor": "NSF", "page_limits": {},
                           "required_attachments": [], "source_quotes": {}})

    monkeypatch.setattr(sx, "_call_gemini", _fake)
    out = sx.extract_from_pages(pages)
    assert out is not None
    assert out["coverage"]["slices_failed"] == 1
    assert out["coverage"]["pages_read"] < out["coverage"]["pages_total"]


# ---------------------------------------------------------------------------
# 2. No crying wolf: the unverified flag must be precise
# ---------------------------------------------------------------------------

_TEXT = ("Project Description may not exceed 15 pages. "
         "The budget justification must be no more than five pages per proposal. "
         "Proposals must be submitted via Research.gov. "
         "The following organizations are eligible to submit proposals to NSF: "
         "Institutions of Higher Education.")


def test_per_entry_quotes_are_accepted():
    """THE core false-alarm fix. A field may supply one quote PER ENTRY; the
    old checker demanded a single string and auto-flagged anything else, so a
    fully-sourced page_limits block was flagged red on every single run."""
    extracted = {
        "page_limits": {"project_description": 15, "budget_justification": 5},
        "source_quotes": {"page_limits": {
            "project_description": "Project Description may not exceed 15 pages.",
            "budget_justification": "The budget justification must be no more than five pages per proposal.",
        }},
    }
    unverified, partial = sx._verify_source_quotes_detailed(extracted, _TEXT)
    assert unverified == []
    assert partial == {}


def test_one_bad_entry_is_named_not_blamed_on_the_whole_field():
    """A single fabricated entry must be pinpointed, not turn the entire
    field red -- the user needs to know WHICH number to re-check."""
    extracted = {
        "page_limits": {"project_description": 15, "biosketch": 99},
        "source_quotes": {"page_limits": {
            "project_description": "Project Description may not exceed 15 pages.",
            "biosketch": "the biosketch is limited to 99 pages",   # not in text
        }},
    }
    unverified, partial = sx._verify_source_quotes_detailed(extracted, _TEXT)
    assert "page_limits" not in unverified        # field as a whole is grounded
    assert partial == {"page_limits": ["biosketch"]}


def test_all_entries_fabricated_still_flags_the_field():
    """The safety property must survive the leniency: if nothing checks out,
    the field is still flagged."""
    extracted = {
        "page_limits": {"a": 1, "b": 2},
        "source_quotes": {"page_limits": {"a": "wholly invented sentence one",
                                          "b": "wholly invented sentence two"}},
    }
    unverified, _ = sx._verify_source_quotes_detailed(extracted, _TEXT)
    assert "page_limits" in unverified


def test_eligibility_summary_is_not_flagged():
    """eligibility is specified as a one-or-two-sentence SUMMARY, so a
    character-for-character quote is impossible by construction. Demanding
    one flagged this field on essentially every solicitation."""
    extracted = {
        "eligibility": "Institutions of higher education may apply.",
        "source_quotes": {"eligibility":
            "The following organizations are eligible to submit proposals to NSF: "
            "Institutions of Higher Education, and others listed in the guide."},
    }
    unverified, _ = sx._verify_source_quotes_detailed(extracted, _TEXT)
    assert unverified == []


def test_identity_value_present_in_text_is_its_own_evidence():
    """program_id / program_name are copied verbatim out of the document, so
    the value appearing in the text is stronger evidence than a surrounding
    quote. Without this they were flagged despite being demonstrably right."""
    extracted = {"program_id": "NSF 24-1", "source_quotes": {}}
    unverified, _ = sx._verify_source_quotes_detailed(
        extracted, "Effective May 20, 2024. NSF 24-1. OMB Control Number 3145-0058")
    assert unverified == []


def test_identity_value_absent_from_text_is_still_flagged():
    """The value-as-evidence shortcut must not become a blanket pass."""
    extracted = {"program_id": "NSF 99-999", "source_quotes": {}}
    unverified, _ = sx._verify_source_quotes_detailed(extracted, "NSF 24-1 guide")
    assert "program_id" in unverified


def test_scalar_fields_stay_strict():
    """High-stakes scalars must NOT inherit the leniency: a quote whose
    opening is real but whose amount is fabricated is still caught."""
    extracted = {
        "budget_cap": 900000,
        "source_quotes": {"budget_cap": "Project Description may not exceed $900,000"},
    }
    unverified, _ = sx._verify_source_quotes_detailed(extracted, _TEXT)
    assert "budget_cap" in unverified


# ---------------------------------------------------------------------------
# 3. Contract shape: the new detail must survive coercion
# ---------------------------------------------------------------------------

def test_variants_keep_special_type_limits_out_of_the_headline():
    """The judgement call this rewrite depends on. NSF 24-1 states a 2-page
    Project Description for an Ideas Lab preliminary proposal; applying
    'most restrictive wins' blindly would tell every PI their Project
    Description is 2 pages and fail a compliant 15-page draft."""
    out = sx._coerce_extracted({
        "page_limits": {"project_description": 15},
        "page_limit_variants": [
            {"section": "project_description", "pages": 2,
             "applies_to": "Ideas Lab Preliminary Proposal", "page": 88},
        ],
    })
    assert out["page_limits"]["project_description"] == 15
    assert out["page_limit_variants"][0]["pages"] == 2
    assert "Ideas Lab" in out["page_limit_variants"][0]["applies_to"]


def test_variant_without_applies_to_is_dropped():
    """A variant that doesn't say what it applies to is indistinguishable
    from the general rule, which makes it worse than useless."""
    out = sx._coerce_extracted({"budget_cap_variants": [
        {"amount": 200000, "applies_to": "RAPID Proposal", "page": 83},
        {"amount": 999999, "page": 1},                     # no applies_to
        {"applies_to": "EAGER", "page": 84},               # no amount
    ]})
    assert len(out["budget_cap_variants"]) == 1
    assert out["budget_cap_variants"][0]["amount"] == 200000


def test_formatting_always_has_all_three_keys():
    """Frontend and Draft Critic read these without conditionals."""
    assert sx._coerce_extracted({})["formatting"] == {
        "font": None, "margins": None, "line_spacing": None}
    got = sx._coerce_extracted({"formatting": {"font": " Arial 10pt "}})
    assert got["formatting"]["font"] == "Arial 10pt"
    assert got["formatting"]["margins"] is None


# ---------------------------------------------------------------------------
# 4. No silent degradation
# ---------------------------------------------------------------------------

def test_primary_model_is_gemini_3_6_flash_in_global():
    """gemini-3.6-flash is published only in the 'global' Vertex location --
    it 404s in us-central1. The model and the location must move together."""
    assert sx._MODEL == "gemini-3.6-flash"
    assert sx._LOCATION == "global"


def test_falls_back_when_the_primary_model_fails(monkeypatch):
    """A user's upload must not fail just because the primary pair is
    unavailable; the previous model/location is tried before giving up."""
    tried = []

    class _Models:
        def __init__(self, loc): self.loc = loc

        def generate_content(self, model, contents, config):
            tried.append((model, self.loc))
            if model == sx._MODEL:
                raise RuntimeError("404 NOT_FOUND: publisher model not found")
            return type("R", (), {"text": '{"ok": true}'})()

    class _Client:
        def __init__(self, loc): self.models = _Models(loc)

    monkeypatch.setattr(sx, "_get_client", lambda loc: _Client(loc))
    raw = sx._call_gemini("hello", system_instruction="sys")

    assert tried == [(sx._MODEL, sx._LOCATION),
                     (sx._FALLBACK_MODEL, sx._FALLBACK_LOCATION)]
    assert json.loads(raw) == {"ok": True}


def test_returns_empty_string_when_every_model_fails(monkeypatch):
    """Total failure returns "" so _parse_response yields None and the
    endpoint surfaces a friendly error rather than a 500."""
    monkeypatch.setattr(sx, "_get_client", lambda loc: None)
    assert sx._call_gemini("hello") == ""


def test_consolidation_is_skipped_when_the_sweep_found_nothing(monkeypatch):
    """No findings means there is nothing to consolidate -- don't burn a call
    inventing a record out of an empty sweep."""
    calls = {"n": 0}

    def _fake(text, **kw):
        calls["n"] += 1
        return json.dumps({"page_limits": [], "required_attachments": [],
                           "deadlines": [], "budget_caps": [],
                           "eligibility": [], "formatting": []})

    monkeypatch.setattr(sx, "_call_gemini", _fake)
    assert sx.extract_from_pages(["some text"]) is None
    assert calls["n"] == 1                      # sweep only, no consolidate


def test_empty_document_makes_no_calls(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("should not call Gemini on an empty document")
    monkeypatch.setattr(sx, "_call_gemini", _boom)
    assert sx.extract_from_pages([]) is None
    assert sx.extract_from_pages(["", "   \n "]) is None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _stage_fake(sweep: dict, contract: dict):
    """Fake `_call_gemini` answering each pipeline stage in its own shape."""
    base = {"identity": {}, "page_limits": [], "required_attachments": [],
            "deadlines": [], "budget_caps": [], "eligibility": [],
            "formatting": []}
    base.update(sweep)
    sweep_json, contract_json = json.dumps(base), json.dumps(contract)

    def _fake(text, **kw):
        if kw.get("system_instruction") is sx._SWEEP_SYSTEM:
            return sweep_json
        return contract_json
    return _fake


def test_merged_list_is_grounded_by_its_items(monkeypatch):
    """required_attachments is merged from findings across many pages, so the
    model supplies a single covering quote only sometimes -- which made this
    field flash red on some runs and not others for identical input. The real
    evidence is that the items are named in the document."""
    text = ("A full proposal must contain a Project Summary, a Project "
            "Description, References Cited, and a Biographical Sketch.")
    extracted = {
        "required_attachments": ["Project Summary", "Project Description",
                                 "References Cited", "Biographical Sketch"],
        "source_quotes": {},                       # no quote at all
    }
    unverified, _ = sx._verify_source_quotes_detailed(extracted, text)
    assert unverified == []


def test_fabricated_list_is_still_flagged():
    """The item-evidence shortcut must not become a blanket pass: a list of
    documents the solicitation never mentions is still caught."""
    text = "A full proposal must contain a Project Summary and a Budget."
    extracted = {
        "required_attachments": ["Interpretive Dance Portfolio",
                                 "Notarized Horoscope", "Signed Baseball Card"],
        "source_quotes": {},
    }
    unverified, _ = sx._verify_source_quotes_detailed(extracted, text)
    assert "required_attachments" in unverified


def test_source_pages_is_always_flat_ints():
    """Regression guard for a live crash (2026-09-03).

    The model sometimes mirrors the per-entry shape of source_quotes and
    returns source_pages["page_limits"] as an OBJECT keyed by section. The
    review modal renders that value directly, and a bare object as a React
    child blanked the entire screen ("Objects are not valid as a React
    child"). The contract must hand the frontend a plain integer."""
    out = sx._coerce_extracted({"source_pages": {
        "deadline": 12,
        "budget_cap": "page 34",
        "page_limits": {"project_description": 50, "biosketch": 70},
        "required_attachments": [46, 52],
        "eligibility": None,
        "junk": {},
    }})
    pages = out["source_pages"]
    assert all(isinstance(v, int) for v in pages.values())
    assert pages["deadline"] == 12
    assert pages["budget_cap"] == 34
    assert pages["page_limits"] == 50      # earliest page of the group
    assert pages["required_attachments"] == 46
    assert "eligibility" not in pages
    assert "junk" not in pages


def test_source_pages_survives_a_non_dict():
    assert sx._coerce_extracted({"source_pages": "page 3"})["source_pages"] == {}
    assert sx._coerce_extracted({})["source_pages"] == {}


def test_multi_value_identity_field_is_grounded_by_its_parts():
    """A field naming several systems ("Research.gov, Grants.gov") is never
    one contiguous string in the PDF, so matching it whole flagged
    submission_portal on every solicitation accepting more than one --
    observed live on NSF 24-1."""
    text = ("proposals submitted to NSF must be submitted via use of "
            "Research.gov or Grants.gov.")
    extracted = {"submission_portal": "Research.gov, Grants.gov",
                 "source_quotes": {}}
    unverified, _ = sx._verify_source_quotes_detailed(extracted, text)
    assert unverified == []


def test_multi_value_field_with_an_invented_part_is_flagged():
    """Every named part must really be there -- one invented portal is caught."""
    text = "proposals must be submitted via use of Research.gov."
    extracted = {"submission_portal": "Research.gov, FakePortal.gov",
                 "source_quotes": {}}
    unverified, _ = sx._verify_source_quotes_detailed(extracted, text)
    assert "submission_portal" in unverified


# ---------------------------------------------------------------------------
# 5. The catch-all: no rule may be dropped for lack of a field
# ---------------------------------------------------------------------------
# Added after a measured recall audit of NSF 23-598 (HBCU-EiR). Of the 34 hard
# requirements the solicitation states, only 41% survived extraction. Every
# single loss had the same cause: the schema had no slot. 10 of them were never
# even reported by the sweep, because its categories (page limits, attachments,
# deadlines, budget caps, eligibility, formatting) had nowhere to put a rule
# like "no more than 30% of the budget can be allocated for equipment". With
# the catch-all, recall went 41% -> 94%.

def test_other_requirements_survive_coercion():
    out = sx._coerce_extracted({"other_requirements": [
        {"requirement": "Limit equipment to no more than 30% of the budget.",
         "category": "budget", "applies_to": "all proposals", "page": 6,
         "quote": "No more than 30% of the budget can be allocated for equipment."},
        {"requirement": "Budget for the PI to attend a two-day grantee meeting.",
         "category": "budget", "page": 10,
         "quote": "All proposals should budget for the PI to attend a two-day grantee meeting"},
    ]})
    reqs = out["other_requirements"]
    assert len(reqs) == 2
    assert reqs[0]["category"] == "budget"
    assert reqs[0]["page"] == 6
    assert "30%" in reqs[0]["requirement"]


def test_requirement_without_a_quote_is_dropped():
    """A rule with no supporting sentence is indistinguishable from an
    invention, and this list must not become a hallucination back door."""
    out = sx._coerce_extracted({"other_requirements": [
        {"requirement": "Submit in triplicate on vellum.", "category": "other"},
        {"requirement": "", "quote": "some real sentence"},
        {"requirement": "Real rule.", "quote": "Real rule appears here."},
    ]})
    assert [r["requirement"] for r in out["other_requirements"]] == ["Real rule."]


def test_requirements_are_deduplicated():
    """NSF 23-598 states the 30% equipment cap twice, in two sections."""
    out = sx._coerce_extracted({"other_requirements": [
        {"requirement": "Limit equipment to 30% of the budget.", "quote": "a", "page": 6},
        {"requirement": "limit  equipment TO 30% of the budget.", "quote": "b", "page": 9},
    ]})
    assert len(out["other_requirements"]) == 1


def test_unknown_category_falls_back_to_other_rather_than_dropping():
    """Never lose a genuine rule over a malformed category -- that would
    defeat the entire purpose of the catch-all."""
    out = sx._coerce_extracted({"other_requirements": [
        {"requirement": "Do the thing.", "quote": "Do the thing.",
         "category": "wildly-invented-category"},
    ]})
    assert len(out["other_requirements"]) == 1
    assert out["other_requirements"][0]["category"] == "other"


def test_other_requirements_defaults_to_empty_list():
    for raw in ({}, {"other_requirements": None}, {"other_requirements": "nope"}):
        assert sx._coerce_extracted(raw)["other_requirements"] == []


def test_sweep_schema_includes_the_catch_all():
    """Regression guard: the sweep must have somewhere to put a rule that
    fits none of its other categories, or the model silently discards it."""
    assert "other_requirements" in sx._FINDING_KEYS
    assert "other_requirements" in sx._SWEEP_SYSTEM
    assert "other_requirements" in sx._CONTRACT_KEYS
