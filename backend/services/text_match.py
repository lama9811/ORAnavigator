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
    return any(qr in hay for qr in _readings(q) for hay in haystacks)
