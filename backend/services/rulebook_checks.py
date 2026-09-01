"""Deterministic checks for the rulebook baseline (golden rule 1).

SIGNATURE CONTRACT, identical to services/generic_checks.py:
  fn(ctx, req) -> (status, detail, evidence)
  ctx = {"text", "spans", "title", "budget", "profile", "pages"}
  req = the requirement row; parameters ride on req["check_args"]

FALSE POSITIVES ARE THE RISK HERE, not misses. A wrong "you left a URL in your
Project Description" costs the PI a hunt for something that is not there and
teaches them to ignore the tool. mechanical_checks shipped a case-insensitive
\\bTO\\s?DO\\b that matched "allow us to do much more work"; its own docstring
had warned about that exact class, and a user found it rather than a test. Every
guard below therefore has a named test.

PRESENCE IS NOT APPROVAL. `rb_headings` decides only that a heading LINE exists.
Whether what sits under it is substantive is a separate SEMANTIC row the model
judges against its own quote. Conflating the two is how an 85-word Project
Summary ended up under a green tick.
"""

from __future__ import annotations

import re
from typing import Optional

from services.generic_checks import WORDS_PER_PAGE
from services.solicitation_profile import heading_regex


def _span_text(ctx: dict, req: dict) -> Optional[str]:
    """The text of this row's section, or None if it was never located.

    None is NOT a failure — see `_unlocated`."""
    key = (req.get("check_args") or {}).get("section")
    span = (ctx.get("spans") or {}).get(key)
    return span["text"] if span else None


def _unlocated(what: str) -> tuple:
    """could_not_locate, and the wording matters.

    Excluded from draft_review._CREDIT, so it leaves the score's denominator.
    Saying "your Broader Impacts statement is missing" when we merely failed to
    find the section would send a PI rewriting something they already wrote."""
    return "could_not_locate", (
        f"{what} was not found in what you pasted, so this rule was not checked. "
        "If you did include it, give it a clear heading on its own line and re-run."
    ), ""


# ── headings on their own line ──────────────────────────────────────────────

def rb_headings(ctx: dict, req: dict) -> tuple:
    """Are the required headings present, each on a line of its own?

    Delegates the line test to solicitation_profile.heading_regex, which is
    already the shared primitive the locate stage segments a draft with — it
    allows numbering, bullets, markdown bold and a trailing colon, and requires
    nothing else on the line. That is LITERALLY NSF's criterion ("a heading must
    be on its own line with no other text on that line"), so this is not a
    stricter rule than the funder's."""
    text = _span_text(ctx, req)
    args = req.get("check_args") or {}
    wanted = list(args.get("headings") or [])
    if text is None:
        return _unlocated("That section")
    if not wanted:
        return "not_checked", "No headings were specified for this rule.", ""

    # Markdown bold survives a paste from Word or a Markdown editor and is a
    # heading by every human standard; strip it before the line test rather
    # than widening the shared regex, which the locate stage also depends on.
    # Computed once — it does not depend on which heading is being tested.
    probe = re.sub(r"^[ \t]*(\*\*|__)|(\*\*|__)[ \t]*$", "",
                   text, flags=re.MULTILINE)

    found, missing = [], []
    for h in wanted:
        (found if heading_regex(h).search(probe) else missing).append(h)

    if not missing:
        return "addressed", (
            "Found " + ", ".join(f"“{h}”" for h in found) +
            ", each on its own line."
        ), ", ".join(found)
    if found:
        return "partial", (
            "Found " + ", ".join(f"“{h}”" for h in found) + ". Missing " +
            ", ".join(f"“{h}”" for h in missing) +
            ". Each must be on a line of its own, with no other text on that line."
        ), ", ".join(found)
    return "not_found", (
        "None of " + ", ".join(f"“{h}”" for h in wanted) +
        " appears as a heading. Each must be on a line of its own, with no other "
        "text on that line — a sentence that mentions the words does not count."
    ), ""


# ── hyperlinks ──────────────────────────────────────────────────────────────

# An email address contains no scheme and no "www.", so the alternatives below
# cannot match one — but `\S+@\S+` is stripped first anyway, because a future
# edit widening this pattern would otherwise start flagging addresses silently.
_EMAIL_RE = re.compile(r"\S+@\S+")
_URL_RE = re.compile(
    r"(?:https?://\S+|www\.[A-Za-z0-9-]+\.\S+|\bdoi\.org/\S+)", re.IGNORECASE)


def rb_no_urls(ctx: dict, req: dict) -> tuple:
    text = _span_text(ctx, req)
    if text is None:
        return _unlocated("Your Project Description")
    hits = _URL_RE.findall(_EMAIL_RE.sub(" ", text))
    if not hits:
        return "clear", "No hyperlinks found in the Project Description.", ""
    shown = ", ".join(h.rstrip(".,;)") for h in hits[:3])
    more = f" and {len(hits) - 3} more" if len(hits) > 3 else ""
    return "flagged", (
        f"Found {len(hits)} hyperlink{'s' if len(hits) != 1 else ''}: {shown}{more}. "
        "Reviewers are not permitted to follow links, so anything behind one "
        "goes unread — put what matters in the text itself."
    ), shown


# ── quantifiable financial information ──────────────────────────────────────

# FIGURES, never the word "funds". NSF's own instruction for this section reads
# "for whom no funds are being requested", so a word-level match would flag the
# section for complying with the rule it is being checked against.
_MONEY_RE = re.compile(
    r"(?:\$\s?[\d,]+(?:\.\d{2})?(?:\s?[KkMm]\b)?"
    r"|\b[\d,]+(?:\.\d{2})?\s?(?:dollars|USD)\b)")


def rb_no_financials(ctx: dict, req: dict) -> tuple:
    text = _span_text(ctx, req)
    if text is None:
        return _unlocated("Your Facilities, Equipment and Other Resources section")
    hits = _MONEY_RE.findall(text)
    if not hits:
        return "clear", "No dollar figures found in this section.", ""
    shown = ", ".join(h.strip() for h in hits[:3])
    return "flagged", (
        f"Found {len(hits)} dollar figure{'s' if len(hits) != 1 else ''}: {shown}. "
        "Quantifiable financial information does not belong in this section — "
        "describe the resource, and put its cost in the budget."
    ), shown


# ── narrative, not a bare list ──────────────────────────────────────────────
#
# DECIDED BY CODE, and that is the whole point. As a model judgement this was
# the last unstable rule in Facilities: `addressed` 5 runs of 6 and `not_found`
# once on an unchanged 617-word section, because "is this narrative?" is a
# matter of taste and the draft is prose containing one table. Splitting cannot
# help -- it asks a single question; the question itself was the problem.
#
# NSF's concern is narrower than tone, and the rule's own `why` says so: "A bare
# equipment list does not tell a reviewer the project is feasible." So the
# checkable question is whether the section contains real sentences.
#
# MEASURED across a real awarded proposal before the threshold was chosen:
# Facilities 18 prose sentences, Project Summary 13, and the smallest genuinely
# narrative section (a 145-word Data Management Plan) 4 -- against 0 for a bare
# list, 0 for a table, and 0 for short bulleted items punctuated one by one.
# Real starts at 4; degenerate is 0. Three sits below everything real and above
# everything this rule exists to catch.
_PROSE_SENTENCE_RE = re.compile(r"[^.!?]{20,}?[.!?]")
_MIN_PROSE_WORDS = 8
_MIN_PROSE_SENTENCES = 3


def _prose_sentences(text: str) -> list[str]:
    """Runs ending in terminal punctuation and long enough to be a sentence.

    Requiring the terminator is what stops an unpunctuated list counting as one
    very long sentence; requiring the length is what stops "Computer lab." from
    counting as prose.
    """
    flat = " ".join((text or "").split())
    return [s for s in _PROSE_SENTENCE_RE.findall(flat)
            if len(s.split()) >= _MIN_PROSE_WORDS]


def rb_narrative(ctx: dict, req: dict) -> tuple:
    text = _span_text(ctx, req)
    if text is None:
        return _unlocated("Your Facilities, Equipment and Other Resources section")
    sentences = _prose_sentences(text)
    if len(sentences) >= _MIN_PROSE_SENTENCES:
        return "addressed", (
            f"Written in prose — {len(sentences)} full sentences. A table or list "
            "inside an otherwise narrative section is fine."
        ), sentences[0][:160]
    return "not_found", (
        "This reads as a list or table rather than a narrative. NSF asks for this "
        "section to be narrative in nature, because a bare inventory does not tell "
        "a reviewer the project is feasible — describe what the resources are and "
        "what they let you do."
    ), ""


# ── et al. ──────────────────────────────────────────────────────────────────

# The literal token with its period. "et alia" and the surname "Etal" must not
# match, which is why the period is required and \b closes the front.
_ET_AL_RE = re.compile(r"\bet\s+al\.", re.IGNORECASE)


def rb_et_al(ctx: dict, req: dict) -> tuple:
    text = _span_text(ctx, req)
    if text is None:
        return _unlocated("Your References Cited section")
    hits = _ET_AL_RE.findall(text)
    if not hits:
        return "clear", "No use of 'et al.' found.", ""
    return "flagged", (
        f"'et al.' appears {len(hits)} time{'s' if len(hits) != 1 else ''}. Full "
        "author lists are expected except for large consortia papers, so this is "
        "advisory — it is not counted against your completeness score."
    ), "et al."


# ── reference completeness ──────────────────────────────────────────────────

# NSF names six elements a reference must carry. The YEAR is the one that is
# checkable without the cited work in hand: four digits, present or absent.
# Author sequence needs the publication itself, so no rule here claims it.
_YEAR_RE = re.compile(r"\b(?:1[89]|20)\d{2}\b")

# A line that STARTS a reference, in the styles a PI actually submits:
#   [12] ...            bracketed number (IEEE, and the NSF sample)
#   12. ...             numbered list
#   Smith, J. ...       surname, initial (APA, Chicago)
#   A. Patel ...        initial, surname (IEEE author-first)
# Anything else continues the entry above it, which is what a wrapped title is.
_ENTRY_START_RE = re.compile(
    r"^(?:\[\d+\]|\d{1,3}\.\s|[A-Z][A-Za-z'\u2019-]+,\s*[A-Z]\.|[A-Z]\.\s*[A-Z][A-Za-z-]+)")


def _reference_entries(text: str) -> list:
    """Split a references section into entries, surviving wrapped lines.

    pdfplumber breaks a long entry across lines, so counting LINES reports a
    complete four-entry list as eight and half of them yearless. Entries are
    therefore started only by an explicit marker and every other line is glued
    to the one above.

    FALLBACK: a list whose entries are all organisation-authored ("National
    Science Foundation. ...") has no marker to find. When NOTHING in the section
    looks like an entry start, each non-empty line is one entry -- which is the
    right reading of exactly that list, and cannot fire while any marker exists.
    """
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return []
    if not any(_ENTRY_START_RE.match(l) for l in lines):
        return lines
    entries: list = []
    for line in lines:
        if _ENTRY_START_RE.match(line):
            entries.append(line)
        elif entries:
            entries[-1] += " " + line
        # Lines BEFORE the first marker are preamble -- the section heading the
        # span begins with, most often. Counting "References Cited" as an entry
        # reported "1 of 2 references have no year" about a single complete
        # reference; the existing suite caught it on the first full run.
    return entries


def rb_citation_year(ctx: dict, req: dict) -> tuple:
    text = _span_text(ctx, req)
    if text is None:
        return _unlocated("Your References Cited section")
    entries = _reference_entries(text)
    if not entries:
        return "not_found", (
            "No reference entries were found in this section, so the year of "
            "publication could not be checked on any of them."
        ), ""
    missing = [e for e in entries if not _YEAR_RE.search(e)]
    total = len(entries)
    if not missing:
        return "addressed", (
            f"All {total} reference{'s' if total != 1 else ''} carry a year of "
            "publication."
        ), entries[0][:160]
    if len(missing) == total:
        return "not_found", (
            f"None of the {total} reference{'s' if total != 1 else ''} carry a "
            "year of publication. NSF asks every entry to include one."
        ), missing[0][:160]
    return "partial", (
        f"{len(missing)} of {total} references have no year of publication. "
        "NSF asks every entry to include one so a reviewer can locate the work."
    ), missing[0][:160]


# ── page limits ─────────────────────────────────────────────────────────────

def rb_page_limit(ctx: dict, req: dict) -> tuple:
    """A real verdict from a real page count; an ESTIMATE otherwise, and an
    estimate is never a pass or a fail.

    Pages are a property of a formatted PDF. `ctx["pages"]` is populated only by
    the section-check upload path, where one file IS one section and pdfplumber's
    count is exact. From a paste there is no page count to have, and reporting a
    word-count estimate as a verdict is the same error class as the scraper
    treating an unreadable page as a deleted one."""
    args = req.get("check_args") or {}
    key, limit = args.get("section"), args.get("limit")
    text = _span_text(ctx, req)
    if text is None:
        return _unlocated("That section")

    real = (ctx.get("pages") or {}).get(key)
    if isinstance(real, int) and real > 0:
        if real <= limit:
            return "clear", f"{real} page{'s' if real != 1 else ''}, within the {limit}-page limit.", ""
        return "flagged", (
            f"{real} pages, over the {limit}-page limit. An over-length section "
            "is returned without review."
        ), ""

    words = len(text.split())
    pages = words / WORDS_PER_PAGE
    return "not_checked", (
        f"About {words:,} words ≈ {pages:.1f} pages — an estimate from word count, "
        f"against a {limit}-page limit. Page count depends on your formatting, so "
        "this is not a pass or a fail. Upload the PDF to have it checked properly."
    ), ""
# ── rules a paste cannot carry ──────────────────────────────────────────────

def rb_not_in_text(ctx: dict, req: dict) -> tuple:
    """A rule about something that is not a property of the text. Never a verdict.

    A one-inch margin, a 10-point font, a Research.gov cover-sheet field and a
    certification SciENcv inserts are all real NSF rules, and not one of them is
    visible in what a PI pastes. Judged against pasted text every one of them
    comes back `not_found` — against a fully COMPLIANT draft. That is
    presence-rendered-as-verdict, which this repo has now unshipped three times:
    the section map's green ticks, `could_not_locate` read as `not_found`, and
    the scraper treating an unreadable page as a deleted one.

    So the row keeps its place and its quote — the PI still learns the rule
    exists, which is the whole point of holding a rulebook — and reports
    `not_checked`, which is absent from `draft_review._CREDIT` and therefore
    leaves the score's denominator.

    `handled_by` is REQUIRED and is not decoration. "Not checked here" with no
    address reads as our omission, the same way "Not checked" did before it
    became "Not ours to check"; a PI who cannot be told where to look is worse
    off than one who was never shown the rule. A test asserts every such row
    names a real tool.
    """
    by = (req.get("check_args") or {}).get("handled_by") or ""
    what = (req.get("check_args") or {}).get("section") or "this"
    detail = (
        "This is not something the text of a draft can show, so it was not "
        f"checked here. It is decided by {by}." if by else
        "This is not something the text of a draft can show, so it was not checked here."
    )
    return "not_checked", detail, ""


CHECKS = {
    "rb_headings": rb_headings,
    "rb_no_urls": rb_no_urls,
    "rb_no_financials": rb_no_financials,
    "rb_citation_year": rb_citation_year,
    "rb_et_al": rb_et_al,
    "rb_narrative": rb_narrative,
    "rb_page_limit": rb_page_limit,
    "rb_not_in_text": rb_not_in_text,
}
