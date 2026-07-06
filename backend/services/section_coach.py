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
from services import sample_proposals as _samples

# Each coach section -> an authored entry in the Sample Proposals Library, so a
# PI can see a worked example of what a strong version reads like. Validated
# against get_sample at call time, so a renamed/removed sample yields no link
# rather than a dead one. Only sections with a genuinely relevant authored
# sample are mapped; the rest get no hint.
SECTION_SAMPLES = {
    "specific_aims": "nih-r01-funded-application",
    "research_strategy": "nih-r01-funded-application",
    "project_summary": "nsf-full-funded-proposal",
    "project_description": "nsf-full-funded-proposal",
    "broader_impacts": "nsf-full-funded-proposal",
    "data_management_plan": "nsf-full-funded-proposal",
}


def _sample_hint(section_key: str) -> Optional[dict]:
    """{"id", "title"} for the worked-example sample mapped to this section, or
    None when there's no mapping or the sample no longer exists."""
    sid = SECTION_SAMPLES.get(section_key)
    if not sid:
        return None
    s = _samples.get_sample(sid)
    if not s:
        return None
    return {"id": sid, "title": s.get("title", "")}

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


# ── Reviewer-lens rubric ────────────────────────────────────────────────────
# The ACTUAL criteria a review panel scores against, per sponsor. This is the
# authoritative, deterministic framework (always present, even with AI off) so a
# first-time PI writes TO the criteria and sees how their draft lands. Sourced
# from the public NSF PAPPG and NIH peer-review criteria.
REVIEW_RUBRICS = {
    "NSF": [
        {"criterion": "Intellectual Merit", "weight": "Equally weighted",
         "asks": "Does the work advance knowledge? Is the approach sound, creative, and feasible, and is the team qualified?"},
        {"criterion": "Broader Impacts", "weight": "Equally weighted",
         "asks": "How does the work benefit society and broaden participation (education, outreach, diversity)?"},
    ],
    "NIH": [
        {"criterion": "Significance", "weight": "Scored 1-9 → Overall Impact",
         "asks": "Does it address an important problem? Will success change the field or practice?"},
        {"criterion": "Innovation", "weight": "Scored 1-9 → Overall Impact",
         "asks": "Does it challenge current paradigms or use novel concepts, methods, or interventions?"},
        {"criterion": "Approach", "weight": "Scored 1-9 → Overall Impact",
         "asks": "Are the strategy, methods, and analyses rigorous, with pitfalls and alternatives considered?"},
        {"criterion": "Investigator(s)", "weight": "Scored 1-9 → Overall Impact",
         "asks": "Are the PI and team suited to the project (training, expertise, track record)?"},
        {"criterion": "Environment", "weight": "Scored 1-9 → Overall Impact",
         "asks": "Will the institutional resources and support enable success?"},
    ],
}
_GENERIC_RUBRIC = [
    {"criterion": "Significance", "weight": "Reviewer judgment",
     "asks": "Why does this matter, and to whom?"},
    {"criterion": "Approach / feasibility", "weight": "Reviewer judgment",
     "asks": "Is the plan sound, and can this team actually do it?"},
    {"criterion": "Outcomes / impact", "weight": "Reviewer judgment",
     "asks": "What changes if the project succeeds?"},
]


def review_rubric(sponsor: Optional[str]) -> list[dict]:
    """The panel's scoring criteria for a sponsor (deterministic, always
    available). Falls back to a generic rubric for unknown sponsors."""
    return [dict(c) for c in REVIEW_RUBRICS.get((sponsor or "").upper(), _GENERIC_RUBRIC)]


def available_sections(sponsor: Optional[str]) -> list[dict]:
    """The sections offered for a sponsor, as [{key, label}] in display order."""
    keys = _SPONSOR_ORDER.get((sponsor or "").upper(), _GENERIC_ORDER)
    out = []
    for k in keys:
        tmin, tmax = WORD_TARGETS.get(k, (None, None))
        out.append({"key": k, "label": SECTIONS[k]["label"],
                    "target_min": tmin, "target_max": tmax})
    return out


# Numeric word targets for the live length meter (min, max). None = open-ended
# (page-limited sections vary by solicitation, so we don't guess a word cap).
WORD_TARGETS = {
    "project_summary": (200, 500),
    "project_description": (None, None),
    "broader_impacts": (250, 700),
    "specific_aims": (350, 650),       # ~1 page
    "research_strategy": (None, None),
    "data_management_plan": (None, 1000),   # ~2 pages
    "abstract": (150, 300),
    "narrative": (None, None),
}


def _targets(section_key: str):
    return WORD_TARGETS.get(section_key, (None, None))


# ── Clarity check (deterministic, no AI) ───────────────────────────────────
import re as _re

_PASSIVE_RE = _re.compile(r"\b(?:is|are|was|were|be|been|being)\s+\w+ed\b", _re.IGNORECASE)
_ACRONYM_RE = _re.compile(r"\b[A-Z]{2,6}s?\b")
_COMMON_ACRONYMS = {
    "PI", "PIS", "NSF", "NIH", "DOD", "DOE", "NASA", "USDA", "EPA", "USA", "US",
    "DNA", "RNA", "AI", "ML", "PHD", "USA", "FAQ", "OK", "DMP", "RCR", "IRB",
    "IACUC", "COI", "STEM", "K12", "K", "PD", "CO", "MSU",
}
_VAGUE_WORDS = ["very", "really", "clearly", "obviously", "a number of",
                "various", "several", "some", "many", "significantly", "novel"]


def _sentences(text: str):
    return [s.strip() for s in _re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def clarity_check(text: str) -> list[dict]:
    """Deterministic writing-clarity flags (no AI). Returns a list of
    {type, severity, message}. Empty list = nothing flagged."""
    text = (text or "").strip()
    out: list[dict] = []
    if not text:
        return out

    # Long sentences (hard for reviewers to parse).
    long_sents = [s for s in _sentences(text) if len(s.split()) > 40]
    if long_sents:
        out.append({"type": "long_sentences", "severity": "warn",
                    "message": f"{len(long_sents)} sentence(s) run over 40 words — split them so reviewers can follow."})

    # Passive voice (overuse buries who does what).
    passive = len(_PASSIVE_RE.findall(text))
    if passive >= 4:
        out.append({"type": "passive_voice", "severity": "info",
                    "message": f"{passive} likely passive-voice phrases — prefer active voice ('we measured', not 'was measured')."})

    # Acronyms used before being defined as 'Term (ACRONYM)'.
    undefined = []
    for m in _ACRONYM_RE.finditer(text):
        ac = m.group(0).rstrip("s").upper()
        if ac in _COMMON_ACRONYMS or len(ac) < 2:
            continue
        if f"({m.group(0)})" in text or f"({ac})" in text.upper():
            continue
        undefined.append(ac)
    undefined = sorted(set(undefined))
    if undefined:
        out.append({"type": "acronyms", "severity": "warn",
                    "message": "Acronyms used without being spelled out first: "
                               + ", ".join(undefined[:8]) + ". Define each on first use."})

    # Vague / weasel words.
    low = text.lower()
    found_vague = [w for w in _VAGUE_WORDS if _re.search(r"\b" + _re.escape(w) + r"\b", low)]
    if len(found_vague) >= 3:
        out.append({"type": "vague", "severity": "info",
                    "message": "Vague words weaken claims: " + ", ".join(found_vague[:6])
                               + ". Be specific (numbers, named methods)."})
    return out


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
    ra = context.get("required_attachments") or []
    if ra:
        bits.append("required attachments: " + ", ".join(str(a) for a in ra[:8]))
    elig = (context.get("eligibility") or "").strip()
    if elig:
        bits.append(f"eligibility: {elig[:200]}")
    return "; ".join(bits)


def _solicitation_constraints(context: Optional[dict]) -> dict:
    """The solicitation facts worth showing next to the section (for the
    'match THIS solicitation' panel). Empty values omitted."""
    if not context:
        return {}
    out = {}
    if context.get("page_limits"):
        out["page_limits"] = context["page_limits"]
    if context.get("required_attachments"):
        out["required_attachments"] = context["required_attachments"]
    if (context.get("eligibility") or "").strip():
        out["eligibility"] = context["eligibility"].strip()
    return out


# ── OUTLINE ────────────────────────────────────────────────────────────────

def outline_section(sponsor: Optional[str], section_key: str,
                    topic: str = "", context: Optional[dict] = None) -> Optional[dict]:
    """Deterministic structure for the section (always returned), optionally
    enriched with AI tips tailored to `topic`. Never writes prose."""
    sec = SECTIONS.get(section_key)
    if not sec:
        return None
    outline = [{"heading": mh["item"], "guidance": ""} for mh in sec["must_haves"]]
    tmin, tmax = _targets(section_key)
    result = {
        "section": section_key,
        "label": sec["label"],
        "mode": "outline",
        "ai": False,
        "purpose": sec["purpose"],
        "outline": outline,
        "pitfalls": list(sec["pitfalls"]),
        "target_words": sec["target_words"],
        "target_min": tmin,
        "target_max": tmax,
        "kb_hint": sec["kb_hint"],
        # Surface the funder's rules in outline mode too (review already does),
        # and a worked-example link when one exists.
        "solicitation_constraints": _solicitation_constraints(context),
        "sample": _sample_hint(section_key),
        # The panel's scoring criteria, so the PI writes TO the rubric up front.
        "rubric": review_rubric(sponsor),
    }
    topic = (topic or "").strip()
    ctx = _context_line(context)
    # Tailor when the PI gave a topic OR we have this solicitation's constraints —
    # so a proposal with a solicitation but no typed topic still gets tailored tips.
    if not topic and not ctx:
        return result
    # Optional AI: tailor one short tip per outline heading to the PI's topic
    # and/or THIS solicitation's stated constraints. Coaching tips only.
    headings = [o["heading"] for o in outline]
    prompt = (
        (f"Project topic: {topic}\n" if topic else "")
        + f"Section: {sec['label']} for a {sponsor or 'grant'} proposal.\n"
        + (f"This solicitation's constraints: {ctx}\n" if ctx else "")
        + "For EACH heading below, give ONE short, concrete tip (max 25 words) on what to write "
        + ("for THIS topic" if topic else "for this section")
        + (" given the solicitation's constraints" if ctx else "")
        + ". Do NOT write the section text itself — only coaching tips.\n"
        + f"Headings: {headings}\n"
        + 'Return JSON: {"tips": [{"heading": "<exact heading>", "tip": "<tip>"}]}'
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
    "4. If SOLICITATION CONSTRAINTS are given, also check whether the draft addresses the "
    "requirements they state (eligibility, scope, page limits). Add a checklist item for any "
    "stated requirement the draft does NOT clearly address, marked 'missing' or 'partial'.\n"
    "5. If REVIEW CRITERIA are given, write the 'summary' in a reviewer's voice and add "
    "'reviewer_notes': one advisory note per criterion on how a panel would judge THIS draft "
    "and what would strengthen it. These are OPINIONS/QUESTIONS (no quote required) and must "
    "NOT assert the draft 'covers' anything — coverage stays in the grounded checklist (rule 2).\n"
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


def _quote_in(text: str, quote: str) -> bool:
    """True if `quote` appears in `text`, ignoring whitespace/line-wrap and case.
    Collapse ALL whitespace runs (newlines included) on BOTH sides before
    matching: a pasted draft is often hard-wrapped, so the draft contains
    "health,\\nand" while Gemini quotes "health, and"; a raw substring check
    then fails and would (wrongly) reject a real quote. Shared by the section
    evidence check and the cross-section coherence check (golden rule 2)."""
    q = " ".join((quote or "").lower().split())
    if not q:
        return False
    return q in " ".join((text or "").lower().split())


def _verify_evidence(checklist: list, draft_text: str) -> list:
    """Drop 'covered' claims whose evidence isn't actually in the draft (anti-
    hallucination, mirrors draft_critic). Demote them to 'unclear'."""
    out = []
    for c in checklist:
        if not isinstance(c, dict):
            continue
        status = c.get("status")
        ev = (c.get("evidence") or "").strip()
        if status == "covered":
            if not _quote_in(draft_text, ev):
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
    tmin, tmax = _targets(section_key)
    base = {
        "section": section_key,
        "label": sec["label"],
        "mode": "review",
        "target_words": sec["target_words"],
        "target_min": tmin,
        "target_max": tmax,
        "solicitation_constraints": _solicitation_constraints(context),
        "sample": _sample_hint(section_key),
        "rubric": review_rubric(sponsor),
    }
    if not draft_text:
        return {**base, "ai": False, "summary": "Paste your draft of this section to get feedback.",
                "checklist": [], "suggestions": [], "reviewer_notes": [], "word_count": 0,
                "clarity": [], "length_status": "none"}

    words = len(draft_text.split())
    if tmax and words > tmax * 1.1:
        length_status = "long"
    elif tmin and words < tmin * 0.5:
        length_status = "short"
    else:
        length_status = "ok"
    extra = {"clarity": clarity_check(draft_text), "length_status": length_status}

    ctx = _context_line(context)
    expected = [mh["item"] for mh in sec["must_haves"]]
    rubric = review_rubric(sponsor)
    criteria_line = "; ".join(f"{c['criterion']} ({c['asks']})" for c in rubric)
    prompt = (
        f"SECTION: {sec['label']} for a {sponsor or 'grant'} proposal.\n"
        f"PURPOSE: {sec['purpose']}\n"
        f"EXPECTED ELEMENTS: {expected}\n"
        f"REVIEW CRITERIA: {criteria_line}\n"
        + (f"SOLICITATION CONSTRAINTS: {ctx}\n" if ctx else "")
        + "DRAFT_TEXT:\n\"\"\"\n" + draft_text[:12000] + "\n\"\"\"\n\n"
        'Return JSON: {"summary": "<2-3 sentences, reviewer voice>", '
        '"checklist": [{"item": "<expected element>", "status": "covered|partial|missing", '
        '"note": "<one sentence>", "evidence": "<verbatim quote or empty>"}], '
        '"reviewer_notes": [{"criterion": "<exact criterion>", "note": "<advisory, how a panel judges THIS draft>"}], '
        '"suggestions": ["<concrete next step>", ...]}'
    )
    ai = gemini_client.generate_json(prompt, temperature=0.2, max_output_tokens=1800,
                                     system_instruction=_REVIEW_SYSTEM)
    if not ai or not isinstance(ai.get("checklist"), list):
        return {**base, **_keyword_review(sec, draft_text), "reviewer_notes": [], **extra}

    checklist = _verify_evidence(ai["checklist"], draft_text)
    suggestions = [str(s) for s in (ai.get("suggestions") or []) if str(s).strip()][:6]
    # Reviewer notes are advisory opinions keyed to a real rubric criterion — no
    # evidence grounding (they're questions/judgments, not coverage claims).
    valid_criteria = {c["criterion"] for c in rubric}
    reviewer_notes = [
        {"criterion": str(n.get("criterion", "")).strip(), "note": str(n.get("note", "")).strip()}
        for n in (ai.get("reviewer_notes") or [])
        if isinstance(n, dict) and str(n.get("criterion", "")).strip() in valid_criteria
        and str(n.get("note", "")).strip()
    ]
    return {
        **base,
        "ai": True,
        "summary": str(ai.get("summary", "")).strip() or "Feedback below.",
        "checklist": checklist,
        "reviewer_notes": reviewer_notes,
        "suggestions": suggestions,
        "word_count": words,
        **extra,
    }


# ── CROSS-SECTION COHERENCE ─────────────────────────────────────────────────
# A proposal fails review not just on weak sections but on sections that
# disagree: a Research Strategy that drops an aim, a scope the PI isn't eligible
# for, a narrative timeline the budget doesn't fund. This advisory check compares
# the PI's SAVED sections against each other (and against eligibility/budget).
# AI-driven WITH the same evidence grounding as review_section; deterministic
# fallback when the LLM is off. Coaching only — never rewrites anything.

_COHERENCE_SYSTEM = (
    "You are a senior research mentor checking whether the SECTIONS of a draft grant "
    "proposal AGREE with each other. You do NOT rewrite anything — you flag inconsistencies.\n"
    "RULES:\n"
    "1. Judge ONLY the text provided for each side. Never invent content.\n"
    "2. For any pair you mark 'aligned', you MUST quote BOTH sides: 'evidence_a' is a VERBATIM "
    "quote (<=160 chars) from side A and 'evidence_b' a VERBATIM quote from side B that show the "
    "agreement. No quotes from both -> it is NOT 'aligned' (use 'gap' or 'unclear').\n"
    "3. Mark 'gap' when the sides conflict or one omits what the other promises; 'unclear' when "
    "you can't tell. Notes are specific and constructive.\n"
)


def _coherence_candidate_pairs(drafts: dict, context: Optional[dict],
                               budget: Optional[dict]) -> list[dict]:
    """Build the cross-checks that are POSSIBLE given what's saved. Each:
    {id, a_label, b_label, a_text, b_text, question}. Deterministic."""
    def text(k):
        return (drafts.get(k) or "").strip()

    checks: list[dict] = []

    # 1. Specific Aims <-> Research Strategy (NIH): does the Strategy address each aim?
    if text("specific_aims") and text("research_strategy"):
        checks.append({
            "id": "aims_strategy",
            "a_label": "Specific Aims", "b_label": "Research Strategy",
            "a_text": text("specific_aims"), "b_text": text("research_strategy"),
            "question": "Does the Research Strategy address EACH aim named in the Specific Aims "
                        "(Aim 1, Aim 2, ...)? Flag any aim with no matching approach.",
        })

    # 2. Project Summary <-> Project Description (NSF): do the promises match?
    if text("project_summary") and text("project_description"):
        checks.append({
            "id": "summary_description",
            "a_label": "Project Summary", "b_label": "Project Description",
            "a_text": text("project_summary"), "b_text": text("project_description"),
            "question": "Do the goals and claims in the Project Summary match what the Project "
                        "Description actually proposes? Flag claims the description doesn't deliver.",
        })

    # The PI's main narrative section, used for scope/eligibility + timeline checks.
    narrative_key = next((k for k in ("project_description", "research_strategy", "narrative")
                          if text(k)), None)

    # 3. Scope <-> eligibility (does the drafted scope conflict with who may apply / what's funded?)
    elig = ((context or {}).get("eligibility") or "").strip()
    if narrative_key and elig:
        checks.append({
            "id": "scope_eligibility",
            "a_label": SECTIONS[narrative_key]["label"], "b_label": "Solicitation eligibility",
            "a_text": text(narrative_key), "b_text": elig,
            "question": "Does the proposed scope/audience in the draft fit the solicitation's "
                        "eligibility/scope text? Flag any conflict (wrong applicant, audience, or focus).",
        })

    # 4. Timeline <-> staffing (only when a budget exists): does the narrative's timeline match
    #    the budget's project length and staffing?
    if narrative_key and budget:
        my = budget.get("multi_year") or {}
        years = my.get("project_years")
        people = [p.get("name") or "a team member" for p in (budget.get("personnel") or [])]
        if years or people:
            b_text = (f"The budget funds a {years}-year project. "
                      if years else "The budget covers a single year. ")
            b_text += ("Budgeted personnel: " + ", ".join(people) + "."
                       if people else "No personnel are budgeted.")
            checks.append({
                "id": "timeline_staffing",
                "a_label": SECTIONS[narrative_key]["label"], "b_label": "Budget",
                "a_text": text(narrative_key), "b_text": b_text,
                "question": "Does the timeline and team described in the draft match the budget "
                            "(project length in years, and the people funded)?",
            })

    return checks


def _coherence_fallback(checks: list[dict]) -> dict:
    """Deterministic coherence result when the LLM is unavailable. No fabricated
    quotes: report which cross-checks are possible, plus a light keyword pass for
    the aims<->strategy case, all as advisory 'unclear'/'gap' notes."""
    pairs = []
    for c in checks:
        status, note = "unclear", (f"Compare your {c['a_label']} against your {c['b_label']} "
                                   f"by hand: {c['question']}")
        if c["id"] == "aims_strategy":
            a_low, b_low = c["a_text"].lower(), c["b_text"].lower()
            aims = [n for n in ("aim 1", "aim 2", "aim 3", "aim 4")
                    if n in a_low]
            missing = [n for n in aims if n not in b_low]
            if aims and missing:
                status = "gap"
                note = (f"Your Specific Aims name {', '.join(a.title() for a in aims)}, but "
                        f"{', '.join(m.title() for m in missing)} "
                        f"{'is' if len(missing) == 1 else 'are'} not referenced by label in the "
                        f"Research Strategy — make sure each aim has a matching approach.")
        pairs.append({"a": c["a_label"], "b": c["b_label"], "status": status,
                      "note": note, "evidence_a": "", "evidence_b": ""})
    return {
        "ai": False,
        "ready": True,
        "summary": "Cross-section check (offline mode): compare these section pairs by hand.",
        "pairs": pairs,
        "suggestions": [],
    }


def coherence_check(sponsor: Optional[str], drafts: dict,
                    context: Optional[dict] = None, budget: Optional[dict] = None) -> dict:
    """Advisory cross-section coherence check over the PI's SAVED sections.
    Grounded (every 'aligned' claim quotes both sides, verified with `_quote_in`)
    and degrades to a deterministic result when the LLM is off. Returns
    {ai, ready, summary, pairs:[{a,b,status,note,evidence_a,evidence_b}], suggestions}."""
    drafts = {k: v for k, v in (drafts or {}).items() if (v or "").strip()}
    if len(drafts) < 2:
        return {"ai": False, "ready": False,
                "summary": "Save at least two sections to check that they agree with each other.",
                "pairs": [], "suggestions": []}

    checks = _coherence_candidate_pairs(drafts, context, budget)
    if not checks:
        return {"ai": False, "ready": False,
                "summary": "No cross-section checks apply to the sections you've saved yet. "
                           "Save a pair like Specific Aims + Research Strategy to compare them.",
                "pairs": [], "suggestions": []}

    lines = ["Check whether these proposal sides AGREE. For each pair, judge alignment and quote both sides.\n"]
    for i, c in enumerate(checks):
        lines.append(f"PAIR {i} — A={c['a_label']} | B={c['b_label']}\nQUESTION: {c['question']}\n"
                     f"A_TEXT:\n\"\"\"\n{c['a_text'][:6000]}\n\"\"\"\n"
                     f"B_TEXT:\n\"\"\"\n{c['b_text'][:6000]}\n\"\"\"\n")
    lines.append('Return JSON: {"summary": "<2-3 sentences>", '
                 '"pairs": [{"index": <PAIR number>, "status": "aligned|gap|unclear", '
                 '"note": "<one sentence>", "evidence_a": "<verbatim from A or empty>", '
                 '"evidence_b": "<verbatim from B or empty>"}], '
                 '"suggestions": ["<concrete fix>", ...]}')
    prompt = "\n".join(lines)

    ai = gemini_client.generate_json(prompt, temperature=0.2, max_output_tokens=1800,
                                     system_instruction=_COHERENCE_SYSTEM)
    if not ai or not isinstance(ai.get("pairs"), list):
        return _coherence_fallback(checks)

    by_index = {}
    for p in ai["pairs"]:
        if isinstance(p, dict):
            try:
                by_index[int(p.get("index"))] = p
            except (TypeError, ValueError):
                continue

    pairs = []
    for i, c in enumerate(checks):
        p = by_index.get(i, {})
        status = p.get("status") if p.get("status") in ("aligned", "gap", "unclear") else "unclear"
        ev_a = (p.get("evidence_a") or "").strip()
        ev_b = (p.get("evidence_b") or "").strip()
        note = str(p.get("note", "")).strip()
        # Grounding: 'aligned' must be backed by a real quote from BOTH sides.
        if status == "aligned" and not (_quote_in(c["a_text"], ev_a) and _quote_in(c["b_text"], ev_b)):
            status = "unclear"
            ev_a = ev_b = ""
            if not note:
                note = "Could not verify the two sides line up — double-check by hand."
        pairs.append({"a": c["a_label"], "b": c["b_label"], "status": status,
                      "note": note or c["question"], "evidence_a": ev_a, "evidence_b": ev_b})

    suggestions = [str(s) for s in (ai.get("suggestions") or []) if str(s).strip()][:6]
    return {
        "ai": True,
        "ready": True,
        "summary": str(ai.get("summary", "")).strip() or "Cross-section feedback below.",
        "pairs": pairs,
        "suggestions": suggestions,
    }


# ── SOLICITATION RESPONSIVENESS MATRIX ──────────────────────────────────────
# The #1 reason a proposal gets triaged is non-responsiveness: the draft doesn't
# visibly address something the solicitation asked for, or a stated review
# criterion is never covered. This proposal-level check maps each explicit ASK
# (required narrative elements/attachments, named page-limited sections, the
# sponsor's published review criteria, eligibility/scope) to WHERE the PI's SAVED
# drafts address it, with a grounded status + verbatim quote.
#
# Boundary (golden rule 1 + the deleted Fundability tool): this is a GROUNDED
# COVERAGE checklist, never a score/grade/verdict. It describes the drafts, not
# the proposal's odds. The requirements list is assembled DETERMINISTICALLY from
# already-grounded state; the AI only assigns a coverage status per fixed
# requirement id and can NEVER invent a requirement (anti-hallucination).

_RESPONSIVENESS_SYSTEM = (
    "You are a grant program officer checking whether a draft proposal RESPONDS to what the "
    "solicitation asked for. You do NOT rewrite anything and you do NOT judge quality, rate, or "
    "score it — you only check COVERAGE: for each requirement, is it addressed in the draft text?\n"
    "RULES:\n"
    "1. Judge ONLY the DRAFT SECTIONS provided. Never invent content.\n"
    "2. Assess ONLY the requirements in the REQUIREMENTS list, by their exact id. NEVER add, rename, "
    "or split requirements. Return at most one row per id.\n"
    "3. For any requirement you mark 'addressed' or 'partial', you MUST include 'evidence': a VERBATIM "
    "quote (<=160 chars) copied from a draft that shows it. No verbatim quote -> use 'not_found'.\n"
    "4. Use 'not_found' when the drafts do not cover it. Do NOT grade, score, or rate — coverage only.\n"
)

# Map a required-attachment / page-limit name onto a known coach section key so
# coverage can be attributed to the right draft. Best-effort, normalized substring.
_SECTION_ALIASES = {
    "data management plan": "data_management_plan",
    "data management": "data_management_plan",
    "project summary": "project_summary",
    "project description": "project_description",
    "broader impacts": "broader_impacts",
    "specific aims": "specific_aims",
    "research strategy": "research_strategy",
    "abstract": "abstract",
    "executive summary": "abstract",
    "project narrative": "narrative",
    "narrative": "narrative",
}


def _match_section_key(name: str) -> Optional[str]:
    """Best-effort map of a solicitation ask name to a coach section key, or None."""
    n = " ".join((name or "").lower().split())
    if n in SECTIONS:
        return n
    for alias, key in _SECTION_ALIASES.items():
        if alias in n:
            return key
    return None


def _responsiveness_requirements(sponsor: Optional[str],
                                 context: Optional[dict]) -> list[dict]:
    """The solicitation's explicit ASKS, assembled DETERMINISTICALLY from grounded
    state (never AI-invented). Each: {id, requirement, source, detail, section_key}.
    Deduped by normalized requirement label; order-stable."""
    context = context or {}
    out: list[dict] = []
    seen: set = set()

    def add(req_id: str, requirement: str, source: str,
            detail: str = "", section_key: Optional[str] = None):
        norm = " ".join((requirement or "").lower().split())
        if not norm or norm in seen:
            return
        seen.add(norm)
        out.append({"id": req_id, "requirement": requirement, "source": source,
                    "detail": detail, "section_key": section_key})

    # 1. Named / page-limited narrative sections (carry a section_key + page limit).
    for key, limit in (context.get("page_limits") or {}).items():
        label = SECTIONS[key]["label"] if key in SECTIONS else str(key).replace("_", " ").title()
        detail = f"{limit}-page limit" if isinstance(limit, int) else ""
        add(f"section:{key}", label, "section", detail, section_key=key)

    # 2. Required attachments / narrative elements (grounded at ingestion).
    for att in (context.get("required_attachments") or []):
        att = str(att).strip()
        if att:
            add(f"attachment:{att.lower()}", att, "attachment",
                section_key=_match_section_key(att))

    # 3. Stated review criteria (always present via the deterministic rubric).
    for c in review_rubric(sponsor):
        add(f"criterion:{c['criterion'].lower()}", c["criterion"], "criterion", c.get("asks", ""))

    # 4. Eligibility / scope fit.
    elig = (context.get("eligibility") or "").strip()
    if elig:
        add("eligibility", "Eligibility / scope fit", "eligibility", elig[:200])

    return out


def _where_quote(labeled: dict, quote: str) -> str:
    """The label of the saved section whose text contains `quote` (verified with
    `_quote_in`), or "" — so `where` is attributed authoritatively, not trusted
    from the model."""
    for label, txt in labeled.items():
        if _quote_in(txt, quote):
            return label
    return ""


def _responsiveness_fallback(requirements: list[dict], drafts: dict) -> dict:
    """Deterministic responsiveness result when the LLM is unavailable. Never
    fabricates a quote and never guesses 'not_found' (offline we can't prove
    absence): every row is 'check_by_hand'."""
    drafts = {k: v for k, v in (drafts or {}).items() if (v or "").strip()}
    rows = []
    for r in requirements:
        sk = r.get("section_key")
        if sk and (drafts.get(sk) or "").strip():
            label = SECTIONS[sk]["label"] if sk in SECTIONS else sk
            note = f"You have a draft for {label}; confirm by hand that it addresses this."
        else:
            note = "Check by hand whether your drafts address this requirement."
        rows.append({"requirement": r["requirement"], "source": r["source"],
                     "detail": r.get("detail", ""), "status": "check_by_hand",
                     "note": note, "where": "", "evidence": ""})
    return {
        "ai": False,
        "ready": True,
        "summary": "Responsiveness check (offline mode): confirm by hand that your drafts "
                   "address each requirement below.",
        "rows": rows,
        "suggestions": [],
    }


def responsiveness_matrix(sponsor: Optional[str], drafts: dict,
                          context: Optional[dict] = None) -> dict:
    """Advisory whole-proposal responsiveness check: does each solicitation ASK
    appear in the PI's SAVED drafts? Grounded (every 'addressed'/'partial' quotes
    the draft, verified with `_quote_in`); degrades to a deterministic result when
    the LLM is off. Coverage only — never a score or verdict. Returns
    {ai, ready, summary, rows:[{requirement,source,detail,status,note,where,evidence}],
    suggestions}."""
    drafts = {k: v for k, v in (drafts or {}).items() if (v or "").strip()}
    if not drafts:
        return {"ai": False, "ready": False,
                "summary": "Save at least one draft section to check it against the "
                           "solicitation's requirements.",
                "rows": [], "suggestions": []}

    requirements = _responsiveness_requirements(sponsor, context)
    if not requirements:
        return {"ai": False, "ready": False,
                "summary": "No solicitation requirements are on file to check against yet. "
                           "Start this proposal from a solicitation so its required elements "
                           "and review criteria are available.",
                "rows": [], "suggestions": []}

    # Labeled corpus of saved drafts + a flat union for grounding / where-attribution.
    labeled: dict = {}
    for k, v in drafts.items():
        label = SECTIONS[k]["label"] if k in SECTIONS else k
        labeled[label] = v.strip()
    union_text = "\n\n".join(labeled.values())

    lines = ["Check whether the DRAFT SECTIONS respond to each REQUIREMENT. Coverage only — "
             "never judge quality.\n", "REQUIREMENTS:"]
    for r in requirements:
        detail = f" — {r['detail']}" if r.get("detail") else ""
        lines.append(f"  id={r['id']} | {r['requirement']} ({r['source']}){detail}")
    lines.append("\nDRAFT SECTIONS:")
    for label, txt in labeled.items():
        lines.append(f'--- {label} ---\n"""\n{txt[:6000]}\n"""\n')
    lines.append('Return JSON: {"summary": "<2-3 sentences>", '
                 '"rows": [{"id": "<requirement id>", "status": "addressed|partial|not_found", '
                 '"note": "<one sentence>", "evidence": "<verbatim quote from a draft, or empty>"}], '
                 '"suggestions": ["<concrete gap-closer>", ...]}')
    prompt = "\n".join(lines)

    ai = gemini_client.generate_json(prompt, temperature=0.2, max_output_tokens=1800,
                                     system_instruction=_RESPONSIVENESS_SYSTEM)
    if not ai or not isinstance(ai.get("rows"), list):
        return _responsiveness_fallback(requirements, drafts)

    by_id: dict = {}
    for row in ai["rows"]:
        if isinstance(row, dict) and row.get("id"):
            by_id[str(row.get("id"))] = row   # ignore any id not in our fixed set below

    rows = []
    for r in requirements:
        p = by_id.get(r["id"], {})
        status = p.get("status") if p.get("status") in ("addressed", "partial", "not_found") else "not_found"
        ev = (p.get("evidence") or "").strip()
        note = str(p.get("note", "")).strip()
        where = ""
        # Grounding: 'addressed'/'partial' must be backed by a real quote from the drafts.
        if status in ("addressed", "partial"):
            if ev and _quote_in(union_text, ev):
                where = _where_quote(labeled, ev)
            else:
                status = "check_by_hand"
                ev = ""
                if not note:
                    note = "Couldn't verify this is addressed in your drafts — check by hand."
        rows.append({
            "requirement": r["requirement"],
            "source": r["source"],
            "detail": r.get("detail", ""),
            "status": status,
            "note": note,
            "where": where,
            "evidence": ev,
        })

    suggestions = [str(s) for s in (ai.get("suggestions") or []) if str(s).strip()][:6]
    return {
        "ai": True,
        "ready": True,
        "summary": str(ai.get("summary", "")).strip() or "Responsiveness check below.",
        "rows": rows,
        "suggestions": suggestions,
    }
