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

The deterministic checks (verdict/checks/counts) are LLM-free and AUTHORITATIVE:
every one is derived deterministically from the PDF text + the solicitation
context, so there's no hallucination risk -- if Draft Critic says "Biographical
Sketch: MISSING", the user can trust that string really is not in the document.

As of 2026-06-03 an ADVISORY Gemini layer (`_ai_review`) is appended as a separate
`ai_review` key: a plain-English review, semantic compliance judgments the rules
can't make, and rewrite suggestions. It is advisory ONLY -- it never alters the
deterministic verdict/checks/counts, and on any failure (or when the model is
unavailable) `ai_review` is simply None and the deterministic output is unchanged.
"""

from __future__ import annotations

import json
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
_NAME_ALIASES = (
    ("biosketch", "biographical sketch"),
    ("bibliography and references cited", "references cited"),
    ("data management and sharing plan", "data management plan"),
)


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
    if cand == target_norm or cand == target_norm + "s":  # allow a simple plural
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


# ---------------------------------------------------------------------------
# Synonym groups + running-header (positional, multi-page) presence
# ---------------------------------------------------------------------------

# Curated equivalence groups: a required / standard section counts as present
# if ANY member of its group appears. Members are compared after _norm(), so
# the _NAME_ALIASES collapses (biosketch -> biographical sketch, ...) still
# apply. This only EXPANDS matching within hand-picked groups -- it never
# loosens into generic substring matching, so "Budget" still != "Budget
# Justification".
_SECTION_EQUIVALENTS = (
    {"data management plan", "data management and sharing plan",
     "data management sharing plan", "data management sharing",
     "resource sharing plan", "resource sharing plans"},
    {"references cited", "bibliography and references cited", "references"},
    {"biographical sketch", "biosketch"},
)

# A trailing page-number token on a header line: "Research Strategy 80",
# "References Cited Page 30", "Budget Justification p 12".
_TRAILING_PAGENO_RE = re.compile(r"\s+(?:page|pg|pp)?\.?\s*\d{1,4}$", re.IGNORECASE)


def _equivalent_names(name: str) -> list[str]:
    """All section names equivalent to `name` (just itself if no group hits)."""
    n = _norm(name)
    for grp in _SECTION_EQUIVALENTS:
        if any(_norm(m) == n for m in grp):
            return list(grp)
    return [name]


def _running_header_present(pages_text: Optional[list[str]], name: str) -> bool:
    """True when `name` is the FIRST non-empty line of >=2 pages, allowing a
    trailing page number ("Research Strategy 80"). That is the signature of a
    running / continuation header -- which the strict matcher rejects -- and a
    table-of-contents entry CANNOT clear the >=2-page bar (it lists each
    section once), so this stays false-positive-safe."""
    if not pages_text:
        return False
    target = _norm(name)
    if not target:
        return False
    hits = 0
    for pt in pages_text:
        first = next((ln.strip() for ln in pt.splitlines() if ln.strip()), "")
        if not first:
            continue
        cand = _norm(_LEADING_RE.sub("", first))
        cand = _TRAILING_PAGENO_RE.sub("", cand).strip()
        if cand == target or cand == target + "s":
            hits += 1
            if hits >= 2:
                return True
    return False


def _present_single(text: str, pages_text: Optional[list[str]], name: str) -> bool:
    """One name: a clean header anywhere (strict) OR a running header (>=2 pages)."""
    return _section_present(text, name) or _running_header_present(pages_text, name)


def _section_present_pages(text: str, pages_text: Optional[list[str]],
                           name: str) -> bool:
    """Header-aware presence with two safe relaxations over `_section_present`:
    (1) synonym groups (Resource Sharing Plan == Data Management Plan, bare
    References == References Cited, ...) and (2) running/continuation headers
    that carry a page number. Falls back to pure-text behavior when
    `pages_text` is None."""
    return any(_present_single(text, pages_text, v)
               for v in _equivalent_names(name))


# ===========================================================================
# Individual check functions (pure, unit-testable)
# ===========================================================================

def check_page_count(
    actual_pages: int,
    page_limits: Optional[dict],
    pages_text: Optional[list[str]] = None,
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
    # When we have per-page text and the cap names a narrative section, measure
    # THAT section's span (reusing _estimate_section_pages) rather than the whole
    # document -- a full assembled package shouldn't fail a section page cap.
    measured = actual_pages
    scope_note = ""
    if pages_text:
        est = _estimate_section_pages(pages_text, label.replace("_", " "))
        if est is not None:
            measured = est
            scope_note = f" (measured the {label.replace('_', ' ')} section span)"

    status = "ok" if measured <= limit_int else "fail"
    over_by = measured - limit_int
    if status == "ok":
        detail = (f"{label.replace('_', ' ').capitalize()} is "
                  f"{_plural(measured, 'page')}; the solicitation caps it at "
                  f"{limit_int}.{scope_note}")
    else:
        detail = (f"{label.replace('_', ' ').capitalize()} is "
                  f"{_plural(measured, 'page')} -- {over_by} over the "
                  f"{limit_int}-page cap.{scope_note} Trim before submitting.")
    return {
        "name": "Page count",
        "status": status,
        "value": f"{measured} / {limit_int}",
        "detail": detail,
    }


def check_required_attachments(
    text: str,
    required: list[str],
    pages_text: Optional[list[str]] = None,
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
        if _section_present_pages(text, pages_text, str(att).strip()):
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
    pages_text: Optional[list[str]] = None,
) -> dict:
    """Standard sponsor skeleton check. `suppress` is a set of section
    names already reported by another check (typically Required
    Attachments) -- those are silenced here so the user doesn't see the
    same missing item twice."""
    suppress_lc = {s.lower() for s in (suppress or set())}
    sections = _sponsor_default_sections(sponsor)
    found = [s for s in sections if _section_present_pages(text, pages_text, s)]
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


_BUDGET_TOTAL_RE = re.compile(
    r"(?:total\s+(?:direct\s+)?(?:costs?|project\s+costs?|budget)"
    r"|total\s+amount(?:\s+requested)?"
    r"|amount\s+requested"
    r"|budget\s+total)"
    r"[^\$\d]{0,40}\$\s*([\d,]+(?:\.\d+)?)\s*"  # require a real '$' before the figure
    r"(million|billion|thousand|mm|m|b|k)?",
    re.IGNORECASE,
)


def _budget_total_amount(text: str) -> Optional[int]:
    """Largest amount attached to a 'Total ... costs/budget/amount requested'
    label -- a far better proxy for the requested budget than the single
    largest dollar figure in the document (which is often a stray market /
    population / id number)."""
    if not text:
        return None
    best = None
    for m in _BUDGET_TOTAL_RE.finditer(text):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        val *= _MAGNITUDE.get((m.group(2) or "").upper(), 1)
        if not math.isfinite(val) or val <= 0 or val > 1e12:
            continue
        if best is None or val > best:
            best = val
    return int(best) if best is not None else None


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
    # Prefer a figure tied to a "Total ... costs/budget" label; only fall back
    # to the single largest $ in the document when no labeled total is found.
    labeled = _budget_total_amount(text)
    largest = _largest_dollar_amount(text)
    figure = labeled if labeled is not None else largest

    if figure is None:
        return {
            "name": "Budget vs cap",
            "status": "warn",
            "value": f"cap ${budget_cap:,}",
            "detail": (
                "No dollar amounts found in the draft. The budget "
                "section may be missing, or the PDF is image-only."
            ),
        }
    # Fallback path only: a figure wildly above the cap is almost certainly NOT
    # the budget (market size, genome length, an id number). Warn, don't false-fail.
    if labeled is None and figure > budget_cap * 50:
        return {
            "name": "Budget vs cap",
            "status": "warn",
            "value": f"${figure:,}? / ${budget_cap:,}",
            "detail": (f"Largest $ figure in the draft is ${figure:,} -- far above "
                       f"the ${budget_cap:,} cap, so it's probably a stray number, "
                       f"not the budget total. Confirm the budget section."),
        }
    # Only a $0 (or nothing meaningful) could be read -- don't pass it off as
    # 'under cap'; the real total likely lives in a form field the text omits.
    if figure == 0:
        return {
            "name": "Budget vs cap",
            "status": "warn",
            "value": f"cap ${budget_cap:,}",
            "detail": ("Couldn't read a budget total (found only $0). The budget "
                       "may be in a form field the PDF text doesn't expose -- "
                       "verify manually."),
        }
    status = "ok" if figure <= budget_cap else "fail"
    if status == "fail":
        over = figure - budget_cap
        detail = (f"Budget figure in the draft is ${figure:,} -- "
                  f"${over:,} over the ${budget_cap:,} per-award cap. "
                  f"Trim before submitting.")
    elif figure == budget_cap:
        detail = (f"Budget figure (${figure:,}) is exactly at the cap. "
                  f"Reviewer scrutiny on tight budgets is high -- double-"
                  f"check the budget justification.")
    else:
        headroom = budget_cap - figure
        detail = (f"Budget figure in the draft is ${figure:,}; "
                  f"${headroom:,} under the ${budget_cap:,} cap.")
    return {
        "name": "Budget vs cap",
        "status": status,
        "value": f"${figure:,} / ${budget_cap:,}",
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


# Major sections that legitimately END another section's page span. Internal
# subheadings ("Significance", "Approach", "Aim 1") are deliberately NOT here,
# so a section's span isn't cut short at its own sub-parts.
_MAJOR_SECTION_NORMS = {
    _norm(s) for s in (
        list(_NSF_DEFAULT_SECTIONS) + list(_NIH_DEFAULT_SECTIONS)
        + list(_DOD_DEFAULT_SECTIONS) + [
            "Specific Aims", "Research Strategy", "Project Description",
            "Project Summary", "Project Narrative", "References Cited",
            "Bibliography and References Cited", "Biographical Sketch",
            "Budget Justification", "Facilities", "Equipment",
            "Protection of Human Subjects", "Human Subjects",
            "Vertebrate Animals", "Resource Sharing Plan",
            "Data Management Plan", "Authentication of Key Biological",
            "Letters of Support", "Multiple PD PI Leadership Plan",
            "Consortium", "Cover Letter", "Select Agent Research",
        ]
    )
}


def _first_line_section_norm(page_text: str) -> str:
    """Normalized first non-empty line of a page, with leading numbering and a
    trailing page number stripped -- so 'Research Strategy 80' -> 'research
    strategy'. Used to detect section starts/boundaries by header position."""
    first = next((ln.strip() for ln in page_text.splitlines() if ln.strip()), "")
    cand = _norm(_LEADING_RE.sub("", first))
    return _TRAILING_PAGENO_RE.sub("", cand).strip()


def _estimate_section_pages(pages_text: list[str], section_name: str) -> Optional[int]:
    """Estimate how many pages a named section occupies: find where its header
    first appears (a clean header anywhere on a page, OR a page-numbered running
    header as the page's first line) and count forward until the next MAJOR
    section header. Returns None if the section isn't found."""
    if not pages_text or not section_name:
        return None
    target = _norm(section_name)
    target_grp = {_norm(v) for v in _equivalent_names(section_name)}
    start_page = None
    for i, page_text in enumerate(pages_text):
        if (_first_line_section_norm(page_text) in target_grp
                or any(_header_match(line, target)
                       for line in page_text.splitlines() if line.strip())):
            start_page = i
            break
    if start_page is None:
        return None
    # Walk forward; stop only at a page whose FIRST line is a DIFFERENT major
    # section header (not this section's own continuation). Internal subheads
    # don't count, so the span isn't truncated at "Significance"/"Approach".
    end_page = len(pages_text)
    for j in range(start_page + 1, len(pages_text)):
        fl = _first_line_section_norm(pages_text[j])
        if fl and fl in _MAJOR_SECTION_NORMS and fl not in target_grp:
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

# ===========================================================================
# ADVISORY AI review layer (Gemini) -- additive, never authoritative.
# ===========================================================================

# Strict "rules of the road" passed as the model's SYSTEM INSTRUCTION (carries
# more weight than inline prompt text). The actual data (draft, solicitation,
# checks) is passed separately as the user content.
_AI_REVIEW_SYSTEM = """You are a senior research-program officer giving an ADVISORY second read of a draft grant proposal. You are NOT the compliance gate; your review appears in a separate "advisory" box beneath an authoritative rules engine.

ABSOLUTE RULES:
1. GROUND EVERYTHING IN THE DRAFT. Every statement you make about the proposal must be supported by the actual DRAFT_TEXT provided. Do not use outside knowledge, prior proposals, or assumptions about what is "usually" present.
2. QUOTE YOUR EVIDENCE. For any finding with status "addressed" or "partial" you MUST include an "evidence" field: a VERBATIM, word-for-word quote copied from DRAFT_TEXT (<=200 chars) that proves it. If you cannot find a real supporting quote, you are NOT allowed to make that claim -- drop it.
3. NEVER FABRICATE. Do not invent quotes, section names, numbers, or requirements. A quote you put in "evidence" MUST appear character-for-character in DRAFT_TEXT. Fabricated quotes are automatically detected and the entire finding is discarded.
4. RESPECT GROUND TRUTH. The DETERMINISTIC_CHECKS are verified facts. Never contradict them; never say a section passes that they marked missing/fail. Do not re-check page counts or mere section presence -- the rules already did that.
5. COMPARE TO THE SOLICITATION, ONLY WHAT'S GIVEN. You may judge the draft against SOLICITATION_CONTEXT (stated priorities, required attachments, budget cap). Never invent a solicitation requirement that isn't in SOLICITATION_CONTEXT.
6. WHEN IN DOUBT, SAY SO. If the draft text is too thin or ambiguous to judge something, use status "unclear" rather than guessing. "I can't tell from the draft" is a correct, valued answer -- never guess to fill space.
7. ABSENCE NEEDS NO QUOTE. For "missing" findings (something the draft lacks), evidence may be empty -- you cannot quote what isn't there -- but only claim "missing" after genuinely scanning for it.
8. The summary must only restate what your findings support -- introduce no new claims there.

Output ONLY a JSON object (no markdown, no prose) with EXACTLY:
{
  "summary": "2-4 sentence plain-English review a PI can act on.",
  "compliance_findings": [
    {"area": "<short label>", "status": "addressed|partial|missing|unclear", "detail": "<one sentence>", "evidence": "<verbatim DRAFT_TEXT quote, or empty string for missing/unclear>"}
  ],
  "suggestions": [
    {"section": "<section name>", "suggestion": "<one concrete, actionable rewrite/stub action>"}
  ]
}
At most 6 compliance_findings and at most 6 suggestions. status MUST be one of addressed|partial|missing|unclear."""

_AI_STATUSES = {"addressed", "partial", "missing", "unclear"}
_AI_DRAFT_CHAR_CAP = 120_000


def _norm_for_match(s: str) -> str:
    """Lowercase + collapse all whitespace runs, for substring matching an
    evidence quote against the draft (Gemini often re-spaces PDF text)."""
    return " ".join((s or "").lower().split())


def _verify_evidence(findings: list[dict], draft_text: str) -> list[dict]:
    """Deterministic anti-hallucination gate. Drops any finding whose claim
    isn't actually backed by the draft:
      - evidence quote present but NOT a substring of the draft  -> DROP (fabricated quote)
      - status addressed/partial with empty/missing evidence     -> DROP (claim without proof)
      - status missing/unclear with empty evidence               -> KEEP (can't quote an absence)
    This is what makes "only insights from the draft" a hard guarantee, not a
    polite request."""
    draft_norm = _norm_for_match(draft_text)
    kept: list[dict] = []
    for f in findings:
        status = f.get("status", "unclear")
        evidence = (f.get("evidence") or "").strip()
        if evidence:
            if _norm_for_match(evidence) not in draft_norm:
                continue  # fabricated quote -> drop the whole finding
        elif status in ("addressed", "partial"):
            continue  # asserted the draft does something but gave no proof -> drop
        kept.append(f)
    return kept


def _ai_review(
    draft_text: str,
    solicitation: dict,
    deterministic_checks: list[dict],
    sponsor: Optional[str],
) -> Optional[dict]:
    """Advisory Gemini review. Returns {summary, compliance_findings,
    suggestions} or None on ANY failure / unavailability. Never raises."""
    try:
        from services import gemini_client

        # Compact the checks so the model sees the verdicts without huge detail blobs.
        slim_checks = [
            {k: c.get(k) for k in ("name", "status", "value", "detail", "missing") if c.get(k) is not None}
            for c in (deterministic_checks or [])
        ]
        sol_ctx = {
            "sponsor": sponsor,
            "budget_cap": solicitation.get("budget_cap"),
            "page_limits": solicitation.get("page_limits"),
            "required_attachments": solicitation.get("required_attachments"),
        }
        # Data goes in the user content; the strict rules are the system prompt.
        draft = (draft_text or "")[:_AI_DRAFT_CHAR_CAP]
        prompt = (
            f"SPONSOR: {sponsor or 'Unknown'}"
            + "\n\nSOLICITATION_CONTEXT (JSON):\n" + json.dumps(sol_ctx, ensure_ascii=False)
            + "\n\nDETERMINISTIC_CHECKS (JSON, authoritative):\n" + json.dumps(slim_checks, ensure_ascii=False)
            + "\n\nDRAFT_TEXT:\n" + draft
        )
        data = gemini_client.generate_json(
            prompt, temperature=0.0, max_output_tokens=4096, timeout_s=20,
            system_instruction=_AI_REVIEW_SYSTEM,
        )
        if not isinstance(data, dict):
            return None

        # Defensive normalization -- never trust the model's shape verbatim.
        summary = data.get("summary")
        summary = summary.strip() if isinstance(summary, str) else ""

        findings = []
        for f in (data.get("compliance_findings") or [])[:6]:
            if not isinstance(f, dict):
                continue
            status = str(f.get("status", "unclear")).strip().lower()
            if status not in _AI_STATUSES:
                status = "unclear"
            findings.append({
                "area": str(f.get("area", "")).strip(),
                "status": status,
                "detail": str(f.get("detail", "")).strip(),
                "evidence": str(f.get("evidence", "")).strip(),
            })

        # HARD anti-hallucination gate: drop any finding the draft doesn't back.
        findings = _verify_evidence(findings, draft)

        suggestions = []
        for s in (data.get("suggestions") or [])[:6]:
            if not isinstance(s, dict):
                continue
            suggestions.append({
                "section": str(s.get("section", "")).strip(),
                "suggestion": str(s.get("suggestion", "")).strip(),
            })

        # If the model returned nothing usable, treat as no review.
        if not summary and not findings and not suggestions:
            return None
        return {"summary": summary, "compliance_findings": findings, "suggestions": suggestions}
    except Exception as e:
        print(f"   [DRAFT_CRITIC] AI review failed: {e}")
        return None


def critique_pdf(
    pdf_bytes: bytes,
    sponsor: Optional[str],
    solicitation: Optional[dict] = None,
    include_ai: bool = True,
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

    # 1. Page count (scoped to the narrative section when pages_text allows)
    checks.append(check_page_count(page_count, sol.get("page_limits"),
                                   pages_text=pages_text))

    # 2. Required attachments
    req_check = check_required_attachments(text, sol.get("required_attachments") or [],
                                           pages_text=pages_text)
    checks.append(req_check)

    # 3. Standard sponsor sections, with attachments already flagged by
    #    Required Attachments suppressed to avoid double-reporting.
    suppress = set(req_check.get("missing") or [])
    checks.append(check_sponsor_default_sections(text, sponsor, suppress=suppress,
                                                 pages_text=pages_text))

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

    # ADVISORY AI review -- computed AFTER and independently of the deterministic
    # result above, added as a sibling key. Guarded so it can never affect the
    # verdict/checks/counts; None when disabled, unavailable, or on failure.
    ai_review = None
    if include_ai:
        try:
            ai_review = _ai_review(text, sol, checks, sponsor)
        except Exception:
            ai_review = None

    return {
        "pages": page_count,
        "sponsor": sponsor,
        "verdict": verdict,
        "checks": checks,
        "counts": counts,
        "issues": issues,
        "ai_review": ai_review,
    }
