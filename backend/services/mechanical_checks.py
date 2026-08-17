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
_CAPTION_RE = re.compile(r"^[ \t]*(Figure|Fig\.|Table)\s+(\d+[A-Za-z]?)\s*[.:—-]",
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
            + _number_conflicts(text, budget))
    if whole_document:
        rows += _missing_references(text)
    return rows
