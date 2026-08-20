"""Requirements a solicitation DELEGATES to a rulebook this app never reads.

WHY THIS EXISTS, from a real proposal
-------------------------------------
NSF 23-598 states exactly one rule about the Project Summary:

    "The Project Summary must include the LOI number in addition to all the
     requirements outlined in the PAPPG."

So the whole of what Draft Review could check about a Project Summary was (a)
does a section by that name exist and (b) does it contain the LOI number. A
five-line summary with no Intellectual Merit or Broader Impacts statement came
back "Addressed" — and NSF would very likely return that proposal without
review. The tool had not judged the summary and approved it. It was never given
the rule, because the rule lives in the PAPPG.

`solicitation_requirements._SYSTEM` rule 6 is what puts these rows in the list
at all: "when the text imposes a requirement by pointing elsewhere, return the
pointer itself as a requirement, quoted. Never invent what the referenced
document says." That is the right call — inventing the PAPPG's contents would
break golden rule 2 — but it means the list contains rows nothing can assess,
and until now they were rendered exactly like rows that had been.

DETERMINISTIC, AND RECOMPUTED AT REVIEW TIME
--------------------------------------------
No model judgement anywhere here (golden rule 1): a delegated row is decided by
matching the funder's own sentence against a table of named rulebooks. It is
applied in `draft_review`, NOT stored on the extracted requirement, for the same
reason `compliance_sentinel` recomputes its verdicts on every load: improving
this detector then retroactively fixes every proposal already in the database,
including the ones whose requirements were extracted before it existed.

TWO SHAPES, AND CONFLATING THEM DELETES A REAL FINDING
------------------------------------------------------
POINTER-ONLY  "Adhere to PAPPG guidelines" — the entire ask is "follow that
              document". Unassessable, so it must not read as a pass and must
              not sit in the score's denominator.
RIDER         "Include the LOI number ... in addition to all the requirements
              outlined in the PAPPG" — a concrete ask that also cites a
              rulebook. It IS checkable, it WAS checked, and it came back
              not_found. It keeps its status and only gains a note.

Failure is deliberately SAFE in both directions: a rulebook we do not recognise
leaves the row exactly as it is today (no finding lost, no score moved), and a
pointer-only row we fail to recognise stays scored rather than vanishing.
"""

from __future__ import annotations

import re
from typing import Optional

# Named rulebooks, in the words funders actually use. `name` is rendered to the
# PI verbatim, so it carries its own article ("the PAPPG", "2 CFR 200").
#
# Each carries a one-line gloss in plain English. This product is for PIs who
# have not written a proposal before: naming "the PAPPG" at someone who does not
# know what that is explains nothing, and the notice built on these names was
# read back to us as "what does this mean?". The gloss also keeps the UI honest
# about SUBJECT MATTER — the PAPPG governs how a proposal is written, while the
# Build America, Buy America Act governs what an award may buy. An earlier
# version of the notice blended every rulebook into one sentence and told a PI
# that BABA sets "section structure, length, formatting", which it does not.
# The fourth field is a URL, and it is EMPTY unless the link has actually been
# opened and checked. Telling a PI "go read the PAPPG" and handing them a 404 is
# worse than telling them to search for it themselves — and this notice exists
# precisely because the rules are somewhere they have to go and read. Add a URL
# only after visiting it.
_RULEBOOKS: list[dict] = [
    {"name": "the PAPPG", "short": "NSF's rulebook for how proposals must be written",
     "pattern": r"\bPAPPG\b|\bProposal\s+and\s+Award\s+Policies\s+and\s+Procedures\s+Guide\b",
     # Solicitations link the PAPPG in several URL forms — NSF 23-598 uses the
     # legacy `pub_summ.jsp?ods_key=pappg`, the current site uses
     # `/policies/pappg`. Matching the rulebook rather than one URL is what makes
     # "follow the link" work across both.
     "url_patterns": [r"ods_key=pappg", r"nsf\.gov/policies/pappg", r"/pappg\b"],
     "url": "https://www.nsf.gov/policies/pappg",
     "description": ("NSF's Proposal and Award Policies and Procedures Guide — the "
                     "rulebook for every NSF proposal, covering how each section must "
                     "be written and formatted. Chapter II holds the proposal "
                     "preparation instructions.")},
    {"name": "the Grants.gov Application Guide", "short": "how to fill in and upload the forms",
     "pattern": r"\bGrants\.gov\s+Application\s+Guide\b|\bGrants\.gov\s+Applicant\s+Guide\b",
     "url_patterns": [r"ods_key=grantsgovguide"],
     "url": "",
     "description": ("The form-by-form instructions for preparing and uploading the "
                     "application package through Grants.gov.")},
    {"name": "the NIH Grants Policy Statement", "short": "NIH's terms for applications and awards",
     "pattern": r"\bNIH\s+Grants\s+Policy\s+Statement\b",
     "url_patterns": [r"grants\.nih\.gov/policy.*grants?[-_]policy[-_]statement"],
     "url": "",
     "description": "NIH's standing terms and conditions for applications and awards."},
    {"name": "the SF424 Application Guide", "short": "the federal form-filling instructions",
     "pattern": r"\bSF\s?424\s*\(R&R\)?\s*(?:Application\s+Guide)?\b",
     "url_patterns": [],
     "url": "",
     "description": "The federal form-by-form instructions for the application package."},
    # 2 CFR 200 and "the Uniform Guidance" are the SAME document, and they are
    # kept as separate entries on purpose: the name is rendered to the PI
    # verbatim, and echoing the words their own solicitation used is what makes
    # the notice findable in it. Merging them would show "2 CFR 200" to someone
    # who read "Uniform Guidance".
    {"name": "2 CFR 200", "short": "the federal rules on which costs are allowable",
     "pattern": r"\b2\s*CFR\s*(?:Part\s*)?200\b",
     "url_patterns": [r"ecfr\.gov[^\s]*title-2[^\s]*part-200"],
     "url": "",
     "description": ("The federal cost principles — which costs are allowable on an "
                     "award, and how they must be budgeted.")},
    {"name": "the Uniform Guidance", "short": "the federal rules on which costs are allowable",
     "pattern": r"\bUniform\s+Guidance\b",
     "url_patterns": [],
     "url": "",
     "description": ("The federal cost principles (2 CFR 200) — which costs are "
                     "allowable on an award, and how they must be budgeted.")},
    {"name": "the Federal Acquisition Regulation", "short": "the federal rules for buying goods and services",
     "pattern": r"\bFederal\s+Acquisition\s+Regulation\b|\bFAR\s+Part\s+\d+",
     "url_patterns": [r"acquisition\.gov/far"],
     "url": "",
     "description": "The federal rules for purchasing goods and services under an award."},
    {"name": "the Build America, Buy America Act", "short": "a law on what an award may buy",
     "pattern": r"\bBuild\s+America,?\s+Buy\s+America\b",
     "url_patterns": [],
     "url": "",
     "description": ("A federal law on domestic sourcing — it governs what an award "
                     "may BUY (iron, steel, construction materials), not how the "
                     "proposal is written.")},
    {"name": "the Code of Federal Regulations", "short": "the federal regulations themselves",
     "pattern": r"\bCode\s+of\s+Federal\s+Regulations\b",
     "url_patterns": [],
     "url": "",
     "description": "The body of federal regulation the solicitation points into."},
]

# A pointer at the document we ALREADY read is not delegation — the rule is
# right there and the reviewer can check it. Without this, "as described in this
# solicitation" would mark a perfectly checkable row unassessable.
_SELF_REFERENCE = re.compile(
    r"\bthis\s+(solicitation|funding\s+opportunity|announcement|program\s+description|document)\b",
    re.I)

# The row's whole ask is "go and comply with that document". Requires a rulebook
# as well (see classify) — the verb alone is not enough, or "Comply with the 30%
# equipment cap" would stop being checked.
_POINTER_ONLY_LABEL = re.compile(
    r"^\s*(?:adhere\s+to|follow|comply\s+with|conform\s+to|observe|abide\s+by"
    r"|(?:prepare|submit|format|develop)\b[^,]{0,60}?\bin\s+accordance\s+with)\b",
    re.I)


def cited_rulebook(text: str) -> Optional[str]:
    """The external rulebook `text` points at, or None if it states its own rule."""
    text = text or ""
    if not text.strip():
        return None
    for book in _RULEBOOKS:
        m = re.search(book["pattern"], text, re.I)
        if not m:
            continue
        name = book["name"]
        # "in accordance with this solicitation's PAPPG-based rules" is contrived,
        # but a self-reference sharing the sentence must not suppress a real
        # pointer — so only reject when the sentence names NO rulebook at all.
        return name
    return None


def classify(label: str, source: str) -> tuple[Optional[str], bool]:
    """(rulebook, pointer_only) for one requirement row.

    `source` is the funder's verbatim sentence; `label` is the short name the
    extractor gave the ask. The rulebook may be named in either — a label like
    "Adhere to PAPPG guidelines" often carries it when the quote is longer.
    """
    label = label or ""
    source = source or ""
    blob = f"{label} {source}"
    if _SELF_REFERENCE.search(blob) and cited_rulebook(blob) is None:
        return None, False
    target = cited_rulebook(blob)
    if target is None:
        return None, False
    return target, bool(_POINTER_ONLY_LABEL.match(label.strip()))


def short_for(name: str) -> str:
    """A handful of words saying what the document IS.

    The notice carried a full sentence per rulebook and crowded the screen; with
    it removed the notice read "the PAPPG · 1 requirement not checked", which
    means nothing to a PI who has never submitted to NSF. Neither length was the
    problem — this is the content that has to be there."""
    for book in _RULEBOOKS:
        if book["name"] == name:
            return book.get("short", "")
    return ""


def describe(name: str) -> str:
    """The plain-English gloss for a rulebook name, or "" if unknown."""
    for book in _RULEBOOKS:
        if book["name"] == name:
            return book["description"]
    return ""


def url_for(name: str) -> str:
    """Where to go and read it, or "" when we have not verified a link."""
    for book in _RULEBOOKS:
        if book["name"] == name and book["url"]:
            return book["url"]
    return ""


def summarize(findings: list, *, covered: Optional[dict] = None) -> list[dict]:
    """One row per rulebook this solicitation defers to, for the UI.

    Built here rather than in the frontend so the names, the glosses and the
    counts all come from one place. `unchecked` is the number of requirements
    whose ENTIRE ask was "follow that document" — those are the ones excluded
    from the score; the rest were checked and merely carry rules that continue
    elsewhere, which is a different and much weaker warning.

    `covered` maps a RULEBOOK NAME to the sections whose rules we now hold and
    DID check, so the notice can shrink as coverage grows. Telling a PI "the
    PAPPG is not checked here" when four of its sections now are is the same
    dishonesty as the badge that read "attached" for a proposal carrying rules
    only. Defaults to none covered, so every existing caller behaves as before.

    IT IS A MAP, NOT A LIST, AND THAT IS THE WHOLE POINT. One list stamped onto
    every row told a solicitation citing both the PAPPG and the Build America,
    Buy America Act that we had checked BABA's Project Summary rules. We hold
    nothing of that Act at all. A caveat naming the wrong document is worse than
    no caveat — it reports coverage of a rulebook nothing read.

    `delegated_to` is normally set by `apply_delegation` before a finding ever
    reaches here — but falls back to `cited_rulebook(label)` when it is absent,
    so a caller that only has the raw label (as a stored `delegated` row read
    back without going through classification again) is still grouped
    correctly. This never disagrees with `apply_delegation`: it only fires
    when `delegated_to` is unset, and `classify` never suppresses a rulebook
    genuinely named in the label — so the fallback and the real classification
    agree by construction.

    A BASELINE row (carries `rulebook`, set by `draft_review._finding` from
    `rulebook_baseline`) is skipped outright, same as `apply_delegation`'s own
    guard and for the identical reason: it IS the rulebook's rule, checked, not
    a pointer INTO the rulebook. Without this, the `cited_rulebook(label)`
    fallback above would misfire on it — a baseline row whose LABEL happens to
    name its own rulebook (e.g. a future row phrased "Comply with the NIH
    Grants Policy Statement's page limit") would get folded into the "not
    checked" summary for a rule we just checked. No current baseline label
    triggers this (verified: none of the 14 PAPPG row labels name "PAPPG"), but
    the fallback exists precisely so a label CAN carry the name, so the guard
    has to be unconditional rather than relying on today's wording.
    """
    covered = dict(covered or {})
    rows: dict = {}
    for f in findings or []:
        if f.get("rulebook"):
            continue
        name = f.get("delegated_to") or cited_rulebook(f.get("label", ""))
        if not name:
            continue
        row = rows.setdefault(name, {"name": name, "description": describe(name),
                                     "short": short_for(name),
                                     "url": url_for(name),
                                     "total": 0, "unchecked": 0,
                                     "covered_sections": list(covered.get(name) or [])})
        row["total"] += 1
        if f.get("status") == "delegated":
            row["unchecked"] += 1
    return sorted(rows.values(), key=lambda r: (-r["unchecked"], -r["total"], r["name"]))


def note_for(target: str, *, pointer_only: bool) -> str:
    """What the PI reads. It has to say we did not check, and why, without
    implying they skipped a step — the rule is simply somewhere we cannot see."""
    if pointer_only:
        return (f"The whole of this requirement is \"follow {target}\", which was not "
                f"read — check it yourself.")
    # A RIDER gets NOTHING here. It already carries a "defers to X" tag and the
    # notice above the findings says the same thing once. Appending a sentence to
    # every rider's note put the identical caveat on the screen four times over —
    # in the notice, on each section heading, as a tag, and inside the note — and
    # it buried the actual feedback the PI needs to act on.
    return ""


# ── which rulebooks does a whole solicitation point at? ─────────────────────
#
# `classify` answers that per REQUIREMENT ROW, off the sentence the extractor
# kept. This answers it for the WHOLE document, off the raw text — because the
# link is often nowhere near the sentence that imposes the rule, and because a
# solicitation may point at a rulebook in a URL without naming it in prose.
#
# WHY IT MATCHES RULEBOOKS AND NOT LINKS. Measured on NSF 23-598: the document
# carries 13 distinct URLs and exactly TWO are rulebooks (the PAPPG and the
# Grants.gov Application Guide). The rest are the submission portal, award
# search, a browse page, a Senate report and two homepages. Following every URL
# would read nsf.gov's front page and turn it into requirements — the reason to
# extract links at all is to identify KNOWN rulebooks, never to crawl.
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")


def rulebooks_in(text: str) -> list[dict]:
    """Every known rulebook this document points at, named or linked.

    Each row carries the canonical `url` we would read, and `cited_url` — the
    link as it actually appears in the document, which is how we know the
    solicitation really did point there rather than merely mentioning a name.
    """
    text = text or ""
    if not text.strip():
        return []
    urls = _URL_RE.findall(text)
    # Name matching runs on the PROSE with links removed. A URL like
    # `...ods_key=pappg` contains the word "pappg", so matching over the raw text
    # would report every linked rulebook as also named — and `named` exists
    # precisely to tell those two cases apart.
    prose = _URL_RE.sub(" ", text)
    out = []
    for book in _RULEBOOKS:
        by_name = bool(re.search(book["pattern"], prose, re.I))
        cited = ""
        for pat in book.get("url_patterns") or []:
            hit = next((u for u in urls if re.search(pat, u, re.I)), "")
            if hit:
                cited = hit.rstrip(".,;)")
                break
        if not by_name and not cited:
            continue
        out.append({
            "name": book["name"],
            "description": book["description"],
            "url": book["url"] or cited,
            "cited_url": cited,
            "named": by_name,
        })
    return out
