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


# Strict rules passed as the model's SYSTEM INSTRUCTION; the solicitation text is
# sent separately as the user content. Every filled field must carry a verbatim
# source_quotes entry, which a deterministic check (_verify_source_quotes) then
# confirms is actually present in the PDF -- so a fabricated value is flagged.
_EXTRACT_SYSTEM = """You extract structured metadata from a research grant funding announcement (solicitation / FOA) PDF for a university grants office. ACCURACY IS CRITICAL: a wrong deadline, budget cap, or page limit can cost a faculty member an entire grant. Read the ENTIRE text -- the load-bearing facts often appear well into the document ("Award Information", "Eligibility", "Proposal Preparation" / "Content and Form of Application" / "Format / Page Limitations"), not just on the cover page. You output DATA ONLY from the text provided.

ABSOLUTE RULES:
1. EXTRACT ONLY FROM THE PROVIDED TEXT. Use only what the SOLICITATION TEXT actually states. Never use outside knowledge, memory of other NSF/NIH programs, or assumptions about "typical" or "usual" values. You know nothing about this program beyond the text given.
2. QUOTE EVERY VALUE. For EVERY field you fill (non-null), source_quotes MUST contain a VERBATIM, character-for-character quote (<=200 chars) copied from the SOLICITATION TEXT that states that value. If you cannot find a real supporting quote in the text, you MUST return null for that field. No quote, no value.
3. NEVER GUESS OR INVENT. If a value is not explicitly stated, return null (or {} / [] for object/array fields). Do not fill a "reasonable" default. NEVER guess a deadline -- if the PDF gives multiple/recurring deadlines or none, return null.
4. QUOTES ARE VERBATIM. Every source_quotes value must be an exact substring of the SOLICITATION TEXT -- do not paraphrase, summarize, normalize, or fix typos. Fabricated quotes are automatically detected and the field is flagged for human review.
5. MOST RESTRICTIVE WINS. If different values are given for different applicant types or conditions, return the SMALLEST / most restrictive (smallest budget cap, smallest page limit) so an applicant is never told they have more room than they do; record the full breakdown in source_quotes.
6. budget_cap = the maximum PER PROPOSAL / PER AWARD, NEVER the total program budget or "anticipated funding amount". If stated per year, return the per-year value.
7. page_limits: ALWAYS include the main narrative cap (Project Description / Research Narrative / Research Strategy / Proposal Narrative) when stated; {} only if the PDF truly states no page limit anywhere.

Return ONLY a JSON object with EXACTLY these fields (unknown -> null, or {} / []):
{
  "sponsor": one of "NSF" | "NIH" | "DoD" | "DoE" | "NASA" | "USDA" | "EPA" | "NOAA" | "State of Maryland" | "Internal", OR for any other funder the FULL organization name exactly as written (e.g. "Alfred P. Sloan Foundation") -- never the bare word "Foundation",
  "program_id": short program identifier as it appears (e.g. "NSF 23-573", "PA-24-001", "DE-FOA-0002884") or null,
  "program_name": short human-readable name (e.g. "Faculty Early Career Development") or null,
  "deadline": ISO-8601 with timezone if known (e.g. "2026-06-12T17:00:00-05:00") or date "2026-06-12" or null,
  "page_limits": object mapping section name (snake_case) -> integer page limit. Examples: {"project_description": 15, "data_management_plan": 2, "biosketch": 5},
  "required_attachments": array of required attachment / element names (e.g. ["Biosketch", "Current & Pending Support", "Data Management Plan"]); include conditionally-required, exclude purely optional; [] if none,
  "eligibility": one or two sentence summary of who may apply, including any alternate path stressed, or null,
  "budget_cap": integer dollar maximum per proposal/award (e.g. 600000), no commas/symbols, or null,
  "submission_portal": the submission system(s); if more than one is accepted list ALL comma-separated (e.g. "Research.gov, Grants.gov"), or null,
  "source_quotes": object mapping each FILLED field name -> a <=200-char VERBATIM quote from the text supporting it. Example: {"deadline": "Proposals are due no later than 5:00 p.m. on June 12, 2026."}
}
Return ONLY the JSON object. No prose, no markdown fences."""


def _call_gemini(prompt_text: str, system_instruction: Optional[str] = None) -> str:
    """Single Gemini round-trip. Returns the raw response text (may
    contain markdown fences; _parse_response handles that). Returns
    empty string on error so callers can fail gracefully.

    When `system_instruction` is given, the rules are sent as the model's
    system prompt and `prompt_text` carries only the data -- stronger
    rule-adherence than inlining the rules into the content."""
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
        config = {
            "temperature": 0.0,
            "max_output_tokens": 8192,
            "response_mime_type": "application/json",
        }
        if system_instruction:
            config["system_instruction"] = system_instruction
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_text,
            config=config,
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


# Structured multi-item fields: their quote spans a bulleted list whose tail
# legitimately diverges from the literal PDF layout, so match leniently (leading
# chunk). Scalar high-stakes fields (deadline, budget_cap, ...) stay STRICT so a
# wrong date / amount inside the quote is still caught.
_LENIENT_QUOTE_FIELDS = {"page_limits", "required_attachments"}


def _quote_grounded(quote: str, text_norm: str, lenient: bool) -> bool:
    """True if the quote is genuinely from the PDF. Strict = full normalized
    substring. Lenient (list fields only) also accepts a present LEADING chunk.
    A wholesale fabricated quote matches neither, so it's still caught."""
    qn = _norm_for_match(quote)
    if not qn:
        return False
    if qn in text_norm:
        return True
    return lenient and qn[:60] in text_norm


def _verify_source_quotes(extracted: dict, text: str) -> list:
    """Deterministic anti-hallucination check. Returns the list of FILLED
    fields whose extracted value is NOT backed by a quote actually present in
    the PDF text -- either no source_quotes entry, or a fabricated quote.
    Values are NOT changed; the caller surfaces these as 'double-check this'
    flags in the UI."""
    text_norm = _norm_for_match(text)
    quotes = extracted.get("source_quotes") or {}
    unverified = []
    for field in _VERIFIABLE_FIELDS:
        if not _has_value(extracted.get(field)):
            continue
        q = quotes.get(field)
        if not isinstance(q, str) or not q.strip():
            unverified.append(field)          # value with no supporting quote
            continue
        if not _quote_grounded(q, text_norm, lenient=(field in _LENIENT_QUOTE_FIELDS)):
            unverified.append(field)          # quote not actually in the PDF
    return unverified


def extract_from_text(text: str) -> Optional[dict]:
    """Send text to Gemini and return the parsed, coerced contract dict.
    Returns None on empty input or malformed Gemini response. Adds an
    `unverified_fields` list flagging values not backed by a real PDF quote."""
    if not text or not text.strip():
        return None
    snippet = text[:_MAX_PROMPT_CHARS]
    raw = _call_gemini("SOLICITATION TEXT:\n" + snippet,
                       system_instruction=_EXTRACT_SYSTEM)
    parsed = _parse_response(raw)
    if parsed is None:
        return None
    out = _coerce_extracted(parsed)
    out["unverified_fields"] = _verify_source_quotes(out, snippet)
    return out


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
