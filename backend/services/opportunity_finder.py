"""
Opportunity Finder + Fit Advisor
================================
Helps a new PI go from "I have an idea" to "here are live, open federal
opportunities I'm eligible for, and why each fits" -- the discovery gap ORA
Navigator didn't cover (it only helped *after* the PI already had a solicitation).

Design (see docs/superpowers/specs/2026-06-24-opportunity-finder-design.md):
  * The live Grants.gov API is authoritative for what *exists*.
  * The institution-eligibility STOP-gate is deterministic pure code.
  * Gemini is advisory only: it re-ranks and explains what the API returned,
    grounding every fit claim in a quote from the opportunity text (unquotable
    claims are dropped). Falls back to API relevance order when Gemini is down.

Two Grants.gov endpoints (public, no key, fixed host -> no SSRF):
  POST /v1/api/search2          keyword -> opp ids + basics
  POST /v1/api/fetchOpportunity opp id  -> rich synopsis (eligibility/desc/dates)
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import requests

from services import gemini_client
from services.proposals_service import internal_routing_deadline

# --- Grants.gov API ---------------------------------------------------------
_GRANTS_HOST = "https://api.grants.gov/v1/api"
_SEARCH_URL = f"{_GRANTS_HOST}/search2"
_FETCH_URL = f"{_GRANTS_HOST}/fetchOpportunity"
_TIMEOUT = 25
_MAX_RESULTS = 12

# --- Morgan State institutional profile (baked in, like the F&A rates) ------
# Morgan State University: a PUBLIC, state-controlled HBCU (also an MSI) in MD.
# Used by the deterministic eligibility gate.
INSTITUTION = {
    "name": "Morgan State University",
    "type": "public_ihe",   # public / state-controlled institution of higher education
    "hbcu": True,
    "msi": True,
    "state": "MD",
}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "my",
    "our", "we", "i", "is", "are", "this", "that", "new", "using", "use", "based",
    "study", "studies", "research", "project", "proposal", "develop", "developing",
}


# ===========================================================================
# Deterministic pieces (authoritative)
# ===========================================================================

def eligibility_gate(applicant_types: list) -> str:
    """Deterministic institution STOP-gate. Given Grants.gov `applicantTypes`
    (each {id, description}), decide whether Morgan State (a public IHE) may
    apply. Keys off the human-readable descriptions, not memorized codes.

    Returns: 'eligible' | 'unrestricted' | 'see_text' | 'ineligible'.
    """
    descs = [(t.get("description") or "").lower() for t in (applicant_types or [])]
    if not descs:
        return "see_text"
    if any("unrestricted" in d for d in descs):
        return "unrestricted"
    if any(("public" in d or "state controlled" in d) and "higher education" in d
           for d in descs):
        return "eligible"
    if any("see text" in d or d.startswith("others") for d in descs):
        return "see_text"
    return "ineligible"


def extract_query(description: str, profile: Optional[dict] = None) -> str:
    """Build a Grants.gov keyword string from the PI's free-text description,
    enriched with their saved profile interests. Deterministic: keeps content
    words, drops stopwords, dedups, caps length."""
    if not description or not description.strip():
        return ""
    text = description
    if profile and profile.get("interests"):
        text = f"{text} {profile['interests']}"
    words: list[str] = []
    seen: set = set()
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text.lower()):
        if w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        words.append(w)
        if len(words) >= 12:
            break
    return " ".join(words)


def _verify_quote(quote: str, source: str) -> str:
    """Keep a Gemini-supplied fit quote only if it really appears in the source
    text (whitespace collapsed on both sides, like section_coach/_verify_evidence)
    -- a fabricated quote is dropped. Returns the quote or ''."""
    if not quote or not source:
        return ""
    q = " ".join(quote.split())
    s = " ".join(source.split())
    return quote if q.lower() in s.lower() else ""


# ===========================================================================
# Grants.gov API boundary (returns [] / None on any failure -> graceful)
# ===========================================================================

def search_grantsgov(keyword: str, rows: int = _MAX_RESULTS) -> list:
    """POST /search2 for OPEN ("posted") opportunities. Returns a list of
    {id, number, title, agency, agencyCode, openDate, closeDate, cfdaList}.
    Empty list on any error."""
    if not keyword or not keyword.strip():
        return []
    try:
        resp = requests.post(
            _SEARCH_URL,
            json={"keyword": keyword, "oppStatuses": "posted", "rows": rows},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = (resp.json() or {}).get("data", {}) or {}
        return data.get("oppHits", []) or []
    except Exception as e:  # noqa: BLE001 -- never break the request
        print(f"   [OPP_FINDER] search2 failed: {e}")
        return []


def fetch_opportunity(opp_id: str) -> Optional[dict]:
    """POST /fetchOpportunity for the rich synopsis. Returns a normalized dict
    (the fields the finder uses), or None on any error."""
    if not opp_id:
        return None
    try:
        resp = requests.post(_FETCH_URL, json={"opportunityId": opp_id}, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = (resp.json() or {}).get("data", {}) or {}
        syn = data.get("synopsis", {}) or {}
        return {
            "id": str(data.get("id") or opp_id),
            "number": data.get("opportunityNumber") or "",
            "title": data.get("opportunityTitle") or "",
            "agency": syn.get("agencyName") or data.get("owningAgencyCode") or "",
            "closeDate": syn.get("responseDate") or "",
            "synopsisDesc": _strip_html(syn.get("synopsisDesc") or ""),
            "applicant_types": syn.get("applicantTypes") or [],
            "applicantEligibilityDesc": _strip_html(syn.get("applicantEligibilityDesc") or ""),
            "award_ceiling": syn.get("awardCeilingFormatted") or syn.get("awardCeiling") or "",
            "cost_sharing": bool(syn.get("costSharing")),
            "contact": {
                "name": syn.get("agencyContactName") or "",
                "email": syn.get("agencyContactEmail") or "",
                "phone": syn.get("agencyContactPhone") or "",
            },
            "solicitation_url": _solicitation_url(data, syn),
        }
    except Exception as e:  # noqa: BLE001
        print(f"   [OPP_FINDER] fetchOpportunity({opp_id}) failed: {e}")
        return None


# ===========================================================================
# Advisory ranking + grounded explanation (Gemini; deterministic fallback)
# ===========================================================================

def rank_and_explain(description: str, opps: list) -> list:
    """Ask Gemini to re-rank the opportunities by fit to the PI's description and
    give a 1-2 sentence fit explanation per opp, quoting the opportunity text.
    Grounded: a quote that isn't in the source is dropped. Falls back to API
    order with no explanation when Gemini is unavailable."""
    base = [{**o, "fit_explanation": "", "fit_quote": ""} for o in opps]
    if not opps:
        return base

    prompt = _rank_prompt(description, opps)
    # Up to 12 opps each with a fit sentence + a verbatim quote; 1536 truncated
    # the JSON mid-string (→ parse fail → silent fallback to API order), so give
    # the ranking response ample room.
    data = gemini_client.generate_json(prompt, temperature=0.0, max_output_tokens=4096)
    if not data:
        return base  # graceful fallback: API relevance order, no fabricated prose

    items = data.get("items", {}) or {}
    by_id = {o["id"]: o for o in opps}
    enriched: dict = {}
    for oid, o in by_id.items():
        meta = items.get(oid, {}) or {}
        enriched[oid] = {
            **o,
            "fit_explanation": (meta.get("fit") or "").strip(),
            "fit_quote": _verify_quote((meta.get("quote") or "").strip(), o.get("synopsisDesc", "")),
        }

    ranking = [str(i) for i in (data.get("ranking") or [])]
    ordered = [enriched[i] for i in ranking if i in enriched]
    ordered += [enriched[i] for i in by_id if i not in ranking]  # any the model omitted
    return ordered


# ===========================================================================
# Orchestrator
# ===========================================================================

def find_opportunities(description: str, profile: Optional[dict] = None,
                       rows: int = _MAX_RESULTS) -> list:
    """Full pipeline: description (+profile) -> keywords -> live search -> fetch
    each -> deterministic eligibility -> advisory rank/explain -> result rows."""
    keyword = extract_query(description, profile)
    hits = search_grantsgov(keyword, rows=rows)
    detailed = [d for d in (fetch_opportunity(h.get("id")) for h in hits) if d]
    # Grants.gov "posted" still returns opportunities whose response date has
    # already passed (recurring programs keep an old date) — drop those so the
    # finder only surfaces opportunities a PI can still actually apply to.
    detailed = [d for d in detailed if _is_open(d.get("closeDate", ""))]
    ranked = rank_and_explain(description, detailed)
    return [_result_row(o) for o in ranked]


def _result_row(o: dict) -> dict:
    return {
        "id": o["id"],
        "number": o.get("number", ""),
        "title": o.get("title", ""),
        "agency": o.get("agency", ""),
        "close_date": o.get("closeDate", ""),
        "internal_deadline": _internal_deadline(o.get("closeDate", "")),
        "award_ceiling": o.get("award_ceiling", ""),
        "cost_sharing": o.get("cost_sharing", False),
        "fit_explanation": o.get("fit_explanation", ""),
        "fit_quote": o.get("fit_quote", ""),
        "institution_eligibility": eligibility_gate(o.get("applicant_types", [])),
        "pi_eligibility_note": o.get("applicantEligibilityDesc", ""),
        "mechanism_note": _mechanism_note(o),
        "solicitation_url": o.get("solicitation_url", ""),
        "contact": o.get("contact", {}),
    }


# ===========================================================================
# Small deterministic helpers
# ===========================================================================

def _internal_deadline(close: str) -> str:
    """ISO date string of the internal routing deadline (5 business days before
    the sponsor close), or '' if the close date can't be parsed."""
    dt = _parse_date(close)
    if not dt:
        return ""
    internal = internal_routing_deadline(dt)
    return internal.date().isoformat() if internal else ""


def _is_open(close: str) -> bool:
    """True if the opportunity is still open to apply: no close date (rolling /
    continuous submission) or a close date today or later. Grants.gov 'posted'
    status still returns opportunities whose response date has already passed, so
    this filters out expired ones. Unparseable dates are kept (don't hide a
    possibly-open opportunity on a formatting quirk)."""
    if not close or not close.strip():
        return True
    dt = _parse_date(close)
    if not dt:
        return True
    return dt.date() >= datetime.now().date()


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.split(" 12:00")[0].strip(), fmt)
        except ValueError:
            continue
    return None


def _mechanism_note(o: dict) -> str:
    """A new-PI-friendly note about the opportunity's mechanism, inferred from
    the opportunity number's NIH activity code when present. Advisory only."""
    num = (o.get("number") or "").upper()
    m = re.search(r"\b(R01|R21|R03|R15|K01|K08|K23|K99|F31|F32|U01|P01)\b", num)
    if not m:
        return ""
    code = m.group(1)
    notes = {
        "R21": "R21 — exploratory/developmental; preliminary data not required. Well-suited to a first-time PI.",
        "R03": "R03 — small grant, short duration; good for a discrete pilot.",
        "R15": "R15 (AREA/REAP) — for research-intensive support at undergraduate-focused institutions.",
        "R01": "R01 — the standard, larger research award; reviewers typically expect preliminary data.",
        "F31": "F31 — predoctoral fellowship (trainee, not PI-led research project).",
        "F32": "F32 — postdoctoral fellowship.",
    }
    return notes.get(code, f"{code} — see the funding opportunity for mechanism details.")


def _solicitation_url(data: dict, syn: dict) -> str:
    """Best link to the actual solicitation for the proposal handoff."""
    for url in (data.get("synopsisDocumentURLs") or []):
        if isinstance(url, str) and url.startswith("http"):
            return url
    if data.get("assistURL"):
        return data["assistURL"]
    if syn.get("fundingDescLinkUrl"):
        return syn["fundingDescLinkUrl"]
    oid = str(data.get("id") or "")
    return f"https://www.grants.gov/search-results-detail/{oid}" if oid else ""


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").strip() if text else ""


def _rank_prompt(description: str, opps: list) -> str:
    lines = [
        "You are helping a new principal investigator find the best-fitting federal",
        "funding opportunity. Rank the opportunities below by how well each fits the",
        "PI's described work. For each, give a 1-2 sentence fit explanation and a",
        "SHORT verbatim quote (copied EXACTLY) from that opportunity's description",
        "that supports the fit. Do NOT invent quotes; copy real text only.",
        "",
        f"PI's described work:\n{description}",
        "",
        "Opportunities (id | title | description):",
    ]
    for o in opps:
        lines.append(f"- {o['id']} | {o.get('title','')} | {o.get('synopsisDesc','')[:600]}")
    lines += [
        "",
        'Return JSON: {"ranking": ["<id>", ...],',
        '  "items": {"<id>": {"fit": "<1-2 sentences>", "quote": "<verbatim text>"}}}',
    ]
    return "\n".join(lines)
