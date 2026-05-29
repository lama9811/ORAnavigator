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


_PROMPT = """You are extracting structured metadata from a research grant solicitation PDF for a university grants office.

Read the solicitation text below and return ONLY a JSON object with EXACTLY these fields:

{
  "sponsor": "NSF" | "NIH" | "DoD" | "DoE" | "NASA" | "USDA" | "EPA" | "NOAA" | "Foundation" | "State of Maryland" | "Internal" | string,
  "program_id": short program identifier as it appears in the PDF (e.g. "NSF 23-573", "PA-24-001") or null,
  "program_name": short human-readable name (e.g. "Faculty Early Career Development") or null,
  "deadline": ISO-8601 string with timezone if known (e.g. "2026-06-12T17:00:00-05:00") or just date "2026-06-12" or null,
  "page_limits": object mapping section name -> integer page limit. Examples: {"project_description": 15, "data_management_plan": 2, "biosketch": 5}. Empty object {} if no limits stated.
  "required_attachments": array of attachment names the solicitation lists as required (e.g. ["Biosketch", "Current & Pending Support", "Data Management Plan", "Project Description"]). Empty array if unclear.
  "eligibility": one-sentence summary of who can apply or null,
  "budget_cap": integer dollar maximum (e.g. 600000) or null. If the cap is per year, return the per-year value. No commas or currency symbols.
  "submission_portal": "Research.gov" | "Grants.gov" | "ASSIST" | "eRA Commons" | other | null,
  "source_quotes": object mapping each filled field name to a short (<=200 char) verbatim quote from the PDF that supports the extracted value. Example: {"deadline": "Proposals are due no later than 5:00 p.m. on June 12, 2026."}
}

RULES:
- Return ONLY the JSON object. No prose, no markdown fences, no explanation.
- If a field is genuinely not stated in the PDF, return null (or {} / [] for object/array fields).
- NEVER guess a deadline. If the PDF gives multiple deadlines or none, return null.
- Quotes in source_quotes must be VERBATIM substrings of the input text. Do not paraphrase.
- budget_cap is the per-proposal maximum, not the total program budget.

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
                "max_output_tokens": 6000,
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
        parsed = json.loads(text)
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

    return out


# ============================================================================
# Public API
# ============================================================================

# Sponsor PDFs are long; we cap how much we send to keep latency and cost
# reasonable. Most key metadata lives in the first ~30k chars (cover page
# + summary + budget section). The full document goes through anyway via
# the user-facing checklist; this is just the metadata-extraction window.
_MAX_PROMPT_CHARS = 40_000


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
    return "\n\n".join(pages_text)


def extract_from_pdf_bytes(pdf_bytes: bytes) -> Optional[dict]:
    """One-shot: PDF bytes -> structured contract dict, or None on
    failure. Used by the /api/me/submissions/from-solicitation endpoint."""
    text = extract_text_from_pdf(pdf_bytes)
    if not text:
        return None
    return extract_from_text(text)
