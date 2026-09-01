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


# ── VOTING, and why a single call was not enough ─────────────────────────────
#
# Measured 2026-08-31 on the awarded NSF EiR Project Summary
# (Desktop/NSF-EiR-Sections/01-Project-Summary.pdf), 18 live runs of the
# identical text: the pass reported NOTHING in 9 of them. The two real errors in
# that section -- a spurious comma in "...work; and, the PI extends..." and in
# "...workshops; and, each trainee..." -- each surfaced in about half the runs.
# A PI running the same check twice saw two different answers, which is what
# prompted this.
#
# The rules already had this protection (`draft_review.SEMANTIC_VOTES = 3`,
# merged by median); this pass had none. The UNION is deliberate and is NOT the
# median: at a ~50% per-call hit rate a 2-of-3 threshold recovers nothing (it is
# ~50% again), while the union takes the miss rate to ~1 in 8. The precision that
# costs is paid back by dropping `word_choice` below, not by the threshold.

def _fake_sequence(*per_call):
    """A different response per call, so a union is distinguishable from one call."""
    calls = {"n": 0}
    def gen(prompt, **kw):
        i = calls["n"]
        calls["n"] += 1
        return {"issues": list(per_call[i]) if i < len(per_call) else []}
    return gen


def test_the_proofreader_asks_more_than_once_and_a_missed_run_costs_nothing(monkeypatch):
    """The fix for the measured miss rate: a reader that overlooks an error no
    longer loses it, as long as another reader saw it.

    UPDATED 2026-08-31 with the threshold below -- this used to assert that ONE
    reader was enough (a plain union). That was right when a single call found a
    real error only ~50% of the time; once the extraction artifacts were filtered
    the per-call rate rose to 67-75% and one reader became noise rather than
    signal. Two readers now decide, and a third and fourth missing costs
    nothing."""
    text = "The PI trains fourr students; and, the PI extends their prior work."
    monkeypatch.setattr(pr.gemini_client, "generate_json", _fake_sequence(
        [{"quote": "trains fourr students", "kind": "spelling", "detail": '"fourr" -> "four".'}],
        [{"quote": "trains fourr students", "kind": "spelling", "detail": '"fourr" -> "four".'},
         {"quote": "; and, the PI extends", "kind": "punctuation", "detail": "Spurious comma."}],
        [{"quote": "; and, the PI extends", "kind": "punctuation", "detail": "Spurious comma."}],
        [], [],
    ))
    out = pr.proofread(text, votes=5)
    assert len(out) == 2, [r["evidence"] for r in out]
    assert {r["kind"] for r in out} == {"spelling", "punctuation"}


def test_one_error_quoted_with_different_spans_is_reported_once(monkeypatch):
    """The three runs quote the SAME comma with different spans -- exactly what
    the live runs did. Keyed on the quote alone this would print three rows for
    one error, which is worse than the miss it was meant to fix."""
    text = "Nothing in the literature outside the PI's work; and, the PI extends their prior use."
    monkeypatch.setattr(pr.gemini_client, "generate_json", _fake_sequence(
        [{"quote": "work; and, the PI extends", "kind": "punctuation", "detail": "Spurious comma."}],
        [{"quote": "; and, the PI extends", "kind": "punctuation", "detail": "Spurious comma."}],
        [{"quote": "and, the PI extends their prior use", "kind": "punctuation",
          "detail": "Spurious comma after 'and'."}],
    ))
    out = pr.proofread(text, votes=3)
    assert len(out) == 1, [r["evidence"] for r in out]
    # The shortest span wins: this module has already had to fix a row that
    # quoted a whole 445-character paragraph instead of the part at fault.
    assert out[0]["evidence"] == "; and, the PI extends"


def test_two_different_errors_in_one_sentence_are_not_merged(monkeypatch):
    """The guard on the containment merge. Same sentence, different faults --
    merging them would silently delete a real error."""
    text = "The PI trains fourr students; and, the PI extends their prior work."
    monkeypatch.setattr(pr.gemini_client, "generate_json", _fake_sequence(
        [{"quote": "trains fourr students", "kind": "spelling", "detail": "typo"},
         {"quote": "students; and, the PI", "kind": "punctuation", "detail": "Spurious comma."}],
    ))
    out = pr.proofread(text, votes=1)
    assert len(out) == 2


def test_a_word_choice_row_is_not_reported(monkeypatch):
    """Fix 2, and it is what pays for the union above.

    Both false positives measured in those 18 runs were this kind -- "use
    'respectively' instead of the adjective 'respective'" over NSF-correct prose
    ("photons and electrons, for respective examples"). That is a REWRITE, which
    `_SYSTEM` already forbids in words, and CLAUDE.md records that tightening the
    prompt against it cost two thirds of the recall on real errors. So it is
    filtered in code and the prompt is left alone."""
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "for respective examples", "kind": "word_choice",
                                "detail": "Use 'respectively'."}]))
    assert pr.proofread("photons and electrons, for respective examples).") == []


def test_an_unrecognised_kind_is_not_reported(monkeypatch):
    """An unknown kind was already bucketed as `word_choice`, so it goes with it.
    Conservative on purpose: this pass drops a doubtful row rather than showing
    one, the same direction as the quote gate above."""
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "trains fourr students", "kind": "style",
                                "detail": "Consider rephrasing."}]))
    assert pr.proofread("The PI trains fourr students.") == []


def test_the_real_kinds_still_survive(monkeypatch):
    """The mirror of the two tests above: dropping word_choice must not quietly
    take spelling, grammar or punctuation with it."""
    text = "The PI trains fourr students; and, the objectives is unclear."
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "trains fourr students", "kind": "spelling", "detail": "typo"},
                               {"quote": "; and, the objectives", "kind": "punctuation", "detail": "comma"},
                               {"quote": "the objectives is unclear", "kind": "grammar", "detail": "agreement"}]))
    out = pr.proofread(text, votes=1)
    assert {r["kind"] for r in out} == {"spelling", "punctuation", "grammar"}


def test_a_vote_that_raises_does_not_lose_the_round(monkeypatch):
    """Same contract as `_voted_batch`: one lost call must not cost the answer."""
    calls = {"n": 0}
    def gen(prompt, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return {"issues": [{"quote": "trains fourr students", "kind": "spelling",
                            "detail": "typo"}]}
    monkeypatch.setattr(pr.gemini_client, "generate_json", gen)
    out = pr.proofread("The PI trains fourr students.", votes=3)
    assert len(out) == 1


def test_use_ai_false_still_makes_no_call(monkeypatch):
    """Voting must not reintroduce a model call on the deterministic-only path."""
    def boom(prompt, **kw):
        raise AssertionError("the model must not be called when use_ai=False")
    monkeypatch.setattr(pr.gemini_client, "generate_json", boom)
    assert pr.proofread("The PI trains fourr students.", use_ai=False) == []


# ── OUR OWN EXTRACTION DAMAGE IS NOT THE AUTHOR'S SPELLING ───────────────────
#
# Measured 2026-08-31 over TEN real HTTP uploads of one awarded Project Summary
# PDF. The two genuine errors were steady at 8/10 each. Everything else was an
# artifact of how we read the PDF, each surfacing in ONE run and making the
# wording count swing 0..6:
#
#     1/10 spelling  "Lie superal- gebra into their"
#     1/10 spelling  "theo- retical physics increases."
#     1/10 spelling  "fi- nite presentations of diagonal"
#     1/10 spelling  "Histor- ically Black Colleges"
#     1/10 spelling  "commu- nities in mathematics by"
#     1/10 spelling  "Submitted/PI: Dwight A Williams Ii /Proposal No:"
#
# Every one is a word the TYPESETTER split at a line end, or Research.gov page
# furniture stamped on the page. `text_match` already treats a line-end hyphen
# as ambiguous for grounding and `mechanical_checks` already recognises running
# furniture; the proofreader used neither, so it reported our damage as the PI's
# spelling. An author cannot act on either -- the words are spelled correctly in
# their document -- so these are noise by definition, and the loudest single
# cause of "it gives me a different answer every time".

def test_a_word_split_by_the_typesetter_is_not_a_spelling_error(monkeypatch):
    text = ("The PI studies Lie superal- gebra representations and their theo- "
            "retical consequences.")
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "Lie superal- gebra", "kind": "spelling",
                                "detail": '"superal" is not a word.'},
                               {"quote": "theo- retical", "kind": "spelling",
                                "detail": '"theo" is not a word.'}]))
    assert pr.proofread(text, votes=1) == []


def test_a_real_error_beside_a_line_break_hyphen_still_survives(monkeypatch):
    """The guard on the guard. Only the row QUOTING the split word is dropped --
    dropping every row in a hyphenated paragraph would silence real findings in
    exactly the documents this tool is for."""
    text = ("The PI studies Lie superal- gebra representations; and, the PI "
            "extends that work.")
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "Lie superal- gebra", "kind": "spelling",
                                "detail": "typo"},
                               {"quote": "; and, the PI extends", "kind": "punctuation",
                                "detail": "Spurious comma."}]))
    out = pr.proofread(text, votes=1)
    assert [r["evidence"] for r in out] == ["; and, the PI extends"]


def test_a_real_hyphenated_compound_is_not_treated_as_damage(monkeypatch):
    """`bosonic-fermionic` is the author's own hyphen with no line break after
    it, so a genuine error quoting it must still be reported. The artifact is a
    dash followed by WHITESPACE -- the same rule text_match uses."""
    text = "The bosonic-fermionic sytems are studied."
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "bosonic-fermionic sytems", "kind": "spelling",
                                "detail": '"sytems" should be "systems".'}]))
    out = pr.proofread(text, votes=1)
    assert len(out) == 1


def test_a_submission_stamp_is_not_proofread(monkeypatch):
    """Research.gov stamps a header on every page of a submitted proposal, and
    pdfplumber reads it as prose. It is not the author's writing and they cannot
    edit it -- observed live as a "spelling" row for "Dwight A Williams Ii".

    ONE OCCURRENCE, deliberately. `mechanical_checks._running_furniture` needs a
    line to REPEAT three times, which never happens in a one-page section -- the
    exact case measured. So the stamp is recognised by its SHAPE, not by
    repetition. The first version of this test used two copies of the line and
    passed against a detector that could not have caught the real thing."""
    text = ("Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008\n"
            "Overview\nThe project studies representations.\n")
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "Dwight A Williams Ii", "kind": "spelling",
                                "detail": '"Ii" should be "II".'}]))
    assert pr.proofread(text, votes=1) == []


def test_a_split_word_is_dropped_even_when_the_model_closes_the_gap(monkeypatch):
    """The first version of this filter tested the QUOTE for "dash + space", and
    3 artifacts still got through in 10 live uploads -- because the model quotes
    the word BOTH ways. The draft holds "superal- gebra"; the model returned
    "Lie superal-gebra", with the gap closed, and sailed past the check.

    So the damage is derived from the TEXT, which is the only place that knows
    the typesetter split it, and matched against the quote in either form."""
    text = "The PI studies Lie superal- gebra representations."
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "Lie superal-gebra", "kind": "spelling",
                                "detail": '"superalgebra" is misspelt.'}]))
    assert pr.proofread(text, votes=1) == []


# ── A THRESHOLD, NOW THAT ONE IS AFFORDABLE ─────────────────────────────────
#
# The union of 3 was chosen when a single call surfaced the real errors only
# ~50% of the time, where a 2-of-3 threshold recovers ~50% -- i.e. nothing. That
# rate was being dragged down by our own extraction damage; with the artifact
# filters above, a single call now hits 8/12 and 9/12 on the two real errors and
# 1/12 on noise. Measured per call, 2026-08-31.
#
# At those rates the arithmetic flips:
#     union of 3   -> real 98%, noise 22%   <- the count swinging 0/2/3
#     >=2 of 5     -> real 98%, noise  5%
# Same recall, a quarter of the noise. Five calls still run concurrently, so the
# wall clock is one call's.

@pytest.mark.skip(reason="threshold reverted: it halved recall on real errors "
                         "because the votes are correlated, not independent -- "
                         "see PROOFREAD_MIN_VOTES. Kept as the record of what "
                         "was tried and what it measured.")
def test_an_issue_only_one_reader_saw_is_dropped(monkeypatch):
    """The noise rule. One reader in five is not a finding."""
    text = "The PI trains fourr students; and, the PI extends that work."
    seq = [
        [{"quote": "; and, the PI extends", "kind": "punctuation", "detail": "comma"},
         {"quote": "trains fourr students", "kind": "spelling", "detail": "typo"}],
        [{"quote": "; and, the PI extends", "kind": "punctuation", "detail": "comma"}],
        [{"quote": "; and, the PI extends", "kind": "punctuation", "detail": "comma"}],
        [{"quote": "; and, the PI extends", "kind": "punctuation", "detail": "comma"}],
        [{"quote": "; and, the PI extends", "kind": "punctuation", "detail": "comma"}],
    ]
    monkeypatch.setattr(pr.gemini_client, "generate_json", _fake_sequence(*seq))
    out = pr.proofread(text, votes=5)
    assert [r["evidence"] for r in out] == ["; and, the PI extends"]


def test_an_issue_two_readers_saw_is_kept(monkeypatch):
    """Still true under the union: two readers agreeing is certainly enough."""
    text = "The PI trains fourr students; and, the PI extends that work."
    seq = [
        [{"quote": "trains fourr students", "kind": "spelling", "detail": "typo"}],
        [{"quote": "trains fourr students", "kind": "spelling", "detail": "typo"}],
        [], [], [],
    ]
    monkeypatch.setattr(pr.gemini_client, "generate_json", _fake_sequence(*seq))
    out = pr.proofread(text, votes=5)
    assert [r["evidence"] for r in out] == ["trains fourr students"]


def test_a_single_reader_still_decides_when_there_is_only_one(monkeypatch):
    """votes=1 must stay a plain single call -- a threshold of 2 over 1 vote
    would silently report nothing at all."""
    monkeypatch.setattr(pr.gemini_client, "generate_json",
                        _fake([{"quote": "trains fourr students", "kind": "spelling",
                                "detail": "typo"}]))
    assert len(pr.proofread("The PI trains fourr students.", votes=1)) == 1
