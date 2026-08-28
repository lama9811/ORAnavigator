"""Draft reviewer — completeness of a pasted proposal against ITS solicitation.

WHAT THIS IS (and is not)
-------------------------
A PI pastes their whole proposal into one box. This returns, per requirement in
the PROFILE it is handed, whether the draft addresses it — every positive claim
backed by a verbatim quote from the PI's own text.

The profile (services/solicitation_profile.py) is the only thing that makes this
funder-specific, and it is an ARGUMENT. There is no hardcoded-solicitation path
here any more: the same engine reviews an NSF proposal, an NIH R01, or anything
else a PI attaches, and it behaves identically because it cannot tell them apart.

It is a COMPLETENESS reviewer. The score it returns is "percent of THIS
solicitation's requirements this draft demonstrably addresses", computed in code
from the coverage counts. It is NOT a prediction of funding, and no part of it is
a go/no-go verdict (the deleted Fundability tool did that; see CLAUDE.md).

FOUR STAGES, in order:
  1. LOCATE       (model, verified) — find where each section begins
  2. DETERMINISTIC (code only)      — the yes/no rules; the model never sees them
  3. SEMANTIC     (model, verified) — coverage per requirement, must quote
  4. SCORE        (code only)       — arithmetic over stage 2+3

WHY STAGE 1 EXISTS AND WHY IT VERIFIES
--------------------------------------
The input is one undifferentiated blob, so "is the sustainability plan present?"
is unanswerable until we know which text is the Project Description. If the
model's section marker cannot be found verbatim in the paste, the section is
`could_not_locate` and its requirements are reported as UNLOCATED and dropped
from the score's denominator. Reporting "missing" for a section we simply failed
to find would be the same class of error as kb_scraper treating an unreadable
page as a deleted one (CLAUDE.md) — a confident, wrong, destructive claim.

GROUNDING (golden rule 2)
-------------------------
Every `addressed`/`partial` carries a verbatim quote checked with _quote_in (the
shared whitespace-collapsing test). Unverifiable -> demoted to `not_found`, quote
dropped. Reviewer notes are opinions, carry no quote, and never count as coverage.

FALLBACK (golden rule 3)
------------------------
Model unavailable -> stages 1 and 3 degrade to a deterministic keyword pass,
stage 2 runs unchanged, and the SCORE IS SUPPRESSED (`score: None`). A number
computed with the semantic half missing would read as "this draft is 30% done"
when it means "the AI was offline".
"""

from __future__ import annotations

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from services import delegated_rules
from services import draft_scope
from services import gemini_client
from services import mechanical_checks
from services import rulebook_baseline
from services import section_guidance
from services import solicitation_profile as sp
from services import proofread
# The shared whitespace-collapsing membership test (golden rule 2). Deliberately
# imported rather than re-implemented so every grounded feature uses ONE
# definition. Lived in section_coach._quote_in until the Drafting Coach was
# removed (2026-08-10); now has a feature-neutral home.
from services.text_match import quote_in as _quote_in

# gemini-3.6-flash is region-locked to the "global" endpoint — it 404s in
# us-central1. Both must move together, which is why they are one env pair.
MODEL = os.getenv("EIR_REVIEW_MODEL", "gemini-3.6-flash")
MODEL_LOCATION = os.getenv("EIR_REVIEW_LOCATION", "global")

# The most text handed to the model in one prompt.
#
# 120_000 WAS TOO SMALL, and the failure was silent. A real awarded NSF 23-598
# package (56 pages, Morgan State, 2026) extracts to 145,023 characters, so the
# last 25,023 never reached the locate stage. Measured on that document: at
# 120_000 the locate stage found 8 sections; given the whole text it found 10,
# and the two it gained were exactly the ones past the cut — Special
# Information/Supplementary Documents (heading at 131,720) and the Letters of
# Collaboration (136,314). Every requirement filed under them reported "Not
# located" and left the score's denominator, on a proposal NSF funded.
#
# 400_000 covers a package roughly three times that long. It is ~100k tokens
# against gemini-3.6-flash's context, so the model is not the constraint — and
# measured on the same document, handing it the WHOLE text was not slower:
# 3.4s for 145,023 chars against 16.5s for the truncated 120,000.
#
# It is still a cap, so `review_draft` REPORTS when it bites (`truncated`).
# The old comment called the cap generous and moved on; the number was wrong
# and nothing would have said so.
MAX_DRAFT_CHARS = 400_000

# Gemini thinking is CAPPED here, not disabled, and the number is measured.
# gemini-3.6-flash thinks by default and that costs ~9s a review. Turning it
# OFF is faster still and is the wrong trade for this caller: over paired runs
# the review went 15.4s -> 8.0s but `assessed` fell 38.7 -> 35.3, one run
# collapsing to 27. The reviewer OMITS rows when it cannot think; an omitted row
# becomes `unclear`, and `unclear` drops out of the score's denominator — so the
# speed is paid for in coverage, silently, which is exactly the failure this
# tool is built to avoid.
#
# 1024 buys the latency without the loss. Six runs: mean 8.0s and assessed=37
# EVERY time, versus ~17s / 37 on the default — faster than thinking-on AND
# more deterministic than thinking-off.
#
# NOT the same call as services/solicitation_requirements.py, which disables
# thinking outright: there it made recall BETTER, because that module spends a
# wall-clock budget it was wasting on thinking. Same model, opposite answer;
# measure per caller rather than copying the setting.
THINKING_BUDGET = 1024

# Most requirements sent to the reviewer in ONE call. The response is capped at
# max_output_tokens=8192, and when the model cannot fit every row it OMITS some
# rather than truncating visibly — an omitted row becomes `unclear`, and `unclear`
# is absent from _CREDIT, so it leaves the score's denominator with nothing on
# screen to say so. 15 keeps a batch comfortably inside the ceiling while leaving
# a 3-rule section a single round-trip.
REVIEW_BATCH = 15

# How many times Check a Section asks the reviewer the same question.
#
# Measured on a real awarded Project Summary, five identical uploads: four
# scored 100% and one 92%. Five of six rules were stable every run; the one that
# moved is a genuinely BORDERLINE call (an Overview naming objectives but not
# methods). Rules decided by code never move, and a clear-cut judgement does not
# either -- a weak 47-word summary was identical across all five runs.
#
# Temperature is already 0 and thinking is already capped, and CLAUDE.md records
# two measured attempts to fix this class by prompt tuning, one of which cost
# two thirds of the recall on real errors and was reverted. Asking more than
# once and taking the median is the lever that is left.
SEMANTIC_VOTES = 3

# Status ordering for the median. Two vocabularies that never mix -- a row is a
# prohibition or it is not -- so one table serves both.
_VOTE_RANK = {"not_found": 0, "flagged": 0, "partial": 1, "clear": 2, "addressed": 2}


def merge_votes(votes: list[list[dict]]) -> list[dict]:
    """One finding per requirement, taking the MEDIAN status across runs.

    The median is a single rule covering both cases: with a 2-1 majority it IS
    the majority, and with three different answers it is the middle one rather
    than whichever call happened to return first. With an even number of usable
    votes it takes the LOWER, so a disagreement never resolves upward into a
    claim the draft has not earned.

    Returns the winning run's row WHOLE. A note arguing `partial` printed under
    a status of `addressed` would be a new kind of wrong, so note, evidence and
    suggestion travel with the status they were written for.

    `unclear` means the reviewer returned nothing for that row -- it is not an
    opinion, so it does not get a vote. When every run failed it is all there is
    and it stands.
    """
    if len(votes) <= 1:
        return list(votes[0]) if votes else []
    order: list[str] = []
    seen: dict[str, list[dict]] = {}
    for run in votes:
        for f in run:
            rid = f.get("id")
            if rid not in seen:
                seen[rid] = []
                order.append(rid)
            seen[rid].append(f)
    out = []
    for rid in order:
        rows = seen[rid]
        ranked = sorted((r for r in rows if r.get("status") in _VOTE_RANK),
                        key=lambda r: _VOTE_RANK[r["status"]])
        out.append(ranked[(len(ranked) - 1) // 2] if ranked else rows[0])
    return out

_SCORE_BANDS = ((85, "green"), (60, "amber"))


# ── STAGE 1: LOCATE ─────────────────────────────────────────────────────────

_LOCATE_SYSTEM = (
    "You segment a pasted grant proposal into its named sections. You do NOT judge, "
    "summarise, or rewrite anything.\n"
    "RULES:\n"
    "1. For each section you find, return the VERBATIM first line of that section "
    "exactly as it appears in the text — usually its heading. Copy it character for "
    "character. Do not clean it up, renumber it, or expand abbreviations.\n"
    "2. Only report a section you actually found. Omit the rest. Never guess.\n"
    "3. A section heading may be numbered, upper-cased, or on its own line. Match the "
    "author's wording, not the canonical name.\n"
)


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split())


def _find_offset(text: str, marker: str) -> Optional[int]:
    """Character offset of `marker` in `text`, tolerant of line wrapping.

    Tries a plain case-insensitive find first, then a regex that allows any
    whitespace run between tokens — the same hard-wrap problem _quote_in solves
    for membership, except here we need the POSITION, which _quote_in cannot give
    (it compares normalized copies, whose indices don't map back)."""
    marker = _norm_ws(marker)
    if not marker:
        return None
    idx = text.lower().find(marker.lower())
    if idx >= 0:
        return idx
    pattern = r"\s+".join(re.escape(tok) for tok in marker.split())
    m = re.search(pattern, text, re.IGNORECASE)
    return m.start() if m else None


# A labelled SUB-section: a heading that legitimately sits INSIDE another
# section rather than beside it. {child_key: parent_key}.
#
# WHY THIS TABLE EXISTS — following our own advice used to break the check.
# The spans TILE, so every accepted marker cuts the section before it. NSF wants
# a "Broader Impacts" heading in the Project Summary AND a separately labelled
# one in the Project Description, and `_find_offset` resolves a marker to its
# FIRST occurrence in the whole document. On a package that did exactly what
# this tool asks, the summary's own third heading became a top-level boundary,
# truncated the Project Summary before it, and `pappg_ps_headings` reported
# "Missing Broader Impacts" about a summary that has it. The mirror is just as
# real: with no such heading in the summary, the boundary lands inside the
# Project Description and cuts its span off immediately before its own header,
# so `pappg_pd_impacts_header` reports not_found on a compliant one.
#
# A sub-section therefore never acts as a boundary, and it is resolved INSIDE
# its parent's span — so an identical heading earlier in the document cannot
# claim it. `_project_description_span` already encodes this same parent/child
# relationship for the opposite reason (folding the text back in); this is the
# locate half of it.
_SUBSECTIONS = {"broader_impacts": "project_description"}


def _parent_section(child_key: str, sections: dict) -> Optional[str]:
    """The parent of `child_key` in THIS section universe, or None.

    Matched on `section_signature`, never on the raw key, because the row
    sources spell one part of a proposal with different functions:
    `canon_section` SINGULARISES ("broader_impact") while `section_key` does not
    ("broader_impacts"), and this table is written in the second spelling. A
    stored profile is re-canonicalised on every load, so the first spelling is
    the one production actually builds -- the guard above was keyed on the one
    it never sees, and so never fired outside its own tests.

    Measured before this was fixed, on a package that did exactly what this tool
    asks: the Project Summary span came back 104 words instead of 155, cut at
    its own third heading, so `pappg_ps_headings` reported "Missing Broader
    Impacts" and `pappg_ps_impacts` reported "the draft lacks a dedicated
    Broader Impacts section" -- about a summary that has one.

    The PARENT is resolved through the universe for the same reason, and that
    also preserves the old behaviour of only treating a child as nested when its
    parent is a section this solicitation actually names."""
    sig = sp.section_signature(child_key)
    if not sig:
        return None
    for child, parent in _SUBSECTIONS.items():
        if sp.section_signature(child) == sig:
            return sp.resolve_section_key(sections, parent)
    return None


def _spans_from_markers(text: str, markers: dict, sections: dict) -> dict:
    """{section_key: first_line} -> {section_key: {"text","start","end","marker"}}.

    Top-level sections TILE: each runs from its own marker to the next in
    document order, so they cover the paste without overlapping. A SUB-section
    (see _SUBSECTIONS) is deliberately outside that tiling — it never truncates
    anything, and where its parent was located it is resolved within the
    parent's span so a same-named heading elsewhere cannot claim it."""
    offsets = {}
    for key, marker in markers.items():
        if key not in sections:
            continue                      # model invented a section name — ignore
        m = str(marker or "")
        off = _find_offset(text, m)
        if off is None:
            continue                      # marker not verifiable -> not located
        offsets[key] = (off, m)

    # A child is held out of the tiling whenever its parent is a section THIS
    # solicitation names — even if the parent turns out not to be located, since
    # the point is that it must not cut a sibling either way.
    subs = {k: v for k, v in offsets.items() if _parent_section(k, sections)}
    found = sorted((off, k, m) for k, (off, m) in offsets.items() if k not in subs)

    spans = {}
    for i, (start, key, marker) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(text)
        spans[key] = {"text": text[start:end].strip(), "start": start,
                      "end": end, "marker": marker}

    for key, (off, marker) in subs.items():
        parent = spans.get(_parent_section(key, sections))
        if parent is not None:
            rel = _find_offset(text[parent["start"]:parent["end"]], marker)
            if rel is None:
                # The heading exists, but not inside the parent — so it belongs
                # to some other section (a Project Summary's own Broader Impacts
                # line). Reporting it as this sub-section would point every one
                # of its requirements at the wrong text; unlocated is honest.
                continue
            start, end = parent["start"] + rel, parent["end"]
        else:
            start = off
            end = next((s for s, _, _ in found if s > start), len(text))
        spans[key] = {"text": text[start:end].strip(), "start": start,
                      "end": end, "marker": marker}
    return spans


def _locate_fallback(text: str, sections: dict) -> dict:
    """Deterministic segmentation: scan for heading lines matching known aliases.
    Used when the model is unavailable, and to fill sections the model missed."""
    markers = {}
    for key, meta in sections.items():
        best = None
        for alias in meta.get("aliases") or []:
            m = sp.heading_regex(alias).search(text)
            if m and (best is None or m.start() < best.start()):
                best = m
        if best is not None:
            markers[key] = best.group(0).strip()
    return markers


def locate_sections(text: str, sections: dict, *, use_ai: bool = True) -> tuple[dict, bool]:
    """Segment the paste. Returns (spans, ai_used).

    `sections` is the profile's section universe ({key: {"label","aliases"}}) —
    the reviewer only ever looks for sections THIS solicitation defines.

    The model proposes markers; code verifies each one is really in the text and
    only then accepts it. The deterministic scan fills any section the model did
    not report, so the two are additive rather than either/or."""
    text = text or ""
    sections = sections or {}
    markers: dict = {}
    ai_used = False
    if use_ai and text.strip():
        known = {k: v["label"] for k, v in sections.items()}
        prompt = (
            "Segment this grant proposal into sections.\n"
            f"SECTION KEYS you may use (use the key, not the label): {known}\n"
            "PROPOSAL TEXT:\n\"\"\"\n" + text[:MAX_DRAFT_CHARS] + "\n\"\"\"\n\n"
            'Return JSON: {"sections": {"<section_key>": "<verbatim first line of '
            'that section>", ...}}. Omit any section you did not find.'
        )
        ai = gemini_client.generate_json(
            prompt, temperature=0.0, max_output_tokens=2048, timeout_s=60,
            thinking_budget=THINKING_BUDGET,
            system_instruction=_LOCATE_SYSTEM, model=MODEL, location=MODEL_LOCATION,
        )
        if ai and isinstance(ai.get("sections"), dict):
            ai_used = True
            markers = {str(k): str(v) for k, v in ai["sections"].items() if v}

    for key, marker in _locate_fallback(text, sections).items():
        markers.setdefault(key, marker)

    return _spans_from_markers(text, markers, sections), ai_used


# ── STAGE 2: DETERMINISTIC CHECKS ───────────────────────────────────────────
# Every function here returns (status, detail, evidence). Statuses:
#   addressed / partial / not_found   — normal requirements
#   clear / flagged                   — prohibitions (flag_if_present)
#   not_checked                       — the input needed wasn't supplied
# `not_checked` and `could_not_locate` are excluded from the score denominator.

def run_deterministic(text: str, spans: dict, profile: dict, *,
                      title: Optional[str] = None,
                      budget: Optional[dict] = None,
                      pages: Optional[dict] = None) -> list[dict]:
    """Every code-decided requirement. No model involved, so these findings are
    identical whether or not Gemini is reachable (golden rule 1).

    A row names its check by string, resolved in three tiers: the profile's own
    callables, then the shared library in services/generic_checks.py, then the
    rulebook baseline's in services/rulebook_checks.py. A row whose check
    resolves to nothing is SKIPPED rather than guessed at — a fabricated verdict
    on a rule we cannot evaluate is worse than silence.

    `pages` maps a section key to its REAL page count, and is populated only by
    the section-check upload path where one file is one section. Absent it, a
    page rule reports an estimate and refuses to call it a pass or a fail."""
    rows = [r for r in profile.get("requirements", []) if r["kind"] == "deterministic"]
    if not rows:
        return []
    # Imported here, and only once a row actually needs it: both are separate
    # modules and this keeps the engine importable without them.
    from services import generic_checks
    from services import rulebook_checks
    ctx = {"text": text or "", "spans": spans or {}, "title": title,
           "budget": budget, "profile": profile, "pages": pages or {}}
    out = []
    for req in rows:
        name = req.get("check", "")
        fn = (profile.get("checks", {}).get(name)
              or generic_checks.CHECKS.get(name)
              or rulebook_checks.CHECKS.get(name))
        if fn is None:
            continue
        status, detail, evidence = fn(ctx, req)
        out.append(_finding(req, status, detail, evidence, source="check"))
    return out


# ── STAGE 3: SEMANTIC COVERAGE ──────────────────────────────────────────────

def _review_system(solicitation_id: str) -> str:
    """The reviewer's system prompt. A function, not a constant, because the one
    thing that varies is which solicitation it is judging against."""
    return (
        "You assess whether a draft proposal section addresses a FIXED list of "
        f"requirements from solicitation {solicitation_id}. You are an advisory "
        "reviewer, not an editor, and you never rewrite the author's prose.\n"
        "RULES:\n"
        "1. Judge ONLY the DRAFT TEXT given. Never credit content that is not there.\n"
        "2. You may ONLY report requirement ids from the REQUIREMENTS list. Never invent a "
        "requirement, never merge two, never drop one — return a row for every id given.\n"
        "3. For every 'addressed' or 'partial' you MUST supply 'evidence': a VERBATIM quote "
        "(<=200 chars) copied from the DRAFT TEXT. No quote means the status is 'not_found'. "
        "Do not paraphrase, do not tidy the quote, do not join distant sentences.\n"
        "4. The three statuses are about PRESENCE, not quality:\n"
        "   - 'not_found'  = the draft says NOTHING about this requirement. Absence only.\n"
        "   - 'partial'    = the draft addresses it, but thinly or incompletely.\n"
        "   - 'addressed'  = the draft addresses it substantively.\n"
        "   If ANY sentence in the draft speaks to the requirement, the floor is 'partial'. "
        "Never use 'not_found' to mean 'present but weak' — say that in the note instead. A "
        "sentence like 'four undergraduates per year will be trained' DOES address 'improves "
        "research opportunities for students'; it is 'partial' if thin, never 'not_found'.\n"
        "5. 'note' is one or two sentences of concrete, actionable coaching. Say WHAT to add. "
        "Never write the prose for the author.\n"
        "6. Be strict about the line between 'partial' and 'addressed': a vague gesture at a "
        "topic is 'partial'. Reviewers are strict, and a false 'addressed' costs this author "
        "an award.\n"
    )


_NOTES_SYSTEM = (
    "You are an experienced grant review panellist giving ADVISORY impressions of a draft "
    "proposal. "
    "Your notes are OPINIONS AND QUESTIONS a panel would raise — never claims that the "
    "draft does or does not contain something (coverage is assessed separately). Write one "
    "note per merit criterion, addressed to the author, specific to THIS draft."
)


def _semantic_fallback(reqs: list[dict], section_text: str) -> list[dict]:
    """Keyword presence per requirement when the model is unavailable.

    Deliberately never returns `not_found`: a keyword miss is weak evidence of
    absence, and this path has no quote to justify a hard claim. Everything
    unmatched is `unclear`, which is excluded from the score — consistent with
    suppressing the score entirely on this path."""
    low = (section_text or "").lower()
    out = []
    for req in reqs:
        hit = next((kw for kw in req.get("keywords", []) if kw in low), None)
        if hit:
            out.append(_finding(req, "unclear",
                                f'Found related wording ("{hit}"), but the AI reviewer is '
                                "offline so this was not properly assessed.", "",
                                source="fallback"))
        else:
            out.append(_finding(req, "unclear",
                                "Not assessed — the AI reviewer is offline. Check this one "
                                "by hand.", "", source="fallback"))
    return out


def _section_label(sections: dict, key: str) -> str:
    return (sections.get(key) or {}).get("label") or key


def _review_section(section_key: str, span: Optional[dict], reqs: list[dict],
                    sections: dict, solicitation_id: str,
                    votes: int = 1) -> list[dict]:
    """Coverage for one section's requirements, grounded and verified.

    BATCHED, and the batching is load-bearing. Every requirement for a section used
    to go into ONE prompt capped at max_output_tokens=8192. When the reviewer cannot
    finish, it OMITS rows; an omitted row becomes `unclear`; `unclear` is absent
    from _CREDIT, so it silently leaves the score's denominator and the PI is told
    nothing. See the module docstring — that is the failure this tool exists to
    prevent, and it was reachable from inside the tool itself.

    Four sections of hand-curated rules never came close to the ceiling. Reading
    the PAPPG changes that: its Project Description alone yields 22 rules and the
    Budget section is denser still.

    A batch whose model call fails falls back to `unclear` for ITS rows only —
    losing every assessment because one call timed out would be worse than the
    problem this solves."""
    if not reqs:
        return []
    label = _section_label(sections, section_key)
    if span is None:
        return [_finding(r, "could_not_locate",
                         f"Could not find the {label} in what you pasted, so this "
                         "requirement was not assessed. If you did include it, add a "
                         "clear heading and re-run.", "", source="locate")
                for r in reqs]

    section_text = span["text"]
    if len(reqs) > REVIEW_BATCH:
        out: list[dict] = []
        for i in range(0, len(reqs), REVIEW_BATCH):
            out.extend(_voted_batch(section_key, span, reqs[i:i + REVIEW_BATCH],
                                    sections, solicitation_id, votes))
        return out
    return _voted_batch(section_key, span, reqs, sections, solicitation_id, votes)


def _voted_batch(section_key: str, span: dict, reqs: list[dict], sections: dict,
                 solicitation_id: str, votes: int) -> list[dict]:
    """`_review_batch` asked `votes` times, merged by median.

    Concurrent, so three votes cost roughly one call's wall clock rather than
    three. A vote that raises is dropped rather than losing the round: two
    usable answers still decide, and only losing ALL of them falls back.
    """
    if votes <= 1:
        return _review_batch(section_key, span, reqs, sections, solicitation_id)
    results = []
    with ThreadPoolExecutor(max_workers=votes) as pool:
        futures = [pool.submit(_review_batch, section_key, span, reqs,
                               sections, solicitation_id) for _ in range(votes)]
        for fut in futures:
            try:
                results.append(fut.result())
            except Exception as exc:            # one vote lost, not the round
                print(f"[REVIEW] a vote failed: {exc}")
    if not results:
        return _semantic_fallback(reqs, span["text"])
    return merge_votes(results)


def _review_batch(section_key: str, span: dict, reqs: list[dict],
                  sections: dict, solicitation_id: str) -> list[dict]:
    """One model call, for at most REVIEW_BATCH requirements."""
    label = _section_label(sections, section_key)
    section_text = span["text"]
    # `prohibition` is not decoration. ABSENCE MEANS PASS for these rows and FAIL
    # for every other row, and the reviewer had only the second vocabulary — so a
    # Budget Justification that never mentions alcohol was told to fix "Do not
    # request NSF funds for alcoholic beverages". Nine of twenty-three items in
    # one live fix-list were rules the draft already obeyed. The engine already
    # had `clear`/`flagged` (both in _CREDIT, both used by the deterministic
    # rows); they were simply unreachable from a model-judged row.
    listing = [{"id": r["id"], "requirement": r["label"],
                "solicitation_says": r["source"],
                "prohibition": bool(r.get("flag_if_present"))} for r in reqs]
    prompt = (
        f"SECTION: {label} of a proposal to solicitation {solicitation_id}.\n"
        f"REQUIREMENTS (return a row for EVERY id):\n{listing}\n\n"
        "DRAFT TEXT:\n\"\"\"\n" + section_text[:MAX_DRAFT_CHARS] + "\n\"\"\"\n\n"
        'Return JSON: {"findings": [{"id": "<requirement id>", '
        '"status": "addressed|partial|not_found", "note": "<1-2 sentences, actionable>", '
        '"evidence": "<verbatim quote from DRAFT TEXT, or empty>", '
        '"suggestion": "<ONE concrete thing that would strengthen this row>"}]}\n\n'
        'A requirement marked "prohibition": true is a rule about what the draft '
        'must NOT do. For those rows ONLY, use "clear" when the draft does not do '
        'the forbidden thing, and "flagged" when it does — never '
        '"addressed"/"partial"/"not_found". A draft that simply never mentions the '
        'forbidden thing is "clear": that is compliance, not an omission. Give '
        '"evidence" for "flagged" only — quote the offending text verbatim; "clear" '
        'takes no quote, because there is nothing to point at.\n\n'
        '"suggestion" is REQUIRED on EVERY row, including rows you mark "addressed" '
        'or "clear". These requirements are about PRESENCE, so "addressed" means the '
        'rule is satisfied — it does not mean the section is strong, and an author '
        'reading "addressed" with no suggestion concludes there is nothing left to '
        'do. Name the single most useful thing to add or sharpen, in one sentence, '
        'specific to THIS text. Do not praise the draft, do not restate the status, '
        'and do not write the sentence for the author.'
    )
    ai = gemini_client.generate_json(
        prompt, temperature=0.0, max_output_tokens=8192, timeout_s=90,
        thinking_budget=THINKING_BUDGET,
        system_instruction=_review_system(solicitation_id),
        model=MODEL, location=MODEL_LOCATION,
    )
    if not ai or not isinstance(ai.get("findings"), list):
        return _semantic_fallback(reqs, section_text)

    by_id = {str(f.get("id")): f for f in ai["findings"] if isinstance(f, dict)}
    out = []
    for req in reqs:
        raw = by_id.get(req["id"])
        if raw is None:
            # The model skipped a row despite rule 2. Report honestly as unassessed
            # rather than inferring absence from the model's own omission.
            out.append(_finding(req, "unclear",
                                "The reviewer did not return a result for this requirement. "
                                "Check it by hand.", "", source="ai"))
            continue
        prohibition = bool(req.get("flag_if_present"))
        status = str(raw.get("status", "")).strip().lower()
        evidence = str(raw.get("evidence", "") or "").strip()
        note = str(raw.get("note", "") or "").strip()
        suggestion = str(raw.get("suggestion", "") or "").strip()

        if prohibition:
            # A prohibition has exactly two outcomes. Anything else the model
            # returns falls back to `clear`, NOT to `flagged`: the draft is
            # presumed to comply until something in it can be quoted to the
            # contrary, which is the same burden of proof `addressed` carries in
            # the other direction.
            if status not in ("clear", "flagged"):
                status = "flagged" if status in ("not_found",) and evidence else "clear"
            # GOLDEN RULE 2, in the direction that matters here: "flagged" is a
            # POSITIVE claim about the draft — you did the forbidden thing — so
            # it needs a verifiable quote exactly as "addressed" does. An
            # unquotable flag sends a PI hunting for text that is not there.
            # "clear" is an ABSENCE claim and cannot be quoted, so it is exempt,
            # the same exemption budget_cap_status gets from _VERIFIABLE_FIELDS.
            if status == "flagged" and not _quote_in(section_text, evidence):
                status, evidence = "clear", ""
                note = (note + " ").strip() + (
                    " (The reviewer could not point to the offending text in your draft, "
                    "so this is reported as respected.)")
        else:
            if status not in ("addressed", "partial", "not_found"):
                status = "not_found"
            # GOLDEN RULE 2: a positive claim without a verifiable quote is dropped.
            if status in ("addressed", "partial") and not _quote_in(section_text, evidence):
                status, evidence = "not_found", ""
                note = (note + " ").strip() + (
                    " (A supporting quote could not be verified in your text, so this is reported "
                    "as not found.)")
        out.append(_finding(req, status, note, evidence, source="ai",
                            suggestion=suggestion))
    return out


def _reviewer_notes(spans: dict, profile: dict) -> list[dict]:
    """Advisory panel impressions, one per merit criterion the profile names.
    Opinions only — no quote required, never counted as coverage — coverage stays
    in the grounded findings above (golden rule 2)."""
    merit = profile.get("merit_criteria") or []
    if not merit:
        return []
    span = _project_description_span(spans, profile.get("sections") or {})
    if not span:
        return []
    criteria = "; ".join(f"{c['criterion']} ({c['asks']})" for c in merit)
    prompt = (
        f"MERIT CRITERIA: {criteria}\n"
        "DRAFT PROJECT DESCRIPTION:\n\"\"\"\n" + span["text"][:MAX_DRAFT_CHARS] + "\n\"\"\"\n\n"
        'Return JSON: {"reviewer_notes": [{"criterion": "<exact criterion name>", '
        '"note": "<how a panel would judge THIS draft on that criterion, and what would '
        'strengthen it>"}]}'
    )
    ai = gemini_client.generate_json(
        prompt, temperature=0.3, max_output_tokens=2048, timeout_s=60,
        thinking_budget=THINKING_BUDGET,
        system_instruction=_NOTES_SYSTEM, model=MODEL, location=MODEL_LOCATION,
    )
    if not ai or not isinstance(ai.get("reviewer_notes"), list):
        return []
    valid = {c["criterion"] for c in merit}
    return [
        {"criterion": str(n.get("criterion", "")).strip(),
         "note": str(n.get("note", "")).strip()}
        for n in ai["reviewer_notes"]
        if isinstance(n, dict)
        and str(n.get("criterion", "")).strip() in valid
        and str(n.get("note", "")).strip()
    ]


# ── STAGE 4: SCORE ──────────────────────────────────────────────────────────

# Credit per status. Absent from this map => excluded from the denominator
# entirely (could_not_locate, not_checked, unclear) — the honest treatment of
# "we did not assess this", as opposed to "we assessed it and it is missing".
def _source_rank(finding: dict) -> int:
    """0 for the solicitation's own rule, 1 for the rulebook's.

    The solicitation LEADS and the rulebook is the floor, so the PI meets their
    own funder's asks first. `build_generic` already appends baseline rows last
    for this reason; `review_section` looks the rulebook up directly and had the
    opposite order, so a Project Summary showed five PAPPG rows above the one
    rule NSF 23-598 actually wrote for it.

    Applied at BOTH sort sites rather than fixing the one that was wrong: two
    entry points that disagree about the order of one section is the confusion
    `review_section` exists to remove.

    ORDER ONLY -- no row is added, dropped or restatused, so no score moves.
    Provenance still reaches the reader through the per-row tag and
    `score.by_source`; it is deliberately NOT a heading, because the three
    provenance headings were removed on 2026-08-26 after a PI read them as
    "these are checked differently" when every section is checked against both.
    """
    return 1 if finding.get("rulebook") else 0


_CREDIT = {
    "addressed": 1.0,
    "partial": 0.5,
    "not_found": 0.0,
    "clear": 1.0,      # prohibition respected
    "flagged": 0.0,    # prohibition violated
}


def score(findings: list[dict], *, solicitation_id: str = "",
          scope: Optional[str] = None) -> Optional[dict]:
    """Completeness against `solicitation_id`, computed in code (golden rule 1).

    Deliberately NOT a funding prediction and deliberately not model-assigned.
    Returns None when nothing scoreable was assessed.

    `by_source` splits the same arithmetic by the authority each rule came from,
    because a section is judged against TWO at once — NSF's standing rulebook and
    this program's own asks — and one number cannot say which half is failing. A
    draft meeting every PAPPG rule and missing every solicitation rule reads the
    same 50% as one that did the reverse, and the second is far likelier to come
    back without review. Same rows, same credit, same denominator rules: this
    partitions the score, it does not recompute it.
    """
    scored = [f for f in findings if f.get("scored") and f["status"] in _CREDIT]
    if not scored:
        return None
    earned = sum(_CREDIT[f["status"]] for f in scored)
    # Half-UP, not Python's default banker's rounding: round() would send 62.5 to
    # 62 while sending 87.5 to 88, which looks arbitrary on a number a PI reads.
    pct = int(math.floor(100.0 * earned / len(scored) + 0.5))
    band = next((name for floor, name in _SCORE_BANDS if pct >= floor), "red")
    counts: dict = {}
    for f in findings:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    by_source: dict = {}
    for f in scored:
        src = f.get("rulebook") or solicitation_id or "this solicitation"
        b = by_source.setdefault(src, {"percent": 0, "assessed": 0, "earned": 0.0})
        b["assessed"] += 1
        b["earned"] += _CREDIT[f["status"]]
    for b in by_source.values():
        b["percent"] = int(math.floor(100.0 * b["earned"] / b["assessed"] + 0.5))
        b["earned"] = round(b["earned"], 1)
    if scope is None:
        # DERIVED, never asserted. `review_section` used to pass a fixed
        # "the PAPPG's rules for this section", so a Letter of Intent — a
        # section the rulebook holds no rules for at all — told the PI its
        # score measured completeness against NSF's standing policy when every
        # rule counted came from their own solicitation. The authorities that
        # actually contributed are already computed one line above.
        names = list(by_source)
        scope = (" and ".join(names) if len(names) < 3
                 else ", ".join(names[:-1]) + " and " + names[-1]) or "the solicitation"
    return {
        "percent": pct,
        "band": band,
        "assessed": len(scored),
        "earned": round(earned, 1),
        "counts": counts,
        "by_source": by_source,
        # Shown verbatim next to the number so it cannot be read as a funding odds.
        # The id qualifies the COUNT, so it may only appear when every counted
        # rule really did come from that document. With a rulebook also
        # contributing rows it would read "the 7 NSF 23-598 requirements" over a
        # set that is six sevenths NSF's standing policy — attributing rules to a
        # solicitation that never stated them. `by_source` carries the breakdown;
        # this sentence stays true instead of trying to carry it too.
        #
        # The guard is `by_source` naming THAT SOLICITATION AND NOTHING ELSE,
        # not `len(by_source) == 1`. The looser form let the mirror bug through
        # and it shipped: a Project Summary whose only scored rule was the
        # rulebook's own deterministic heading check (every semantic row
        # `unclear`) reported "100% of the 1 NSF 23-598 requirements" about a
        # rule NSF 23-598 never wrote. One entry is not the same fact as the
        # right entry.
        "basis": (f"{pct}% of the {len(scored)} "
                  + (f"{solicitation_id} "
                     if solicitation_id and list(by_source) == [solicitation_id]
                     else "")
                  + "requirements this reviewer could assess are addressed in your draft. "
                  # THE PRESENCE CAVEAT, and it travels with the number into both
                  # modals because it is the guardrail, not decoration. A PI asked
                  # "why would you rate it 100" of a 152-word Project Summary that
                  # met all seven of its rules — every one a presence check. The
                  # arithmetic was right; the screen read as a grade. Saying it
                  # here means neither modal can render the number without it.
                  "These rules check that something is present, not how strong "
                  f"it is. This measures completeness against {scope}, not the "
                  "quality of the writing or the likelihood of an award."),
    }


# ── VERDICT ─────────────────────────────────────────────────────────────────
# TWO COUNTS, ONE READING. Measured on a running backend 2026-08-26: a Project
# Summary carrying a doubled word, two misspellings, a wrong word, a sentence
# with no terminal punctuation and ten more wording problems reported
# **5 of 5 rules met, 100%**. Every one of the fifteen had been found —
# `mechanical_checks`, `language_slips` and the model proofreader all ran — and
# every one was outside the score, on the reasoning that a typo is not
# incompleteness against a solicitation. The reasoning holds; the screen it
# produced does not. A PI reading 100% concludes the section is done.
#
# NOTHING IS BLENDED, and that is the design rather than a shortcut. An error is
# verifiable — a doubled word is in the text or it is not. A weight is an
# opinion: folding fifteen errors and five rules into one percentage means
# deciding a typo is worth some fraction of a missing Broader Impacts statement,
# and no fraction of that is defensible. So both counts stay whole and only the
# VERDICT reads both — which is the one thing the old headline could not do,
# because it only ever knew one of them.
_ISSUE_MINOR_MAX = 5

# Statuses meaning the rule is SATISFIED. Derived from _CREDIT rather than typed
# again, so a new full-credit status cannot appear in one and not the other.
_PASSING = {s for s, credit in _CREDIT.items() if credit >= 1.0}


def verdict(score_block: Optional[dict], *, mistakes: list,
            wording: list, findings: Optional[list] = None) -> dict:
    """A reading of BOTH what the rules found and what the proofreaders found.

    Deterministic and model-free, like `score()` itself (golden rule 1). The
    model's contribution is already inside the two lists; this only counts them.

    Levels, in the order they are decided:
      needs_work  a scored rule is unmet, OR more than five errors. A section
                  missing its Broader Impacts statement is not "minor" because
                  the spelling is perfect — the rules are the floor and errors
                  sit on top of it, never instead of it.
      minor       every rule met, one to five errors.
      clean       every rule met, no errors found. UNREACHABLE without a score,
                  because claiming a clean bill of health for a rules check that
                  never ran is the same lie as a completeness percentage
                  computed with the semantic half missing.

    The wording is deliberately the weakest true statement available. "No
    problems found" is what was measured; "ready to submit" is not ours to say,
    and this repo has rendered presence as approval four times already.
    """
    counts = {"mistakes": len(mistakes or []), "wording": len(wording or []),
              "total": len(mistakes or []) + len(wording or [])}
    total = counts["total"]
    assessed = (score_block or {}).get("assessed") or 0
    earned = (score_block or {}).get("earned")
    rules_met = bool(score_block) and (score_block.get("percent") == 100)
    fraction = (f"{_trim(earned)} of {assessed}" if score_block else "")

    if not score_block:
        level = "minor" if 0 < total <= _ISSUE_MINOR_MAX else (
            "needs_work" if total else "unknown")
        # WHY THIS BRANCH READS THE FINDINGS. Withholding the number is correct
        # when no scored rule could be assessed -- but "the rules were not
        # checked" was printed even when a rule HAD been checked and passed,
        # directly above an open "Addressed" group saying so. References Cited
        # is where it shows: two rules, and only one of them scoreable, so a
        # single unassessed rule empties the denominator while the advisory
        # `et al.` check sits there having passed.
        rows = list(findings or [])
        scoreable = [f for f in rows if f.get("scored")]
        advisory_passed = [f for f in rows
                           if not f.get("scored") and f.get("status") in _PASSING]
        if not rows:
            head = "The rules were not checked for this section"
        elif scoreable:
            # Transient: a scored rule exists and did not come back.
            head = ("No scored rule could be assessed this time — running the "
                    "check again usually fixes it")
        else:
            # Permanent: nothing here can produce a score, so do not send the
            # author round a loop that cannot end.
            head = "This section has no scored rules"
        if advisory_passed:
            n = len(advisory_passed)
            head += (f", though {n} advisory rule{'' if n == 1 else 's'} "
                     f"{'was' if n == 1 else 'were'} checked and passed")
        head += (", and the writing has " + _plural(total) + "."
                 if total else ", and no writing problems were found.")
        return {"level": level, "label": _LEVEL_LABELS[level],
                "issues": counts, "summary": head}

    if not rules_met or total > _ISSUE_MINOR_MAX:
        level = "needs_work"
    elif total:
        level = "minor"
    else:
        level = "clean"

    if rules_met and total:
        summary = (f"Every rule was met ({fraction}), but the writing has "
                   f"{_plural(total)}. Fix those before you submit.")
    elif rules_met:
        summary = f"Every rule was met ({fraction}) and no writing problems were found."
    elif total:
        summary = (f"{fraction} rules met, and the writing has "
                   f"{_plural(total)}.")
    else:
        summary = f"{fraction} rules met. No writing problems were found."
    return {"level": level, "label": _LEVEL_LABELS[level],
            "issues": counts, "summary": summary}


_LEVEL_LABELS = {"clean": "No problems found", "minor": "Minor issues",
                 "needs_work": "Needs work", "unknown": "Not checked"}


def _plural(n: int) -> str:
    return f"{n} problem" if n == 1 else f"{n} problems"


def _trim(value) -> str:
    """1.0 -> "1", 3.5 -> "3.5". The fraction is read, not computed with."""
    if value is None:
        return "0"
    return str(int(value)) if float(value).is_integer() else str(value)


# ── ASSEMBLY ────────────────────────────────────────────────────────────────

def _project_description_span(spans: dict, sections: dict) -> Optional[dict]:
    """The Project Description span, WITH the Broader Impacts text folded back in.

    Both keys are conventions, not guarantees — a solicitation that names neither
    is simply unaffected, because the lookups below miss and this returns None or
    the bare Project Description.

    Broader Impacts, where a solicitation has one, is a labeled sub-section INSIDE
    the Project Description, not a sibling of it — but the locate stage has to
    treat its heading as a boundary in order to prove the heading exists (the
    "separately labeled" requirement). That split has a side effect: Project
    Description requirements would be judged against text with the Broader Impacts
    paragraphs cut out.

    Measured, before this existed: a draft whose Broader Impacts read "Four
    undergraduates per year will be trained in materials characterization" was
    told it had not addressed "improves research opportunities for students" —
    the exact sentence that addresses it, invisible because of where we cut.

    Concatenating is safe for grounding: evidence is verified against whatever
    text we pass, so a quote from either part still verifies against the real
    draft, and neither part is fabricated."""
    pd = spans.get("project_description") if "project_description" in sections else None
    bi = spans.get("broader_impacts") if "broader_impacts" in sections else None
    if pd is None:
        return None
    if bi is None:
        return pd
    # Since _SUBSECTIONS made Broader Impacts a NESTED span, its text is already
    # part of the Project Description's — concatenating would repeat it, and a
    # paragraph appearing twice is a mistake `mechanical_checks` reports. The
    # concatenation below still matters for the case that is not nested: a
    # Broader Impacts heading located when the Project Description was not, or a
    # solicitation whose Broader Impacts really is a sibling section.
    if pd["start"] <= bi["start"] < pd["end"]:
        return pd
    return {**pd, "text": pd["text"] + "\n\n" + bi["text"]}


def _finding(req: dict, status: str, note: str, evidence: str, *, source: str,
             suggestion: str = "") -> dict:
    return {
        "id": req["id"],
        "label": req["label"],
        "section": req.get("section"),
        "kind": req["kind"],
        "scored": bool(req.get("scored")),
        "prohibition": bool(req.get("flag_if_present")),
        "status": status,
        "note": note,
        # ONE concrete thing that would strengthen this row — carried on EVERY
        # row, including the ones that pass. A PI pasted a 76-word Project
        # Summary, was told six of eight rules were "Addressed", and every
        # passing row's note was praise ("The draft clearly states the
        # overarching objective"), which tells an author nothing and makes
        # "Addressed" read as "you are done here". Empty when the model omits it:
        # a fabricated suggestion is advice about a draft nobody read.
        "suggestion": suggestion,
        "evidence": evidence,
        "solicitation_says": req["source"],
        "why": req.get("why", ""),
        "source": source,   # check | ai | fallback | locate — for debugging/UI
        # A baseline row (services/rulebook_baseline.py) carries the name of the
        # rulebook it WAS drawn from — e.g. "the PAPPG" — never set by a model.
        # apply_delegation()'s guard reads THIS key to recognise a baseline row;
        # without propagating it here, every finding in the real pipeline would
        # arrive with no `rulebook` key regardless of what the requirement row
        # carried, and the guard would never fire. Ordinary rows carry None.
        "rulebook": req.get("rulebook"),
        # Set by apply_delegation() below, never by a model.
        "delegated_to": None,
    }


def apply_draft_scope(findings: list[dict]) -> list[dict]:
    """Stop grading a DOCUMENT on things only a submission can satisfy.

    Measured on a real proposal: 7 of 29 assessed requirements were portal
    clicks, who-signs-the-submission rules, or limits on how many proposals a PI
    may have. All scored `not_found`, all counted against the draft, all in "Fix
    these first" — and none fixable by writing. They become `not_in_draft`,
    which is absent from _CREDIT and therefore out of the denominator, and their
    note says where they ARE handled: the checklist.

    Applied at review time, not stored, so improving `draft_scope` retroactively
    fixes every profile already in the database.
    """
    for f in findings:
        # `delegated` is the stronger statement — the rule lives in a document we
        # never read. Do not overwrite it.
        if f.get("status") == "delegated":
            continue
        if draft_scope.is_draft_checkable(f.get("label", ""),
                                          f.get("solicitation_says", "")):
            continue
        f["status"] = "not_in_draft"
        f["evidence"] = ""
        f["note"] = draft_scope.NOTE
    return findings


def apply_delegation(findings: list[dict]) -> list[dict]:
    """Mark rows whose rule lives in a document this app never read.

    Deterministic and applied HERE rather than stored on the extracted
    requirement, so improving `delegated_rules` retroactively fixes every
    proposal already in the database — the same reason compliance_sentinel
    recomputes its verdicts on every load.

    A POINTER-ONLY row ("Adhere to PAPPG guidelines") is unassessable: its
    status becomes `delegated`, which is absent from _CREDIT and therefore out
    of the score's denominator, next to could_not_locate and unclear. Reporting
    it "addressed" was the bug — a five-line Project Summary passed because the
    only rule about it lives in the PAPPG.

    A RIDER keeps whatever status it earned. "Include the LOI number in addition
    to all the requirements outlined in the PAPPG" IS checkable, WAS checked and
    came back not_found; demoting it would delete a true finding about the
    draft to make room for a caveat.

    A BASELINE row (`rulebook_baseline`) is neither shape above — it IS the
    rulebook's rule, quoted, not a pointer INTO the rulebook. Its own quote
    names the PAPPG ("Your file must include three separate section headers"),
    so without this guard it would classify as a pointer or a rider and get
    reclassified or re-noted by the very engine it exists to route around —
    at worst demoted to `delegated` and deleted from the score's denominator,
    silently undoing the finding this feature was built to add.

    THE GUARD DEPENDS ON `_finding()` PROPAGATING `rulebook` (fixed 2026-08-17
    — it did not, originally, and the guard below silently never fired: every
    finding is built through `_finding()`, which never copied `req["rulebook"]`
    onto the dict it returned, so `f.get("rulebook")` was `None` for every row
    including a real baseline one. `classify()` happened to independently
    return `pointer_only=False` for all 14 current baseline labels — none
    starts with a compliance verb — so nothing broke in production, but a
    future rulebook row phrased that way would have been silently demoted with
    the guard doing nothing. Confirm `_finding()` still sets
    `"rulebook": req.get("rulebook")` before trusting this guard again.
    """
    for f in findings:
        if f.get("rulebook"):
            continue
        target, pointer_only = delegated_rules.classify(
            f.get("label", ""), f.get("solicitation_says", ""))
        f["delegated_to"] = target
        if target is None:
            continue
        if pointer_only:
            f["status"] = "delegated"
            f["evidence"] = ""       # nothing was verified, so quote nothing
            f["note"] = delegated_rules.note_for(target, pointer_only=True)
        # A rider keeps its note EXACTLY as the reviewer wrote it. The caveat is
        # carried by its `defers to X` tag and stated once above the findings;
        # repeating it inside every note buried the real feedback.
    return findings


def _basics_and_solicitation(profile: dict) -> dict:
    """`profile` with the rulebook's EXTENDED rows removed, sections and all.

    The sections go with them: an extended row can be the only thing that put a
    section in the universe, and leaving an empty one behind would report a PI's
    package as missing a part nothing was going to check anyway.

    A shallow copy, never a mutation — the caller's profile is the stored one
    and other tools still read every row from it.
    """
    all_rows = profile.get("requirements") or []
    rows = [r for r in all_rows if r.get("tier") != "extended"]

    # ONLY the sections an extended row BROUGHT IN are dropped, never every
    # section that ends up empty. A required attachment puts a section in the
    # universe with no requirement rows of its own — the locate stage still has
    # to look for it, and reporting a missing attachment is the compliance
    # rejection this tool exists to prevent. Dropping those would silently stop
    # telling a PI their Letters of Collaboration are absent.
    introduced = {r.get("section") for r in all_rows
                  if r.get("tier") == "extended" and r.get("section")}
    surviving = {r.get("section") for r in rows if r.get("section")}
    orphaned = introduced - surviving
    sections = {k: v for k, v in (profile.get("sections") or {}).items()
                if k not in orphaned}
    return {**profile, "requirements": rows, "sections": sections}


def review_draft(draft_text: str, *, profile: dict, title: Optional[str] = None,
                 budget: Optional[dict] = None, use_ai: bool = True,
                 pages: Optional[dict] = None) -> dict:
    """Review a pasted proposal against the solicitation `profile` describes.

    draft_text — the whole proposal, one blob.
    profile    — a services/solicitation_profile.make_profile() dict. The ONLY
                 thing that makes this review funder-specific.
    title      — the tracked proposal's title, for checks that read it.
    budget     — a compute_budget() result, if the PI has saved one.
    use_ai     — False forces the deterministic path (used by tests).
    pages      — section key -> REAL page count, from an upload. Absent it,
                 page rules report an estimate and never a verdict.
    """
    # THE SOLICITATION AND THE RULEBOOK'S BASICS — product decision 2026-08-26,
    # the same narrowing Check a Section took earlier that day. Measured on a
    # live proposal: 204 rules (48 solicitation + 14 basics + 142 extended)
    # became 62. A fix-list of two hundred rows buries the handful that matter,
    # which is the failure this repo predicted in writing before the extracted
    # PAPPG rules ever shipped.
    #
    # THIS RETIRES THE EXTENDED ROWS ENTIRELY. Check a Section already excluded
    # them, so this was the last path that read them: 142 reviewed rules now sit
    # in kb_structured/_pappg_24_1_rules.json with no reader, including the 19
    # prohibitions ("Do not request NSF funds for alcoholic beverages"). That is
    # a real loss and it was taken knowingly. What makes it defensible is that
    # NONE of the 142 carries a deterministic check — every code-decided rule is
    # among the curated 14 — so this narrows the model-judged half and leaves
    # the arithmetic untouched.
    #
    # Filtered HERE rather than at profile build, so a stored profile is
    # unchanged and the decision can be revisited by editing one line.
    profile = _basics_and_solicitation(profile)
    sections = profile.get("sections") or {}
    requirements = profile.get("requirements") or []
    solicitation_id = profile.get("id") or ""

    text = (draft_text or "").strip()
    if not text:
        return {
            "solicitation": _solicitation_meta(profile),
            "ai": False, "findings": [], "reviewer_notes": [], "score": None,
            "sections_located": [], "sections_missing": list(sections),
            "word_count": 0, "mistakes": [], "truncated": None,
            "message": "Paste your proposal to get a completeness review.",
        }

    spans, ai_located = locate_sections(text, sections, use_ai=use_ai)

    findings = run_deterministic(text, spans, profile, title=title, budget=budget,
                                 pages=pages)

    # Every remaining model call is independent once the spans are known, so they
    # run CONCURRENTLY. Measured on a real draft: 39s sequential -> ~15s. Five
    # round-trips at 8s each is the difference between a tool a PI uses and one
    # they abandon. Same ThreadPoolExecutor pattern as opportunity_finder's
    # fetchOpportunity fan-out.
    pd_span = _project_description_span(spans, sections)

    jobs = []
    for section_key in sections:
        reqs = [r for r in sp.requirements_for(profile, section_key)
                if r["kind"] == "semantic"]
        if reqs:
            span = pd_span if section_key == "project_description" else spans.get(section_key)
            jobs.append((section_key, span, reqs, sections, solicitation_id))
    # Whole-document semantic rows (no owning section) — assessed against the
    # Project Description if we have one, else the whole paste.
    loose = [r for r in sp.requirements_for(profile, None) if r["kind"] == "semantic"]
    if loose:
        jobs.append(("project_description", pd_span or {"text": text}, loose,
                     sections, solicitation_id))

    notes: list[dict] = []
    if jobs or use_ai:
        # CAPPED, and the cap is load-bearing. With a hand-written solicitation
        # this pool was ~6 wide because the section list was hand-written. An
        # EXTRACTED profile can name 25 sections, and an uncapped pool would fire
        # 25 concurrent Gemini calls per review — a 429 storm, and a request that
        # blows the 300s Cloud Run cap. Six at a time keeps the fan-out fast
        # (the reason it is concurrent at all: 39s sequential -> ~15s) without
        # letting a verbose solicitation take the service down.
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(jobs) + 1))) as pool:
            futures = [pool.submit(_review_section, *job) for job in jobs]
            notes_future = pool.submit(_reviewer_notes, spans, profile) if use_ai else None
            for fut in futures:
                findings.extend(fut.result())
            if notes_future is not None:
                notes = notes_future.result()

    ai_reviewed = any(f["source"] == "ai" for f in findings)

    # BEFORE score(): a pointer-only row must be out of the denominator, and a
    # model verdict on a rule it was never shown must not survive into the UI.
    findings = apply_delegation(findings)
    # AFTER delegation, so a delegated row keeps its stronger status, and before
    # score() so an out-of-scope row leaves the denominator.
    findings = apply_draft_scope(findings)

    order = {r["id"]: i for i, r in enumerate(requirements)}
    findings.sort(key=lambda f: (_source_rank(f), order.get(f["id"], 999)))

    ai_used = bool(ai_located or ai_reviewed)
    return {
        "solicitation": _solicitation_meta(profile),
        "ai": ai_used,
        "findings": findings,
        "reviewer_notes": notes,
        # Suppressed on the offline path: a percentage computed without the
        # semantic half would read as a verdict on the draft rather than on our
        # own availability.
        "score": score(findings, solicitation_id=solicitation_id) if ai_used else None,
        "sections_located": [
            {"key": k, "label": _section_label(sections, k), "heading": v["marker"],
             "word_count": len(v["text"].split())}
            for k, v in sorted(spans.items(), key=lambda kv: kv[1]["start"])
        ],
        "sections_missing": [
            {"key": k, "label": v["label"]} for k, v in sections.items() if k not in spans
        ],
        "word_count": len(text.split()),
        # A cap that is never reported is invisible however large it is. Same
        # contract as solicitation_extractor's `truncated` / `input_chars`:
        # both numbers, so a PI can see how much of their package was actually
        # read instead of being handed a completeness score computed over part
        # of it. ABSENT (None) when the package fitted — a warning that renders
        # on every review stops being read.
        "truncated": ({"chars": len(text), "read": MAX_DRAFT_CHARS}
                      if len(text) > MAX_DRAFT_CHARS else None),
        # Mechanical errors — placeholders, dangling figure references, a total
        # that contradicts the saved budget. Deterministic and quoted, and
        # deliberately OUTSIDE `findings` and outside the score: a leftover
        # "TBD" is not incompleteness against the solicitation, and folding the
        # two together would make a number that is already over-read mean less.
        "mistakes": mechanical_checks.find_mistakes(text, budget=budget),
        # One row per rulebook this solicitation points into, with a plain
        # description and how many of its requirements went unchecked. Also
        # names the sections whose rules the baseline supplied and we DID
        # check, so the caveat shrinks as coverage grows instead of forever
        # saying "not checked here" about a section that now is — PER RULEBOOK,
        # because one flat list stamped on every row claimed we check the Build
        # America, Buy America Act's Project Summary rules.
        "delegated": delegated_rules.summarize(
            findings,
            covered=rulebook_baseline.covered_sections(
                profile.get("requirements", []))),
        "eligibility_notes": profile.get("eligibility_notes") or [],
        "message": None if ai_used else (
            "The AI reviewer is unavailable, so only the rule-based checks ran and the "
            "completeness score is withheld. Everything below is still accurate."
        ),
    }


def review_section(text: str, *, section: str, rulebook: str,
                   profile: Optional[dict] = None,
                   pages: Optional[int] = None,
                   budget: Optional[dict] = None,
                   use_ai: bool = True) -> dict:
    """Check ONE section against its rulebook's rules, while the PI writes it.

    The same primitives as review_draft, with the requirement universe filtered
    to one section — NOT a parallel engine. The reason is the one CLAUDE.md
    gives for routing Draft Review's "Use that document" through the single
    existing attach path: a second path drifts, and two engines that disagree
    about the same section is exactly the confusion this tool exists to remove.

    NO SOLICITATION REQUIRED, and no score returned. Draft Review's 409 exists
    so a completeness percentage is never computed against zero requirements;
    this returns no percentage, so that guard has nothing to protect. When a
    `profile` IS supplied its own rows for this section are checked too.

    `pages` is this section's REAL page count from an uploaded PDF — one file is
    one section here, so unlike the whole-package path the count is exact.
    """
    label = rulebook_baseline.section_label(section)
    if profile and not rulebook_baseline.rules_for(rulebook, section, tier="basic"):
        # A section only the SOLICITATION names. `section_label` builds a name
        # out of the key when the rulebook has never heard of the section, so
        # `letter_intent` renders as "Letter Intent" while the profile carries
        # the funder's own wording, "Letter of Intent" — a tool that misspells
        # the deliverable it is checking reads as one that does not know it.
        # Scoped to sections the rulebook does NOT own, so Research.gov's
        # wording keeps winning everywhere it actually applies.
        meta = (profile.get("sections") or {}).get(section)
        label = (meta or {}).get("label") or label
    text = (text or "").strip()
    base = {
        "section": section, "label": label, "rulebook": rulebook,
        "skeleton": rulebook_baseline.skeleton_for(rulebook, section),
        "findings": [], "mistakes": [], "score": None, "ai": False,
        "word_count": len(text.split()),
    }
    if not text:
        return {**base, "message": f"Paste your {label} to have it checked."}

    # BASICS ONLY — see the note on rulebook_baseline.RULES. The extended rows
    # are the long tail read out of the rulebook itself (fonts, margins,
    # conditionals, per-proposal-type variants); measured on a live proposal
    # they outnumbered that solicitation's own rules 138 to 33, and 45 to 6 on
    # its Budget section, which is not what a PI opens this screen to see.
    #
    # NOT a narrowing of the product: `review_draft` reads the whole profile
    # and still gets every extended row, so a PI who has never met NSF's font
    # rules meets them in a full package review. Same distinction
    # `checklist_filter` draws keeping 7 of 24 requirements as tick-boxes while
    # the stored profile keeps all 24.
    rows = rulebook_baseline.rules_for(rulebook, section, tier="basic")
    if profile:
        # The picker sends a RULEBOOK key; a profile is keyed in the
        # solicitation's OWN vocabulary, and `sections_from` keeps whichever
        # name was already in use when it merges two spellings of one section.
        # `requirements_for` is an exact-key match, so asking it for the
        # rulebook's key drops every solicitation row filed under the key that
        # won the merge — silently, since the rulebook's own rows still fill
        # the page. `_refile_rows` already closed this for Draft Review, which
        # reads the whole profile; this is the same repair on the entry point
        # that keys off the picker instead.
        sections = profile.get("sections") or {}
        key = section if section in sections else (
            sp.resolve_section_key(sections, label) or section)
        # EXTENDED ROWS ARE EXCLUDED HERE TOO, and this is the door that
        # mattered. `baseline_rows` injects every rulebook rule INTO the profile
        # at load time — that is what makes the rulebook retroactive for
        # proposals stored before it existed — so a profile carries the extended
        # rows whether or not this function looks them up directly. Narrowing
        # only the lookup above changed nothing on a real proposal: a live
        # Budget check still returned 51 rules, 45 of them extended.
        seen = {b["id"] for b in rows}
        rows = rows + [r for r in sp.requirements_for(profile, key)
                       if r["id"] not in seen and r.get("tier") != "extended"]
    if not rows:
        return {**base, "message": (
            f"No rules are on file for {label}. Nothing was checked.")}

    # The whole paste IS the section, so the span is known without the locate
    # stage — that is the one thing this entry point genuinely skips.
    spans = {section: {"text": text, "marker": label, "start": 0}}
    mini = {"requirements": rows, "checks": _checks_from_profile(profile),
            "sections": {section: {"label": label, "aliases": [label]}}}

    findings = run_deterministic(text, spans, mini, budget=budget,
                                 pages={section: pages} if pages else None)

    semantic = [r for r in rows if r["kind"] == "semantic"]
    skipped_semantic = False
    if semantic:
        if use_ai:
            # _review_section's last parameter is `solicitation_id`, which reaches
            # the model only through _review_system's prompt text ("requirements
            # from <id>"). Passing the RULEBOOK name is correct here and reads
            # correctly: these rules do come from the PAPPG, not a solicitation.
            # VOTED here and not in review_draft: this is the entry point a PI
            # re-runs on one paragraph, where an unstable answer is visible and
            # infuriating. Tripling a ~14-section Draft Review is the 429 storm
            # the fan-out cap already exists to prevent.
            findings.extend(_review_section(section, spans[section], semantic,
                                            mini["sections"], rulebook,
                                            votes=SEMANTIC_VOTES))
        else:
            # use_ai=False must not make semantic rules VANISH. Run the same
            # fallback review_draft uses on an AI outage, so a caller sees
            # `unclear` rows with their explanatory note instead of nothing —
            # the difference between "no semantic rules exist for this section"
            # and "semantic rules were not assessed" must stay visible.
            findings.extend(_semantic_fallback(semantic, spans[section]["text"]))
            skipped_semantic = True

    # THE SAME TWO PASSES AS review_draft, IN THE SAME ORDER. This entry point
    # exists so a PI cannot be told two different things about one section, and
    # skipping delegation broke exactly that: a solicitation row whose whole ask
    # is "follow the PAPPG" came back `delegated` in Draft Review and
    # `not_found` — counted against the draft — here. Baseline rows carry
    # `rulebook` and apply_delegation's guard leaves them alone.
    findings = apply_delegation(findings)
    findings = apply_draft_scope(findings)
    order = {r["id"]: i for i, r in enumerate(rows)}
    findings.sort(key=lambda f: (_source_rank(f), order.get(f["id"], 999)))

    out = {
        **base,
        "findings": findings,
        "ai": any(f["source"] == "ai" for f in findings),
        # WHAT TO DO NEXT, and how much of the page this uses. Both computed in
        # code (services/section_guidance.py), and that is deliberate: the report
        # that prompted them was a 76-word Project Summary told six of eight
        # rules were "Addressed", where two runs of the same paste disagreed
        # about how many passed. A model asked "is this thin?" would be
        # inconsistent on exactly the question the author already distrusts. A
        # word count and an ordering are arithmetic.
        "guidance": {
            # `pages` is the real count from the upload path. Passing it is the
            # whole fix for a screen that said "over the limit ... upload it to
            # have this checked properly" beside a deterministic row reading
            # "1 page, within the 1-page limit", on an uploaded PDF.
            "length": section_guidance.length_guidance(
                base["word_count"], _section_page_limit(rows, section),
                pages=pages),
            "priorities": section_guidance.priorities(findings),
            # The list's own name, decided by what is IN it. "Do this first"
            # over a section that met every rule reads as failure.
            "priorities_heading": section_guidance.priorities_heading(findings),
        },
        # ONE SECTION, so the whole-document rules are off. Every well-cited
        # Project Description used to come back with "Works are cited but no
        # reference list was found ... If it is a separate file, upload it too",
        # in a modal that accepts one file for one section and says the rest of
        # the proposal is not needed — advice the PI cannot act on.
        "mistakes": mechanical_checks.find_mistakes(text, budget=budget,
                                                    whole_document=False),
        # PROOFREADING, under its OWN key. `mistakes` is model-free by contract
        # and the modal calls those rows "found by a rule, not a judgement" —
        # model output in that list would make the caption false. These are
        # advisory, they quote the draft (golden rule 2, verified in code), and
        # they are ABSENT from the score: a comma splice is not incompleteness
        # against a solicitation. Deliberately not run by `review_draft`: a whole
        # package is many thousands of words, and a proofread of all of it is a
        # different feature with a different cost, not this one scaled up.
        # The section's own headings, read off the RULE that enforces them so
        # the locator and the checker can never name different ones — the same
        # reason `_section_page_limit` reads the allowance off `rb_page_limit`.
        "wording": proofread.proofread(text, use_ai=use_ai,
                                       headings=_section_headings(rows)),
        # THE SCORE IS A RULES-MET SHARE, NEVER "how done this section is".
        # This used to return None, on the reasoning that a percentage would
        # read as "your Project Summary is 60% written" — which the rules cannot
        # measure, since they are NSF's floor and not a completeness universe.
        # That concern is real and is answered by what the number is computed
        # over rather than by withholding it: the denominator is the rules that
        # were actually CHECKED, so `not_checked`, `could_not_locate` and
        # `unclear` leave it entirely and an unscored conditional never enters.
        # `basis` says so in words, and `by_source` says which authority each
        # half came from. Same `score()` and same `_CREDIT` as Draft Review —
        # one scorer, two entry points, so the two can never disagree about the
        # same section.
        # `scope` is DERIVED inside score() from the rules that actually scored.
        # This used to pass a fixed "<rulebook>'s rules for this section", which
        # named the PAPPG on a Letter of Intent — a section the rulebook holds
        # no rules for at all.
        "score": score(findings,
                       solicitation_id=(profile or {}).get("id", "")),
        "message": (
            "The AI reviewer was not run for this check, so the requirements "
            "needing judgment are marked unclear below rather than assessed — "
            "only the rule-based checks are complete."
        ) if skipped_semantic else None,
    }
    # THE VERDICT READS BOTH HALVES, and is the only thing here that does.
    # `score` knows the rules and nothing about the writing; `mistakes` and
    # `wording` know the writing and nothing about the rules. Measured before
    # this existed: five rules met, fifteen errors found, headline "100%".
    out["verdict"] = verdict(out["score"], mistakes=out["mistakes"],
                             wording=out["wording"], findings=out["findings"])
    return out


def _section_headings(rows: list[dict]) -> list[str]:
    """The headings this section's own rule requires, or [].

    Read from the RULES rather than typed a second time: two copies of one list
    drift, and then the wording locator names a heading the checker does not
    require. Same contract as `_section_page_limit` below.
    """
    for r in rows or []:
        if r.get("check") == "rb_headings":
            names = (r.get("check_args") or {}).get("headings") or []
            return [str(n) for n in names]
    return []


def _section_page_limit(rows: list[dict], section: str) -> Optional[float]:
    """This section's stated page limit, read off the rule that enforces it.

    Read from the RULES rather than passed in, so the allowance and the check
    that enforces it can never disagree — the same reason the profile rebuilds
    its deterministic rows from `contract` instead of storing two copies. None
    when no rule states one, and `length_guidance` then says nothing at all:
    most sections have no limit, and deriving an allowance for them would
    fabricate the very rule this is careful not to state.
    """
    for r in rows:
        if r.get("check") in _PAGE_CHECK_NAMES:
            args = r.get("check_args") or {}
            if args.get("section") in (section, None) and args.get("limit"):
                try:
                    return float(args["limit"])
                except (TypeError, ValueError):
                    return None
    return None


_PAGE_CHECK_NAMES = {"rb_page_limit", "page_limit"}


def _checks_from_profile(profile: Optional[dict]) -> dict:
    """The profile's own check callables, or none."""
    return (profile or {}).get("checks") or {}


def _solicitation_meta(profile: dict) -> dict:
    return {
        "id": profile.get("id"),
        "title": profile.get("title"),
        "url": profile.get("url"),
    }
