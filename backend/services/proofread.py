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


def locate_quote(text: str, quote: str) -> Optional[dict]:
    """{"line", "context"} for `quote` inside `text`, or None if unplaceable.

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
                                    if line - 1 < len(source) else "")}
    return None


def proofread(text: str, *, use_ai: bool = True) -> list[dict]:
    """Mechanical language errors in `text`, each quoting the draft.

    Returns [] on every failure path — no model, a bad response, an unparseable
    row — because a proofreading pass is the least important thing on the screen
    and must never be the reason a review fails (golden rule 3).
    """
    text = (text or "").strip()
    if not text or not use_ai:
        return []

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

    rows: list[dict] = []
    seen: set[str] = set()
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
        key = " ".join(quote.lower().split())
        if key in seen:          # the same sentence reported twice
            continue
        seen.add(key)
        rows.append({
            "kind": kind if kind in _ALLOWED_KINDS else "word_choice",
            "label": _LABELS.get(kind, "Wording"),
            "detail": detail,
            "evidence": quote,
            # Deterministic, and ABSENT rather than wrong when the quote cannot
            # be placed. Never a gate: a row that passed the quote check must
            # not be dropped because the locator was less tolerant.
            "where": locate_quote(text, quote),
            # Marks the row as a model opinion wherever it is rendered, so it can
            # never be shown alongside deterministic rows without saying so.
            "source": "ai",
        })
    return rows
