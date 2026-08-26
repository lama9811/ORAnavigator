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
