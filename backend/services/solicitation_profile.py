"""The shape services/draft_review.py reviews against.

WHY THIS EXISTS
---------------
The reviewer used to import one hardcoded solicitation module directly, which is
why it only worked for that one funder. A PROFILE is that same information
as data, so the engine can be handed any solicitation — the one it was written
for, or anything a PI attaches — and behave identically.

A profile is deliberately a plain dict (no dataclass): it round-trips through
json.dumps into Submission.solicitation_json, and `checks` — the only
non-serializable member — is re-attached on load from code, never persisted.

DATA ONLY: no LLM, no network, no DB (mirrors forms_catalog.py).
"""

from __future__ import annotations

import re
from typing import Optional

_NUMBERING_RE = re.compile(r"^\s*(?:[\dIVXivx]+[.)]\s*)+")


def aliases_for(label: str) -> list[str]:
    """Headings a PI plausibly types for a section called `label`.

    The locate stage matches these against whole heading LINES, so they are
    lowercased variants, never substrings of prose."""
    label = " ".join((label or "").split())
    if not label:
        return []
    out = [label.lower()]
    bare = _NUMBERING_RE.sub("", label).strip().lower()
    if bare and bare not in out:
        out.append(bare)
    # "Letters of Collaboration" -> also match the singular a PI may write.
    if bare.endswith("s") and bare[:-1] not in out:
        out.append(bare[:-1])
    return out


def section_key(name: str) -> str:
    """The canonical key for a section name. PUBLIC because more than one module
    has to arrive at the same key independently: sections_from() files a section
    under it, and generic_checks looks a located span up by it. Two definitions
    would drift and the lookup would silently miss."""
    key = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return key or "other"


def _section_label(key: str) -> str:
    return " ".join(w.capitalize() for w in key.split("_"))


# Words that carry no meaning in a section NAME. Deliberately the same set as
# solicitation_requirements._SECTION_FILLER, and NOT imported from there: this
# module is data-only (no LLM, no network, no DB) and that import would drag
# gemini_client in behind it. A test asserts the two stay identical, because a
# silent divergence here re-splits sections that should be one.
_SECTION_FILLER = {"the", "a", "an", "of", "for", "and", "section", "sections",
                   "part", "your", "proposal", "proposals"}


def _singular(word: str) -> str:
    """Naive, with the same guard canon_section uses so "analysis" survives."""
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def section_signature(name: str) -> frozenset:
    """The SET of meaning-carrying words in a section name.

    Two names denote the same part of a proposal when their signatures match.
    A SET, so a repeated word collapses ("Budget and Budget Justification" ->
    {budget, justification}); filler-stripped, so connectives do not split a
    section ("Letter of Intent" -> {letter, intent}).

    Set EQUALITY, never containment — that is the whole safety property.
    Containment would fold "Project Description Supplementary Documents" into
    "Project Description" and silently lose a real section; equality cannot,
    because the extra words are real ones.
    """
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").strip().lower()).split()
    return frozenset(_singular(w) for w in words if w not in _SECTION_FILLER)


# Names that denote ONE part of a proposal while sharing no equal word-set, so
# `section_signature` cannot see they are the same thing.
#
# A NAMED EQUIVALENCE, deliberately, rather than a looser matcher. Loosening
# equality to containment is what would fold "Project Description Supplementary
# Documents" into "Project Description" and silently lose a real section — the
# hole set-equality exists to close, and there is a test that keeps it closed.
# Every entry here is a judgement someone made on purpose and can be read back.
#
# The first entry was found by auditing a live proposal. One funder's rulebook
# calls an upload slot "Special Information and Supplementary Documentation"
# while a solicitation drawing on that same rulebook writes "Supplementary
# Documents"; the two never merged, so that solicitation's rules about letters
# of support sat in a section the section picker does not offer.
#
# NOTE the tension, since a test in this module's suite guards it: these are
# section NAMES, not a funder branch. Nothing here asks who the sponsor is and
# the entry fires for any document using either phrasing. If this table ever
# grows entries that only make sense for one sponsor, it belongs in
# `rulebook_baseline` (which is keyed on the rulebook) rather than here — this
# module is deliberately dependency-free and must stay that way.
_EQUIVALENT_SECTIONS = (
    (frozenset({"supplementary", "document"}),
     frozenset({"special", "information", "supplementary", "documentation"})),
)


def _equivalent_signatures(sig: frozenset) -> list[frozenset]:
    """`sig` plus any signature declared to mean the same section."""
    out = [sig]
    for a, b in _EQUIVALENT_SECTIONS:
        if sig == a:
            out.append(b)
        elif sig == b:
            out.append(a)
    return out


def resolve_section_key(sections: dict, name: str) -> Optional[str]:
    """Which section in `sections` does `name` refer to, or None.

    PUBLIC because generic_checks looks up an attachment's span by the
    solicitation's verbatim name while the section may be filed under the
    canonicalised one. An exact key hit wins; otherwise the signature decides.
    """
    if not sections:
        return None
    key = section_key(name)
    if key in sections:
        return key
    sig = section_signature(name)
    if not sig:
        return None
    wanted = _equivalent_signatures(sig)
    for k, meta in sections.items():
        if section_signature(meta.get("label") or k) in wanted:
            return k
        if any(section_signature(a) in wanted for a in (meta.get("aliases") or [])):
            return k
        # The KEY itself, for a universe whose entry carries neither a label
        # matching the equivalence nor an alias for it.
        if section_signature(k) in wanted:
            return k
    return None


def heading_regex(alias: str) -> re.Pattern:
    """A heading LINE for `alias`: optional numbering/bullets, the alias, then
    optional punctuation — and nothing else on the line.

    Anchored per-line so a passing mention inside a paragraph never counts as a
    heading. PUBLIC and shared: the locate stage uses it to segment a draft, and
    generic_checks uses it to tell a real attachment section from a sentence
    that merely names one ("as described in our Data Management Plan")."""
    return re.compile(
        r"^[ \t]*(?:[\dIVXivx]+[.)]\s*)*(?:[-–—*•]\s*)?" + re.escape(alias) + r"\s*:?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )


def _title_if_lower(phrase: str) -> str:
    """Capitalise a mined name, but only if the funder did not capitalise it.

    A phrase mined from mid-sentence arrives lower-case ("restrict the content
    of letters of collaboration"), and rendering "letters of collaboration"
    beside "Project Summary" in a picker reads as a defect. A phrase carrying
    any capital is the funder's own casing and is left exactly as written --
    including forms a title-caser would ruin.

    Filler words stay lower-case unless they lead, so "Facilities, Equipment
    and Other Resources" keeps NSF's own shape rather than becoming
    "... And Other ...".
    """
    if phrase != phrase.lower():
        return phrase
    out = []
    for i, word in enumerate(phrase.split()):
        bare = word.strip(".,;:()").lower()
        out.append(word if i and bare in _SECTION_FILLER else word.capitalize())
    return " ".join(out)


def sections_from(requirements: list[dict], page_limits: Optional[dict] = None,
                  attachments: Optional[list] = None) -> dict:
    """Assemble the section universe the locate stage segments the draft into.

    Three sources, all already produced by extraction: the `section` each
    requirement row names, the sections the solicitation gives a page limit for,
    and the attachments it requires by name. A requirement whose section is not
    otherwise known still gets a section here, so it can never be dropped."""
    sections: dict = {}
    # signature -> key, so the same part of a proposal arriving under two names
    # merges instead of splitting. The three sources use different vocabulary:
    # requirement rows carry a canonicalised key ("letter_intent"), attachments
    # arrive verbatim from the solicitation ("Letter of Intent").
    by_signature: dict = {}

    def add(raw_key: str, label: Optional[str] = None) -> None:
        key = section_key(raw_key)
        if not key:
            return
        lbl = label or _section_label(key)
        sig = section_signature(lbl) or section_signature(key)

        # A DECLARED equivalence counts as the same signature here, exactly as
        # it does in `resolve_section_key`. Both layers are needed and neither
        # substitutes for the other: measured on a live proposal, the universe
        # split "Supplementary Documents" from "Special Information and
        # Supplementary Documentation", and because BOTH keys then existed the
        # lookup took its exact-key hit and never consulted the equivalence at
        # all -- so rules under the first stayed unreachable from the picker.
        existing = None
        if sig:
            for candidate in _equivalent_signatures(sig):
                existing = by_signature.get(candidate)
                if existing is not None:
                    break
        if existing is not None and existing != key:
            # Same section, different words for it. Keep the key already in use
            # (requirement rows point at it) and WIDEN the aliases, so a PI who
            # writes either heading is located. Prefer the authored label when
            # it is the fuller one: "Letter of Intent" is what the solicitation
            # calls it, "Letter Intent" is an artefact of canonicalisation and
            # is a heading nobody would ever type.
            meta = sections[existing]
            for alias in aliases_for(lbl):
                if alias not in meta["aliases"]:
                    meta["aliases"].append(alias)
            if label and len(lbl) > len(meta["label"]):
                meta["label"] = lbl
            return

        if key in sections:
            return
        # The DERIVED label is kept as an extra alias alongside the authored
        # one. A row may name the section properly ("Facilities, Equipment and
        # Other Resources") while a PI types it without NSF's commas, and both
        # spellings have to locate. Aliases only ever widen the net, and
        # heading_regex still demands a whole line, so this cannot match prose.
        alias_list = aliases_for(lbl)
        for extra in aliases_for(_section_label(key)):
            if extra not in alias_list:
                alias_list.append(extra)
        sections[key] = {"label": lbl, "aliases": alias_list}
        if sig:
            by_signature[sig] = key

    # An AUTHORED label wins over the title-cased key, wherever on the row list
    # it appears. Collected first because `add` is first-writer-wins by key: the
    # solicitation's own rows come before the rulebook baseline's, so a baseline
    # row carrying the real NSF heading would otherwise arrive too late to be
    # used.
    authored: dict = {}
    for req in requirements or []:
        if req.get("section") and req.get("section_label"):
            authored.setdefault(section_key(str(req["section"])),
                                str(req["section_label"]))

    # FALLBACK: mine the funder's own wording out of the requirement LABELS.
    #
    # `canon_section` strips filler when it builds a key, so "Letter of Intent"
    # becomes `letter_intent` and title-casing that back gives "Letter Intent"
    # — a name nobody writes, offered in the section picker. `section_label` is
    # the intended source and the extraction emits it sometimes; when it does
    # not, the real name is still sitting in the rows ("Include required title
    # format in Letter of Intent").
    #
    # SET EQUALITY is the safety property, the same one every other section
    # comparison in this module rests on: a phrase is accepted only when its
    # meaning-carrying words are EXACTLY the key's. Containment would let
    # "Project Description Supplementary Documents" name `project_description`
    # and lose a real section, which is the hole equality exists to close.
    for req in requirements or []:
        key = section_key(str(req.get("section") or ""))
        if not key or key in authored:
            continue
        wanted = section_signature(key)
        if not wanted:
            continue
        words = str(req.get("label") or "").split()
        for size in range(len(wanted), min(len(wanted) + 3, len(words)) + 1):
            for i in range(len(words) - size + 1):
                phrase = " ".join(words[i:i + size]).strip(".,;:()")
                if phrase and section_signature(phrase) == wanted:
                    authored[key] = _title_if_lower(phrase)
                    break
            if key in authored:
                break

    for req in requirements or []:
        if req.get("section"):
            add(str(req["section"]),
                label=authored.get(section_key(str(req["section"]))))
    for name in (page_limits or {}):
        add(str(name))
    for name in (attachments or []):
        add(str(name), label=str(name))
    return sections


def make_profile(*, id: str, title: str, url: Optional[str] = None,
                 sections: dict, requirements: list[dict],
                 checks: Optional[dict] = None,
                 merit_criteria: Optional[list] = None,
                 eligibility_notes: Optional[list] = None) -> dict:
    return {
        "id": id,
        "title": title,
        "url": url,
        "sections": sections or {},
        "requirements": list(requirements or []),
        "checks": checks or {},
        "merit_criteria": merit_criteria or [],
        "eligibility_notes": eligibility_notes or [],
    }


def sections_offered_for(profile: Optional[dict], rulebook: str) -> list[dict]:
    """The sections THIS proposal can have checked one at a time.

    `rulebook_baseline.sections_offered` answers the same question for the
    rulebook alone, and that was the whole picker until 2026-08-26 — so every
    proposal was offered its rulebook's sections whatever its own solicitation
    asked for. Measured on a live federal proposal: of that solicitation's 53
    rules only 24 were reachable, and the largest unreachable group was a whole
    deliverable — its Letter of Intent, 8 scored rules, the FIRST thing that
    program requires and so the one a PI is most likely to be writing when they
    reach for this tool.

    BOTH LISTS ARE NEEDED AND NEITHER CONTAINS THE OTHER. Offering only what the
    solicitation names would have dropped 48 rulebook rules on that same
    proposal, covering three sections its solicitation never mentions —
    including the 34 that catch a date of birth in a biographical sketch. A
    solicitation is silent about those precisely BECAUSE the standing rulebook
    covers them and the funder enforces it either way. That is the normal shape
    of a solicitation, so the baseline has to survive the silence.

    THE RULEBOOK'S KEY WINS WHEN BOTH NAME A SECTION, and that is not cosmetic:
    `review_section` looks its rulebook rows up by exact key
    (`rules_for(rulebook, section)`) and only resolves the PROFILE's rows
    through `resolve_section_key`. Emitting the solicitation's spelling
    (`budget_justification`) for a shared section would silently return zero
    rulebook rows — the 45-rule version of the orphaning bug this module's
    `_refile_rows` already exists to prevent.

    A solicitation-only section must hold at least one SCORED row to be offered,
    the same test `sections_offered` applies to Cover Sheet: a section whose
    every row is conditional gives the PI a page of "if this applies to you",
    which is a dead end dressed as a tool. Those rows still appear in a full
    Draft Review, which is where an advisory row belongs.

    Returns the rulebook's own list unchanged when there is no profile.
    """
    from services import rulebook_baseline

    base = rulebook_baseline.sections_offered(rulebook)
    if not profile:
        return [dict(s) for s in base]

    sections = profile.get("sections") or {}
    rows = [r for r in (profile.get("requirements") or []) if not r.get("rulebook")]

    total, scored = {}, {}
    for r in rows:
        key = r.get("section")
        if not key:
            continue
        total[key] = total.get(key, 0) + 1
        if r.get("scored"):
            scored[key] = scored.get(key, 0) + 1

    # Every spelling of a rulebook section, so a profile key can be recognised
    # as naming one. Built from the label, the key and the named equivalences,
    # because the three row sources spell one section with three functions.
    #
    # NAMING comes from EVERY section the rulebook knows, not just the offered
    # ones, while INCLUSION below comes from `base`. The two must not be the
    # same set: a section the rulebook holds only extended rules for (Budget
    # carries 45 and no basics) is not offered on the rulebook's own account,
    # but when the solicitation fills it the entry must still be keyed the
    # rulebook's way. Keying it the solicitation's way instead would make the
    # picker's key depend on which tier happened to be empty — the same moving
    # target that has orphaned rows three times in this codebase already.
    known = {}
    for row in rulebook_baseline.rules_for(rulebook):
        known.setdefault(row["section"],
                         rulebook_baseline.section_label(row["section"]))

    to_base = {}
    for key, label in known.items():
        for name in (label, key):
            sig = section_signature(name)
            if not sig:
                continue
            for equiv in _equivalent_signatures(sig):
                to_base.setdefault(equiv, key)

    labels = dict(known)
    labels.update({s["key"]: s["label"] for s in base})
    out, seen = [], set()

    def _emit(key: str, label: str, sol_key: Optional[str]) -> None:
        if key in seen:
            return
        seen.add(key)
        out.append({
            "key": key,
            "label": label,
            "solicitation_rules": total.get(sol_key, 0) if sol_key else 0,
            # BASIC rows only — the count has to be what the review will
            # actually check, or the picker promises rules the screen never
            # shows.
            "rulebook_rules": len(
                rulebook_baseline.rules_for(rulebook, key, tier="basic")),
        })

    # ORDER IS THE ONLY THING LEFT TO CARRY THE MESSAGE. The picker used to
    # group these under headings naming where each section's rules came from,
    # and a PI read that as "these are checked differently" — every section is
    # checked against BOTH sources, so the headings described provenance, not
    # behaviour, and were removed. What was left was an order that only made
    # sense inside the groups: sections were emitted as the solicitation
    # happened to mention them, so on a real proposal Budget led the list and
    # Project Summary sat fifth.
    #
    # 1. Parts the rulebook has never heard of — a letter of intent, letters of
    #    collaboration. They cannot be placed in its order because they are not
    #    in it, and for a program that requires one the letter of intent is
    #    genuinely the first thing its PI writes, so the front is right.
    for key, meta in sections.items():
        if not total.get(key) or not scored.get(key):
            continue
        sig = section_signature(meta.get("label") or key) or section_signature(key)
        if sig and to_base.get(sig):
            continue                       # the rulebook knows it — step 2 places it
        _emit(key, meta.get("label") or _section_label(key), key)

    # 2. Everything the rulebook knows, in ITS order — Research.gov's, which is
    #    the order a PI meets these parts of a package. A section the rulebook
    #    holds only extended rules for (Budget) is offered here when the
    #    solicitation fills it, and takes its proper place rather than the front.
    ordered = [s["key"] for s in base]
    ordered += [k for k in known if k not in set(ordered)]
    for key in ordered:
        sol_key = resolve_section_key(sections, labels.get(key, key)) or (
            key if key in sections else None)
        if key in {s["key"] for s in base} or (sol_key and total.get(sol_key)):
            _emit(key, labels.get(key, key), sol_key)

    return out


def requirements_for(profile: dict, section: Optional[str]) -> list[dict]:
    """Rows belonging to `section`; None -> the whole-document rows."""
    return [r for r in profile.get("requirements", []) if r.get("section") == section]


def scored_requirements(profile: dict) -> list[dict]:
    return [r for r in profile.get("requirements", []) if r.get("scored")]


def _refile_rows(rows: list[dict], sections: dict) -> list[dict]:
    """Point every row at the section key that SURVIVED the merge.

    `sections_from` already collapses two names for one part of a proposal —
    `Budget and Budget Justification` and `Budget Justification` are one section,
    which is what stopped a Budget Justification sitting in a draft being
    reported missing. But it merges the section UNIVERSE and keeps whichever key
    was already in use, and rows pointing at the key that lost were left behind.

    That is not hypothetical. The three row sources spell a section name with
    three different functions: a solicitation's own rows arrive canonicalised by
    `solicitation_requirements.canon_section` (which strips "and", giving
    `budget_justification`), contract rows arrive verbatim, and rulebook rows use
    `section_key` (which does not, giving `budget_and_budget_justification`).
    Measured on a real proposal: the universe merged to the first and 45 PAPPG
    rules kept pointing at the second, so every one reported "Not located" and
    dropped out of the score's denominator — recovered and then never checked,
    which is worse than never having them because the row looks handled.

    ONLY ever moves a row ONTO a section that exists. `resolve_section_key`
    returns None for a name the universe does not know, and an unknown section is
    LEFT ALONE rather than blanked: silently re-filing a row we cannot place is
    the over-reach `canon_section`'s document-heading deny-list was deliberately
    kept narrow to avoid.
    """
    if not sections:
        return rows
    out = []
    for row in rows:
        key = row.get("section")
        if key and key not in sections:
            resolved = resolve_section_key(sections, key)
            if resolved:
                row = dict(row)
                row["section"] = resolved
        out.append(row)
    return out


def build_generic(contract: dict, requirements: list[dict], *, id: str, title: str,
                  url: Optional[str] = None,
                  merit_criteria: Optional[list] = None,
                  eligibility_notes: Optional[list] = None) -> dict:
    """A profile for an arbitrary solicitation.

    `requirements` are the SEMANTIC rows read out of the document; the
    deterministic rows are derived here from the contract's hard numbers rather
    than stored, so they always track the contract the PI actually confirmed —
    edit the page limit and the check that enforces it moves with it.

    Sections are likewise derived, never persisted: a stored section list could
    drift out of step with the requirements that name it.

    A third source: when the solicitation CITES a rulebook we hold rules for
    (the PAPPG today), that rulebook's rules are appended. Detection reads the
    requirement rows' own quotes, never a sponsor string. Because this runs on
    every profile load, adding a rule retroactively fixes every proposal already
    in the database."""
    from services import generic_checks
    from services import rulebook_baseline
    contract = contract or {}
    extracted = list(requirements or [])
    # THREE row sources now. Baseline rows go LAST so a solicitation's own row
    # for the same part of the proposal is the one a reader meets first — its
    # quote is the sentence the PI must actually satisfy.
    rows = (extracted
            + generic_checks.contract_requirements(contract)
            + rulebook_baseline.baseline_rows(
                extracted, url=url or "",
                page_limits=contract.get("page_limits")))

    sections = sections_from(rows,
                             page_limits=contract.get("page_limits"),
                             attachments=contract.get("required_attachments"))
    rows = _refile_rows(rows, sections)
    return make_profile(
        id=id, title=title, url=url,
        sections=sections,
        requirements=rows,
        checks=generic_checks.CHECKS,
        merit_criteria=merit_criteria,
        eligibility_notes=eligibility_notes,
    )
