# -*- coding: utf-8 -*-
"""
Feature Suggester — deterministic in-app feature callouts for the chat path.
============================================================================
The chatbot answers from the ORA knowledge base (the authoritative content).
On TOP of that answer, when the user's question maps to one of the app's own
tools, we surface a deterministic "we have a tool for this — use it" callout
card (rendered by the frontend under the answer, with a button to the tool).

Golden rule alignment: this is the *deterministic core*, not the LLM. The card
is computed from the user's QUESTION via keyword match — it does not depend on
what the model wrote, so the nudge is reliable and never fabricated. The model
just answers from the KB; the app owns the feature CTA.

`suggest_feature(query)` returns a render-ready dict or None:
    {
      "id":    "find_funding",
      "title": "Skip the manual search",
      "body":  "...one or two sentences...",
      "cta":   "Open Find Funding",
      "route": "/opportunities",
    }
Matching is on the QUESTION only, so an unrelated answer that merely mentions
"funding" never triggers a card.
"""

import re
from typing import Optional

# Ordered most-specific first. The first feature with a matching trigger wins,
# so a "sample proposal" question maps to Samples even though it contains
# "proposal" (which Proposals also keys on). Triggers are multi-word phrases
# (never a bare word like "funding") to avoid over-firing.
_FEATURES = [
    {
        "id": "samples",
        "route": "/sample-proposals",
        "title": "See what a funded proposal looks like",
        "body": "Browse our Sample Proposals library for annotated, real-style "
                "examples you can model your own writing on.",
        "cta": "Open Samples",
        "triggers": [
            "sample proposal", "example proposal", "example of a proposal",
            "examples of proposals", "see a proposal", "good proposal look",
            "successful proposal", "fundable proposal", "winning proposal",
            "what a proposal looks like", "model proposal",
        ],
    },
    {
        "id": "forms",
        "route": "/forms",
        "title": "Find the right ORA form",
        "body": "The Forms catalog has ORA's templates, checklists, and required "
                "forms in one place — searchable by name.",
        "cta": "Open Forms",
        "triggers": [
            "which form", "what form", "find the form", "find a form",
            "the form for", "form do i need", "form should i", "need a form",
            "template for", "download the form", "forms catalog", "right form",
        ],
    },
    {
        "id": "build_proposal",
        "route": "/my-proposals",
        "title": "Build it step by step",
        "body": "The Proposals workspace walks you through a guided pathway — "
                "solicitation intake, budget helper, drafting coach, compliance, "
                "and a pre-submission critique.",
        "cta": "Open Proposals",
        "triggers": [
            "write a proposal", "writing a proposal", "start a proposal",
            "starting a proposal", "build a proposal", "create a proposal",
            "prepare a proposal", "draft a proposal", "draft my proposal",
            "work on my proposal", "submit a proposal", "submitting a proposal",
            "proposal budget", "build my budget", "budget for my proposal",
            "track my proposal", "proposal checklist", "put together a proposal",
        ],
    },
    {
        "id": "find_funding",
        "route": "/opportunities",
        "title": "Skip the manual search",
        "body": "Use our Find Funding tool to describe your research in plain "
                "language and get matched to live, open federal grants — each with "
                "a fit explanation and an eligibility check.",
        "cta": "Open Find Funding",
        "triggers": [
            "find funding", "finding funding", "funding opportunit",
            "funding opportunities", "find grant", "find a grant",
            "find grants", "grant opportunit", "discover funding",
            "search for funding", "searching for funding", "looking for funding",
            "where to find funding", "how to find funding", "funding source",
            "funding for my", "find money for", "available grants",
            "open grants", "funding databases", "find sponsors",
            "sources of funding", "funding to apply",
        ],
    },
]


def _normalize(query: str) -> str:
    return " ".join((query or "").lower().split())


def suggest_feature(query: str) -> Optional[dict]:
    """Return the in-app feature callout that best matches the question, or None.

    Deterministic keyword match on the question text. Returns a copy without the
    internal `triggers` list so the caller can hand it straight to the client.
    """
    q = _normalize(query)
    if not q:
        return None
    for feature in _FEATURES:
        if any(t in q for t in feature["triggers"]):
            return {k: v for k, v in feature.items() if k != "triggers"}
    return None
