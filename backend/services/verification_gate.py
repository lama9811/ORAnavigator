"""
Verification Gate: Post-Agent Claim Verification
=================================================
Takes the agent's response and verifies factual claims against
the KB documents that were retrieved pre-agent.

Extracts "atomic claims" (phone numbers, emails, room numbers,
course codes, names) and checks if each appears in the KB docs.
Claims not found in KB get flagged.

This catches the case where Gemini's parametric memory generates
plausible-but-wrong facts (e.g., wrong phone number, invented room).
"""

import re
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    original: str
    verified: str
    claims_total: int = 0
    claims_verified: int = 0
    claims_unverified: int = 0
    unverified_details: list = None

    def __post_init__(self):
        if self.unverified_details is None:
            self.unverified_details = []


# Patterns for extracting factual claims
_PHONE_RE = re.compile(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}')
_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_ROOM_RE = re.compile(r'(?:McMechen|Tyler|Montebello|Truth|Mitchell|Jenkins|Carnegie|Schaefer|Murphy|Hurt|Key|Baldwin|Banneker|Martin|Soper|Rawlings|Spencer|Portage|Thurgood|Dixon|Blount|Cummings|Harper|Tubman|Marble|O\'Connell)\s+(?:(?:Science\s+Research\s+Center|Hall)\s+)?(?:(?:Suite|Room)\s+)?-?\d{2,4}[A-Z]?(?:-[A-Z])?', re.IGNORECASE)
_COURSE_RE = re.compile(r'\b(?:COSC|MATH|CLCO|PHYS|ENGL|BIOL|CHEM|ECON|ACCT)\s*\d{3}\b')
_URL_RE = re.compile(r'https?://[^\s\)]+')


def _extract_claims(text: str) -> list[dict]:
    """Extract verifiable factual claims from the response."""
    claims = []

    for match in _PHONE_RE.finditer(text):
        claims.append({"type": "phone", "value": match.group(), "pos": match.start()})

    for match in _EMAIL_RE.finditer(text):
        claims.append({"type": "email", "value": match.group(), "pos": match.start()})

    for match in _ROOM_RE.finditer(text):
        claims.append({"type": "room", "value": match.group(), "pos": match.start()})

    for match in _COURSE_RE.finditer(text):
        claims.append({"type": "course", "value": match.group(), "pos": match.start()})

    # Don't verify URLs (they could be from the procedure link injection)
    # Don't verify names (too many false positives with partial matches)

    return claims


def _normalize(value: str) -> str:
    """Normalize a value for fuzzy matching (strip formatting)."""
    return re.sub(r'[\s()\-.]', '', value).lower()


def _claim_in_docs(claim: dict, doc_texts: list[str]) -> bool:
    """Check if a claim appears in any of the KB documents."""
    normalized_claim = _normalize(claim["value"])

    for doc in doc_texts:
        normalized_doc = _normalize(doc)
        if normalized_claim in normalized_doc:
            return True

    # For course codes, also try without space (COSC350 vs COSC 350)
    if claim["type"] == "course":
        compact = claim["value"].replace(" ", "")
        for doc in doc_texts:
            if compact.lower() in doc.lower().replace(" ", ""):
                return True

    # For phone numbers, try just the last 7 digits
    if claim["type"] == "phone":
        digits = re.sub(r'\D', '', claim["value"])
        if len(digits) >= 7:
            last7 = digits[-7:]
            for doc in doc_texts:
                if last7 in re.sub(r'\D', '', doc):
                    return True

    return False


# Well-known facts that don't need KB verification
_KNOWN_FACTS = {
    "4438853962",  # CS department phone
    "4438854503",  # Dr. Wang phone
    "compsci@morgan.edu",
    "shuangbao.wang@morgan.edu",
    "mcmechenhall507",  # CS department location
}


def _is_known_fact(claim: dict) -> bool:
    """Check if a claim is a well-known department fact."""
    normalized = _normalize(claim["value"])
    return normalized in _KNOWN_FACTS


def verify_response(response: str, doc_texts: list[str], query: str = "") -> VerificationResult:
    """
    Main entry point: Verify factual claims in the agent's response.

    Args:
        response: The agent's generated response text
        doc_texts: List of KB document texts from the retrieval gate
        query: The original query (for logging)

    Returns:
        VerificationResult with original response, possibly modified response,
        and verification statistics.
    """
    if not response or not doc_texts:
        return VerificationResult(original=response, verified=response)

    claims = _extract_claims(response)
    if not claims:
        # No verifiable claims found - pass through
        return VerificationResult(original=response, verified=response)

    verified_count = 0
    unverified = []

    for claim in claims:
        if _is_known_fact(claim):
            verified_count += 1
            continue

        if _claim_in_docs(claim, doc_texts):
            verified_count += 1
        else:
            unverified.append(claim)
            log.warning(
                f"[VERIFY] Unverified {claim['type']}: '{claim['value']}' "
                f"(query: '{query[:40]}')"
            )

    result = VerificationResult(
        original=response,
        verified=response,
        claims_total=len(claims),
        claims_verified=verified_count,
        claims_unverified=len(unverified),
        unverified_details=[f"{c['type']}:{c['value']}" for c in unverified],
    )

    # If more than half of claims are unverified, add a disclaimer
    if len(claims) > 0 and len(unverified) > len(claims) / 2:
        log.warning(
            f"[VERIFY] High unverified rate: {len(unverified)}/{len(claims)} claims "
            f"(query: '{query[:40]}')"
        )
        disclaimer = (
            "\n\n---\n*Some details in this response could not be fully verified against "
            "my knowledge base. Please confirm specific numbers and locations with the "
            "CS department at (443) 885-3962 or compsci@morgan.edu.*"
        )
        result.verified = response + disclaimer

    log.info(
        f"[VERIFY] {verified_count}/{len(claims)} claims verified "
        f"(query: '{query[:40]}')"
    )
    return result
