"""
Backend pre-processor for KB-browse / enumeration queries.

Why this lives in the backend (not the ADK agent):
    Gemini's tool API does NOT permit mixing VertexAiSearchTool with
    FunctionTool in the same agent ("Multiple tools are supported only when
    they are all search tools"). So we cannot expose list_kb_topics as an
    ADK FunctionTool while also keeping native Vertex AI Search grounding.

    Instead, the backend detects enumeration phrasing BEFORE calling the
    agent, looks up the answer in the bundled _manifest.json (mirrors the
    morgan.edu/ora nav), and returns a deterministic formatted response.

    The agent never sees these queries — so they're free of hallucination
    AND return in ~10ms (no LLM call, no Vertex roundtrip).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------
_MANIFEST_PATH = Path(__file__).parent / "kb_structured" / "_manifest.json"
_MANIFEST: Optional[dict] = None
_INDEX: dict[str, dict] = {}    # path -> node


def _index_tree(nodes: list[dict]) -> None:
    for n in nodes:
        _INDEX[n["path"]] = n
        if n.get("children"):
            _index_tree(n["children"])


def _load_manifest() -> dict:
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = json.loads(_MANIFEST_PATH.read_text())
        _index_tree(_MANIFEST.get("tree", []))
    return _MANIFEST


# ---------------------------------------------------------------------------
# Topic aliases — natural-language phrase -> manifest path
# Longest matches first; case-insensitive.
# ---------------------------------------------------------------------------
TOPIC_ALIASES: dict[str, str] = {
    # Pre-award (deepest first so "budget development" wins over "budget")
    "university application information":     "pre_award/university_application_information",
    "role of principal investigator":         "pre_award/role_of_principal_investigator",
    "principal investigator role":            "pre_award/role_of_principal_investigator",
    "proposal components":                    "pre_award/proposal_components",
    "proposal submission checklist":          "pre_award/proposal_submission_checklist",
    "submission checklist":                   "pre_award/proposal_submission_checklist",
    "internal routing form":                  "pre_award/internal_routing_form",
    "internal routing":                       "pre_award/internal_routing_form",
    "budget development":                     "pre_award/budget_development",
    "budget preparation":                     "pre_award/budget_development",
    "fringe benefit rate":                    "pre_award/fringe_benefit_rate",
    "fringe rate":                            "pre_award/fringe_benefit_rate",
    "fanda cost rates":                       "pre_award/fanda_cost_rates",
    "f&a cost rates":                         "pre_award/fanda_cost_rates",
    "f&a rate":                               "pre_award/fanda_cost_rates",
    "f&a rates":                              "pre_award/fanda_cost_rates",
    "fa rates":                               "pre_award/fanda_cost_rates",
    "indirect cost":                          "pre_award/fanda_cost_rates",
    "indirect rate":                          "pre_award/fanda_cost_rates",
    "proposal and budget examples":           "pre_award/proposal_and_budget_examples",
    "proposal examples":                      "pre_award/proposal_and_budget_examples",
    "budget examples":                        "pre_award/proposal_and_budget_examples",
    "pre-award spending":                     "pre_award/pre_award_spending",
    "pre award spending":                     "pre_award/pre_award_spending",
    "advance account":                        "pre_award/pre_award_spending",
    "pre-award subawards":                    "pre_award/pre_award_subawards",
    "pre-award subaward":                     "pre_award/pre_award_subawards",
    "limited submission":                     "pre_award/limited_submission",
    "pre-award":                              "pre_award",
    "pre award":                              "pre_award",
    "preaward":                               "pre_award",

    # Post-award
    "notification and setup":                 "post_award/notification_and_setup_of_award",
    "notification of award":                  "post_award/notification_and_setup_of_award",
    "award setup":                            "post_award/notification_and_setup_of_award",
    "setup of award":                         "post_award/notification_and_setup_of_award",
    "changes to an award":                    "post_award/changes_to_an_award",
    "changes to award":                       "post_award/changes_to_an_award",
    "award changes":                          "post_award/changes_to_an_award",
    "no-cost extension":                      "post_award/changes_to_an_award",
    "no cost extension":                      "post_award/changes_to_an_award",
    "nce":                                    "post_award/changes_to_an_award",
    "change of pi":                           "post_award/changes_to_an_award",
    "post-award subawards":                   "post_award/post_award_subawards",
    "post award subawards":                   "post_award/post_award_subawards",
    "post-award subaward":                    "post_award/post_award_subawards",
    "reporting":                              "post_award/reporting",
    "effort reporting":                       "post_award/reporting",
    "final report":                           "post_award/reporting",
    "post-award forms":                       "post_award/forms",
    "post award forms":                       "post_award/forms",
    "docusign forms":                         "post_award/forms",
    "docusign":                               "post_award/forms",
    "post-award":                             "post_award",
    "post award":                             "post_award",
    "postaward":                              "post_award",

    # Research compliance (deepest first)
    "animal housing capacity":                "research_compliance/animal_research/animal_housing_capacity",
    "available equipment":                    "research_compliance/animal_research/available_equipment",
    "iacuc sops":                             "research_compliance/animal_research/iacuc_sops",
    "iacuc sop":                              "research_compliance/animal_research/iacuc_sops",
    "iacuc standard operating procedures":    "research_compliance/animal_research/iacuc_sops",
    "iacuc forms":                            "research_compliance/animal_research/iacuc_forms",
    "iacuc form":                             "research_compliance/animal_research/iacuc_forms",
    "training and consultation":              "research_compliance/animal_research/training_and_consultation",
    "animal research":                        "research_compliance/animal_research",
    "iacuc":                                  "research_compliance/animal_research",
    "human subjects research":                "research_compliance/human_subjects_research",
    "human subjects":                         "research_compliance/human_subjects_research",
    "irb":                                    "research_compliance/human_subjects_research",
    "information technology resources":       "research_compliance/research_security/information_technology_resources",
    "research compliance and security training": "research_compliance/research_security/research_compliance_and_security_training",
    "research security training":             "research_compliance/research_security/research_compliance_and_security_training",
    "technology control plan":                "research_compliance/research_security/technology_control_plan_tcp",
    "tcp":                                    "research_compliance/research_security/technology_control_plan_tcp",
    "research security program committee":    "research_compliance/research_security/research_security_program_committee",
    "nspm-33":                                "research_compliance/research_security/nspm_33_overview",
    "nspm 33":                                "research_compliance/research_security/nspm_33_overview",
    "research security":                      "research_compliance/research_security",
    "conflict of interest for sponsored research": "research_compliance/conflict_of_interest/conflict_of_interest_for_sponsored_research",
    "conflict of interest":                   "research_compliance/conflict_of_interest",
    "coi":                                    "research_compliance/conflict_of_interest",
    "fcoi":                                   "research_compliance/conflict_of_interest",
    "responsible conduct of research":        "research_compliance/responsible_conduct_of_research",
    "rcr":                                    "research_compliance/responsible_conduct_of_research",
    "research misconduct":                    "research_compliance/research_misconduct",
    "state of maryland ethics":               "research_compliance/state_of_maryland_ethics_and_financial_disclosure",
    "maryland ethics":                        "research_compliance/state_of_maryland_ethics_and_financial_disclosure",
    "ethics and financial disclosure":        "research_compliance/state_of_maryland_ethics_and_financial_disclosure",
    "diversity and eeo":                      "research_compliance/diversity_and_eeo",
    "eeo":                                    "research_compliance/diversity_and_eeo",
    "drug, alcohol and tobacco":              "research_compliance/drug_alcohol_and_tobacco_policies",
    "drug alcohol tobacco":                   "research_compliance/drug_alcohol_and_tobacco_policies",
    "research compliance updates":            "research_compliance/research_compliance_updates_and_news",
    "compliance updates":                     "research_compliance/research_compliance_updates_and_news",
    "research compliance":                    "research_compliance",
    "compliance":                             "research_compliance",

    # Trainings
    "e-training":                             "trainings/e_training",
    "e training":                             "trainings/e_training",
    "etraining":                              "trainings/e_training",
    "new faculty development seminars":       "trainings/new_faculty_development_seminars",
    "new faculty development":                "trainings/new_faculty_development_seminars",
    "new faculty seminars":                   "trainings/new_faculty_development_seminars",
    "faculty development":                    "trainings/new_faculty_development_seminars",
    "faculty seminars":                       "trainings/new_faculty_development_seminars",
    "monthly d-red seminars":                 "trainings/monthly_d_red_seminars",
    "monthly d-red":                          "trainings/monthly_d_red_seminars",
    "d-red seminars":                         "trainings/monthly_d_red_seminars",
    "d-red":                                  "trainings/monthly_d_red_seminars",
    "dred":                                   "trainings/monthly_d_red_seminars",
    "d red":                                  "trainings/monthly_d_red_seminars",
    "special workshops":                      "trainings/special_workshops",
    "workshops":                              "trainings/special_workshops",
    "test prep":                              "trainings/test_prep",
    "racc":                                   "trainings/test_prep",
    "racc test prep":                         "trainings/test_prep",
    "msu trainings outside ora":              "trainings/msu_trainings_outside_ora",
    "trainings outside ora":                  "trainings/msu_trainings_outside_ora",
    "trainings":                              "trainings",
    "training":                               "trainings",

    # Resources / handbooks / templates
    "principal investigator handbooks":       "resources/principal_investigator_handbooks",
    "pi handbooks":                           "resources/principal_investigator_handbooks",
    "pi handbook":                            "resources/principal_investigator_handbooks",
    "handbooks":                              "resources/principal_investigator_handbooks",
    "templates":                              "resources/templates",
    "letter templates":                       "resources/templates",
    "resources":                              "resources",

    # Policies
    "numbered policies":                      "policies_and_guidelines/numbered_policies",
    "ora policies":                           "policies_and_guidelines/numbered_policies",
    "policies and guidelines":                "policies_and_guidelines",
    "policies":                               "policies_and_guidelines",

    # Funding sources
    "external funding databases":             "funding_sources/external_databases",
    "funding databases":                      "funding_sources/external_databases",
    "private foundations":                    "funding_sources/private_foundations",
    "state of maryland funding":              "funding_sources/state_of_maryland",
    "federal funding":                        "funding_sources/federal",
    "federal grants":                         "funding_sources/federal",
    "funding sources":                        "funding_sources",
    "funding opportunities":                  "funding_sources",
    "funding":                                "funding_sources",
    "opportunities":                          "funding_sources",

    # About
    "staff directory":                        "about/staff_directory",
    "staff list":                             "about/staff_directory",
    "ora staff":                              "about/staff_directory",
    "staff":                                  "about/staff_directory",
    "mission and vision":                     "about/mission_and_vision",
    "mission & vision":                       "about/mission_and_vision",
    "history":                                "about/history",
    "ora history":                            "about/history",
    "about ora":                              "about",
    "about":                                  "about",

    # Announcements
    "ora announcements":                      "ora_announcements",
    "announcements":                          "ora_announcements",
}


# ---------------------------------------------------------------------------
# Detection: is this an enumeration query?
#
# Two tiers:
#   STRONG - unambiguous "browse the KB" intent. Always eligible for the
#            deterministic path, even mid-conversation.
#   WEAK   - phrasing that reads as enumeration in isolation but is usually a
#            substantive follow-up when prior conversation exists
#            (e.g. "can you give me what forms do I need to fill?").
#
# When a request has conversation history and ONLY weak triggers fired,
# try_browse() defers to the LLM agent so it can answer the follow-up in
# context. Caller still requires a topic-alias match before returning a list
# response.
# ---------------------------------------------------------------------------
_STRONG_TRIGGERS = re.compile(
    # \blist\b but NOT the "list" inside "list-serv" -- asking how to subscribe
    # to the ORA Announcements list-serv is a content question, not a request to
    # list documents.
    r"\blist\b(?!-serv)"
    r"|\benumerate\b"
    r"|\bbrowse\b"
    r"|\bshow me\b"
    r"|\bdo you have\b"
    r"|\bwhat do you have\b"
    r"|\bwhat'?s in\b"
    r"|\bwhat is in\b",
    re.IGNORECASE,
)

# NOTE: the noun lists below are deliberately DOCUMENT-CLASS words only
# (forms, templates, policies, sops, ...). Content-describing words --
# topics / types / kinds / categories -- were intentionally REMOVED: a question
# like "what topics does SOP 41.2 cover?" or "what types of items are reviewed?"
# is asking for the CONTENT of one doc, not a directory listing. Treating those
# as enumeration made the bot dump a list of links instead of answering (~50
# coverage failures, concentrated in trainings + IACUC SOPs).
_WEAK_TRIGGERS = re.compile(
    r"\bgive me\b"
    r"|\btell me about\b"
    r"|\bhow many\b"
    r"|\bwhat (?:docs|documents|files|materials|forms|templates|"
              r"policies|guidelines|sops|seminars|workshops|trainings|"
              r"resources|opportunities|sources)\b"
    r"|\bwhat \S+ (?:docs|documents|forms|templates|policies|materials|"
                    r"guidelines|sops|seminars|workshops|trainings|resources)\b",
    re.IGNORECASE,
)


# A FILTER cue means the user wants a SUBSET that meets a condition ("templates
# that SUPPORT AI", "forms RELATED TO animal research"), not the whole section.
# The directory dump can't honor a filter -- it would list everything and ignore
# the condition -- so these defer to the agent, which can actually search. Kept
# to clear filter phrases so plain "list/what's in X" enumeration is untouched.
_FILTER_CUE_RE = re.compile(
    r"\b(?:support|supports|supporting|related to|relating to|specific to|"
    r"geared (?:to|toward)|used (?:for|to)|that (?:support|help|cover|address|use|apply|deal)|"
    r"help(?:s|ful)? (?:with|for)|to (?:support|help|conduct|enable))\b",
    re.IGNORECASE,
)


def _detect_enumeration(query: str) -> tuple[bool, bool]:
    """Return (matched_any, matched_strong).

    matched_any    - query contains at least one enumeration trigger.
    matched_strong - query contains at least one STRONG (unambiguous) trigger.
    """
    strong = bool(_STRONG_TRIGGERS.search(query))
    weak = bool(_WEAK_TRIGGERS.search(query))
    return (strong or weak, strong)


def _match_topic(query: str) -> Optional[str]:
    """Find the deepest TOPIC_ALIASES match in the query."""
    q = query.lower()
    # Sort aliases by length descending so longer/more specific phrases win
    matches: list[tuple[int, str]] = []
    for phrase, path in TOPIC_ALIASES.items():
        if phrase in q:
            matches.append((len(phrase), path))
    if not matches:
        return None
    matches.sort(key=lambda m: -m[0])
    return matches[0][1]


# ---------------------------------------------------------------------------
# Response formatting
# ---------------------------------------------------------------------------
_MAX_DOCS_INLINE = 25   # cap docs listed inline before truncating


def _format_node(node: dict) -> str:
    """Render a manifest node as a markdown response."""
    title = node.get("title", node.get("path", "ORA KB"))
    total = node.get("doc_count", 0)
    direct = node.get("direct_doc_count", 0)
    children = node.get("children", [])
    docs = node.get("docs", [])

    lines = [f"**{title}** — {total} doc{'s' if total != 1 else ''} total"]
    lines.append("")

    if children:
        lines.append(f"### Sub-pages ({len(children)})")
        for c in children:
            lines.append(f"- **{c['title']}** ({c['doc_count']} doc{'s' if c['doc_count'] != 1 else ''})")
        lines.append("")

    if docs:
        shown = docs[:_MAX_DOCS_INLINE]
        if children:
            lines.append(f"### Docs at this page ({direct})")
        else:
            lines.append(f"### Docs ({direct})")
        for d in shown:
            title_str = d.get("title", d.get("doc_id", ""))
            src = d.get("source_url", "")
            if src:
                lines.append(f"- [{title_str}]({src})")
            else:
                lines.append(f"- {title_str}")
        if len(docs) > _MAX_DOCS_INLINE:
            lines.append(f"- *…and {len(docs) - _MAX_DOCS_INLINE} more*")
        lines.append("")

    if children:
        lines.append("*Ask me about any of the sub-pages above to see what's inside.*")
    elif docs:
        lines.append("*Click any link above to view the full doc on morgan.edu, "
                     "or ask me for details about a specific item.*")

    return "\n".join(lines).strip()


def _format_root() -> str:
    m = _load_manifest()
    tree = m.get("tree", [])
    total = m.get("total_docs", 0)
    lines = [f"**ORA Knowledge Base** — {total} docs across {len(tree)} sections (mirrors morgan.edu/ora nav)",
             ""]
    for n in tree:
        kids = len(n.get("children", []))
        sub_str = f", {kids} sub-pages" if kids else ""
        lines.append(f"- **{n['title']}** — {n['doc_count']} doc{'s' if n['doc_count']!=1 else ''}{sub_str}")
    lines.append("")
    lines.append("*Ask me about any section to drill in — e.g. \"What's in pre-award?\" or \"List IACUC SOPs\".*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def try_browse(query: str, has_history: bool = False) -> Optional[str]:
    """If `query` looks like a KB-browse / enumeration question, return a
    formatted markdown response. Otherwise return None (caller should
    continue to the normal agent flow).

    Args:
        query:       the user's (possibly rewritten) message.
        has_history: True when the request belongs to a conversation that
                     already has prior turns. When set, ambiguous ("weak")
                     enumeration phrasing is deferred to the LLM agent so it
                     can answer the follow-up in context, and a strong-but-
                     topicless query returns None instead of dumping the full
                     KB tree.

    ~5-10ms when triggered; never calls Gemini or Vertex AI Search.
    """
    if not query or len(query) > 500:
        return None

    matched_any, matched_strong = _detect_enumeration(query)
    if not matched_any:
        return None

    # Filtered/content question ("what templates SUPPORT AI") -> the directory
    # dump can't honor the filter, so defer to the agent which can search and,
    # e.g., correctly say no AI-specific template exists. Applies even to STRONG
    # triggers ("list templates that support AI" still wants a filtered answer).
    if _FILTER_CUE_RE.search(query):
        return None

    # Mid-conversation + only weak/ambiguous triggers -> let the agent answer
    # the follow-up in context ("can you give me what forms do I need?").
    if has_history and not matched_strong:
        return None

    _load_manifest()
    path = _match_topic(query)

    if path is None:
        # Enumeration phrasing but no topic match. Only an explicit STRONG
        # request ("list", "show me", "what's in the KB") on a fresh turn
        # justifies dumping the whole 9-section index. WEAK-only phrasing
        # ("what forms do I need to add a co-investigator?") reads as enumeration
        # in isolation but is almost always a real content question -> defer to
        # the agent so it can actually answer instead of dumping the directory.
        return _format_root() if (matched_strong and not has_history) else None

    node = _INDEX.get(path)
    if not node:
        return _format_root() if (matched_strong and not has_history) else None

    return _format_node(node)


def browse_citations(query: str, has_history: bool = False) -> list:
    """Return the {title, url} Sources for a browse answer that `try_browse`
    would produce for the same query, so the enumeration path can show a
    Sources block like the grounded agent does.

    Mirrors try_browse's gating exactly so the returned list always matches
    what was rendered. Returns [] when try_browse would defer to the agent or
    show the section-overview root index (which lists sections, not docs).
    Capped at 5 to match the Sources UI.
    """
    if not query or len(query) > 500:
        return []

    matched_any, matched_strong = _detect_enumeration(query)
    if not matched_any:
        return []
    if _FILTER_CUE_RE.search(query):
        return []
    if has_history and not matched_strong:
        return []

    _load_manifest()
    path = _match_topic(query)
    if path is None:
        return []
    node = _INDEX.get(path)
    if not node:
        return []

    out: list = []
    for d in node.get("docs", []):
        title = d.get("title") or d.get("doc_id", "")
        url = d.get("source_url", "")
        if title and url:
            out.append({"title": title, "url": url})
        if len(out) >= 5:
            break
    return out
