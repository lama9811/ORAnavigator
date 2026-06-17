# -*- coding: utf-8 -*-
"""
Eligibility go/no-go — Phase 3 of the "help researchers win" roadmap.
=====================================================================
A deterministic, rules-based self-check (in the spirit of compliance_sentinel)
that catches the most demoralizing mistake a first-time PI makes: spending weeks
on a proposal they were never eligible to submit.

It does NOT scrape eligibility from the solicitation automatically (that would
risk a wrong "you're fine"). Instead it asks the PI a few plain questions, maps
each answer to go / stop / check / coordinate, and surfaces the solicitation's
own eligibility text next to the questions so they can verify. The overall
verdict is conservative: any hard "no" is a STOP; any "unsure" is a CHECK.
"""

from typing import Optional

# answer values: "yes" | "no" | "unsure" (anything else -> treated as unanswered)
QUESTIONS = [
    {
        "id": "appointment_ok",
        "q": "Does your appointment match the PI eligibility this program requires "
             "(e.g., tenure-track, rank, or career-stage rules)?",
    },
    {
        "id": "org_eligible",
        "q": "Is Morgan State an eligible organization type for this program "
             "(e.g., it allows universities / minority-serving institutions)?",
    },
    {
        "id": "within_limits",
        "q": "Are you within any limits the solicitation sets (career-stage window, "
             "number of prior awards, one-submission-per-PI, etc.)?",
    },
    {
        "id": "limited_submission",
        "q": "Does this program cap how many proposals the whole institution may "
             "submit (a 'limited submission')?",
    },
]


def _verdict_hard(ans: Optional[str]):
    """For requirements that MUST be met: yes->ok, no->stop, else->check."""
    if ans == "yes":
        return "ok"
    if ans == "no":
        return "stop"
    return "check"


def assess_eligibility(answers: Optional[dict], sponsor: Optional[str] = None,
                       eligibility_text: Optional[str] = None) -> dict:
    """Evaluate the self-check. Returns {items, overall, eligibility_text}.
    overall: 'go' (all clear) | 'caution' (something to confirm/coordinate) |
    'stop' (a hard requirement is not met). Never raises."""
    ans = answers or {}
    items = []

    for qid, msg_ok, msg_stop, msg_check in (
        ("appointment_ok",
         "Your appointment fits the PI eligibility.",
         "STOP: your appointment does not meet this program's PI eligibility. Confirm with ORA pre-award before investing more time.",
         "Confirm your appointment meets the PI eligibility before drafting."),
        ("org_eligible",
         "Morgan State is an eligible organization for this program.",
         "STOP: Morgan State may not be an eligible organization type for this program. Verify with ORA before proceeding.",
         "Verify Morgan State is an eligible organization type for this program."),
        ("within_limits",
         "You're within the program's PI limits.",
         "STOP: you appear to be outside a limit the solicitation sets (career stage, prior-award count, etc.). Check with ORA.",
         "Check the solicitation's PI limits (career-stage window, prior awards, one-per-PI)."),
    ):
        status = _verdict_hard(ans.get(qid))
        msg = {"ok": msg_ok, "stop": msg_stop, "check": msg_check}[status]
        items.append({"id": qid, "status": status, "message": msg})

    # Limited submission is not a stop -- it requires INTERNAL coordination.
    ls = ans.get("limited_submission")
    if ls == "yes":
        items.append({"id": "limited_submission", "status": "coordinate",
                      "message": "This is a limited submission -- the institution must pick who applies. "
                                 "Contact ORA pre-award NOW to enter the internal competition; do not submit directly."})
    elif ls == "no":
        items.append({"id": "limited_submission", "status": "ok",
                      "message": "Not a limited submission -- no internal cap to coordinate."})
    else:
        items.append({"id": "limited_submission", "status": "check",
                      "message": "Check whether this is a limited submission; if so, ORA must coordinate the institutional entry."})

    statuses = {i["status"] for i in items}
    if "stop" in statuses:
        overall = "stop"
    elif {"check", "coordinate"} & statuses:
        overall = "caution"
    else:
        overall = "go"

    return {
        "items": items,
        "overall": overall,
        "eligibility_text": (eligibility_text or "").strip() or None,
    }
