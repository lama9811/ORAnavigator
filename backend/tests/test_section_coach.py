"""Unit tests for the Section Drafting Coach (Phase 2).

The conftest autouse fixture forces Gemini OFFLINE, so these exercise the
deterministic paths: section catalog, outline skeleton, the keyword-based review
fallback, and the anti-hallucination evidence check. The AI path is layered on
top of these and falls back to them when the LLM is unavailable."""
from services import section_coach as sc


# ── catalog ────────────────────────────────────────────────────────────────

def test_available_sections_by_sponsor():
    nsf = [s["key"] for s in sc.available_sections("NSF")]
    assert "project_summary" in nsf and "broader_impacts" in nsf
    nih = [s["key"] for s in sc.available_sections("NIH")]
    assert "specific_aims" in nih and "research_strategy" in nih
    # Unknown sponsor falls back to the generic set.
    assert [s["key"] for s in sc.available_sections("Some Foundation")] == \
        ["abstract", "narrative", "data_management_plan"]


# ── outline ──────────────────────────────────────────────────────────────--

def test_outline_returns_deterministic_structure():
    o = sc.outline_section("NSF", "project_summary")  # no topic, AI off -> ai False
    assert o["mode"] == "outline" and o["ai"] is False
    headings = [x["heading"] for x in o["outline"]]
    assert "A labeled 'Intellectual Merit' statement" in headings
    assert "A labeled 'Broader Impacts' statement" in headings
    assert o["pitfalls"] and o["target_words"] and o["purpose"]


def test_outline_unknown_section_is_none():
    assert sc.outline_section("NSF", "nope") is None


# ── review (deterministic keyword fallback, AI off) ────────────────────────

def test_review_flags_missing_elements():
    draft = ("Overview: this project studies X. Intellectual Merit: it advances "
             "theory in the field with rigorous methods.")
    r = sc.review_section("NSF", "project_summary", draft)
    assert r["ai"] is False
    statuses = {c["item"]: c["status"] for c in r["checklist"]}
    # Overview + Intellectual Merit present; Broader Impacts missing -> unclear.
    assert statuses["A labeled 'Broader Impacts' statement"] == "unclear"
    assert any("Broader Impacts" in s for s in r["suggestions"])
    assert r["word_count"] > 0


def test_review_all_covered_when_keywords_present():
    draft = ("Overview of the work. Intellectual Merit: advances the field. "
             "Broader Impacts: trains underrepresented students and broadens participation.")
    r = sc.review_section("NSF", "project_summary", draft)
    assert all(c["status"] == "covered" for c in r["checklist"])
    assert r["suggestions"] == []


def test_review_empty_draft_prompts_for_text():
    r = sc.review_section("NSF", "project_summary", "")
    assert r["word_count"] == 0 and r["checklist"] == []


def test_review_unknown_section_is_none():
    assert sc.review_section("NSF", "nope", "text") is None


# ── anti-hallucination: drop 'covered' claims not quotable in the draft ─────

def test_outline_includes_word_targets():
    o = sc.outline_section("NSF", "project_summary")
    assert o["target_min"] == 200 and o["target_max"] == 500


def test_review_includes_targets_length_and_clarity():
    draft = "Overview of the work. Intellectual Merit: it advances theory. Broader Impacts: trains students."
    r = sc.review_section("NSF", "project_summary", draft)
    assert "target_min" in r and "length_status" in r and "clarity" in r
    assert r["length_status"] in ("ok", "short", "long")


def test_review_flags_too_long():
    long_draft = "word " * 700  # well over project_summary's 500 max
    r = sc.review_section("NSF", "project_summary", long_draft)
    assert r["length_status"] == "long"


def test_review_surfaces_solicitation_constraints():
    ctx = {"required_attachments": ["Data Management Plan"], "eligibility": "Tenure-track only",
           "page_limits": {"project_description": 15}}
    r = sc.review_section("NSF", "project_description", "Some draft text here.", ctx)
    sc_block = r["solicitation_constraints"]
    assert sc_block.get("required_attachments") == ["Data Management Plan"]
    assert sc_block.get("eligibility") == "Tenure-track only"


# ── clarity check (deterministic) ──────────────────────────────────────────

def test_clarity_flags_long_sentence():
    text = "This " + "very long ".join(["clause"] * 45) + " end."
    issues = [i["type"] for i in sc.clarity_check(text)]
    assert "long_sentences" in issues


def test_clarity_flags_undefined_acronym():
    issues = sc.clarity_check("We used the QWERTY method to study things.")
    assert any(i["type"] == "acronyms" for i in issues)


def test_clarity_ignores_defined_acronym_and_common_ones():
    # CRISPR is defined inline; NSF/PI are common -> no acronym flag.
    issues = sc.clarity_check("The NSF PI used Clustered Regularly Interspaced (CRISPR) tools.")
    assert not any(i["type"] == "acronyms" for i in issues)


def test_clarity_clean_text_is_empty():
    assert sc.clarity_check("We measured growth. Results were clear. The team will share data.") == []


def test_verify_evidence_demotes_unquotable_covered_claims():
    checklist = [
        {"item": "X", "status": "covered", "evidence": "phrase not in the draft", "note": ""},
        {"item": "Y", "status": "covered", "evidence": "a real quote", "note": ""},
        {"item": "Z", "status": "missing", "evidence": "", "note": "absent"},
    ]
    out = sc._verify_evidence(checklist, "the draft includes a real quote right here")
    assert out[0]["status"] == "unclear" and out[0]["evidence"] == ""   # unquotable -> demoted
    assert out[1]["status"] == "covered"                                # quotable -> kept
    assert out[2]["status"] == "missing"                                # untouched


# ── solicitation-aware outline (Phase 1) ────────────────────────────────────

def test_outline_surfaces_solicitation_constraints():
    """When a solicitation context is passed, the outline carries the same
    'match THIS solicitation' block the review already returns."""
    ctx = {"required_attachments": ["Data Management Plan"],
           "eligibility": "Tenure-track only",
           "page_limits": {"project_description": 15}}
    o = sc.outline_section("NSF", "project_description", "", ctx)
    block = o["solicitation_constraints"]
    assert block.get("required_attachments") == ["Data Management Plan"]
    assert block.get("eligibility") == "Tenure-track only"
    assert block.get("page_limits") == {"project_description": 15}


def test_outline_without_context_has_empty_constraints():
    o = sc.outline_section("NSF", "project_summary")
    assert o["solicitation_constraints"] == {}


# ── worked-example sample links (Phase 2) ───────────────────────────────────

def test_section_samples_all_resolve_to_real_samples():
    """Every id in SECTION_SAMPLES must exist in the Sample Proposals Library,
    so a coach link can never 404."""
    from services import sample_proposals as samples
    for section_key, sample_id in sc.SECTION_SAMPLES.items():
        assert samples.get_sample(sample_id) is not None, \
            f"{section_key} -> {sample_id} does not resolve"


def test_outline_includes_sample_hint():
    o = sc.outline_section("NIH", "specific_aims")
    assert o["sample"]["id"] == "nih-specific-aims-research-strategy"
    assert o["sample"]["title"]                     # carries a human title
    nsf = sc.outline_section("NSF", "project_summary")
    assert nsf["sample"]["id"] == "nsf-ej-idss-planning-proposal"


def test_review_includes_sample_hint():
    r = sc.review_section("NIH", "specific_aims", "Some aims draft text.")
    assert r["sample"]["id"] == "nih-specific-aims-research-strategy"


def test_unmapped_section_has_no_sample_hint():
    o = sc.outline_section("Some Foundation", "abstract")
    assert o["sample"] is None
