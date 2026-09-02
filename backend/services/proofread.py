"""AI proofreading: mechanical language errors, quoted, advisory, never scored.

WHY A MODEL HERE, WHEN EVERYTHING ELSE RESISTS ONE
---------------------------------------------------
`mechanical_checks` covers language slips that are decidable from the characters
alone, and that ceiling is low and was measured. A PI pasted a Project Summary
carrying three errors and got zero rows back:

    fourr                                   a one-off typo, on no list
    trains fourr undergraduates, and two    a spurious comma
    ...delivered to students...             ends in a period, so every rule passes

Three errors, three different causes, and no regex reaches all three. Catching a
typo that is not a known misspelling needs a dictionary; catching a comma needs a
parser. A model has both, and proofreading is the one job in this app where the
model is being asked about ENGLISH rather than about a funder's rules — a
question it is genuinely authoritative on, unlike "is this proposal good".

WHY IT IS FENCED OFF, IN FOUR WAYS
-----------------------------------
1. ITS OWN KEY, not `mistakes`. `mechanical_checks` is model-free by contract and
   the modal calls its rows "found by a rule, not a judgement". Model output in
   that list makes that sentence false.
2. NEVER SCORED. Same rule as `mistakes`: a comma splice is not incompleteness
   against a solicitation, and the completeness percentage is already over-read.
3. EVERY ROW QUOTES THE DRAFT, verified in code with the same `quote_in` the
   reviewer is held to (golden rule 2). A proofreader that invents the sentence
   it is correcting sends an author hunting through their own draft for text that
   was never there — worse than reporting nothing at all.
4. ERRORS ONLY, never style. The Drafting Coach was deleted by product decision
   and this must not quietly become it: the prompt forbids rewriting anyone's
   science, grading their prose, or offering tone and word-choice preferences.
   Same boundary `section_guidance` holds when it requires a concrete suggestion
   but bans writing the sentence for the author.

NOT DETERMINISTIC, and the UI must not imply otherwise. Two runs of one paste can
return different rows. That is acceptable precisely because these rows change no
number — the honest framing is "a second reader noticed these", not "your draft
contains exactly N errors".
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from services import gemini_client
from services import text_match as _tm
from services.text_match import quote_in

# Same model and region as both review paths. `gemini_client.DEFAULT_MODEL` is
# 2.5-flash, so a call that forgets `model=` silently downgrades with nothing
# going red, and 3.6-flash 404s outside `global` — the pair must travel together.
MODEL = os.getenv("EIR_REVIEW_MODEL", "gemini-3.6-flash")
MODEL_LOCATION = os.getenv("EIR_REVIEW_LOCATION", "global")

# Thinking is capped rather than disabled, matching `draft_review`: measured
# there, disabling it outright made the model OMIT rows rather than work faster.
THINKING_BUDGET = 512

_ALLOWED_KINDS = {"spelling", "grammar", "punctuation", "word_choice"}

# WHAT IS ACTUALLY SHOWN. `word_choice` is parsed (the prompt still asks for the
# four kinds -- see below for why the prompt is left alone) and then dropped.
#
# Measured 2026-08-31 over 18 live runs of one awarded Project Summary: BOTH
# false positives were this kind, and both were rewrites of correct prose --
# "use 'respectively' instead of the adjective 'respective'" over NSF's own
# "photons and electrons, for respective examples". `_SYSTEM` already forbids
# rewrites in words and the model does it anyway; CLAUDE.md records that
# tightening the prompt against this exact shape removed it (0 of 6) and cost
# TWO THIRDS of the recall on real errors (4 of 6 -> 1 of 6), and was reverted.
# So it is filtered here, in code, and the prompt is not touched.
#
# This is also what pays for the union in `proofread` below: a wider net catches
# more junk, and this removes the class the junk came from rather than trading
# it against recall. `language_slips` still catches confused-word pairs
# ("rather then") deterministically, so little real coverage is lost.
_REPORTED_KINDS = {"spelling", "grammar", "punctuation"}

# HOW MANY TIMES THE MODEL IS ASKED. The rules have had this since
# `draft_review.SEMANTIC_VOTES`; this pass had nothing, and was the more
# unstable half of a Section Check because of it.
PROOFREAD_VOTES = 5

# HOW MANY READERS MUST SEE AN ISSUE BEFORE IT IS SHOWN. One, and a threshold of
# two was TRIED AND REVERTED -- do not re-add it without re-measuring.
#
# The arithmetic said 2-of-5 would keep 98% of real errors and cut noise from 22%
# to 5%, from a measured per-call rate of 8/12 and 9/12. It assumed the five
# calls fail INDEPENDENTLY. They do not: a single call returns both real errors
# or neither, and that correlation carries across a whole run. Measured over 10
# real uploads with the threshold on: noise went to ZERO and the two genuine
# errors fell from 8/10 to 5/10. A proofreader that misses a real comma half the
# time is worse than one that occasionally shows an extra.
#
# The lesson worth keeping: votes here are NOT independent samples, so binomial
# reasoning about them is wrong. Measure the configuration, never the model of it.
PROOFREAD_MIN_VOTES = 1

_SYSTEM = """You are a proofreader checking one section of a research proposal
for MECHANICAL LANGUAGE ERRORS ONLY.

REPORT exactly these four kinds of error:
  spelling      — a misspelt word ("fourr", "recieve", "Chesepeake")
  grammar       — subject/verb disagreement, a wrong verb form, a broken sentence
  punctuation   — a missing or spurious comma, a run-on, a trailing "..." where a
                  sentence should end, a missing period. Two shapes the model
                  reliably skips unless named: a comma before "and" joining only
                  TWO items is spurious ("trains four undergraduates, and two
                  graduate students"), and an introductory clause needs one
                  ("Should both be awarded, the PI will reduce effort")
  word_choice   — a real word used where another is meant ("rather then",
                  "affect" for "effect", "principle" for "principal")

NEVER report any of these. They are not errors and reporting them makes this
tool something the author turns off:
  - style, tone, register, or word-choice PREFERENCE
  - sentence length, readability, "this could be clearer", "consider rephrasing"
  - anything about the science, the argument, or the quality of the writing
  - passive voice, first person, or any house-style opinion
  - a rewrite of the author's sentence

LEAVE TECHNICAL VOCABULARY ALONE. A research proposal is full of correct words
you may not recognise — zwitterionic, Donnan, estuarine, potentiostat, MTDC, PSU,
gene and reagent and instrument names, acronyms, and proper nouns. If you are not
certain a word is wrong, say nothing. A proofreader that "corrects" correct
science is switched off, and then no errors are found at all.

QUOTE VERBATIM. `quote` must be copied character for character from the text you
were given, long enough to locate (roughly 4 to 12 words) and containing the
error itself. A quote that is not in the text is discarded, so an approximate or
tidied-up quote loses the finding.

Return JSON only:
{"issues": [{"quote": "...", "kind": "spelling|grammar|punctuation|word_choice",
             "detail": "one short sentence naming the error and the fix"}]}

Return {"issues": []} when the text is clean. That is a normal and common answer
— do not invent an error to have something to say."""

_LABELS = {
    "spelling": "Spelling",
    "grammar": "Grammar",
    "punctuation": "Punctuation",
    "word_choice": "Wrong word",
}

# A section is short. Whole packages are not proofread by this entry point --
# see `draft_review.review_section`'s call site for why.
MAX_CHARS = 20_000



# ── WHERE, computed in code ────────────────────────────────────────────────
# A quote is evidence; it is not a location. A PI handed "Remove the spurious
# comma after 'and'" over a six-word fragment still has to hunt 523 words for
# it. The model is asked about ENGLISH, which it is authoritative on; where a
# string sits in a document is arithmetic, and asking a model for it would
# invite a confidently wrong line number.
#
# FAILS TO ABSENT, NEVER TO WRONG. A quote that cannot be placed gets no
# location. Sending an author to line 12 for something on line 40 is worse than
# sending them nowhere, because they will act on it.
def _normalized_with_lines(text: str) -> tuple[str, list[int]]:
    """Whitespace-collapsed lowercase text, plus the source line of each char.

    Mirrors `text_match.normalize` exactly — a locator looser or tighter than
    the gate that admitted the quote would place rows the gate rejected, or
    fail to place rows it accepted.
    """
    chars: list[str] = []
    lines: list[int] = []
    lineno, prev_space = 1, True
    for ch in text or "":
        if ch.isspace():
            if not prev_space and chars:
                chars.append(" ")
                lines.append(lineno)
                prev_space = True
            if ch == "\n":
                lineno += 1
        else:
            chars.append(ch.lower())
            lines.append(lineno)
            prev_space = False
    return "".join(chars), lines


def _dash_variants(chars: list[str], lines: list[int]) -> list[tuple[str, list[int]]]:
    """The same two readings `text_match._readings` produces, WITH line maps.

    Reading the text two ways is only half of locating a quote in it: joining
    `histor- ically` shortens the string, so an index found in that reading no
    longer points at the same character of the original. Both variants are
    therefore built alongside their own index->line arrays, in one pass, so a
    hit maps straight back to a real line.
    """
    joined_c: list[str] = []
    joined_l: list[int] = []
    kept_c: list[str] = []
    kept_l: list[int] = []
    i, n = 0, len(chars)
    while i < n:
        ch = chars[i]
        breaks = (ch in _tm._DASHES and 0 < i and i + 2 < n
                  and chars[i - 1].isalnum() and chars[i + 1] == " "
                  and chars[i + 2].isalnum())
        if breaks:
            kept_c.append(ch)
            kept_l.append(lines[i])
            i += 2          # drop the dash (joined) / the space (both)
            continue
        joined_c.append(ch)
        joined_l.append(lines[i])
        kept_c.append(ch)
        kept_l.append(lines[i])
        i += 1
    return [("".join(joined_c), joined_l), ("".join(kept_c), kept_l)]


def _heading_above(text: str, line: int, headings: Optional[list]) -> Optional[str]:
    """The nearest heading ABOVE `line`, from the ones the rule names.

    ONLY the named headings count. A short line is not a heading, and guessing
    would label a wrapped sentence fragment as a section -- so text sitting
    above every heading gets None rather than the first one below it. Same
    fail-to-absent posture as the line number: a heading the quote does not sit
    under sends an author to the wrong paragraph.
    """
    if not headings:
        return None
    wanted = {" ".join(h.lower().split()): h for h in headings}
    found = None
    for i, raw in enumerate((text or "").splitlines(), 1):
        if i > line:
            break
        key = " ".join(raw.strip().lower().split()).rstrip(":")
        if key in wanted:
            found = wanted[key]
    return found


def locate_quote(text: str, quote: str,
                 headings: Optional[list] = None) -> Optional[dict]:
    """{"line", "context", "heading"} for `quote`, or None if unplaceable.

    Reports the line the quote STARTS on: a quote written across a wrap is one
    the reader reads as a single phrase, and the first line is what they jump
    to.

    The dash readings are tried the same way `text_match.quote_in` tries them,
    because a locator that gave up wherever a typesetter split a word would
    miss exactly the drafts copied out of a PDF -- which is most of them.
    """
    q = _tm.normalize(quote)
    if not q or not (text or "").strip():
        return None
    hay, lines = _normalized_with_lines(text)
    for reading, line_map in [(hay, lines)] + _dash_variants(list(hay), lines):
        for qr in _tm._readings(q):
            idx = reading.find(qr)
            if idx < 0:
                # Anchor on the opening words, which survive any reading.
                head = " ".join(qr.split()[:4])
                idx = reading.find(head) if len(head) > 8 else -1
            if 0 <= idx < len(line_map):
                line = line_map[idx]
                source = (text or "").splitlines()
                return {"line": line,
                        "context": (source[line - 1].strip()
                                    if line - 1 < len(source) else ""),
                        "heading": _heading_above(text, line, headings)}
    return None


# WORDS THE TYPESETTER SPLIT, derived from the DRAFT rather than from the quote.
# Testing the quote for "dash + space" is not enough and that was measured: over
# 10 live uploads three artifacts still got through, because the model quotes the
# word both ways -- the draft holds "superal- gebra" and the model returned
# "Lie superal-gebra" with the gap closed. Only the text knows a split happened,
# so the damaged forms are collected from it once and matched against the quote.
_SPLIT_RE = re.compile(r"(\w+)[-\u2010\u2011\u2012\u2013\u2014](\s+)(\w+)")


def _split_words(text: str) -> frozenset:
    """Both readings of every word a line-end dash broke, lower-cased."""
    out = set()
    for m in _SPLIT_RE.finditer(text or ""):
        left, right = m.group(1).lower(), m.group(3).lower()
        out.add(f"{left}-{right}")        # the model's closed-up form
        out.add(f"{left}- {right}")       # the form actually in the draft
    return frozenset(out)


def _is_split_word_artifact(quote: str, split: frozenset) -> bool:
    q = " ".join((quote or "").lower().split())
    return any(form in q for form in split)


# A SUBMISSION-SYSTEM STAMP, recognised by SHAPE rather than by repetition.
# `mechanical_checks._running_furniture` needs a line to repeat three times,
# which is right for a whole package and useless for ONE SECTION: measured on a
# real one-page Project Summary, "Submitted/PI: ... /Proposal No: 2503008"
# appears exactly ONCE and was reported as a spelling error ("Ii"). These are
# markers a portal prints on the page, never prose an author wrote.
_STAMP_RE = re.compile(r"(proposal\s+no\.?\s*:|page\s+\d+\s+of\s+\d+|submitted/pi\s*:)",
                       re.I)


def _stamp_lines(text: str) -> frozenset:
    """Lines that a submission system printed, not lines the author wrote."""
    from services.mechanical_checks import _running_furniture
    lines = {" ".join(ln.lower().split())
             for ln in (text or "").split("\n") if _STAMP_RE.search(ln)}
    # Plus anything genuinely repeating, which is what catches a whole-package
    # header that carries no recognisable marker.
    lines |= {" ".join(ln.lower().split()) for ln in _running_furniture(text or "")}
    return frozenset(l for l in lines if l)


def _in_furniture(quote: str, furniture: frozenset) -> bool:
    """True when `quote` sits inside a line the submission system stamped."""
    if not furniture:
        return False
    q = " ".join((quote or "").lower().split())
    return bool(q) and any(q in line for line in furniture)


def _one_pass(text: str, furniture: frozenset = frozenset(),
              split: frozenset = frozenset()) -> list[dict]:
    """One model call -> validated candidates [{kind, detail, quote}].

    Everything that can reject a row happens here, so a vote contributes only
    rows that would have been shown on their own. Returns [] on every failure
    path (golden rule 3)."""
    data = gemini_client.generate_json(
        f"Proofread this text:\n\n{text[:MAX_CHARS]}",
        system_instruction=_SYSTEM,
        temperature=0.0,
        max_output_tokens=4096,
        thinking_budget=THINKING_BUDGET,
        model=MODEL,
        location=MODEL_LOCATION,
    )
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for raw in (data.get("issues") or []):
        if not isinstance(raw, dict):
            continue
        quote = (raw.get("quote") or "").strip()
        detail = (raw.get("detail") or "").strip()
        kind = (raw.get("kind") or "").strip().lower()
        if not quote or not detail:
            continue
        # GOLDEN RULE 2. An unverifiable quote is dropped, never softened into a
        # finding without evidence.
        if not quote_in(text, quote):   # (text, quote) -- not the reverse
            continue
        kind = kind if kind in _ALLOWED_KINDS else "word_choice"
        # An unknown kind was already bucketed as `word_choice`, so it leaves
        # with it. Conservative on purpose, the same direction as the gate above.
        if kind not in _REPORTED_KINDS:
            continue
        # OUR OWN EXTRACTION DAMAGE IS NOT THE AUTHOR'S SPELLING. Measured over
        # 10 real uploads of one awarded PDF: the two genuine errors were steady
        # at 8/10, and EVERY other row was a word the typesetter split at a line
        # end ("superal- gebra", "theo- retical", "commu- nities") or a
        # Research.gov header line -- each appearing in one run, swinging the
        # count 0..6. The words are spelled correctly in the author's document;
        # they cannot act on either, so both are noise by definition.
        if _tm.has_line_break_hyphen(quote) or _is_split_word_artifact(quote, split):
            continue
        if _in_furniture(quote, furniture):
            continue
        out.append({"kind": kind, "detail": detail, "quote": quote})
    return out


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _union(passes: list[list[dict]], text: str,
           min_votes: int = 1) -> list[dict]:
    """Every candidate any pass found, one row per underlying error.

    THE UNION IS NOT A MEDIAN, and the difference is the whole point. Measured
    over 18 live runs, a single call surfaced the two real errors in a section
    about HALF the time. A 2-of-3 threshold over a ~50% per-call rate recovers
    ~50% -- i.e. nothing; the union takes it to roughly 1 miss in 8. Precision is
    bought back by `_REPORTED_KINDS`, not by the threshold.

    Rows merge on KIND plus an OVERLAP of their spans in the draft. Containment
    is not enough and that was measured, not assumed: the live runs quoted one
    comma as "work; and, the PI extends", "; and, the PI extends" and "and, the
    PI extends their previous use" -- the first and last OVERLAP without either
    containing the other, so a containment test printed two rows for one error.
    Position is the thing they actually share.

    Requiring the KINDS to match is the guard that keeps two different faults in
    one sentence as two rows (its own test). Two rows of the SAME kind whose
    spans overlap are treated as one error, which is the intended reading of
    "the same place".

    The SHORTEST span represents the group: this module has already had to fix a
    row that quoted a whole 445-character paragraph instead of the part at fault.
    """
    flat = _norm(text)
    groups: list[dict] = []
    for rows in passes:
        for cand in rows:
            q = _norm(cand["quote"])
            i = flat.find(q)
            span = (i, i + len(q)) if i >= 0 else None
            for g in groups:
                if cand["kind"] != g["kind"]:
                    continue
                # An unplaceable quote (the tolerant gate accepted it, a plain
                # find does not) cannot be compared by position, so it falls
                # back to containment rather than merging on nothing.
                if span is None or g["span"] is None:
                    gq = _norm(g["rows"][0]["quote"])
                    if q in gq or gq in q:
                        g["rows"].append(cand)
                        break
                elif span[0] < g["span"][1] and g["span"][0] < span[1]:
                    g["rows"].append(cand)
                    g["span"] = (min(span[0], g["span"][0]),
                                 max(span[1], g["span"][1]))
                    break
            else:
                groups.append({"kind": cand["kind"], "span": span, "rows": [cand]})
    out = []
    for g in groups:
        # THE THRESHOLD. A group is one underlying error; `rows` is how many
        # readers reported it. One reader in five is noise, not a finding.
        if len(g["rows"]) < min_votes:
            continue
        best = min(g["rows"], key=lambda c: len(c["quote"]))
        out.append((g["span"][0] if g["span"] else len(flat), best))
    # Document order, so two runs that found the same set present it the same
    # way. An unplaceable quote sorts last rather than jumping around.
    out.sort(key=lambda t: t[0])
    return [b for _, b in out]


def proofread(text: str, *, use_ai: bool = True,
              headings: Optional[list] = None,
              votes: Optional[int] = None) -> list[dict]:
    """Mechanical language errors in `text`, each quoting the draft.

    Returns [] on every failure path -- no model, a bad response, an unparseable
    row -- because a proofreading pass is the least important thing on the screen
    and must never be the reason a review fails (golden rule 3).

    Asked `votes` times CONCURRENTLY, so three calls cost roughly one call's wall
    clock. A vote that raises is dropped rather than losing the round; only
    losing all of them returns nothing.
    """
    text = (text or "").strip()
    if not text or not use_ai:
        return []

    n = PROOFREAD_VOTES if votes is None else max(1, int(votes))

    furniture = _stamp_lines(text)
    split = _split_words(text)

    if n == 1:
        passes = [_one_pass(text, furniture, split)]
    else:
        passes = []
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(_one_pass, text, furniture, split)
                       for _ in range(n)]
            for fut in futures:
                try:
                    passes.append(fut.result())
                except Exception as exc:      # one vote lost, not the round
                    print(f"[PROOFREAD] a vote failed: {exc}")

    # A single vote can never clear a threshold of two, so votes=1 stays a plain
    # single call rather than silently reporting nothing at all.
    merged = _union(passes, text, PROOFREAD_MIN_VOTES if n > 1 else 1)
    rows = [{
        "kind": c["kind"],
        "label": _LABELS.get(c["kind"], "Wording"),
        "detail": c["detail"],
        "evidence": c["quote"],
        # Deterministic, and ABSENT rather than wrong when the quote cannot
        # be placed. Never a gate: a row that passed the quote check must
        # not be dropped because the locator was less tolerant.
        "where": locate_quote(text, c["quote"], headings),
        # Marks the row as a model opinion wherever it is rendered, so it can
        # never be shown alongside deterministic rows without saying so.
        "source": "ai",
    } for c in merged]
    return rows
