"""Sponsor Fit-Finder -- ranks federal / state / foundation funding
opportunities against a faculty member's profile.

Pipeline:
  1. Build a user profile from UserMemory rows + their Submission history
     (department, research interests, prior sponsors, active grants).
  2. Load the funding-sources subtree of the KB (15 curated docs spanning
     federal-by-discipline, HBCU/MSI-specific programs, private
     foundations, Maryland-state, external opportunity DBs).
  3. Score each source with a transparent, deterministic algorithm:
       + keyword overlap with the user's profile string
       + sponsor-history boost (user has prior NSF work -> NSF sources get
         a lift)
       + HBCU/MSI boost (Morgan is an HBCU -- HBCU/MSI-targeted programs
         are always relevant)
       + role boost (PI vs grad student -> different opportunity tiers)
  4. (Optional) ask Gemini for a one-sentence "Why this matches you"
     explanation per top match, with a template fallback so the feature
     still works without LLM credentials.

Design choices:
  - 15 KB docs is small enough that keyword scoring beats embeddings on
    latency AND interpretability. Every score is explainable from the
    matched_signals list. Embeddings can come later if the KB grows.
  - Funding sources are loaded once at import and cached in-memory. The
    KB is build-time content; cold-reading every request would be waste.
  - The "Why" explanation is per-request, not stored. Cheap enough at
    Gemini Flash prices, and the profile drifts as the user adds
    submissions -- caching invites stale rationales.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from models import Submission, UserMemory, User


log = logging.getLogger(__name__)

# Lazy Gemini client (same pattern as solicitation_extractor)
_genai = None
_gemini_client = None
_gemini_init_attempted = False


def _get_gemini_client():
    """Reuse the codebase's Vertex-first / API-key-fallback pattern."""
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
        log.warning(f"[SPONSOR_FIT] Gemini client init failed: {e}")
    return _gemini_client


# ===========================================================================
# Load funding sources (cached at module level)
# ===========================================================================

# Path resolves whether the import is from backend/ or backend/tests/.
_KB_FUNDING_DIR = Path(__file__).resolve().parent.parent / "kb_structured" / "funding_sources"

_funding_sources_cache: Optional[list[dict]] = None


def _load_funding_sources() -> list[dict]:
    """Read every .json file under kb_structured/funding_sources/ (and
    one level deep) into a list of source dicts. Skips files that aren't
    parseable JSON instead of crashing the service."""
    sources: list[dict] = []
    if not _KB_FUNDING_DIR.exists():
        log.warning(f"[SPONSOR_FIT] funding_sources dir missing: {_KB_FUNDING_DIR}")
        return sources
    for path in _KB_FUNDING_DIR.rglob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict) and doc.get("doc_id"):
                sources.append(doc)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"[SPONSOR_FIT] skipped {path.name}: {e}")
    return sources


def load_funding_sources(force_reload: bool = False) -> list[dict]:
    """Public, cached accessor. Pass force_reload=True from tests."""
    global _funding_sources_cache
    if force_reload or _funding_sources_cache is None:
        _funding_sources_cache = _load_funding_sources()
    return list(_funding_sources_cache)


# ===========================================================================
# User profile -- pulled from UserMemory + Submission history
# ===========================================================================

# Memory types we care about for matching. Other types (preference,
# goal, context, etc.) are valuable to the chat agent but don't help
# rank funding opportunities.
_PROFILE_MEMORY_TYPES = ("department", "role", "interest", "sponsor",
                         "active_grant", "irb_protocol", "iacuc_protocol")


def build_user_profile(db: Session, user_id: int) -> dict:
    """Snapshot a user's profile for ranking. Returns a dict with:
        - department: str | None
        - role: str | None ("PI" | "student" | ...)
        - interests: list[str]
        - sponsors_seen: set[str]    (from active_grant + submission history)
        - is_hbcu: bool              (always True for Morgan, but explicit)

    All fields tolerate absence -- a new user with no memories still
    gets a sensible (mostly empty) profile, and the ranker falls back
    to defaults like the HBCU/MSI boost."""
    facts = (
        db.query(UserMemory)
        .filter(
            UserMemory.user_id == user_id,
            UserMemory.memory_type.in_(_PROFILE_MEMORY_TYPES),
            UserMemory.paused == False,  # noqa: E712 -- SQLAlchemy idiom
        )
        .all()
    )

    department: Optional[str] = None
    role: Optional[str] = None
    interests: list[str] = []
    sponsors_seen: set[str] = set()

    for f in facts:
        content = (f.content or "").strip()
        if not content:
            continue
        mt = f.memory_type
        if mt == "department" and department is None:
            department = content
        elif mt == "role" and role is None:
            role = content
        elif mt == "interest":
            interests.append(content)
        elif mt == "sponsor":
            # "sponsor" memories are typically just the agency name
            sponsors_seen.add(content.split(":", 1)[0].strip().upper())
        elif mt == "active_grant":
            # active_grant content is "<SPONSOR>: <Title>"
            first = content.split(":", 1)[0].strip().upper()
            if first:
                sponsors_seen.add(first)

    # Augment from real Submission history (more authoritative than memory)
    subs = db.query(Submission).filter(Submission.user_id == user_id).all()
    for s in subs:
        if s.sponsor:
            sponsors_seen.add(s.sponsor.strip().upper())

    return {
        "department": department,
        "role": role,
        "interests": interests,
        "sponsors_seen": sponsors_seen,
        # Morgan is always an HBCU; this gate exists so the service is
        # reusable from a non-Morgan deployment without code changes.
        "is_hbcu": True,
    }


# ===========================================================================
# Scoring -- transparent, deterministic, debuggable
# ===========================================================================

# Discipline -> keywords that should boost matching when present in the
# source's title or content. Keyword presence (case-insensitive) in the
# user's department / interests string drives the lookup.
_DEPT_KEYWORDS = {
    "computer": ("cyber", "computing", "computer", "informatics", "data",
                 "artificial intelligence", "AI", "machine learning",
                 "software", "autonomy"),
    "engineering": ("engineering", "materials", "manufacturing",
                    "aerospace", "transportation", "energy", "robotics"),
    "biology": ("biolog", "biomed", "health", "medical", "cancer",
                "genetics", "neuroscience"),
    "chemistry": ("chemistry", "materials", "biochem"),
    "physics": ("physics", "materials", "renewable energy", "quantum"),
    "psychology": ("psychology", "behavioral", "mental health", "social"),
    "education": ("education", "STEM education", "teacher", "training",
                  "learning"),
    "business": ("business", "economics", "finance", "entrepreneurship"),
    "social sciences": ("social", "sociology", "anthropology",
                        "criminal justice"),
    "humanities": ("humanities", "history", "arts", "literature",
                   "philosophy", "language"),
    "public health": ("public health", "health disparities", "epidemiology"),
    "environment": ("environment", "climate", "sustainability",
                    "ecolog", "oceanography", "atmospheric"),
    "math": ("mathematics", "math", "statistics"),
}


def _dept_to_keywords(department: Optional[str]) -> list[str]:
    """Map a free-text department name to the discipline-keywords most
    likely to match in funding-source content. Falls back to the empty
    list when the dept doesn't match any known discipline."""
    if not department:
        return []
    d = department.lower()
    kws: list[str] = []
    for marker, words in _DEPT_KEYWORDS.items():
        if marker in d:
            kws.extend(words)
    return kws


_SPONSOR_ALIASES = {
    "NSF": ("nsf", "national science foundation"),
    "NIH": ("nih", "national institutes of health"),
    "DOD": ("dod", "department of defense", "darpa", "afosr", "onr",
            "army research", "naval research"),
    "DOE": ("doe", "department of energy", "nnsa", "nrel"),
    "NASA": ("nasa",),
    "EPA": ("epa", "environmental protection"),
    "USDA": ("usda",),
    "NOAA": ("noaa", "atmospheric administration"),
    "NEH": ("neh", "national endowment for the humanities"),
    "ED": ("department of education", "doed", "ed.gov"),
}


def score_source(profile: dict, source: dict) -> dict:
    """Score ONE funding source for ONE user profile. Returns:
        {
          "doc_id": str,
          "score": int (higher = better fit; ~0..150 in practice),
          "matched_signals": list[str]  (human-readable reasons),
        }

    The score is intentionally simple: a base 10, plus bumps for each
    matched signal. We expose `matched_signals` so the UI can render
    "Why" content without an LLM call when one isn't available."""
    text = " ".join([
        (source.get("title") or ""),
        (source.get("display_label") or ""),
        (source.get("content") or ""),
        (source.get("subcategory") or ""),
    ]).lower()

    score = 10  # everyone starts with a small base so the list is non-empty
    signals: list[str] = []

    # ---- 1. HBCU / MSI: every Morgan PI is eligible. Huge boost on programs
    # that specifically target HBCU/MSI institutions.
    if profile.get("is_hbcu") and (
        "hbcu" in text or "minority serving" in text or "msi" in text
    ):
        score += 30
        signals.append("HBCU/MSI eligibility (Morgan is an HBCU)")

    # ---- 2. Department -> keyword overlap. Each matched discipline keyword
    # is worth a few points. Capped so a heavy keyword overlap doesn't
    # crowd out other signals.
    dept = profile.get("department")
    if dept:
        kws = _dept_to_keywords(dept)
        matches = sum(1 for kw in kws if kw.lower() in text)
        if matches:
            bumped = min(matches * 6, 30)
            score += bumped
            signals.append(f"Discipline fit for {dept.title()} "
                           f"({matches} keyword match{'es' if matches > 1 else ''})")

    # ---- 3. Interests -- explicit research-topic mentions from memory.
    # Counts as a strong fit signal because the user told us themselves.
    interests = profile.get("interests") or []
    interest_matches = 0
    matched_interest_terms: list[str] = []
    for interest in interests:
        terms = [t for t in re.split(r"[,\s]+", interest.lower()) if len(t) > 3]
        for term in terms:
            if term in text:
                interest_matches += 1
                matched_interest_terms.append(term)
                break  # one match per interest line is enough
    if interest_matches:
        score += min(interest_matches * 8, 24)
        signals.append("Matches stated research interests: "
                       + ", ".join(matched_interest_terms[:3]))

    # ---- 4. Sponsor history. If the user has worked with NSF before, NSF
    # sources are an easier fit (institutional relationship, agency-side
    # familiarity, prior submission framework). Heuristic: any alias of
    # a sponsor the user has worked with appears in the source content.
    sponsors_seen = profile.get("sponsors_seen") or set()
    sponsor_signals: list[str] = []
    for sponsor in sponsors_seen:
        aliases = _SPONSOR_ALIASES.get(sponsor, (sponsor.lower(),))
        if any(alias in text for alias in aliases):
            sponsor_signals.append(sponsor)
    if sponsor_signals:
        score += min(15 * len(sponsor_signals), 30)
        signals.append("Sponsor history match: " + ", ".join(sponsor_signals[:3]))

    # ---- 5. Role-based filter. Student-focused programs (internships,
    # undergraduate research) shouldn't appear for PIs and vice-versa.
    role = (profile.get("role") or "").lower()
    if role:
        student_markers = ("undergraduate", "internship", "predoctoral",
                           "graduate student")
        is_student_program = any(m in text for m in student_markers)
        if "student" in role and is_student_program:
            score += 8
            signals.append("Matches your student role")
        if "pi" in role and is_student_program:
            score -= 6  # gentle de-rank, not a hard block

    return {
        "doc_id": source.get("doc_id"),
        "score": max(score, 0),
        "matched_signals": signals,
    }


# ===========================================================================
# Ranking + explanation
# ===========================================================================

def rank_matches(
    profile: dict,
    sources: Optional[list[dict]] = None,
    limit: int = 12,
) -> list[dict]:
    """Score every source, sort descending by score (then by doc_id for
    deterministic tie-breaks so tests are stable), return the top
    `limit` with the source object inlined for the API."""
    sources = sources if sources is not None else load_funding_sources()
    scored = [
        {**score_source(profile, s), "source": s}
        for s in sources
    ]
    # Sort: score desc, doc_id asc (stable tie-break).
    scored.sort(key=lambda r: (-r["score"], r.get("doc_id") or ""))
    return scored[:limit]


_EXPLAIN_PROMPT = (
    "You are helping a Morgan State University faculty member decide whether "
    "a federal/foundation funding source is a good fit. In ONE sentence "
    "(<=30 words, no preamble), explain why this source matches the user's "
    "profile. Use only facts present in the inputs.\n\n"
    "USER PROFILE:\n{profile}\n\n"
    "MATCHED SIGNALS (the scoring system already flagged these):\n{signals}\n\n"
    "FUNDING SOURCE TITLE: {title}\n"
    "FUNDING SOURCE EXCERPT:\n{excerpt}\n\n"
    "Reply with the sentence only, no quotes or markdown."
)


def _template_explanation(profile: dict, ranked: dict) -> str:
    """Deterministic fallback when Gemini is unavailable or returns junk.
    Composes a sentence from `matched_signals` so the UI never shows an
    empty rationale."""
    signals = ranked.get("matched_signals") or []
    title = (ranked.get("source") or {}).get("title", "this program")
    if not signals:
        return f"{title} is a relevant Morgan-eligible funding source."
    # Take up to 2 signals and join naturally.
    head = signals[0]
    if len(signals) >= 2:
        return f"{title} fits because {head.lower()}, and {signals[1].lower()}."
    return f"{title} fits because {head.lower()}."


def explain_match(profile: dict, ranked: dict,
                  use_llm: bool = True) -> str:
    """One-sentence "Why this matches you" rationale. Tries Gemini Flash;
    falls back to the template version on any failure."""
    if not use_llm:
        return _template_explanation(profile, ranked)
    client = _get_gemini_client()
    if client is None:
        return _template_explanation(profile, ranked)
    source = ranked.get("source") or {}
    try:
        prompt = _EXPLAIN_PROMPT.format(
            profile=json.dumps({
                "department": profile.get("department"),
                "role": profile.get("role"),
                "interests": profile.get("interests"),
                "sponsors_seen": sorted(profile.get("sponsors_seen") or []),
                "is_hbcu": profile.get("is_hbcu"),
            }),
            signals="\n".join(f"- {s}" for s in ranked.get("matched_signals") or []),
            title=source.get("title") or "",
            excerpt=(source.get("content") or "")[:800],
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={"temperature": 0.2, "max_output_tokens": 120},
        )
        text = (response.text or "").strip()
        # Strip wrapping quotes Gemini sometimes adds.
        text = text.strip('"').strip("'").strip()
        if text:
            return text
    except Exception as e:
        log.warning(f"[SPONSOR_FIT] LLM explanation failed: {e}")
    return _template_explanation(profile, ranked)


# ===========================================================================
# Public API: orchestrator returning a UI-shaped payload
# ===========================================================================

def find_matches(
    db: Session,
    user_id: int,
    limit: int = 12,
    explain: bool = True,
) -> dict:
    """Top-level entry point. Returns:
        {
          "profile": {...},
          "matches": [
              {
                "doc_id": str,
                "title": str,
                "source_url": str,
                "subcategory": str,
                "score": int,
                "matched_signals": [str, ...],
                "explanation": str,
                "content_excerpt": str,
              },
              ...
          ],
          "total_sources_scanned": int,
        }
    """
    profile = build_user_profile(db, user_id)
    sources = load_funding_sources()
    ranked = rank_matches(profile, sources, limit=limit)
    matches: list[dict] = []
    for r in ranked:
        src = r.get("source") or {}
        explanation = (
            explain_match(profile, r) if explain
            else _template_explanation(profile, r)
        )
        matches.append({
            "doc_id": r.get("doc_id"),
            "title": src.get("title") or src.get("display_label") or "",
            "source_url": src.get("source_url"),
            "subcategory": src.get("subcategory"),
            "score": r.get("score"),
            "matched_signals": r.get("matched_signals") or [],
            "explanation": explanation,
            "content_excerpt": (src.get("content") or "")[:240],
        })
    return {
        "profile": {
            "department": profile.get("department"),
            "role": profile.get("role"),
            "interests": profile.get("interests"),
            "sponsors_seen": sorted(profile.get("sponsors_seen") or []),
            "is_hbcu": profile.get("is_hbcu"),
        },
        "matches": matches,
        "total_sources_scanned": len(sources),
    }
