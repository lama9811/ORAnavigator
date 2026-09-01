"""Which extracted requirements a DRAFT can actually satisfy.

THE PROBLEM, MEASURED ON A REAL PROPOSAL
----------------------------------------
Of 29 requirements Draft Review assessed, SEVEN were things no document can
contain — "Select secondary unit of consideration in Research.gov", "Ensure the
Letter of Intent is submitted by an Authorized Organizational Representative",
"Limit career total awards per PI". Each came back `not_found`, each counted
against the draft, and each sat in "Fix these first". The score read 22% partly
because of them, and a PI cannot fix a portal click by editing their Project
Description.

They are still real requirements. `checklist_filter` already routes them to the
checklist, which is the tool that can help: a tick-box is exactly right for
"submit via Research.gov", and useless for "describe your research goals". This
module only stops the DRAFT reviewer from grading a document on them, and says
where they are handled instead.

Deterministic and model-free (golden rule 1), applied at review time like
`delegated_rules` so improving it retroactively fixes every stored profile.

THE RISK IS OVER-EXCLUDING, NOT UNDER-EXCLUDING
-----------------------------------------------
A genuine content requirement dropped here is worse than the noise it removes:
the PI would never learn it was missing, and silence is the failure mode this
whole tool exists to prevent. So exclusion demands an EXPLICIT signal — a named
submission system, a named submitting officer, a registration, or a limit on a
PERSON's participation — and anything phrased as what the proposal must SAY,
CONTAIN or DESCRIBE is kept even when a portal is mentioned in the same
sentence. When in doubt the row stays scored.
"""

from __future__ import annotations

import re
from typing import Optional

# Named submission systems. A rule about operating one of these is a rule about
# a website, not about a document.
_SYSTEM = re.compile(
    r"\b(?:research\.gov|grants\.gov|fastlane|sam\.gov|era\s+commons|ncbi|assist"
    r"|submission\s+system|proposal\s+submission\s+portal)\b", re.I)

# Who performs the submission. The PI cannot write their way into being an AOR.
_SUBMITTER = re.compile(
    r"\b(?:authorized\s+organizational\s+representative|\bAOR\b"
    r"|sponsored\s+(?:research|projects)\s+office"
    r"|signing\s+official|institutional\s+official|business\s+official)\b", re.I)

_REGISTRATION = re.compile(
    r"\b(?:register|registration|registered)\b.{0,40}"
    r"\b(?:sam\.gov|grants\.gov|research\.gov|era\s+commons|system|portal|prior\s+to)\b|"
    r"\b(?:sam\.gov|grants\.gov|era\s+commons)\b.{0,40}\b(?:register|registration)\b", re.I)

# A cap on how many proposals or awards a PERSON may have. It constrains who may
# apply, not what the document says — the review already has an eligibility
# panel for exactly this class ("No draft text can prove these").
_PERSON_LIMIT = re.compile(
    r"\b(?:may\s+(?:participate|submit|serve|receive)|limited\s+to|no\s+more\s+than"
    r"|maximum\s+of|not\s+exceed)\b.{0,80}"
    r"\b(?:per\s+(?:deadline|competition|cycle|round|year)|career|lifetime"
    r"|as\s+(?:a\s+)?PI(?:\s+or\s+co-?PI)?|per\s+PI)\b", re.I)

# Form plumbing: choosing a routing option, ticking a box on a cover sheet.
_FORM_FIELD = re.compile(
    r"\b(?:cover\s+sheet|check\s+the\s+box|tick\s+the\s+box|drop-?down|menu\s+option"
    r"|unit\s+of\s+consideration)\b", re.I)

# THE OVERRIDE. Anything phrased as what the document must say is content, even
# when a system is named in the same sentence ("the budget justification
# uploaded to Research.gov must explain each cost"). Checked FIRST.
_CONTENT = re.compile(
    r"\b(?:describe|description|discuss|explain|state|address|articulate|summari[sz]e"
    r"|must\s+(?:include|contain)|should\s+(?:include|contain)|include\s+a\b"
    r"|labeled|labelled|section|narrative|plan\b|justif|rationale"
    r"|page|pages|word|words|character|font|margin|prohibit|may\s+not\s+exceed"
    r"|no\s+more\s+than\s+\d+\s*%|letter\b)\b", re.I)


# A POST-AWARD REPORT IS DUE AFTER AN AWARD THAT DOES NOT EXIST YET. No draft can
# contain one and no PI can act on it while writing, so scoring it against a
# package is scoring a proposal for not being an award. Measured on the AWARDED
# NSF EiR package: "Submit annual project report prior to budget period end" and
# "Submit final project report and outcomes report following expiration" both
# came back `not_found` on a funded proposal.
#
# NARROW ON PURPOSE, because over-excluding is this module's dangerous direction:
# a REPORT NOUN qualified by annual/final/interim/outcomes, or a submit-a-report
# ask tied to the award period. "Describe the plan for reporting results in the
# Project Description" matches neither and stays scored (its own test).
_REPORT_NOUN = re.compile(
    r"\b(annual|final|interim|outcomes?)\s+(project\s+)?(report|reports)\b", re.I)
_AWARD_PERIOD = re.compile(
    r"\bbudget period\b|\bexpiration of the award\b|\bduring the award\b"
    r"|\bafter the award\b|\baward period\b", re.I)
_SUBMIT_REPORT = re.compile(r"\bsubmit\b[^.]{0,80}\breport\b", re.I)


def _is_post_award(blob: str) -> bool:
    return bool(_REPORT_NOUN.search(blob)
                or (_SUBMIT_REPORT.search(blob) and _AWARD_PERIOD.search(blob)))


# SECTIONS THAT ARE A SEPARATE SUBMISSION, not a part of the package. A Letter of
# Intent has its own, earlier deadline; by the time a package exists it is months
# gone, so counting its rules means EVERY proposal to such a solicitation loses
# those points permanently. Measured on the awarded package: 3 of the 14 scored
# rules that lost points were LOI rules.
#
# PACKAGE-SCOPED, NOT RULE-SCOPED, and that distinction is load-bearing:
# `apply_draft_scope` runs in review_section too, so excluding these outright
# would leave a PI who opens Check a Section on their Letter of Intent with every
# rule reading "Done at submission". The letter is perfectly checkable on its own.
#
# Keyed on the SECTION, never on the sponsor -- a letter of intent is a
# proposal-process concept, not one funder's vocabulary.
_SEPARATE_SUBMISSIONS = {"letter_intent"}


def apply_package_scope(findings: list[dict], *,
                        section: Optional[str] = None) -> list[dict]:
    """Stop scoring a PACKAGE on parts that are submitted separately.

    `section` is the one section being checked, when this is a Section Check --
    that section's own rules are then left alone, because checking the letter
    itself is exactly what that screen is for.
    """
    out = []
    for f in findings:
        sec = f.get("section")
        if (sec in _SEPARATE_SUBMISSIONS and sec != section
                and f.get("status") not in ("not_in_draft",)):
            f = dict(f)
            f["status"] = "not_in_draft"
            f["note"] = ("A Letter of Intent is a separate submission with its own, "
                         "earlier deadline, so it is not part of this package. Check "
                         "it on its own from Check a Section.")
        out.append(f)
    return out


def is_draft_checkable(label: str, source: str) -> bool:
    """Can a proposal DOCUMENT satisfy this requirement?

    True keeps the row in the review and in the score. False routes it to the
    checklist instead. Defaults to True: an unrecognised requirement is assessed,
    never silently dropped.
    """
    blob = f"{label or ''} {source or ''}"
    if not blob.strip():
        return True

    # A post-award report is never about the document, whatever else the sentence
    # says, so it short-circuits before the content-wording rule below.
    if _is_post_award(blob):
        return False

    out_of_scope = (_SUBMITTER.search(blob) or _REGISTRATION.search(blob)
                    or _PERSON_LIMIT.search(blob) or _FORM_FIELD.search(blob)
                    or _SYSTEM.search(blob))
    if not out_of_scope:
        return True

    # A system may be named while the ask is still about the document. Content
    # wording wins — except for the two signals that are never about content:
    # who submits, and how many proposals a person may have.
    if _SUBMITTER.search(blob) or _PERSON_LIMIT.search(blob):
        return False
    return bool(_CONTENT.search(blob))


NOTE = ("This is done at submission — in the funder's system, or by your research "
        "office — so a draft cannot show it. It is tracked on your proposal's "
        "checklist instead.")
