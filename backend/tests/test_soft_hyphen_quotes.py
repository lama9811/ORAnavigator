"""A word broken across a line by a hyphen must not cost a real quote.

WHAT WENT WRONG
---------------
A PI pasted a Project Summary copied out of a typeset PDF. Twelve of its words
were broken across lines by a typesetter's hyphen -- `un-\\ndergraduate`,
`Histor-\\nically`, `theo-\\nretical`. The reviewer read the section correctly
and returned `addressed` for all three content rules. Two of those three were
then thrown away by our own grounding gate, and the PI was told:

    The Intellectual Merit statement addresses intellectual merit -- NOT FOUND
    (note: "The Intellectual Merit section details the potential to advance
     knowledge through concrete research steps and novel superalgebra methods.")

A row whose note says the draft covers the rule, over a status that says it does
not. Instrumenting the RAW model response before the gate is what settled it --
the model never disagreed with the draft, so this was never a judgement:

    model: addressed  pappg_ps_overview  -> quote VERIFIES
    model: addressed  pappg_ps_merit     -> quote REJECTED BY GATE
    model: addressed  pappg_ps_impacts   -> quote REJECTED BY GATE

The reviewer quotes a word the way a human reads it. `quote_in` collapses
whitespace, so the draft's `un-\\ndergraduate` becomes `un- dergraduate`, and
"undergraduate" is not in it. Golden rule 2 then correctly drops a claim it
cannot verify. The gate was right; the normalisation was incomplete.

Same family as the pdfplumber word-spacing bug (2026-08-20): a text-extraction
artifact that makes the tool say something false about the PROPOSAL rather than
report a broken read.

WHY TWO READINGS AND NOT ONE
----------------------------
A hyphen at a line end is genuinely AMBIGUOUS. `un-\\ndergraduate` is one word
the typesetter split; `bosonic-\\nfermionic` is a real compound that happened to
land on the break. Nothing in the text says which, so joining always would break
real compounds and keeping the hyphen always would leave split words broken.
So the TEXT is read both ways and a quote matching EITHER reading verifies.

That cannot manufacture a false positive: both readings are derived from the
draft itself, so a quote matching one is a quote that appears in the draft under
a defensible reading of it. A quote matching neither is still rejected, which is
what the tests below hold.
"""

from services.text_match import quote_in


SPLIT = ("Through funding, advising, and cultivating support teams, the PI\n"
         "mentors up-and-coming un-\ndergraduate/graduate researchers at MSU,\n"
         "one of three Histor-\nically Black Colleges and Universities.")


# ── the bug ────────────────────────────────────────────────────────────────

def test_a_quote_verifies_across_a_hyphen_broken_word():
    assert quote_in(SPLIT, "the PI mentors up-and-coming undergraduate/graduate researchers")


def test_the_second_real_quote_from_the_report_verifies():
    assert quote_in(SPLIT, "one of three Historically Black Colleges and Universities")


# ── the risk this had to be designed around ────────────────────────────────

def test_a_real_compound_split_at_a_line_end_still_verifies_with_its_hyphen():
    """`bosonic-fermionic` is one hyphenated word, not two joined by a
    typesetter. Joining it to `bosonicfermionic` would break a quote that is
    verbatim correct -- the mirror of the bug being fixed, and the reason the
    text is read BOTH ways rather than normalised one way."""
    text = "symmetries of bosonic-\nfermionic systems in a linear fashion"
    assert quote_in(text, "symmetries of bosonic-fermionic systems")


def test_a_hyphen_inside_one_line_is_untouched():
    assert quote_in("we study bosonic-fermionic systems",
                    "we study bosonic-fermionic systems")


# ── golden rule 2 must still bite ──────────────────────────────────────────

def test_a_quote_that_is_not_in_the_draft_is_still_rejected():
    """The whole point of the gate. Widening the readings must not make an
    unsupported claim verifiable."""
    assert not quote_in(SPLIT, "the PI mentors forty postdoctoral fellows")


def test_words_are_not_silently_glued_across_a_plain_line_break():
    """Only a HYPHEN licenses joining. Without one, `super` and `algebra` on
    two lines are two words, and a quote for `superalgebra` must fail -- else
    any two adjacent words could be run together to manufacture a match."""
    assert not quote_in("we study super\nalgebra methods", "superalgebra methods")


def test_an_empty_quote_still_never_verifies():
    assert not quote_in(SPLIT, "")
    assert not quote_in(SPLIT, "   \n  ")


# ── the existing contract is unchanged ─────────────────────────────────────

def test_plain_line_wrap_still_verifies():
    assert quote_in("improves research\nopportunities for students",
                    "improves research opportunities for students")


def test_list_noise_handling_is_unaffected():
    """The glyph sits INSIDE the quoted span, which is the shape that actually
    breaks a match -- a leading one is skipped by the substring test anyway."""
    noisy = "submit (cid:127) a data management plan"
    assert quote_in(noisy, "submit a data management plan", drop_list_noise=True)
    assert not quote_in(noisy, "submit a data management plan")


# ── the dash is not always a hyphen, and typeset text is where that bites ──

REAL_PARAGRAPH = (
    "Overall, the proposed research solidifies reduction algebra methods\n"
    "to address the decomposition–\nbases problem in super cases never before\n"
    "explored and furthers the classification of representations.")


def test_a_quote_verifies_across_an_EN_DASH_broken_word():
    """Found by re-running the PI's own draft after the hyphen fix shipped.

    The first fix recovered Broader Impacts and left Intellectual Merit still
    reported "Not found" -- because that paragraph breaks on `decomposition–`
    with an EN DASH (U+2013), and the pattern matched only hyphen-minus. A fix
    that covers one of the three characters a typesetter actually uses is a fix
    that looks like it worked, which is why this was caught by measuring the
    real draft again rather than by the passing test suite.
    """
    assert quote_in(
        REAL_PARAGRAPH,
        "the proposed research solidifies reduction algebra methods to address "
        "the decomposition–bases problem")


def test_an_em_dash_break_is_handled_too():
    text = "reduction superalgebras—\ndynamical quantum spaces—not present here"
    assert quote_in(text, "reduction superalgebras—dynamical quantum spaces")


def test_a_dash_used_as_punctuation_between_spaces_is_untouched():
    """`2019 - 2024` has a space BEFORE the dash, so the word-character anchor
    never fires. Widening the character class must not start gluing ranges."""
    assert quote_in("the award runs 2019 - 2024 inclusive",
                    "the award runs 2019 - 2024 inclusive")
    assert not quote_in("the award runs 2019 - 2024 inclusive", "20192024")


# ── LIGATURES: the same failure, a third character-level cause ─────────────

LIGATURE_TEXT = (
    "Funding requests outside the typical range should have corresponding\n"
    "detailed budget justications that demonstrate the relevance of the\n"
    "request to the project. The Foundation Oce of Integrative Activities\n"
    "notes the rate in eect for the award period.")


def test_a_quote_verifies_when_the_pdf_lost_its_LIGATURES():
    """Found on a real NSF solicitation the PI uploaded.

    A typeset PDF sets `fi`, `ff` and `ffi` as SINGLE glyphs. pdfplumber cannot
    decode them and emits a control character -- 112 times in that document --
    and `read_pdf` strips those, so `justification` is stored as `justication`
    and `Office` as `Oce`. 72 words were mangled that way.

    The model reads the mangled text and writes the word CORRECTLY when it
    quotes, so its quote no longer matches our copy and golden rule 2 discards a
    real requirement. Measured: that solicitation reported "2 proposed
    requirements were dropped because they could not be quoted", and the one
    reproduced here is a genuine NSF rule about budget justifications.

    Un-stripping is not available: the same control character stands for
    SEVERAL ligatures (`Noti-cation` is fi, `o-cer` is ffi, `e-ect` is ff), so
    nothing in the character says which letters were lost. The comparison is
    made aware of the artifact instead.
    """
    assert quote_in(
        LIGATURE_TEXT,
        "detailed budget justifications that demonstrate the relevance")


def test_the_longest_ligature_is_stripped_first():
    """`office` is `o` + `ffi` + `ce`. Taking `ff` first leaves `oice`, which
    matches nothing -- so the order is part of the fix, not an implementation
    detail."""
    assert quote_in(LIGATURE_TEXT, "The Foundation Office of Integrative")


def test_a_double_f_ligature_verifies():
    assert quote_in(LIGATURE_TEXT, "the rate in effect for the award period")


# ── and it must not become a way in ────────────────────────────────────────

def test_a_quote_with_no_ligature_letters_is_matched_no_more_loosely():
    """The fallback is only attempted when the quote actually contains one of
    the sequences a PDF can lose. A quote without them can never take the
    looser path, so the widening cannot reach ordinary text."""
    assert not quote_in(LIGATURE_TEXT, "the budget must not exceed the cap")


def test_a_fabricated_quote_containing_a_ligature_is_still_rejected():
    assert not quote_in(LIGATURE_TEXT,
                        "the office of research requires fifteen signatures")


def test_an_exact_quote_is_unaffected():
    assert quote_in(LIGATURE_TEXT, "outside the typical range")


def test_the_ligature_path_composes_with_list_noise():
    noisy = "(cid:127) detailed budget justications that demonstrate"
    assert quote_in(noisy, "detailed budget justifications that demonstrate",
                    drop_list_noise=True)


# ── A FOURTH CHARACTER-LEVEL PDF ARTIFACT: TYPOGRAPHIC PUNCTUATION ──────────
#
# Measured 2026-09-01 on the AWARDED NSF EiR Project Description (15 pages,
# 57,682 chars). Three rules came back "Not found" under notes that said the
# opposite -- "The draft details a clear sustainability plan focusing on future
# funding applications like NSF CAREER. (A supporting quote could not be
# verified in your text, so this is reported as not found.)" TEN raw rows were
# dropped by the gate in one run.
#
# Every one diverged at the same character. The typeset PDF carries a CURLY
# apostrophe and the model returns a STRAIGHT one:
#
#     document:  builds the PI’s Super Representation Theory   (U+2019)
#     model:     builds the PI's Super Representation Theory        (U+0027)
#
# That document holds 54 curly apostrophes and ZERO straight ones, and "the PI's"
# is unavoidable in a proposal -- so this silently demoted whole rules on a
# FUNDED package. Same family as the welded words, the dash line-breaks and the
# lost ligatures already handled here, and the same fix shape: normalise BOTH
# sides. Folding cannot manufacture a match -- it maps two spellings of one
# character onto each other, exactly as the whitespace collapse does.

def test_a_curly_apostrophe_in_the_draft_matches_a_straight_one_in_the_quote():
    doc = "The proposed research builds the PI’s Super Representation Theory program."
    assert quote_in(doc, "builds the PI's Super Representation Theory")


def test_a_straight_apostrophe_in_the_draft_matches_a_curly_one_in_the_quote():
    """Both directions: a PI who types in Word gets curly, one who pastes from a
    terminal gets straight, and the model may return either."""
    doc = "The proposed research builds the PI's Super Representation Theory program."
    assert quote_in(doc, "builds the PI’s Super Representation Theory")


def test_curly_double_quotes_fold_too():
    doc = "NSF calls this “broader impacts” in the solicitation."
    assert quote_in(doc, 'NSF calls this "broader impacts" in the solicitation.')


def test_a_unicode_ellipsis_matches_three_dots():
    doc = "Year 1: define the maps… Year 2: determine the generators."
    assert quote_in(doc, "define the maps... Year 2")


def test_folding_cannot_make_an_unrelated_quote_match():
    """The safety property. Normalising two spellings of ONE character onto each
    other cannot turn a sentence the draft never contained into a match."""
    doc = "The proposed research builds the PI’s program."
    assert not quote_in(doc, "The proposed research destroys the PI's program.")
