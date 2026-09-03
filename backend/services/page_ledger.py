"""Every page of an uploaded PDF is accounted for, or the review says so.

WHY THIS MODULE EXISTS. A PI uploaded an AWARDED 56-page NSF package and was
told five sections were missing; all five were in the document, on pages the
splitter had assigned to nothing, and NOTHING anywhere counted them. The
guarantee here is structural: code enumerates the pages before any model call
and reconciles the answers back by page id, so a page cannot be skipped -- only
left unanswered, which is a row on screen.

THE RECEIPT IS WHY THIS FILE DOES NOT JUST CALL `quote_in`. That function is
built to search a WHOLE DOCUMENT, and one of its widenings degenerates on a
single page: `_strip_page_furniture` sets `floor = len(marks)`, which is 1 when
there is one page marker, so every line qualifies as furniture, up to nine
consecutive lines of the author's prose are deleted, and a quote welded across
the gap verifies. Measured directly. A forged receipt is worse than no receipt,
so the haystack is cleaned here first and the page marker goes with it.
"""
from __future__ import annotations

import re
from typing import Optional

from services.text_match import quote_in

# A receipt must be long enough that it cannot arise by chance. Six words was
# enough for 56/56 real pages and rejected 56/56 wrong-page quotes.
RECEIPT_MIN_WORDS = 6

# Below this many non-furniture characters a page has nothing to quote. Measured
# on the real package: p40 is 71 chars ("Other Personnel Biographical
# Information / Data Not Available") and p49 is 13 -- nothing but the stamp,
# almost certainly a scanned signature page. A blank page is a FACT to report,
# never a failure to read.
BLANK_CHARS = 40

# Per-page and therefore NOT caught by the repetition threshold, but it is still
# furniture and must not be quotable. Dropping it also keeps `quote_in`'s own
# single-page furniture path unreachable: with no marker it returns None.
_PAGE_MARK = re.compile(r"^\s*page\s+\d+\s+of\s+\d+\s*$", re.I)

_WORD = re.compile(r"[^a-z0-9]+")


def document_furniture(page_texts: list) -> frozenset:
    """Lines the PDF stamps on most pages -- never the author's words.

    Delegates to `pdf_sections._furniture`, which uses a SHARE threshold over
    the whole document (`max(2, int(0.5 * n_pages))`) and so does not degenerate
    the way `text_match`'s single-page detector does. One definition, reused.
    """
    from services import pdf_sections as _ps
    return frozenset(_ps._furniture(page_texts or []))


def body_text(page_text: str, furniture: frozenset = frozenset()) -> str:
    """A page with its stamps removed -- the only thing a receipt may quote."""
    out = []
    for line in (page_text or "").splitlines():
        bare = line.strip()
        if not bare or bare in furniture or _PAGE_MARK.match(bare):
            continue
        out.append(bare)
    return "\n".join(out)


def is_blank(page_body: str) -> bool:
    """True when there is nothing on this page to read or to quote."""
    return len((page_body or "").strip()) < BLANK_CHARS


def _words(s: str) -> list:
    return _WORD.sub(" ", (s or "").lower()).split()


def receipt_ok(page_body: str, quote: str) -> bool:
    """True if `quote` is really on this page. The proof that the page was read.

    Two readings, and the second is not slack for its own sake. Measured on the
    real document: the page carries a CURLY closing quote and the model returned
    the sentence with that character OMITTED rather than substituted, which
    `normalize`'s character fold does not cover. Punctuation is exactly what PDF
    extraction damages, and a receipt proves CONTACT with the page, not the
    precision of a claim -- a weaker job than golden rule 2's evidence gate, and
    deliberately a weaker test.

    Adjacency holds for an ordinary quote: the words must appear as a
    contiguous run in the page's own order, so a quote assembled from
    scattered phrases fails. The one exception is an ELLIPSIS-abridged quote
    ("fragment ... fragment"), which `quote_in`'s own abridgement fallback
    accepts when each fragment individually clears its length floor and
    appears on the page in order -- never across a gap it invented, and never
    across a PAGE boundary, because `page_body` is only ever this one page's
    text. Verified over the whole 56-page document: 0 of 56 wrong-page quotes
    accepted.
    """
    if not (quote or "").strip():
        return False
    # SELF-CLEANING, NOT A CONVENTION. `page_body` used to be a bare parameter
    # name trusting the caller had already run it through `body_text` -- a RAW
    # page handed in directly re-opens the exact furniture-widening hole this
    # module exists to close, because `quote_in`'s degenerate single-page path
    # only goes inert once the page marker is gone. Measured: a caller who
    # skips `body_text` and hands `receipt_ok` a raw page gets the hole back,
    # verbatim. Idempotent -- an already-cleaned body has no marker left to
    # strip and comes back unchanged -- so this costs nothing on the path every
    # caller was already meant to take, and closes it for the one that forgets.
    page_body = body_text(page_body)
    qwords = _words(quote)
    if len(qwords) < RECEIPT_MIN_WORDS:
        return False
    if quote_in(page_body, quote):
        return True
    pwords = _words(page_body)
    n = len(qwords)
    return any(pwords[i:i + n] == qwords for i in range(len(pwords) - n + 1))
