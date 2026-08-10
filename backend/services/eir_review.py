"""EiR draft reviewer — completeness of a pasted proposal against NSF 23-598.

WHAT THIS IS (and is not)
-------------------------
A PI pastes their whole EiR proposal into one box. This returns, per requirement
in services/eir_solicitation.EIR_REQUIREMENTS, whether the draft addresses it —
every positive claim backed by a verbatim quote from the PI's own text.

It is a COMPLETENESS reviewer. The score it returns is "percent of NSF 23-598's
requirements this draft demonstrably addresses", computed in code from the
coverage counts. It is NOT a prediction of funding, and no part of it is a
go/no-go verdict (the deleted Fundability tool did that; see CLAUDE.md).

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

from services import gemini_client
from services import eir_solicitation as sol
# The shared whitespace-collapsing membership test (golden rule 2). Deliberately
# imported rather than re-implemented so every grounded feature uses ONE
# definition. Lived in section_coach._quote_in until the Drafting Coach was
# removed (2026-08-10); now has a feature-neutral home.
from services.text_match import quote_in as _quote_in

# gemini-3.6-flash is region-locked to the "global" endpoint — it 404s in
# us-central1. Both must move together, which is why they are one env pair.
MODEL = os.getenv("EIR_REVIEW_MODEL", "gemini-3.6-flash")
MODEL_LOCATION = os.getenv("EIR_REVIEW_LOCATION", "global")

# A full EiR Project Description runs ~15 pages. Truncating the paste would make
# a late section look missing, so the cap is generous; the model's context is not
# the binding constraint here.
MAX_DRAFT_CHARS = 120_000

# ~550 words/page at NSF's formatting rules (11pt, single-spaced, 1" margins).
# Used only to estimate a pasted letter's page count — flagged as an estimate in
# the finding, because a real PDF is the authority.
WORDS_PER_PAGE = 550

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


def _spans_from_markers(text: str, markers: dict) -> dict:
    """{section_key: first_line} -> {section_key: {"text","start","end","marker"}}.

    Each located section runs from its own marker to the next marker in document
    order, so the spans tile the paste without overlapping."""
    found = []
    for key, marker in markers.items():
        if key not in sol.SECTIONS:
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


def _heading_regex(alias: str) -> re.Pattern:
    """A heading line for `alias`: optional numbering/bullets, the alias, then
    optional punctuation — and nothing else on the line. Anchored per-line so a
    passing mention inside a paragraph never counts as a heading."""
    return re.compile(
        r"^[ \t]*(?:[\dIVXivx]+[.)]\s*)*(?:[-–—*•]\s*)?" + re.escape(alias) + r"\s*:?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )


def _locate_fallback(text: str) -> dict:
    """Deterministic segmentation: scan for heading lines matching known aliases.
    Used when the model is unavailable, and to fill sections the model missed."""
    markers = {}
    for key, meta in sol.SECTIONS.items():
        best = None
        for alias in meta["aliases"]:
            m = _heading_regex(alias).search(text)
            if m and (best is None or m.start() < best.start()):
                best = m
        if best is not None:
            markers[key] = best.group(0).strip()
    return markers


def locate_sections(text: str, *, use_ai: bool = True) -> tuple[dict, bool]:
    """Segment the paste. Returns (spans, ai_used).

    The model proposes markers; code verifies each one is really in the text and
    only then accepts it. The deterministic scan fills any section the model did
    not report, so the two are additive rather than either/or."""
    text = text or ""
    markers: dict = {}
    ai_used = False
    if use_ai and text.strip():
        known = {k: v["label"] for k, v in sol.SECTIONS.items()}
        prompt = (
            "Segment this grant proposal into sections.\n"
            f"SECTION KEYS you may use (use the key, not the label): {known}\n"
            "PROPOSAL TEXT:\n\"\"\"\n" + text[:MAX_DRAFT_CHARS] + "\n\"\"\"\n\n"
            'Return JSON: {"sections": {"<section_key>": "<verbatim first line of '
            'that section>", ...}}. Omit any section you did not find.'
        )
        ai = gemini_client.generate_json(
            prompt, temperature=0.0, max_output_tokens=2048, timeout_s=60,
            system_instruction=_LOCATE_SYSTEM, model=MODEL, location=MODEL_LOCATION,
        )
        if ai and isinstance(ai.get("sections"), dict):
            ai_used = True
            markers = {str(k): str(v) for k, v in ai["sections"].items() if v}

    for key, marker in _locate_fallback(text).items():
        markers.setdefault(key, marker)

    # Broader Impacts is a labeled sub-section INSIDE the Project Description, so
    # a heading match is the only evidence that it is "separately labeled" — the
    # requirement the solicitation actually states. Don't let it swallow the tail
    # of the Project Description as if it were a sibling top-level section.
    return _spans_from_markers(text, markers), ai_used


# ── STAGE 2: DETERMINISTIC CHECKS ───────────────────────────────────────────
# Every function here returns (status, detail, evidence). Statuses:
#   addressed / partial / not_found   — normal requirements
#   clear / flagged                   — prohibitions (flag_if_present)
#   not_checked                       — the input needed wasn't supplied
# `not_checked` and `could_not_locate` are excluded from the score denominator.

_LOI_RE = re.compile(
    r"\b(?:LOI|letter\s+of\s+intent)\b[^\n]{0,60}?\b(\d{5,})\b", re.IGNORECASE)

_SUPPORT_LETTER_RE = re.compile(r"letters?\s+of\s+support", re.IGNORECASE)
_COST_SHARE_RE = re.compile(
    r"\b(?:cost[\s-]?shar\w*|matching\s+funds?|in[-\s]kind\s+(?:contribution|match)\w*)\b",
    re.IGNORECASE)
# Phrases that mean the PI is DESCRIBING the rule, not offering the thing. Without
# this, a proposal that correctly says "no cost sharing is included" gets flagged
# for the very compliance it is demonstrating.
_NEGATION_WINDOW = 90
_NEGATIONS = ("not allowed", "not permitted", "prohibited", "no ", "not included",
              "is not", "are not", "cannot", "not required", "not requested",
              "without", "none", "does not", "do not", "not offering", "not offered")

# The invariant middle of NSF's mandated collaboration sentence — the part no
# real letter can paraphrase away, with the bracketed placeholders (which a real
# letter fills in) excluded.
_COLLAB_SPINE = ("is selected for funding by the NSF, it is my intent to collaborate "
                 "and/or commit resources as detailed in the Project Description")

_DC_TRAVEL_RE = re.compile(
    r"(?:washington|\bd\.?c\.?\b|grantee\s+meeting|annual\s+meeting|pi\s+meeting)",
    re.IGNORECASE)
_EVERY_YEAR_RE = re.compile(
    r"(?:each\s+year|every\s+year|annual(?:ly)?|per\s+year|all\s+\w+\s+years|"
    r"years?\s*1\s*[-–]\s*\d)", re.IGNORECASE)


def _negated_nearby(text: str, at: int) -> bool:
    """True if a negation appears just before `at` — i.e. the match is the PI
    stating the rule rather than violating it."""
    window = text[max(0, at - _NEGATION_WINDOW):at].lower()
    return any(n in window for n in _NEGATIONS)


def _check_title_prefix(ctx: dict) -> tuple:
    prefix = sol.TITLE_PREFIX.lower()
    title = _norm_ws(ctx.get("title") or "")
    if title.lower().startswith(prefix):
        return "addressed", "The proposal title carries the required prefix.", title[:160]
    head = _norm_ws(ctx["text"][:400])
    idx = head.lower().find(prefix)
    if idx >= 0:
        return "addressed", "Found the required prefix at the top of the draft.", \
            head[idx:idx + 160]
    got = title or head[:80]
    return "not_found", (
        f'Nothing starts with "{sol.TITLE_PREFIX}". '
        + (f'The title reads "{got}".' if got else "No title found in the paste.")
    ), ""


def _check_loi_number(ctx: dict) -> tuple:
    span = ctx["spans"].get("project_summary")
    if not span:
        return "could_not_locate", "No Project Summary found in the paste.", ""
    m = _LOI_RE.search(span["text"])
    if m:
        return "addressed", f"Found LOI number {m.group(1)}.", _norm_ws(m.group(0))[:160]
    return "not_found", (
        "No Letter of Intent number in the Project Summary. NSF 23-598 requires it there, "
        "and this requirement does not exist for most other NSF programs."
    ), ""


def _check_institutional_letter_length(ctx: dict) -> tuple:
    span = ctx["spans"].get("institutional_support_letter")
    if not span:
        return "could_not_locate", "No Letter of Institutional Support found in the paste.", ""
    words = len(span["text"].split())
    pages = words / WORDS_PER_PAGE
    if pages <= sol.INSTITUTIONAL_LETTER_MAX_PAGES:
        return "addressed", (
            f"About {words:,} words ≈ {pages:.1f} pages (estimated), within the 2-page cap."
        ), ""
    return "not_found", (
        f"About {words:,} words ≈ {pages:.1f} pages (estimated), over the 2-page cap. "
        "This is an estimate from word count — check the formatted PDF."
    ), ""


def _check_collaboration_letter_format(ctx: dict) -> tuple:
    span = ctx["spans"].get("collaboration_letters")
    if not span:
        # Absent is legitimate: collaboration letters are required only when the
        # project HAS collaborators. Never scored as a failure on that basis.
        return "not_checked", (
            "No collaboration letters in the paste. Required only if your project has "
            "collaborators — if it does, they must use NSF's exact sentence."
        ), ""
    if _quote_in(span["text"], _COLLAB_SPINE):
        return "addressed", "Uses NSF's mandated single-sentence wording.", _COLLAB_SPINE[:160]
    return "not_found", (
        "The collaboration letter does not contain NSF's mandated sentence. NSF means "
        "single-sentence literally — a longer, warmer letter is a deviation."
    ), ""


def _check_no_support_letters(ctx: dict) -> tuple:
    for m in _SUPPORT_LETTER_RE.finditer(ctx["text"]):
        before = ctx["text"][max(0, m.start() - 40):m.start()].lower()
        # "Letter of Institutional Support" is REQUIRED — never flag it.
        if "institutional" in before or "institution" in before:
            continue
        if _negated_nearby(ctx["text"], m.start()):
            continue
        return "flagged", (
            "Found a reference to a letter of support. NSF 23-598 states these are not "
            "allowed from collaborators — only the single-sentence collaboration letter is. "
            "(The Letter of Institutional Support is separate and IS required.)"
        ), _norm_ws(ctx["text"][max(0, m.start() - 60):m.end() + 60])[:160]
    return "clear", "No prohibited letters of support detected.", ""


def _check_no_cost_sharing(ctx: dict) -> tuple:
    for m in _COST_SHARE_RE.finditer(ctx["text"]):
        if _negated_nearby(ctx["text"], m.start()):
            continue
        return "flagged", (
            "Found what looks like an offer of cost sharing or matching funds. Voluntary "
            "committed cost sharing is prohibited under this solicitation."
        ), _norm_ws(ctx["text"][max(0, m.start() - 60):m.end() + 60])[:160]
    return "clear", "No voluntary committed cost sharing detected.", ""


def _check_equipment_cap(ctx: dict) -> tuple:
    budget = ctx.get("budget")
    if not budget:
        return "not_checked", (
            "No budget saved for this proposal yet. Build one in the Budget Helper and this "
            "check runs against real numbers."
        ), ""
    equipment = float(budget.get("equipment") or 0.0)
    total = float(budget.get("total") or 0.0)
    if total <= 0:
        return "not_checked", "The saved budget has no total to measure against.", ""
    pct = equipment / total * 100.0
    basis = f"${equipment:,.0f} equipment of ${total:,.0f} total = {pct:.1f}%"
    if pct <= sol.EQUIPMENT_MAX_PCT:
        return "addressed", f"{basis}, within the 30% cap.", ""
    return "not_found", (
        f"{basis}, over the 30% cap. Reduce equipment or increase other direct costs."
    ), ""


def _check_dc_meeting_travel(ctx: dict) -> tuple:
    span = ctx["spans"].get("budget_justification")
    scope = span["text"] if span else ctx["text"]
    where = "budget justification" if span else "draft"
    hit = _DC_TRAVEL_RE.search(scope)
    if not hit:
        return "not_found", (
            f"No mention of the annual grantee meeting in the {where}. NSF 23-598 requires "
            "budgeting for the PI to attend a two-day meeting in Washington, DC every year "
            "of the project. This is the most commonly omitted EiR budget line."
        ), ""
    window = scope[max(0, hit.start() - 200):hit.end() + 200]
    if _EVERY_YEAR_RE.search(window):
        return "addressed", "Grantee-meeting travel appears to be budgeted for each year.", \
            _norm_ws(window)[:160]
    return "partial", (
        "Found grantee-meeting travel, but nothing indicating it is budgeted for EVERY year "
        "of the project — which is what the solicitation requires."
    ), _norm_ws(window)[:160]


_DETERMINISTIC_CHECKS = {
    "title_prefix": _check_title_prefix,
    "loi_number": _check_loi_number,
    "institutional_letter_length": _check_institutional_letter_length,
    "collaboration_letter_format": _check_collaboration_letter_format,
    "no_support_letters": _check_no_support_letters,
    "no_cost_sharing": _check_no_cost_sharing,
    "equipment_cap": _check_equipment_cap,
    "dc_meeting_travel": _check_dc_meeting_travel,
}


def run_deterministic(text: str, spans: dict, *, title: Optional[str] = None,
                      budget: Optional[dict] = None) -> list[dict]:
    """Every code-decided requirement. No model involved, so these findings are
    identical whether or not Gemini is reachable (golden rule 1)."""
    ctx = {"text": text or "", "spans": spans or {}, "title": title, "budget": budget}
    out = []
    for req in sol.EIR_REQUIREMENTS:
        if req["kind"] != "deterministic":
            continue
        fn = _DETERMINISTIC_CHECKS.get(req.get("check", ""))
        if fn is None:
            continue
        status, detail, evidence = fn(ctx)
        out.append(_finding(req, status, detail, evidence, source="check"))
    return out


# ── STAGE 3: SEMANTIC COVERAGE ──────────────────────────────────────────────

_REVIEW_SYSTEM = (
    "You assess whether a draft NSF proposal section addresses a FIXED list of "
    "requirements from solicitation NSF 23-598. You are an advisory reviewer, not an "
    "editor, and you never rewrite the author's prose.\n"
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
    "You are an experienced NSF panellist giving ADVISORY impressions of a draft proposal. "
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


def _review_section(section_key: str, span: Optional[dict],
                    reqs: list[dict]) -> list[dict]:
    """Coverage for one section's requirements, grounded and verified."""
    if not reqs:
        return []
    if span is None:
        return [_finding(r, "could_not_locate",
                         f"Could not find the {sol.SECTIONS[section_key]['label']} in what you "
                         "pasted, so this requirement was not assessed. If you did include it, "
                         "add a clear heading and re-run.", "", source="locate")
                for r in reqs]

    section_text = span["text"]
    listing = [{"id": r["id"], "requirement": r["label"],
                "solicitation_says": r["source"]} for r in reqs]
    prompt = (
        f"SECTION: {sol.SECTIONS[section_key]['label']} of an NSF HBCU-EiR proposal "
        f"(solicitation {sol.SOLICITATION_ID}).\n"
        f"REQUIREMENTS (return a row for EVERY id):\n{listing}\n\n"
        "DRAFT TEXT:\n\"\"\"\n" + section_text[:MAX_DRAFT_CHARS] + "\n\"\"\"\n\n"
        'Return JSON: {"findings": [{"id": "<requirement id>", '
        '"status": "addressed|partial|not_found", "note": "<1-2 sentences, actionable>", '
        '"evidence": "<verbatim quote from DRAFT TEXT, or empty>"}]}'
    )
    ai = gemini_client.generate_json(
        prompt, temperature=0.0, max_output_tokens=8192, timeout_s=90,
        system_instruction=_REVIEW_SYSTEM, model=MODEL, location=MODEL_LOCATION,
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


def _reviewer_notes(spans: dict) -> list[dict]:
    """Advisory panel impressions, one per NSF merit criterion. Opinions only —
    no quote required, never counted as coverage — coverage stays in the grounded
    findings above (golden rule 2)."""
    span = _project_description_span(spans)
    if not span:
        return []
    criteria = "; ".join(f"{c['criterion']} ({c['asks']})" for c in sol.MERIT_CRITERIA)
    prompt = (
        f"MERIT CRITERIA: {criteria}\n"
        "DRAFT PROJECT DESCRIPTION:\n\"\"\"\n" + span["text"][:MAX_DRAFT_CHARS] + "\n\"\"\"\n\n"
        'Return JSON: {"reviewer_notes": [{"criterion": "<exact criterion name>", '
        '"note": "<how a panel would judge THIS draft on that criterion, and what would '
        'strengthen it>"}]}'
    )
    ai = gemini_client.generate_json(
        prompt, temperature=0.3, max_output_tokens=2048, timeout_s=60,
        system_instruction=_NOTES_SYSTEM, model=MODEL, location=MODEL_LOCATION,
    )
    if not ai or not isinstance(ai.get("reviewer_notes"), list):
        return []
    valid = {c["criterion"] for c in sol.MERIT_CRITERIA}
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


def score(findings: list[dict]) -> Optional[dict]:
    """Completeness against NSF 23-598, computed in code (golden rule 1).

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
        "basis": (f"{pct}% of the {len(scored)} NSF 23-598 requirements this reviewer could "
                  "assess are addressed in your draft. This measures completeness against "
                  "the solicitation, not the likelihood of an award."),
    }


# ── ASSEMBLY ────────────────────────────────────────────────────────────────

def _project_description_span(spans: dict) -> Optional[dict]:
    """The Project Description span, WITH the Broader Impacts text folded back in.

    Broader Impacts is a labeled sub-section INSIDE the Project Description, not a
    sibling of it — but the locate stage has to treat its heading as a boundary in
    order to prove the heading exists (the "separately labeled" requirement). That
    split has a side effect: Project Description requirements would be judged
    against text with the Broader Impacts paragraphs cut out.

    Measured, before this existed: a draft whose Broader Impacts read "Four
    undergraduates per year will be trained in materials characterization" was
    told it had not addressed "improves research opportunities for students" —
    the exact sentence that addresses it, invisible because of where we cut.

    Concatenating is safe for grounding: evidence is verified against whatever
    text we pass, so a quote from either part still verifies against the real
    draft, and neither part is fabricated."""
    pd = spans.get("project_description")
    bi = spans.get("broader_impacts")
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
    }


def review_eir_draft(draft_text: str, *, title: Optional[str] = None,
                     budget: Optional[dict] = None,
                     use_ai: bool = True) -> dict:
    """Review a pasted EiR proposal against NSF 23-598.

    draft_text — the whole proposal, one blob.
    title      — the tracked proposal's title, for the prefix check.
    budget     — a compute_budget() result, if the PI has saved one.
    use_ai     — False forces the deterministic path (used by tests).
    """
    text = (draft_text or "").strip()
    if not text:
        return {
            "solicitation": _solicitation_meta(),
            "ai": False, "findings": [], "reviewer_notes": [], "score": None,
            "sections_located": [], "sections_missing": list(sol.SECTIONS),
            "word_count": 0,
            "message": "Paste your EiR proposal to get a completeness review.",
        }

    spans, ai_located = locate_sections(text, use_ai=use_ai)

    findings = run_deterministic(text, spans, title=title, budget=budget)

    # Every remaining model call is independent once the spans are known, so they
    # run CONCURRENTLY. Measured on a real draft: 39s sequential -> ~15s. Five
    # round-trips at 8s each is the difference between a tool a PI uses and one
    # they abandon. Same ThreadPoolExecutor pattern as opportunity_finder's
    # fetchOpportunity fan-out.
    pd_span = _project_description_span(spans)

    jobs = []
    for section_key in sol.SECTIONS:
        reqs = [r for r in sol.requirements_for(section_key) if r["kind"] == "semantic"]
        if reqs:
            span = pd_span if section_key == "project_description" else spans.get(section_key)
            jobs.append((section_key, span, reqs))
    # Whole-document semantic rows (no owning section) — assessed against the
    # Project Description if we have one, else the whole paste.
    loose = [r for r in sol.requirements_for(None) if r["kind"] == "semantic"]
    if loose:
        jobs.append(("project_description", pd_span or {"text": text}, loose))

    notes: list[dict] = []
    if jobs or use_ai:
        with ThreadPoolExecutor(max_workers=max(1, len(jobs) + 1)) as pool:
            futures = [pool.submit(_review_section, *job) for job in jobs]
            notes_future = pool.submit(_reviewer_notes, spans) if use_ai else None
            for fut in futures:
                findings.extend(fut.result())
            if notes_future is not None:
                notes = notes_future.result()

    ai_reviewed = any(f["source"] == "ai" for f in findings)

    order = {r["id"]: i for i, r in enumerate(sol.EIR_REQUIREMENTS)}
    findings.sort(key=lambda f: order.get(f["id"], 999))

    ai_used = bool(ai_located or ai_reviewed)
    return {
        "solicitation": _solicitation_meta(),
        "ai": ai_used,
        "findings": findings,
        "reviewer_notes": notes,
        # Suppressed on the offline path: a percentage computed without the
        # semantic half would read as a verdict on the draft rather than on our
        # own availability.
        "score": score(findings) if ai_used else None,
        "sections_located": [
            {"key": k, "label": sol.SECTIONS[k]["label"], "heading": v["marker"],
             "word_count": len(v["text"].split())}
            for k, v in sorted(spans.items(), key=lambda kv: kv[1]["start"])
        ],
        "sections_missing": [
            {"key": k, "label": v["label"]} for k, v in sol.SECTIONS.items() if k not in spans
        ],
        "word_count": len(text.split()),
        "eligibility_notes": sol.ELIGIBILITY_NOTES,
        "message": None if ai_used else (
            "The AI reviewer is unavailable, so only the rule-based checks ran and the "
            "completeness score is withheld. Everything below is still accurate."
        ),
    }


def _solicitation_meta() -> dict:
    from datetime import date
    year = date.today().year
    # Past this year's full-proposal deadline, the next cycle is what matters.
    if date.today() > sol.full_proposal_deadline(year):
        year += 1
    return {
        "id": sol.SOLICITATION_ID,
        "title": sol.SOLICITATION_TITLE,
        "url": sol.SOLICITATION_URL,
        "cycle": sol.cycle_dates(year),
    }
