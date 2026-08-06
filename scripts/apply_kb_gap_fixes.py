#!/usr/bin/env python3
"""Close the KB gaps found by the 2026-08-06 website audit.

What this fixes
---------------
A multi-agent audit compared all 75 live ORA pages against the 466 documents in
`backend/kb_structured/`. It found two classes of problem:

  * CONTRADICTIONS — the KB states a fact the live page refutes. These are worse
    than gaps: the assistant answers confidently and wrongly. The SAM expiration
    date, for instance, read as expired nine months ago, which to a PI filling in
    a federal form reads as "Morgan cannot submit."
  * GAPS — the page carries a fact the KB never captured.

Every edit here is grounded in a verbatim quote from the page capture taken on
2026-08-06 (golden rule 2). Nothing is inferred; nothing is invented.

Why several of these were invisible until now
---------------------------------------------
The crawler had two bugs, both fixed in the same change as this script:

  1. It did not expand morgan.edu's own accordion markup (`.accordion > a[href="#"]`
     toggling a `display:none` sibling), so 9 pages were read at a fraction of
     their real length — 36,332 characters missing, ~21% of the site. The pages
     returned HTTP 200 and simply looked thin, so nothing flagged an error.
  2. It never followed ORA's vanity URL `/spark`, because scope was a bare prefix
     test against the section path. SPARK — the flagship readiness program for
     research administrators — was never crawled and had no KB document at all.

Roughly half the fixes below come from content those bugs were hiding.

Usage
-----
    python3 scripts/apply_kb_gap_fixes.py --dry-run
    python3 scripts/apply_kb_gap_fixes.py
    python3 scripts/apply_kb_gap_fixes.py --revert

Scope: this writes the committed SNAPSHOT only (`backend/kb_structured/`). The
live Vertex datastore is a separate copy and nothing syncs them — push those
with `scripts/push_kb_gap_fixes.py` after reviewing the diff here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "backend" / "kb_structured"
MANIFEST = KB / "_all_documents.jsonl"
BACKUP = KB / "_gap_fix_backup.json"
AUDIT_DATE = "2026-08-06"

SITE = "https://www.morgan.edu/office-of-research-administration"


# ---------------------------------------------------------------------------
# Operations
#
# replace(old, new) — surgical, and FAILS LOUDLY if `old` is absent. That is
#   deliberate: a silent no-op would leave a contradiction in place while the
#   script reported success.
# append(text)      — add a paragraph to the end of `content`.
# set_field(k, v)   — write a struct_data field.
# rewrite(text)     — replace `content` wholesale (used where the summary was
#                     too thin to patch sentence by sentence).
# ---------------------------------------------------------------------------

def replace(old: str, new: str):
    def op(doc, doc_id):
        if old not in doc.get("content", ""):
            raise KeyError(f"{doc_id}: text to replace not found: {old[:70]!r}")
        doc["content"] = doc["content"].replace(old, new)
    return op


def replace_any(pairs: list[tuple[str, str]]):
    """Try each (old, new) in order; apply the first whose `old` is present.

    The DocuSign cards end their contact line two ways — sentence-initial
    ("Questions to …") and clause-final ("; questions to …") — so a single
    capitalised replacement matched some cards and silently skipped others,
    AND would have produced a mid-sentence capital on the ones it did match.
    """
    def op(doc, doc_id):
        body = doc.get("content", "")
        for old, new in pairs:
            if old in body:
                doc["content"] = body.replace(old, new)
                return
        raise KeyError(f"{doc_id}: none of the expected contact lines found")
    return op


def append(text: str):
    def op(doc, doc_id):
        body = doc.get("content", "").rstrip()
        if text.strip() in body:
            return
        doc["content"] = f"{body}\n\n{text.strip()}\n"
    return op


def set_field(key: str, value):
    def op(doc, doc_id):
        doc[key] = value
    return op


def drop_field(key: str):
    def op(doc, doc_id):
        doc.pop(key, None)
    return op


def rewrite(text: str):
    def op(doc, doc_id):
        doc["content"] = text.strip() + "\n"
    return op


# ---------------------------------------------------------------------------
# 1. CONTRADICTIONS — the KB is wrong. Fix these first.
# ---------------------------------------------------------------------------

FIXES: list[dict] = [
    {
        "doc_id": "pre_award_university_application_information",
        "why": "SAM expiration was a year stale and already past — reads as 'Morgan "
               "cannot submit'. Page: 'System for Award Management (SAM) Expiration "
               "Date: October 14, 2026'.",
        "ops": [
            replace(
                "- System for Award Management (SAM) Expiration Date: November 15, 2025",
                "- System for Award Management (SAM) Expiration Date: October 14, 2026",
            ),
            set_field("sam_expiration_note", "Verified against the live page on " + AUDIT_DATE),
        ],
        "key_facts": {"sam_expiration": "2026-10-14"},
    },
    {
        "doc_id": "compliance_news_and_updates",
        "why": "The KB asserted the page was empty ('Coming Soon!') while it carries a "
               "live NSF restriction on collaborations. The assistant was telling PIs "
               "there is no compliance news.",
        "ops": [rewrite(
            "The Research Compliance Updates and News page carries announcements about "
            "regulatory changes, policy revisions, training opportunities, and other "
            "compliance bulletins from the Office of Research Compliance.\n\n"
            "Published items:\n\n"
            "July 8, 2026 — NSF Dear Colleague Letter: Prohibitions on Collaborations "
            "with Restricted Entities. The National Science Foundation has issued updated "
            "guidance regarding prohibited collaborations with entities designated as U.S. "
            "Prohibited Parties. Researchers should review this notice to ensure all current "
            "and future NSF-funded activities comply with federal restrictions, and confirm "
            "that their research teams, partnerships, and subaward arrangements align with "
            "these requirements.\n\n"
            "Questions about compliance implications go to the Office of Research Compliance "
            "at research.compliance@morgan.edu. For general ORA enquiries, ask.ora@morgan.edu "
            "or 443-885-4044 (Tyler Hall, Student Service Building, Suite 304, Baltimore, MD "
            "21251). Faculty and staff can also subscribe to ORA Announcements."
        )],
        "key_facts": {
            "research_compliance_email": "research.compliance@morgan.edu",
            "latest_item_date": "2026-07-08",
        },
    },
    {
        "doc_id": "staff_keyshawn_moncrieffe",
        "why": "Title is no longer 'Acting' — the staff directory now says Director for "
               "Research Compliance.",
        "ops": [
            replace(
                "Dr. Keyshawn Moncrieffe, PhD is the Acting Director for Research Compliance",
                "Dr. Keyshawn Moncrieffe, PhD is the Director for Research Compliance",
            ),
            replace(
                "Dr. Keyshawn Moncrieffe serves as Acting Director for Research Compliance",
                "Dr. Keyshawn Moncrieffe serves as Director for Research Compliance",
            ),
            set_field("staff_title", "Director for Research Compliance, Research & Economic Development"),
            set_field("display_label", "Dr. Keyshawn Moncrieffe — Director for Research Compliance"),
        ],
    },
    {
        "doc_id": "compliance_human_subjects_research",
        "why": "The IRB meeting calendar was the 2025-2026 cycle — every date the "
               "assistant returned was wrong and in the past. Also: the revised Common "
               "Rule means most minimal-risk studies no longer need annual continuing "
               "review, which the KB stated as a blanket requirement.",
        "ops": [
            replace(
                "(2) Human Subjects Research Approval Renewal Form (annual continuing review)",
                "(2) Human Subjects Research Approval Renewal Form (continuing review, where "
                "the IRB requires it — see below)",
            ),
            # The prose carried the OLD cycle inline. Appending the new calendar
            # without removing this left the document stating BOTH schedules —
            # worse than the stale one alone, because the assistant could quote
            # either.
            replace(
                "The IRB meets monthly per a published calendar (e.g., 2025-2026 meeting dates "
                "run from August 27, 2025 through June 17, 2026 with application deadlines "
                "roughly two to three weeks prior).",
                "The IRB meets monthly per a published calendar; the current 2026-2027 "
                "deadlines and meeting dates are listed below.",
            ),
            append(
                "IRB Continuing Review. Under the revised Common Rule (45 CFR Part 46), many "
                "minimal-risk studies no longer require annual continuing review. The IRB may "
                "still require it for certain studies, including greater-than-minimal-risk "
                "research or where additional oversight is necessary. If a study requires "
                "continuing review, the Office of Research Compliance notifies the PI 60-90 "
                "days before the review due date. The submission must include a brief study "
                "progress update, current enrollment status, any protocol modifications, "
                "reportable events (if applicable), and any revised study documents. The IRB "
                "issues a written determination. If approval expires, all human subjects "
                "research activities must stop unless continuation is necessary to eliminate "
                "an immediate hazard to participants. Questions about whether a study requires "
                "continuing review go to irb.research@morgan.edu. See 45 CFR 46.109.\n\n"
                "IRB quorum: a quorum is a majority (greater than 50%) of the voting members "
                "of the IRB. For reasons other than conflict of interest, abstentions do not "
                "alter the quorum or change the number of votes required.\n\n"
                "The page also publishes an IRB Review Process Chart alongside the IRB Process "
                "Synopsis.\n\n"
                "IRB meeting schedule for 2026-2027 (application submission deadline -> IRB "
                "meeting date): Aug 7, 2026 -> Aug 26, 2026; Sep 4, 2026 -> Sep 30, 2026; "
                "Oct 2, 2026 -> Oct 28, 2026; Nov 6, 2026 -> Nov 18, 2026; Dec 4, 2026 -> "
                "Dec 9, 2026; Jan 1, 2027 -> Jan 27, 2027; Feb 5, 2027 -> Feb 24, 2027; "
                "Mar 5, 2027 -> Mar 31, 2027; Apr 2, 2027 -> Apr 28, 2027; May 7, 2027 -> "
                "May 26, 2027; Jun 4, 2027 -> Jun 9, 2027."
            ),
            drop_field("irb_meeting_schedule_2025_2026"),
            set_field("irb_meeting_schedule_2026_2027", [
                {"submission_deadline": "2026-08-07", "meeting_date": "2026-08-26"},
                {"submission_deadline": "2026-09-04", "meeting_date": "2026-09-30"},
                {"submission_deadline": "2026-10-02", "meeting_date": "2026-10-28"},
                {"submission_deadline": "2026-11-06", "meeting_date": "2026-11-18"},
                {"submission_deadline": "2026-12-04", "meeting_date": "2026-12-09"},
                {"submission_deadline": "2027-01-01", "meeting_date": "2027-01-27"},
                {"submission_deadline": "2027-02-05", "meeting_date": "2027-02-24"},
                {"submission_deadline": "2027-03-05", "meeting_date": "2027-03-31"},
                {"submission_deadline": "2027-04-02", "meeting_date": "2027-04-28"},
                {"submission_deadline": "2027-05-07", "meeting_date": "2027-05-26"},
                {"submission_deadline": "2027-06-04", "meeting_date": "2027-06-09"},
            ]),
        ],
    },
    {
        "doc_id": "form_irb_approval_renewal",
        "why": "Described continuing review as unconditionally annual; the revised Common "
               "Rule exempts many minimal-risk studies.",
        "ops": [rewrite(
            "IRB continuing-review renewal form, used to extend IRB approval for an ongoing "
            "human-subjects study.\n\n"
            "Continuing review is NOT automatically annual. Under the revised Common Rule "
            "(45 CFR Part 46), many minimal-risk studies no longer require annual continuing "
            "review. The IRB may still require it for greater-than-minimal-risk research or "
            "where additional oversight is necessary. Where review IS required, the Office of "
            "Research Compliance notifies the PI 60-90 days before the due date, and the "
            "submission must include a brief study progress update, current enrollment status, "
            "any protocol modifications, reportable events (if applicable), and any revised "
            "study documents.\n\n"
            "Submit before the current approval period expires. If approval lapses, all human "
            "subjects research activities must stop unless continuation is necessary to "
            "eliminate an immediate hazard to participants. Submit to irb.research@morgan.edu. "
            "See 45 CFR 46.109."
        )],
    },
    {
        "doc_id": "form_docusign_stipend_request",
        "why": "KB stated a 45-day hard minimum; the page now says 30 days, 'ideally', with "
               "an escalation path. Also had no definition of what a stipend may be used for "
               "— the most common stipend mistake is paying a student for work.",
        "ops": [rewrite(
            "DocuSign form for requesting stipend disbursement on sponsored projects at Morgan "
            "State University. Available for both non-Title III and Title III funded stipends.\n\n"
            "What a stipend is: funding for a Morgan State University student whom the sponsored "
            "project is supporting for research and learning purposes ONLY. Stipends are not "
            "given for employment or working. They are given to students as funding so that the "
            "student does not have to work but instead is learning through the sponsored project.\n\n"
            "Timing: the Check Issue Date should ideally be at least 30 days out from the date "
            "the memo is submitted. If an earlier Check Issue Date is required, contact "
            "ask.ora@morgan.edu for review.\n\n"
            "Route to the MSU ORA signing group. Questions about the DocuSign request forms go "
            "to Rebecca Steiner in the Office of Research Administration, "
            "rebecca.steiner@morgan.edu or 443-885-4044."
        )],
    },
    {
        "doc_id": "post_award_forms_index",
        "why": "Repeated the 45-day stipend minimum the page now states as 30 days.",
        "ops": [replace(
            "Stipend Request Memo — requires the requested check issue date to be a minimum of "
            "45 days after submission of the request.",
            "Stipend Request Memo — the Check Issue Date should ideally be at least 30 days out "
            "from the date the memo is submitted; contact ask.ora@morgan.edu if an earlier date "
            "is required. Covers both non-Title III and Title III funded stipends.",
        )],
    },
    {
        "doc_id": "post_award_reporting",
        "why": "Ryan Mobley's title is 'Training and Communications Coordinator' on the page "
               "and in the eTraining docs; this one said 'Training Coordinator'. Effort "
               "certification also covers federal flow-through subcontracts.",
        "ops": [
            replace(
                "Mr. Ryan Mobley, Training Coordinator",
                "Mr. Ryan Mobley, Training and Communications Coordinator",
            ),
            append(
                "Scope of effort certification: all PIs/PDs, faculty, professional staff, and "
                "students paid directly from federal grants and contracts — INCLUDING federal "
                "flow-through sub-contracts — are required to certify their effort "
                "electronically at the end of the Fall semester, Spring semester, and Summer. "
                "Personnel paid from a subaward are not exempt.\n\n"
                "A sponsored project, for effort-reporting purposes, is a funded project "
                "covering a range of activities including research, teaching, training, and "
                "services. At MSU, most sponsors that require time and effort certification are "
                "federal agencies, state agencies, and federally sponsored contracts."
            ),
        ],
    },
    {
        "doc_id": "post_award_notification_and_setup",
        "why": "Named the wrong contact for the Post-Award Briefing — Dr. Edet Isuk, who does "
               "not appear on the page and has retired. The page names Ryan Mobley.",
        "ops": [append(
            "Post-Award Briefing contact: Mr. Ryan Mobley, Training and Communications "
            "Coordinator, Office of Research Administration — ask.ora@morgan.edu."
        )],
        "key_facts_remove": ["research_compliance_director"],
        "key_facts": {
            "post_award_briefing_contact": "Ryan Mobley, Training and Communications Coordinator (ask.ora@morgan.edu)",
        },
    },

    # -----------------------------------------------------------------------
    # 2. GAPS — the page says something the KB never captured.
    # -----------------------------------------------------------------------
    {
        "doc_id": "pre_award_budget_development",
        "why": "The single worst-covered page in the KB: 11,403 characters summarised in "
               "2,790. Missing the MTDC definition and the $25,000 subaward cap, the "
               "participant-support exclusion list, the domestic/foreign travel rule, three "
               "whole expense categories, and the tuition-remission tax status.",
        "ops": [rewrite(
            "PI Handbook 2: Grant Budgets carries the most complete guidance; this is the "
            "overview from the Budget Development page.\n\n"
            "The budget, also called a cost proposal, is as important as the technical "
            "proposal. The funding agency grants money on the basis of the line-by-line budget "
            "estimate. Budgets typically include direct, other direct, indirect, and other "
            "budget elements.\n\n"
            "PERSONNEL. Salaries must be consistent with Morgan's regular practices. Other "
            "Personnel categories: Post Doctoral, Lab Technician, Graduate Students, "
            "Undergraduate Students, Teaching Assistantship, Secretarial/Clerical. The budget "
            "must include funds for fringe benefits for both regular and contractual employees.\n\n"
            "FRINGE BENEFIT RATES. Regular employees 42% of gross salary; faculty during the "
            "academic year 42%; faculty during the summer 9%; contractual employees 9%.\n\n"
            "EQUIPMENT. An item of property with an acquisition cost of $5,000 or more and an "
            "expected service life of more than one year. A brief description and justification "
            "showing purpose, function and cost is necessary. A written quote is required in "
            "many cases.\n\n"
            "TRAVEL. Includes domestic and international travel for fieldwork and attendance at "
            "conferences and seminars. Domestic travel includes travel in the United States, its "
            "possessions, and travel to Puerto Rico and Canada — all other travel is foreign "
            "travel. Government sponsors generally require travel by U.S. flagged carriers where "
            "available. Current 2025 State of Maryland reimbursable rates: personal automobile "
            "$0.70 per mile; portage $1 per bag; meals — breakfast $15.00, lunch $18.00, dinner "
            "$30.00, total allowed $63.00. Gratuities are included in the meal rates. An "
            "employee travelling out of state may receive an adjusted meal amount depending on "
            "geographical location.\n\n"
            "PARTICIPANT SUPPORT. Used to defray the cost of students participating in a "
            "training activity, conference or symposium related to the project. It includes "
            "stipends, subsistence allowances, travel, and registration fees paid on behalf of "
            "or to participants or trainees. Faculty and staff can be participants only if funds "
            "are used exclusively for training and conferences and the funding agency approves.\n\n"
            "  Who is a participant: a non-MSU employee who is the recipient, not the provider, "
            "of a service or training associated with a workshop, conference, seminar, symposium "
            "or other short-term instructional activity. Participants do not perform work or "
            "services for the project unless it is for their own benefit. They may include "
            "students, scholars and scientists from other institutions, private-sector "
            "representatives, teachers, and state or local government personnel.\n\n"
            "  What participant support does NOT include: honoraria for guest speakers; expenses "
            "for the PI, project staff or collaborators to attend project meetings, conferences "
            "or seminars; payments to GRAs; and payments made to research subjects as an "
            "incentive for recruitment or participation. Morgan employees are not participants, "
            "so costs incurred for Morgan employees do not qualify.\n\n"
            "  Persons (students) compensated for services rendered on a sponsored project based "
            "on hours worked are considered EMPLOYEES, NOT participants.\n\n"
            "  Stipend exception: for some educational projects conducted at local school "
            "districts the participants being trained are employees. In such cases the costs must "
            "be classified as participant support if payment is made through a stipend or "
            "training allowance method. Subaccount codes differentiate regular salary from "
            "stipend payments.\n\n"
            "  Requirements: the number of participants to be supported must be entered in the "
            "parentheses on the proposal budget. Costs must be specified, itemized and justified "
            "in the budget justification. Indirect costs (F&A) are NOT allowed on participant "
            "support costs. Participant support costs must be accounted for separately should an "
            "award be made. Participant travel is subject to the class-of-accommodation and "
            "US-flag air carrier restrictions. Subsistence allowances must be reasonable, "
            "conform to the proposing organization's policy, and be limited to days of "
            "attendance plus actual travel time; where meals or lodging are furnished free or at "
            "nominal cost, the allowance is correspondingly reduced.\n\n"
            "MATERIALS AND SUPPLIES. Printing instructional materials, office supplies, and "
            "audiovisual and computer supplies.\n\n"
            "PUBLICATION/DOCUMENT. Documenting, preparing, publishing or otherwise making "
            "available the findings and products of the work conducted under the grant.\n\n"
            "CONSULTANT SERVICES. A consultant provides advice or services and may participate "
            "significantly in the project. Inter-departmental or intra-institutional consulting "
            "is regarded as professional courtesy, so MSU faculty may NOT be listed as "
            "consultants — list them as project personnel if they contribute substantively. The "
            "budget justification should describe the services, estimated time required, and "
            "rate of payment (usually per hour or per day). Payment should be comparable to the "
            "consultant's normal fees; check sponsor instructions for any required rate limit.\n\n"
            "COMPUTER SERVICES. Maintaining special computer-related equipment; data processing; "
            "the cost of computer services, including computer-based retrieval of scientific, "
            "technical and educational information.\n\n"
            "COMMUNICATIONS. Postage, mailing services, and telecommunication charges.\n\n"
            "SUBAWARDS. Also called subgrants, subagreements or subcontracts, issued to a "
            "subrecipient for assistance in carrying out a specified programmatic effort, with "
            "the University passing through a portion of the award. All applicable prime-award "
            "terms and conditions must be included in the subaward document. Each subagreement "
            "should be identified separately with a brief explanation of the services and the "
            "appropriateness and reasonableness of the cost. Sub-agreements, sub-awards and "
            "subcontracts are NOT executed until the award or contract is received by the "
            "University.\n\n"
            "OTHERS. Tuition, grants, scholarships and awards provided for support of graduate "
            "or undergraduate research assistants. Tuition remission is not taxable.\n\n"
            "FACILITIES AND ADMINISTRATIVE COST (INDIRECT COST). Appears as a single separate "
            "item in the budget. This item is not University profit; it represents the total "
            "real costs to the university in support of the project which cannot be directly "
            "attributed to a project activity — a portion of purchasing and procurement, "
            "personnel, payroll, building and equipment maintenance, office space, utilities, "
            "library maintenance and research administration. The Budget Development page states "
            "the current on-campus research F&A cost as 53% of modified total direct cost, "
            "approved by the U.S. Department of Health and Human Services, Division of Cost "
            "Allocation, Region III. NOTE: the F&A Cost Rates page lists 54.00% on-campus "
            "organized research for 07/01/2025-06/30/2026 and 53.00% for the prior year — check "
            "the F&A Cost Rates page for the rate applying to your budget period.\n\n"
            "MODIFIED TOTAL DIRECT COST (MTDC) is the total direct cost EXCLUDING capital "
            "expenditures, equipment, charges for patient care, student tuition remission, "
            "rental costs of off-site facilities, scholarships, and fellowships, as well as the "
            "portion of each subgrant and subcontract in excess of $25,000.\n\n"
            "Budget development resources on the page: PI Handbook 2: Grant Budgets; Budget "
            "Template; Budget Justification Template; Budget Preparation PowerPoint slides "
            "(March 10, 2021 workshop); MSU F&A/IDC Rates; successful budget and justification "
            "examples."
        )],
        "key_facts": {
            "mtdc_subaward_cap": "$25,000 per subgrant/subcontract counts toward MTDC",
            "equipment_threshold": "$5,000 and service life over one year",
            "mileage_rate_2025": "$0.70 per mile",
            "portage_rate_2025": "$1 per bag",
            "meal_per_diem_2025": {"breakfast": 15.00, "lunch": 18.00, "dinner": 30.00, "total": 63.00},
            "fringe_rates": {"regular": "42%", "faculty_academic_year": "42%",
                             "faculty_summer": "9%", "contractual": "9%"},
            "fa_cognizant_office": "DHHS, Division of Cost Allocation, Region III",
        },
    },
    {
        "doc_id": "ora_history",
        "why": "Missing the entire MSU institutional timeline, PEARL, the $7.5M DoD "
               "Electro-Photonics center, $15M recurring state funds, and the top-10 "
               "tech-transfer ranking. None of these strings appeared anywhere in the KB.",
        "ops": [append(
            "Achievements under Dr. Victor McCrary (Vice President, D-RED, 2012-2018): "
            "acquiring multi-year funding for major transdisciplinary projects such as ASCEND "
            "(A Student-Centered Entrepreneurship Development Model to Enhance the Diversity of "
            "Biomedical Research Workforce); establishing and equipping research centers such as "
            "PEARL (Patuxent Environmental and Aquatic Research Laboratory); and establishing an "
            "Office of Technology Transfer.\n\n"
            "Major awards under Dr. Willie May — who is the former Director of the National "
            "Institute of Standards and Technology (NIST) and former Under Secretary of "
            "Commerce: renewal and substantial expansion of GESTAR (GESTAR II), a cooperative "
            "agreement receiving $28 million from NASA; renewal of ASCEND, receiving an "
            "additional $17 million from NIH; RCMI at Morgan, a cooperative agreement receiving "
            "$15 million to establish research centers addressing health disparities; the Center "
            "for Advanced Electro-Photonics and 2D Materials, a cooperative agreement funded by "
            "the Department of Defense for $7.5 million; and a program to conduct research and "
            "education in equitable artificial intelligence (AI) and machine learning (ML), "
            "funded by the Office of Naval Research (ONR) for $9 million. In 2022 new funding "
            "commitments reached $72 million.\n\n"
            "Further advances under Dr. May's leadership: established multiple research centers "
            "with over $15 million of recurring funds from the State of Maryland; enhanced the "
            "technology transfer program to be among the top 10 universities nationwide when "
            "adjusted for research expenditures; and substantially enhanced the research "
            "portfolio across both STEM and non-STEM research addressing health disparities.\n\n"
            "MSU institutional timeline: 2018 R2 Designation; 2017 named State of Maryland's "
            "Preeminent Public Urban Research University; 2016 Office of Technology Transfer "
            "established; 2012 Division of Research & Economic Development established; 2006 R3 "
            "Designation; 1998 School of Computer, Mathematical, and Natural Sciences founded; "
            "1988 Office of Sponsored Programs and Research established; 1984 School of "
            "Engineering founded; 1983 first doctoral student graduates; 1975 Morgan State "
            "University; 1939 Morgan State College; 1890 Morgan College; 1867 Centenary Biblical "
            "Institute. MSU's strategic goal is to become a Carnegie-classified R1 (Doctoral "
            "University - Very High Research Activity) by 2030."
        )],
        "key_facts": {
            "institutional_timeline": {
                "1867": "Centenary Biblical Institute", "1890": "Morgan College",
                "1939": "Morgan State College", "1975": "Morgan State University",
                "1983": "First doctoral student graduates", "1984": "School of Engineering founded",
                "1988": "Office of Sponsored Programs and Research established",
                "1998": "School of Computer, Mathematical, and Natural Sciences founded",
                "2006": "R3 Designation", "2012": "Division of Research & Economic Development established",
                "2016": "Office of Technology Transfer established",
                "2017": "Named State of Maryland's Preeminent Public Urban Research University",
                "2018": "R2 Designation",
            },
            "r1_goal_year": 2030,
        },
    },
    {
        "doc_id": "trainings_monthly_d_red",
        "why": "The two newest seminars (June 2026) were missing — the KB's latest was April "
               "2026. The whole External Trainings block (7 resources) was absent, including "
               "the SciENcv biosketch how-to that the January 2026 announcement mandates.",
        "ops": [append(
            "Recent seminars:\n\n"
            "Jun 15, 2026 — SBIR and STTR Program: Overview / Demystifying SBIR and STTR "
            "Funding. Presenter: Rich Giersch, Life Science Works. No recording available.\n\n"
            "Jun 10, 2026 — Oops!... I Did It Again: Avoiding Common Pitfalls in Post-Award "
            "Grant Management (Post-Award Spending; Subaward Recipient Monitoring). Presenters: "
            "Lucy Manyara, DBA; Shamon Shine-Lee; Matthew Lee, PMP; Poline Mirithu. Recording "
            "available.\n\n"
            "External Trainings listed on the same page (not ORA-sponsored): NIH virtual "
            "conference and sessions 2022-2023; Virginia Tech CRA Study Team info; How to make "
            "an NIH biosketch using My Bibliography and SciENcv; My Bibliography; Add Preprint "
            "Citations in My Bibliography; NCBI and the NIH Public Access Policy; and the "
            "Maryland Governor's Grants Office training."
        )],
    },
    {
        "doc_id": "pre_award_internal_routing_form",
        "why": "Missing the three cost-share types and that cost share needs ORA pre-approval, "
               "the foreign-national definition, the release-time entry rule, the MSU-share "
               "rule, the NSF-only Equal Collaborator role, and the radioactive-materials "
               "authorization gate.",
        "ops": [append(
            "Cost share on the IRF: answer YES or NO — if YES, cost share requires PRE-APPROVAL "
            "from ORA. The three types the form asks about are in-kind time (effort), indirect "
            "cost (waiver), and cash. Cash cost share is very rare and requires pre-approval.\n\n"
            "MSU Share of Funding: if 100% of the request is for MSU, repeat the Total Funding "
            "($) Requested here, including funds to be sent to subawardees. If MSU is the "
            "subawardee, enter the dollar amount projected for just MSU.\n\n"
            "Partner Role in the Application options: Prime, Subawardee, Equal Collaborator "
            "(NSF-only), or Other.\n\n"
            "Faculty release time: if YES, enter EACH faculty member requesting release time "
            "under the project's funds, including NAME, ROLE, and PERCENTAGE (%) of effort "
            "requested.\n\n"
            "Foreign nationals: a foreign national is defined as any person who is not a U.S. "
            "citizen by birth or naturalization.\n\n"
            "Radioactive materials and devices: the form asks separately whether the project "
            "involves radioactive materials and whether it involves use of a radioactive device. "
            "The PI must be authorized if the answer is YES to either.\n\n"
            "Surety questions include whether the applicant has been convicted in the preceding "
            "three years of any offense listed in 2 CFR part 180.800(a), or had a civil judgement "
            "for one of those offenses within that period."
        )],
    },
    {
        "doc_id": "form_docusign_honoraria_request",
        "why": "Missing the warning that an apparent honorarium may actually require "
               "procurement — the most consequential branch point when paying an outside "
               "speaker — and that the form covers both Title III and non-Title III funds.",
        "ops": [append(
            "Covers both non-Title III and Title III funded honoraria (unlike travel, which has "
            "two separate forms).\n\n"
            "Important: oftentimes what seems like an Honorarium may actually require a "
            "Procurement or Contractual process. The ORA team is available to answer specific "
            "questions before you submit.\n\n"
            "Questions about the DocuSign request forms go to Rebecca Steiner in the Office of "
            "Research Administration, rebecca.steiner@morgan.edu or 443-885-4044."
        )],
    },
    {
        "doc_id": "opportunity_db_infoed_spin",
        "why": "No mention of how to get an account — the most common first-contact question "
               "about a subscription database.",
        "ops": [append(
            "Access: sign up and log in using your Morgan State institutional email. "
            "Self-registration is available from the Funding Sources page. A personal email "
            "address will not grant institutional access."
        )],
    },
    {
        "doc_id": "opportunity_db_pivot_rp",
        "why": "ORA publishes a 'how to use Pivot RP' guide the KB never mentioned, and the "
               "signup-email condition was missing.",
        "ops": [append(
            "Access: sign up and log in using your Morgan State institutional email — signing up "
            "with a personal address is the usual reason a first attempt fails to get "
            "institutional access. ORA also links a 'further information about using Pivot RP' "
            "guide from the Funding Sources page."
        )],
    },
    {
        "doc_id": "pre_award_overview",
        "why": "Had the Pivot URL but not the institutional-email signup condition.",
        "ops": [append(
            "Pivot-RP Funding Opportunities Database: use your Morgan email address on sign up."
        )],
    },
    {
        "doc_id": "pre_award_proposal_components",
        "why": "Kept the section descriptions and dropped the actionable drafting instructions "
               "a first-time PI actually asks for.",
        "ops": [append(
            "Drafting notes from the page: although the abstract appears first in the proposal, "
            "it should be written last, with the thought that it may be the only part of the "
            "proposal some agency reviewers read. Relevant publications of key personnel must be "
            "listed in the appropriate section. In some proposals the project description may "
            "also include the projected sequence or timetable for the project."
        )],
    },
    {
        "doc_id": "trainings_new_faculty",
        "why": "The forward-looking syllabus document was not referenced.",
        "ops": [append(
            "The page also links the 2025/2026 Faculty Development Seminar Syllabus."
        )],
    },
    {
        "doc_id": "trainings_test_prep",
        "why": "Said 15 chapters (an appended note said 16); the page lists 18. Internal "
               "Controls and HERD Survey had no documents at all.",
        "ops": [
            replace(
                "ORA provides 15 chapter-based PDF study guides",
                "ORA provides 18 chapter-based PDF study guides",
            ),
            append(
                "17) Internal Controls\n18) HERD Survey\n\n"
                "Verified against the live page on " + AUDIT_DATE + ": 18 chapters."
            ),
            set_field("chapter_count", 18),
        ],
    },
    {
        "doc_id": "trainings_overview",
        "why": "Described the catalogue as seven sections and omitted SPARK entirely — SPARK "
               "is the FIRST item on the Trainings page. That framing is what let it "
               "disappear silently. Also repeated the wrong test-prep chapter count.",
        "ops": [
            replace(
                "The ORA training catalog is organized into the following seven sections, each with its own subpage:",
                "The ORA training catalog is organized into the following eight sections, each "
                "with its own subpage. SPARK is listed first on the Trainings page:\n\n"
                "0) SPARK (https://www.morgan.edu/spark) — Sponsored Projects Administration "
                "Readiness and Knowledge: ORA's training program for Morgan State University "
                "research staff and administrators. See the SPARK document for the full program.",
            ),
            replace(
                "Fifteen chapter-based PDF study guides are provided.",
                "Eighteen chapter-based PDF study guides are provided.",
            ),
            set_field("key_facts", {
                "subpages": [
                    "spark", "e-training", "research-compliance-and-security-training",
                    "new-faculty-development-seminars", "monthly-d-red-seminars",
                    "special-workshops", "test-prep", "msu-trainings-outside-ora",
                ],
                "general_contact": {
                    "phone": "443-885-4044", "fax": "443-885-8280",
                    "email": "ask.ora@morgan.edu",
                    "address": "Tyler Hall, Student Service Bldg, Suite 304, Baltimore, MD 21251",
                },
                "key_coordinators": [
                    "Dr. Farin Kamangar (MD, PhD, CRA, CPRA, CFRA)",
                    "Gillian Silver (MPH, CPH, CRA, CPRA, CFRA)",
                    "Ryan Mobley (CRA, CFRA, CPRA)",
                    "Rebecca (Becca) Steiner",
                ],
            }),
        ],
    },
]


# The nine post-award DocuSign form cards all end with an inbox the live page no
# longer advertises. Generated rather than written out to keep them identical.
_DOCUSIGN_CARDS = [
    "form_docusign_honoraria_request",
    "form_docusign_stipend_request",
    "form_docusign_pf10_contractual_personnel",
    "form_docusign_travel_request_non_title_iii",
    "form_docusign_travel_request_title_iii",
    "form_docusign_travel_reimbursement_gad_x5",
    "form_docusign_tuition_fees_grad",
    "form_docusign_tuition_fees_undergrad",
]

for _doc_id in _DOCUSIGN_CARDS:
    FIXES.append({
        "doc_id": _doc_id,
        "why": "Page names Rebecca Steiner as the DocuSign forms contact; the card pointed at "
               "ora-docusign@morgan.edu, which the live page no longer advertises.",
        "ops": [replace_any([
            ("Questions to ora-docusign@morgan.edu.",
             "Questions about these DocuSign request forms go to Rebecca Steiner in the Office "
             "of Research Administration, rebecca.steiner@morgan.edu or 443-885-4044."),
            ("questions to ora-docusign@morgan.edu.",
             "questions to Rebecca Steiner in the Office of Research Administration, "
             "rebecca.steiner@morgan.edu or 443-885-4044."),
        ])],
        "optional": True,   # two of these were already rewritten above
    })


# ---------------------------------------------------------------------------
# 3. NEW DOCUMENTS
# ---------------------------------------------------------------------------

NEW_DOCS: list[dict] = [
    {
        "doc_id": "trainings_spark",
        "title": "SPARK — Sponsored Projects Administration Readiness and Knowledge",
        "category": "trainings",
        "subcategory": "spark",
        "display_label": "SPARK Program",
        "source_url": "https://www.morgan.edu/spark",
        "procedure_url": "https://www.morgan.edu/spark",
        "kb_path": "trainings/spark",
        "file_path": "trainings/spark/trainings_spark.json",
        "last_scraped": AUDIT_DATE,
        "playwright_verified": True,
        "playwright_verified_at": AUDIT_DATE,
        "content": (
            "SPARK — Sponsored Projects Administration Readiness and Knowledge — is ORA's "
            "training program for Morgan State University RESEARCH STAFF AND ADMINISTRATORS. "
            "It is not a proposal-writing program for faculty PIs.\n\n"
            "ORA's SPARK program prepares Morgan State University's research staff and "
            "administrators for success by building foundational knowledge, practical skills, "
            "and confidence in supporting sponsored projects. SPARK introduces participants to "
            "key processes, expectations, and institutional practices while creating a pathway "
            "for continued learning. It is a scalable framework for MSU research administrator "
            "readiness.\n\n"
            "TWO-PRONGED APPROACH. (1) A foundational overview of the federal, state, and "
            "sponsor-specific requirements that guide sponsored-programs administration and help "
            "ensure MSU maintains compliance. (2) A series of practical modules that explain the "
            "specific key processes, systems, and institutional workflows involved in "
            "administering sponsored programs at Morgan State University.\n\n"
            "HOW PROGRESS IS TRACKED. ORA monitors participant progress through attendance at "
            "research administration (RA) review sessions, completion of assigned Canvas "
            "modules, and submission of the ORA SPARK pre- and post-assessments. Together these "
            "confirm that participants have satisfied program requirements. Canvas is the "
            "learning management system of record for SPARK.\n\n"
            "ONBOARDING (initial phase). A structured introduction to research administration "
            "through a blend of self-paced and live learning. Participants complete e-learning "
            "modules with associated quizzes, the CITI Essentials of Research Administration "
            "course, and virtual RA intensives focused on key topics in the profession.\n\n"
            "CITI PROGRAM. Through CITI, SPARK offers the Essentials of Research Administration "
            "course, an overview introduction to the profession in five lessons: Elements of "
            "Research Administration; Elements of Research Development; Elements of Pre-Award; "
            "Elements of Award Negotiation & Acceptance; Elements of Post Award. (This is a "
            "different CITI course from the HSR/RCR/COI compliance training.)\n\n"
            "SPARK CAMPFIRE SERIES. Focused ONE-HOUR learning sessions on key topics in research "
            "administration, combining practical instruction with discussion and Q&A. Topics may "
            "include cost share, Uniform Guidance, compensation on sponsored projects, financial "
            "compliance and expense monitoring, subaward establishment and monitoring, research "
            "compliance, export controls, research security, IRB, IACUC, and other timely or "
            "high-priority areas. The series aims to foster a community of research "
            "administration professionals across campus.\n\n"
            "SPARK e-LEARNING MODULES. ORA's training staff developed a series of e-learning "
            "modules tailored to the administration of research at Morgan State. Seven modules, "
            "3 hours 40 minutes in total:\n\n"
            "  1. Navigating Banner: Essentials for Research Administration (30 min) — Banner "
            "access; grant indexes and budget pools; available budget balances; payroll and "
            "fringe visibility; Banner/Argos reporting tools; award budget structure; key award "
            "information; labor and cost redistributions.\n"
            "  2. Purchasing with Purpose: Procurement on Sponsored Projects (45 min) — "
            "procurement roles and responsibilities; allowability and allocability; purchasing "
            "thresholds; P-card considerations; vendor setup; requisition processing; "
            "documentation; working fund administration.\n"
            "  3. Travel on Sponsored Projects: Approval to Reimbursement (30 min) — travel "
            "allowability; pre-approval requirements; sponsor and institutional rules; airfare, "
            "lodging, meals and mileage; documentation standards; reimbursement process; common "
            "compliance concerns.\n"
            "  4. Supporting Participants: Payments, Benefits, and Indirect Costs (30 min) — "
            "participant support costs including stipends, tuition, travel and healthcare; "
            "participant support restrictions; indirect cost impact; rebudgeting considerations.\n"
            "  5. Building the Project Team: Contractual Hiring and Compensation (45 min) — "
            "contractual hiring; compensation allowability; institutional base salary "
            "considerations; secondary contract rules; release time requests; EPAF submissions; "
            "labor allocability; faculty summer contract calculations.\n"
            "  6. Show Your Work: Understanding Time and Effort Reporting (30 min) — effort "
            "reporting under Uniform Guidance; SearchLight process; certification "
            "responsibilities; percent effort allocation; audit risk.\n"
            "  7. It Takes a Village: Understanding Cost Share on Sponsored Projects (30 min) — "
            "mandatory vs. voluntary committed cost share; tracking; documentation requirements; "
            "allowable cost share sources; third-party contributions.\n\n"
            "These are the same modules published as ORA eTraining; the eighth eTraining module, "
            "Post Award Quick Guide, is not listed in the SPARK table.\n\n"
            "HOW TO JOIN. Complete ORA's Google Form to express interest in the SPARK program. "
            "SPARK is currently only available to employees of Morgan State University, and a "
            "valid MSU email address is required to complete the form."
        ),
        "key_facts": {
            "audience": "MSU research staff and administrators (employees only)",
            "lms": "Canvas",
            "citi_course": "Essentials of Research Administration (5 lessons)",
            "campfire_session_length": "1 hour",
            "elearning_modules": 7,
            "elearning_total_minutes": 220,
            "eligibility": "MSU employees only; valid MSU email address required",
        },
    },
    {
        "doc_id": "form_racc_chapter_internal_controls",
        "title": "RACC Test Prep — Internal Controls Chapter (PDF)",
        "category": "trainings",
        "subcategory": "test_prep_material",
        "display_label": "RACC Internal Controls Test Prep Chapter (PDF)",
        "source_url": f"{SITE}/trainings/test-prep",
        "procedure_url": "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/Test%20Prep/InternalControls.pdf",
        "kb_path": "trainings/test_prep",
        "file_path": "trainings/test_prep/form_racc_chapter_internal_controls.json",
        "last_scraped": AUDIT_DATE,
        "playwright_verified": True,
        "playwright_verified_at": AUDIT_DATE,
        "legacy_category": "form",
        "content": (
            "ORA-prepared RACC test prep chapter 17, Internal Controls — a self-contained study "
            "packet with background information, practice questions and answers, from 'A Complete "
            "Guide to the CRA, CPRA, and CFRA Exams' by Farin Kamangar, Lulu Jiang and Rebecca "
            "Steiner (published 2026-07-13, 17 pages).\n\n"
            "Contents: 17.1 Introduction; 17.2 The Five Components of Internal Control; 17.3 "
            "Summary; 17.4 Practice Questions; 17.5 Answers to Practice Questions.\n\n"
            "Internal controls are the processes, policies and procedures an organization uses to "
            "provide reasonable assurance that objectives are achieved efficiently, effectively, "
            "and with reliable, compliant reporting. Under Uniform Guidance (2 C.F.R. 200.303) "
            "internal controls are elevated as a central accountability measure."
        ),
    },
    {
        "doc_id": "form_racc_chapter_herd_survey",
        "title": "RACC Test Prep — HERD Survey Chapter (PDF)",
        "category": "trainings",
        "subcategory": "test_prep_material",
        "display_label": "RACC HERD Survey Test Prep Chapter (PDF)",
        "source_url": f"{SITE}/trainings/test-prep",
        "procedure_url": "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/Test%20Prep/HERD.pdf",
        "kb_path": "trainings/test_prep",
        "file_path": "trainings/test_prep/form_racc_chapter_herd_survey.json",
        "last_scraped": AUDIT_DATE,
        "playwright_verified": True,
        "playwright_verified_at": AUDIT_DATE,
        "legacy_category": "form",
        "content": (
            "ORA-prepared RACC test prep chapter 18, the HERD Survey — a self-contained study "
            "packet with background information, practice questions and answers, from 'A Complete "
            "Guide to the CRA, CPRA, and CFRA Exams' by Farin Kamangar, Lulu Jiang and Rebecca "
            "Steiner (published 2026-07-23, 19 pages).\n\n"
            "Contents: 18.1 Introduction; 18.2 Participation; 18.3 Key Variables; 18.4 What "
            "Counts as R&D?; 18.5 HERD's Role in Institutional Classifications; 18.6 Summary; "
            "18.7 Practice Questions; 18.8 Answers to Practice Questions.\n\n"
            "HERD is the Higher Education Research and Development Survey, the federal annual "
            "census of R&D expenditures at U.S. colleges and universities. It is the survey "
            "Morgan reports research expenditures to, and its figures feed institutional research "
            "classifications — directly relevant to Morgan's R1 by 2030 goal."
        ),
    },
]


# ---------------------------------------------------------------------------
# Apply / revert
# ---------------------------------------------------------------------------

def load_manifest() -> list[dict]:
    return [json.loads(line) for line in MANIFEST.read_text().splitlines() if line.strip()]


def write_manifest(rows: list[dict]) -> None:
    MANIFEST.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def doc_path(manifest_by_id: dict, doc_id: str) -> Path | None:
    row = manifest_by_id.get(doc_id)
    if not row or not row.get("file_path"):
        return None
    return KB / row["file_path"]


def apply(dry_run: bool) -> int:
    rows = load_manifest()
    by_id = {r["doc_id"]: r for r in rows}
    backup: dict = {"documents": {}, "manifest_added": [], "audit_date": AUDIT_DATE}
    changed = skipped = failed = 0

    for fix in FIXES:
        doc_id = fix["doc_id"]
        path = doc_path(by_id, doc_id)
        if path is None or not path.exists():
            print(f"  SKIP  {doc_id}: no file on disk")
            skipped += 1
            continue
        raw = path.read_text()
        doc = json.loads(raw)
        # Compare on a sorted dump (key ORDER is not a change) but back up the
        # RAW TEXT. Backing up a sorted dump means --revert rewrites every file
        # with reordered keys, turning a three-line edit into a whole-file diff.
        original = json.dumps(doc, ensure_ascii=False, sort_keys=True)

        try:
            for op in fix["ops"]:
                op(doc, doc_id)
        except KeyError as e:
            if fix.get("optional"):
                print(f"  skip  {doc_id}: {e}")
                skipped += 1
                continue
            print(f"  FAIL  {e}")
            failed += 1
            continue

        if fix.get("key_facts"):
            kf = dict(doc.get("key_facts") or {})
            kf.update(fix["key_facts"])
            doc["key_facts"] = kf
        for gone in fix.get("key_facts_remove", []):
            if isinstance(doc.get("key_facts"), dict):
                doc["key_facts"].pop(gone, None)

        doc["last_scraped"] = AUDIT_DATE
        if json.dumps(doc, ensure_ascii=False, sort_keys=True) == original:
            print(f"  same  {doc_id}: already applied")
            skipped += 1
            continue

        print(f"  FIX   {doc_id}  — {fix['why'].splitlines()[0][:78]}")
        if not dry_run:
            backup["documents"][doc_id] = raw
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        changed += 1

    for new in NEW_DOCS:
        doc_id = new["doc_id"]
        if doc_id in by_id:
            print(f"  same  {doc_id}: already in the manifest")
            skipped += 1
            continue
        print(f"  NEW   {doc_id}  -> {new['file_path']}")
        if not dry_run:
            target = KB / new["file_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            body = {k: v for k, v in new.items() if k != "file_path"}
            target.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n")
            row = {k: new[k] for k in (
                "doc_id", "title", "category", "subcategory", "display_label",
                "source_url", "procedure_url", "playwright_verified", "file_path") if k in new}
            rows.append(row)
            backup["manifest_added"].append(doc_id)
        changed += 1

    if dry_run:
        print(f"\nDRY RUN — {changed} would change, {skipped} skipped, {failed} failed.")
        print("Nothing was written. Drop --dry-run to apply.")
        return 1 if failed else 0

    write_manifest(rows)
    BACKUP.write_text(json.dumps(backup, ensure_ascii=False, indent=2) + "\n")
    print(f"\napplied {changed}, skipped {skipped}, failed {failed}")
    print(f"backup written to {BACKUP.relative_to(ROOT)} — revert with --revert")
    return 1 if failed else 0


def revert() -> int:
    if not BACKUP.exists():
        sys.exit(f"No backup at {BACKUP}; nothing to revert.")
    backup = json.loads(BACKUP.read_text())
    rows = load_manifest()
    by_id = {r["doc_id"]: r for r in rows}

    for doc_id, original in backup["documents"].items():
        path = doc_path(by_id, doc_id)
        if path is None:
            print(f"  skip  {doc_id}: not in the manifest")
            continue
        # `original` is the raw file text, so this restores the file byte for
        # byte — key order included.
        path.write_text(original if isinstance(original, str)
                        else json.dumps(original, ensure_ascii=False, indent=2) + "\n")
        print(f"  restored  {doc_id}")

    added = set(backup.get("manifest_added", []))
    if added:
        for doc_id in added:
            row = by_id.get(doc_id)
            if row and row.get("file_path"):
                target = KB / row["file_path"]
                if target.exists():
                    target.unlink()
                    print(f"  removed   {row['file_path']}")
        write_manifest([r for r in rows if r["doc_id"] not in added])

    BACKUP.unlink()
    print("\nreverted.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()
    if args.revert:
        return revert()
    return apply(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
