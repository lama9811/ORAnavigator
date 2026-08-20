"""Deterministic language slips: doubled words, punctuation, never-valid spellings.

WHY THIS EXISTS. A PI planted nine language errors in a Project Summary and the
review returned an unchanged 7 of 7 (100%) with 0 mistakes: `zwiterionic`,
`The objectives is`, `the the`, `Chesepeake`, a missing final period,
`The work advance`, `models fails`, `rather then`, `Univeristy`, `Departement`.
Nothing in the app looked at language at all.

WHY IT IS NARROW ON PURPOSE. A dictionary spellchecker is the obvious tool and
the wrong one here: `zwitterionic`, `Donnan`, `estuarine`, `potentiostat`, `MTDC`
and `PSU` are all correct and all absent from a dictionary. A checker that yells
at correct science is ignored by the second page, and then the real errors ship
too — the failure this module's docstring already records for `TO DO`.

So: no dictionary, no grammar model. Only slips that are mechanically decidable.
Every test below names a REAL sentence from the repo's own drafts that must NOT
be flagged.
"""
from services.mechanical_checks import find_mistakes


def _labels(text, **kw):
    return [m["label"] for m in find_mistakes(text, **kw)]


def _kinds(text, **kw):
    return [m["kind"] for m in find_mistakes(text, **kw)]


# ── what it must CATCH ──────────────────────────────────────────────────────

def test_a_doubled_word_is_caught():
    out = find_mistakes("Prototypes were validated in the the Chesapeake Bay.")
    hit = [m for m in out if m["kind"] == "doubled_word"]
    assert hit, out
    assert "the the" in hit[0]["evidence"].lower()


def test_a_never_valid_spelling_is_caught():
    out = find_mistakes("We will seperate the two fractions and recieve the data.")
    hits = [m for m in out if m["kind"] == "misspelling"]
    assert len(hits) == 2, [m["evidence"] for m in hits]


def test_rather_then_is_caught():
    out = find_mistakes("Rational design rather then empirical selection of materials.")
    assert any(m["kind"] == "misspelling" for m in out)


def test_a_missing_space_after_a_period_is_caught():
    out = find_mistakes("We synthesize the network.The response is then measured.")
    assert any(m["kind"] == "spacing" for m in out)


def test_a_space_before_a_comma_is_caught():
    out = find_mistakes("We measured conductance , impedance and drift over time.")
    assert any(m["kind"] == "spacing" for m in out)


def test_a_prose_paragraph_with_no_final_punctuation_is_caught():
    out = find_mistakes(
        "The methods combine controlled radical polymerization, impedance "
        "spectroscopy, and field calibration against reference CTD instruments")
    assert any(m["kind"] == "unfinished_sentence" for m in out)


# ── what it must NOT flag (each is real text from this repo's drafts) ────────

def test_technical_vocabulary_is_never_flagged():
    """The whole reason there is no dictionary in here."""
    real = ("This project develops adaptive zwitterionic polymer networks for "
            "continuous salinity sensing in estuarine waters, characterized by "
            "impedance spectroscopy on a Gamry potentiostat across 0-35 PSU, "
            "where existing Donnan models fail. F&A is applied to MTDC.")
    assert _kinds(real) == [], _labels(real)


def test_a_heading_on_its_own_line_needs_no_period():
    """'Overview', 'Intellectual Merit', 'Broader Impacts' are NSF's own required
    headings and carry no terminal punctuation by design. Flagging them would
    fire on every correctly formatted Project Summary in existence."""
    assert _kinds("Overview\n\nIntellectual Merit\n\nBroader Impacts\n") == []


def test_a_legitimate_doubled_word_is_not_flagged():
    """"had had" and "that that" are ordinary English."""
    assert _kinds("The instrument had had two prior calibrations.") == []


def test_a_decimal_and_an_abbreviation_are_not_read_as_missing_spaces():
    """0.35, e.g., i.e., U.S., Fig. 3 and a DOI all put a period next to a
    character with no space, and every one is correct."""
    real = ("Response was linear to 0.35 PSU (e.g., the U.S. reference range), "
            "see Fig. 3 and https://doi.org/10.1021/acs.est.2c07711 for detail.")
    assert _kinds(real) == [], _labels(real)


def test_a_sentence_ending_in_a_colon_or_a_list_is_not_unfinished():
    real = ("The project has three aims:\n"
            "- synthesize the library\n"
            "- characterize the response\n"
            "- deploy six prototypes\n")
    assert _kinds(real) == [], _labels(real)


def test_a_bare_reference_entry_is_not_an_unfinished_sentence():
    """A bibliography line legitimately ends without a period, and References
    Cited is a whole section of them."""
    real = ("Researcher, A.; Nguyen, M. Charge spacing controls salinity "
            "response. Macromolecules 2022, 55, 4410-4419")
    assert "unfinished_sentence" not in _kinds(real), _labels(real)


def test_a_url_with_a_capitalised_host_is_not_a_missing_space():
    """`www.Morgan.edu` puts a period against a capital by design. Without the
    URL guard the run-together rule fires on every link in a draft."""
    assert "spacing" not in _kinds("Visit www.Morgan.edu for the full protocol.")


def test_outline_numbering_is_not_a_missing_space():
    """`II.Background` and `1.Introduction` are formatting choices, not run-on
    sentences. This is what the two-lowercase-letters lookbehind buys: a single
    letter or a digit before the period is never treated as the end of a word.

    It costs a real miss, accepted deliberately: `Ph.D.Chemistry is her field`
    IS a missing space and is suppressed by the same rule. Failing safe here
    means a checker people keep reading."""
    assert "spacing" not in _kinds("II.Background and Motivation of the study")
    assert "spacing" not in _kinds("1.Introduction to the coastal sensing problem")


def test_the_unfinished_sentence_quote_shows_the_END_of_the_paragraph():
    """The evidence must point at the place the period is missing. It quoted the
    paragraph's OPENING words instead — "this paragraph stops without a period"
    over a snippet of its first line, which tells an author nothing about where
    to look. Golden rule 2 is about a finding being locatable, not merely quoted.
    """
    # Long enough that a snippet anchored at the paragraph START cannot reach
    # the end — which is exactly the real draft that exposed this.
    para = ("This project develops adaptive zwitterionic polymer networks for "
            "continuous salinity sensing in estuarine waters, where commercial "
            "conductivity sensors drift beyond tolerance within six weeks of "
            "biofouling and force monthly servicing across the whole monitoring "
            "network, so the methods combine controlled radical polymerization, "
            "impedance spectroscopy, and twelve months of field calibration "
            "against reference CTD instruments deployed in the bay")
    row = next(m for m in find_mistakes(para) if m["kind"] == "unfinished_sentence")
    assert "reference CTD instruments deployed in the bay" in row["evidence"], \
        row["evidence"]
    # ...and ONLY the end. It quoted the entire 445-character paragraph, where
    # every other rule in this module quotes a short snippet. A wall of text in
    # a mistakes list is not a quote, it is the paragraph again.
    assert len(row["evidence"]) <= 140, len(row["evidence"])
    assert not row["evidence"].startswith("This project develops")


def test_a_capitalised_misspelling_is_corrected_in_the_same_case():
    """"Univeristy" -> 'Did you mean "university"?' reads as a second error in a
    tool whose whole subject is spelling."""
    row = next(m for m in find_mistakes("Morgan State Univeristy, an HBCU.")
               if m["kind"] == "misspelling")
    assert '"University"' in row["detail"], row["detail"]
