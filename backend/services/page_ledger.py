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
    """True if `quote` is really on THIS page's text. Content, not custody.

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
    text.

    WHAT THIS FUNCTION DOES NOT PROVE, ON ITS OWN: that the quote is not
    ALSO true of some other page. On the real 56-page package, NSF's own
    budget-form header ("PROPOSAL BUDGET FOR NSF USE ONLY") recurs verbatim
    on several budget-year pages -- under 50% of the document, so
    `document_furniture` never catches it -- and this function alone
    happily accepted it against a page that never produced it. A receipt is
    "content is on this page", not "content is ONLY on this page"; the
    second, stronger claim is `_receipt_is_solid`, below, which this
    function deliberately does not fold in -- it must keep working on one
    page in isolation, with no notion of "the rest of the document" at all,
    so its own tests stay meaningful standalone.
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


def _receipt_is_solid(bodies: list, page_idx: int, quote: str) -> bool:
    """THE SECURITY GUARANTEE, made explicit. A receipt is valid only if
    `quote` is on the page it claims AND ON NO OTHER PAGE.

    `receipt_ok` alone answers "is this quote on this page" -- and NSF's own
    budget-form header ("PROPOSAL BUDGET FOR NSF USE ONLY") is on FOUR
    pages, so that question alone let a page-27 answer's quote pass as
    proof of page-29, and a page-50 letter's opener pass as proof of
    page-51's different letter. Measured: 3 of 3 wrong-page acceptances in
    the live gate were exactly this -- boilerplate under the 50% share
    `document_furniture` requires to be caught, or a near-identical letter
    opener no threshold catches at all.

    Deliberately NOT folded into `receipt_ok` -- that function takes one
    page and must keep working standalone with its own tests; this one
    needs every page's text in hand, which only exists once the ledger is
    being built. One definition, called from both `build_ledger` (where a
    row is actually verified) and `walk_pages` (to decide what still needs
    asking) -- so the two can never disagree about what "solid" means.

    Cheap over the whole document: `receipt_ok` is regex/string work, not a
    model call, so checking a candidate against all ~55 other pages costs
    nothing worth measuring.
    """
    if page_idx < 0 or page_idx >= len(bodies):
        return False
    if not receipt_ok(bodies[page_idx], quote):
        return False
    return not any(i != page_idx and receipt_ok(other, quote)
                   for i, other in enumerate(bodies))


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

# Single-page retry rounds beyond the first windowed pass, MEASURED not
# chosen. On the real 56-page package a first pass typically leaves 10-16
# pages shaky (no answer, or a quote that also receipts another page); each
# targeted retry round -- with the SPECIFIC other-page(s) named -- clears
# most of what remains, but a handful of pages (a garbled multi-field cover
# form, several near-identical letter openers) keep reaching for the same
# non-unique text even when told exactly which pages it also matches, so a
# bounded THIRD round is worth the extra calls: it is cheap (one page, one
# call) and it is the only lever left once the prompt has already been made
# as specific as it can be without becoming a page-specific script.
_MAX_RETRY_ROUNDS = 3

# Nonzero ONLY for re-ask calls -- the first pass stays at temperature 0.0
# for reproducibility. MEASURED: at 0.0, a retry given the same
# `retry_notes` (naming the same other page(s)) returns the SAME wrong
# quote, round after round, because nothing about the prompt actually
# differs -- a deterministic model given an unchanged prompt has no reason
# to answer differently. This is what a bounded `_MAX_RETRY_ROUNDS` alone
# could not fix.
_RETRY_TEMPERATURE = 0.4

_WALK_SYSTEM = """You are reading ONE PAGE AT A TIME of an assembled grant proposal PDF.
Read every line of each page you are given, top to bottom, before answering.
Each page is shown RAW, including any running header, footer, or stamp --
use that to help identify the page, but see the quote rule below.

For EVERY page number listed in the input you MUST return exactly one object.
Never omit a page. If you cannot tell what a page is, return "unsure" as the
section -- never leave the page out.

Each object:
  page    : the page number, exactly as given
  section : one value from the allowed list, or "unsure"
  quote   : a VERBATIM span of at least 6 words copied character-for-character
            from THAT page's own CONTENT. It is your proof that you read the
            page, so it must be a line that could belong to NO OTHER page:
              - NEVER a running header, footer, page-number stamp
                ("Page N of M"), or "Submitted/PI: ..." line.
              - NEVER a form label or boilerplate that recurs on other pages
                of the same kind (e.g. a budget form's own printed heading,
                which repeats on every budget year page).
              - NEVER a short quoted title (a citation's article/book title
                in quotation marks is usually too short on its own -- quote
                the surrounding sentence instead, e.g. the author name plus
                the title, so the quote is at least 6 words AND unique to
                this page).
              - Prefer a full sentence from the page's own paragraph, letter
                body, or list content -- something no other page could
                contain word-for-word.
            Never invent it, never take it from another page, never paraphrase.

A page with a heading is named by its heading. A page with no heading -- a
letter, a form, a continuation of prose -- belongs to whatever it continues.
Return ONLY {"pages":[...]}."""


def _window_prompt(nums, page_texts, section_keys, known, retry_notes=None):
    # RAW page text, not the furniture-stripped body (fix round 2). Round 1
    # (IMP-5) showed the stripped body so a quote lifted verbatim from a
    # Research.gov stamp could not be returned in the first place -- but it
    # also removes whatever a page needs to be IDENTIFIED (a running header
    # can carry the one clue a bare content page lacks), and receipts are
    # still checked against the stripped body downstream regardless of what
    # the model is shown. `_WALK_SYSTEM` is what now keeps the model from
    # quoting the furniture it can see here -- the receipt gate is the
    # backstop, not the only line of defense.
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
    retry = ""
    if retry_notes:
        # SPECIFIC feedback, not a repeat of the same static instruction
        # (fix round 2). A generic "be unique" rule already sits in
        # `_WALK_SYSTEM` and the model still picked a template opener three
        # different collaboration letters share verbatim -- telling it
        # EXACTLY which pages the last quote also matched is what a human
        # reviewer would say next, and it is information only `walk_pages`
        # has (it required reading every other page).
        notes = "; ".join(f"page {p}: {retry_notes[p]}" for p in nums if retry_notes.get(p))
        if notes:
            retry = f"\nRETRY -- {notes}\n"
    return (f"Allowed section values: {', '.join(section_keys)}, unsure\n{fixed}{retry}\n"
            f"Return exactly {len(nums)} objects, one for each of pages {list(nums)}.\n\n{body}")


def _ask_window(nums, page_texts, section_keys, known, retry_notes=None, temperature=0.0):
    """One model call for one window. Returns {page: {section, quote}}.

    `page_texts` here is the RAW page text (fix round 2) -- see
    `_window_prompt`. Whatever the model returns is unverified until
    `walk_pages`/`build_ledger` check it against the stripped body; this
    function only reconciles row SHAPE.

    `temperature` defaults to 0.0 -- the first pass should be reproducible.
    A RETRY is a different situation (fix round 2, measured): at 0.0 a
    retry with the same `retry_notes` text produces the IDENTICAL wrong
    quote every round, because the model is deterministic and nothing about
    the prompt actually changed round to round. `walk_pages` passes a
    nonzero temperature on re-ask calls for exactly this reason -- not for
    quality, but so a second attempt can reach a DIFFERENT completion than
    the one already known to be wrong.

    Defensive about row SHAPE (fix round 1, IMP-2): a non-dict row or a
    numeric `quote` used to raise `AttributeError` out of this function, which
    is exactly what made a single malformed reply cost every LATER window its
    successful answers (IMP-1) -- `Executor.map`'s generator stops at the
    first raising future.
    """
    from services import draft_review as _dr
    from services import gemini_client as _gc

    reply = _dr._ask_model(
        _gc.generate_json,
        _window_prompt(nums, page_texts, section_keys, known, retry_notes=retry_notes),
        system_instruction=_WALK_SYSTEM,
        model=_dr.MODEL, location=_dr.MODEL_LOCATION,
        temperature=temperature, max_output_tokens=8192,
        thinking_budget=_dr.THINKING_BUDGET,
        # A bare top-level array is an ANSWER, not a failure: commit 3553be5
        # records one costing 15 rules and rendering a false 100%.
        list_key="pages",
    )
    out: dict = {}
    dropped = set()
    for row in (reply or {}).get("pages") or []:
        if not isinstance(row, dict):
            continue
        try:
            page = int(row.get("page"))
        except (TypeError, ValueError):
            continue
        # Reconcile by ID, never by count. A row for a page we did not send is
        # dropped rather than trusted -- same rule as `_review_batch`.
        if page not in nums or page in dropped:
            continue
        section = row.get("section")
        quote = str(row.get("quote") or "").strip()
        prior = out.get(page)
        if prior is not None and prior.get("section") != section:
            # Answered twice with DIFFERENT sections (IMP-3). A wrong label is
            # ~6x more damaging than a missing one, and here it would be
            # decided by array position -- drop the page rather than trust
            # either answer. The receipt binds a quote to a page, not a
            # section, so this is the one place a section can go wrong for
            # free; refuse it instead.
            out.pop(page, None)
            dropped.add(page)
            continue
        out[page] = {"section": section, "quote": quote}
    return out


def _safe_ask_window(nums, page_texts, section_keys, known, retry_notes=None, temperature=0.0):
    """`_ask_window` wrapped so ONE bad window can never cost another its
    answers (fix round 1, IMP-1). `Executor.map` is a generator that raises at
    the first failing future and abandons every result queued behind it --
    measured on a 12-page document with only window 2 malformed: pages 9-12
    were lost in the first pass, then 6-12 lost AGAIN in the re-ask, because
    the bad page sorts first in `missing`. On a real 56-page package one bad
    row could leave ~50 pages unassigned -- the exact symptom this task exists
    to remove. `_ask_window` is defensive about row shape now (IMP-2), but
    this catches whatever else could still raise (a `_dr._ask_model` failure,
    a malformed `known`), so no single window can ever take another down.
    """
    try:
        return _ask_window(nums, page_texts, section_keys, known,
                            retry_notes=retry_notes, temperature=temperature)
    except Exception as exc:                        # noqa: BLE001 -- golden rule 3
        print(f"[PAGE-LEDGER] window {nums} failed: {exc}")
        return {}


def walk_pages(page_texts: list, section_keys: list, *, furniture=frozenset(),
               known: Optional[dict] = None) -> dict:
    """Ask the model what each page is. {page number: {section, quote}}.

    Windows run CONCURRENTLY but every call passes through
    `draft_review._ask_model`, so this contends for the existing semaphore
    rather than opening a fourth uncapped pool (`services/proofread.py` does
    that, and it is a defect this must not copy).

    A page missing from its window's reply is RE-ASKED ON ITS OWN before being
    given up on. So is a page whose answer came back SHAKY -- receipted on no
    page, or receipted on more than one (fix round 2) -- reusing the exact
    same `_receipt_is_solid` check `build_ledger` uses to make its own final
    call, so a first answer that cannot be trusted gets one more chance
    before `build_ledger` ever sees it, the same courtesy IMP-1 already gives
    a page with no answer at all. Never raises: with no model this returns {}.
    """
    from concurrent.futures import ThreadPoolExecutor

    n = len(page_texts or [])
    if not n or not section_keys:
        return {}
    bodies = [body_text(t, furniture) for t in page_texts]
    windows = [list(range(i, min(i + PAGE_WINDOW, n + 1)))
               for i in range(1, n + 1, PAGE_WINDOW)]
    got: dict = {}
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for part in pool.map(
                    lambda w: _safe_ask_window(w, page_texts, section_keys, known), windows):
                got.update(part or {})
    except Exception as exc:                       # noqa: BLE001 -- golden rule 3
        print(f"[PAGE-LEDGER] walk failed: {exc}")

    had_answers = bool(got)                        # IMP-4's signal, captured
    # ONCE, before any round pops anything: with no model at all the first
    # pass already returns {} for every window, so a page-by-page re-ask
    # would be full amplification (measured: 12 pages -> 15 calls, 56 pages
    # -> 70) for zero benefit, since a dead model answers no better one page
    # at a time. A real partial/shaky miss is still re-asked below exactly
    # as before.
    if not had_answers:
        missing = [p for p in range(1, n + 1) if p not in got]
        if missing:
            print(f"[PAGE-LEDGER] skipping re-ask -- first pass returned nothing "
                  f"({len(missing)} page(s) would have amplified for no benefit)")
        return got

    for _round in range(1, _MAX_RETRY_ROUNDS + 1):
        # SPECIFIC retry feedback (fix round 2), not just "try again": for a
        # page whose quote receipted somewhere else too, name exactly which
        # page(s) -- a generic re-ask repeats the same wording the model
        # already ignored once (measured: three separate collaboration
        # letters share one boilerplate opener, and a bare "be unique"
        # instruction did not stop the model reaching for it three times
        # running). Reuses `_receipt_is_solid`, the exact check
        # `build_ledger` uses to make its own final call, so a first answer
        # that cannot be trusted gets another chance before `build_ledger`
        # ever sees it -- the same courtesy IMP-1 already gives a page with
        # no answer at all.
        retry_notes: dict = {}
        for p, ans in list(got.items()):
            if ans.get("section") not in section_keys or not ans.get("quote"):
                continue
            quote = ans["quote"]
            if _receipt_is_solid(bodies, p - 1, quote):
                continue
            others = [i + 1 for i, b in enumerate(bodies)
                      if i != p - 1 and receipt_ok(b, quote)]
            if others:
                retry_notes[p] = (
                    "Your previous quote for this page also appears on page(s) "
                    f"{', '.join(map(str, others))} -- it is not unique to this page. "
                    "Give a DIFFERENT verbatim quote of at least 6 words that appears "
                    "ONLY on this page. Avoid a shared template opener, a mandated "
                    "certification paragraph, or any wording another similar page "
                    "could also contain. The most reliable choices are a PERSONAL "
                    "NAME, an email address, an institution name, a dollar figure, "
                    "a date, or an ID/timestamp printed on this page and no other -- "
                    "look for one of those, in a full sentence or line of at least "
                    "6 words, before falling back to anything else.")
            else:
                retry_notes[p] = (
                    "Your previous quote for this page could not be verified on "
                    "this page's own text -- it may have been too short, "
                    "paraphrased, or copied from a different page. Give a "
                    "different verbatim quote of at least 6 words copied exactly "
                    "from this page's own content.")
            got.pop(p, None)

        missing = [p for p in range(1, n + 1) if p not in got]
        if not missing:
            break
        print(f"[PAGE-LEDGER] round {_round}: re-asking {len(missing)} page(s): {missing}")
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for part in pool.map(
                        lambda p: _safe_ask_window([p], page_texts, section_keys, known,
                                                    retry_notes=retry_notes,
                                                    temperature=_RETRY_TEMPERATURE), missing):
                    got.update(part or {})
        except Exception as exc:                   # noqa: BLE001
            print(f"[PAGE-LEDGER] re-ask round {_round} failed: {exc}")
            break
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
    _structure = {}
    for k, v in (structure or {}).items():
        try:
            _structure[int(k)] = v
        except (TypeError, ValueError):                # golden rule 3 (IMP-7):
            continue                                    # a bad key is dropped,
    structure = _structure                              # never a raise
    furniture = document_furniture(page_texts or [])
    bodies = [body_text(t, furniture) for t in (page_texts or [])]
    keys = list(sections or {})

    answers = {}
    if keys:
        try:
            # `walk_pages` documents "never raises", but this is the roll
            # call's own guarantee (golden rule 3), not a promise borrowed
            # from a callee -- a bug in the walk must not become a bug here.
            answers = walk_pages(page_texts, keys, furniture=furniture, known=structure) or {}
        except Exception as exc:                        # noqa: BLE001
            print(f"[PAGE-LEDGER] walk_pages raised unexpectedly: {exc}")
            answers = {}

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
        elif guess in sections and _receipt_is_solid(bodies, page - 1, quote):
            # THE LEDGER'S OWN VERIFICATION. `_receipt_is_solid` is where a
            # row is actually decided verified -- not `receipt_ok` alone,
            # which only proves the quote is ON this page, never that it is
            # NOT also on another. A quote receipted here is on this page
            # and this page only.
            row.update(section=guess, source="model", quote=quote, verified=True)
            # CONTIGUITY -- RECORDED, not refused (fix round 2). It used to
            # discard the page outright: "page 47 is not the Project
            # Summary" was a real risk when a receipt only proved the quote
            # was SOMEWHERE on the page, so a coincidental match reappearing
            # under a closed-out section was the model's best guess, not a
            # verified fact. `_receipt_is_solid` is a STRONGER guarantee --
            # unique to this page, document-wide -- so a reappearance is
            # real content, not noise. Measured on a real 56-page package:
            # NSF's own Supplementary Documents genuinely interleave one
            # collaboration letter between two institutional-support pages
            # (a PI concatenating individually-authored letters in whatever
            # order they arrived), and refusing it threw away a page that
            # was correctly read. `spans_from_ledger` already has a
            # mechanism built for exactly this shape -- a key's pages that
            # are not one contiguous run report the extra pages in
            # `dropped_pages` rather than silently losing them -- so
            # refusing here was fighting machinery this module already has.
            # Still worth a reviewer's eye, so it is flagged rather than
            # accepted silently.
            if guess in seen and (order and order[-1] != guess):
                row["out_of_order"] = True
        elif guess in sections and quote:
            row["refused"] = guess               # answered, receipt did not hold
                                                   # (on this page, or on this
                                                   # page alone -- either way,
                                                   # not solid enough to keep)

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

    Golden rule 3 -- this is called from `review_draft`, which must never
    raise. A non-dict row is skipped (there is no page number to report and
    no promise a caller ever wrote such a row); a dict row with no `page` is
    NOT skipped -- it names the exact failure this function exists to catch
    (a row we cannot even read the page number of), so it is reported as
    unaccounted (as `None`) rather than silently passed over.
    """
    unaccounted = [r.get("page") for r in (rows or [])
                   if isinstance(r, dict) and r.get("source") == "unassigned"]
    return (not unaccounted), unaccounted


def _page_offsets(page_texts: list) -> list:
    """(start, end) of each page in `"\\n".join(page_texts)`.

    Built on exactly the join `document_text._extract_pdf` uses, because
    `pdf_sections` computes its offsets on the same string and the two must not
    disagree about where a page begins.
    """
    offsets, cursor = [], 0
    for text in page_texts or []:
        offsets.append((cursor, cursor + len(text or "")))
        cursor += len(text or "") + 1               # the "\n" the join inserts
    return offsets


def spans_from_ledger(rows: list, page_texts: list, sections: dict) -> dict:
    """{section key: span} covering the pages the ledger assigned to it.

    A span runs from a key's FIRST assigned page to its LAST, ABSORBING any
    interior page that is `blank` or `unassigned` on the way -- but it STOPS
    before an interior page the ledger gave to a DIFFERENT section. Edges are
    excluded (a leading/trailing unassigned page never joins), interiors are
    absorbed (a stray blank/unassigned page never splits a section in two).

    Why absorb rather than stop at the first gap: `blank`/`unassigned` covers
    a full-page figure, a chart, a scanned page -- ordinary content INSIDE a
    real section. A Project Description running pages 6-20 with a figure on
    page 10 must not silently become "pages 6-9" -- that reports real content
    on pages 11-20 as `not_found`, the exact false accusation this whole
    feature exists to prevent. Losing a few unattributed words on an absorbed
    page costs far less than losing ten real pages.

    Why still stop at a DIFFERENT section: `document_text.extract_upload`
    keeps only `start`/`end` and RE-SLICES `text` from `text[start:end]`, a
    single contiguous cut -- so a span can never legitimately claim a page the
    ledger gave to someone else. Because nothing is skipped, the returned
    `text` is always exactly `joined[start:end]` -- the offsets can never
    disagree with the text they claim to address.

    A key can have pages ASSIGNED to it beyond where its own span stops --
    `[A, B, A]` gives A a span over its first run only, but a later page the
    ledger separately gave back to A is real and must not vanish silently.
    Those are reported in `dropped_pages` on the span rather than folded in
    (folding would reopen the exact single-contiguous-slice problem above).
    A caller that needs those pages' text has `page_texts` to read them
    directly by number.
    """
    if not rows or not page_texts:
        return {}
    joined = "\n".join(page_texts)
    offsets = _page_offsets(page_texts)
    page_section: dict = {}
    pages_of: dict = {}
    for row in rows:
        try:
            page = int(row["page"])
        except (TypeError, ValueError, KeyError):
            continue
        key = row.get("section")
        key = key if key in (sections or {}) else None
        page_section[page] = key
        if key:
            pages_of.setdefault(key, []).append(page)

    spans = {}
    for key, pages in pages_of.items():
        first, last_assigned = min(pages), max(pages)
        # Walk forward from the first assigned page, absorbing any interior
        # page with NO section of its own; stop the instant a page belongs to
        # someone else. Never walks past `last_assigned` -- there is no
        # assigned page beyond it pulling the span any further.
        end_page = first
        for p in range(first, last_assigned + 1):
            other = page_section.get(p)
            if other is not None and other != key:
                break
            end_page = p
        start = offsets[first - 1][0]
        end = offsets[end_page - 1][1]
        # Pages the ledger gave to this key but that the span above could not
        # reach without reopening the single-contiguous-slice problem -- see
        # the docstring. Reported, never silently dropped.
        dropped = sorted(p for p in pages if p > end_page)
        spans[key] = {
            "start": start, "end": end, "text": joined[start:end],
            "label": (sections.get(key) or {}).get("label") or key,
            # The heading a locate-stage span would carry. There is no marker
            # string here -- the page ledger IS the evidence -- so it names its
            # own provenance instead, and the modal can render it.
            "marker": f"pages {first}-{end_page}" if end_page > first else f"page {first}",
            "pages": end_page - first + 1,
            "dropped_pages": dropped,
        }
    return spans


def page_counts_from_ledger(rows: list) -> dict:
    """{section key: number of pages the ledger ASSIGNED to it}.

    ATTRIBUTION, NOT REACH -- DO NOT feed this to a page-limit rule. A span's
    absorbed blank/unassigned interior pages (`spans_from_ledger`) count
    against a section's real page total -- a full-page figure inside a
    15-page Project Description still counts as one of the 15 to NSF -- but
    they were never ASSIGNED to the section, so this function does not count
    them. A 16-page section with 2 absorbed blanks reports 14 here and would
    silently PASS a 15-page limit it actually violates.

    The number a page-limit rule wants is `span["pages"]` from
    `spans_from_ledger`'s output (the span's real reach, absorbed pages
    included) -- this function exists for the different question of how many
    pages the ledger could actually attribute to a section by name."""
    counts: dict = {}
    for row in rows or []:
        key = row.get("section")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


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
