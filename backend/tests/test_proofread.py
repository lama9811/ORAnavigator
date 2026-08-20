"""The AI proofreading pass: quoted, advisory, and never part of a score.

WHY IT EXISTS. Deterministic language rules have a hard ceiling. A PI pasted a
Project Summary with three errors and got zero mistakes: `fourr` (a one-off typo,
not a known misspelling), `trains fourr undergraduates, and two graduate
students` (a spurious comma -- grammar), and a sentence ending `students...`
(the last character is a period, so it passes every punctuation rule). Three
errors, three different causes, none reachable by regex.

WHY IT IS FENCED. `mechanical_checks` is model-free by contract and its rows are
called "found by a rule, not a judgement" on screen. Model output must not enter
that list or that sentence stops being true, so this returns its own rows under
its own key. It is advisory, it never enters the completeness score, and every
row must quote text that is actually in the draft -- golden rule 2, the same
`quote_in` the reviewer is held to.
"""
import pytest

from services import proofread as pr


def _fake(rows):
    def gen(prompt, **kw):
        return {"issues": rows}
    return gen


def test_a_quoted_issue_survives(monkeypatch):
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "trains fourr undergraduates",
                                "kind": "spelling", "detail": '"fourr" should be "four".'}]))
    out = pr.proofread("The project trains fourr undergraduates, and two students.")
    assert len(out) == 1
    assert out[0]["evidence"] == "trains fourr undergraduates"
    assert out[0]["source"] == "ai"


def test_an_issue_whose_quote_is_not_in_the_draft_is_DROPPED(monkeypatch):
    """The whole grounding contract. A proofreader that invents the sentence it
    is correcting sends an author hunting through their own draft for text that
    was never there -- worse than reporting nothing."""
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "trains five undergraduates",
                                "kind": "spelling", "detail": "typo"}]))
    assert pr.proofread("The project trains fourr undergraduates.") == []


def test_whitespace_differences_do_not_drop_a_real_quote(monkeypatch):
    """A pasted draft is hard-wrapped; the model's quote has single spaces. The
    shared membership test collapses both sides -- the reason `quote_in` exists."""
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "trains fourr undergraduates",
                                "kind": "spelling", "detail": "typo"}]))
    assert len(pr.proofread("The project trains fourr\n   undergraduates.")) == 1


def test_no_model_means_no_rows_never_an_error(monkeypatch):
    """Golden rule 3. A proofreading pass is the least important thing on the
    screen; it must never be the reason a review fails."""
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        lambda *a, **k: None)
    assert pr.proofread("Anything at all here.") == []


def test_use_ai_false_makes_no_call(monkeypatch):
    called = []
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        lambda *a, **k: called.append(1) or {"issues": []})
    assert pr.proofread("Some text.", use_ai=False) == []
    assert not called


def test_empty_text_makes_no_call(monkeypatch):
    called = []
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        lambda *a, **k: called.append(1) or {"issues": []})
    assert pr.proofread("   ") == []
    assert not called


def test_the_prompt_forbids_style_and_quality_opinions():
    """The line between proofreading and the deleted Drafting Coach. This must
    report ERRORS, never rewrite anyone's science or grade their prose -- the
    same boundary `section_guidance` holds when it requires a suggestion but
    bans writing the sentence for the author."""
    sys = pr._SYSTEM.lower()
    assert "style" in sys and "tone" in sys
    assert "do not" in sys or "never" in sys


def test_the_prompt_tells_it_to_leave_technical_vocabulary_alone():
    """`zwitterionic`, `Donnan`, `potentiostat`, `MTDC`. A proofreader that
    "corrects" correct science is turned off, and then nothing is checked."""
    assert "technical" in pr._SYSTEM.lower()


def test_the_model_and_region_are_named(monkeypatch):
    """gemini_client.DEFAULT_MODEL is 2.5-flash, so a forgotten kwarg silently
    downgrades; 3.6-flash 404s outside `global`. Same guard as both review paths."""
    seen = {}

    def spy(prompt, **kw):
        seen.update(kw)
        return {"issues": []}

    monkeypatch.setattr(pr.gemini_client, "generate_json", spy)
    pr.proofread("Some text to check.")
    assert seen.get("model") == pr.MODEL
    assert seen.get("location") == pr.MODEL_LOCATION


# ── wired into Section Check ────────────────────────────────────────────────

def test_review_section_returns_wording_rows_under_their_own_key(monkeypatch):
    """Its OWN key, not `mistakes`. That list is captioned "found by a rule, not
    a judgement" and is model-free by contract; putting model rows in it makes
    the caption false."""
    from services import draft_review as dr

    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "trains fourr undergraduates",
                                "kind": "spelling", "detail": '"fourr" -> "four".'}]))
    out = dr.review_section(
        "Broader Impacts\nThe project trains fourr undergraduates each year.",
        section="project_summary", rulebook="the PAPPG", use_ai=True)

    assert [r["evidence"] for r in out["wording"]] == ["trains fourr undergraduates"]
    assert out["mistakes"] == [], "model rows must not enter the deterministic list"


def test_wording_rows_never_touch_the_score(monkeypatch):
    """A comma splice is not incompleteness against a solicitation, and the
    percentage is already over-read."""
    from services import draft_review as dr

    text = "Overview\nThe project trains fourr undergraduates each year at an HBCU."
    monkeypatch.setattr(pr.gemini_client, "generate_json", _fake([]))
    clean = dr.review_section(text, section="project_summary",
                              rulebook="the PAPPG", use_ai=True)["score"]
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "trains fourr undergraduates",
                                "kind": "spelling", "detail": "typo"}]))
    dirty = dr.review_section(text, section="project_summary",
                              rulebook="the PAPPG", use_ai=True)["score"]
    assert clean == dirty, "wording rows moved the score"


def test_use_ai_false_suppresses_the_proofread_too(monkeypatch):
    """`use_ai=False` must mean NO model work anywhere in the call, not "no
    semantic review". A caller asking for a deterministic-only check and getting
    a Gemini round-trip anyway is the gap CLAUDE.md already records open for
    `review_draft`; do not add a second one."""
    from services import draft_review as dr

    called = []
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        lambda *a, **k: called.append(1) or {"issues": [
                            {"quote": "trains fourr", "kind": "spelling",
                             "detail": "typo"}]})
    out = dr.review_section("Overview\nThe project trains fourr students.",
                            section="project_summary", rulebook="the PAPPG",
                            use_ai=False)
    assert out["wording"] == []


def test_the_prompt_names_the_two_item_comma_because_the_model_will_not_find_it_otherwise():
    """A PI wrote "trains fourr undergraduates, and two graduate students" and
    the comma went unreported across three runs. It was NOT a grounding drop —
    instrumenting the raw response showed the model never returned it at all,
    twice over. Naming the shape fixes it; the prompt is where that has to live,
    because nothing deterministic can see a discretionary comma.

    A/B measured before shipping: the user's text went 2 rows -> 3, and four
    clean drafts stayed at 0 across three runs each. The fifth draft gained one
    row that turned out to be a REAL missing comma after an introductory clause
    in a draft this repo wrote — a find, not a false positive.
    """
    sys = pr._SYSTEM
    assert '"and"' in sys and "TWO items" in sys, (
        "the two-item comma shape is not named; the model does not report it")
