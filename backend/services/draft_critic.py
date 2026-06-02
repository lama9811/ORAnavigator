"""Draft Critic -- mechanical pre-submission check for a research proposal
PDF against the solicitation requirements already known by the Proposals
tracker.

Version 2 (2026-05-27) upgrades:
  - Grammar (singular "1 page" not "1 pages"); cleaner detail copy.
  - Overall verdict surfaced at the top of the response so the UI can
    show one big "Looks ready" / "Needs review" / "Critical issues" line
    instead of just a counts strip.
  - Header-aware section detection: a section is "found" only when the
    name appears at the start of a line (not buried inside body text).
    Cuts the false-positive rate that made checks feel shallow.
  - Per-section page-limit checks (DMP, Biographical Sketch) in addition
    to the document-wide cap, so users see the same fine-grained checks
    the solicitation specifies.
  - Project Summary word-count check (NSF caps it at 1 page / ~500 words).
  - "This looks like a solicitation, not a draft" detector -- catches
    the common user mistake of uploading the sponsor PDF instead of
    their own proposal.
  - De-duplicates findings across the Required Attachments and Standard
    Sections checks: when both would flag the same missing item, only
    the Required-Attachments row is shown (since that one is sponsor-
    mandated, not just convention).

No LLM is called. Every result is derived deterministically from the PDF
text + the solicitation context the user already gave us, so there's no
hallucination risk -- if Draft Critic says "Biographical Sketch: MISSING",
the user can trust that string really is not in the document.
"""

from __future__ import annotations

import math
import re
from io import BytesIO
from typing import Optional

# Lazy import (matches solicitation_extractor pattern). pdfplumber adds
# noticeable startup cost; we only pay it when someone actually runs a
# critique.
_pdfplumber = None


def _get_pdfplumber():
    global _pdfplumber
    if _pdfplumber is None:
        import pdfplumber  # type: ignore
        _pdfplumber = pdfplumber
    return _pdfplumber


# ===========================================================================
# Sponsor-default required sections
# ===========================================================================

_NSF_DEFAULT_SECTIONS = [
    "Project Summary",
    "Project Description",
    "References Cited",
    "Biographical Sketch",
    "Budget Justification",
    "Current and Pending Support",
    "Facilities, Equipment and Other Resources",
    "Data Management Plan",
]

_NIH_DEFAULT_SECTIONS = [
    "Specific Aims",
    "Research Strategy",
    "Bibliography and References Cited",
    "Biographical Sketch",
    "Budget Justification",
    "Resource Sharing Plan",
    "Authentication of Key Biological and/or Chemical Resources",
]

_DOD_DEFAULT_SECTIONS = [
    "Technical Volume",
    "Cost Volume",
    "Biographical Sketch",
    "Budget Justification",
]

_GENERIC_DEFAULT_SECTIONS = [
    "Project Summary",
    "Project Description",
    "Budget",
    "Budget Justification",
]


def _sponsor_default_sections(sponsor: Optional[str]) -> list[str]:
    s = (sponsor or "").upper()
    if s == "NSF":
        return list(_NSF_DEFAULT_SECTIONS)
    if s == "NIH":
        return list(_NIH_DEFAULT_SECTIONS)
    if s in ("DOD", "DOE"):
        return list(_DOD_DEFAULT_SECTIONS)
    return list(_GENERIC_DEFAULT_SECTIONS)


# ===========================================================================
# Helpers: PDF text & page count
# ===========================================================================

def _extract_pdf(pdf_bytes: bytes) -> tuple[str, int, list[str]]:
    """PDF bytes -> (full_text, page_count, pages_text_list).
    The per-page list lets us localize section-specific checks (e.g.
    "Data Management Plan must be 2 pages") instead of only looking at
    the whole document."""
    if not pdf_bytes:
        return ("", 0, [])
    try:
        pdfp = _get_pdfplumber()
    except ImportError:
        print("   [DRAFT_CRITIC] pdfplumber not installed")
        return ("", 0, [])
    pages_text: list[str] = []
    page_count = 0
    try:
        with pdfp.open(BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                t = page.extract_text() or ""
                pages_text.append(t)
    except Exception as e:
        print(f"   [DRAFT_CRITIC] PDF parse failed: {e}")
        return ("", 0, [])
    return ("\n\n".join(pages_text), page_count, pages_text)


# ===========================================================================
# Grammar helpers
# ===========================================================================

def _plural(n: int, singular: str, plural: Optional[str] = None) -> str:
    """Return a properly-pluralized noun: 1 page / 2 pages."""
    if n == 1:
        return f"{n} {singular}"
    return f"{n} {plural or singular + 's'}"


# ===========================================================================
# Section detection -- header-aware
# ===========================================================================

# A section is considered "present" when its canonical name appears at
# the START of a non-empty line (with optional leading numbering / bullets
# / bolding artifacts that pdfplumber emits). This is much stricter than
# a substring search and dramatically reduces false-positive matches
# like "this proposal includes a biographical sketch" -> matched
# "Biographical Sketch" inside body text.
# Leading list/numbering/label noise to strip off before comparing a line
# to a header name: "1." "1)" "1:" "(1)" "A." "b)" roman "IV." bullets, bold
# artifacts, and an optional "Section 3:" / "Part B." outline label.
_LEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:section|part|appendix|attachment|item)\s+[\w\-]+\s*[:.\)]\s+"
    r"|\d+[\.\):]\s+"
    r"|\(\d+\)\s+"
    r"|[a-z][\.\)]\s+"
    r"|[ivxlcdm]+[\.\)]\s+"
    r"|[•‣▪·\-\*]\s+"
    r"|<b>\s*|\*\*\s*"
    r")+",
    re.IGNORECASE,
)

# Minimal, SAFE name normalization so common synonyms still match without
# re-loosening into substring matching: "&" -> "and", whitespace collapse,
# and a tiny curated alias list.
_WS_RE = re.compile(r"\s+")
_NAME_ALIASES = (("biosketch", "biographical sketch"),)


def _norm(s: Optional[str]) -> str:
    s = (s or "").strip().lower().replace("&", " and ")
    s = _WS_RE.sub(" ", s).strip()
    for a, b in _NAME_ALIASES:
        s = s.replace(a, b)
    return s


def _header_match(line: str, target_norm: str) -> bool:
    """True only when `line` is a heading whose name is exactly
    `target_norm` (after stripping leading numbering / outline labels).
    The name must be the WHOLE header, not a prefix of a longer different
    one: what follows the name must not be another word/number. So
    "Budget" !~ "Budget Justification" and a TOC "... Plan 12" !~ "Plan",
    but "Summary:", "Plan (2 pages)", and "Sketch ..." all still match.
    No substring / anywhere-colon matching (that caused false passes)."""
    cand = _norm(_LEADING_RE.sub("", line.strip()))
    if cand == target_norm:
        return True
    if target_norm and cand.startswith(target_norm):
        rest = cand[len(target_norm):].lstrip()
        return rest == "" or not rest[0].isalnum()
    return False


def _section_present(text: str, name: str) -> bool:
    """Header-aware presence check. A section counts as present only when
    its name appears as an actual heading (start of a line, after optional
    numbering / outline labels), NOT as a substring of body prose, a
    table-of-contents entry, or a longer different header."""
    if not text:
        return False
    target = _norm(name)
    if not target:
        return False
    return any(_header_match(line, target)
               for line in text.splitlines() if line.strip())


# ===========================================================================
# Individual check functions (pure, unit-testable)
# ===========================================================================

def check_page_count(
    actual_pages: int,
    page_limits: Optional[dict],
) -> dict:
    """Compare the PDF's total page count to the document-wide limit
    declared in the solicitation. Most solicitations enumerate the
    Project Description cap as the dominating constraint; we use that
    one when available."""
    if not page_limits or not isinstance(page_limits, dict):
        return {
            "name": "Page count",
            "status": "skipped",
            "value": _plural(actual_pages, "page"),
            "detail": "No page limit in the solicitation -- nothing to check.",
        }
    priority_keys = ["project_description", "research_strategy",
                     "project description", "research strategy"]
    limit = None
    label = None
    for k in priority_keys:
        if k in page_limits:
            limit = page_limits[k]
            label = k
            break
    if limit is None:
        # Broaden to other DOCUMENT-WIDE cap names (NIH "research strategy",
        # generic "narrative" / "research plan"). Per-section caps (DMP,
        # biosketch, project summary) are intentionally NOT eligible here --
        # they are checked separately by check_per_section_page_limits.
        _DOC_HINTS = ("description", "research", "narrative", "research plan",
                      "project plan", "document", "overall", "total page")
        for k, v in page_limits.items():
            if any(h in k.lower() for h in _DOC_HINTS):
                limit = v
                label = k
                break
    if limit is None:
        # Only per-section limits were stated -> there is no document-wide
        # cap to check here. Do NOT compare the whole-document page count
        # against a single section's cap (that was a false-fail bug).
        return {
            "name": "Page count",
            "status": "skipped",
            "value": _plural(actual_pages, "page"),
            "detail": ("The solicitation states only per-section page limits; "
                       "those are checked separately."),
        }
    try:
        limit_int = int(limit)
    except (ValueError, TypeError):
        return {
            "name": "Page count",
            "status": "skipped",
            "value": _plural(actual_pages, "page"),
            "detail": f"Page limit '{limit}' isn't a number.",
        }
    if limit_int <= 0:
        return {
            "name": "Page count",
            "status": "skipped",
            "value": _plural(actual_pages, "page"),
            "detail": f"Ignoring a non-positive page limit ({limit_int}).",
        }
    status = "ok" if actual_pages <= limit_int else "fail"
    over_by = actual_pages - limit_int
    if status == "ok":
        detail = (f"Draft is {_plural(actual_pages, 'page')}; "
                  f"the solicitation caps {label.replace('_', ' ')} at "
                  f"{limit_int}.")
    else:
        detail = (f"Draft is {_plural(actual_pages, 'page')} -- "
                  f"{over_by} over the {limit_int}-page cap on "
                  f"{label.replace('_', ' ')}. Trim before submitting.")
    return {
        "name": "Page count",
        "status": status,
        "value": f"{actual_pages} / {limit_int}",
        "detail": detail,
    }


def check_required_attachments(
    text: str,
    required: list[str],
) -> dict:
    """For each required attachment from the solicitation, check
    whether a matching section / heading appears in the draft."""
    if not required:
        return {
            "name": "Required attachments",
            "status": "skipped",
            "value": "none listed",
            "detail": "The solicitation didn't enumerate required attachments.",
            "found": [],
            "missing": [],
        }
    found = []
    missing = []
    for att in required:
        if not att or not str(att).strip():
            continue
        if _section_present(text, str(att).strip()):
            found.append(att)
        else:
            missing.append(att)
    total = len(found) + len(missing)
    status = "ok" if not missing else "fail"
    if not missing:
        detail = "All listed attachments appear in the draft."
    else:
        miss = _plural(len(missing), "attachment")
        detail = (f"Solicitation requires {total} attachments; "
                  f"{miss} not found in the draft: "
                  f"{', '.join(missing)}.")
    return {
        "name": "Required attachments",
        "status": status,
        "value": f"{len(found)} of {total}",
        "detail": detail,
        "found": found,
        "missing": missing,
    }


def check_sponsor_default_sections(
    text: str,
    sponsor: Optional[str],
    suppress: Optional[set[str]] = None,
) -> dict:
    """Standard sponsor skeleton check. `suppress` is a set of section
    names already reported by another check (typically Required
    Attachments) -- those are silenced here so the user doesn't see the
    same missing item twice."""
    suppress_lc = {s.lower() for s in (suppress or set())}
    sections = _sponsor_default_sections(sponsor)
    found = [s for s in sections if _section_present(text, s)]
    missing_all = [s for s in sections if s not in found]
    missing = [s for s in missing_all if s.lower() not in suppress_lc]
    status = "ok" if not missing else "warn"
    label = f"Standard {sponsor or 'proposal'} sections"
    if not missing:
        if missing_all:
            detail = (f"All remaining standard sections detected "
                      f"(the others are already flagged above).")
        else:
            detail = "All standard sections detected."
    else:
        detail = (f"Conventional but not mandated for this solicitation: "
                  f"{', '.join(missing)}. Worth adding for reviewer "
                  f"familiarity.")
    return {
        "name": label,
        "status": status,
        "value": f"{len(found)} of {len(sections)} present",
        "detail": detail,
        "found": found,
        "missing": missing,
    }


_DOLLAR_RE = re.compile(
    r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand|mm|m|b|k)?\b",
    re.IGNORECASE,
)
_MAGNITUDE = {
    "K": 1_000, "THOUSAND": 1_000,
    "M": 1_000_000, "MM": 1_000_000, "MILLION": 1_000_000,
    "B": 1_000_000_000, "BILLION": 1_000_000_000,
}


def _largest_dollar_amount(text: str) -> Optional[int]:
    """Largest $-prefixed amount in the PDF text, used as a proxy for the
    proposal's total requested budget. Understands K / M / MM / B and the
    spelled-out 'thousand' / 'million' / 'billion' so an over-cap figure
    written as '$2.5 million' is NOT silently read as $2."""
    if not text:
        return None
    largest = 0.0
    found_any = False
    for m in _DOLLAR_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        suffix = (m.group(2) or "").upper()
        try:
            val = float(raw)
        except ValueError:
            continue
        val *= _MAGNITUDE.get(suffix, 1)
        if not math.isfinite(val) or val > 1e15:
            # implausible (digit run / id number); ignore rather than crash
            continue
        found_any = True
        if val > largest:
            largest = val
    return int(largest) if found_any else None


def check_budget_cap(
    text: str,
    budget_cap: Optional[int],
) -> dict:
    if not budget_cap:
        return {
            "name": "Budget vs cap",
            "status": "skipped",
            "value": "no cap set",
            "detail": "The solicitation didn't specify a budget cap.",
        }
    if not isinstance(budget_cap, (int, float)):
        return {
            "name": "Budget vs cap",
            "status": "skipped",
            "value": "no cap set",
            "detail": "Budget cap isn't a number; skipping the budget check.",
        }
    largest = _largest_dollar_amount(text)
    if largest is None:
        return {
            "name": "Budget vs cap",
            "status": "warn",
            "value": f"cap ${budget_cap:,}",
            "detail": (
                "No dollar amounts found in the draft. The budget "
                "section may be missing, or the PDF is image-only."
            ),
        }
    status = "ok" if largest <= budget_cap else "fail"
    if status == "fail":
        over = largest - budget_cap
        detail = (f"Largest figure in the draft is ${largest:,} -- "
                  f"${over:,} over the ${budget_cap:,} per-award cap. "
                  f"Trim before submitting.")
    elif largest == budget_cap:
        detail = (f"Largest figure (${largest:,}) is exactly at the cap. "
                  f"Reviewer scrutiny on tight budgets is high -- double-"
                  f"check the budget justification.")
    else:
        headroom = budget_cap - largest
        detail = (f"Largest figure in the draft is ${largest:,}; "
                  f"${headroom:,} under the ${budget_cap:,} cap.")
    return {
        "name": "Budget vs cap",
        "status": status,
        "value": f"${largest:,} / ${budget_cap:,}",
        "detail": detail,
    }


# ===========================================================================
# Per-section page limit checks (DMP, Biosketch, etc.)
# ===========================================================================

# Map page_limits keys to user-facing labels and matching section names
# we'll look for in the PDF text to identify where the section starts.
_PER_SECTION_LIMITS = {
    "data_management_plan": ("Data Management Plan", "data management plan"),
    "biosketch":             ("Biographical Sketch",    "biographical sketch"),
    "biographical_sketch":   ("Biographical Sketch",    "biographical sketch"),
    "project_summary":       ("Project Summary",        "project summary"),
}


def _estimate_section_pages(pages_text: list[str], section_name: str) -> Optional[int]:
    """Estimate how many pages a named section occupies, by finding the
    page where the section header first appears and counting forward
    until the next likely section header. Returns None if the section
    isn't found."""
    if not pages_text or not section_name:
        return None
    target = _norm(section_name)
    name_lc = section_name.lower()  # used by the forward-walk heuristic below
    start_page = None
    for i, page_text in enumerate(pages_text):
        if any(_header_match(line, target)
               for line in page_text.splitlines() if line.strip()):
            start_page = i
            break
    if start_page is None:
        return None
    # Walk forward; stop at the first page that starts with another
    # plausible section header (a non-empty line ending without
    # punctuation, shorter than 80 chars, and not the same section).
    end_page = len(pages_text)
    for j in range(start_page + 1, len(pages_text)):
        first_lines = [
            l.strip() for l in pages_text[j].splitlines() if l.strip()
        ][:3]
        if not first_lines:
            continue
        first = first_lines[0]
        is_new_section = (
            len(first) < 80
            and not first.endswith((".", ","))
            and name_lc not in first.lower()
            and first[0].isupper()
        )
        if is_new_section:
            end_page = j
            break
    return end_page - start_page


def check_per_section_page_limits(
    pages_text: list[str],
    page_limits: Optional[dict],
) -> list[dict]:
    """For each sub-section the solicitation caps (DMP, Biosketch, etc.)
    estimate its actual length and flag if over."""
    if not page_limits or not pages_text:
        return []
    results: list[dict] = []
    for key, (label, search_name) in _PER_SECTION_LIMITS.items():
        if key not in page_limits:
            continue
        try:
            cap = int(page_limits[key])
        except (ValueError, TypeError):
            continue
        actual = _estimate_section_pages(pages_text, search_name)
        if actual is None:
            results.append({
                "name": f"{label} length",
                "status": "warn",
                "value": f"not detected / {cap}",
                "detail": (
                    f"Couldn't locate a '{label}' section header in the "
                    f"draft -- the solicitation caps it at "
                    f"{_plural(cap, 'page')}."
                ),
            })
            continue
        status = "ok" if actual <= cap else "fail"
        if status == "ok":
            detail = (
                f"{label} appears to be {_plural(actual, 'page')}; "
                f"cap is {cap}."
            )
        else:
            detail = (
                f"{label} appears to span {_plural(actual, 'page')}, "
                f"over the {_plural(cap, 'page')} cap."
            )
        results.append({
            "name": f"{label} length",
            "status": status,
            "value": f"{actual} / {cap}",
            "detail": detail,
        })
    return results


# ===========================================================================
# Project Summary word count -- specific check, high signal
# ===========================================================================

# Tokens that mean "this is the Project Summary section" so we can find
# its body text.
_SUMMARY_HEADERS = ("project summary", "abstract", "specific aims")


def _extract_section_text(pages_text: list[str], header_name: str) -> str:
    """Pull out the body text of a named section. Goes from the header
    line to (a) the next plausible header OR (b) ~2000 chars later --
    enough for word counting without including the whole document."""
    full = "\n".join(pages_text)
    if not full:
        return ""
    lc = full.lower()
    idx = lc.find(header_name.lower())
    if idx == -1:
        return ""
    after = full[idx + len(header_name):]
    # Take the next ~2500 chars as a reasonable summary upper bound.
    return after[:2500]


def check_project_summary_wordcount(pages_text: list[str]) -> Optional[dict]:
    """NSF caps Project Summary at 1 page (~ 500 words). NIH Specific
    Aims is usually 1 page too. If we can find such a section, count
    its words and flag if grossly short (probably a stub) or grossly
    long (probably swallowed the Project Description)."""
    if not pages_text:
        return None
    for header in _SUMMARY_HEADERS:
        body = _extract_section_text(pages_text, header)
        if not body or len(body) < 20:
            continue
        words = len(body.split())
        # We're scanning 2500 chars max; usable wordcount range ~30-500
        # for a 1-page summary.
        if words < 30:
            status, detail = "warn", (
                f"'{header.title()}' section is only ~{words} words -- "
                f"looks like a stub. Most reviewers expect a self-"
                f"contained 1-page summary (~300-500 words)."
            )
        elif words > 700:
            status, detail = "warn", (
                f"'{header.title()}' appears to be ~{words}+ words -- "
                f"sponsors typically cap this at 1 page. Verify it "
                f"fits."
            )
        else:
            status, detail = "ok", (
                f"'{header.title()}' is ~{words} words -- a typical "
                f"length for a 1-page summary."
            )
        return {
            "name": "Project Summary length",
            "status": status,
            "value": f"~{words} words",
            "detail": detail,
        }
    return None


# ===========================================================================
# "This looks like a solicitation, not a draft" detector
# ===========================================================================

# Phrases that are highly characteristic of a sponsor's funding
# announcement (not a research proposal). Hit 3+ of these and we warn
# the user they may have uploaded the wrong PDF.
_SOLICITATION_HEADERS = (
    "program description",
    "program solicitation",
    "important dates",
    "award information",
    "submission portal",
    "page limitations",
    "required attachments",
    "anticipated funding amount",
    "estimated number of awards",
    "eligibility information",
    "deadline date",
    "proposals must be submitted via",
)


def check_looks_like_solicitation(text: str) -> Optional[dict]:
    """Return a warning check if the draft text looks more like the
    sponsor's solicitation announcement than a research proposal."""
    if not text:
        return None
    lc = text.lower()
    hits = [h for h in _SOLICITATION_HEADERS if h in lc]
    if len(hits) < 3:
        return None
    return {
        "name": "Draft sanity check",
        "status": "warn",
        "value": "may be wrong file",
        "detail": (
            f"This PDF looks like a SPONSOR SOLICITATION (matched "
            f"{len(hits)} solicitation-style headers), not a research "
            f"proposal draft. Double-check you uploaded YOUR draft and "
            f"not the funding announcement."
        ),
        "matched_headers": hits[:6],
    }


# ===========================================================================
# Overall verdict
# ===========================================================================

def _overall_verdict(counts: dict) -> dict:
    """Roll the per-check counts into one banner the UI can show at the
    top: ready / needs work / significant gaps. Severity ordered:
      - 1+ fail   -> "Critical issues" (red)
      - 2+ warn   -> "Needs review" (amber)
      - 1 warn    -> "Minor issues"
      - 0 fail/warn -> "Looks ready"
    """
    fail = counts.get("fail", 0)
    warn = counts.get("warn", 0)
    if fail >= 1:
        return {
            "level": "critical",
            "label": "Critical issues",
            "message": (
                f"{_plural(fail, 'check')} failing -- "
                f"fix before submitting."
            ),
        }
    if warn >= 2:
        return {
            "level": "needs_review",
            "label": "Needs review",
            "message": f"{_plural(warn, 'warning')} -- worth a second look.",
        }
    if warn == 1:
        return {
            "level": "minor",
            "label": "Minor issues",
            "message": "One warning to review. Otherwise looks solid.",
        }
    return {
        "level": "ready",
        "label": "Looks ready",
        "message": "All mechanical checks pass. Good to go.",
    }


# ===========================================================================
# Public API
# ===========================================================================

def critique_pdf(
    pdf_bytes: bytes,
    sponsor: Optional[str],
    solicitation: Optional[dict] = None,
) -> Optional[dict]:
    """One-shot: PDF bytes + sponsor + (optional) solicitation context
    dict -> structured critique. Returns None when the PDF can't be
    parsed at all.

    `solicitation` dict shape (any field may be missing/None):
        {
            "budget_cap": int | None,
            "page_limits": {section: int, ...} | {},
            "required_attachments": [str, ...] | [],
        }
    """
    text, page_count, pages_text = _extract_pdf(pdf_bytes)
    # No extractable text at all -> unreadable / scanned / image-only PDF
    # (even if page_count > 0). Return None so the endpoint surfaces the
    # friendly "couldn't read this PDF" message instead of a meaningless
    # "everything is missing" critique.
    if not text or not text.strip():
        return None

    sol = solicitation or {}
    checks: list[dict] = []

    # 0. Sanity: did you upload the right PDF at all? Surfaced FIRST
    #    when relevant so the user fixes the upload before reading
    #    the rest.
    sanity = check_looks_like_solicitation(text)
    if sanity is not None:
        checks.append(sanity)

    # 1. Page count (document-wide)
    checks.append(check_page_count(page_count, sol.get("page_limits")))

    # 2. Required attachments
    req_check = check_required_attachments(text, sol.get("required_attachments") or [])
    checks.append(req_check)

    # 3. Standard sponsor sections, with attachments already flagged by
    #    Required Attachments suppressed to avoid double-reporting.
    suppress = set(req_check.get("missing") or [])
    checks.append(check_sponsor_default_sections(text, sponsor, suppress=suppress))

    # 4. Project Summary word count (if a summary section is detectable)
    summary_check = check_project_summary_wordcount(pages_text)
    if summary_check is not None:
        checks.append(summary_check)

    # 5. Per-section page limits (DMP, Biosketch, etc.)
    checks.extend(check_per_section_page_limits(pages_text, sol.get("page_limits")))

    # 6. Budget
    checks.append(check_budget_cap(text, sol.get("budget_cap")))

    # Roll-up counts for the headline strip.
    counts = {"ok": 0, "warn": 0, "fail": 0, "skipped": 0}
    for c in checks:
        counts[c.get("status", "skipped")] = counts.get(c.get("status", "skipped"), 0) + 1
    issues = counts["warn"] + counts["fail"]

    verdict = _overall_verdict(counts)

    return {
        "pages": page_count,
        "sponsor": sponsor,
        "verdict": verdict,
        "checks": checks,
        "counts": counts,
        "issues": issues,
    }
