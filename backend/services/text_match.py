"""Whitespace-tolerant quote verification — the shared basis of golden rule 2.

Every AI claim in this codebase must quote its source, and the quote is checked
in CODE before the claim is shown. This is that check, in one place.

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
    return q in normalize(text, drop_list_noise=drop_list_noise)
