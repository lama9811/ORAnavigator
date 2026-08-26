"""A quote alone is not a location. Say WHERE in the section it is.

A PI reading "Remove the spurious comma after 'and'" over the fragment
`work; and, the PI extends their previous use` still has to hunt through 523
words to find it. The row already carries the evidence; what it lacks is a
position.

DETERMINISTIC, and that is the point: the model is asked about ENGLISH, which
it is authoritative on. Where a string sits in a document is arithmetic, and
asking a model for it would invite a confidently wrong line number -- the exact
shape of error this codebase spends most of its guards preventing.

FAILS TO ABSENT, NEVER TO WRONG. A quote we cannot place gets no location at
all. Sending an author to line 12 for something on line 40 is worse than
sending them nowhere, because they will believe it.
"""

from services.proofread import locate_quote


TEXT = (
    "Overview\n"                                        # 1
    "We study zwitterionic networks for salinity\n"     # 2
    "sensing in estuarine water.\n"                     # 3
    "\n"                                                # 4
    "Broader Impacts\n"                                 # 5
    "The work trains students; and, each trainee\n"     # 6
    "leaves with collaborators.\n"                      # 7
)


def test_a_quote_reports_the_line_it_starts_on():
    where = locate_quote(TEXT, "The work trains students; and, each trainee")
    assert where["line"] == 6, where


def test_the_context_is_the_line_the_author_will_look_at():
    where = locate_quote(TEXT, "each trainee")
    assert "each trainee" in where["context"], where


def test_a_quote_spanning_a_line_break_reports_where_it_STARTS():
    """The reviewer quotes across the wrap, as a reader reads. The author needs
    the line to jump to, which is the first one."""
    where = locate_quote(TEXT, "salinity sensing in estuarine water")
    assert where["line"] == 2, where


def test_a_quote_spanning_a_dash_break_is_still_placed():
    """Same artifact `quote_in` was taught to read both ways. A location that
    gives up wherever a typesetter split a word would miss exactly the drafts
    copied out of a PDF -- which is most of them."""
    text = "one of three Histor-\nically Black Colleges and Universities.\n"
    where = locate_quote(text, "one of three Historically Black Colleges")
    assert where and where["line"] == 1, where


def test_a_quote_that_is_not_there_gets_no_location():
    """Fails to ABSENT. A wrong line number is believed; a missing one is not."""
    assert locate_quote(TEXT, "the PI requests forty postdoctoral fellows") is None


def test_an_empty_quote_gets_no_location():
    assert locate_quote(TEXT, "") is None
    assert locate_quote("", "anything") is None


# ── wiring ─────────────────────────────────────────────────────────────────

def test_a_wording_row_carries_its_location(monkeypatch):
    from services import proofread as pf
    monkeypatch.setattr(pf.gemini_client, "generate_json", lambda *a, **k: {"issues": [
        {"quote": "The work trains students; and, each trainee",
         "kind": "punctuation",
         "detail": "Remove the spurious comma after 'and'."}]})
    rows = pf.proofread(TEXT)
    assert rows and rows[0]["where"]["line"] == 6, rows


def test_an_unplaceable_row_still_ships_without_a_location(monkeypatch):
    """The location is an affordance, not a gate. A row that survived the quote
    check must not be dropped because the locator was less tolerant than
    `quote_in` -- that would silently lose real findings."""
    from services import proofread as pf
    monkeypatch.setattr(pf.gemini_client, "generate_json", lambda *a, **k: {"issues": [
        {"quote": "each trainee", "kind": "grammar", "detail": "d"}]})
    rows = pf.proofread(TEXT)
    assert rows, rows
    assert "where" in rows[0]


# ── which PART of the section, not just which line ─────────────────────────

SUMMARY = (
    "Overview\n"                                        # 1
    "We study zwitterionic networks for salinity\n"     # 2
    "sensing in estuarine water.\n"                     # 3
    "\n"                                                # 4
    "Intellectual Merit\n"                              # 5
    "The work advances knowledge; and, it does so\n"    # 6
    "by new methods.\n"                                 # 7
    "\n"                                                # 8
    "Broader Impacts\n"                                 # 9
    "The work trains students; and, each trainee\n"     # 10
    "leaves with collaborators.\n"                      # 11
)

HEADINGS = ["Overview", "Intellectual Merit", "Broader Impacts"]


def test_a_quote_reports_the_heading_it_sits_under():
    """A line number in a 523-word section is still a hunt. "Broader Impacts"
    is what the author actually navigates by."""
    where = locate_quote(SUMMARY, "each trainee", headings=HEADINGS)
    assert where["heading"] == "Broader Impacts", where


def test_the_heading_is_the_nearest_one_ABOVE_the_quote():
    where = locate_quote(SUMMARY, "The work advances knowledge", headings=HEADINGS)
    assert where["heading"] == "Intellectual Merit", where


def test_text_before_any_heading_gets_no_heading():
    """Fails to ABSENT, like the line number. Naming a heading the quote does
    not sit under would send an author to the wrong paragraph."""
    where = locate_quote("A stray opening line.\n" + SUMMARY,
                         "A stray opening line", headings=HEADINGS)
    assert where["heading"] is None, where


def test_an_unknown_heading_is_never_invented():
    """Only headings the RULE names count. A short line is not a heading --
    guessing would label a wrapped sentence fragment as a section."""
    where = locate_quote(SUMMARY, "each trainee", headings=["Overview"])
    assert where["heading"] == "Overview", where


def test_no_headings_supplied_means_no_heading_reported():
    where = locate_quote(SUMMARY, "each trainee")
    assert where.get("heading") is None, where


def test_the_headings_come_from_the_rule_that_enforces_them():
    """Read off `rb_headings`' own check_args, never typed twice -- the same
    reason `_section_page_limit` reads the allowance off `rb_page_limit`. Two
    copies of one list drift, and then the locator names a heading the checker
    does not require."""
    from services import draft_review as dr
    from services import rulebook_baseline as rb
    rows = rb.rules_for("the PAPPG", "project_summary", tier="basic")
    assert dr._section_headings(rows) == ["Overview", "Intellectual Merit",
                                          "Broader Impacts"]
