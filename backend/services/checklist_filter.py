"""Which extracted requirements earn a checklist tick-box.

A real solicitation yields ~45 requirements, and MOST of them are content that
lives inside one section of the narrative — "Background grounded in the
literature", "General plan of work", "How success will be determined". Measured
on the human-verified NSF 23-598 list: 13 of 24 sit in `project_description`
alone. Turning those into tick-boxes produces an outline of the Project
Description rather than a submission checklist, and it duplicates Draft Review,
which already checks each of them against the PI's actual draft text.

The split this module makes:

    PRODUCE  a document, letter, plan or form the PI hands over  -> task
    OBEY     a page limit, a cap, a prohibition, a title prefix   -> task
    WRITE    subject matter inside a section                      -> Draft Review

A checkbox can only ask the PI to assert that they covered their research goals.
Draft Review can read the draft and tell them. Whereas nothing but a checklist
will remind them to attach the institutional support letter.

DELIBERATELY NOT keyed on `section`. Section names are model-assigned open
vocabulary that varies per solicitation (CLAUDE.md: they name a part of the
APPLICANT'S proposal, not the FOA's own structure), so a hardcoded list of
"content sections" would be brittle and would quietly re-introduce the
funder-specific branching that was deleted from this codebase on purpose. The
signals below are about the SHAPE of the ask and work on any funder's text.
"""
from __future__ import annotations

import re

# A measurable rule: a number with a unit, a sum of money, or the phrasing that
# introduces a bound. These are the asks a PI can get wrong mechanically and
# have the proposal returned without review.
_RULE_RE = re.compile(
    r"""(
        \b\d+\s*(?:page|pages|%|percent|year|years|month|months|word|words|character|characters)\b
      | \$\s?[\d,]+
      | \b(?:must\s+not\s+exceed|not\s+to\s+exceed|no\s+more\s+than|no\s+fewer\s+than
          |no\s+less\s+than|at\s+least|at\s+most|limited\s+to|maximum|minimum
          |or\s+fewer|or\s+less)\b
      | \b(?:prefix|font|margin|spacing|single[-\s]sentence|verbatim|exact\s+sentence)\b
      | \b(?:begin|start)\w*\s+with\b        # "begins with" AND "beginning with"
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# A prohibition is a rule stated negatively. "No letters of support from
# collaborators" gets a proposal returned; it is not narrative content.
_PROHIBITION_RE = re.compile(
    r"^\s*(?:no|do\s+not|does\s+not|may\s+not|must\s+not|never|avoid)\b",
    re.IGNORECASE,
)

# Something handed over as its own artifact.
_ARTIFACT_RE = re.compile(
    r"""\b(
        plan | letter | letters | statement | form | biosketch | biographical\s+sketch
      | c\.?v\.?  | r[eé]sum[eé] | appendix | appendices | attachment | attachments
      | documentation | certification | certificate | budget | justification
      | bibliography | data\s+management | mentoring | facilities | equipment
      | current\s+and\s+pending | support\s+letter | subaward | subawards
      | registration | abstract
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

# The verb that makes an artifact a deliverable rather than a topic.
_PRODUCE_RE = re.compile(
    r"""\b(
        submit | submitted | attach | attached | include | included | provide | provided
      | upload | uploaded | prepare | obtain | complete | sign | signed | register
      | required\s+as | must\s+accompany | shall\s+be\s+submitted | use[sd]?
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)


def is_checklist_task(requirement: dict) -> bool:
    """True when this requirement belongs on the checklist rather than only in
    Draft Review. Malformed rows are excluded rather than guessed at — a row we
    cannot read is not one we should be putting in front of a PI as the
    funder's instruction."""
    if not isinstance(requirement, dict):
        return False
    label = str(requirement.get("label") or "").strip()
    quote = str(requirement.get("source") or "").strip()
    if not label:
        return False

    # The label is what the PI reads; the quote is the funder's sentence. A rule
    # is often only measurable in the quote ("2 pages or fewer" may be paraphrased
    # away in the label), so both are searched.
    both = f"{label}\n{quote}"

    if _PROHIBITION_RE.search(label):
        return True
    if _RULE_RE.search(both):
        return True
    if _ARTIFACT_RE.search(label) and _PRODUCE_RE.search(both):
        return True
    return False


def split_requirements(requirements: list[dict]) -> tuple[list[dict], list[dict]]:
    """(checklist tasks, Draft-Review-only) — kept together so the two halves can
    never drift apart, and so a caller can report how many went each way."""
    rows = [r for r in requirements or [] if isinstance(r, dict)]
    tasks = [r for r in rows if is_checklist_task(r)]
    rest = [r for r in rows if r not in tasks]
    return tasks, rest
