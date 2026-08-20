#!/usr/bin/env python3
"""Turn the UNREVIEWED PAPPG extraction into the REVIEWED rule table the app loads.

Offline. Never on a request path. Run it, read the diff, commit the output:

    python3 kb_pappg/build_rules.py            # writes the reviewed table
    python3 kb_pappg/build_rules.py --report   # prints every decision, changes nothing

WHY A SCRIPT AND NOT A HAND-EDITED FILE
---------------------------------------
`_pappg_24_1_extracted_review.json` is model output and says so on its face
("UNREVIEWED -- do not paste into RULES until a human has read it"). Golden rule
4 says a human reads it before it reaches a PI. A human did; this file is what
they decided, written down per row rather than applied silently with a text
editor. Re-running it reproduces the reviewed table from the extraction, so the
review is auditable and a re-extraction can be re-reviewed instead of re-typed.

THE FIVE PASSES, AND WHY EACH ONE EXISTS
----------------------------------------
1. QUOTE GATE. Every row's `source` must appear verbatim in its own PAPPG slice.
   Measured at 161/161 before any of this, so it is a regression guard rather
   than a filter -- but golden rule 2 is the reason the table is trustworthy at
   all, and a future re-extraction is exactly when it would silently stop being.

2. DUPLICATE OF A CURATED ROW. The 14 curated rules come from Research.gov's own
   distillation and half of them carry DETERMINISTIC checks. Where the PAPPG
   restates one, the curated row wins and the extracted row is dropped: adding
   both would put one rule on screen twice, once decided by code and once as a
   model opinion, free to disagree in front of the PI. The extraction's merged
   "Include overview, intellectual merit, and broader impacts in Project Summary"
   is the exact failure CLAUDE.md records from the earlier whole-chapter attempt
   -- four usable rows collapsed into one useless one -- and it is dropped here
   in favour of the curated deterministic heading check plus three separate
   content rows.

3. WITHIN-SECTION DUPLICATE. Two rows quoting the same sentence and saying the
   same thing. NOT every shared quote is a duplicate: `_SYSTEM` rule 4 splits
   compound sentences deliberately, so "authors in publication sequence" and
   "complete publication metadata" are two real rules off one sentence and both
   stay. Token overlap proposes; a human decides.

4. PLACEMENT. The PAPPG states a rule WHERE THE SITUATION ARISES; Research.gov
   states it WHERE YOU NEED IT. So the Budget section is where NSF says unfunded
   senior personnel get described in Facilities, and the Project Description is
   where NSF says collaboration letters go in Supplementary Documents. Filing a
   rule under the slice it was read from would put it in a section it does not
   govern, where it can never be located. This is the one thing "the section is
   an INPUT" does not solve, and CLAUDE.md predicted it before it was seen.

5. UNVERIFIABLE FROM TEXT. A margin, a font, a Research.gov form field, a
   SciENcv-generated certification and a page count are not properties of pasted
   text. Left alone they would every one report `not_found` against a fully
   compliant draft -- presence-rendered-as-verdict, the error this repo has
   unshipped three times. They keep their row, keep their quote, and report
   `not_checked` with a note naming the tool that DOES handle them, which is
   exactly what `could_not_locate` and `not_in_draft` already do elsewhere.
   Marked here as DATA rather than by widening `services/draft_scope.py`: that
   module's own docstring says over-excluding is its dangerous direction, and a
   regex broad enough to catch "Maintain one-inch margins" would start dropping
   real content requirements out of real solicitations.

WHAT IS DELIBERATELY *NOT* DONE
-------------------------------
The curated rows' own `source` quotes are NOT rewritten, with three exceptions.
Research.gov's wording is NSF's wording and is correctly attributed, so swapping
it for the PAPPG's is churn against the only yardstick `test_pappg_recall.py`
has. The exceptions are the three Project Summary content rows whose `source`
today reads "Derived from ... NSF's own wording is in the PAPPG" -- the module
docstring names that as a known gap, and the PAPPG slice closes it with the real
sentence. Those three are upgraded and nothing else is.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

SRC = os.path.join(ROOT, "backend", "kb_structured", "_pappg_24_1_extracted_review.json")
SLICES = os.path.join(ROOT, "backend", "kb_structured", "_pappg_24_1_sections.json")
OUT = os.path.join(ROOT, "backend", "kb_structured", "_pappg_24_1_rules.json")

PROJECT_SUMMARY = "project_summary"
PROJECT_DESCRIPTION = "project_description"
REFERENCES_CITED = "references_cited"
FACILITIES = "facilities_equipment_and_other_resources"
BUDGET = "budget_and_budget_justification"
SENIOR_KEY = "senior_key_personnel_documents"
SPECIAL = "special_information_and_supplementary_documentation"

# Where a rule a pasted draft cannot carry is actually handled. The note a PI
# reads is built from this, so it must name a real tool, never "elsewhere".
BY_PDF = "the formatting of the PDF you upload — check it there, not in the text"
BY_COVER = "the Research.gov cover sheet, not a document you write"
BY_SCIENCV = "SciENcv, which generates and formats these documents for you"
BY_BUDGET_FORM = "the Research.gov budget form and the Budget Helper"
BY_PAGES = "the page count of the file you upload — a paste has no pages"

# ── THE DECISIONS ───────────────────────────────────────────────────────────
# Keyed (section, label) exactly as the extraction wrote them. Anything absent
# is KEPT AND SCORED, so a new row from a re-extraction is never silently
# dropped -- it shows up in --report as an undecided keep, for a human to look at.
DROP = {}          # (section, label) -> why
MOVE = {}          # (section, label) -> new section
UNVERIFIABLE = {}  # (section, label) -> who handles it
CONDITIONAL = set()  # (section, label) -> scored: False


def _drop(section, label, why):
    DROP[(section, label)] = why


def _move(section, label, to):
    MOVE[(section, label)] = to


def _unver(section, label, by):
    UNVERIFIABLE[(section, label)] = by


def _cond(section, label):
    CONDITIONAL.add((section, label))


# ── Project Summary ─────────────────────────────────────────────────────────
_drop(PROJECT_SUMMARY, "Limit Project Summary length to one page",
      "duplicate of curated pappg_ps_one_page, which enforces it deterministically")
_drop(PROJECT_SUMMARY, "Include overview, intellectual merit, and broader impacts in Project Summary",
      "MERGES three rules into one. Curated pappg_ps_headings decides the headings "
      "in code and pappg_ps_overview/_merit/_impacts judge each statement separately. "
      "This is the exact merge failure CLAUDE.md records from the whole-chapter attempt.")
_drop(PROJECT_SUMMARY, "Include activity description, objectives, and methods in overview",
      "duplicate of curated pappg_ps_overview — its quote is promoted onto that row instead")
_drop(PROJECT_SUMMARY, "Describe potential to advance knowledge in intellectual merit statement",
      "duplicate of curated pappg_ps_merit — its quote is promoted onto that row instead")
_drop(PROJECT_SUMMARY, "Describe societal benefits in broader impacts statement",
      "duplicate of curated pappg_ps_impacts — its quote is promoted onto that row instead")

# The three curated rows whose source today says "Derived from ... NSF's own
# wording is in the PAPPG". It is in the PAPPG, and this is it.
SOURCE_UPGRADES = {
    "pappg_ps_overview":
        "The overview includes a description of the activity that would result if "
        "the proposal were funded and a statement of objectives and methods to be employed.",
    "pappg_ps_merit":
        "The statement on intellectual merit should describe the potential of the "
        "proposed activity to advance knowledge.",
    "pappg_ps_impacts":
        "The statement on broader impacts should describe the potential of the "
        "proposed activity to benefit society and contribute to the achievement of "
        "specific, desired societal outcomes.",
}

# ── Project Description ─────────────────────────────────────────────────────
_drop(PROJECT_DESCRIPTION, "Include Broader Impacts heading on its own line within Project Description",
      "duplicate of curated pappg_pd_impacts_header, which decides it in code")
_drop(PROJECT_DESCRIPTION, "Limit Project Description to 15 pages",
      "curated pappg_pd_page_limit is strictly better: it DEFERS to the solicitation's "
      "own limit and is suppressed when the solicitation states one. A hard 15 would "
      "contradict any solicitation that sets a different limit.")
_drop(PROJECT_DESCRIPTION, "Do not exceed 15-page limit for group proposals without prior deviation",
      "same rule as the 15-page row above, in its conditional form")
_drop(PROJECT_DESCRIPTION, "Do not include URLs in Project Description",
      "duplicate of curated pappg_pd_no_urls, which decides it in code")
_drop(PROJECT_DESCRIPTION, "Ensure Project Description is self-contained without external site links",
      "same sentence and same rule as the URL row above")
_move(PROJECT_DESCRIPTION, "Describe unfunded substantial collaborations in Facilities section", FACILITIES)
_move(PROJECT_DESCRIPTION, "Provide collaboration letters in supplementary documentation", SPECIAL)
_move(PROJECT_DESCRIPTION, "Describe plans for data management in Special Information and Supplementary Docs", SPECIAL)
_unver(PROJECT_DESCRIPTION, "Limit Results from Prior NSF Support to maximum five pages", BY_PAGES)
for _l in ("Justify foreign entity or international branch campus funding",
           "Describe relation of completed work to proposed work for renewal proposals",
           "Describe goals and broader impacts if prior award has no new results",
           "Provide prior support report for most closely related award if multiple exist"):
    _cond(PROJECT_DESCRIPTION, _l)

# ── References Cited ────────────────────────────────────────────────────────
_drop(REFERENCES_CITED, "Follow accepted scholarly practices for source citations",
      "duplicate of curated pappg_rc_scholarly")
_drop(REFERENCES_CITED, "Exclude parenthetical narrative text from references section",
      "its clause is already carried by 'Restrict References Cited to bibliographic "
      "citations only', which quotes the whole sentence")
_cond(REFERENCES_CITED, "Include optional website URLs when available")

# ── Facilities, Equipment and Other Resources ───────────────────────────────
_drop(FACILITIES, "Include aggregated internal and external resources",
      "duplicate of curated pappg_fe_coverage")
_drop(FACILITIES, "Include both physical and personnel resources from organization and collaborators",
      "same sentence and same rule as the aggregated-resources row above; both "
      "restate curated pappg_fe_coverage")
_drop(FACILITIES, "Write Facilities section as narrative without financial info",
      "two curated rules already cover it: pappg_fe_narrative, and pappg_fe_no_financials "
      "which finds dollar figures in code")
_drop(FACILITIES, "Do not include quantifiable financial information in Facilities section",
      "duplicate of curated pappg_fe_no_financials, which decides it in code")

# ── Budget and Budget Justification ─────────────────────────────────────────
# The rule NSF states in the Budget section but which governs Facilities. This
# is the cross-section case CLAUDE.md predicted before it was ever seen.
_move(BUDGET, "Describe unfunded senior personnel in Facilities section", FACILITIES)
_move(BUDGET, "Include subaward work description in Project Description", PROJECT_DESCRIPTION)
for _l, _by in (
    ("Include annual budget", BY_BUDGET_FORM),
    ("Limit budget justification page count", BY_PAGES),
    ("Limit subaward budget justification page count", BY_PAGES),
    ("Limit collaborative proposal budget justification page count", BY_PAGES),
    ("Remove unsalaried senior personnel from budget Section A", BY_BUDGET_FORM),
    ("Include foreign travel destinations and dates on budget", BY_BUDGET_FORM),
    ("Specify participant count on budget", BY_BUDGET_FORM),
    ("Enter total direct costs on Line H", BY_BUDGET_FORM),
    ("Enter total direct and indirect costs on Line J", BY_BUDGET_FORM),
    ("Enter cumulative mandatory cost sharing on Year 1 budget Line M", BY_BUDGET_FORM),
):
    _unver(BUDGET, _l, _by)
for _l in ("Provide separate budget and justification for subrecipients",
           "Itemize and submit confidential senior personnel salary statement",
           "Provide explanation of mandatory cost sharing in budget justification",
           "Do not exceed mandatory cost sharing level specified in solicitation",
           "Do not count other Federal funds toward NSF cost sharing requirements",
           "Disclose and justify special purpose computing equipment",
           "Justify non-standard participant support costs"):
    _cond(BUDGET, _l)

# ── Senior/Key Personnel Documents ──────────────────────────────────────────
# Biosketches and current-and-pending are GENERATED by SciENcv and uploaded as
# their own PDFs. A PI pasting their package may well include them, so the
# CONTENT rules stay judgeable; what cannot be judged from text is how the tool
# formats them, what a form field's character limit is, and whether a
# certification NSF's own system inserts is present.
_drop(SENIOR_KEY, "Do not include personal information in biographical sketch",
      "duplicate of 'Omit personal information from biographical sketch' — same sentence")
for _l, _by in (
    ("Provide separate biographical sketch for each senior/key person via SciENcv", BY_SCIENCV),
    ("Provide separate current and pending support for each senior/key person via SciENcv", BY_SCIENCV),
    ("Include required biographical certification language", BY_SCIENCV),
    ("Include required certification in current and pending support", BY_SCIENCV),
    ("Ensure biographical sketch and support signatures are dated within 12 months", BY_SCIENCV),
    ("Limit overall objectives field in proposals/active projects to 1500 characters", BY_SCIENCV),
    ("Limit overall objectives field in in-kind contributions to 1500 characters", BY_SCIENCV),
    ("Enter N/A for non-applicable biographical sketch product citation fields", BY_SCIENCV),
    ("Provide Collaborators & Other Affiliations information using the designated template", BY_SCIENCV),
    ("Do not alter COA template content, format, column sizes, or font type", BY_SCIENCV),
    ("Complete all five tables of the Collaborators & Other Affiliations template", BY_SCIENCV),
    ("Upload other personnel biographical information as a single PDF in Other Supplementary Documents", BY_PDF),
    ("Upload equipment proposal biographical information as a single PDF", BY_PDF),
    ("Provide a maximum one-page document of up to five synergistic activities per senior/key person", BY_PAGES),
    ("Do not participate in a malign foreign talent recruitment program", BY_COVER),
):
    _unver(SENIOR_KEY, _l, _by)
for _l in ("Provide required biographical info and publication list for equipment proposal auxiliary users",
           "Identify equipment proposal biographical information clearly",
           "Convert foreign currency amounts to US dollars rounded to nearest dollar",
           "Disclose in-kind contributions valued at $5000 or more requiring time commitment",
           "Disclose foreign government-sponsored contracts and activities in support document",
           "Disclose applicable consulting activities under proposals and active projects"):
    _cond(SENIOR_KEY, _l)

# ── Special Information and Supplementary Documentation ─────────────────────
for _l, _by in (
    ("Observe One-Page Limit for Mentoring Plan", BY_PAGES),
    ("Limit Data Management and Sharing Plan to Two Pages", BY_PAGES),
    ("Upload Mentoring Plan under specified section in Research.gov", BY_COVER),
    ("Upload Data Management Plan under specified supplementary section", BY_COVER),
):
    _unver(SPECIAL, _l, _by)
for _l in ("Include Mentoring Plan for Postdocs or Graduate Students",
           "Submit Single Unified Data Management Plan for Collaborative Projects",
           "Submit a single unified mentoring plan for collaborative projects",
           "Justify Data Management Plan Statement When No Data Is Produced",
           "Include Logistical Plan for Antarctic Proposals",
           "Obtain Tribal Permission for Research Impacting Tribal Interests",
           "Alert NSF to Proprietary, Privacy, or Security Circumstances",
           "Discuss collaborative data issues within joint Data Management Plan"):
    _cond(SPECIAL, _l)

# ── Format of the Proposal, Cover Sheet ─────────────────────────────────────
# Whole sections. Every rule is a property of the uploaded PDF or of a
# Research.gov form field, and none of them is visible in pasted text.
WHOLE_SECTION_UNVERIFIABLE = {
    "format_of_the_proposal": BY_PDF,
    "cover_sheet": BY_COVER,
}

# Rows the PAPPG states in one section but which govern another, AND which a
# curated row already covers once moved. Dropped at the destination.
_drop(FACILITIES, "Describe unfunded senior personnel in Facilities section",
      "arrives from the Budget slice and duplicates curated pappg_fe_unfunded")
_drop(SPECIAL, "Describe plans for data management in Special Information and Supplementary Docs",
      "arrives from the Project Description slice and duplicates "
      "'Include Data Management and Sharing Plan'")


# ── PASS 6: prohibitions ────────────────────────────────────────────────────
#
# FOUND BY RUNNING THE APP. A live Section Check of a clean Budget Justification
# put "Do not request NSF funds for alcoholic beverages" in the FIX LIST. The
# draft never mentions alcohol — that IS compliance — and nine of the twenty-three
# items the PI was told to fix were rules the draft already obeyed.
#
# Absence means PASS for a prohibition and FAIL for a content rule. The semantic
# reviewer only had the second vocabulary; the engine already had the first
# (`clear`/`flagged`, both in _CREDIT) but only deterministic rows could reach it.
#
# Matched on the LABEL's opening verb, applied offline, and the result is written
# into the committed table one row at a time — so this is a proposal a human
# reads in the diff, not a rule applied live to a solicitation's own text. All 19
# it matches were checked by hand. The mirror risk is the dangerous one: marking
# a CONTENT rule as a prohibition makes a draft that omits it come back `clear`,
# reporting a missing requirement as compliance. A test names rules that must
# never be caught here.
_PROHIBITION = re.compile(
    r"^(do not|don't|never|prohibit|omit|exclude|restrict|avoid)\b", re.I)


def build(report_only: bool = False) -> dict:
    from services.text_match import quote_in

    slices = {s["section_key"]: s for s in json.load(open(SLICES, encoding="utf-8"))["sections"]}
    src = json.load(open(SRC, encoding="utf-8"))

    rows, log, unquoted = [], [], []
    for sec in src["sections"]:
        key = sec["section_key"]
        whole = WHOLE_SECTION_UNVERIFIABLE.get(key)
        for r in sec.get("rows") or []:
            label = r["label"]

            # PASS 1 -- the quote gate, on the slice the row was READ from,
            # before any move relabels it.
            if not quote_in(slices[key]["text"], r["source"], drop_list_noise=True):
                unquoted.append((key, label))
                log.append({"section": key, "label": label, "decision": "dropped",
                            "why": "source quote is not verbatim in its PAPPG slice"})
                continue

            # PASS 4 -- placement, before the drop lookup, so a row can be moved
            # INTO a section and dropped there as a duplicate of a curated rule.
            dest = MOVE.get((key, label), key)
            moved_from = key if dest != key else None

            # PASS 2 + 3 -- duplicates, checked at the destination.
            why = DROP.get((dest, label)) or DROP.get((key, label))
            if why:
                log.append({"section": key, "label": label, "decision": "dropped",
                            "moved_to": dest if moved_from else None, "why": why})
                continue

            row = dict(r)
            row["section"] = dest
            row["section_label"] = _LABELS[dest]
            if moved_from:
                # Regenerate rather than string-patch: the id's section prefix is
                # a SLUG of the key, not the key ("budget_and_budget_justification"
                # -> "budget_budget_justification"), so a replace() on the key
                # silently matches nothing and the row keeps an id that reads as
                # belonging to the section it just left.
                from services.pappg_ingest import _pappg_id
                row["id"] = _pappg_id(dest, label)

            # PASS 5 -- unverifiable from text.
            by = whole or UNVERIFIABLE.get((dest, label)) or UNVERIFIABLE.get((key, label))
            if by:
                row["kind"] = "deterministic"
                row["check"] = "rb_not_in_text"
                row["check_args"] = {"section": dest, "handled_by": by}
                row["review"] = "unverifiable_from_text"
            elif (dest, label) in CONDITIONAL or (key, label) in CONDITIONAL:
                row["scored"] = False
                row["review"] = "conditional"
            elif moved_from:
                row["review"] = f"moved_from:{moved_from}"
            else:
                row["review"] = "kept"

            # PASS 6, and INDEPENDENT of the chain above: a conditional rule can
            # be a prohibition too, and a moved one certainly can. Only rows a
            # paste cannot be judged against are exempt -- their status is
            # decided by rb_not_in_text before any of this matters.
            if row.get("check") != "rb_not_in_text" and _PROHIBITION.match(label):
                row["flag_if_present"] = True

            if moved_from:
                row["moved_from"] = moved_from
            rows.append(row)
            log.append({"section": key, "label": label, "decision": row["review"],
                        "moved_to": dest if moved_from else None})

    return {"rows": rows, "log": log, "unquoted": unquoted}


_LABELS = {
    PROJECT_SUMMARY: "Project Summary",
    PROJECT_DESCRIPTION: "Project Description",
    REFERENCES_CITED: "References Cited",
    FACILITIES: "Facilities, Equipment and Other Resources",
    BUDGET: "Budget and Budget Justification",
    SENIOR_KEY: "Senior/Key Personnel Documents",
    SPECIAL: "Special Information and Supplementary Documentation",
    "format_of_the_proposal": "Format of the Proposal",
    "cover_sheet": "Cover Sheet",
    "table_of_contents": "Table of Contents",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    built = build()
    rows, log = built["rows"], built["log"]

    import collections
    by_sec = collections.Counter(r["section"] for r in rows)
    by_dec = collections.Counter(e["decision"] for e in log)

    print(f"{'section':<52}{'rules':>6}{'scored':>8}{'not_checked':>13}")
    print("-" * 79)
    for k in _SECTION_ORDER_OUT:
        if not by_sec.get(k):
            continue
        sec_rows = [r for r in rows if r["section"] == k]
        nc = sum(1 for r in sec_rows if r.get("check") == "rb_not_in_text")
        sc = sum(1 for r in sec_rows if r.get("scored") and r.get("check") != "rb_not_in_text")
        print(f"  {k:<50}{len(sec_rows):>6}{sc:>8}{nc:>13}")
    print("-" * 79)
    nc = sum(1 for r in rows if r.get("check") == "rb_not_in_text")
    sc = sum(1 for r in rows if r.get("scored") and r.get("check") != "rb_not_in_text")
    print(f"  {'TOTAL':<50}{len(rows):>6}{sc:>8}{nc:>13}")
    proh = [r["label"] for r in rows if r.get("flag_if_present")]
    print(f"\nprohibitions (absence = compliance): {len(proh)}")
    for p in proh:
        print(f"   {p}")
    print(f"\ndecisions: {dict(by_dec)}")
    if built["unquoted"]:
        print(f"\nDROPPED, QUOTE NOT VERBATIM ({len(built['unquoted'])}):")
        for s, l in built["unquoted"]:
            print(f"   {s}: {l}")

    if args.report:
        print("\n--- every decision ---")
        for e in log:
            extra = f"  -> {e['moved_to']}" if e.get("moved_to") else ""
            print(f"  [{e['decision']:<22}] {e['section']}: {e['label']}{extra}")
            if e.get("why"):
                print(f"       why: {e['why']}")
        return

    payload = {
        "pappg_version": "NSF 24-1",
        "source_url": "https://www.nsf.gov/policies/pappg/24-1/ch-2-proposal-preparation",
        "generated_by": "kb_pappg/build_rules.py",
        "review_status": "REVIEWED — every row carries the decision that kept it",
        "extracted_from": os.path.basename(SRC),
        "source_upgrades": SOURCE_UPGRADES,
        "rules": rows,
        "decisions": log,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print(f"\nwrote {args.out}")


_SECTION_ORDER_OUT = [
    "cover_sheet", PROJECT_SUMMARY, "table_of_contents", PROJECT_DESCRIPTION,
    REFERENCES_CITED, BUDGET, FACILITIES, SENIOR_KEY, SPECIAL,
    "format_of_the_proposal",
]

if __name__ == "__main__":
    main()
