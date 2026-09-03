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


# Four pages per call, MEASURED not chosen. Over the real 56-page package:
# 4 -> 56/56 receipts in 22.8s (14 calls); 12 -> 55/56 in 41.6s; 28 -> 54/56.
# Smaller is more accurate AND faster, because short calls run concurrently
# under `_MODEL_SLOTS`. Agrees with the published direction (arXiv:2301.08721
# measures a significant accuracy drop at batch size 6) and with this repo's own
# strongest measurement -- one rule per call gave 92% ten times out of ten where
# batch=15 split 83%/92%.
PAGE_WINDOW = 4

# Enough of a page for the model to name it; the receipt only needs one real
# line. Whole pages would multiply input tokens for no measured gain.
_PAGE_CHARS = 2600

_WALK_SYSTEM = """You are reading ONE PAGE AT A TIME of an assembled grant proposal PDF.
Read every line of each page you are given, top to bottom, before answering.

For EVERY page number listed in the input you MUST return exactly one object.
Never omit a page. If you cannot tell what a page is, return "unsure" as the
section -- never leave the page out.

Each object:
  page    : the page number, exactly as given
  section : one value from the allowed list, or "unsure"
  quote   : a VERBATIM span of at least 6 words copied character-for-character
            from THAT page's text. It is your proof that you read the page.
            Never invent it, never take it from another page, never paraphrase.

A page with a heading is named by its heading. A page with no heading -- a
letter, a form, a continuation of prose -- belongs to whatever it continues.
Return ONLY {"pages":[...]}."""


def _window_prompt(nums, page_texts, section_keys, known):
    body = "\n\n".join(
        f"=== PAGE {n} ===\n{(page_texts[n - 1] or '')[:_PAGE_CHARS]}" for n in nums)
    fixed = ""
    if known:
        named = "; ".join(f"page {p} is {k}" for p, k in sorted(known.items()) if k)
        if named:
            # Anchors, so the walk labels CONSISTENTLY around what the PDF's own
            # structure already settled. Never a licence to overrule it --
            # `build_ledger` keeps the structural answer regardless.
            fixed = f"\nAlready established from the document's structure: {named}.\n"
    return (f"Allowed section values: {', '.join(section_keys)}, unsure\n{fixed}\n"
            f"Return exactly {len(nums)} objects, one for each of pages {list(nums)}.\n\n{body}")


def _ask_window(nums, page_texts, section_keys, known):
    """One model call for one window. Returns {page: {section, quote}}."""
    from services import draft_review as _dr
    from services import gemini_client as _gc

    reply = _dr._ask_model(
        _gc.generate_json,
        _window_prompt(nums, page_texts, section_keys, known),
        system_instruction=_WALK_SYSTEM,
        model=_dr.MODEL, location=_dr.MODEL_LOCATION,
        temperature=0.0, max_output_tokens=8192,
        thinking_budget=_dr.THINKING_BUDGET,
        # A bare top-level array is an ANSWER, not a failure: commit 3553be5
        # records one costing 15 rules and rendering a false 100%.
        list_key="pages",
    )
    out = {}
    for row in (reply or {}).get("pages") or []:
        try:
            page = int(row.get("page"))
        except (TypeError, ValueError):
            continue
        # Reconcile by ID, never by count. A row for a page we did not send is
        # dropped rather than trusted -- same rule as `_review_batch`.
        if page in nums:
            out[page] = {"section": row.get("section"),
                         "quote": (row.get("quote") or "").strip()}
    return out


def walk_pages(page_texts: list, section_keys: list, *, furniture=frozenset(),
               known: Optional[dict] = None) -> dict:
    """Ask the model what each page is. {page number: {section, quote}}.

    Windows run CONCURRENTLY but every call passes through
    `draft_review._ask_model`, so this contends for the existing semaphore
    rather than opening a fourth uncapped pool (`services/proofread.py` does
    that, and it is a defect this must not copy).

    A page missing from its window's reply is RE-ASKED ON ITS OWN before being
    given up on. Never raises: with no model this returns {}.
    """
    from concurrent.futures import ThreadPoolExecutor

    n = len(page_texts or [])
    if not n or not section_keys:
        return {}
    windows = [list(range(i, min(i + PAGE_WINDOW, n + 1)))
               for i in range(1, n + 1, PAGE_WINDOW)]
    got: dict = {}
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for part in pool.map(
                    lambda w: _ask_window(w, page_texts, section_keys, known), windows):
                got.update(part or {})
    except Exception as exc:                       # noqa: BLE001 -- golden rule 3
        print(f"[PAGE-LEDGER] walk failed: {exc}")

    missing = [p for p in range(1, n + 1) if p not in got]
    if missing:
        print(f"[PAGE-LEDGER] re-asking {len(missing)} page(s): {missing}")
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for part in pool.map(
                        lambda p: _ask_window([p], page_texts, section_keys, known), missing):
                    got.update(part or {})
        except Exception as exc:                   # noqa: BLE001
            print(f"[PAGE-LEDGER] re-ask failed: {exc}")
    return got


def build_ledger(page_texts: list, sections: dict, *,
                 structure: Optional[dict] = None) -> list:
    """One row per page, built by CODE before anything else runs.

    The loop below is the whole guarantee: a page cannot be skipped, only left
    unanswered, and an unanswered page is a row reading `unassigned` that the
    modal renders. Precedence is structure > model > blank > unassigned --
    `pdf_sections` is deterministic and identical every run, so the walk fills
    gaps and never overrules it; a disagreement is RECORDED, not resolved.
    """
    n = len(page_texts or [])
    structure = {int(k): v for k, v in (structure or {}).items()}
    furniture = document_furniture(page_texts or [])
    bodies = [body_text(t, furniture) for t in (page_texts or [])]
    keys = list(sections or {})

    answers = walk_pages(page_texts, keys, furniture=furniture,
                         known=structure) if keys else {}

    rows, order, seen = [], [], set()
    for page in range(1, n + 1):
        body = bodies[page - 1]
        row = {"page": page, "section": None, "source": "unassigned",
               "quote": "", "verified": False, "chars": len(body.strip())}

        fixed = structure.get(page)
        answer = answers.get(page) or {}
        guess = answer.get("section")
        quote = answer.get("quote") or ""

        if fixed:
            row.update(section=fixed, source="structure")
            if guess and guess != fixed and guess in sections:
                row["disagreed_with_model"] = guess
        elif is_blank(body):
            row["source"] = "blank"
        elif guess in sections and receipt_ok(body, quote):
            # CONTIGUITY. A section that has already ended cannot reappear --
            # page 47 is not the Project Summary. A label that breaks the order
            # is refused in code rather than argued with.
            if guess in seen and (order and order[-1] != guess):
                row["refused"] = guess
            else:
                row.update(section=guess, source="model", quote=quote, verified=True)
        elif guess in sections and quote:
            row["refused"] = guess               # answered, receipt did not hold

        if row["section"]:
            if not order or order[-1] != row["section"]:
                order.append(row["section"])
            seen.add(row["section"])
        rows.append(row)
    return rows


def completeness(rows: list) -> tuple:
    """(every page accounted for?, the page numbers that are not).

    `blank` COUNTS as accounted for: a page with nothing on it was read and
    found empty, which is a fact about the document rather than a gap in our
    reading. Only `unassigned` is a gap.
    """
    unaccounted = [r["page"] for r in (rows or []) if r.get("source") == "unassigned"]
    return (not unaccounted), unaccounted


def reconcile_toc(rows: list, page_texts: list, sections: dict) -> list:
    """Where the ledger and NSF's own table of contents disagree about a length.

    AN EXTERNAL CHECK -- the funder wrote these counts, not us and not the
    model. It is the strongest defence available against a WRONG label, which is
    ~6x more damaging than a missing one (12 points against 2 on a 50-rule
    review) and can report an absent required attachment as present.

    It REPORTS, it never overrides, and it tolerates a legitimate exception --
    the same discipline as `pappg_ingest`'s `suspicious_yield`. Measured on the
    real package, the one standing mismatch is CORRECT: an auto-generated NSF
    "Data Not Available" filler page makes the biographical-sketch count 3 where
    the table of contents says 2. The document and its own table of contents
    genuinely disagree, and showing that beats picking a side.

    Returns [] when there is no NSF table of contents, so a package from any
    other source simply gets no third check.
    """
    from services import pdf_sections as _ps
    from collections import Counter

    roster = _ps._find_toc(page_texts or [], sections or {})
    if not roster:
        return []
    want = {k: n for k, n, _ in roster if k}
    counted = Counter(r["section"] for r in (rows or []) if r.get("section"))
    out = []
    for key, expected in want.items():
        got = counted.get(key, 0)
        if got and got != expected:
            out.append({"section": key,
                        "label": (sections.get(key) or {}).get("label") or key,
                        "ledger_pages": got, "toc_pages": expected})
    return out
