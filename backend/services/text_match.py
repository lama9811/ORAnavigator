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
services/eir_review.py. The same rule is independently implemented in
kb_scraper (adjudicator / file_adjudicator / fingerprint), which runs as a
separate Cloud Run Job and cannot import from backend/.
"""

from __future__ import annotations


def quote_in(text: str, quote: str) -> bool:
    """True if `quote` appears in `text`, ignoring whitespace/line-wrap and case.

    An empty or whitespace-only quote is False — "no quote" must never verify,
    or an unsupported claim would pass the grounding check for free.
    """
    q = " ".join((quote or "").lower().split())
    if not q:
        return False
    return q in " ".join((text or "").lower().split())
