"""THE GATE: does reading the PAPPG recover the rules we already know are in it?

WHY THIS DECIDES WHETHER THE APPROACH SHIPS
--------------------------------------------
Reading the PAPPG failed three times (CLAUDE.md): 598 requirements and 97 invented
sections from Chapter II whole; a scoped prompt that cut rows 36% without moving
the noise and merged four usable Project Summary rows into two; an output filter
that removed 11%. None had a checkpoint — there was no yardstick, so "598 rules"
could not be judged good or bad.

There is a yardstick now: the 14 rules in `rulebook_baseline.RULES`, curated by
hand from Research.gov's Content Instructions, which are NSF's own distillation of
these same PAPPG sections. If extraction does not recover them, the method is wrong.

WHY THIS DOES *NOT* REUSE THE SOLICITATION RECALL TEST'S SCORING
-----------------------------------------------------------------
The plan said to reuse `test_solicitation_requirements_recall.py`'s 5-gram shingle
overlap. That was wrong, and running it proved it. That method works because both
sides quote ONE document — "did the extraction find this sentence?" is then a fair
question. Here the two sides quote DIFFERENT documents that paraphrase each other:

  Research.gov: "This section should be narrative in nature and include internal
                 and external resources (both physical and personnel)."
  PAPPG:        "The description should be narrative in nature and must not include
                 any quantifiable financial information."
              + "Proposers should include an aggregated description of the internal
                 and external resources (both physical and personnel)..."

Same rules, different words, and one Research.gov sentence maps to two PAPPG ones.
Shingle overlap scored those 0.31 against a 0.34 floor — a failure that says
nothing about the extraction. Two further automated heuristics were tried and both
mis-scored rules the slice demonstrably states.

So the mapping is written out by hand below. That is not a weaker test; it is the
honest one. Cross-document equivalence is a judgement, and writing it down makes it
reviewable instead of hiding it in a similarity threshold.

WHAT THE MAPPING RECORDS, AND THE FINDING IT MADE
--------------------------------------------------
Two curated rules are NOT in the PAPPG's corresponding section at all. Verified
directly: the Facilities slice contains no "postdoctoral", no "senior", no "no
funds are being requested"; the References Cited slice never says "et al.".

**Research.gov and the PAPPG are complementary, not redundant.** Neither source
alone is the rulebook. That is worth knowing before anyone proposes replacing the
curated table with an extraction.

OPT-IN, LIVE MODEL, NEVER IN CI. Set PAPPG_RECALL=1. ~90s for all four sections.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("PAPPG_RECALL"),
    reason="set PAPPG_RECALL=1 to run against the live model")


# For each curated rule: the phrase the PAPPG uses for the SAME rule, or None when
# the PAPPG's section does not state it. Phrases are verbatim from the slices and
# are also asserted by tests/test_pappg_slicer.py, which needs no model.
_EXPECTED: dict = {
    "project_summary": {
        "pappg_ps_headings": "an overview, a statement on the intellectual merit",
        "pappg_ps_overview": "objectives and methods to be employed",
        "pappg_ps_merit": "advance knowledge",
        "pappg_ps_impacts": "benefit society",
        "pappg_ps_one_page": "not more than one page in length",
    },
    "project_description": {
        "pappg_pd_impacts_header": "separate section within the narrative",
        "pappg_pd_no_urls": "urls must not be used",
        "pappg_pd_page_limit": "15-page",
    },
    "references_cited": {
        "pappg_rc_scholarly": "accepted scholarly practice",
        # NOT in the PAPPG's References Cited section — verified: the slice never
        # says "et al.". It is a Research.gov Content Instruction only.
        "pappg_rc_et_al": None,
    },
    "facilities_equipment_and_other_resources": {
        "pappg_fe_no_financials": "must not include any quantifiable financial information",
        "pappg_fe_narrative": "narrative in nature",
        "pappg_fe_coverage": "internal and external resources (both physical and personnel)",
        # NOT in the PAPPG's Facilities section — verified: no "postdoctoral", no
        # "senior", no "no funds are being requested". Research.gov only.
        "pappg_fe_unfunded": None,
    },
}


@pytest.fixture(autouse=True)
def _live_gemini(monkeypatch):
    """conftest pins the AI layer OFF for every test (`_no_live_gemini`). This one
    needs it live. Without this, extraction returns nothing instantly and every
    assertion fails for a reason unrelated to the PAPPG — which is what happened
    the first time this ran."""
    monkeypatch.undo()


@pytest.fixture
def ingest():
    from services import pappg_ingest
    return pappg_ingest


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


@pytest.mark.parametrize("section_key", sorted(_EXPECTED))
def test_extraction_recovers_every_rule_the_pappg_states(ingest, section_key):
    result = ingest.read_section(section_key)
    extracted = result["rows"]
    assert extracted, f"{section_key} extracted nothing"

    haystack = " || ".join(_norm(r["source"]) for r in extracted)
    expected = _EXPECTED[section_key]
    checkable = {rid: p for rid, p in expected.items() if p}

    missing = [rid for rid, phrase in checkable.items()
               if _norm(phrase) not in haystack]

    print(f"\n{section_key}: {len(extracted)} rows, "
          f"{len(checkable) - len(missing)}/{len(checkable)} curated rules recovered, "
          f"{result['read_report']['elapsed_s']:.1f}s "
          f"({len(expected) - len(checkable)} not stated in this PAPPG section)")
    for rid in missing:
        print(f"   MISSED  {rid}  — expected {expected[rid]!r}")

    assert not missing, f"{section_key} lost: {missing}"


def test_every_extracted_row_is_grounded_in_its_slice(ingest):
    """Golden rule 2, with no floor. A quote that is not in the slice is a
    fabricated quote, and one is too many."""
    from services import text_match
    slices = ingest.load_slices()
    for key in ("project_summary", "references_cited"):
        for row in ingest.read_section(key, slices=slices)["rows"]:
            assert text_match.quote_in(slices[key]["text"], row["source"],
                                       drop_list_noise=True), \
                f"{key}: not in the slice — {row['source'][:90]!r}"


def test_no_row_is_filed_under_a_section_the_model_invented(ingest):
    """The failure that sank the first attempt: 97 invented sections. The slice
    supplies the key and the model's own `section` is discarded — this asserts the
    discarding really happens end to end."""
    slices = ingest.load_slices()
    for key in ("project_summary", "facilities_equipment_and_other_resources"):
        for row in ingest.read_section(key, slices=slices)["rows"]:
            assert row["section"] == key, f"expected {key}, got {row['section']!r}"


def test_a_slice_is_one_read_not_a_chunked_sweep(ingest):
    """The whole point of slicing. If a section needs multiple chunks or trips a
    cap, the slice is too big and the noise problem is coming back."""
    for key in ("project_summary", "project_description"):
        rr = ingest.read_section(key)["read_report"]
        assert rr["chunks"] == 1, f"{key} needed {rr['chunks']} chunks"
        assert not rr["hit_round_cap"], f"{key} hit the round cap"
        assert not rr["hit_time_cap"], f"{key} hit the time cap"


def test_the_two_sources_are_complementary(ingest):
    """A finding worth protecting: two curated rules are NOT in the PAPPG section
    they belong to. Neither source alone is the rulebook, so nobody should replace
    the curated table with an extraction. If a future PAPPG edition adds them, this
    goes red and the mapping above should be updated — deliberately."""
    slices = ingest.load_slices()
    fac = _norm(slices["facilities_equipment_and_other_resources"]["text"])
    for absent in ("postdoctoral", "senior", "no funds are being requested"):
        assert absent not in fac, \
            f"the PAPPG Facilities section now says {absent!r} — update _EXPECTED"
    assert "et al" not in _norm(slices["references_cited"]["text"])
