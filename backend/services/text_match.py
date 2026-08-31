"""Whitespace-tolerant quote verification — the shared basis of golden rule 2.

Every AI claim in this codebase must quote its source, and the quote is checked
in CODE before the claim is shown. This is that check, in one place.

A SECOND ARTIFACT, ADDED 2026-08-26: a word broken across a line by a
typesetter's hyphen. A PI pasted a Project Summary out of a typeset PDF with
twelve such breaks; the reviewer judged all three content rules `addressed`,
and two of the three were dropped here because their quotes spanned a break.
The PI was told a section was "Not found" under a note saying it was covered.
See `_readings` for why the text is read two ways rather than normalised one.

WHY THE WHITESPACE COLLAPSE IS LOAD-BEARING
-------------------------------------------
A pasted or PDF-extracted draft is hard-wrapped, so it contains
"health,\\nand" where the model quotes "health, and". A raw substring test then
REJECTS a real quote — and the caller reads that as "the author didn't write
this", marking a complete section missing. Collapsing all whitespace runs on
both sides before matching is what prevents that.

Extracted from services/section_coach._quote_in when the Drafting Coach was
removed (2026-08-10). It had already been factored out of that module's
_verify_evidence precisely so every grounded feature shares ONE definition;
this just gives it a home that doesn't belong to any one feature. Callers today:
services/draft_review.py. The same rule is independently implemented in
kb_scraper (adjudicator / file_adjudicator / fingerprint), which runs as a
separate Cloud Run Job and cannot import from backend/.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

# Bullet glyphs and pdfplumber's undecoded-glyph artifacts ("(cid:127)"). These
# sit BETWEEN the items of a bulleted list, which is the single most common
# shape a solicitation requirement takes. A model quoting such an item returns
# it clean; the PDF text has the glyphs in it; a raw match then fails and the
# caller drops a real requirement — silently, and systematically, on exactly the
# rows the feature exists to capture. Mirrors solicitation_extractor._LIST_NOISE_RE.
_LIST_NOISE_RE = re.compile(r"\(cid:\d+\)|[•‣▪●·∙◦⁃]")


def normalize(s: str, *, drop_list_noise: bool = False) -> str:
    """Lowercase + collapse whitespace, optionally dropping PDF list noise."""
    s = (s or "").lower()
    if drop_list_noise:
        s = _LIST_NOISE_RE.sub(" ", s)
    return " ".join(s.split())


# A DASH ending a line: a word the typesetter split, or a real compound that
# happened to land on the break. Matched AFTER the whitespace collapse, so the
# newline has already become a single space.
#
# ALL THREE DASHES, and that is not defensiveness. The first version of this
# matched hyphen-minus alone and recovered two of the three rules that had been
# wrongly dropped on a real draft; the third stayed broken because that
# paragraph breaks on `decomposition–` with an EN DASH. A fix covering one of
# the characters a typesetter actually uses is a fix that looks like it worked.
#
# `\w` on BOTH sides is the safety property: a dash used as punctuation between
# spaces ("2019 - 2024") has whitespace before it, so nothing fires and ranges
# are never glued together.
_SOFT_HYPHEN_RE = re.compile(r"(\w)[-\u2010\u2011\u2012\u2013\u2014]\s+(\w)")
_DASHES = "-\u2010\u2011\u2012\u2013\u2014"


def has_line_break_hyphen(s: str) -> bool:
    """True when `s` contains a word split by a dash at a line end.

    The SAME rule `_readings` uses, exposed because `proofread` needs to
    recognise our own extraction damage and a second definition of it would
    drift -- this module exists because "verified" was once implemented twice.
    A dash between spaces ("2019 - 2024") does not match: a word character is
    required on both sides.
    """
    return bool(_SOFT_HYPHEN_RE.search(s or ""))


def _readings(s: str) -> list[str]:
    """Every defensible reading of `s` where a hyphen ends a line.

    A hyphen at a line end is genuinely AMBIGUOUS and nothing in the text says
    which it is: `un- dergraduate` is one word split by the typesetter, while
    `bosonic- fermionic` is a real compound that landed on the break. Joining
    always would break the second; keeping the hyphen always leaves the first
    broken. So both readings are produced and a quote matching EITHER verifies.

    This cannot manufacture a false positive: both readings are derived from the
    text itself, so a quote matching one is a quote that appears in the text
    under a defensible reading of it. A quote matching neither is still
    rejected, and only a HYPHEN licenses joining — two words split by a plain
    line break stay two words, or any adjacent pair could be run together.
    """
    if not any(d + " " in s for d in _DASHES):
        return [s]
    # The KEPT reading restores the dash the text actually used (\g<0> would
    # bring the space back), so a real compound keeps the author's own
    # character rather than being normalised to a hyphen.
    kept = _SOFT_HYPHEN_RE.sub(
        lambda m: m.group(1) + m.group(0)[len(m.group(1))] + m.group(2), s)
    return [_SOFT_HYPHEN_RE.sub(r"\1\2", s), kept]


# A THIRD CHARACTER-LEVEL ARTIFACT, and the one that silently loses whole
# requirements. A typeset PDF sets `fi`, `ff` and `ffi` as SINGLE glyphs.
# pdfplumber cannot decode them and emits a control character -- 112 times in
# one real NSF solicitation -- and `solicitation_extractor.read_pdf` strips
# those, so `justification` is stored as `justication` and `Office` as `Oce`.
# 72 words were mangled in that document.
#
# The model reads the mangled text and writes the word CORRECTLY when it
# quotes, so its quote no longer matches our copy and golden rule 2 discards a
# real rule. That solicitation reported "2 proposed requirements were dropped
# because they could not be quoted"; both were genuine NSF requirements.
#
# UN-STRIPPING IS NOT AVAILABLE. The same control character stands for several
# different ligatures -- `Noti-cation` is fi, `o-cer` is ffi, `e-ect` is ff --
# so nothing in the character says which letters were lost. The COMPARISON is
# made aware of the artifact instead, which needs no guess about the text.
#
# LONGEST FIRST: "office" is o + ffi + ce, and taking `ff` first leaves "oice",
# which matches nothing.
_LIGATURES = ("ffl", "ffi", "ff", "fl", "fi")


def _drop_ligatures(s: str) -> str:
    for lig in _LIGATURES:
        s = s.replace(lig, "")
    return s


# A FOURTH ARTIFACT, ADDED 2026-08-28: page furniture injected MID-SENTENCE.
# Research.gov stamps every page of a submitted proposal with a header/footer
# block, so a sentence crossing a page boundary extracts as
#
#     "Hence, your research programs are strongly
#      Page 52 of 56
#      Revised Proposal Budget Revision #1 for 2503008 Submitted On ...
#      Submitted/PI: Dwight A Williams Ii /Proposal No: 2503008
#      supported by me and they firmly align with the mission ..."
#
# The reviewer reads the sentence and quotes it whole; a contiguous match then
# fails and golden rule 2 demotes a real `addressed` to `not_found`. Measured on
# the awarded NSF EiR package: "Attach Letter of Institutional Support" reported
# MISSING, under a note describing the letter that is right there. A required
# attachment declared missing is the compliance rejection this tool exists to
# prevent, invented by the tool.
#
# ANCHORED TO THE PAGE MARKER, NEVER TO REPETITION ALONE. In that same file the
# line "Sincerely," occurs three times — real prose, in three letters. Dropping
# lines because they repeat would delete an author's words from the text every
# claim is checked against, which is the dangerous direction. A line is
# furniture only if it sits in an unbroken run beside a "Page N of M" marker AND
# recurs at least as often as those markers do, i.e. it is on essentially every
# page.
_PAGE_MARKER_RE = re.compile(r"^\s*page\s+\d+\s+of\s+\d+\s*$", re.I)

# How far a furniture run may reach from its marker. A header/footer is a few
# lines; an unbounded walk could eat a paragraph that happens to repeat.
_FURNITURE_REACH = 4


def _strip_page_furniture(text: str) -> Optional[str]:
    """`text` with per-page headers/footers removed, or None if there are none.

    Returns None rather than the unchanged text so the caller can skip building
    a second reading it would never need — the same "cannot reach ordinary text"
    property the ligature fallback gets from testing the quote first.
    """
    lines = (text or "").split("\n")
    marks = [i for i, ln in enumerate(lines) if _PAGE_MARKER_RE.match(ln)]
    if not marks:
        return None
    counts = Counter(ln.strip() for ln in lines if ln.strip())
    floor = len(marks)          # on every page, or it is not furniture

    drop: set[int] = set()
    for i in marks:
        drop.add(i)
        for step in (-1, 1):
            j = i + step
            while 0 <= j < len(lines) and abs(j - i) <= _FURNITURE_REACH:
                key = lines[j].strip()
                if key and counts[key] >= floor:
                    drop.add(j)
                    j += step
                    continue
                break
    if not drop:
        return None
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop)


# AN ABRIDGED QUOTE, ADDED 2026-08-28. The reviewer shortens its evidence with
# an ellipsis — "There are presently 50 math and actuarial science majors along
# with 37 doctoral students... Faculty at MSU are expected to carry 12 credit
# hours" — where both halves are in the draft, in that order, with a sentence
# between them. Not a CONTIGUOUS span, so this dropped it and the row was
# reported `not_found`: the draft was told it never described its institutional
# context. Measured over 50 runs of the awarded package, 30 findings were
# demoted this way and every one was in Project Description; after the
# reading-order fix, 32 of 55 dropped quotes carried an ellipsis and 25 of those
# had every fragment present, in order. The same shape defeats this repo's own
# curated NSF fixture row.
#
# WHAT IS GIVEN UP, PRECISELY. Golden rule 2 asks that a claim be checkable
# against the author's own words. Every word of the quote is still required to
# be in the draft, in the sequence the reviewer wrote them; only ADJACENCY is
# relaxed. A fabrication is not in the text under any reading and still fails.
#
# THE MINIMUM LENGTH IS THE GUARD THAT MATTERS. Without it this degrades into a
# bag-of-words match: "the PI... the work... the results" appears in order in
# almost every proposal ever written. ONE short fragment rejects the WHOLE
# quote rather than just that fragment — the conservative direction, because a
# lost quote costs a row and a wrong one costs the grounding guarantee.
_ELLIPSIS_RE = re.compile(r"\[\s*\.\s*\.\s*\.\s*\]|\.\s*\.\s*\.|…")
_MIN_FRAGMENT = 20


def _abridged_fragments(q: str) -> Optional[list[str]]:
    """The pieces of an ellipsis-abridged quote, or None if it is not one.

    None — never a partial list — when any piece is too short to be evidence,
    so the caller cannot accidentally verify a quote on its long half alone.
    """
    if not _ELLIPSIS_RE.search(q):
        return None
    parts = [p.strip(" .,;:") for p in _ELLIPSIS_RE.split(q)]
    parts = [p for p in parts if p]
    if not parts or any(len(p) < _MIN_FRAGMENT for p in parts):
        return None
    return parts


def _in_order(hay: str, parts: list[str]) -> bool:
    """Every fragment present, each one after the previous ends."""
    pos = 0
    for p in parts:
        j = hay.find(p, pos)
        if j < 0:
            return False
        pos = j + len(p)
    return True


def quote_in(text: str, quote: str, *, drop_list_noise: bool = False) -> bool:
    """True if `quote` appears in `text`, ignoring whitespace/line-wrap and case.

    An empty or whitespace-only quote is False — "no quote" must never verify,
    or an unsupported claim would pass the grounding check for free.

    `drop_list_noise` additionally ignores bullet glyphs and `(cid:N)` artifacts
    on both sides. It defaults to FALSE so every existing caller's behavior is
    byte-identical; pass True when matching model output against text extracted
    from a PDF, which is where those glyphs come from.
    """
    q = normalize(quote, drop_list_noise=drop_list_noise)
    if not q:
        return False
    haystacks = _readings(normalize(text, drop_list_noise=drop_list_noise))
    # The page-furniture reading is ADDITIONAL, never a replacement: the text as
    # extracted stays a haystack, so a quote of a line that this drops (the
    # author's own "Sincerely,") still verifies against the original.
    unfurnished = _strip_page_furniture(text)
    if unfurnished is not None:
        haystacks += _readings(normalize(unfurnished,
                                         drop_list_noise=drop_list_noise))
    if any(qr in hay for qr in _readings(q) for hay in haystacks):
        return True
    # ABRIDGEMENT FALLBACK, after the honest match and before the ligature one.
    # Reached only when the quote itself contains an ellipsis, so it cannot
    # touch a quote that does not use one — the same "cannot reach ordinary
    # text" property the ligature fallback gets from testing the quote first.
    parts = _abridged_fragments(q)
    if parts and any(_in_order(hay, parts) for hay in haystacks):
        return True
    # LIGATURE FALLBACK, attempted only after an honest match has failed AND
    # only when the quote actually contains a sequence a PDF can lose. That
    # second condition is a safety property, not an optimisation: a quote with
    # no ligature letters can never take the looser path, so this widening
    # cannot reach ordinary text.
    if not any(lig in q for lig in _LIGATURES):
        return False
    bare = _drop_ligatures(q)
    return any(bare in _drop_ligatures(hay) for hay in haystacks)
