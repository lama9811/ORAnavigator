"""Mechanical mistakes in a draft — objective, quotable, no model involved.

This is the half the review was missing. Until now the only deterministic checks
were page limits, required attachments and the budget cap: nothing looked for an
actual ERROR. A draft could be complete against every requirement, score well,
and still go out with "[insert PI name]" in it.

WHY DETERMINISTIC AND NOT A GRADE (golden rule 1). Every row here is a fact
about the text with the offending words quoted, never a judgement of the
writing. Asked to rate how *well written* a proposal is, this app has no ground
truth to calibrate against — the sample library is 22 FUNDED proposals and zero
declined ones — so a quality number would be the model's opinion presented as a
measurement. A mistake needs no calibration: the placeholder is either there or
it is not.

FALSE POSITIVES ARE THE REAL RISK. A checker that cries wolf gets ignored, and
then the real placeholder goes out too. Every guard below has its own test.
"""
from services import mechanical_checks as mc


def _kinds(text, **kw):
    return {m["kind"] for m in mc.find_mistakes(text, **kw)}


# ── placeholders ────────────────────────────────────────────────────────────

def test_placeholder_text_is_caught_and_quoted():
    text = "Project Description\nThe PI, [insert name], will lead the work. TBD."
    found = [m for m in mc.find_mistakes(text) if m["kind"] == "placeholder"]
    assert found, "no placeholder reported"
    quoted = " ".join(m["evidence"].lower() for m in found)
    assert "[insert name]" in quoted or "insert name" in quoted
    assert "tbd" in quoted


def test_a_literal_figure_X_is_a_placeholder():
    assert "placeholder" in _kinds("As shown in Figure X, the signal rises.")


def test_a_bracketed_citation_is_NOT_a_placeholder():
    """[12] is a reference marker, and flagging every numeric citation would
    bury the one real placeholder in noise."""
    assert "placeholder" not in _kinds(
        "Prior work [12] established the mechanism, as did others [3].")


# ── broken cross-references ─────────────────────────────────────────────────

def test_a_reference_to_a_figure_that_has_no_caption_is_flagged():
    text = ("Figure 1: conductivity against salinity.\n"
            "As shown in Figure 1 the response is linear, and Figure 4 confirms it.\n")
    found = [m for m in mc.find_mistakes(text) if m["kind"] == "broken_reference"]
    assert found and "Figure 4" in found[0]["detail"]


def test_a_draft_with_NO_captions_at_all_is_left_alone():
    """THE important guard. Pasted text loses image captions routinely, so a
    draft that mentions figures but carries no captions has almost certainly
    lost them in the paste — not written a broken reference. Flagging every
    figure mention there would make this whole feature noise."""
    text = "As shown in Figure 1 the response is linear, and Figure 2 confirms it."
    assert "broken_reference" not in _kinds(text)


# ── duplicated paragraphs ───────────────────────────────────────────────────

def test_a_paragraph_pasted_twice_is_flagged():
    para = ("The zwitterionic network responds to salinity through a change in "
            "ion transport, which we measure by electrochemical impedance "
            "spectroscopy across the full estuarine range of interest here.")
    text = f"Project Description\n\n{para}\n\nSomething else entirely.\n\n{para}\n"
    assert "duplicate_paragraph" in _kinds(text)


def test_a_repeated_HEADING_is_not_a_duplicate_paragraph():
    """Short lines repeat legitimately — headings, labels, "Not applicable"."""
    text = "Broader Impacts\n\nWe train students.\n\nBroader Impacts\n\nAnd more.\n"
    assert "duplicate_paragraph" not in _kinds(text)


# ── the narrative contradicting the saved budget ────────────────────────────

def test_a_total_in_the_narrative_that_contradicts_the_budget_is_flagged():
    """The app holds both numbers and has never compared them."""
    text = "Budget Justification\nThe total project cost is $450,000 over three years."
    budget = {"total_cost": 512340}
    found = [m for m in mc.find_mistakes(text, budget=budget)
             if m["kind"] == "number_conflict"]
    assert found
    assert "450,000" in found[0]["evidence"]
    assert "512,340" in found[0]["detail"]


def test_a_matching_total_is_not_flagged():
    text = "The total project cost is $512,340."
    assert "number_conflict" not in _kinds(text, budget={"total_cost": 512340})


def test_no_saved_budget_means_no_number_check():
    """Never invent the comparison: with no budget there is nothing to conflict
    with, and guessing which figure is 'the' total would be a fabricated error."""
    text = "The total project cost is $450,000."
    assert "number_conflict" not in _kinds(text)


def test_a_dollar_figure_that_is_not_a_total_is_ignored():
    text = "Equipment: a potentiostat at $18,000, well under the cap."
    assert "number_conflict" not in _kinds(text, budget={"total_cost": 512340})


# ── citations with no reference list ────────────────────────────────────────

def test_citations_with_no_reference_section_are_flagged():
    text = ("Prior work (Smith 2019) and (Lee et al. 2021) established this, "
            "and later results (Jones 2022) confirmed it.")
    assert "missing_references" in _kinds(text)


def test_citations_WITH_a_reference_section_are_fine():
    text = ("Prior work (Smith 2019), (Lee et al. 2021) and (Jones 2022).\n\n"
            "References Cited\nSmith, J. (2019). A paper.\n")
    assert "missing_references" not in _kinds(text)


def test_one_stray_year_in_parentheses_is_not_a_citation_pattern():
    """Below the threshold on purpose: "(2019)" appears in prose all the time."""
    text = "The instrument was purchased in a prior award (2019) and still runs."
    assert "missing_references" not in _kinds(text)


# ── the contract ────────────────────────────────────────────────────────────

def test_a_clean_draft_reports_nothing():
    text = ("Project Description\n\nWe will synthesise three copolymers and "
            "measure their conductivity across the estuarine salinity range.\n")
    assert mc.find_mistakes(text) == []


def test_every_mistake_quotes_the_text_it_found():
    """Same rule the AI findings live under: an unquotable claim is dropped. A
    mistake the PI cannot locate in their own draft is not actionable."""
    text = ("The PI, [insert name], will lead. TBD.\n\n"
            "Figure 2: results.\nSee Figure 9 for details.\n")
    for m in mc.find_mistakes(text):
        assert m["evidence"], f"{m['kind']} has no evidence"
        assert m["evidence"].strip() in " ".join(text.split()) or \
            m["evidence"].strip().lower() in text.lower(), \
            f"{m['kind']} quoted text that is not in the draft: {m['evidence']!r}"


def test_the_ordinary_phrase_to_do_is_not_a_placeholder():
    """Shipped as a false positive and caught by a real user: "would allow us TO
    DO much more work" was reported as unfilled placeholder text. The pattern was
    case-insensitive `TO\\s?DO`, which matches ordinary English. A checker that
    cries wolf gets ignored, and then the genuine placeholder ships too."""
    text = ("This equipment would allow us to do much more work in this area, and "
            "there is much to do before the deadline.")
    assert mc.find_mistakes(text) == []


def test_a_SHOUTED_placeholder_still_counts():
    """The convention is capitals. TODO as one word is a placeholder in any case
    — nobody writes "todo" in proposal prose — but the spaced form only when
    shouted."""
    assert "placeholder" in _kinds("Methods TO DO before submission.")
    assert "placeholder" in _kinds("Methods TODO before submission.")
    assert "placeholder" in _kinds("Budget: XXX")


def test_lowercase_xxx_in_prose_is_not_a_placeholder():
    assert "placeholder" not in _kinds("The file is stored at path/xxx/data.")
