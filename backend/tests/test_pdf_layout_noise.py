"""Text that did not linearize cleanly must not be reported as writing errors.

WHY THIS EXISTS. `mechanical_checks` was written and measured against PASTED
PROSE. Section Check also takes a PDF UPLOAD -- and is recommended to, because
only an uploaded PDF gives the page-limit rule a real page count instead of a
word-count estimate. Run over a typeset PDF containing mathematics or tables,
three of its rules turn into pure noise.

MEASURED, on the Project Description of a FUNDED NSF HBCU-EiR proposal
(NSF 23-598, award 2503008), uploaded through `/section-check/upload`:
**56 "mistakes to fix", of which 2 were real.** 52 were `spacing`, 2 were
`doubled_word`, 1 was an `unfinished_sentence`. Every one of the 54 was created
by the extractor, not by the author.

THE MECHANISM. pdfplumber cannot represent two-dimensional maths, so it
flattens a line and emits the displaced sub/superscripts as orphans. The page
prints

    For a Z_2-graded vector space V = V_0 (+) V_1, the parity map ...

with no space before that comma -- the subscript sits tight against it. The
extraction returns

    rity). For a Z -graded vector space V = V (+) V , the parity map ... 2 0 1 0

and the hole the subscript left behind becomes "space before punctuation".
Same for the year numbers below: the page reads "in Year 1; three ... Year 2;
and, three ... Year 3." -- perfect punctuation -- and the digits are emitted on
their own line.

THE GUARD IS NEIGHBOURHOOD EVIDENCE, NOT A GUESS ABOUT ONE MARK. A floating
comma on its own is a plausible typo and stays reportable (there is a test for
it below, and one in test_language_slips.py that predates this). What marks a
line unreliable is evidence that the text around it failed to linearize: an
unmapped glyph, a mathematical operator, or an adjacent line that is nothing but
stranded fragments.

OVER-SUPPRESSION IS THE DANGEROUS DIRECTION -- a real error silenced here is
never learned about, while a surviving false positive is merely annoying. So the
symbol set deliberately EXCLUDES lower-case Greek (a chemist writes "beta-lactam"
in ordinary prose) and every guard below has a matching test that a real error in
clean prose still fires.
"""
from services import mechanical_checks as mc


def _kinds(text, **kw):
    return {m["kind"] for m in mc.find_mistakes(text, **kw)}


def _spacing(text):
    return [m for m in mc.find_mistakes(text) if m["kind"] == "spacing"]


# ── Real lines, verbatim, from the awarded proposal's extraction ────────────

MATHS = (
    "Definition E.2 (parity). For a Z -graded vector space V = V ⊕ V , the "
    "parity map | · |: (V ∪ V ) \\ 2 0¯ 1¯ 0¯\n"
    "{0} → Z sends the set of nonzero homogeneous elements of V by x ∈ V "
    "\\ {0} maps to |x| = i¯, for 2 i¯ i¯ ∈ Z .\n"
)

UNMAPPED_GLYPH = (
    "Question (4) Does there exist a superalgebra map Ψ (cid:98) : "
    "U(osp(1|2n)) → Cl(1|2n) ⊗ Cl(n|0)\n"
    "between the universal enveloping algebra and tensor products.\n"
)

# The digits 1 / 2 3 are the relocated year numbers. The page prints
# "in Year 1; three ... in Year 2; and, three ... in Year 3."
RELOCATED_SUPERSCRIPTS = (
    "Undergraduate research trainees The research supports two undergraduates "
    "in Year ; three\n"
    "1\n"
    "undergraduates in Year ; and, three undergraduates in Year . The PI "
    "provides an intensive paid\n"
    "2 3\n"
    "research experience for two students yearly during the fall semester.\n"
)

SUBSCRIPT_COLLISION = (
    "partials ∂ , adhering to x ∂ − ∂ x = δ . Suppose also "
    "that variables/partials of differing parities\n"
    "xi i xj xj i ij\n"
    "anticommute. Then the Clifford-type superalgebra Cl(s|t) is the quotient "
    "of the free superalgebra.\n"
)


def test_flattened_maths_does_not_report_space_before_punctuation():
    """The page has no space before that comma; the extractor made the hole."""
    assert _spacing(MATHS) == [], _spacing(MATHS)


def test_an_unmapped_glyph_marks_the_line_as_unreliable():
    """`(cid:98)` is pdfplumber saying it could not map a character. Nothing on
    a line carrying one can be trusted to be what the author typed."""
    assert _spacing(UNMAPPED_GLYPH) == [], _spacing(UNMAPPED_GLYPH)


def test_an_unmapped_glyph_is_enough_on_its_own():
    """The UNMAPPED_GLYPH fixture above also carries maths operators, so the
    maths rule covers it and this guard was never exercised -- mutation-testing
    found that, not the suite. Here the `(cid:)` marker is the ONLY evidence."""
    text = "The transition matrix (cid:98) maps the states , preserving grading."
    assert _spacing(text) == [], _spacing(text)


def test_a_relocated_superscript_does_not_report_space_before_punctuation():
    """No maths symbol on these lines at all -- the only evidence is the
    neighbouring line that holds nothing but the displaced digits."""
    assert _spacing(RELOCATED_SUPERSCRIPTS) == [], _spacing(RELOCATED_SUPERSCRIPTS)


def test_a_subscript_collision_is_not_a_doubled_word():
    """`xj xj` is two subscripts landing side by side, not a repeated word."""
    doubled = [m for m in mc.find_mistakes(SUBSCRIPT_COLLISION)
               if m["kind"] == "doubled_word"]
    assert doubled == [], doubled


def test_an_orphan_fragment_paragraph_is_not_an_unfinished_sentence():
    """A block of stranded fragments has no period because it is not a sentence.

    `_MIN_PROSE_WORDS` already drops SHORT orphan blocks, so the case that gets
    through is a long one -- the displaced subscripts of a dense definition,
    which is exactly what a maths proposal produces most of."""
    text = ("Suppose the parity map sends homogeneous elements to their degree.\n\n"
            "0¯ 1¯ 0¯ i¯ i¯ 2 a¯ b¯ i¯\n\n"
            "The tensor product inherits the grading from both factors.")
    rows = [m for m in mc.find_mistakes(text) if m["kind"] == "unfinished_sentence"]
    assert rows == [], rows


# ── The over-suppression guards: real errors must still fire ────────────────

def test_a_space_before_a_comma_in_clean_prose_is_still_caught():
    text = "We measured conductance , impedance and drift over time."
    assert _spacing(text), "the guard silenced a real spacing error"


def test_a_genuine_space_before_a_colon_in_prose_is_still_caught():
    """Verified against the printed page of the awarded proposal: it really does
    read "superalgebras [48, 64] : Unlike". A real slip, and one of only two real
    findings in the 56. Nothing on this line is extraction damage."""
    text = ("More on Clifford-Weyl superalgebras [48, 64] : Unlike supergeometry "
            "where supersymmetric algebras mandate that odd elements square to zero.")
    assert _spacing(text), "the guard silenced a REAL space before a colon"


def test_a_real_doubled_word_in_clean_prose_is_still_caught():
    text = ("The proposed work will will train four undergraduates each year "
            "in estuarine sensing methods.")
    assert "doubled_word" in _kinds(text)


def test_a_confused_phrase_on_a_maths_page_is_still_caught():
    """Word-level rules read the WORDS, which survive relinearisation -- only
    the layout-sensitive rules are gated. Uses `rather then`, because
    `in regards to` was retired from the table (see the usage test below)."""
    text = ("taining singular vectors, either result of Year (rather then "
            "Conjecture E.21) leads to use of re-\n"
            "1\n"
            "duction algebras to compute singular vectors ∈ V .\n")
    rows = [m for m in mc.find_mistakes(text) if m["kind"] == "misspelling"]
    assert rows, "the guard silenced a REAL wrong-word finding"
    assert "rather then" in rows[0]["evidence"]


def test_a_lower_case_greek_letter_does_not_silence_ordinary_prose():
    """A chemist writes beta and mu in running prose. If those counted as maths,
    every technical draft would lose its spacing checks."""
    text = "The β-lactam resistance , measured at μ-molar concentration, rose."
    assert _spacing(text), "an ordinary Greek letter silenced a real error"


def test_a_clean_prose_paragraph_still_reports_its_missing_period():
    text = ("The methods combine controlled radical polymerization, impedance "
            "spectroscopy, and field calibration against reference instruments")
    assert "unfinished_sentence" in _kinds(text)


# ── Running page furniture ─────────────────────────────────────────────────
# After the maths fix this became the single most common false positive: the
# per-page stamp fired in ALL FOUR sections, including a Project Summary whose
# only reported "mistake" it was.

def test_a_running_page_footer_is_not_an_unfinished_sentence():
    """A line the PDF stamps on every page is furniture, not a paragraph. The
    signal is that it REPEATS verbatim -- no NSF string is hard-coded, so this
    works for any funder's stamp."""
    stamp = ("Revised Proposal Budget Revision #1 for 2503008 Submitted On "
             "Fri Jun 20 09:52:24 EDT 2025 Electronic Signature")
    text = "\n\n".join([
        "The proposed research addresses the decomposition problem directly.",
        stamp,
        "Reduction algebras give bases for the resulting representations here.",
        stamp,
        "The work supports doctoral students at Morgan State University now.",
        stamp,
    ])
    rows = [m for m in mc.find_mistakes(text) if m["kind"] == "unfinished_sentence"]
    assert rows == [], rows


def test_a_sentence_that_appears_once_still_reports_its_missing_period():
    """The repeat is the whole signal. Said once, it is an ordinary paragraph."""
    text = ("The proposed research addresses the decomposition problem for "
            "infinite dimensional representations of Lie superalgebras")
    assert "unfinished_sentence" in _kinds(text)


# ── Bibliography entries ───────────────────────────────────────────────────
# References Cited is one of the four sections the picker offers, and it was
# barely improved by the maths fix: 11 of its 12 rows were these two shapes.

def test_a_removed_italic_title_is_not_a_spacing_error():
    """pdfplumber lifts the italic title out of a BibTeX entry and leaves the
    two periods that bracketed it sitting side by side. The page reads
    "F. Wegner. Supermathematics and its Applications. Vol. 920." """
    text = ("[6] F. Wegner. . Vol. 920. Lecture Notes in Physics. Springer, 2016.")
    assert _spacing(text) == [], _spacing(text)


def test_a_doi_typeset_with_spaces_is_not_a_spacing_error():
    """TeX sets a long DOI with breakable spaces. Every one reads as a space
    before a period."""
    text = "issn: 18150659. doi: 10 . 3842 / SIGMA . 2021 . 031. url: https://emis.de/"
    assert _spacing(text) == [], _spacing(text)


def test_prose_citing_a_bracketed_number_still_reports_spacing():
    """The over-suppression trap. A bracketed citation number is ordinary in a
    Project Description, so a bibliography guard must NOT key on `[12]`."""
    text = "As shown in [12] the measured drift , recorded hourly, stayed small."
    assert _spacing(text), "a prose citation silenced a real spacing error"


def test_a_decimal_number_in_prose_still_reports_a_space_before_a_period():
    """The digit guard must be about a period BETWEEN digits, not any digit on
    the line."""
    text = "We recorded 4 samples per hour across 12 sites ."
    assert _spacing(text), "a line containing digits lost its spacing check"


# ── One-page sections ──────────────────────────────────────────────────────
# The repeat signal cannot see a stamp that appears ONCE, and a one-page
# section is exactly where that happens. Measured after the repeat fix: the
# footer still fired on the Project Summary and on Facilities -- and on the
# Project Summary it was the only "mistake" reported at all.

PAGE_STAMP = ("Page 4 of 56\n"
              "Revised Proposal Budget Revision #1 for 2503008 Submitted On "
              "Fri Jun 20 09:52:24 EDT 2025 Electronic Signature")


def test_a_page_stamp_appearing_once_is_still_not_an_unfinished_sentence():
    """A `Page N of M` line marks what follows as furniture however few times
    it occurs. The prose above it ends properly and must be what is judged."""
    text = ("Broader Impacts\n"
            "The project trains four undergraduates and two graduate students "
            "each year in a collaborative environment spanning academic and "
            "social experiences.\n" + PAGE_STAMP)
    rows = [m for m in mc.find_mistakes(text) if m["kind"] == "unfinished_sentence"]
    assert rows == [], rows


def test_prose_that_really_lacks_its_period_is_still_caught_above_a_page_stamp():
    """The over-suppression guard: stripping furniture must reveal the real end
    of the prose, not silence the paragraph wholesale."""
    text = ("Broader Impacts\n"
            "The project trains four undergraduates and two graduate students "
            "each year in a collaborative environment spanning academic work\n"
            + PAGE_STAMP)
    rows = [m for m in mc.find_mistakes(text) if m["kind"] == "unfinished_sentence"]
    assert rows, "stripping page furniture silenced a REAL missing period"
    assert "academic work" in rows[0]["evidence"]


# ── Table cells ────────────────────────────────────────────────────────────
# Two adjacent cells holding the same value read as a repeated word. Measured:
# the dissemination table's Attendees and Presenters columns both say "PI", and
# the collaborators table has two header cells starting "Affiliation".

def test_two_table_cells_with_the_same_value_are_not_a_doubled_word():
    """`Name | Affiliation | Affiliation Role | Location | Research Role` -- a
    header row, not a sentence. No terminal punctuation and every token
    capitalised is what separates it from prose."""
    text = ("with the following unfunded individuals:\n"
            "Name Affiliation Affiliation Role Location Research Role\n"
            "Jonas T. Hartwig Iowa State University Associate Professor Ames, IA\n")
    rows = [m for m in mc.find_mistakes(text) if m["kind"] == "doubled_word"]
    assert rows == [], rows


def test_a_table_row_repeating_an_acronym_cell_is_not_a_doubled_word():
    text = ("NAM Faculty Conference on To be Apr. \u201926, \u201927, PI PI\n"
            "Research and Teaching Excellence determined \u201928\n")
    rows = [m for m in mc.find_mistakes(text) if m["kind"] == "doubled_word"]
    assert rows == [], rows


def test_a_doubled_word_in_WRAPPED_prose_is_still_caught():
    """The over-suppression guard that matters most. Extracted prose wraps, so
    most real prose lines DO end without punctuation -- the table signal must
    rest on capitalisation too, not on the missing period alone."""
    text = ("the research supports two undergraduates and and three graduate\n"
            "students each year in a collaborative environment\n")
    rows = [m for m in mc.find_mistakes(text) if m["kind"] == "doubled_word"]
    assert rows, "a real doubled word in wrapped prose was silenced"


# ── A lifted italic run ────────────────────────────────────────────────────

def test_punctuation_stranded_at_the_end_of_a_line_is_not_a_spacing_error():
    """pdfplumber lifts the italic journal name onto its own line and the comma
    that followed it dangles. The page reads "...fit the Journal of Algebra,
    among others." """
    text = ("in 2025); and, results of the main questions of the proposed research fit the ,\n"
            "Journal of Algebra\n"
            "among others. The PI notes several venues for future submission.\n")
    assert _spacing(text) == [], _spacing(text)


# ── A decomposed diacritic ─────────────────────────────────────────────────

def test_a_decomposed_diacritic_is_not_a_spacing_error():
    """The font stores Z-caron as `Z` + a bare caron, so "D. \u017d. Djokovi\u0107"
    extracts as "D. Z\u02c7 . Djokovic\u00b4" and the period is pushed off the name."""
    text = ("[45] D. Z\u02c7 . Djokovic\u00b4 and G. Hochschild. \u201cSemisimplicity of "
            "2-Graded Lie Algebras\u201d.")
    assert _spacing(text) == [], _spacing(text)


def test_a_properly_composed_accented_name_still_reports_spacing():
    """The guard must key on a BARE diacritic ending a token, not on the name
    being accented at all -- `Djokovi\u0107` is one precomposed character."""
    text = "We follow Djokovi\u0107 , who proved the graded case in 1976."
    assert _spacing(text), "an accented name silenced a real spacing error"


def test_a_bare_acute_accent_is_also_a_decomposed_diacritic():
    """The set has two halves -- a Latin-1 literal group and the modifier-letter
    RANGE -- and mutation-testing showed the range alone covered every case, so
    the literals guarded nothing. The same file really does contain
    "Djokovic\u00b4" (c + U+00B4 ACUTE), which is the literal half."""
    text = "[45] D. Djokovic\u00b4 , and G. Hochschild proved the graded case."
    assert _spacing(text) == [], _spacing(text)


# ── Usage judgements are not mistakes ──────────────────────────────────────

def test_in_regards_to_is_not_reported_as_a_mistake():
    """RETIRED from `_CONFUSED_PHRASES`. It was the one entry of six that is a
    USAGE judgement rather than an outright error: usage guides call it
    nonstandard, but competent editors disagree, and the panel above these rows
    promises "found by a rule, not a judgement -- these are errors".

    That promise is what this module's docstring rests on: "A MISTAKE needs no
    calibration -- the placeholder is in the text or it is not." `in regards to`
    needs calibration; `could of` does not. Found in a FUNDED proposal, where it
    was one of only two surviving rows.
    """
    text = "The plan was revised in regards to Conjecture E.21 during Year 1."
    rows = [m for m in mc.find_mistakes(text) if m["kind"] == "misspelling"]
    assert rows == [], rows


def test_the_five_real_confusions_still_fire():
    """The removal must not quietly widen. These five are wrong in every
    register, which is exactly what `in regards to` is not."""
    for phrase in ("rather then", "as oppose to", "could of", "would of",
                   "should of"):
        text = f"The team {phrase} the alternative approach for this study."
        rows = [m for m in mc.find_mistakes(text) if m["kind"] == "misspelling"]
        assert rows, f"{phrase!r} stopped being reported"
