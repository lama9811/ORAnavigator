# -*- coding: utf-8 -*-
"""
Task guidance catalog — Phase 4 of the "help researchers win" roadmap.
======================================================================
Each checklist task today is just a title + description. A first-time PI often
doesn't know HOW to do the task or what "good" looks like. This catalog attaches
a short how-to and (where useful) a tiny sample to known tasks, matched by
keyword against the task title — so it works for both template-seeded and
solicitation-seeded tasks WITHOUT any database change. Tasks with no match get
no guidance (the field is simply absent/None).
"""

# (keywords, {how_to, sample}). First rule whose ALL keywords appear in the
# lowercased title wins. Keep keywords specific enough not to cross-match.
_RULES = [
    (("eligibility",), {
        "how_to": "Check your appointment type and Morgan State's status against the solicitation's 'Who May Submit' / eligibility section. Use Fundability → Eligibility for a quick go/no-go. If anything is unclear, email ORA pre-award BEFORE you start writing.",
        "sample": "",
    }),
    (("budget", "justification"), {
        "how_to": "Explain WHY each budget line is needed and HOW the cost was derived. One short paragraph per category. Numbers must match the budget exactly — use Build budget → Draft justification to generate a grounded starting point, then edit.",
        "sample": "Personnel: Dr. Smith (PI), 2 summer months, will lead the study design and analysis. Salary is based on her current academic-year base, prorated, plus fringe at the federally negotiated rate.",
    }),
    (("budget",), {
        "how_to": "Use Build budget to enter personnel (salary x effort), equipment, travel, supplies, and subawards. It applies Morgan State's F&A rate to the modified base automatically and checks you against the sponsor cap.",
        "sample": "",
    }),
    (("data management",), {
        "how_to": "State the data types you'll produce, the formats/standards, where you'll store and back them up, how and when you'll share them (name a repository), and how long you'll retain them. NSF caps this at 2 pages.",
        "sample": "Data types: de-identified survey responses (CSV) and analysis code. Sharing: deposited in the Open Science Framework within 12 months of collection; retained for 5 years.",
    }),
    (("specific aims",), {
        "how_to": "One page: open with the problem and the gap, state your long-term goal and central hypothesis, then list 2-3 INDEPENDENT aims (one failing shouldn't sink the others). Close with the expected impact.",
        "sample": "Aim 1: Determine whether X regulates Y (hypothesis: X increases Y via Z). Aim 2: Test whether blocking X reduces the phenotype in vivo.",
    }),
    (("project summary",), {
        "how_to": "NSF requires three LABELED parts on one page: Overview, Intellectual Merit, and Broader Impacts. Use those exact headings — proposals missing them are returned without review.",
        "sample": "Overview: ...  Intellectual Merit: This project advances ...  Broader Impacts: The work trains ... and broadens participation by ...",
    }),
    (("broader impacts",), {
        "how_to": "Name specific societal benefits and concrete activities (education, mentoring, broadening participation) — and say how you'll MEASURE that they happened. Vague claims score poorly.",
        "sample": "We will mentor two undergraduates from the Meyerhoff program each year and assess outcomes via a validated research-skills survey.",
    }),
    (("biosketch",), {
        "how_to": "Use the sponsor's current format (NSF and NIH differ). Include the required sections and stay within the page/entry limits. Generate NSF biosketches in SciENcv to stay compliant.",
        "sample": "",
    }),
    (("current", "pending"), {
        "how_to": "List ALL active and pending support for each senior person — including this proposal — with title, sponsor, amount, your role, and person-months. Use the sponsor's current form.",
        "sample": "",
    }),
    (("facilities",), {
        "how_to": "Describe the labs, equipment, computing, and institutional resources available to the project. No dollar amounts here — this section shows you CAN do the work.",
        "sample": "The Department provides 800 sq ft of wet-lab space, a shared confocal microscope, and access to the University's HPC cluster.",
    }),
    (("letters of support",), {
        "how_to": "Request letters early (collaborators are busy). Each should be specific about what the person/organization will contribute. Follow any format the solicitation requires (some sponsors restrict content).",
        "sample": "",
    }),
    (("internal routing",), {
        "how_to": "Complete the Internal Routing Form in DocuSign and get the PI and chair signatures. ORA requires this BEFORE the proposal goes to the sponsor — start it ~5 business days out.",
        "sample": "",
    }),
    (("solicitation",), {
        "how_to": "Read the whole solicitation once, then note: deadline (date AND time + zone), eligibility, page/format limits, required attachments, and the submission portal. The Start-from-solicitation importer pulls most of these for you.",
        "sample": "",
    }),
    (("narrative",), {
        "how_to": "State your objectives/questions up front, then background & significance, then a clear, feasible approach a reviewer can evaluate, plus a timeline and how you'll know it worked. Use Drafting coach to outline it.",
        "sample": "",
    }),
    (("research strategy",), {
        "how_to": "NIH: cover Significance, Innovation, and Approach. In Approach, give methods per aim, include preliminary data, and address potential pitfalls and alternatives. Use Drafting coach for an outline.",
        "sample": "",
    }),
    (("final review", "ora"), {
        "how_to": "Send the complete package to ORA pre-award at least 5 business days before the deadline. Only the Authorized Organization Representative submits to the sponsor — not the PI.",
        "sample": "",
    }),
]


def _norm(title: str) -> str:
    return " ".join((title or "").lower().split())


def guidance_for(title: str):
    """Return {how_to, sample} for a task title, or None if nothing matches."""
    t = _norm(title)
    if not t:
        return None
    for keywords, g in _RULES:
        if all(k in t for k in keywords):
            out = {"how_to": g["how_to"]}
            if g.get("sample"):
                out["sample"] = g["sample"]
            return out
    return None
