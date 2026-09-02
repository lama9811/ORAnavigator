"""Mechanical mistakes in a draft: objective, quotable, and no model involved.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
Asked to rate how *well written* a proposal is, this app has nothing to
calibrate against: the sample library is 22 FUNDED proposals and zero declined
ones, so there is no signal anywhere in the system for what separates a funded
draft from a rejected one. A quality score would be the model's opinion
presented as a measurement — and the completeness score already had to be
captioned defensively because a percentage next to a proposal reads as
"likelihood of funding". (A reviewer-lens scoring tool existed once and was
deleted by product decision; see the Fundability note in CLAUDE.md.)

A MISTAKE needs no calibration. The placeholder is either in the text or it is
not. So this module reports errors, never grades, and every row quotes the words
it found — the same standard golden rule 2 holds the AI findings to, for the
same reason: a finding the PI cannot locate in their own draft is not
actionable.

These rows are kept OUT of the completeness score. A leftover "TBD" is not
incompleteness against the solicitation, and folding the two together would make
a number that already gets over-read mean even less.

FALSE POSITIVES ARE THE REAL RISK
---------------------------------
A checker that cries wolf gets ignored, and then the genuine placeholder ships
too. Every rule below is deliberately narrow and carries its own guard:

  * `[12]` is a citation, not a placeholder — only bracketed INSTRUCTIONS count.
  * A draft with no figure captions at ALL is not flagged for broken figure
    references: pasted text loses captions routinely, so absent captions mean
    the paste dropped them, not that the author wrote a dangling reference.
  * Short repeated lines are headings, not duplicated paragraphs.
  * A dollar figure is only compared against the saved budget when the text
    calls it a total, and only when a budget has actually been saved.
  * Citation markers are counted, with a threshold, because "(2019)" appears in
    ordinary prose.
"""

from __future__ import annotations

import re
from typing import Optional

# Instructions to the author that were never resolved. Bracketed forms are
# restricted to imperative words: "[12]" is a reference marker, and flagging
# every numeric citation would bury the one real placeholder in noise.
# Case-INSENSITIVE: these are never ordinary prose in a proposal.
_PLACEHOLDER_PATTERNS = [
    r"\bTBD\b",
    r"\bTODO\b",
    r"\blorem ipsum\b",
    r"\bplaceholder\b",
    r"\[\s*(?:insert|add|enter|your|name|date|todo|tbd|xx)[^\]]{0,60}\]",
    r"\{\{[^}]{0,60}\}\}",
    r"<\s*(?:insert|your|name|date)[^>]{0,60}>",
]
_PLACEHOLDER_RE = re.compile("|".join(_PLACEHOLDER_PATTERNS), re.IGNORECASE)

# Case-SENSITIVE, because the lower-case forms are ordinary English and the
# convention for a placeholder is capitals. Shipped without this and a real draft
# was told "would allow us TO DO much more work" was unfilled template text —
# the exact false positive that makes a checker get ignored.
_PLACEHOLDER_CASED = re.compile(
    r"\bTO DO\b"
    r"|\bXXX+\b"
    # A literal "Figure X" / "Table X" — the template was never filled in.
    r"|\b(?:Figure|Table) X\b")

# "as shown in Figure 3" / "see Table 2"
_REFERENCE_RE = re.compile(r"\b(Figure|Fig\.|Table)\s+(\d+[A-Za-z]?)\b", re.IGNORECASE)
# A caption line: the label starts the line, which is how captions are written.
#
# THE LABEL MAY STAND ALONE, and requiring punctuation after it reported a
# caption as a dangling reference TO ITSELF. NSF's Collaborators & Other
# Affiliations form writes each table's caption as a bare line —
#
#     Table 4
#     4 Name: Organizational Affiliation Optional (email, Department)
#
# — so a funded proposal was told "Table 4 is referred to but never labelled"
# on a page where the label is right there. Still anchored to the LINE START and
# still requiring the line to END there, so "Table 4 shows the results" stays a
# reference rather than becoming its own caption.
_CAPTION_RE = re.compile(
    r"^[ \t]*(Figure|Fig\.|Table)\s+(\d+[A-Za-z]?)(?:\s*[.:—-]|[ \t]*$)",
    re.IGNORECASE | re.MULTILINE)

# (Smith 2019) / (Lee et al., 2021) / [17]
_CITATION_RE = re.compile(
    r"\([A-Z][A-Za-z\-]+(?:\s+et\s+al\.?)?,?\s+(?:19|20)\d\d[a-z]?\)|\[\d{1,3}\]")
_REFERENCE_SECTION_RE = re.compile(
    r"^[ \t]*(references(\s+cited)?|bibliography|literature\s+cited|works\s+cited)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE)
# Below this, parenthesised years are just prose ("a prior award (2019)").
_MIN_CITATIONS = 3

# A dollar figure the text itself calls a total, either order.
_TOTAL_BEFORE = re.compile(r"\btotals?\b[^.$\n]{0,40}\$\s*([\d,]+)", re.IGNORECASE)
_TOTAL_AFTER = re.compile(r"\$\s*([\d,]+)[^.\n]{0,40}\b(?:in\s+)?totals?\b", re.IGNORECASE)

# Shorter than this and a repeated block is a heading or a stock phrase
# ("Not applicable"), not a paragraph pasted twice.
_MIN_DUPLICATE_WORDS = 20


def _snippet(text: str, start: int, end: int, pad: int = 45) -> str:
    """The offending words with enough around them to be findable in the draft."""
    return " ".join(text[max(0, start - pad):end + pad].split())


def _row(kind: str, label: str, detail: str, evidence: str) -> dict:
    return {"kind": kind, "label": label, "detail": detail, "evidence": evidence}


def _placeholders(text: str) -> list[dict]:
    out, seen = [], set()
    hits = list(_PLACEHOLDER_RE.finditer(text)) + list(_PLACEHOLDER_CASED.finditer(text))
    for m in sorted(hits, key=lambda m: m.start()):
        found = m.group(0)
        key = found.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(_row(
            "placeholder",
            f"Unfilled placeholder: {found}",
            "This looks like text you meant to replace. Reviewers read it as "
            "an unfinished proposal.",
            _snippet(text, m.start(), m.end())))
    return out


def _broken_references(text: str) -> list[dict]:
    captions = {m.group(2).lower() for m in _CAPTION_RE.finditer(text)}
    # THE GUARD: no captions anywhere means the paste dropped them (images do
    # not survive a copy-paste), not that every reference is dangling.
    if not captions:
        return []
    out, seen = [], set()
    for m in _REFERENCE_RE.finditer(text):
        num = m.group(2).lower()
        if num in captions or num in seen:
            continue
        # A caption may sit on the same line as the reference in tight prose;
        # only report labels that appear NOWHERE as a caption.
        seen.add(num)
        kind_word = "Table" if m.group(1).lower().startswith("t") else "Figure"
        out.append(_row(
            "broken_reference",
            f"{kind_word} {m.group(2)} is referred to but never labelled",
            f"The draft points at {kind_word} {m.group(2)}, but no caption for it "
            "appears in what you pasted. Either the label is missing or the "
            "number is wrong.",
            _snippet(text, m.start(), m.end())))
    return out


def _duplicate_paragraphs(text: str) -> list[dict]:
    seen: dict = {}
    out = []
    for block in re.split(r"\n\s*\n", text):
        norm = " ".join(block.split())
        if len(norm.split()) < _MIN_DUPLICATE_WORDS:
            continue
        key = norm.lower()
        if key in seen:
            if seen[key] == 1:      # report each duplicated block once
                out.append(_row(
                    "duplicate_paragraph",
                    "The same paragraph appears more than once",
                    "A paragraph is repeated word for word. Usually a paste that "
                    "went in twice — it costs space against the page limit.",
                    norm[:180]))
            seen[key] += 1
        else:
            seen[key] = 1
    return out


def _number_conflicts(text: str, budget: Optional[dict]) -> list[dict]:
    """Only ever compares against a total the PI actually saved.

    With no budget there is nothing to conflict with, and picking a figure out
    of the narrative to call "the" total would be a fabricated error."""
    if not isinstance(budget, dict):
        return []
    total = budget.get("total_cost") or budget.get("total") or budget.get("total_project_cost")
    try:
        total = int(round(float(total)))
    except (TypeError, ValueError):
        return []
    if total <= 0:
        return []

    out, seen = [], set()
    for rx in (_TOTAL_BEFORE, _TOTAL_AFTER):
        for m in rx.finditer(text):
            raw = m.group(1)
            try:
                stated = int(raw.replace(",", ""))
            except ValueError:
                continue
            if stated == total or stated in seen:
                continue
            seen.add(stated)
            out.append(_row(
                "number_conflict",
                "The total in your narrative does not match your saved budget",
                f"The text states ${stated:,} as a total; the budget saved on this "
                f"proposal totals ${total:,}. One of them is out of date.",
                _snippet(text, m.start(), m.end())))
    return out


def _missing_references(text: str) -> list[dict]:
    markers = _CITATION_RE.findall(text)
    if len(markers) < _MIN_CITATIONS or _REFERENCE_SECTION_RE.search(text):
        return []
    m = _CITATION_RE.search(text)
    return [_row(
        "missing_references",
        "Works are cited but no reference list was found",
        f"{len(markers)} citations appear in the text with no References Cited "
        "section in what you pasted. If it is a separate file, upload it too.",
        _snippet(text, m.start(), m.end()))]


# ── LANGUAGE SLIPS ──────────────────────────────────────────────────────────
#
# NO DICTIONARY, DELIBERATELY, and it is the single most important decision in
# this block. A spellchecker is the obvious tool and the wrong one: a proposal's
# correct vocabulary — zwitterionic, Donnan, estuarine, potentiostat, MTDC, PSU,
# every gene and reagent and instrument name — is exactly what a dictionary does
# not contain. A checker that yells at correct science is ignored by the second
# page, and then the real errors ship too. That is not hypothetical here: this
# module already shipped `\bTO\s?DO\b` case-insensitive and flagged "would allow
# us TO DO much more work" in a real draft.
#
# So only slips that are decidable from the characters alone, and each with its
# own guard and its own test. What that buys is a low catch rate with a near-zero
# false-positive rate, which is the right trade for a tool nobody is obliged to
# read. What it CANNOT catch, and no amount of tuning here will: a one-off typo
# that is not a known misspelling ("Chesepeake", "zwiterionic"), and grammar
# ("The objectives is"). Both need a dictionary or a parser. Say so rather than
# implying the section was proofread.

# Doubled words that are ordinary English. "had had" is a past perfect, "that
# that" a legitimate relative clause. Without this the checker fires on correct
# prose, which is the whole failure mode above.
_LEGIT_DOUBLES = {"had", "that", "is", "no", "so", "very", "sic", "long", "ha"}
_DOUBLED_RE = re.compile(r"\b([A-Za-z]{2,})(\s+)(\1)\b", re.IGNORECASE)

# Never valid in ANY variety of English. British/American pairs are deliberately
# ABSENT (organise/organize, acknowledgement/acknowledgment) — flagging a valid
# spelling is the failure this block exists to avoid.
_MISSPELLINGS = {
    "seperate": "separate", "seperated": "separated", "seperately": "separately",
    "recieve": "receive", "recieved": "received", "occured": "occurred",
    "occuring": "occurring", "definately": "definitely", "alot": "a lot",
    "thier": "their", "becuase": "because", "enviroment": "environment",
    "goverment": "government", "developement": "development",
    "independant": "independent", "occurence": "occurrence",
    "refered": "referred", "sucessful": "successful", "succesful": "successful",
    "neccessary": "necessary", "necessery": "necessary",
    "accomodate": "accommodate", "existance": "existence",
    "maintainance": "maintenance", "responsibilty": "responsibility",
    "univeristy": "university", "departement": "department",
    "prefered": "preferred", "reccommend": "recommend", "recomend": "recommend",
    "publically": "publicly", "consistant": "consistent",
    "significatly": "significantly", "collaboratoin": "collaboration",
    "reserach": "research", "resutls": "results", "teh": "the",
}
_MISSPELLING_RE = re.compile(
    r"\b(" + "|".join(sorted(_MISSPELLINGS)) + r")\b", re.IGNORECASE)

# Two-word slips where both words are real, so no single-token rule can see them.
# Wrong in EVERY register -- that is the bar for this table. "in regards to"
# was here and was retired: usage guides call it nonstandard, but competent
# editors disagree, so it is a JUDGEMENT, and the panel rendering these rows
# promises "found by a rule, not a judgement -- these are errors". It was one of
# only two rows surviving on a FUNDED proposal, which is where the mismatch
# showed. Anything needing calibration does not belong here.
_CONFUSED_PHRASES = {
    "rather then": "rather than",
    "as oppose to": "as opposed to",
    "could of": "could have",
    "would of": "would have",
    "should of": "should have",
}
_CONFUSED_RE = re.compile(
    r"\b(" + "|".join(k.replace(" ", r"\s+") for k in _CONFUSED_PHRASES) + r")\b",
    re.IGNORECASE)

# A period between two words with no space. TWO lowercase letters are required
# before it, which is what excludes the forms that legitimately have none:
# initials ("A.B."), "U.S.", "e.g."/"i.e." (single letter before each period),
# decimals and section numbers (digits, not letters), and "Fig. 3"/"55, 4410"
# (the character after is not an upper-case letter).
_NO_SPACE_AFTER_RE = re.compile(r"(?<=[a-z]{2})\.(?=[A-Z][a-z])")
# A space before closing punctuation. Tabs and spaces only -- \s would match a
# newline and fire on a line that legitimately begins with a period.
_SPACE_BEFORE_RE = re.compile(r"[ \t]+([,;:])|[ \t]+(\.)(?=[ \t]|$)")
# Anything inside one of these is a URL or DOI, where dots abut letters by design.
_URLISH = ("http", "www.", "doi.org", "@")

# \u2500\u2500 Text that did not linearize \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# A PDF is two-dimensional and this text is one-dimensional, so a typeset
# formula or table arrives with its sub/superscripts and cells RELOCATED. The
# hole a displaced token leaves behind reads as "space before punctuation", and
# two cells landing side by side read as a doubled word. Measured on the
# Project Description of a FUNDED NSF proposal: 56 rows, 54 of them this.
#
# These rules are LAYOUT-SENSITIVE and are the only ones gated. The word-level
# rules (placeholders, misspellings, confused phrases) read the WORDS, which
# survive relinearisation, and stay on everywhere.
#
# OVER-SUPPRESSION IS THE DANGEROUS DIRECTION: a real error dropped here is
# never learned about, while a surviving false positive is merely annoying. So
# the evidence must be positive and local, never "this looks technical".

# pdfplumber's marker for a glyph it could not map. Nothing on a line carrying
# one is reliably what the author typed.
_UNMAPPED_GLYPH = "(cid:"
# Operators that essentially never appear in running prose. Lower-case Greek is
# DELIBERATELY ABSENT -- a chemist writes "\u03b2-lactam" and "\u03bc-molar" in ordinary
# sentences, and treating those as maths would strip the spacing checks from
# every technical draft.
_MATHS_SYMBOLS = set("\u2297\u2295\u2282\u2286\u2283\u2287\u222a\u2229\u2208\u2209\u220b\u2192\u2190\u21a6\u21d2\u21d4\u2211\u220f\u222b\u221a\u221e\u2264\u2265\u2260\u2248\u2261\u226a\u226b\u2200\u2203\u2204\u2202\u2207\u22a5\u2227\u2228\u00ac\u00b1\u00d7\u00f7\u27e8\u27e9\u2205\u2124\u211d\u2102\u2115\u00af")
# A line that is nothing but stranded fragments -- the displaced subscripts
# themselves ("1", "2 3", "xi i xj xj i ij", "0\u00af 1\u00af 0\u00af"). Its presence NEXT to a
# line is what proves that line was mis-linearized, and it is the only evidence
# available where the maths symbols went onto a different line than the prose.
_MAX_FRAGMENT_CHARS = 3


def _is_fragment_line(line: str) -> bool:
    """Every token short enough to be a displaced sub/superscript, not a word."""
    tokens = line.split()
    if not tokens:
        return False
    return all(len(t) <= _MAX_FRAGMENT_CHARS for t in tokens)


# TWO ROWS OF A FORM ZIPPED TOGETHER. pdfplumber groups characters into lines by
# baseline, and `document_text.PDF_Y_TOLERANCE = 5` is wide enough that NSF's
# budget form -- which stacks two rows of small type within five points -- comes
# back as ONE line with the two rows' characters interleaved by x-position:
#
#   'A. KS eE yN AIO ssR o/ cK iaE tY es P (E LR isS t O eaN cN h E sL e: ...'
#
# i.e. "A. SENIOR/KEY PERSONNEL: PD/PI, Co-PI's, Faculty ..." merged with the row
# beneath it. A PI was shown four "space before punctuation" mistakes quoting
# that line, on a FUNDED proposal.
#
# LOWERING THE TOLERANCE IS NOT THE FIX, and that is measured. At y_tolerance=2
# the form reads cleanly and drops 56 characters per page elsewhere, and the
# Project Description's superscript numerals vanish ("two undergraduates in
# Year 1" -> "in Year ; three") -- the exact sentence the tolerance was raised
# to 5 to repair. No single value reads both correctly, so the reading is left
# alone and this stops the damage being reported as the author's writing.
#
# THE SIGNAL IS MEAN TOKEN LENGTH, not `all(len(t) <= 3)`. That older test is
# right for a line of displaced subscripts and wrong here: interleaving leaves a
# MIX ('arI/' is four characters), so one long fragment let the whole line
# through. Interleaving SHATTERS words, so the mean collapses -- measured 2.2 on
# the form row against 3.5-4.1 for real prose built of short words.
#
# Measured over all 12 PDFs of the awarded package: 20 of 1,337 judged lines
# (1.5%) trip this, and every one is the budget form, interleaved maths, a table
# row of single letters, or the Morgan letterhead's spaced-out logo. No prose
# sentence is among them.
_MIN_TOKENS_TO_JUDGE = 6          # fewer than this and a low mean is a heading
_MIN_MEAN_TOKEN_CHARS = 3.0


def _is_interleaved_line(line: str) -> bool:
    """Words shattered into fragments — two columns read as one line."""
    tokens = [t for t in line.split() if any(c.isalpha() for c in t)]
    if len(tokens) < _MIN_TOKENS_TO_JUDGE:
        return False
    return sum(len(t) for t in tokens) / len(tokens) < _MIN_MEAN_TOKEN_CHARS


def _damaged_lines(text: str) -> set[int]:
    """Indices of lines whose spacing cannot be trusted.

    A line is damaged when it carries direct evidence itself (an unmapped glyph,
    a maths operator, or its own words shattered into fragments), or when it
    ADJOINS a fragment line -- because the fragment line holds the very tokens
    that were lifted out of it.

    An interleaved line does NOT damage its neighbours: the adjacency rule
    exists for text something was lifted OUT of, and nothing was lifted out of
    the rows either side of a merged one.
    """
    lines = text.split("\n")
    fragments = {i for i, ln in enumerate(lines) if _is_fragment_line(ln)}
    damaged = set()
    for i, ln in enumerate(lines):
        if (_UNMAPPED_GLYPH in ln or any(c in _MATHS_SYMBOLS for c in ln)
                or _is_interleaved_line(ln)):
            damaged.add(i)
        elif (i - 1) in fragments or (i + 1) in fragments:
            damaged.add(i)
    return damaged | fragments


def _line_index(text: str, pos: int) -> int:
    return text.count("\n", 0, pos)


# A line the PDF stamps on every page -- "Not for distribution", a submission
# receipt, a running header. It repeats VERBATIM, which is the whole signal, so
# no funder's wording is hard-coded here. Three is once-a-page on the shortest
# document that could have furniture at all.
_FURNITURE_REPEATS = 3


# A pagination stamp marks everything beside it as furniture however FEW times
# it occurs -- which is the case the repeat count cannot see, and a one-page
# section is exactly where it arises.
_PAGE_STAMP_RE = re.compile(r"^Page\s+\d+\s+of\s+\d+$", re.IGNORECASE)


def _running_furniture(text: str) -> set[str]:
    counts: dict[str, int] = {}
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            counts[stripped] = counts.get(stripped, 0) + 1
    return {ln for ln, n in counts.items() if n >= _FURNITURE_REPEATS}


# A font that stores "Ž" as "Z" + a bare caron leaves the accent as its own
# character, and the extractor then pushes the following punctuation off the
# word: "D. Ž. Djoković" arrives as "D. Zˇ . Djokovic´". A token ENDING in a
# bare accent is always this decomposition, never something an author typed --
# a properly composed "Djoković" is one character and is untouched by this.
_BARE_DIACRITICS = (
    set("´`¨¸")
    | {chr(c) for c in range(0x02B0, 0x0300)}   # spacing modifier letters
    | {chr(c) for c in range(0x0300, 0x0370)}   # combining marks
)

# A row of table cells, not a sentence: no terminal punctuation, and the tokens
# are overwhelmingly capitalised labels. Extracted PROSE wraps and so also ends
# without punctuation -- capitalisation is what tells the two apart, and it is
# why the ratio is load-bearing rather than decoration.
_TABLE_CAPS_RATIO = 0.6
_MIN_TABLE_TOKENS = 3


def _looks_like_table_row(line: str) -> bool:
    tokens = line.split()
    if len(tokens) < _MIN_TABLE_TOKENS:
        return False
    if line.rstrip()[-1:] in ".!?":
        return False
    alpha = [t for t in tokens if t[:1].isalpha()]
    if len(alpha) < _MIN_TABLE_TOKENS:
        return False
    caps = sum(1 for t in alpha if t[:1].isupper())
    return caps / len(alpha) >= _TABLE_CAPS_RATIO


def _is_stranded_at_line_end(text: str, ws_start: int) -> bool:
    """The mark is the last thing on its line, with nothing after it.

    That is what a lifted italic run leaves behind: the page reads "...fit the
    Journal of Algebra, among others", the extractor moves the italic title to
    its own line, and the comma that followed it dangles. Prose does not end a
    line with a space-separated bare comma.

    A PERIOD is deliberately excluded: a sentence legitimately ends a line with
    one, so "…across 12 sites ." is a REAL typo, not debris. Including it here
    silenced that case, and the guard test caught it.
    """
    end = text.find("\n", ws_start)
    rest = text[ws_start:end if end != -1 else len(text)]
    return rest.strip() in {",", ";", ":"}


def _is_typesetting_period(text: str, ws_start: int, mark: str) -> bool:
    """A floating period that belongs to a machine-set reference, not to prose.

    Two shapes, both from References Cited and both measured there:
      * `Wegner. . Vol. 920.` -- pdfplumber lifted the italic TITLE out of a
        BibTeX entry and left the periods that bracketed it side by side.
      * `doi: 10 . 3842 / SIGMA . 2021 . 031` -- TeX sets a long DOI with
        breakable spaces, so every segment boundary reads as a stray period.

    Deliberately NOT a "this line is a bibliography entry" test: a bracketed
    citation number is ordinary in a Project Description, so keying on `[12]`
    would strip the spacing checks from real prose.
    """
    if mark != ".":
        return False
    before = text[:ws_start].rstrip()
    if before.endswith("."):           # `. .` -- a removed italic run
        return True
    # Inside a DOI: everything after the `doi:` on this line is one machine
    # identifier, however TeX chose to break it.
    line_start = text.rfind("\n", 0, ws_start) + 1
    if "doi:" in text[line_start:ws_start].lower():
        return True
    after = text[ws_start:].lstrip()[1:].lstrip()
    return bool(before[-1:].isdigit() and after[:1].isdigit())


_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*\u2022\u00b7]|\(?\d+[.)]|[a-z][.)])\s+")
# A bibliography line ends on a page range or a volume number, not a period.
_REFERENCE_TAIL_RE = re.compile(r"[\d\u2013-]+\s*$")
_SENTENCE_ENDERS = '.!?:;\u2026"\')]}'
# Below this a paragraph is a heading, a label or a table cell, not prose.
_MIN_PROSE_WORDS = 8
# How much of a paragraph's end to quote when its period is missing.
_TAIL_CHARS = 110


def _doubled_words(text: str) -> list[dict]:
    rows = []
    damaged = _damaged_lines(text)
    for m in _DOUBLED_RE.finditer(text):
        if m.group(1).lower() in _LEGIT_DOUBLES:
            continue
        if "\n" in m.group(2):          # across a line break it is a layout artifact
            continue
        if _line_index(text, m.start()) in damaged:
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.start())
        if _looks_like_table_row(text[line_start:line_end if line_end != -1 else len(text)]):
            continue
        rows.append(_row(
            "doubled_word", "A word is repeated",
            f'"{m.group(1)}" appears twice in a row. Delete one.',
            _snippet(text, m.start(), m.end())))
    return rows


def _misspellings(text: str) -> list[dict]:
    rows = []
    for m in _MISSPELLING_RE.finditer(text):
        word = m.group(1)
        fix = _MISSPELLINGS[word.lower()]
        # Match the author's capitalisation. "Univeristy" -> 'Did you mean
        # "university"?' reads as a second mistake in a tool about spelling.
        if word[:1].isupper():
            fix = fix[:1].upper() + fix[1:]
        rows.append(_row(
            "misspelling", "Misspelled word",
            f'"{word}" is not a word. Did you mean "{fix}"?',
            _snippet(text, m.start(), m.end())))
    for m in _CONFUSED_RE.finditer(text):
        phrase = " ".join(m.group(1).split()).lower()
        rows.append(_row(
            "misspelling", "Wrong word",
            f'"{m.group(1)}" should be "{_CONFUSED_PHRASES[phrase]}".',
            _snippet(text, m.start(), m.end())))
    return rows


def _spacing(text: str) -> list[dict]:
    rows = []
    damaged = _damaged_lines(text)
    for m in _NO_SPACE_AFTER_RE.finditer(text):
        window = text[max(0, m.start() - 40):m.end() + 40]
        if any(u in window for u in _URLISH):
            continue
        if _line_index(text, m.start()) in damaged:
            continue
        rows.append(_row(
            "spacing", "Missing space after a period",
            "Two sentences are run together with no space between them.",
            _snippet(text, m.start(), m.end())))
    for m in _SPACE_BEFORE_RE.finditer(text):
        if _line_index(text, m.start()) in damaged:
            continue
        mark = m.group(1) or m.group(2)
        if _is_typesetting_period(text, m.start(), mark):
            continue
        if _is_stranded_at_line_end(text, m.start()):
            continue
        if text[:m.start()].rstrip()[-1:] in _BARE_DIACRITICS:
            continue
        rows.append(_row(
            "spacing", "Space before punctuation",
            f'There is a space before the "{mark}". Close it up.',
            _snippet(text, m.start(), m.end())))
    return rows


def _unfinished_sentences(text: str) -> list[dict]:
    """A prose paragraph that stops without terminal punctuation.

    Four guards, and every one of them is a real shape in a real proposal:
    headings ("Overview") carry no period by NSF's own rule, list items are
    written without one, bibliography entries end on a page range, and anything
    short is a label rather than a sentence."""
    rows = []
    furniture = _running_furniture(text)
    for para in re.split(r"\n\s*\n", text):
        stripped = para.strip()
        if not stripped:
            continue
        lines = [ln for ln in stripped.splitlines() if ln.strip()]
        # Drop trailing page furniture so the REAL end of the prose is what gets
        # judged -- and quoted. A one-page section ends "...social experiences."
        # followed by "Page 4 of 56" and a submission stamp; without this the
        # rule reports the stamp, on a paragraph that ended perfectly well.
        while lines:
            tail = lines[-1].strip()
            if _PAGE_STAMP_RE.match(tail) or tail in furniture:
                lines.pop()
                continue
            # A stamp sitting directly on a pagination line is furniture too,
            # whatever its wording -- that is what makes this funder-agnostic.
            if len(lines) >= 2 and _PAGE_STAMP_RE.match(lines[-2].strip()):
                lines.pop()
                continue
            break
        if not lines:
            continue
        stripped = "\n".join(lines).strip()
        last_line = lines[-1].strip()
        if _LIST_MARKER_RE.match(last_line):
            continue
        # Displaced sub/superscripts, or two form rows read as one line —
        # damaged text, not a sentence missing its period. A form row genuinely
        # has no terminal punctuation and never should have.
        if all(_is_fragment_line(ln) or _is_interleaved_line(ln) for ln in lines):
            continue
        if len(stripped.split()) < _MIN_PROSE_WORDS:
            continue
        if stripped[-1] in _SENTENCE_ENDERS:
            continue
        if _REFERENCE_TAIL_RE.search(stripped):
            continue
        # Quote the TAIL, not the paragraph. The missing period is at the end,
        # and every other rule here quotes a short snippet — a 445-character
        # block is the paragraph again, not evidence someone can act on.
        tail = " ".join(last_line.split())[-_TAIL_CHARS:]
        rows.append(_row(
            "unfinished_sentence", "Sentence has no ending punctuation",
            "This paragraph stops without a period. Check it is complete.",
            ("\u2026" + tail) if len(last_line) > _TAIL_CHARS else tail))
    return rows


def find_mistakes(text: str, *, budget: Optional[dict] = None,
                  whole_document: bool = True) -> list[dict]:
    """Mechanical errors in `text`, each quoting the words it found.

    Deterministic and model-free (golden rule 1). Deliberately NOT scored: a
    leftover "TBD" is not incompleteness against the solicitation.

    `whole_document=False` when `text` is ONE section of a proposal rather than
    the package, and it suppresses the rules whose evidence can only live
    elsewhere. `missing_references` is the whole of that set today: References
    Cited is a separate section by NSF's own structure, so any well-cited
    Project Description tripped it, and its advice — "if it is a separate file,
    upload it too" — is impossible to follow in a modal that takes ONE file for
    ONE section and says the rest of the proposal is not needed. Unfollowable
    advice on every well-cited section is exactly the cry-wolf failure this
    module's docstring warns about.

    The others are deliberately KEPT, and each for a reason:
      * `placeholders` / `duplicate_paragraphs` — a leftover "TBD" and a
        paragraph pasted twice are errors in any span of text.
      * `broken_references` — already fails safe on its own guard: with no
        captions ANYWHERE it reports nothing, which is what a Project Summary or
        a References Cited section will do. Where a section does carry captions,
        a dangling reference inside it is findable and fixable inside it.
      * `number_conflicts` — compares against the budget the PI SAVED on the
        proposal, not against something elsewhere in the paste, so the evidence
        is present whichever entry point is running.

    A PARAMETER, not a filter over the output by label: the caller knows what it
    handed in, and matching on strings would break silently the moment a row is
    reworded.
    """
    text = text or ""
    if not text.strip():
        return []
    rows = (_placeholders(text)
            + _broken_references(text)
            + _duplicate_paragraphs(text)
            + _number_conflicts(text, budget)
            # Language slips are errors in ANY span of text, so unlike
            # `missing_references` they are not gated on whole_document.
            + _doubled_words(text)
            + _misspellings(text)
            + _spacing(text)
            + _unfinished_sentences(text))
    if whole_document:
        rows += _missing_references(text)
    return _dedupe(rows)


def _dedupe(rows: list[dict]) -> list[dict]:
    """One finding, once.

    A PI was shown FOUR "Space before punctuation" rows carrying the SAME quote:
    four marks close enough together that each one's context window clipped to
    the identical string. A mistake row has no line number (unlike a wording
    row, which carries one from `proofread.locate_quote`), so two rows agreeing
    on kind, label AND evidence are indistinguishable to the reader — they are
    one finding shown twice, and four of them buried the three real errors.

    EXACT match on ALL FOUR fields, deliberately — and `detail` is the one that
    is easy to leave out and must not be. Dropping it silently deleted a real
    error, caught by the existing suite rather than by anything written here:
    "We will seperate the two fractions and recieve the data" is TWO
    misspellings whose kind, label and quoted line are all identical, and only
    `detail` names the word ("seperate" vs "recieve"). Collapsing by kind-per-
    line would lose them the same way. This removes only rows a reader could not
    tell apart. Order is preserved so the first occurrence keeps its place.
    """
    out, seen = [], set()
    for row in rows:
        key = (row["kind"], row["label"], row["detail"], row["evidence"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out
