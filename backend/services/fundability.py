# -*- coding: utf-8 -*-
"""
Fundability / Reviewer Lens — Phase 3 of the "help researchers win" roadmap.
============================================================================
An advisory "what would a reviewer say" pass over the PI's draft, scored against
the SPONSOR'S OWN published review criteria (NSF Intellectual Merit + Broader
Impacts; NIH Significance/Investigators/Innovation/Approach/Environment). It is
explicitly NOT a guarantee and NOT a compliance gate -- it's a candid second
read to catch the weaknesses that sink first-time proposals.

Same discipline as Draft Critic / Section Coach:
  * AI is advisory; every "strong/adequate" claim must quote the draft or it's
    demoted (anti-hallucination).
  * When the LLM is unavailable, a deterministic fallback still returns the
    criteria + guidance so the feature is never dead.
"""

from typing import Optional

from services import gemini_client

# Sponsor review criteria (label + what reviewers look for).
REVIEW_CRITERIA = {
    "NSF": [
        {"key": "intellectual_merit", "label": "Intellectual Merit",
         "desc": "Potential to advance knowledge; sound rationale; qualified team; adequate resources."},
        {"key": "broader_impacts", "label": "Broader Impacts",
         "desc": "Benefit to society; education, broadening participation, with a plan to assess it."},
    ],
    "NIH": [
        {"key": "significance", "label": "Significance",
         "desc": "Important problem; improves the field if aims are met."},
        {"key": "investigators", "label": "Investigator(s)",
         "desc": "Team is well-suited; appropriate expertise and track record."},
        {"key": "innovation", "label": "Innovation",
         "desc": "Challenges paradigms; novel concepts, approaches, or methods."},
        {"key": "approach", "label": "Approach",
         "desc": "Sound strategy and methods; feasible; pitfalls and alternatives addressed; rigor."},
        {"key": "environment", "label": "Environment",
         "desc": "Institutional support, equipment, and resources are adequate."},
    ],
}
_GENERIC_CRITERIA = [
    {"key": "significance", "label": "Significance / Merit",
     "desc": "Important problem clearly motivated."},
    {"key": "approach", "label": "Approach",
     "desc": "Clear, feasible methods a reviewer can evaluate."},
    {"key": "impact", "label": "Impact / Outcomes",
     "desc": "Concrete expected outcomes and who benefits."},
]


def review_criteria(sponsor: Optional[str]) -> list[dict]:
    return REVIEW_CRITERIA.get((sponsor or "").upper(), _GENERIC_CRITERIA)


_SYSTEM = (
    "You are an experienced grant review panelist giving a PI a candid ADVISORY "
    "pre-submission read. You are NOT a guarantee of funding and NOT the compliance "
    "gate. Judge ONLY the DRAFT text against the listed criteria.\n"
    "RULES:\n"
    "1. Ground every assessment in the draft. For any criterion rated 'strong' or "
    "'adequate' you MUST include 'evidence': a VERBATIM quote (<=160 chars) from the "
    "draft. No quote -> rate it 'weak' or 'unclear'.\n"
    "2. Never invent content the draft doesn't contain.\n"
    "3. Be specific and constructive; fixes say WHAT to strengthen, not rewrite it for them.\n"
)


def _verify(criteria_results: list, draft_text: str) -> list:
    # Collapse all whitespace (newlines included) on BOTH sides: a pasted draft
    # is often hard-wrapped, so it contains "data,\nand" while Gemini quotes
    # "data, and". A raw substring check then fails and wrongly demotes every
    # strong/adequate rating to 'unclear'. (Mirrors the section_coach fix.)
    low = " ".join(draft_text.lower().split())
    out = []
    for c in criteria_results:
        if not isinstance(c, dict):
            continue
        rating = c.get("rating")
        ev = " ".join((c.get("evidence") or "").lower().split())
        if rating in ("strong", "adequate") and (not ev or ev not in low):
            c["rating"] = "unclear"
            c["evidence"] = ""
        out.append({
            "key": str(c.get("key", "")),
            "label": str(c.get("label", "")),
            "rating": c.get("rating", "unclear"),
            "comment": str(c.get("comment", "")),
            "evidence": str(c.get("evidence", "")),
            "fix": str(c.get("fix", "")),
        })
    return out


def _fallback(sponsor: Optional[str], criteria: list, draft_text: str) -> dict:
    words = len(draft_text.split())
    return {
        "ai": False,
        "summary": (f"Reviewer-criteria checklist for a {sponsor or 'grant'} proposal "
                    f"(~{words} words). The AI reviewer is offline, so here are the "
                    f"criteria to self-check against -- ideally have a funded colleague read it."),
        "criteria": [{"key": c["key"], "label": c["label"], "rating": "unclear",
                      "comment": c["desc"], "evidence": "", "fix": ""} for c in criteria],
        "top_risks": [],
    }


def reviewer_assessment(sponsor: Optional[str], draft_text: str,
                        context: Optional[dict] = None) -> dict:
    """Advisory reviewer-style assessment of the draft against sponsor criteria.
    AI with grounding; deterministic fallback when the LLM is unavailable."""
    criteria = review_criteria(sponsor)
    draft_text = (draft_text or "").strip()
    base = {"sponsor": (sponsor or "").upper() or None,
            "criteria_set": [c["label"] for c in criteria]}
    if not draft_text:
        return {**base, "ai": False, "summary": "Paste your draft to get reviewer-style feedback.",
                "criteria": [], "top_risks": []}

    spec = "; ".join(f"{c['label']}: {c['desc']}" for c in criteria)
    prompt = (
        f"SPONSOR: {sponsor or 'generic'}\n"
        f"REVIEW CRITERIA: {spec}\n"
        "DRAFT_TEXT:\n\"\"\"\n" + draft_text[:16000] + "\n\"\"\"\n\n"
        'Return JSON: {"summary": "<3-4 sentence reviewer impression>", '
        '"criteria": [{"key": "<criterion key>", "label": "<label>", '
        '"rating": "strong|adequate|weak|unclear", "comment": "<one sentence>", '
        '"evidence": "<verbatim quote or empty>", "fix": "<what to strengthen>"}], '
        '"top_risks": ["<the biggest reasons a reviewer might score this low>", ...]}'
    )
    ai = gemini_client.generate_json(prompt, temperature=0.2, max_output_tokens=1800,
                                     system_instruction=_SYSTEM)
    if not ai or not isinstance(ai.get("criteria"), list):
        return {**base, **_fallback(sponsor, criteria, draft_text)}

    return {
        **base,
        "ai": True,
        "summary": str(ai.get("summary", "")).strip() or "Reviewer feedback below.",
        "criteria": _verify(ai["criteria"], draft_text),
        "top_risks": [str(x) for x in (ai.get("top_risks") or []) if str(x).strip()][:6],
    }
