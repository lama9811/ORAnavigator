"""Solicitation extractor -- parses a sponsor PDF (NSF / NIH / DoD / DoE
/ foundation) into a structured dict that the Proposals tracker can use
to seed a Submission tuned to *that specific solicitation*, not just a
generic NSF/NIH template.

Pipeline (2026-09-03 rewrite -- "read every page, miss nothing"):
  PDF bytes
    -> pdfplumber PER-PAGE text extraction (page numbers preserved)
    -> SWEEP: pages are cut into fixed-size slices and each slice is sent
       to Gemini separately, in parallel. Every page lands in exactly ONE
       slice, so full coverage is guaranteed BY THE CODE, not by asking the
       model nicely to "read everything". Each slice returns every page
       limit / attachment / deadline / cap / eligibility / formatting rule
       it can see, with the page number and a verbatim quote.
    -> CONSOLIDATE: the merged findings (not the raw PDF) go to Gemini once
       more, which folds them into the strict JSON contract.
    -> deterministic coercion + source-quote verification.

Why not one big prompt? Measured on NSF 24-1 (216 pages, 748k chars):
  - old single pass, truncated at 250k chars ->  5 page limits found
  - single pass over the FULL text (gemini-3.6-flash) ->  6 page limits
    (it *reported* "pages_examined: 216" and still missed 25 of them --
    a model's self-report of coverage is not coverage)
  - this page-by-page sweep -> 31 page limits, plus per-proposal-type
    budget caps ($100k planning / $200k RAPID / $300k EAGER / $1M RAISE)
    each correctly tagged with what it applies to.

Privacy & safety:
  - The extractor NEVER auto-creates a Submission. The endpoint returns
    the extracted dict; the user confirms / edits / discards in the UI;
    a second call commits. This is the agent's recommended mitigation
    for Gemini extraction errors (a wrong deadline = a missed proposal).
  - source_quotes carries the verbatim text Gemini used so the UI can
    show "we got this from page 3: '...due June 12, 2026...'" -- the
    user can see what's being trusted.
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import List, Optional, Tuple

# Lazy import: pdfplumber adds startup cost; we only need it on demand.
_pdfplumber = None
_genai = None


# ============================================================================
# Model configuration
# ============================================================================
# gemini-3.6-flash is only published in the "global" Vertex location -- it
# 404s in us-central1 (verified against this project 2026-09-03). So the
# model and the location move together. If the primary pair is unavailable
# for any reason we fall back to the previous pair rather than failing the
# user's upload.
_MODEL = os.getenv("SOLICITATION_MODEL", "gemini-3.6-flash")
_LOCATION = os.getenv("SOLICITATION_LOCATION", "global")
_FALLBACK_MODEL = os.getenv("SOLICITATION_FALLBACK_MODEL", "gemini-2.5-flash")
_FALLBACK_LOCATION = os.getenv("SOLICITATION_FALLBACK_LOCATION", "us-central1")

_clients: dict = {}          # location -> client (or None if init failed)


def _get_pdfplumber():
    global _pdfplumber
    if _pdfplumber is None:
        import pdfplumber  # type: ignore
        _pdfplumber = pdfplumber
    return _pdfplumber


def _get_client(location: str):
    """Reuse the codebase's Vertex-first / API-key-fallback pattern for
    Gemini. One cached client per location."""
    global _genai
    if location in _clients:
        return _clients[location]
    client = None
    try:
        from google import genai
        _genai = genai
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or "infra-vertex-494621-v1"
        try:
            client = genai.Client(vertexai=True, project=project,
                                  location=location)
        except Exception:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if api_key:
                client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"   [SOLICITATION] Gemini client init failed ({location}): {e}")
    _clients[location] = client
    return client


# ============================================================================
# JSON contract
# ============================================================================
# Every extracted dict has exactly these top-level keys. Frontend renders
# them in this order; missing values are surfaced as empty inputs so the
# user can fill them in.
#
# The first 10 keys are the ORIGINAL contract and must not be renamed or
# reordered -- proposals_service, draft_critic and the upload modal all key
# on them. The keys after them were added 2026-09-03 and are additive: they
# carry the detail that used to be discarded (per-proposal-type variants,
# formatting rules, page provenance, coverage proof).

_CONTRACT_KEYS = (
    "sponsor", "program_id", "program_name", "deadline",
    "page_limits", "required_attachments", "eligibility",
    "budget_cap", "submission_portal", "source_quotes",
    # --- additive (2026-09-03) ---
    "formatting",            # {font, margins, line_spacing} -- Draft Critic checks these
    "page_limit_variants",   # [{section, pages, applies_to, page}] special proposal types
    "budget_cap_variants",   # [{amount, applies_to, page}]
    "deadline_notes",        # free text: recurring windows, LOI dates, time-of-day rule
    "source_pages",          # {field: page number the value came from}
    # Catch-all for every MUST/MUST-NOT that fits no other field. Added
    # 2026-09-03 after a measured recall audit of NSF 23-598: only 41% of the
    # solicitation's 34 hard requirements survived, and EVERY loss traced to
    # the same cause -- no slot. 10 of them were never even reported by the
    # sweep because its schema had nowhere to put them.
    "other_requirements",    # [{requirement, category, applies_to, page, quote}]
)


# ---------------------------------------------------------------------------
# SWEEP prompt -- runs once per slice of pages.
# ---------------------------------------------------------------------------
# Deliberately asks for EVERYTHING with no de-duplication and no "pick the
# right one" judgement. Choosing between competing values is the
# consolidator's job; a sweep that filters is a sweep that loses data.
_SWEEP_SYSTEM = """You are scanning ONE SLICE of a research grant funding announcement (solicitation / FOA / agency proposal guide) for a university grants office. The slice is plain text split into pages, each beginning with a marker like [[PAGE 57]].

ACCURACY IS CRITICAL: a missed page limit, deadline, or budget cap can cost a faculty member an entire grant.

METHOD -- MANDATORY:
Walk EVERY page in this slice, in order, from the first page marker to the last. Do not skim. Do not stop early. Do not summarize. For each page in turn, ask: does this page state a page limit, a required or conditionally-required document, a deadline or submission window, a per-award dollar cap, an eligibility rule, or a formatting rule (font, type size, margins, line spacing, paper size)? Record every one you find and move to the next page. Only when you have reached the last page of the slice do you write your JSON.

REPORT EVERYTHING:
- A required attachment is ANY component the proposal must contain, however it is labeled: a headed section in the preparation instructions (e.g. "PROJECT SUMMARY:", "PROJECT DESCRIPTION:", "BUDGET AND BUDGET JUSTIFICATION:", "SUPPLEMENTARY DOCUMENTS:"), a letter, a plan, a form, or a separately submitted item such as a Letter of Intent or preliminary proposal. Report EACH ONE, and report it EVEN IF the solicitation defers its detailed content to an agency-wide guide ("follow the directions in the PAPPG") -- naming the component is what makes it required.
- Report a rule even if it looks minor, repeated, or restated from elsewhere. Duplicates are fine and expected -- they are removed later.
- Report rules that apply only to a SPECIAL proposal type (RAPID, EAGER, RAISE, planning, conference, workshop, equipment, travel, Ideas Lab, renewal, supplement, preliminary proposal, collaborative, subaward). These are the ones that get missed. Always fill "applies_to" so the rule can never be mistaken for the general case.
- Report rules from checklists, tables, appendices and summary-of-changes sections too, not only from prose.
- **NEVER DISCARD A RULE BECAUSE IT HAS NO OBVIOUS HOME.** If the proposer MUST or MUST NOT do something and it does not fit any category above, put it in "other_requirements". That list is the catch-all and it exists precisely so nothing is lost: percentage limits on a budget line ("no more than 30% for equipment"), prohibited or mandatory costs ("voluntary committed cost sharing is prohibited", "budget for the PI to attend the grantee meeting"), limits on how many proposals or awards a person or organization may have, character or word limits, required title prefixes or naming conventions, who must submit ("submission by an Authorized Organizational Representative is required"), caps on numbers of personnel or collaborating organizations, content that must appear inside a named component (e.g. "the Project Summary must include the LOI number", "the Project Description must contain a separate section labeled Broader Impacts", "the Letter of Intent must list the PI and co-PI names, departments, phone numbers and email addresses") -- report each such rule separately, and routing/portal conditions. When in doubt, report it.

ABSOLUTE RULES:
1. USE ONLY THE TEXT IN THIS SLICE. No outside knowledge, no memory of other NSF/NIH programs, no assumptions about "typical" values. If this slice does not state it, it does not exist.
2. EVERY item MUST carry a "quote" that is a VERBATIM, character-for-character substring of this slice (<=200 chars) and the "page" number of the [[PAGE n]] marker it came from. No quote, no item.
3. NEVER invent, paraphrase, normalize, or fix typos inside a quote.
4. If a category has nothing in this slice, return an empty list for it. An empty list is a correct answer.
5. "pages" and "amount" are plain integers. Write dollar amounts with no commas or symbols ("200000", not "$200,000"). Spelled-out numbers become digits ("five pages" -> 5).
6. budget_caps means the maximum PER PROPOSAL / PER AWARD (state the period in applies_to if it is per-year). NEVER the total program budget or the anticipated number of awards.

Return ONLY this JSON object:
{
  "identity": {"sponsor": str|null, "program_id": str|null, "program_name": str|null, "submission_portal": str|null},
  "page_limits": [{"section": str, "pages": int, "applies_to": str, "page": int, "quote": str}],
  "required_attachments": [{"name": str, "conditional": bool, "applies_to": str, "page": int, "quote": str}],
  "deadlines": [{"text": str, "iso": str|null, "applies_to": str, "page": int, "quote": str}],
  "budget_caps": [{"amount": int, "applies_to": str, "page": int, "quote": str}],
  "eligibility": [{"text": str, "page": int, "quote": str}],
  "formatting": [{"rule_type": "font"|"margins"|"line_spacing"|"other", "value": str, "page": int, "quote": str}],
  "other_requirements": [{"requirement": str, "category": "budget"|"eligibility"|"submission"|"content"|"process"|"other", "applies_to": str, "page": int, "quote": str}]
}
No prose, no markdown fences."""


# ---------------------------------------------------------------------------
# CONSOLIDATE prompt -- runs once over the merged sweep findings.
# ---------------------------------------------------------------------------
_CONSOLIDATE_SYSTEM = """You are a university grants officer consolidating verified findings that were swept page-by-page out of ONE funding announcement. Your input is a JSON list of findings, each already carrying a verbatim quote and the page it came from. Fold them into one clean record.

You are NOT reading the PDF. You may use ONLY the findings given to you. Never add a value that is not in the findings. Never invent a quote -- copy quotes verbatim from the findings.

HOW TO CHOOSE:
1. THE HEADLINE VALUE IS THE STANDARD FULL PROPOSAL. page_limits and budget_cap must describe an ordinary full research proposal to this program. A limit or cap that a finding ties to a SPECIAL proposal type (RAPID, EAGER, RAISE, planning, conference, workshop, equipment, travel, Ideas Lab, renewal, supplement, preliminary proposal, subaward, or a specific directorate) does NOT belong in the headline -- put it in page_limit_variants / budget_cap_variants with its applies_to text. Never let a special-type value overwrite the general one. (Example: if the Project Description is 15 pages generally but 2 pages for an Ideas Lab preliminary proposal, the headline is 15 and the 2 goes in variants.)
2. If findings genuinely disagree for the SAME standard case, take the most restrictive (smallest) and note the conflict in deadline_notes or the quote.
3. If NO finding states a general-case value -- only special-type ones -- the headline is null and everything goes to variants. A null is better than a wrong number.
4. deadline = the FULL PROPOSAL due date, as an ISO date. If the findings give a specific calendar date for the full proposal you MUST return it -- including when the solicitation ALSO states a recurring pattern ("third Tuesday in October, annually thereafter"), and including when the date has already passed. Return null ONLY when no specific full-proposal calendar date appears anywhere in the findings. NEVER put a Letter of Intent or preliminary-proposal date in `deadline` -- that is a different, earlier deadline. NEVER invent a date that is not in the findings.
4b. deadline_notes MUST carry, whenever the findings state them: the time-of-day and time-zone cutoff (e.g. "due by 5 p.m. submitter's local time"), every Letter of Intent / preliminary-proposal date, and every recurrence pattern. A due date without its cutoff time is a trap -- a proposal submitted at 8 p.m. on the due date is late.
5. required_attachments: the documents a standard full proposal must include. Merge duplicates and near-duplicates into one canonical name. Include conditionally-required items. KEEP the standard components the solicitation itself names (Project Summary, Project Description, Budget and Budget Justification, References Cited, Biographical Sketches, and any letter/plan/supplementary document) even when it defers their content to an agency-wide guide -- the grants office checks the draft against this list, so a component missing from it is a component nobody checks. Exclude only items that apply solely to a special proposal type or to post-award reporting.
6. budget_cap is the maximum per proposal/award, never the total program budget.
7. formatting: the font, margin and line-spacing rules for the proposal body.
8. other_requirements: KEEP EVERY ONE of the other_requirements findings. Merge only exact duplicates; never drop one for being minor, and never drop one because it overlaps a field above. This list is the safety net that stops a real rule from vanishing just because the record has no column for it -- a dropped rule is a rule nobody checks. Write each `requirement` as one short imperative sentence the proposer can act on, and carry its quote and page through unchanged.
9. eligibility: one or two sentences covering BOTH who may SUBMIT (the organization) AND who may serve as PI, when the findings state both -- "our university is eligible" is useless to a faculty member who personally is not. Its source_quotes entry must be ONE single verbatim sentence copied from a finding -- not several sentences stitched together.

Return ONLY this JSON object:
{
  "sponsor": one of "NSF" | "NIH" | "DoD" | "DoE" | "NASA" | "USDA" | "EPA" | "NOAA" | "State of Maryland" | "Internal", OR for any other funder the FULL organization name exactly as written (e.g. "Alfred P. Sloan Foundation") -- never the bare word "Foundation",
  "program_id": short program identifier as it appears (e.g. "NSF 23-573", "PA-24-001") or null,
  "program_name": short human-readable name or null,
  "deadline": "2026-06-12T17:00:00-05:00" or "2026-06-12" or null,
  "deadline_notes": short text describing recurring windows / LOI dates / time-of-day rules, or null,
  "page_limits": {"<section_snake_case>": <int>},
  "page_limit_variants": [{"section": str, "pages": int, "applies_to": str, "page": int}],
  "required_attachments": [str],
  "eligibility": str|null,
  "budget_cap": int|null,
  "budget_cap_variants": [{"amount": int, "applies_to": str, "page": int}],
  "submission_portal": str|null,
  "formatting": {"font": str|null, "margins": str|null, "line_spacing": str|null},
  "other_requirements": [{"requirement": str, "category": str, "applies_to": str, "page": int, "quote": str}],
  "source_quotes": {"<field>": "<verbatim quote copied from a finding>"},
  "source_pages": {"<field>": <int page number>}
}
For page_limits, source_quotes["page_limits"] may be an object mapping each section name to its own verbatim quote.
No prose, no markdown fences."""


def _call_gemini(prompt_text: str, system_instruction: Optional[str] = None,
                 max_output_tokens: int = 8192) -> str:
    """Single Gemini round-trip against the primary model/location, falling
    back to the previous pair if that fails. Returns the raw response text
    (may contain markdown fences; _parse_response handles that), or an empty
    string so callers can fail gracefully.

    When `system_instruction` is given, the rules are sent as the model's
    system prompt and `prompt_text` carries only the data -- stronger
    rule-adherence than inlining the rules into the content."""
    # response_mime_type=application/json forces clean JSON output (no
    # markdown fences, no preamble) so _parse_response doesn't have to guess.
    # max_output_tokens is generous because Gemini spends part of the output
    # budget on implicit reasoning and silently truncates JSON mid-document
    # when the budget is tight.
    config = {
        "temperature": 0.0,
        "max_output_tokens": max_output_tokens,
        "response_mime_type": "application/json",
    }
    if system_instruction:
        config["system_instruction"] = system_instruction

    attempts = [(_MODEL, _LOCATION)]
    if (_FALLBACK_MODEL, _FALLBACK_LOCATION) != (_MODEL, _LOCATION):
        attempts.append((_FALLBACK_MODEL, _FALLBACK_LOCATION))

    last_err = None
    for model, location in attempts:
        client = _get_client(location)
        if client is None:
            last_err = f"client init failed ({location})"
            continue
        try:
            response = client.models.generate_content(
                model=model, contents=prompt_text, config=config,
            )
            raw = (response.text or "").strip()
            preview = raw[:160].replace("\n", "\\n")
            print(f"   [SOLICITATION] {model}@{location} OK, "
                  f"len={len(raw)}, preview={preview}")
            return raw
        except Exception as e:
            last_err = e
            print(f"   [SOLICITATION] {model}@{location} failed: "
                  f"{str(e)[:200]}")
    print(f"   [SOLICITATION] all models failed: {last_err}")
    return ""


def _parse_response(raw: str) -> Optional[dict]:
    """Strip markdown fences, parse JSON. Returns None on malformed
    input so the caller can surface a graceful error."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        # ```json\n...\n```  or  ```\n...\n```
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        # strict=False tolerates literal control characters inside strings
        # (pdfplumber sometimes emits e.g. \x1f from ligature glyphs, which
        # Gemini echoes into source_quotes -> default json.loads rejects it).
        parsed = json.loads(text, strict=False)
    except json.JSONDecodeError as e:
        # Diagnostic: dump the first 400 chars of the offending text so
        # we can see HOW Gemini broke the contract (preamble text? trailing
        # commas? truncated mid-JSON?). Without this the endpoint just
        # returns 422 with no hint of what went wrong.
        snippet = text[:400].replace("\n", "\\n")
        print(f"   [SOLICITATION] JSON parse failed at pos {e.pos}: {snippet}")
        return None
    if not isinstance(parsed, dict):
        print(f"   [SOLICITATION] Parsed but not a dict: {type(parsed).__name__}")
        return None
    return parsed


def _coerce_budget(raw) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        # Strip commas, currency symbols, whitespace; pull the first integer.
        cleaned = re.sub(r"[^\d]", "", raw)
        if cleaned:
            try:
                return int(cleaned)
            except ValueError:
                return None
    return None


# Map a full sponsor name back to the canonical short token the rest of the
# app keys on (get_template + draft_critic._sponsor_default_sections expect
# exactly "NSF"/"NIH"/"DoD"/"DoE"/...). Gemini may return "National Science
# Foundation" or "Department of Energy"; without this, sponsor-specific
# templates/sections silently fall back to generic. Real foundations / unknown
# funders keep their full name. Matches full names by substring (specific) or
# the bare abbreviation by exact match (so it can't false-fire inside a
# foundation name).
_SPONSOR_FULLNAMES = (
    ("national science foundation", "NSF"),
    ("national institutes of health", "NIH"),
    ("department of defense", "DoD"),
    ("defense advanced research projects", "DoD"),
    ("office of naval research", "DoD"),
    ("department of energy", "DoE"),
    ("national aeronautics and space", "NASA"),
    ("department of agriculture", "USDA"),
    ("environmental protection agency", "EPA"),
    ("national oceanic and atmospheric", "NOAA"),
    ("state of maryland", "State of Maryland"),
)
_SPONSOR_ABBREVS = {"nsf": "NSF", "nih": "NIH", "dod": "DoD", "doe": "DoE",
                    "nasa": "NASA", "usda": "USDA", "epa": "EPA", "noaa": "NOAA"}


def _canon_sponsor(s):
    """Canonicalize a sponsor to the token downstream code expects; keep the
    full name for foundations / unknown funders."""
    if not isinstance(s, str) or not s.strip():
        return s
    low = s.strip().lower()
    for name, canon in _SPONSOR_FULLNAMES:
        if name in low:
            return canon
    if low in _SPONSOR_ABBREVS:        # exact bare abbreviation, e.g. "nsf"
        return _SPONSOR_ABBREVS[low]
    if low.startswith("de-foa"):       # DOE FOA number used as the sponsor
        return "DoE"
    return s.strip()


def _clean_key(k) -> str:
    """Sanitize a page_limits key: the notes round-trip in proposals_service
    is comma/colon separated, so those characters would corrupt a neighbour."""
    key = re.sub(r"[,:;]+", " ", str(k))
    return re.sub(r"\s+", " ", key).strip()


def _first_int(v) -> Optional[int]:
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None


def _coerce_extracted(raw: dict) -> dict:
    """Normalize whatever Gemini returned into the strict contract shape
    so the frontend can render every field without conditional checks."""
    out = {}
    for k in _CONTRACT_KEYS:
        out[k] = raw.get(k)

    # page_limits must be a dict of {clean_key: positive_int}. Gemini may
    # return "15 pages" / "15-20" / 2.5 as a value, or a key containing
    # commas/colons -- the downstream notes round-trip needs clean integer
    # values and separator-free keys, otherwise a stated limit is silently
    # lost or corrupts a neighboring key.
    raw_pl = out["page_limits"] if isinstance(out["page_limits"], dict) else {}
    clean_pl = {}
    for k, v in raw_pl.items():
        key = _clean_key(k)
        iv = _first_int(v)
        if key and iv and iv > 0:
            clean_pl[key] = iv
    out["page_limits"] = clean_pl

    # required_attachments must be a list of strings
    ra = out["required_attachments"]
    if ra is None:
        out["required_attachments"] = []
    elif isinstance(ra, str):
        out["required_attachments"] = [ra]
    elif isinstance(ra, list):
        out["required_attachments"] = [str(x) for x in ra if x]
    else:
        out["required_attachments"] = []

    # budget_cap to int when possible
    out["budget_cap"] = _coerce_budget(out["budget_cap"])

    # source_quotes / source_pages must be dicts
    if not isinstance(out["source_quotes"], dict):
        out["source_quotes"] = {}
    # source_pages must be {field: positive int}. The model sometimes mirrors
    # the per-entry shape of source_quotes and returns an OBJECT here (one page
    # per page-limit section). The frontend renders this value directly, and a
    # bare object as a React child crashes the whole review modal -- verified
    # live 2026-09-03. Flatten to a single int, or drop the entry.
    raw_pages = out["source_pages"] if isinstance(out["source_pages"], dict) else {}
    clean_pages = {}
    for k, v in raw_pages.items():
        if isinstance(v, dict):
            # Per-entry pages: keep the earliest, which is where the field's
            # headline value is stated.
            nums = [n for n in (_first_int(x) for x in v.values()) if n and n > 0]
            iv = min(nums) if nums else None
        elif isinstance(v, list):
            nums = [n for n in (_first_int(x) for x in v) if n and n > 0]
            iv = min(nums) if nums else None
        else:
            iv = _first_int(v)
        if iv and iv > 0:
            clean_pages[str(k)] = iv
    out["source_pages"] = clean_pages

    # formatting is a fixed 3-key dict so the frontend and Draft Critic can
    # read it without conditionals.
    fmt = out["formatting"] if isinstance(out["formatting"], dict) else {}
    out["formatting"] = {
        "font": _str_or_none(fmt.get("font")),
        "margins": _str_or_none(fmt.get("margins")),
        "line_spacing": _str_or_none(fmt.get("line_spacing")),
    }

    # Catch-all requirements: keep every row that carries an actual rule and a
    # supporting quote. Deliberately permissive about `category`/`page` -- the
    # whole point of this list is that nothing gets dropped on a technicality.
    out["other_requirements"] = _clean_requirements(out["other_requirements"])

    # Variant lists: keep only well-formed rows so downstream rendering and
    # the Draft Critic never have to defend against half-filled dicts.
    out["page_limit_variants"] = _clean_variants(
        out["page_limit_variants"], "section", "pages")
    out["budget_cap_variants"] = _clean_variants(
        out["budget_cap_variants"], None, "amount")

    # Empty strings should be None for cleaner UI
    for k in ("sponsor", "program_id", "program_name", "deadline",
              "eligibility", "submission_portal", "deadline_notes"):
        if isinstance(out[k], str) and not out[k].strip():
            out[k] = None

    # Canonicalize the sponsor token so downstream template/section selection
    # works whether Gemini returned "NSF" or "National Science Foundation".
    out["sponsor"] = _canon_sponsor(out["sponsor"])

    return out


def _str_or_none(v) -> Optional[str]:
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


_REQUIREMENT_CATEGORIES = {"budget", "eligibility", "submission", "content",
                           "process", "format", "other"}


def _clean_requirements(rows) -> list:
    """Normalize the catch-all requirements list.

    Keeps a row when it has a real rule sentence AND a quote -- the quote is
    what makes it checkable, and an unquoted "requirement" is exactly the kind
    of invention this pipeline exists to prevent. Everything else about the row
    is best-effort, because dropping a genuine rule over a malformed category
    would defeat the purpose of the list."""
    if not isinstance(rows, list):
        return []
    out, seen = [], set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        text = _str_or_none(r.get("requirement"))
        quote = _str_or_none(r.get("quote"))
        if not text or not quote:
            continue
        key = " ".join(text.lower().split())[:120]
        if key in seen:
            continue
        seen.add(key)
        cat = (_str_or_none(r.get("category")) or "other").lower()
        out.append({
            "requirement": text[:400],
            "category": cat if cat in _REQUIREMENT_CATEGORIES else "other",
            "applies_to": (_str_or_none(r.get("applies_to")) or "")[:200],
            "page": _first_int(r.get("page")),
            "quote": quote[:300],
        })
    return out


def _clean_variants(rows, name_key: Optional[str], num_key: str) -> list:
    """Normalize a variants list. Drops rows with no usable number, and rows
    with no applies_to text -- a variant that doesn't say what it applies to
    is indistinguishable from the general case and is therefore dangerous."""
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        num = _first_int(r.get(num_key))
        applies = _str_or_none(r.get("applies_to"))
        if not num or num <= 0 or not applies:
            continue
        row = {num_key: num, "applies_to": applies[:200]}
        if name_key:
            row[name_key] = _clean_key(r.get(name_key) or "")
            if not row[name_key]:
                continue
        pg = _first_int(r.get("page"))
        row["page"] = pg
        out.append(row)
    return out


# ============================================================================
# Page-by-page sweep
# ============================================================================

# Characters of solicitation text per sweep slice. ~90k chars is roughly 25
# PDF pages -- small enough that the model reliably attends to every page in
# the slice, large enough that a 216-page guide needs only 9 calls.
_SLICE_CHARS = int(os.getenv("SOLICITATION_SLICE_CHARS", "90000"))

# Hard ceiling on slices so a pathological upload can't fan out into hundreds
# of Gemini calls -- and, more importantly, so the request cannot outlive Cloud
# Run's 300s timeout. 40 slices ~= 3.6M characters ~= a 1000-page document, far
# beyond any real solicitation; at _SWEEP_WORKERS concurrency that is 4 waves,
# which measured ~30s each. Anything past the ceiling is reported in the
# coverage block ("pages_skipped") rather than silently dropped.
#
# Measured for reference: NSF 24-1 (216 pages, 4.6MB) = 9 slices, 128s total
# end-to-end (16s pdfplumber + ~60s sweep + ~50s consolidate). A typical
# 10-40 page program solicitation is 1-2 slices and finishes in ~25-35s.
_MAX_SLICES = int(os.getenv("SOLICITATION_MAX_SLICES", "40"))

# Slices run concurrently; they are independent read-only calls.
_SWEEP_WORKERS = int(os.getenv("SOLICITATION_SWEEP_WORKERS", "10"))

_FINDING_KEYS = ("page_limits", "required_attachments", "deadlines",
                 "budget_caps", "eligibility", "formatting",
                 "other_requirements")


def _build_slices(pages: List[Tuple[Optional[int], str]]) -> List[List[Tuple[Optional[int], str]]]:
    """Group (page_number, text) pairs into slices of at most _SLICE_CHARS.

    A single page larger than the slice budget still gets its own slice
    rather than being split, so a page marker always introduces the whole
    page. Blank pages are skipped -- they carry no rules and only dilute
    the model's attention."""
    slices: List[List[Tuple[Optional[int], str]]] = []
    current: List[Tuple[Optional[int], str]] = []
    size = 0
    for pageno, text in pages:
        if not text or not text.strip():
            continue
        if current and size + len(text) > _SLICE_CHARS:
            slices.append(current)
            current, size = [], 0
        current.append((pageno, text))
        size += len(text)
    if current:
        slices.append(current)
    return slices


def _render_slice(sl: List[Tuple[Optional[int], str]]) -> str:
    parts = []
    for pageno, text in sl:
        parts.append(f"[[PAGE {pageno}]]\n{text}" if pageno else text)
    return "\n\n".join(parts)


def _sweep_slice(sl: List[Tuple[Optional[int], str]]) -> Optional[dict]:
    """One slice -> findings dict, or None if the call/parse failed."""
    first = sl[0][0]
    last = sl[-1][0]
    header = (f"SLICE: pages {first}-{last}\n\n" if first and last else "")
    raw = _call_gemini(header + _render_slice(sl),
                       system_instruction=_SWEEP_SYSTEM,
                       max_output_tokens=32768)
    return _parse_response(raw)


def _sweep(pages: List[Tuple[Optional[int], str]]) -> Tuple[dict, dict]:
    """Run the page-by-page sweep over every page.

    Returns (findings, coverage). `findings` merges every slice's lists.
    `coverage` is the honest audit trail -- how many pages existed, how many
    were actually sent to the model, and how many slices failed -- so the UI
    can say "read 216 of 216 pages" instead of asking the user to trust us."""
    slices = _build_slices(pages)
    truncated = 0
    if len(slices) > _MAX_SLICES:
        truncated = sum(len(s) for s in slices[_MAX_SLICES:])
        slices = slices[:_MAX_SLICES]

    findings = {k: [] for k in _FINDING_KEYS}
    identity: List[dict] = []
    failed = 0

    if not slices:
        return findings, {"pages_total": len(pages), "pages_read": 0,
                          "slices": 0, "slices_failed": 0,
                          "pages_skipped": truncated, "identity": identity}

    if len(slices) == 1:
        results = [_sweep_slice(slices[0])]
    else:
        workers = max(1, min(_SWEEP_WORKERS, len(slices)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_sweep_slice, slices))

    pages_read = 0
    for sl, res in zip(slices, results):
        if not isinstance(res, dict):
            failed += 1
            continue
        pages_read += len(sl)
        ident = res.get("identity")
        if isinstance(ident, dict):
            identity.append(ident)
        for k in _FINDING_KEYS:
            v = res.get(k)
            if isinstance(v, list):
                findings[k].extend(x for x in v if isinstance(x, dict))

    coverage = {
        "pages_total": len([p for p in pages if p[1] and p[1].strip()]),
        "pages_read": pages_read,
        "slices": len(slices),
        "slices_failed": failed,
        "pages_skipped": truncated,
        "identity": identity,
    }
    print(f"   [SOLICITATION] sweep: {pages_read}/{coverage['pages_total']} pages, "
          f"{len(slices)} slices ({failed} failed), "
          + ", ".join(f"{k}={len(findings[k])}" for k in _FINDING_KEYS))
    return findings, coverage


# Per-category caps on what we hand the consolidator. A 200-page agency guide
# can yield hundreds of near-duplicate findings; sending all of them wastes
# context without adding information. Ordering is preserved (document order),
# so the cap trims the tail of a very repetitive category, never the front.
_MAX_FINDINGS_PER_KEY = 250


def _consolidate(findings: dict, coverage: dict) -> Optional[dict]:
    """Merged findings -> the strict contract dict (unparsed/uncoerced)."""
    payload = {k: findings.get(k, [])[:_MAX_FINDINGS_PER_KEY]
               for k in _FINDING_KEYS}
    payload["identity_candidates"] = coverage.get("identity", [])[:20]
    if not any(payload[k] for k in _FINDING_KEYS) and not payload["identity_candidates"]:
        return None
    raw = _call_gemini(
        "FINDINGS SWEPT FROM THE SOLICITATION:\n"
        + json.dumps(payload, ensure_ascii=False),
        system_instruction=_CONSOLIDATE_SYSTEM,
        max_output_tokens=32768,
    )
    return _parse_response(raw)


# ============================================================================
# Source-quote verification
# ============================================================================

# Fields whose extracted value we cross-check against a verbatim source quote.
# sponsor is excluded -- it's canonicalized to a token (e.g. "NSF") that won't
# be a verbatim substring of the PDF.
_VERIFIABLE_FIELDS = (
    "deadline", "budget_cap", "page_limits", "required_attachments",
    "eligibility", "submission_portal", "program_id", "program_name",
)


# Bullet glyphs + pdfplumber's undecoded-glyph artifacts ("(cid:127)"). These
# sit between items in bulleted lists (page limits, required attachments), so a
# clean multi-line quote isn't a literal substring unless we drop them first.
_LIST_NOISE_RE = re.compile(r"\(cid:\d+\)|[•‣▪●·∙◦⁃*]")


def _norm_for_match(s) -> str:
    """Lowercase, drop bullet/list noise, collapse whitespace -- a forgiving
    substring match of a source quote against the PDF text (pdfplumber
    re-spaces text and sprinkles bullet glyphs through lists)."""
    cleaned = _LIST_NOISE_RE.sub(" ", str(s or "").lower())
    return " ".join(cleaned.split())


def _has_value(v) -> bool:
    if v is None:
        return False
    if isinstance(v, (dict, list, str)):
        return len(v) > 0
    return True


# Fields whose supporting quote legitimately diverges from the literal PDF
# layout, so they match leniently (a present LEADING chunk is enough):
#   - page_limits / required_attachments: the quote spans a bulleted list
#     whose tail is re-flowed by pdfplumber.
#   - eligibility: the contract ASKS for a one-or-two-sentence summary, so a
#     character-for-character quote is impossible by construction. Requiring
#     one flagged this field on essentially every solicitation, which trains
#     the user to ignore the flags -- the opposite of what they are for.
# Scalar high-stakes fields (deadline, budget_cap, ...) stay STRICT so a
# wrong date / amount inside the quote is still caught.
_LENIENT_QUOTE_FIELDS = {"page_limits", "required_attachments", "eligibility"}

# Short identity strings that are copied out of the document as-is. For these
# the VALUE ITSELF appearing verbatim in the text is stronger evidence than a
# surrounding quote, so a missing source_quotes entry is not a red flag when
# the value is right there on the page. (Not for deadline/budget_cap: those
# are normalized to an ISO date / bare integer and so never appear literally.)
_VALUE_AS_EVIDENCE_FIELDS = {"program_id", "program_name", "submission_portal"}

# List fields assembled by merging findings from many pages. Their evidence is
# that the items are actually named in the document, not a single quote that
# happens to cover all of them.
_LIST_ITEM_EVIDENCE_FIELDS = {"required_attachments"}

# Share of list items that must appear verbatim for the list to count as
# grounded. Not 100%: the consolidator canonicalizes near-duplicate names
# ("Biosketch" / "Biographical Sketch(es)"), so a couple of entries can
# legitimately differ from any single spelling in the PDF. A fabricated list
# would match almost nothing and is still caught.
_LIST_ITEM_EVIDENCE_RATIO = 0.6


# Separators used when a single identity field legitimately names several
# things ("Research.gov, Grants.gov"). Such a value is never one contiguous
# string in the PDF, so matching it whole flagged submission_portal on every
# solicitation that accepts more than one system -- verified live 2026-09-03.
_VALUE_SPLIT_RE = re.compile(r"\s*(?:,|;|/|\bor\b|\band\b)\s*", re.IGNORECASE)


def _value_is_its_own_evidence(value: str, text_norm: str) -> bool:
    """True when the value -- whole, or every part of a multi-part value --
    appears verbatim in the document."""
    whole = _norm_for_match(value)
    if whole and whole in text_norm:
        return True
    parts = [_norm_for_match(p) for p in _VALUE_SPLIT_RE.split(value)]
    parts = [p for p in parts if len(p) >= 4]
    return bool(parts) and all(p in text_norm for p in parts)


def _list_items_grounded(items: list, text_norm: str) -> bool:
    """True when enough of a list's entries are named verbatim in the PDF."""
    names = [_norm_for_match(x) for x in items if str(x).strip()]
    names = [n for n in names if len(n) >= 4]     # ignore trivial tokens
    if not names:
        return False
    hits = sum(1 for n in names if n in text_norm)
    return hits / len(names) >= _LIST_ITEM_EVIDENCE_RATIO


def _quote_grounded(quote: str, text_norm: str, lenient: bool) -> bool:
    """True if the quote is genuinely from the PDF. Strict = full normalized
    substring. Lenient also accepts a present LEADING chunk. A wholesale
    fabricated quote matches neither, so it's still caught."""
    qn = _norm_for_match(quote)
    if not qn:
        return False
    if qn in text_norm:
        return True
    return lenient and len(qn) >= 60 and qn[:60] in text_norm


def _verify_source_quotes(extracted: dict, text: str) -> list:
    """Deterministic anti-hallucination check -- the list of filled fields
    with NO grounded evidence. Thin wrapper kept at its original signature
    for existing callers/tests; see _verify_source_quotes_detailed."""
    return _verify_source_quotes_detailed(extracted, text)[0]


def _verify_source_quotes_detailed(extracted: dict, text: str) -> Tuple[list, dict]:
    """Deterministic anti-hallucination check.

    Returns (unverified_fields, partially_verified).

      unverified_fields  -- filled fields with NO grounded evidence at all:
                            no source_quotes entry, or every quote fabricated.
                            These are the "do not trust this value" flags.
      partially_verified -- {field: [sub-keys whose own quote failed]} for a
                            field that supplies one quote PER ENTRY (page
                            limits usually do). The field as a whole is
                            grounded; only the listed entries need a look.

    Values are NEVER changed; the caller surfaces these in the UI.

    Why the split: the previous version demanded a single plain string per
    field, so a richer per-entry quote object was auto-flagged even when
    every one of its quotes was verbatim from the PDF. Measured on NSF 24-1
    that flagged page_limits red on 100% of runs with 4/5 quotes exact -- a
    warning that is always on is the same as no warning."""
    text_norm = _norm_for_match(text)
    quotes = extracted.get("source_quotes") or {}
    unverified: list = []
    partial: dict = {}

    for field in _VERIFIABLE_FIELDS:
        value = extracted.get(field)
        if not _has_value(value):
            continue
        q = quotes.get(field)
        lenient = field in _LENIENT_QUOTE_FIELDS

        # An identity string that appears verbatim in the document is its own
        # evidence -- no separate quote needed.
        if (field in _VALUE_AS_EVIDENCE_FIELDS and isinstance(value, str)
                and _value_is_its_own_evidence(value, text_norm)):
            continue

        # A merged list (required_attachments) is assembled from findings
        # across many pages, so a single covering quote is awkward and the
        # model supplies one only sometimes -- which made this field flash red
        # on some runs and not others for identical input. The real evidence
        # is whether the items themselves are named in the document.
        if field in _LIST_ITEM_EVIDENCE_FIELDS and isinstance(value, list):
            if _list_items_grounded(value, text_norm):
                continue

        if isinstance(q, dict):
            # One quote per entry (e.g. {"project_description": "...", ...}).
            entries = {str(k): v for k, v in q.items()
                       if isinstance(v, str) and v.strip()}
            if not entries:
                unverified.append(field)
                continue
            bad = [k for k, v in entries.items()
                   if not _quote_grounded(v, text_norm, lenient)]
            if len(bad) == len(entries):
                unverified.append(field)      # nothing at all checked out
            elif bad:
                partial[field] = sorted(bad)  # some entries need a look
            continue

        if isinstance(q, list):
            entries = [v for v in q if isinstance(v, str) and v.strip()]
            if not entries:
                unverified.append(field)
            elif not any(_quote_grounded(v, text_norm, lenient) for v in entries):
                unverified.append(field)
            continue

        if not isinstance(q, str) or not q.strip():
            unverified.append(field)          # value with no supporting quote
            continue
        if not _quote_grounded(q, text_norm, lenient):
            unverified.append(field)          # quote not actually in the PDF

    return unverified, partial


# ============================================================================
# Public API
# ============================================================================

def extract_from_pages(pages: List[str]) -> Optional[dict]:
    """Page list (index 0 == page 1) -> the structured contract dict.

    This is the real entry point: the sweep sends every page to the model,
    so nothing is truncated away. Returns None if the document is empty or
    Gemini produced nothing usable."""
    numbered: List[Tuple[Optional[int], str]] = [
        (i, t) for i, t in enumerate(pages, start=1) if t and t.strip()
    ]
    if not numbered:
        return None

    findings, coverage = _sweep(numbered)
    parsed = _consolidate(findings, coverage)
    if parsed is None:
        return None

    out = _coerce_extracted(parsed)
    full_text = "\n\n".join(t for _, t in numbered)
    unverified, partial = _verify_source_quotes_detailed(out, full_text)
    out["unverified_fields"] = unverified
    out["partially_verified"] = partial
    out["coverage"] = {k: v for k, v in coverage.items() if k != "identity"}
    return out


def extract_from_text(text: str) -> Optional[dict]:
    """Plain text (no page boundaries) -> the contract dict.

    Kept for callers that only have flat text -- notably the URL variant,
    which scrapes a web page rather than a PDF. The text is still swept in
    slices, so nothing is truncated; the findings simply carry no page
    numbers. Returns None on empty input or a malformed Gemini response."""
    if not text or not text.strip():
        return None
    return extract_from_pages([text])


def extract_pages_from_pdf(pdf_bytes: bytes) -> List[str]:
    """PDF -> one text string per page via pdfplumber. Page numbers are the
    list index + 1, which is what the sweep's [[PAGE n]] markers report, so a
    quote can always be traced back to a physical page of the user's file."""
    if not pdf_bytes:
        return []
    try:
        pdfp = _get_pdfplumber()
    except ImportError:
        print("   [SOLICITATION] pdfplumber not installed")
        return []
    pages: List[str] = []
    try:
        with pdfp.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                # pdfplumber can emit control characters (e.g. a "fi"/"fl"
                # ligature glyph as \x1f). Those are illegal inside JSON
                # strings and made Gemini's echoed source_quotes unparseable
                # -> the whole extraction returned None on an otherwise-fine
                # PDF. Strip them (keep \t \n \r).
                pages.append(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t))
    except Exception as e:
        print(f"   [SOLICITATION] PDF parse failed: {e}")
        return []
    return pages


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """PDF -> plain text (all pages joined). Kept for callers that just want
    the text; the extraction pipeline itself uses extract_pages_from_pdf so
    it can keep page numbers."""
    return "\n\n".join(p for p in extract_pages_from_pdf(pdf_bytes) if p)


def extract_from_pdf_bytes(pdf_bytes: bytes) -> Optional[dict]:
    """One-shot: PDF bytes -> structured contract dict, or None on
    failure. Used by the /api/me/submissions/from-solicitation endpoint."""
    pages = extract_pages_from_pdf(pdf_bytes)
    if not any(p.strip() for p in pages):
        return None
    return extract_from_pages(pages)
