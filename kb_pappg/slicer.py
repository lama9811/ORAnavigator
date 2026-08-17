#!/usr/bin/env python3
"""Cut the PAPPG into per-section slices. DETERMINISTIC — no model anywhere.

WHY THIS EXISTS
---------------
Reading the PAPPG has been tried three times and shelved each time (CLAUDE.md):
Chapter II whole gave 598 requirements and 97 INVENTED sections; a scoped prompt
cut rows 36% without moving the noise and merged four usable Project Summary rows
into two; a deterministic output filter removed only 11%.

Every one of those fed the model a chapter and asked it to work out which section
each rule belonged to. That is where the 97 invented sections came from.

§II.D.2 is organised by the documents a PI actually uploads — Cover Sheet, Project
Summary, Table of Contents, Project Description, References Cited, Budget and
Budget Justification, Facilities, Senior/Key Personnel Documents, Special
Information — and that list maps 1:1 onto Research.gov's upload pages. So slicing
first makes the section an INPUT rather than a guess. `solicitation_requirements`
rule 9 already forbids doc-heading leakage and it leaks anyway; this removes the
opportunity instead of restating the prohibition.

Measured: Chapter II is 232,859 chars. The slices this emits total ~94,000, and the
largest single one (Budget) is 34,601 — under CHUNK_CHARS, so every section is ONE
model read rather than a chunked multi-round sweep.

BOUNDARY DETECTION IS THE WHOLE RISK, AND TWO OBVIOUS RULES ARE BOTH WRONG
--------------------------------------------------------------------------
Each section title appears TWICE: once in the section's own table of contents,
once as the body heading. Picking the wrong one silently yields a 20-character
slice, and a thin slice reads exactly like a section with few rules.

  "take the first match"           -> every slice is a TOC line.
  "take the one followed by prose" -> WRONG for `d. Project Description` and
                                      `h. Senior/Key Personnel Documents`, whose
                                      body headings are legitimately followed by
                                      sub-headings ("(i) Content").
  "take the one NOT followed by
   another lettered heading"       -> WRONG for Senior/Key Personnel, whose TOC
                                      entry is followed by "(i) Biographical
                                      Sketch(es)" and so reads as prose.

What actually holds: the TOC block is CONTIGUOUS and comes first, so the body is
the LAST full-line match. Monotonically increasing offsets are then asserted, which
is what would catch this going wrong again.

One more trap, found the same way: the body heading carries a parenthetical the TOC
line lacks — `d. Project Description (including Results from Prior NSF Support)` —
so an exact-title match finds only the TOC entry. The pattern allows a trailing
parenthetical.

USAGE
    python3 kb_pappg/slicer.py --pdf /path/to/nsf24_1.pdf --dry-run
    python3 kb_pappg/slicer.py --pdf /path/to/nsf24_1.pdf
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

# The PAGE window Chapter II occupies. Read once, sliced by character offset --
# `solicitation_extractor.read_pdf` takes bytes with no page range, and neither
# does document_text._extract_pdf, so page-slicing is not available to us anyway.
CH2_FIRST_PAGE = 39
CH2_LAST_PAGE = 108

PAPPG_VERSION = "NSF 24-1"
PAPPG_URL = "https://www.nsf.gov/policies/pappg/24-1/ch-2-proposal-preparation"

# The nine parts of §II.D.2, in NSF's order, plus §II.C which governs all of them.
# `nsf_label` is the section's REAL name including punctuation. That matters: a
# baseline row's `section_label` must carry it verbatim, because title-casing a
# key is what silently broke all four Facilities rules ("Facilities Equipment And
# Other Resources" never matches the heading a PI writes).
#
# `expect` is the measured char count. A slice outside its band FAILS rather than
# emitting a thin section -- the truncated-read failure mode CLAUDE.md records,
# where a short read looks exactly like a short section.
SECTIONS: list[dict] = [
    {"title": "Cover Sheet", "ref": "II.D.2.a", "expect": 6805},
    {"title": "Project Summary", "ref": "II.D.2.b", "expect": 973},
    {"title": "Table of Contents", "ref": "II.D.2.c", "expect": 122},
    {"title": "Project Description", "ref": "II.D.2.d", "expect": 7956},
    {"title": "References Cited", "ref": "II.D.2.e", "expect": 1185},
    {"title": "Budget and Budget Justification", "ref": "II.D.2.f", "expect": 34601},
    {"title": "Facilities, Equipment and Other Resources", "ref": "II.D.2.g", "expect": 1394},
    {"title": "Senior/Key Personnel Documents", "ref": "II.D.2.h", "expect": 25711},
    {"title": "Special Information and Supplementary Documentation",
     "ref": "II.D.2.i", "expect": 10341},
]

# §II.C — font, margins, spacing, pagination. Governs every section above, so it is
# sliced separately rather than attached to one of them.
FORMAT_SLICE = {"title": "Format of the Proposal", "ref": "II.C", "expect": 4923}

# The section AFTER the last one we want. Without it the final slice runs to the end
# of the page window and swallows §II.E and §II.F -- 126,792 chars instead of 10,341,
# dragging in every proposal-TYPE variant (RAPID, EAGER, Ideas Lab, Conference) that
# a standard proposal never uses.
END_MARKER = r"^[ \t]*E\.[ \t]+Special Processing Instructions[ \t]*$"

# How far a slice may deviate from its measured size before this refuses to run.
# Tight enough to catch a boundary regression, loose enough to survive NSF fixing a
# typo. A NEW PAPPG edition should fail here -- that is the point.
TOLERANCE = 0.15


def read_chapter_two(pdf_path: str) -> str:
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) < CH2_LAST_PAGE:
            raise SystemExit(
                f"{pdf_path} has {len(pdf.pages)} pages; expected at least "
                f"{CH2_LAST_PAGE}. Is this PAPPG {PAPPG_VERSION}?")
        return "\n".join(
            (pdf.pages[i - 1].extract_text() or "")
            for i in range(CH2_FIRST_PAGE, CH2_LAST_PAGE + 1))


def _heading_re(title: str, letter_class: str = "a-z") -> re.Pattern:
    """A full heading LINE for `title`, allowing the trailing parenthetical the
    body heading carries and the TOC entry does not."""
    return re.compile(
        r"^[ \t]*[" + letter_class + r"]\.[ \t]+" + re.escape(title) +
        r"(?:[ \t]*\([^)\n]*\))?[ \t]*$", re.M)


def body_offset(text: str, title: str, letter_class: str = "a-z") -> tuple:
    """Offset of the BODY heading — the LAST full-line match.

    See the module docstring: the table of contents is contiguous and comes first,
    so last-match is the body. Returns (start, end, n_matches); n_matches is
    reported so the caller can notice a title that stopped appearing twice."""
    matches = list(_heading_re(title, letter_class).finditer(text))
    if not matches:
        return None, None, 0
    return matches[-1].start(), matches[-1].end(), len(matches)


def slice_chapter(text: str) -> list[dict]:
    """The §II.C and §II.D.2 slices, in document order."""
    end_m = re.search(END_MARKER, text, re.M)
    if not end_m:
        raise SystemExit(
            "Could not find §II.E 'Special Processing Instructions'. Without it "
            "the final slice would swallow §II.E and §II.F -- 126,792 chars of "
            "proposal-type variants a standard proposal never uses.")
    d2_end = end_m.start()

    out: list[dict] = []

    # §II.C Format — a lettered section at the chapter level, so its own scan.
    fs, _, _ = body_offset(text, FORMAT_SLICE["title"], letter_class="A-Z")
    fd, _, _ = body_offset(text, "Proposal Contents", letter_class="A-Z")
    if fs is None or fd is None or fd <= fs:
        raise SystemExit("Could not bound §II.C Format of the Proposal.")
    out.append({**FORMAT_SLICE, "start": fs, "end": fd})

    # §II.D.2 — the nine documents a PI uploads.
    starts = []
    for spec in SECTIONS:
        s, _, n = body_offset(text, spec["title"])
        if s is None:
            raise SystemExit(
                f"Section heading not found: {spec['title']!r}. The PAPPG's "
                "structure has changed; re-derive the boundaries before trusting "
                "any slice.")
        starts.append((spec, s, n))

    for i, (spec, s, n) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else d2_end
        out.append({**spec, "start": s, "end": end, "matches": n})

    return out


def build(pdf_path: str) -> list[dict]:
    from services.solicitation_profile import section_key

    text = read_chapter_two(pdf_path)
    slices = slice_chapter(text)

    # STRICTLY INCREASING. This is the assertion that catches a boundary
    # regression -- picking a TOC line instead of a body heading shows up here as
    # an out-of-order offset long before anyone reads the extracted rules.
    prev = -1
    for sl in slices:
        if sl["start"] <= prev:
            raise SystemExit(
                f"Slices are not in document order at {sl['title']!r} "
                f"(offset {sl['start']} follows {prev}). A table-of-contents line "
                "was almost certainly matched instead of the body heading.")
        prev = sl["start"]

    rows: list[dict] = []
    for sl in slices:
        body = text[sl["start"]:sl["end"]].strip()
        n = len(body)
        lo, hi = sl["expect"] * (1 - TOLERANCE), sl["expect"] * (1 + TOLERANCE)
        if not (lo <= n <= hi):
            raise SystemExit(
                f"{sl['title']!r} sliced to {n:,} chars; expected "
                f"~{sl['expect']:,} (±{int(TOLERANCE * 100)}%). A thin slice reads "
                "exactly like a section with few rules, so this refuses rather "
                "than emitting it.")
        rows.append({
            # The key the rest of the app files rules under. MUST be section_key,
            # NOT solicitation_requirements.canon_section -- the two disagree on
            # "Facilities, Equipment and Other Resources" (canon_section strips
            # filler and singularises; section_key does neither), and that exact
            # mismatch silently disabled four rules once already.
            "section_key": section_key(sl["title"]),
            "nsf_label": sl["title"],
            "pappg_ref": sl["ref"],
            "pappg_version": PAPPG_VERSION,
            "source_url": PAPPG_URL,
            "char_count": n,
            "text": body,
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default=os.path.expanduser("~/Desktop/nsf24_1.pdf"))
    ap.add_argument("--out", default=os.path.join(
        ROOT, "backend", "kb_structured", "_pappg_24_1_sections.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        raise SystemExit(f"No PDF at {args.pdf}. Download it from "
                         "https://nsf-gov-resources.nsf.gov/files/nsf24_1.pdf")

    rows = build(args.pdf)
    print(f"{'section_key':<52}{'ref':<11}{'chars':>8}")
    print("-" * 71)
    for r in rows:
        print(f"{r['section_key']:<52}{r['pappg_ref']:<11}{r['char_count']:>8,}")
    print("-" * 71)
    print(f"{'TOTAL':<63}{sum(r['char_count'] for r in rows):>8,}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"version": PAPPG_VERSION, "url": PAPPG_URL, "sections": rows},
                  fh, indent=2, ensure_ascii=False)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
