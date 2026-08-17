"""Read the PAPPG's sliced sections into requirement rows. OFFLINE, never on a
request path.

WHAT THIS IS FOR
----------------
`kb_pappg/slicer.py` cuts Chapter II into ten per-section slices. This reads each
one and returns rows shaped like `rulebook_baseline`'s table, so they can be
reviewed by a human and then pasted in.

THE SECTION IS AN INPUT, NOT A GUESS -- that is the whole design
----------------------------------------------------------------
Three previous attempts fed the model a chapter and asked it which section each
rule belonged to. It invented 97 of them. Here the slice IS the section, so the
model's `section` field is discarded outright and replaced with the slice's key.
The model is asked one question only: what does THIS text require?

NEVER RUNS IN A REQUEST. Ten sections x 10-18 model calls each is minutes of wall
clock and well past the 300s Cloud Run cap. Run it from a terminal, review the
output, commit the rows.

THE TWO MISMATCHES THAT MAKE A NAIVE PASTE FAIL SILENTLY
---------------------------------------------------------
1. DIFFERENT CANONICALISER. `solicitation_requirements.canon_section` strips filler
   words and singularises; `solicitation_profile.section_key` does neither. They
   disagree on SIX of the nine section titles -- most destructively
   `facilities_equipment_and_other_resources` vs
   `facilitie_equipment_other_resource`. A row filed under the wrong key can never
   be located in a draft: it reports "Not located" and drops out of the score's
   denominator, unchecked and unnoticed. That exact mismatch disabled four rules
   earlier this week. Rows here are keyed from the SLICE, never from the model.

2. MISSING KEYS. The extractor emits 8 keys; a baseline row needs 13. The extras
   (`section_label`, `rulebook`, `source_url`, `check`, `check_args`) are added
   here. `section_label` must carry the section's real name WITH punctuation --
   title-casing a key is what broke Facilities.

WHAT THIS DELIBERATELY DOES NOT DO
-----------------------------------
It does not write into `rulebook_baseline.RULES`. Extracted rows are model output;
golden rule 4 says a human reviews before anything reaches a PI. It emits a review
file and stops.

It also never overwrites a curated rule. Where a curated rule and an extracted one
state the same thing, the curated one wins -- it was verified against NSF's own
distillation on Research.gov. The one exception worth making by hand is UPGRADING a
curated row whose `source` is a "Derived from ..." line to the PAPPG's real
sentence, which is why `find_quote_upgrades()` exists.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from services import draft_scope
from services import rulebook_baseline as rb
from services import solicitation_requirements as sr
from services.solicitation_profile import section_key

SLICES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "kb_structured", "_pappg_24_1_sections.json")

# Sections whose rules we already hold, curated from Research.gov. Extraction over
# these is the VALIDATION set: we know what should come back, so a poor result here
# means the method is wrong and nothing else should be trusted.
VALIDATION_SECTIONS = (rb.PROJECT_SUMMARY, rb.PROJECT_DESCRIPTION,
                       rb.REFERENCES_CITED, rb.FACILITIES)


def load_slices(path: str = SLICES_PATH) -> dict:
    """The committed slices, keyed by section_key."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {s["section_key"]: s for s in data["sections"]}


def _pappg_id(section: str, label: str) -> str:
    """A stable-ish id, namespaced so an ingested row can never collide with a
    curated one. Ids derive from the model's own label and are NOT stable across
    two reads -- CLAUDE.md measures id-jaccard at 0.04 on runs that found the same
    rules -- so this is for review bookkeeping, not for matching."""
    base = sr.make_id({"section": section, "label": label})
    return f"pappg_{base}"[:80]


def to_baseline_row(row: dict, slice_meta: dict) -> dict:
    """One extractor row -> one `rulebook_baseline`-shaped row.

    `row["section"]` is DISCARDED. The slice already knows which section this is,
    and the model's guess is exactly what produced 97 invented sections."""
    key = slice_meta["section_key"]
    return rb._row(
        _pappg_id(key, row["label"]),
        key,
        row["label"],
        row["source"],
        row.get("why") or "",
        kind="semantic",          # every ingested row is model-judged; a
                                  # deterministic check must be written by hand in
                                  # rulebook_checks and attached during review.
        check=None,
        scored=bool(row.get("scored", True)),
        rulebook="the PAPPG",
        url=slice_meta["source_url"],
        keywords=row.get("keywords") or [],
    ) | {
        # Review-only provenance. Harmless on a live row (nothing reads them) and
        # the reviewer needs to know which slice a rule came from.
        "pappg_ref": slice_meta["pappg_ref"],
        "section_label": slice_meta["nsf_label"],
    }


def read_section(key: str, *, slices: Optional[dict] = None,
                 use_ai: bool = True, budget_s: float = 150.0) -> dict:
    """Extract one section's requirements from its slice.

    Passes the RULEBOOK prompts, which exist in `solicitation_requirements` and
    which nothing has ever called. They must move together: leaving the sweep on
    the solicitation prompt lets round two re-import everything round one was told
    to skip."""
    slices = slices or load_slices()
    if key not in slices:
        raise KeyError(f"no slice for {key!r}; have {sorted(slices)}")
    sl = slices[key]

    out = sr.extract_requirements(
        sl["text"],
        use_ai=use_ai,
        budget_s=budget_s,
        system=sr._RULEBOOK_SYSTEM,
        sweep_system=sr._RULEBOOK_SWEEP_SYSTEM,
    )

    rows, dropped_scope = [], []
    for r in out.get("requirements") or []:
        # Submission mechanics, portal clicks and post-award obligations. A draft
        # cannot satisfy them, and draft_scope already routes them to the checklist.
        if not draft_scope.is_draft_checkable(r.get("label", ""), r.get("source", "")):
            dropped_scope.append(r["label"])
            continue
        rows.append(to_baseline_row(r, sl))

    return {
        "section_key": key,
        "nsf_label": sl["nsf_label"],
        "pappg_ref": sl["pappg_ref"],
        "chars": sl["char_count"],
        "rows": rows,
        "dropped_out_of_scope": dropped_scope,
        # Carried through verbatim: a run that stopped early must say so rather
        # than let a short list read as a complete one.
        "read_report": {k: out.get(k) for k in
                        ("ai", "rounds", "chunks", "dropped_unverified",
                         "hit_round_cap", "hit_time_cap", "targeted_added",
                         "targeted_ran", "elapsed_s")},
    }


def find_quote_upgrades(section_key_: str, extracted: list[dict]) -> list[dict]:
    """Curated rows whose `source` is a derived line, where the PAPPG states the
    rule outright.

    Three Project Summary rows carry "Derived from ..." because Research.gov's
    summary never stated them -- a grounding weakness a review already raised. The
    PAPPG does state them, so this points at the upgrade rather than applying it:
    swapping a quote is a hand edit a human should see."""
    out = []
    for cur in rb.rules_for("the PAPPG", section_key_):
        if not cur["source"].lstrip().lower().startswith("derived"):
            continue
        want = set(cur["label"].lower().split())
        best, score = None, 0.0
        for ex in extracted:
            overlap = len(want & set(ex["label"].lower().split())) / max(1, len(want))
            if overlap > score:
                best, score = ex, overlap
        if best is not None and score >= 0.4:
            out.append({"curated_id": cur["id"], "curated_label": cur["label"],
                        "derived_source": cur["source"],
                        "pappg_quote": best["source"], "confidence": round(score, 2)})
    return out
