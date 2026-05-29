"""Solicitation extractor -- parses a sponsor PDF (NSF / NIH / DoD / DoE
/ foundation) into a structured dict that the Proposals tracker can use
to seed a Submission tuned to *that specific solicitation*, not just a
generic NSF/NIH template.

Pipeline:
  PDF bytes
    -> pdfplumber text extraction (each page concatenated)
    -> Gemini 2.5 Flash structured-output prompt
    -> JSON contract dict with source_quotes for provenance

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
from io import BytesIO
from typing import Optional

# Lazy import: pdfplumber adds startup cost; we only need it on demand.
_pdfplumber = None
_genai = None
_gemini_client = None
_gemini_init_attempted = False


def _get_pdfplumber():
    global _pdfplumber
    if _pdfplumber is None:
        import pdfplumber  # type: ignore
        _pdfplumber = pdfplumber
    return _pdfplumber


def _get_client():
    """Reuse the codebase's Vertex-first / API-key-fallback pattern for
    Gemini. Cached across calls."""
    global _gemini_client, _gemini_init_attempted, _genai
    if _gemini_client is not None:
        return _gemini_client
    if _gemini_init_attempted:
        return None
    _gemini_init_attempted = True
    try:
        from google import genai
        _genai = genai
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or "infra-vertex-494621-v1"
        try:
            _gemini_client = genai.Client(vertexai=True, project=project,
                                          location="us-central1")
        except Exception:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if api_key:
                _gemini_client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"   [SOLICITATION] Gemini client init failed: {e}")
    return _gemini_client


# ============================================================================
# JSON contract
# ============================================================================
# Every extracted dict has exactly these top-level keys. Frontend renders
# them in this order; missing values are surfaced as empty inputs so the
# user can fill them in.

_CONTRACT_KEYS = (
    "sponsor", "program_id", "program_name", "deadline",
    "page_limits", "required_attachments", "eligibility",
    "budget_cap", "submission_portal", "source_quotes",
)


_PROMPT = """You are extracting structured metadata from a research grant solicitation PDF for a university grants office. ACCURACY IS CRITICAL: a wrong deadline, a wrong budget cap, or a missed page limit can cost a faculty member an entire grant. Read the ENTIRE text provided before answering -- the load-bearing facts are often in the "Award Information", "Eligibility", and "Proposal Preparation" / "Content and Form of Application" / "Format / Page Limitations" sections that can appear well into the document, not just on the cover page.

Return ONLY a JSON object with EXACTLY these fields:

{
  "sponsor": one of "NSF" | "NIH" | "DoD" | "DoE" | "NASA" | "USDA" | "EPA" | "NOAA" | "State of Maryland" | "Internal", OR for any other funder the FULL organization name exactly as written (e.g. "Alfred P. Sloan Foundation") -- never the bare word "Foundation",
  "program_id": short program identifier as it appears in the PDF (e.g. "NSF 23-573", "PA-24-001", "DE-FOA-0002884") or null,
  "program_name": short human-readable name (e.g. "Faculty Early Career Development") or null,
  "deadline": ISO-8601 string with timezone if known (e.g. "2026-06-12T17:00:00-05:00") or just date "2026-06-12" or null,
  "page_limits": object mapping section name (snake_case) -> integer page limit. ALWAYS include the MAIN narrative cap when the PDF states one -- it is usually titled "Project Description", "Research Narrative", "Project Narrative", "Research Strategy", or "Proposal Narrative" -- plus any others you find (project_summary, data_management_plan, biosketch, budget_justification, etc.). Examples: {"project_description": 15, "data_management_plan": 2, "biosketch": 5}. Use {} ONLY if the PDF truly states no page limit anywhere. If a limit is conditional (e.g. 15 pages if the budget is <= $250k, else 20), return the SMALLER / most restrictive number and explain the condition in source_quotes.
  "required_attachments": array of attachment / required-element names the solicitation lists as required (e.g. ["Biosketch", "Current & Pending Support", "Data Management Plan", "Project Description"]). Include conditionally-required items; exclude purely optional ones. [] only if none are listed.
  "eligibility": a one or two sentence summary of who may apply, INCLUDING any alternate path the PDF stresses (e.g. "Full proposals by invitation only; others may submit a Letter of Inquiry"), or null,
  "budget_cap": integer dollar maximum PER PROPOSAL/PER AWARD (e.g. 600000), or null. No commas or currency symbols. If the cap is stated PER YEAR, return the per-year value. If DIFFERENT maximums are given for different applicant types (e.g. single-institution vs multi-institution/collaborative), return the SMALLEST / MOST RESTRICTIVE one so a typical single-PI applicant is never told they have more room than they do, and record the full breakdown in source_quotes. NEVER return the total program budget / "anticipated funding amount".
  "submission_portal": the system(s) used to submit. If MORE THAN ONE portal is accepted, list ALL of them comma-separated (e.g. "Research.gov, Grants.gov"). Typical values: "Research.gov" | "Grants.gov" | "ASSIST" | "eRA Commons" | other | null,
  "source_quotes": object mapping each filled field name to a short (<=200 char) verbatim quote from the PDF that supports the extracted value. Example: {"deadline": "Proposals are due no later than 5:00 p.m. on June 12, 2026."}
}

RULES:
- Return ONLY the JSON object. No prose, no markdown fences, no explanation.
- If a field is genuinely not stated in the PDF, return null (or {} / [] for object/array fields). Do not invent values.
- NEVER guess a deadline. If the PDF gives multiple/recurring deadlines or none, return null.
- Quotes in source_quotes must be VERBATIM substrings of the input text. Do not paraphrase.
- budget_cap is the per-proposal/per-award maximum (the MOST RESTRICTIVE if several are given), NEVER the total program budget.
- Do NOT return an empty page_limits {} if the document states any page limit -- scan the whole text for the main narrative / project-description cap.

SOLICITATION TEXT:
"""


def _call_gemini(prompt_text: str) -> str:
    """Single Gemini round-trip. Returns the raw response text (may
    contain markdown fences; _parse_response handles that). Returns
    empty string on error so callers can fail gracefully."""
    client = _get_client()
    if client is None:
        print("   [SOLICITATION] Gemini client is None (init failed)")
        return ""
    try:
        # max_output_tokens bumped to 6000: Gemini-2.5-Flash uses some of
        # the output budget for implicit reasoning, which silently truncates
        # the JSON mid-document at 2000 tokens (observed for solicitations
        # with 8+ required_attachments).
        # response_mime_type=application/json forces clean JSON output (no
        # markdown fences, no preamble text) so _parse_response doesn't
        # have to guess.
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=_PROMPT + prompt_text,
            config={
                "temperature": 0.0,
                "max_output_tokens": 8192,
                "response_mime_type": "application/json",
            },
        )
        raw = (response.text or "").strip()
        # Log a short preview so we can diagnose downstream parse failures.
        # Length + first 240 chars is enough to see what Gemini returned
        # without flooding logs with the full 2k-token response.
        preview = raw[:240].replace("\n", "\\n")
        print(f"   [SOLICITATION] Gemini OK, len={len(raw)}, preview={preview}")
        return raw
    except Exception as e:
        print(f"   [SOLICITATION] Gemini call failed: {e}")
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
        key = re.sub(r"[,:;]+", " ", str(k))
        key = re.sub(r"\s+", " ", key).strip()
        match = re.search(r"\d+", str(v))
        if key and match:
            iv = int(match.group())
            if iv > 0:
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

    # source_quotes must be a dict
    if not isinstance(out["source_quotes"], dict):
        out["source_quotes"] = {}

    # Empty strings should be None for cleaner UI
    for k in ("sponsor", "program_id", "program_name", "deadline",
              "eligibility", "submission_portal"):
        if isinstance(out[k], str) and not out[k].strip():
            out[k] = None

    # Canonicalize the sponsor token so downstream template/section selection
    # works whether Gemini returned "NSF" or "National Science Foundation".
    out["sponsor"] = _canon_sponsor(out["sponsor"])

    return out


# ============================================================================
# Public API
# ============================================================================

# How much of the solicitation text we send to Gemini. The OLD 40k window
# truncated long FOAs (NSF/DOE often state page limits + required elements in
# a "Content and Form of Application" section past char 40k -> the model never
# saw them and returned {}). Gemini 2.5 Flash has a ~1M-token context, so a
# 250k-char window (~60k tokens) is cheap and captures the metadata sections
# of essentially every real solicitation. Accuracy >> the few extra cents/secs.
_MAX_PROMPT_CHARS = 250_000


def extract_from_text(text: str) -> Optional[dict]:
    """Send text to Gemini and return the parsed, coerced contract dict.
    Returns None on empty input or malformed Gemini response."""
    if not text or not text.strip():
        return None
    snippet = text[:_MAX_PROMPT_CHARS]
    raw = _call_gemini(snippet)
    parsed = _parse_response(raw)
    if parsed is None:
        return None
    return _coerce_extracted(parsed)


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """PDF -> plain text via pdfplumber. Tested via the integration smoke
    test, not unit-tested (depends on real PDFs)."""
    if not pdf_bytes:
        return ""
    try:
        pdfp = _get_pdfplumber()
    except ImportError:
        print("   [SOLICITATION] pdfplumber not installed")
        return ""
    pages_text = []
    try:
        with pdfp.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t:
                    pages_text.append(t)
    except Exception as e:
        print(f"   [SOLICITATION] PDF parse failed: {e}")
        return ""
    joined = "\n\n".join(pages_text)
    # pdfplumber can emit control characters (e.g. a "fi"/"fl" ligature glyph
    # as \x1f). Those are illegal inside JSON strings and made Gemini's echoed
    # source_quotes unparseable -> the whole extraction returned None on an
    # otherwise-fine PDF. Strip them (keep \t \n \r).
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", joined)


def extract_from_pdf_bytes(pdf_bytes: bytes) -> Optional[dict]:
    """One-shot: PDF bytes -> structured contract dict, or None on
    failure. Used by the /api/me/submissions/from-solicitation endpoint."""
    text = extract_text_from_pdf(pdf_bytes)
    if not text:
        return None
    return extract_from_text(text)
