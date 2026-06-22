"""Sample Proposals Library -- a curated shelf of real, public example proposals.

ORA Navigator coaches the *writing* of a proposal but never showed a PI what a
fundable proposal actually looks like. This module backs the /sample-proposals
page: a hand-vetted list of links to authoritative, publicly-available sample /
funded proposals (NSF's official sample, NIH/NIAID funded applications with the
reviewers' summary statements, university example libraries, Open Grants) that a
first-time PI can open and read for reference.

Design (mirrors services/forms_catalog.py and main.DEFAULT_QUESTION_POOL):
  - The data is a static Python constant -- no DB table, no embeddings, no
    network. Every entry links OUT to the source; we never host third-party
    proposals, so there is zero copyright exposure and the links stay current.
  - Each entry is tagged with one or more `categories` (the filter buckets the
    UI shows as chips). Multi-agency university libraries carry several tags.
  - list_samples(category) is the only read the GET /api/sample-proposals
    endpoint needs; it never raises.

Curation note: every URL below was individually verified to resolve to real
sample-proposal content (not generic "how to write a grant" advice). Keep this
list short and editable -- it is the whole feature. When adding an entry, give
it a unique `id`, an https `url`, and at least one tag from CATEGORIES.
"""
from __future__ import annotations

from typing import Optional

# Fixed filter buckets shown as chips in the UI, in display order. An entry may
# belong to several. Keep these in sync with the `categories` values below.
CATEGORIES = ["NSF", "NIH", "Foundations", "Early-career"]

# access: "free"    -> open, no login
#         "partial" -> some content behind a free account / paywall (badge it)
SAMPLE_PROPOSALS: list[dict] = [
    # --- Funder-published samples ------------------------------------------
    {
        "id": "nsf-official-sample",
        "title": "NSF Official Sample Proposal (Cultural Anthropology)",
        "source": "National Science Foundation",
        "url": "https://www.nsf.gov/sbe/bcs/sample-proposal",
        "categories": ["NSF"],
        "kind": "Full proposal + original & revised budgets + 3 peer-review summaries",
        "access": "free",
        "why": "A rare official, end-to-end NSF example — the proposal, the "
               "budget, and the actual reviewer summaries that scored it.",
    },
    {
        "id": "niaid-sample-applications",
        "title": "NIH / NIAID Sample Applications & More",
        "source": "National Institute of Allergy and Infectious Diseases (NIH)",
        "url": "https://www.niaid.nih.gov/grants-contracts/sample-applications",
        "categories": ["NIH", "Early-career"],
        "kind": "Full funded apps — R01, R21, K-series, F31 fellowships, SBIR "
                "— most paired with the reviewers' summary statements",
        "access": "free",
        "why": "The best federal source: complete applications PLUS the written "
               "critiques, so you see both the proposal and what made it fundable.",
    },
    {
        "id": "nsf-hbcu-eir",
        "title": "NSF HBCU Excellence in Research (HBCU-EiR)",
        "source": "National Science Foundation",
        "url": "https://www.nsf.gov/funding/opportunities/hbcu-eir-historically-black-colleges-universities-excellence-research",
        "categories": ["NSF", "Early-career"],
        "kind": "Solicitation to model against (for faculty building a research program)",
        "access": "free",
        "why": "The NSF program written for HBCU faculty starting or rebuilding a "
               "research program — the solicitation your proposal should answer.",
    },

    # --- University example libraries (multi-agency) -----------------------
    {
        "id": "usf-proposal-samples",
        "title": "USF — Proposal Samples (Arts & Sciences)",
        "source": "University of South Florida",
        "url": "https://www.usf.edu/arts-sciences/research-scholarship/proposal-tools/proposal-samples.aspx",
        "categories": ["NSF", "NIH"],
        "kind": "Full proposals (NSF CAREER/EAGER/RAPID, NIH R01/R21, NASA, NEH) "
                "plus Data Management Plans, biosketches, and budget samples",
        "access": "free",
        "why": "One of the richest collections — covers many agencies and the "
               "individual components first-timers struggle with, all in one place.",
    },
    {
        "id": "uaf-sample-funded-proposals",
        "title": "UA Fairbanks — Sample Funded Proposals",
        "source": "University of Alaska Fairbanks",
        "url": "https://www.uaf.edu/ogca/lifecycle/3-develop/sample-funded-proposals.php",
        "categories": ["NSF", "NIH", "Foundations"],
        "kind": "Full funded proposals + summary statements across NSF, NIH, NEH, "
                "USDA, Dept. of Education, and foundations",
        "access": "free",
        "why": "A broad, multi-agency, multi-discipline hub assembled by a real "
               "research office — a good first place to browse.",
    },
    {
        "id": "northwestern-annotated-samples",
        "title": "Northwestern — Annotated Sample Grant Proposals",
        "source": "Northwestern University",
        "url": "https://undergradresearch.northwestern.edu/advising/sample-grant-proposals/",
        "categories": ["NSF", "NIH"],
        "kind": "Full funded proposals tagged by discipline AND research method "
                "(lab, fieldwork, archival, computational)",
        "access": "free",
        "why": "Annotated and method-filtered — find a model that matches HOW you "
               "do research, not just your field.",
    },
    {
        "id": "uchicago-sample-proposals",
        "title": "UChicago — Sample Proposals",
        "source": "University of Chicago Research Administration",
        "url": "https://ura.uchicago.edu/resource-library/sample-proposals",
        "categories": ["NSF", "NIH"],
        "kind": "Agency-by-agency index of full proposals — NIH (R01/R21/K/F31-"
                "Diversity/SBIR), NSF, NEH, USDA, 20+ Dept. of Education",
        "access": "free",
        "why": "A clean, well-organized index including a diversity-fellowship "
               "example that's relevant to early-career PIs.",
    },
    {
        "id": "serc-carleton-nsf",
        "title": "Carleton / SERC — Successful NSF Grant Proposals",
        "source": "Carleton College (SERC)",
        "url": "https://serc.carleton.edu/NAGTWorkshops/earlycareer/research/NSFgrants.html",
        "categories": ["NSF", "Early-career"],
        "kind": "Full funded NSF proposals — CAREER, research, REU, MRI, IUSE "
                "— concentrated in geosciences & STEM education",
        "access": "free",
        "why": "Built specifically for early-career faculty; the strongest source "
               "if you're preparing an NSF CAREER award.",
    },

    # --- Foundations & general ---------------------------------------------
    {
        "id": "open-grants",
        "title": "Open Grants — Shared Proposals Database",
        "source": "ogrants.org",
        "url": "https://www.ogrants.org/grants-01-all",
        "categories": ["NSF", "NIH", "Foundations"],
        "kind": "~300 researcher-shared proposals (NSF, NIH, NASA, USDA, Sloan, "
                "Moore, Wellcome, CZI) — both funded AND unfunded examples",
        "access": "free",
        "why": "The largest single open catalog of real proposals across funders; "
               "the unfunded entries are instructive in their own right.",
    },
    {
        "id": "candid-sample-documents",
        "title": "Candid — Sample Documents",
        "source": "Candid (formerly Foundation Center)",
        "url": "https://learning.candid.org/page/sample-documents",
        "categories": ["Foundations"],
        "kind": "Winning foundation/nonprofit proposals, letters of inquiry, cover "
                "letters, and proposal budgets",
        "access": "partial",
        "why": "The go-to for FOUNDATION (non-federal) proposal style and LOIs — "
               "verify what's free before relying on it.",
    },

    # --- Morgan State / early-career ---------------------------------------
    {
        "id": "morgan-ora-early-career",
        "title": "Morgan State ORA — NSF for Early-Career Researchers",
        "source": "Morgan State University, Office of Research Administration",
        "url": "https://www.morgan.edu/Documents/ADMINISTRATION/OFFICES/ora/2023%20Faculty%20Development%20Seminars/2024-02-15_PRISSEM_Early%20Career%20Morgan%20State.pdf",
        "categories": ["Early-career"],
        "kind": "Morgan's own faculty-development seminar on NSF early-career funding",
        "access": "free",
        "why": "ORA's own guidance, tailored to Morgan faculty starting out — the "
               "most institution-specific reference here.",
    },
]


def list_samples(category: Optional[str] = None) -> list[dict]:
    """Return every sample proposal, or only those tagged with `category`.

    An empty / None / unknown category returns the full list (the page's "All"
    chip; an unknown value means a malformed request, so we degrade to "all"
    rather than show an empty page). Never raises -- the data is a static
    constant. Returns shallow copies so callers can't mutate the module-level
    list.
    """
    if not category or category not in CATEGORIES:
        return [dict(s) for s in SAMPLE_PROPOSALS]
    return [dict(s) for s in SAMPLE_PROPOSALS if category in s["categories"]]


def categories() -> list[str]:
    """The filter buckets, in display order."""
    return list(CATEGORIES)
