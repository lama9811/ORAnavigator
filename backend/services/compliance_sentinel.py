"""Compliance Sentinel — deterministic "which approvals do I need?" engine (2026-06-10).

The trustworthy core of the Compliance Sentinel. WHICH approvals a project needs
is decided entirely by code rules here — never by an LLM — so a PI can rely on
the checklist for a real federal submission. (v1 has no AI layer at all; the
plain-English explanations are deterministic templates.)

Two trigger sources feed the rules:
  1. A short yes/no questionnaire the PI fills in (human subjects, animals,
     financial interest, foreign collaboration, export-controlled tech).
  2. The proposal's `sponsor` — federal sponsors carry their own mandates
     (NSF/NIH → RCR training; PHS/NIH → COI disclosure for ALL investigators).

Each rule resolves to a real Morgan form/page via the KB document index
(`forms_catalog.resolve_kb_doc`) so no "Open form" link is ever fabricated.
Keep the doc_ids in sync if the KB is re-indexed (the unit tests assert each
required item resolves a live URL, so a rename fails the build).
"""

from __future__ import annotations

from typing import Optional

from services import forms_catalog

# ── trigger helpers ────────────────────────────────────────────────────────
_YES = {"yes", "y", "true", "1", "on"}


def _is_yes(v) -> bool:
    """True iff the answer reads as an affirmative. Anything else (no, blank,
    junk, None) is False — we never *guess* a requirement into existence."""
    if v is True:
        return True
    if v is None:
        return False
    return str(v).strip().lower() in _YES


def _is_answered(v) -> bool:
    return v not in (None, "") and str(v).strip() != ""


# Federal sponsors that mandate RCR training (NSF/NIH). Matched case-insensitively
# against the Submission.sponsor string.
_RCR_SPONSORS = ("nsf", "nih")
# Sponsors under the PHS umbrella whose FCOI rule applies to ALL investigators
# regardless of whether they disclosed a personal financial interest.
_PHS_SPONSORS = ("nih", "phs")


def _sponsor_matches(sponsor: Optional[str], needles: tuple[str, ...]) -> bool:
    s = (sponsor or "").strip().lower()
    return any(n in s for n in needles)


# ── rule table ─────────────────────────────────────────────────────────────
# Each rule is evaluated by its own small function (answers, sponsor) -> status,
# returning one of "required" | "not_required" | "review", plus which doc_id to
# link (some rules link a different doc depending on the outcome).

def _rule_irb(answers, sponsor):
    v = answers.get("human_subjects")
    if _is_yes(v):
        return "required", "form_irb_approval_request"
    if _is_answered(v):
        return "not_required", "form_irb_approval_request"
    return "review", "form_irb_approval_request"


def _rule_iacuc(answers, sponsor):
    v = answers.get("animals")
    if _is_yes(v):
        return "required", "compliance_iacuc_forms"
    if _is_answered(v):
        return "not_required", "compliance_iacuc_forms"
    return "review", "compliance_iacuc_forms"


def _rule_coi(answers, sponsor):
    doc = "form_coi_fcoi_sponsored_disclosure"
    if _sponsor_matches(sponsor, _PHS_SPONSORS) or _is_yes(answers.get("financial_interest")):
        return "required", doc
    if _is_answered(answers.get("financial_interest")):
        return "not_required", doc
    # No federal mandate and the question is unanswered -> nudge to review.
    return "review", doc


def _rule_rcr(answers, sponsor):
    doc = "form_citi_training_program"
    if _sponsor_matches(sponsor, _RCR_SPONSORS):
        return "required", doc
    return "not_required", doc


def _rule_export_security(answers, sponsor):
    if _is_yes(answers.get("export_controlled")):
        return "required", "compliance_research_security_technology_control_plan"
    if _is_yes(answers.get("foreign_collaboration")):
        return "review", "compliance_research_security_nspm_33"
    if _is_answered(answers.get("export_controlled")) or _is_answered(answers.get("foreign_collaboration")):
        return "not_required", "compliance_research_security_technology_control_plan"
    return "review", "compliance_research_security_nspm_33"


# id, human title, evaluator, plain-English why-templates (by status), timing,
# and the task title used when "Add to my proposal" is clicked.
RULES = [
    {
        "id": "irb",
        "title": "IRB — Human Subjects Protection",
        "eval": _rule_irb,
        "why": {
            "required": "Your project involves human subjects (surveys, interviews, human data or specimens), so IRB approval is required before any research activity begins.",
            "not_required": "You indicated the project does not involve human subjects, so IRB review is not required.",
            "review": "Confirm whether your project involves human subjects — if it does, IRB approval is required before you begin.",
        },
        "timing": "IRB review commonly takes 3–6 weeks (longer for full-board studies) — submit early.",
        "task_title": "Submit IRB protocol (Human Subjects Research Approval Request)",
    },
    {
        "id": "iacuc",
        "title": "IACUC — Animal Research",
        "eval": _rule_iacuc,
        "why": {
            "required": "Your project involves live vertebrate animals, so an approved IACUC protocol is required before any animal work.",
            "not_required": "You indicated the project does not involve animals, so IACUC review is not required.",
            "review": "Confirm whether your project involves live animals — if it does, an IACUC protocol is required.",
        },
        "timing": "The IACUC reviews on a monthly cycle; allow 4–8 weeks for protocol approval.",
        "task_title": "Submit IACUC animal-use protocol",
    },
    {
        "id": "coi",
        "title": "COI — Conflict of Interest Disclosure",
        "eval": _rule_coi,
        "why": {
            "required": "A Financial Conflict of Interest (FCOI) disclosure is required — either because this is PHS/NIH-funded (which requires disclosure from all investigators) or because you reported a related financial interest.",
            "not_required": "No federal FCOI mandate applies and you reported no related financial interest, so a COI disclosure is not required for this project.",
            "review": "Confirm whether you (or any team member) have a financial interest related to this research; PHS/NIH funding requires a disclosure regardless.",
        },
        "timing": "FCOI disclosures must be on file before award; PHS/NIH also require COI training and annual updates.",
        "task_title": "File Financial Conflict of Interest (FCOI) disclosure",
    },
    {
        "id": "rcr",
        "title": "RCR — Responsible Conduct of Research Training",
        "eval": _rule_rcr,
        "why": {
            "required": "This sponsor (NSF/NIH) mandates Responsible Conduct of Research training for covered personnel on the project.",
            "not_required": "This sponsor does not mandate RCR training, so it is not required for this project.",
            "review": "Check whether your sponsor requires RCR training.",
        },
        "timing": "NSF requires RCR training for students/postdocs; NIH for trainees. CITI modules take a few hours.",
        "task_title": "Complete RCR (CITI) training for project personnel",
    },
    {
        "id": "export_security",
        "title": "Export Control / Research Security",
        "eval": _rule_export_security,
        "why": {
            "required": "Your project involves export-controlled or sensitive technology, so a Technology Control Plan (TCP) and export-control review are required.",
            "not_required": "You indicated no export-controlled technology and no foreign collaboration, so an export-control review is not required.",
            "review": "Your project involves foreign collaborators or international elements — a Research Security review (NSPM-33) may apply. Check with ORA before proceeding.",
        },
        "timing": "Export / Research-Security reviews can gate your ability to start — involve ORA early.",
        "task_title": "Request Export Control / Technology Control Plan review with ORA",
    },
]

# Questionnaire shown to the PI. Derived from the rules above (the sponsor-driven
# triggers — RCR, PHS-COI — are NOT asked; they come from Submission.sponsor).
QUESTIONS = [
    {"key": "human_subjects", "label": "Does your project involve human subjects?",
     "help": "Surveys, interviews, focus groups, human data, tissue, or specimens."},
    {"key": "animals", "label": "Does your project involve live vertebrate animals?",
     "help": "Any use of live animals in research or teaching."},
    {"key": "financial_interest", "label": "Do you or a team member have a related financial interest?",
     "help": "Equity, a paid role, IP/royalties, or a board seat with a company tied to this research."},
    {"key": "foreign_collaboration", "label": "Does the project involve foreign collaborators or international elements?",
     "help": "Foreign co-investigators, foreign funding/subawards, or international travel/shipments."},
    {"key": "export_controlled", "label": "Does the project involve export-controlled or sensitive technology?",
     "help": "Controlled/military-applicable technology, restricted data, or items on the export-control lists."},
]


# ── doc-link resolution ────────────────────────────────────────────────────
def _resolve_doc(doc_id: str) -> dict:
    """Resolve a rule's doc_id -> {kb_doc_id, kb_doc_url, kb_doc_title}. Always
    returns the keys; url/title are None if the id can't be resolved (the UI
    then shows no button rather than a dead link)."""
    row = forms_catalog.resolve_kb_doc(doc_id) or forms_catalog.get_form(doc_id)
    return {
        "kb_doc_id": doc_id,
        "kb_doc_url": (row or {}).get("url") or None,
        "kb_doc_title": (row or {}).get("title") or None,
    }


# ── public API ─────────────────────────────────────────────────────────────
def assess_compliance(answers: Optional[dict], sponsor: Optional[str] = None) -> dict:
    """Evaluate every compliance rule against the questionnaire answers + the
    proposal sponsor. Returns the checklist + a status summary. Never raises."""
    answers = answers or {}
    items = []
    summary = {"required": 0, "not_required": 0, "review": 0}
    for rule in RULES:
        try:
            status, doc_id = rule["eval"](answers, sponsor)
        except Exception:
            status, doc_id = "review", rule.get("default_doc", "")
        summary[status] = summary.get(status, 0) + 1
        item = {
            "id": rule["id"],
            "title": rule["title"],
            "status": status,
            "why": rule["why"].get(status, ""),
            "timing": rule["timing"] if status in ("required", "review") else "",
        }
        item.update(_resolve_doc(doc_id))
        items.append(item)
    return {
        "answers": answers,
        "sponsor": sponsor,
        "items": items,
        "summary": summary,
        "warnings": [],
    }


def suggested_tasks(result: dict) -> list[dict]:
    """The SubmissionTasks to create on 'Add required items to my proposal'.
    One per REQUIRED item only (review items are advisory; not_required items
    are skipped). Carries the rule's kb_doc_id so the task's 'Open form' link
    resolves."""
    rules_by_id = {r["id"]: r for r in RULES}
    tasks = []
    for it in result.get("items", []):
        if it.get("status") != "required":
            continue
        rule = rules_by_id.get(it["id"], {})
        desc = it.get("why", "")
        if it.get("timing"):
            desc = (desc + " " + it["timing"]).strip()
        tasks.append({
            "title": rule.get("task_title") or it["title"],
            "description": desc,
            "kb_doc_id": it.get("kb_doc_id"),
        })
    return tasks


def questionnaire() -> dict:
    """Expose the questionnaire + a note about sponsor-derived triggers for the UI."""
    return {
        "questions": QUESTIONS,
        "sponsor_note": (
            "Some requirements come from your sponsor automatically: NSF/NIH funding "
            "requires RCR training, and PHS/NIH funding requires a COI disclosure from "
            "all investigators."
        ),
    }
