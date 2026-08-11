"""The shape services/draft_review.py reviews against.

WHY THIS EXISTS
---------------
The reviewer used to import one hardcoded solicitation module directly, which is
why it only worked for that one funder. A PROFILE is that same information
as data, so the engine can be handed any solicitation — the one it was written
for, or anything a PI attaches — and behave identically.

A profile is deliberately a plain dict (no dataclass): it round-trips through
json.dumps into Submission.solicitation_json, and `checks` — the only
non-serializable member — is re-attached on load from code, never persisted.

DATA ONLY: no LLM, no network, no DB (mirrors forms_catalog.py).
"""

from __future__ import annotations

import re
from typing import Optional

_NUMBERING_RE = re.compile(r"^\s*(?:[\dIVXivx]+[.)]\s*)+")


def aliases_for(label: str) -> list[str]:
    """Headings a PI plausibly types for a section called `label`.

    The locate stage matches these against whole heading LINES, so they are
    lowercased variants, never substrings of prose."""
    label = " ".join((label or "").split())
    if not label:
        return []
    out = [label.lower()]
    bare = _NUMBERING_RE.sub("", label).strip().lower()
    if bare and bare not in out:
        out.append(bare)
    # "Letters of Collaboration" -> also match the singular a PI may write.
    if bare.endswith("s") and bare[:-1] not in out:
        out.append(bare[:-1])
    return out


def section_key(name: str) -> str:
    """The canonical key for a section name. PUBLIC because more than one module
    has to arrive at the same key independently: sections_from() files a section
    under it, and generic_checks looks a located span up by it. Two definitions
    would drift and the lookup would silently miss."""
    key = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return key or "other"


def _section_label(key: str) -> str:
    return " ".join(w.capitalize() for w in key.split("_"))


def heading_regex(alias: str) -> re.Pattern:
    """A heading LINE for `alias`: optional numbering/bullets, the alias, then
    optional punctuation — and nothing else on the line.

    Anchored per-line so a passing mention inside a paragraph never counts as a
    heading. PUBLIC and shared: the locate stage uses it to segment a draft, and
    generic_checks uses it to tell a real attachment section from a sentence
    that merely names one ("as described in our Data Management Plan")."""
    return re.compile(
        r"^[ \t]*(?:[\dIVXivx]+[.)]\s*)*(?:[-–—*•]\s*)?" + re.escape(alias) + r"\s*:?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )


def sections_from(requirements: list[dict], page_limits: Optional[dict] = None,
                  attachments: Optional[list] = None) -> dict:
    """Assemble the section universe the locate stage segments the draft into.

    Three sources, all already produced by extraction: the `section` each
    requirement row names, the sections the solicitation gives a page limit for,
    and the attachments it requires by name. A requirement whose section is not
    otherwise known still gets a section here, so it can never be dropped."""
    sections: dict = {}

    def add(raw_key: str, label: Optional[str] = None) -> None:
        key = section_key(raw_key)
        if not key or key in sections:
            return
        lbl = label or _section_label(key)
        sections[key] = {"label": lbl, "aliases": aliases_for(lbl)}

    for req in requirements or []:
        if req.get("section"):
            add(str(req["section"]))
    for name in (page_limits or {}):
        add(str(name))
    for name in (attachments or []):
        add(str(name), label=str(name))
    return sections


def make_profile(*, id: str, title: str, url: Optional[str] = None,
                 sections: dict, requirements: list[dict],
                 checks: Optional[dict] = None,
                 merit_criteria: Optional[list] = None,
                 eligibility_notes: Optional[list] = None) -> dict:
    return {
        "id": id,
        "title": title,
        "url": url,
        "sections": sections or {},
        "requirements": list(requirements or []),
        "checks": checks or {},
        "merit_criteria": merit_criteria or [],
        "eligibility_notes": eligibility_notes or [],
    }


def requirements_for(profile: dict, section: Optional[str]) -> list[dict]:
    """Rows belonging to `section`; None -> the whole-document rows."""
    return [r for r in profile.get("requirements", []) if r.get("section") == section]


def scored_requirements(profile: dict) -> list[dict]:
    return [r for r in profile.get("requirements", []) if r.get("scored")]


def build_generic(contract: dict, requirements: list[dict], *, id: str, title: str,
                  url: Optional[str] = None,
                  merit_criteria: Optional[list] = None,
                  eligibility_notes: Optional[list] = None) -> dict:
    """A profile for an arbitrary solicitation.

    `requirements` are the SEMANTIC rows read out of the document; the
    deterministic rows are derived here from the contract's hard numbers rather
    than stored, so they always track the contract the PI actually confirmed —
    edit the page limit and the check that enforces it moves with it.

    Sections are likewise derived, never persisted: a stored section list could
    drift out of step with the requirements that name it."""
    from services import generic_checks
    contract = contract or {}
    rows = list(requirements or []) + generic_checks.contract_requirements(contract)
    return make_profile(
        id=id, title=title, url=url,
        sections=sections_from(rows,
                               page_limits=contract.get("page_limits"),
                               attachments=contract.get("required_attachments")),
        requirements=rows,
        checks=generic_checks.CHECKS,
        merit_criteria=merit_criteria,
        eligibility_notes=eligibility_notes,
    )
