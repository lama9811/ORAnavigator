# -*- coding: utf-8 -*-
"""
Section Drafting Coach — Phase 2 of the "help researchers win" roadmap.
=======================================================================
Helps an inexperienced PI structure and improve a proposal section. Two modes:

  * OUTLINE  — what this section must contain, in order, for this sponsor, with
               common pitfalls and a length target. Deterministic skeleton
               (always works), optionally tailored to the PI's topic by AI.
  * REVIEW   — advisory feedback on the PI's OWN draft text: which expected
               elements are covered vs missing, plus concrete suggestions.
               AI-driven WITH evidence grounding; falls back to a deterministic
               keyword/length check when the LLM is unavailable.

Design rules (same as Draft Critic / Budget Helper):
  * COACHING ONLY — we never write the prose for the PI. Outline gives structure
    and guidance; Review gives feedback. The PI keeps authorship.
  * AI is advisory. Every AI "covered" claim must quote the draft; unquotable
    claims are dropped. When the LLM is off, deterministic output still returns.
"""

from typing import Optional

from services import gemini_client

# ── Section catalog ────────────────────────────────────────────────────────
# Each section: label, target length, what it's for, the must-have elements
# (each with keywords for the deterministic fallback check), and pitfalls.
SECTIONS = {
    "project_summary": {
        "label": "Project Summary (NSF)",
        "sponsors": ("NSF",),
        "target_words": "about 250 words / 1 page; three labeled parts",
        "purpose": "A one-page overview NSF reviewers read first. Must have three labeled sections: Overview, Intellectual Merit, and Broader Impacts.",
        "must_haves": [
            {"item": "Overview of the project", "keywords": ["overview"]},
            {"item": "A labeled 'Intellectual Merit' statement", "keywords": ["intellectual merit"]},
            {"item": "A labeled 'Broader Impacts' statement", "keywords": ["broader impact"]},
        ],
        "pitfalls": [
            "Missing the explicit 'Intellectual Merit' or 'Broader Impacts' headings (NSF returns proposals without them).",
            "Writing it like an abstract instead of three labeled parts.",
        ],
        "kb_hint": "Search the KB for 'Project Summary' and NSF proposal-components guidance.",
    },
    "project_description": {
        "label": "Project Description (NSF)",
        "sponsors": ("NSF",),
        "target_words": "up to the solicitation's page limit (often 15 pages)",
        "purpose": "The core narrative: what you'll do, why it matters, and how. Must address both Intellectual Merit and Broader Impacts.",
        "must_haves": [
            {"item": "Goals / objectives or research questions", "keywords": ["goal", "objective", "aim", "research question"]},
            {"item": "Background & significance / motivation", "keywords": ["background", "significance", "motivation", "prior work"]},
            {"item": "Approach / methods / research plan", "keywords": ["approach", "method", "research plan", "design", "procedure"]},
            {"item": "Intellectual Merit addressed", "keywords": ["intellectual merit"]},
            {"item": "Broader Impacts addressed", "keywords": ["broader impact"]},
            {"item": "Evaluation / expected outcomes / timeline", "keywords": ["evaluation", "expected outcome", "timeline", "milestone"]},
        ],
        "pitfalls": [
            "No clear research questions or objectives up front.",
            "Methods too vague to evaluate feasibility.",
            "Treating Broader Impacts as an afterthought.",
        ],
        "kb_hint": "Search the KB for 'Project Description' and the Research Strategy / proposal-writing training decks.",
    },
    "broader_impacts": {
        "label": "Broader Impacts (NSF)",
        "sponsors": ("NSF",),
        "target_words": "typically ~1 page within the Project Description",
        "purpose": "How the work benefits society beyond the research itself — one of NSF's two review criteria, weighted equally with Intellectual Merit.",
        "must_haves": [
            {"item": "Specific societal benefit(s)", "keywords": ["society", "societal", "benefit", "public", "community"]},
            {"item": "Education / training / mentoring activities", "keywords": ["education", "training", "mentor", "student", "curriculum"]},
            {"item": "Broadening participation of underrepresented groups", "keywords": ["underrepresented", "diversity", "broadening participation", "inclusion"]},
            {"item": "A plan to measure / evaluate the impact", "keywords": ["measure", "evaluate", "assessment", "metric"]},
        ],
        "pitfalls": [
            "Vague claims ('this will benefit society') with no concrete activity.",
            "No plan to assess whether the impact actually happened.",
        ],
        "kb_hint": "Search the KB for 'Broader Impacts' guidance.",
    },
    "specific_aims": {
        "label": "Specific Aims (NIH)",
        "sponsors": ("NIH",),
        "target_words": "1 page",
        "purpose": "The single most important NIH page: the problem, your central hypothesis, and 2-3 aims that test it.",
        "must_haves": [
            {"item": "The problem / gap and its importance", "keywords": ["problem", "gap", "unmet need", "importance", "significance"]},
            {"item": "Long-term goal and the proposal's objective", "keywords": ["long-term goal", "objective", "goal"]},
            {"item": "Central hypothesis", "keywords": ["hypothesis", "rationale"]},
            {"item": "2-3 numbered Specific Aims", "keywords": ["aim 1", "aim 2", "specific aim"]},
            {"item": "Expected outcomes / impact", "keywords": ["expected outcome", "impact", "payoff"]},
        ],
        "pitfalls": [
            "Aims that depend on each other (one fails, all fail).",
            "No stated hypothesis.",
            "More than one page.",
        ],
        "kb_hint": "Search the KB for the 'Specific Aims' training slides and examples.",
    },
    "research_strategy": {
        "label": "Research Strategy (NIH)",
        "sponsors": ("NIH",),
        "target_words": "6 pages (R01) / 6 pages (R21 is shorter) — check the FOA",
        "purpose": "Significance, Innovation, and Approach — the three scored sections of an NIH proposal.",
        "must_haves": [
            {"item": "Significance", "keywords": ["significance"]},
            {"item": "Innovation", "keywords": ["innovation", "innovative", "novel"]},
            {"item": "Approach (per aim, with methods)", "keywords": ["approach", "method", "design", "preliminary data"]},
            {"item": "Potential pitfalls & alternative strategies", "keywords": ["pitfall", "alternative", "limitation", "rigor"]},
        ],
        "pitfalls": [
            "No preliminary data to show feasibility.",
            "No discussion of pitfalls / alternatives (reviewers look for this).",
        ],
        "kb_hint": "Search the KB for the 'Research Strategy / Research Plan' training deck.",
    },
    "data_management_plan": {
        "label": "Data Management Plan",
        "sponsors": ("NSF", "NIH"),
        "target_words": "2 pages max (NSF)",
        "purpose": "How you'll handle, store, share, and preserve the data the project generates.",
        "must_haves": [
            {"item": "Types of data the project will produce", "keywords": ["data type", "types of data", "dataset"]},
            {"item": "Standards / formats / metadata", "keywords": ["format", "standard", "metadata"]},
            {"item": "Storage, backup, and security", "keywords": ["storage", "backup", "secur", "preserv"]},
            {"item": "Sharing / access policy", "keywords": ["shar", "access", "repository", "public"]},
            {"item": "Retention period", "keywords": ["retention", "retain", "archive"]},
        ],
        "pitfalls": ["Saying 'data available on request' with no repository or timeline."],
        "kb_hint": "Search the KB for the 'Data Management Plan' checklist/template.",
    },
    "abstract": {
        "label": "Abstract / Executive Summary",
        "sponsors": ("generic",),
        "target_words": "about 250 words",
        "purpose": "A concise standalone overview of the problem, approach, and expected outcomes.",
        "must_haves": [
            {"item": "Problem / motivation", "keywords": ["problem", "motivation", "need", "challenge"]},
            {"item": "Objective / approach", "keywords": ["objective", "approach", "aim", "method"]},
            {"item": "Expected outcomes / significance", "keywords": ["outcome", "result", "significance", "impact"]},
        ],
        "pitfalls": ["Too much background, not enough about what you'll actually do."],
        "kb_hint": "Search the KB for 'proposal components' guidance.",
    },
    "narrative": {
        "label": "Project Narrative",
        "sponsors": ("generic",),
        "target_words": "per the solicitation",
        "purpose": "The main body: goals, significance, approach, timeline, and evaluation.",
        "must_haves": [
            {"item": "Goals / objectives", "keywords": ["goal", "objective", "aim"]},
            {"item": "Background & significance", "keywords": ["background", "significance", "motivation"]},
            {"item": "Approach / methods", "keywords": ["approach", "method", "plan", "design"]},
            {"item": "Timeline / milestones", "keywords": ["timeline", "milestone", "schedule"]},
            {"item": "Evaluation / outcomes", "keywords": ["evaluation", "outcome", "assessment"]},
        ],
        "pitfalls": ["No timeline; vague methods; no way to tell if the project succeeded."],
        "kb_hint": "Search the KB for 'proposal components' and proposal-writing training.",
    },
}

# Which sections to offer for each sponsor (order matters for the UI).
_SPONSOR_ORDER = {
    "NSF": ["project_summary", "project_description", "broader_impacts", "data_management_plan"],
    "NIH": ["specific_aims", "research_strategy", "data_management_plan"],
}
_GENERIC_ORDER = ["abstract", "narrative", "data_management_plan"]


def available_sections(sponsor: Optional[str]) -> list[dict]:
    """The sections offered for a sponsor, as [{key, label}] in display order."""
    keys = _SPONSOR_ORDER.get((sponsor or "").upper(), _GENERIC_ORDER)
    return [{"key": k, "label": SECTIONS[k]["label"]} for k in keys]


def _context_line(context: Optional[dict]) -> str:
    """One compact line of solicitation context for the AI prompt, if available."""
    if not context:
        return ""
    bits = []
    if context.get("budget_cap"):
        bits.append(f"budget cap ${context['budget_cap']:,}")
    pl = context.get("page_limits") or {}
    if pl:
        bits.append("page limits " + ", ".join(f"{k}: {v}p" for k, v in pl.items()))
    return "; ".join(bits)


# ── OUTLINE ────────────────────────────────────────────────────────────────

def outline_section(sponsor: Optional[str], section_key: str,
                    topic: str = "", context: Optional[dict] = None) -> Optional[dict]:
    """Deterministic structure for the section (always returned), optionally
    enriched with AI tips tailored to `topic`. Never writes prose."""
    sec = SECTIONS.get(section_key)
    if not sec:
        return None
    outline = [{"heading": mh["item"], "guidance": ""} for mh in sec["must_haves"]]
    result = {
        "section": section_key,
        "label": sec["label"],
        "mode": "outline",
        "ai": False,
        "purpose": sec["purpose"],
        "outline": outline,
        "pitfalls": list(sec["pitfalls"]),
        "target_words": sec["target_words"],
        "kb_hint": sec["kb_hint"],
    }
    topic = (topic or "").strip()
    if not topic:
        return result
    # Optional AI: tailor one short tip per outline heading to the PI's topic.
    headings = [o["heading"] for o in outline]
    prompt = (
        f"Project topic: {topic}\n"
        f"Section: {sec['label']} for a {sponsor or 'grant'} proposal.\n"
        f"For EACH heading below, give ONE short, concrete tip (max 25 words) on what to write "
        f"for THIS topic. Do NOT write the section text itself — only coaching tips.\n"
        f"Headings: {headings}\n"
        'Return JSON: {"tips": [{"heading": "<exact heading>", "tip": "<tip>"}]}'
    )
    ai = gemini_client.generate_json(prompt, temperature=0.3, max_output_tokens=700)
    if ai and isinstance(ai.get("tips"), list):
        by_heading = {t.get("heading"): t.get("tip") for t in ai["tips"] if isinstance(t, dict)}
        for o in result["outline"]:
            tip = by_heading.get(o["heading"])
            if tip:
                o["guidance"] = str(tip)
        result["ai"] = True
    return result


# ── REVIEW ─────────────────────────────────────────────────────────────────

_REVIEW_SYSTEM = (
    "You are a senior research mentor giving ADVISORY feedback on ONE section of a "
    "draft grant proposal. You do NOT rewrite it for the author — you coach.\n"
    "RULES:\n"
    "1. Judge ONLY the DRAFT text provided. Never invent content that isn't there.\n"
    "2. For each expected element you mark 'covered', you MUST include an 'evidence' "
    "field: a VERBATIM quote (<=160 chars) from the DRAFT showing it. No quote -> not covered.\n"
    "3. Be specific and constructive. Suggestions say WHAT to add/clarify, never write the prose.\n"
)


def _keyword_review(sec: dict, draft_text: str) -> dict:
    """Deterministic fallback: keyword presence per expected element + length."""
    low = draft_text.lower()
    words = len(draft_text.split())
    checklist = []
    for mh in sec["must_haves"]:
        hit = next((kw for kw in mh["keywords"] if kw in low), None)
        checklist.append({
            "item": mh["item"],
            "status": "covered" if hit else "unclear",
            "note": ("Mentioned." if hit else "Couldn't find this — make sure it's clearly present and labeled."),
            "evidence": "",
        })
    missing = [c["item"] for c in checklist if c["status"] != "covered"]
    summary = (f"Quick check of ~{words} words. "
               + ("Looks like it touches the main elements." if not missing
                  else f"{len(missing)} expected element(s) not clearly found."))
    return {
        "ai": False,
        "summary": summary,
        "checklist": checklist,
        "suggestions": [f"Make sure to clearly address: {m}." for m in missing][:5],
        "word_count": words,
    }


def _verify_evidence(checklist: list, draft_text: str) -> list:
    """Drop 'covered' claims whose evidence isn't actually in the draft (anti-
    hallucination, mirrors draft_critic). Demote them to 'unclear'."""
    low = draft_text.lower()
    out = []
    for c in checklist:
        if not isinstance(c, dict):
            continue
        status = c.get("status")
        ev = (c.get("evidence") or "").strip()
        if status == "covered":
            if not ev or ev.lower() not in low:
                c["status"] = "unclear"
                c["evidence"] = ""
                if not c.get("note"):
                    c["note"] = "Could not verify this is clearly present — double-check."
        out.append({
            "item": str(c.get("item", "")),
            "status": c.get("status", "unclear"),
            "note": str(c.get("note", "")),
            "evidence": str(c.get("evidence", "")),
        })
    return out


def review_section(sponsor: Optional[str], section_key: str, draft_text: str,
                   context: Optional[dict] = None) -> Optional[dict]:
    """Advisory feedback on the PI's draft. AI with evidence grounding; falls
    back to a deterministic keyword/length check when the LLM is unavailable."""
    sec = SECTIONS.get(section_key)
    if not sec:
        return None
    draft_text = (draft_text or "").strip()
    base = {
        "section": section_key,
        "label": sec["label"],
        "mode": "review",
        "target_words": sec["target_words"],
    }
    if not draft_text:
        return {**base, "ai": False, "summary": "Paste your draft of this section to get feedback.",
                "checklist": [], "suggestions": [], "word_count": 0}

    ctx = _context_line(context)
    expected = [mh["item"] for mh in sec["must_haves"]]
    prompt = (
        f"SECTION: {sec['label']} for a {sponsor or 'grant'} proposal.\n"
        f"PURPOSE: {sec['purpose']}\n"
        f"EXPECTED ELEMENTS: {expected}\n"
        + (f"SOLICITATION CONSTRAINTS: {ctx}\n" if ctx else "")
        + "DRAFT_TEXT:\n\"\"\"\n" + draft_text[:12000] + "\n\"\"\"\n\n"
        'Return JSON: {"summary": "<2-3 sentences>", '
        '"checklist": [{"item": "<expected element>", "status": "covered|partial|missing", '
        '"note": "<one sentence>", "evidence": "<verbatim quote or empty>"}], '
        '"suggestions": ["<concrete next step>", ...]}'
    )
    ai = gemini_client.generate_json(prompt, temperature=0.2, max_output_tokens=1400,
                                     system_instruction=_REVIEW_SYSTEM)
    if not ai or not isinstance(ai.get("checklist"), list):
        return {**base, **_keyword_review(sec, draft_text)}

    checklist = _verify_evidence(ai["checklist"], draft_text)
    suggestions = [str(s) for s in (ai.get("suggestions") or []) if str(s).strip()][:6]
    return {
        **base,
        "ai": True,
        "summary": str(ai.get("summary", "")).strip() or "Feedback below.",
        "checklist": checklist,
        "suggestions": suggestions,
        "word_count": len(draft_text.split()),
    }
