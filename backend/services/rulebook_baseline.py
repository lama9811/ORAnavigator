"""The rules a named RULEBOOK states, held as data.

WHY THIS EXISTS
---------------
`delegated_rules` reports, honestly, that a requirement pointing at the PAPPG is
"Not ours to check" — and then the tool stops. NSF 23-598 states exactly one
Project Summary rule ("must include the LOI number in addition to all the
requirements outlined in the PAPPG"), so a five-line summary with no
Intellectual Merit or Broader Impacts statement was reported "Addressed". The
rules that would fail it live in the PAPPG.

Three attempts to read the PAPPG itself are on record as failures (see
CLAUDE.md): Chapter II whole is 598 requirements and 97 invented sections; a
scoped extraction prompt cut rows 36% without moving the noise and MERGED the
four usable Project Summary rows into two; a deterministic output filter removes
only 11%.

Research.gov publishes the answer already distilled: each section's upload page
carries a short "Content Instructions" list — the handful of rules that section
is actually checked against. Four sections, fourteen rules, half deterministic.

CURATED BY HAND, AND THAT IS FORCED, NOT LAZY
---------------------------------------------
Every source page carries `Welcome <name> | Sign Out`. They are behind NSF
login, so `kb_scraper/` can never reach them and no refresh path exists to
design for. This is the same shape as every other authoritative rule table here:
compliance_sentinel's five rules, budget_helper's F&A rates, forms_catalog.

KEYED ON THE RULEBOOK, NEVER THE FUNDER
---------------------------------------
`RULES["the PAPPG"]` is DATA. This is not the funder branch the repo forbids
(`grep -ril "eir_solicitation\\|23-598" backend/services/` must stay empty) —
what was forbidden was branching the ENGINE on one solicitation, the way
`isEirProposal()` hid the tool on 4 of 5 proposals. The engine here stays
funder-blind: it asks which rulebooks a solicitation cites and whether we hold
rules for any of them. `RULES["the NIH Grants Policy Statement"]` slots in with
no engine change.

The trigger is the CITATION, not the sponsor string — the funder's own sentence
saying "follow the PAPPG" is what earns it the PAPPG's rules. That also avoids
the bug compliance_sentinel was bitten by, where a bare substring test read
`nsf` out of "Maryland Technology Tra-nsf-er Fund".

Every row's `source` is NSF's VERBATIM sentence, so golden rule 2 holds by
construction — unlike generic_checks, which must fall back to a derived line
when the shared contract quote does not name the row.
"""

from __future__ import annotations

from typing import Optional

from services import delegated_rules

_PAPPG_URL = "https://www.nsf.gov/policies/pappg"

# Section keys must match what services/solicitation_profile.section_key()
# produces for the section's name, so a baseline row files under the same key as
# the solicitation's own rows for that part of the proposal.
PROJECT_SUMMARY = "project_summary"
PROJECT_DESCRIPTION = "project_description"
REFERENCES_CITED = "references_cited"
FACILITIES = "facilities_equipment_other_resources"

_SECTION_LABELS = {
    PROJECT_SUMMARY: "Project Summary",
    PROJECT_DESCRIPTION: "Project Description",
    REFERENCES_CITED: "References Cited",
    FACILITIES: "Facilities, Equipment and Other Resources",
}

# Order is the order Research.gov lists them, which is the order a PI meets them.
_SECTION_ORDER = [PROJECT_SUMMARY, PROJECT_DESCRIPTION, REFERENCES_CITED, FACILITIES]


def _row(id, section, label, source, why, *, kind="semantic", check=None,
         check_args=None, scored=True, flag_if_present=False,
         rulebook="the PAPPG", url=_PAPPG_URL, keywords=None) -> dict:
    row = {
        "id": id, "section": section, "label": label,
        "kind": kind, "scored": scored,
        "check": check, "check_args": check_args or {},
        "source": source, "why": why, "keywords": keywords or [],
        "rulebook": rulebook, "source_url": url,
    }
    if flag_if_present:
        row["flag_if_present"] = True
    return row


_PAPPG_RULES: list[dict] = [
    # ── Project Summary ─────────────────────────────────────────────────────
    # THE rule. Presence of the three headings is decided by CODE; whether what
    # sits under each is substantive is a separate SEMANTIC row below. Keeping
    # them apart is deliberate: this repo has shipped presence-rendered-as-
    # approval three times and unshipped it each time.
    _row("pappg_ps_headings", PROJECT_SUMMARY,
         "Overview, Intellectual Merit and Broader Impacts each on their own line",
         "Your file must include three separate section headers: Overview, "
         "Intellectual Merit, and Broader Impacts. To be valid, a heading must "
         "be on its own line with no other text on that line.",
         "NSF returns a Project Summary without these three headings without review.",
         kind="deterministic", check="rb_headings",
         check_args={"section": PROJECT_SUMMARY,
                     "headings": ["Overview", "Intellectual Merit", "Broader Impacts"]}),
    _row("pappg_ps_overview", PROJECT_SUMMARY,
         "The Overview describes the objectives and the methods",
         "Your file must include three separate section headers: Overview, "
         "Intellectual Merit, and Broader Impacts.",
         "A heading with nothing substantive under it reads to a reviewer as no answer at all."),
    _row("pappg_ps_merit", PROJECT_SUMMARY,
         "The Intellectual Merit statement addresses intellectual merit",
         "Your file must include three separate section headers: Overview, "
         "Intellectual Merit, and Broader Impacts.",
         "This is one of NSF's two review criteria; a reviewer looks for it here first."),
    _row("pappg_ps_impacts", PROJECT_SUMMARY,
         "The Broader Impacts statement addresses broader impacts",
         "Your file must include three separate section headers: Overview, "
         "Intellectual Merit, and Broader Impacts.",
         "This is NSF's second review criterion and the one most often left thin."),
    _row("pappg_ps_one_page", PROJECT_SUMMARY,
         "Project Summary fits on one page",
         "File cannot exceed one page.",
         "An over-length Project Summary is returned without review.",
         kind="deterministic", check="rb_page_limit", flag_if_present=True,
         check_args={"section": PROJECT_SUMMARY, "limit": 1}),

    # ── Project Description ─────────────────────────────────────────────────
    _row("pappg_pd_impacts_header", PROJECT_DESCRIPTION,
         "A separate Broader Impacts header on its own line",
         "Your file must include a separate section header for Broader Impacts. "
         "To be valid, a heading must be on its own line with no other text on "
         "that line.",
         "NSF requires Broader Impacts to be separately labeled, not woven into the narrative.",
         kind="deterministic", check="rb_headings",
         check_args={"section": PROJECT_DESCRIPTION,
                     "headings": ["Broader Impacts"]}),
    _row("pappg_pd_no_urls", PROJECT_DESCRIPTION,
         "No hyperlinks in the Project Description",
         "Hyperlinks (URLs) must not be used in the Project Description.",
         "A reviewer is not permitted to follow them, so anything behind one is unread.",
         kind="deterministic", check="rb_no_urls", flag_if_present=True,
         check_args={"section": PROJECT_DESCRIPTION}),
    _row("pappg_pd_page_limit", PROJECT_DESCRIPTION,
         "Project Description within its page limit",
         "Refer to your funding opportunity for page limit guidance. The system "
         "will enforce the page limit requirements listed in the funding "
         "opportunity. If the funding opportunity does not provide a specific "
         "limit, the 15-page limit stated in the PAPPG should be followed.",
         "Most funders return an over-length section without review.",
         kind="deterministic", check="rb_page_limit", flag_if_present=True,
         check_args={"section": PROJECT_DESCRIPTION, "limit": 15}),

    # ── References Cited ────────────────────────────────────────────────────
    _row("pappg_rc_scholarly", REFERENCES_CITED,
         "Citations follow accepted scholarly practice",
         "Follow accepted scholarly practices in providing citations for source materials.",
         "An incomplete reference list reads as carelessness to a reviewer in your field."),
    # scored=False: NSF's own sentence carries an exception, and a conditional
    # ask must never be counted against a compliant proposal.
    _row("pappg_rc_et_al", REFERENCES_CITED,
         "Avoid 'et al.' in the reference list",
         "References should avoid the use of et al. (except for large consortia papers).",
         "NSF asks for full author lists so reviewers can see who is involved.",
         kind="deterministic", check="rb_et_al", scored=False,
         flag_if_present=True, check_args={"section": REFERENCES_CITED}),

    # ── Facilities, Equipment and Other Resources ───────────────────────────
    _row("pappg_fe_no_financials", FACILITIES,
         "No dollar figures in Facilities, Equipment and Other Resources",
         "The section must not include any quantifiable financial information.",
         "Cost information belongs in the budget; NSF treats it as an error here.",
         kind="deterministic", check="rb_no_financials", flag_if_present=True,
         check_args={"section": FACILITIES}),
    _row("pappg_fe_narrative", FACILITIES,
         "Written as a narrative",
         "This section should be narrative in nature and include internal and "
         "external resources (both physical and personnel).",
         "A bare equipment list does not tell a reviewer the project is feasible."),
    _row("pappg_fe_coverage", FACILITIES,
         "Covers internal and external resources, physical and personnel",
         "This section should be narrative in nature and include internal and "
         "external resources (both physical and personnel).",
         "Reviewers assess feasibility from this section; omissions read as gaps."),
    _row("pappg_fe_unfunded", FACILITIES,
         "Names senior/key personnel and postdocs drawing no funds",
         "This section should include any senior/key personnel or postdoctoral "
         "scholars for whom no funds are being requested in the budget.",
         "It is the only place an unfunded contributor is visible to a reviewer."),
]

RULES: dict[str, list[dict]] = {"the PAPPG": _PAPPG_RULES}


# ── structural skeletons ────────────────────────────────────────────────────
# NOT a sample proposal and not AI-written prose about the PI's science. The
# failure this feature prevents is a SHAPE problem — a summary with no
# Intellectual Merit statement — so the shape is what to show. `sample_proposals`
# holds 19 link-only entries with no text and we never rehost, so there is
# nothing to quote from; a skeleton is the honest v1.
SKELETONS: dict[str, dict[str, dict]] = {
    "the PAPPG": {
        PROJECT_SUMMARY: {
            "title": "How a Project Summary is laid out",
            "note": "A structural example, not a real proposal. One page, three "
                    "headings, each on its own line.",
            "body": (
                "Overview\n"
                "What you will do and how. State the problem, the objectives, and "
                "the approach in a few sentences each.\n\n"
                "Intellectual Merit\n"
                "What this advances in your field, and why this team can do it. "
                "NSF reviewers score this criterion explicitly.\n\n"
                "Broader Impacts\n"
                "Who benefits beyond your field — students trained, curriculum "
                "changed, communities reached — and how you will know it happened."
            ),
        },
        PROJECT_DESCRIPTION: {
            "title": "Where Broader Impacts sits in a Project Description",
            "note": "A structural example, not a real proposal. Broader Impacts "
                    "must be its own labeled heading, not a paragraph inside the "
                    "narrative.",
            "body": (
                "Introduction and Motivation\n"
                "Results from Prior NSF Support   (if you have any)\n"
                "Research Plan\n"
                "Broader Impacts\n"
                "Timeline and Management Plan\n"
            ),
        },
    },
}


def rules_for(rulebook: str, section: Optional[str] = None) -> list[dict]:
    """Rows for `rulebook`, optionally narrowed to one section.

    An unknown rulebook returns [] rather than raising: a solicitation citing
    something we hold no rules for must behave exactly as it does today."""
    rows = RULES.get(rulebook or "", [])
    if section is None:
        return list(rows)
    return [r for r in rows if r["section"] == section]


def rulebooks_cited_by(requirements: list[dict], url: str = "") -> list[str]:
    """Which rulebooks we hold rules for does this solicitation actually cite?

    Reads the requirement rows' own `source` quotes rather than a sponsor
    string. Two reasons: the raw solicitation TEXT is not available here (it
    lives in `solicitation_sources`, keyed to the submission, not in the stored
    profile), and a citation is the grounded signal — the funder's own sentence
    saying "follow the PAPPG" is what earns it the PAPPG's rules."""
    blob = " ".join(
        str((r or {}).get("source") or "") + " " + str((r or {}).get("label") or "")
        for r in (requirements or [])
    )
    if url:
        blob = f"{blob} {url}"
    return [b["name"] for b in delegated_rules.rulebooks_in(blob) if b["name"] in RULES]


def sections_offered(rulebook: str) -> list[dict]:
    """The sections a PI can check one at a time, in Research.gov's own order."""
    have = {r["section"] for r in rules_for(rulebook)}
    return [{"key": k, "label": _SECTION_LABELS[k]}
            for k in _SECTION_ORDER if k in have]


def skeleton_for(rulebook: str, section: str) -> Optional[dict]:
    return (SKELETONS.get(rulebook or "") or {}).get(section or "")


def section_label(key: str) -> str:
    return _SECTION_LABELS.get(key, key.replace("_", " ").title())
