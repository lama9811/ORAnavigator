"""Three false positives a PI found in one Draft Review, all from PDF damage.

WHY THIS EXISTS. A funded NSF EiR package came back with nine "mistakes to fix
before you submit". Six were not the author's:

  * FOUR "space before punctuation" rows quoting ONE line, and that line is not
    prose at all. NSF's budget form stacks two rows of small text within five
    points of each other, and `document_text.PDF_Y_TOLERANCE = 5` merges them
    into one line, interleaving their characters by x-position:

        'A. KS eE yN AIO ssR o/ cK iaE tY es P (E LR isS t O eaN cN h E sL e: ...'

    which is "A. SENIOR/KEY PERSONNEL: PD/PI, Co-PI's, Faculty ..." zipped
    together with the row beneath it.

  * TWO "Table N is referred to but never labelled" rows, on a file that
    extracts perfectly. `_CAPTION_RE` demanded punctuation after the number, so
    NSF's own bare `Table 4` on its own line was not read as a caption — and the
    label was reported as a dangling reference TO ITSELF.

LOWERING THE TOLERANCE IS NOT THE FIX, and that is measured rather than assumed.
At y_tolerance=2 the budget form reads cleanly and loses 56 characters per page
elsewhere, and the Project Description's superscript numerals vanish
("two undergraduates in Year 1" -> "in Year ; three") — which is the exact
sentence the tolerance was raised to 5 to repair. No single value reads both
correctly. So the reading is left alone and the REPORTING is fixed: the app
cannot un-scramble a form, but it must never blame the author for it.

Same family as the welded words, dash line-breaks, lost ligatures, page
furniture, reading order and curly apostrophes already in this repo — the tool
saying something false about the PROPOSAL instead of reporting a bad read.
"""
from services.mechanical_checks import find_mistakes


# The real line, verbatim from `document_text.extract_upload` over the awarded
# proposal's budget form. Do not tidy it.
INTERLEAVED = (
    "A. KS eE yN AIO ssR o/ cK iaE tY es P (E LR isS t O eaN cN h E sL e: p P arI/ "
    "aP teD ly, C wo it- hP tI i’ ts le, ,F Aac .7u .l ty s ha on wd nO ut mhe br e S r "
    "ie nn bio rr a/ ckets) CAL PNe ArSs CFo A nF- Dumnodnetdh Ss UMR Re pq rF u ou e pn "
    "s otd e ss ed r By gra (in f t dF e iu d ffn ebd ry es nN t)SF"
)

# NSF's Collaborators & Other Affiliations form, verbatim, captions and all.
COA_FORM = """Table 3
3 Advisor/Advisee Name: Organizational Affiliation Optional (email, Department)
G Grantcharov, Dimitar The University of Texas at Arlington
Table 4
4 Name: Organizational Affiliation Optional (email, Department) Last Active Date
A Hartwig, Jonas, T. Iowa State University .
C Jones, Edna Tulane University .
Table 5
5 Name: Organizational Affiliation Journal/Collection Last Active Date
"""


def test_an_interleaved_form_row_reports_no_language_mistakes():
    """It is two rows of a form zipped together, not a sentence with bad spacing."""
    out = find_mistakes(INTERLEAVED)
    assert out == [], [m["label"] for m in out]


def test_interleaving_is_caught_even_beside_ordinary_prose():
    """The guard is per LINE. Real prose around it must still be checked."""
    text = ("The research supports two undergraduates in Year 1.\n"
            + INTERLEAVED
            + "\nPrototypes were validated in the the Chesapeake Bay.\n")
    kinds = [m["kind"] for m in find_mistakes(text)]
    assert "doubled_word" in kinds, kinds          # the real error still lands
    assert "spacing" not in kinds, kinds           # the form row does not


def test_a_bare_table_label_on_its_own_line_is_a_caption():
    """NSF writes `Table 4` alone on a line. That IS the label being asked for."""
    out = [m for m in find_mistakes(COA_FORM) if m["kind"] == "broken_reference"]
    assert out == [], [m["label"] for m in out]


def test_a_genuinely_dangling_table_reference_is_still_reported():
    """The mirror. Loosening the caption pattern must not silence a real one."""
    text = ("Table 1\n"
            "1 Name: Organizational Affiliation\n"
            "Results are summarised in Table 9 below.\n")
    out = [m for m in find_mistakes(text) if m["kind"] == "broken_reference"]
    assert len(out) == 1, out
    assert "9" in out[0]["label"]


def test_two_marks_on_one_short_line_are_not_reported_twice_identically():
    """The PI saw FOUR rows carrying one quote. Two rows whose kind, label and
    evidence are all identical are one finding shown twice, and with no line
    number on a mistake row there is nothing to tell them apart."""
    text = "Alpha , beta , gamma decay was measured.\n"
    rows = [(m["kind"], m["label"], m["evidence"]) for m in find_mistakes(text)]
    assert rows, "the real spacing errors must still be reported"
    assert len(rows) == len(set(rows)), rows


def test_ordinary_prose_with_short_words_is_not_read_as_damage():
    """The guard keys on token LENGTH, so short-worded prose must survive it.

    A real sentence of small words still averages 3.5-4 characters a token; the
    interleaved form row averages 2.2, because interleaving SHATTERS words
    rather than choosing short ones."""
    text = "We ask that all of the data be kept for ten years , as the plan requires.\n"
    kinds = [m["kind"] for m in find_mistakes(text)]
    assert "spacing" in kinds, kinds
