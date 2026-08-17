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
from services import solicitation_profile as sp
# The shared whitespace-collapsing membership test (golden rule 2). Deliberately
# imported rather than re-implemented so every grounded feature uses ONE
# definition. Lived in section_coach._quote_in until the Drafting Coach was
# removed (2026-08-10); now has a feature-neutral home.
from services.text_match import quote_in as _quote_in

# gemini-3.6-flash is region-locked to the "global" endpoint — it 404s in
# us-central1. Both must move together, which is why they are one env pair.
MODEL = os.getenv("EIR_REVIEW_MODEL", "gemini-3.6-flash")
MODEL_LOCATION = os.getenv("EIR_REVIEW_LOCATION", "global")

# A full Project Description runs ~15 pages. Truncating the paste would make a
# late section look missing, so the cap is generous; the model's context is not
# the binding constraint here.
MAX_DRAFT_CHARS = 120_000

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


def _spans_from_markers(text: str, markers: dict, sections: dict) -> dict:
    """{section_key: first_line} -> {section_key: {"text","start","end","marker"}}.

    Each located section runs from its own marker to the next marker in document
    order, so the spans tile the paste without overlapping."""
    found = []
    for key, marker in markers.items():
        if key not in sections:
            continue                      # model invented a section name — ignore
        off = _find_offset(text, str(marker or ""))
        if off is None:
            continue                      # marker not verifiable -> not located
        found.append((off, key, str(marker)))
    found.sort()
    spans = {}
    for i, (start, key, marker) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(text)
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
                    sections: dict, solicitation_id: str) -> list[dict]:
    """Coverage for one section's requirements, grounded and verified."""
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
    listing = [{"id": r["id"], "requirement": r["label"],
                "solicitation_says": r["source"]} for r in reqs]
    prompt = (
        f"SECTION: {label} of a proposal to solicitation {solicitation_id}.\n"
        f"REQUIREMENTS (return a row for EVERY id):\n{listing}\n\n"
        "DRAFT TEXT:\n\"\"\"\n" + section_text[:MAX_DRAFT_CHARS] + "\n\"\"\"\n\n"
        'Return JSON: {"findings": [{"id": "<requirement id>", '
        '"status": "addressed|partial|not_found", "note": "<1-2 sentences, actionable>", '
        '"evidence": "<verbatim quote from DRAFT TEXT, or empty>"}]}'
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
        status = str(raw.get("status", "")).strip().lower()
        if status not in ("addressed", "partial", "not_found"):
            status = "not_found"
        evidence = str(raw.get("evidence", "") or "").strip()
        note = str(raw.get("note", "") or "").strip()
        # GOLDEN RULE 2: a positive claim without a verifiable quote is dropped.
        if status in ("addressed", "partial") and not _quote_in(section_text, evidence):
            status, evidence = "not_found", ""
            note = (note + " ").strip() + (
                " (A supporting quote could not be verified in your text, so this is reported "
                "as not found.)")
        out.append(_finding(req, status, note, evidence, source="ai"))
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
_CREDIT = {
    "addressed": 1.0,
    "partial": 0.5,
    "not_found": 0.0,
    "clear": 1.0,      # prohibition respected
    "flagged": 0.0,    # prohibition violated
}


def score(findings: list[dict], *, solicitation_id: str = "") -> Optional[dict]:
    """Completeness against `solicitation_id`, computed in code (golden rule 1).

    Deliberately NOT a funding prediction and deliberately not model-assigned.
    Returns None when nothing scoreable was assessed."""
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
    return {
        "percent": pct,
        "band": band,
        "assessed": len(scored),
        "earned": round(earned, 1),
        "counts": counts,
        # Shown verbatim next to the number so it cannot be read as a funding odds.
        "basis": (f"{pct}% of the {len(scored)} "
                  + (f"{solicitation_id} " if solicitation_id else "")
                  + "requirements this reviewer could assess are addressed in your draft. "
                  "This measures completeness against the solicitation, not the likelihood "
                  "of an award."),
    }


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
    return {**pd, "text": pd["text"] + "\n\n" + bi["text"]}


def _finding(req: dict, status: str, note: str, evidence: str, *, source: str) -> dict:
    return {
        "id": req["id"],
        "label": req["label"],
        "section": req.get("section"),
        "kind": req["kind"],
        "scored": bool(req.get("scored")),
        "prohibition": bool(req.get("flag_if_present")),
        "status": status,
        "note": note,
        "evidence": evidence,
        "solicitation_says": req["source"],
        "why": req.get("why", ""),
        "source": source,   # check | ai | fallback | locate — for debugging/UI
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
    sections = profile.get("sections") or {}
    requirements = profile.get("requirements") or []
    solicitation_id = profile.get("id") or ""

    text = (draft_text or "").strip()
    if not text:
        return {
            "solicitation": _solicitation_meta(profile),
            "ai": False, "findings": [], "reviewer_notes": [], "score": None,
            "sections_located": [], "sections_missing": list(sections),
            "word_count": 0, "mistakes": [],
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
    findings.sort(key=lambda f: order.get(f["id"], 999))

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
        # saying "not checked here" about a section that now is.
        "delegated": delegated_rules.summarize(
            findings,
            covered=[rulebook_baseline.section_label(s) for s in sorted({
                r["section"] for r in profile.get("requirements", [])
                if r.get("rulebook") and r.get("section")})]),
        "eligibility_notes": profile.get("eligibility_notes") or [],
        "message": None if ai_used else (
            "The AI reviewer is unavailable, so only the rule-based checks ran and the "
            "completeness score is withheld. Everything below is still accurate."
        ),
    }


def _solicitation_meta(profile: dict) -> dict:
    return {
        "id": profile.get("id"),
        "title": profile.get("title"),
        "url": profile.get("url"),
    }
