"""Split an assembled proposal PDF into its sections, deterministically, no model.

WHY THIS EXISTS. A PI who uploads their proposal as ONE combined Research.gov PDF -- the
upload Research.gov itself hands them -- scored a steady 48% against the 76-79% the same
proposal scores as its 11 separate section files. Only 2 of 9 sections could be located, so
21 of 70 rules were assessable and the rest left the score's denominator unchecked. The
number was consistent, which is worse than varying: a steady wrong answer reads as a settled
one.

CLAUDE.md recorded that such a PDF "CANNOT be split, and no amount of model or code work
changes that". That is true of the TEXT and false of the OBJECT GRAPH. "Project Summary"
really does appear once in 56 pages and never on its own line, and the page furniture really
never names the section -- but Research.gov concatenates independently-produced attachments,
and PDFium writes each source document's font dictionaries as a contiguous run of indirect
object ids. A new sub-document starts where `min(font ids on page N) > max(all ids seen so
far)`.

MEASURED on the awarded NSF EiR package (56 pages, 11 real sections):

    font-object discontinuity   28 boundaries, ALL 11 true section starts, 0 missed
                                -> 29 atomic blocks, none straddling a section

That is a boundary SUPERSET with zero search error: every section is an exact union of
consecutive blocks, so the problem becomes "label 29 blocks", not "find a boundary among 56
page positions". Every extra boundary is itself a real sub-document (the four budget form
pages, each individually-uploaded support letter, the reviewer lists).

WHAT DOES NOT WORK, so nobody re-tests it:
  * PDF outline/bookmarks -- ABSENT (0 entries, combined and all 11 section files).
  * PDF metadata (/Title, /Subject, XMP) -- ABSENT; section files carry only /Producer.
  * Font SIZE or boldness -- 1/11 recall, <1% precision, AND A TRAP: it perfectly finds the
    18 SUBheadings inside the Project Description ("Broader Impacts", "Results from Prior NSF
    Support"), which is exactly what lures a reader into declaring a false section.
  * Left margin -- headings and body share x0=72.0 exactly. Whitespace gaps -- heading gaps
    16.9-19.5 against a body p90 of 16.4, not separable.
  * Page furniture -- uniform on 56/56 pages; encodes the proposal number and page index,
    never the section.

EVIDENCE LIMIT: n = 1. One proposal, one merger. PDFium and pypdf emit increasing object ids;
a Ghostscript-rewritten or object-stream-compacted file may not. The fail-safe stack below --
not the signal's generality -- is what makes this shippable, and `_REGRESS_TOLERANCE` turns
the caveat into a RUNTIME test rather than an assumption about the merger. Every bail returns
`{}` and falls back to exactly today's behaviour; none of them raises, and none is silent.

pypdf, NOT PyMuPDF: `fitz` is installed on the development machine and is NOT in
requirements.txt, so importing it would ship a container that fails on import. pypdf is
pinned and `main.py` already imports it.
"""
from __future__ import annotations

import re
from typing import Optional

from services import solicitation_profile as _sp

# A shorter document is not the case this exists for, and the statistics behind
# every threshold below come from assembled multi-attachment packages.
MIN_PAGES = 8
# Fewer blocks than this and there is no sub-document structure to read. One
# block per page means the producer writes fresh fonts on every page, which
# carries no signal at all -- the opposite failure from a single-source PDF.
MIN_BLOCKS = 3
# Ids that go BACKWARDS mean the merger did not lay sources out contiguously,
# which is the one assumption this whole module rests on. A few are tolerable
# (a shared resource re-referenced late); many mean the signal does not hold for
# this producer and we must not use it.
_REGRESS_TOLERANCE = 0.10
# The TOC was 8 of 10 page counts exact on the measured document, so one page of
# slop is expected. More than that means the fold put the wrong blocks together.
_PAGE_SLOP = 1
MIN_SECTIONS = 3
MIN_COVERAGE = 0.60
# How many lines at the top of a block may name it. The section name sits in the
# first line or two of an NSF form attachment; reading further starts matching
# body prose.
_PROBE_LINES = 3
_TOC_RE = re.compile(r"table\s+of\s+contents", re.IGNORECASE)
# "References Cited      6" — label and count on one line.
_TOC_SAME_LINE = re.compile(r"^(?P<name>[A-Za-z][A-Za-z ,/&-]{3,}?)\s+(?P<n>\d{1,3})\s*$")
# A line that is nothing but the count. NSF's own TOC puts the count on its own
# line ABOVE the label for some rows and beside it for others — measured on the
# same page: the first six rows are same-line, the last five are count-above.
# Parsing only one shape reads 5 rows of 11 and resolves 1.
_TOC_BARE_COUNT = re.compile(r"^(\d{1,3})$")
_TOC_LABEL = re.compile(r"^[A-Za-z][A-Za-z ,/&-]{3,}$")
# "Project Summary (not to exceed 1 page)" -> "Project Summary". The
# parenthetical carries digits and instructions, and resolving the whole string
# fails on every row that has one.
_PARENTHETICAL = re.compile(r"\s*\(.*?\)\s*|\s*\(.*$")
# A line the PDF stamps on most pages. It is not the author's text and, on this
# document, it is the FIRST line of every page — so without dropping it the
# probe never reaches the line that names the section.
_FURNITURE_SHARE = 0.5


def _page_font_ids(page) -> set:
    """Indirect object ids of the fonts this page references.

    `raw_get` deliberately, never `[]`: resolving the object would give us the
    font DICTIONARY, and the identity we need is the object NUMBER. Both shapes
    occur in the wild -- an indirect /Font dict, and a direct dict whose members
    are indirect -- and both must be collected or the boundary moves.
    """
    from pypdf.generic import IndirectObject
    ids: set = set()
    res = page.get_inherited("/Resources")      # handles the inherited /Pages case
    if res is None:
        return ids
    res = res.get_object()
    if "/Font" not in res:
        return ids
    font = res.raw_get("/Font")
    if isinstance(font, IndirectObject):
        ids.add(font.idnum)
        font = font.get_object()
    for name in (font or {}):
        v = font.raw_get(name)
        if isinstance(v, IndirectObject):
            ids.add(v.idnum)
    return ids


def page_blocks(data: bytes) -> Optional[list]:
    """Atomic sub-document page ranges as (first, last) 0-based inclusive, or None.

    None means "no usable structure" and is the caller's signal to fall back —
    it is never an error.
    """
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages
        if len(pages) < MIN_PAGES:
            return None
        starts, seen_max, regress = [], -1, 0
        for i, page in enumerate(pages):
            ids = _page_font_ids(page)
            if not ids:
                # An image-only page breaks the monotone argument outright: we
                # cannot tell whether it continued the previous document or
                # began a new one.
                return None
            if i == 0 or min(ids) > seen_max:
                starts.append(i)
            if max(ids) < seen_max:
                regress += 1
            seen_max = max(seen_max, max(ids))
        if regress > _REGRESS_TOLERANCE * len(pages):
            return None
        if len(starts) < MIN_BLOCKS or len(starts) == len(pages):
            return None
        ends = [s - 1 for s in starts[1:]] + [len(pages) - 1]
        return list(zip(starts, ends))
    except Exception as exc:                     # golden rule 3
        print(f"[PDF-SECTIONS] page_blocks failed: {exc}")
        return None


def toc_roster(page_text: str, sections: dict) -> list:
    """NSF's auto-generated Table of Contents as [(section key or None, pages, raw name)].

    A ROSTER AND A VALIDATOR, NEVER A MAP. It lists sections in NSF's canonical
    order, which is not the physical order of the assembled PDF (Budget
    physically precedes Facilities precedes Biosketch), so cumulative-summing
    these counts does NOT give start pages. What it does give is which sections
    should be present and how long each should be — 8 of 10 counts exact on the
    measured document.

    The RAW NAME is kept even for a row that resolves to None. The count used to
    survive while the identity was thrown away, so a row reading
    "Special Information/Supplementary Documents 9" became (None, 9) and nothing
    could report WHICH nine pages the funder expected. Reporting only -- no
    consumer matches on it.
    """
    rows = []
    pending: Optional[int] = None
    for raw in (page_text or "").splitlines():
        # Parentheticals carry their own digits and instructions ("(not to
        # exceed 1 page)"), so a row is unparseable until they are gone.
        line = " ".join(_PARENTHETICAL.sub(" ", raw).split())
        if not line:
            continue
        bare = _TOC_BARE_COUNT.match(line)
        if bare:
            pending = int(bare.group(1))
            continue
        same = _TOC_SAME_LINE.match(line)
        if same:
            name, n = same.group("name"), int(same.group("n"))
            pending = None
        elif pending is not None and _TOC_LABEL.match(_PARENTHETICAL.sub("", line).strip()):
            name, n = line, pending
            pending = None
        else:
            name = _PARENTHETICAL.sub("", line).strip()
            if not _TOC_LABEL.match(name):
                pending = None
            continue
        name = _PARENTHETICAL.sub("", name).strip(" .")
        if len(name) < 4:
            continue
        rows.append((_sp.resolve_section_key(sections or {}, name), n, name))
    return rows


def _furniture(page_texts: list) -> set:
    """Lines the PDF stamps on most pages — never the author's words.

    Recognised by REPETITION across pages, so no funder's wording is hardcoded.
    On the measured document the stamp is the FIRST line of all 56 pages, so
    without dropping it a three-line probe never reaches the line that actually
    names the section."""
    counts: dict = {}
    for text in page_texts or []:
        for line in {ln.strip() for ln in (text or "").splitlines() if ln.strip()}:
            counts[line] = counts.get(line, 0) + 1
    floor = max(2, int(_FURNITURE_SHARE * len(page_texts or [1])))
    return {ln for ln, c in counts.items() if c >= floor}


def _find_toc(page_texts: list, sections: dict) -> list:
    for text in (page_texts or [])[:8]:
        if _TOC_RE.search(text or ""):
            roster = toc_roster(text, sections)
            if len(roster) >= 3:
                return roster
    return []


def _label_block(probe: str, sections: dict, furniture: set = frozenset()) -> Optional[str]:
    """Which section this block's opening lines name, or None.

    Two tiers, and a block naming TWO sections stays unlabelled — the fold below
    will attach it to its predecessor, which is the conservative outcome.
    """
    lines = [ln.strip() for ln in (probe or "").splitlines()
             if ln.strip() and ln.strip() not in furniture][:_PROBE_LINES]
    if not lines:
        return None
    # Tier 1: a line that IS the section name. Reuses the shared signature, so
    # this inherits `_EQUIVALENT_SECTIONS` for free.
    hits = {k for k, meta in (sections or {}).items()
            for line in lines
            if _sp.section_signature(line) in _sp.section_signatures(k, meta)}
    if len(hits) == 1:
        return hits.pop()
    if hits:
        return None
    # Tier 2: the name appears in the opening lines. LONGEST label wins, so
    # "Budget" cannot beat "Budget and Budget Justification" on a document that
    # contains both.
    flat = " ".join(" ".join(lines).lower().split())
    best, best_len = None, 0
    for k, meta in (sections or {}).items():
        for sig_name in [meta.get("label") or k] + list(meta.get("aliases") or []):
            probe_name = " ".join(str(sig_name).lower().split())
            if len(probe_name) >= 6 and probe_name in flat and len(probe_name) > best_len:
                best, best_len = k, len(probe_name)
    return best


def split(data: bytes, page_texts: list, sections: dict):
    """({section key: span}, report). `({}, report)` whenever anything is unsafe.

    Span offsets index `"\\n".join(page_texts)` — the same string the caller's
    PDF extraction builds — so the caller can rebase them onto its own text.
    """
    report = {"reason": None, "blocks": 0, "labelled": 0, "roster": 0}
    try:
        blocks = page_blocks(data)
        if not blocks:
            report["reason"] = "no usable block structure"
            return {}, report
        report["blocks"] = len(blocks)
        if len(page_texts or []) != (blocks[-1][1] + 1):
            # The two reads disagree about how many pages there are, so every
            # offset below would be wrong. MAX_CHARS truncation lands here, and
            # it is silent otherwise.
            report["reason"] = "page count disagrees with the text extraction"
            return {}, report

        roster = _find_toc(page_texts, sections)
        if not roster:
            # DELIBERATE: n=1 evidence, so require the artefact that produced
            # it. A combined PDF exported from Word carries no NSF TOC and is
            # left to the existing path rather than split on an untested signal.
            report["reason"] = "no NSF table of contents"
            return {}, report
        report["roster"] = len(roster)
        # The roster VALIDATES; it does not gate labelling. Filtering candidate
        # labels through it made the quality of the whole split depend on how
        # many TOC rows happened to parse -- measured, that throttled 29 blocks
        # down to 2 labelled and folded 16 pages into References Cited. Blocks
        # are labelled against the section universe; the roster is consulted
        # afterwards, for page counts and for what is missing.
        wanted = {k for k, _, _ in roster if k}

        furniture = _furniture(page_texts)
        labelled = []
        for first, last in blocks:
            key = _label_block(page_texts[first], sections, furniture)
            labelled.append((key, first, last))
        report["labelled"] = sum(1 for k, _f, _l in labelled if k)

        # ANCHOR THE TWO SECTIONS THAT NEVER NAME THEMSELVES.
        #
        # Measured: every attachment that states its own name is labelled
        # correctly (References Cited, Budget Justification, Facilities). The
        # two that are not are Project Summary and Project Description, and the
        # reason is the same for both -- this author's LaTeX running title reads
        # "MPS/DMS/Algebra and Number Theory program", the funding PROGRAM, not
        # the section. Nothing on either page names the part of the proposal it
        # is, so no text rule can recover them.
        #
        # What can: NSF fixes the order of the front matter -- Cover Sheet,
        # Project Summary, Table of Contents, Project Description -- and the
        # Table of Contents page DOES identify itself, because NSF generates it.
        # So the block before it is the Project Summary and the block after it
        # is the Project Description, positionally, with no guessing.
        #
        # Guarded by the roster: the anchor is taken only when the block's page
        # count matches what NSF's own table of contents says that section
        # should be. On the measured document that is 1 page and 15 pages, both
        # exact. A mismatch means the assumption does not hold for this document
        # and the anchor is declined rather than forced.
        toc_at = next((i for i, (_k, f, _l) in enumerate(labelled)
                       if _TOC_RE.search(page_texts[f] or "")), None)
        if toc_at is not None:
            for offset, key in ((-1, "project_summary"), (1, "project_description")):
                i = toc_at + offset
                if not (0 <= i < len(labelled)) or labelled[i][0] is not None:
                    continue
                if key not in (sections or {}) or key in {k for k, _f, _l in labelled}:
                    continue
                _k, first, last = labelled[i]
                want = next((n for rk, n, _ in roster if rk == key), None)
                if want is None or abs((last - first + 1) - want) <= _PAGE_SLOP:
                    labelled[i] = (key, first, last)
            report["labelled"] = sum(1 for k, _f, _l in labelled if k)

        # FOLD: an unlabelled block joins the preceding labelled one. This is
        # what turns 29 atomic blocks into ~11 sections — the individual support
        # letters into Supplementary Documents, the four budget form pages into
        # Budget. Blocks before the FIRST labelled block stay unassigned and are
        # left for the locate stage.
        # A SECTION THAT HAS MET ITS STATED LENGTH DOES NOT ABSORB MORE.
        #
        # Folding every unlabelled block into its predecessor is wrong wherever
        # a run of unnamed pages belongs to the section AFTER it. Measured: the
        # four NSF budget SUMMARY form pages sit between References Cited and
        # the Budget Justification and name neither, so a backward-only fold
        # gave References Cited 10 pages against the 6 its own table of contents
        # states. The roster is what settles it — References Cited is complete
        # at 6, so the forms belong forward, to Budget, whose stated 10 pages
        # they then complete exactly.
        toc_pages_for = {k: n for k, n, _ in roster if k}
        merged: dict = {}
        order: list = []
        current = None
        pending_forward: list = []

        def _full(key):
            want = toc_pages_for.get(key)
            if want is None or key not in merged:
                return False
            first, last = merged[key]
            return (last - first + 1) >= want

        for key, first, last in labelled:
            if key:
                if key not in merged:
                    merged[key] = [first, last]
                    order.append(key)
                else:
                    merged[key][1] = max(merged[key][1], last)
                # Unlabelled blocks held back because the previous section was
                # already complete belong to THIS one.
                if pending_forward:
                    merged[key][0] = min(merged[key][0], pending_forward[0])
                    pending_forward = []
                current = key
            elif current is not None and not _full(current):
                merged[current][1] = max(merged[current][1], last)
            elif current is not None:
                pending_forward.append(first)
        # A TRAILING RUN IS LEFT UNASSIGNED, deliberately. Dumping it into the
        # last labelled section is how Facilities — one page by its own table of
        # contents — ended up claiming twenty, swallowing the biosketch, current
        # and pending support, and every letter. Those pages belong to sections
        # NSF folds into one upload slot, several of which this solicitation
        # never names at all, so the honest outcome is to leave them for the
        # locate stage. The single-section elimination rule below can still
        # claim them when the roster says exactly one section is unaccounted
        # for and the run is the right length.

        missing = wanted - set(merged)
        if missing:
            # ELIMINATION, allowed ONCE: exactly one roster entry unaccounted
            # for, and a single contiguous unassigned run at the END of the
            # document whose length matches it. That is the measured Letters
            # block, which carries no name of any kind.
            # ANCHORED ON A BLOCK BOUNDARY, never on "everything left over".
            #
            # The unassigned tail is not all one section. Measured on the awarded
            # package: after the six labelled sections, 19 pages remain, but the
            # table of contents says Supplementary Documents is 9 — the other 10
            # are the biographical sketch and current-and-pending support, which
            # NSF folds into an upload slot this solicitation never names, so
            # they belong to no section in this universe at all. Claiming the
            # whole tail would file ten pages of the wrong document under a
            # section and judge its rules against them.
            #
            # The block boundaries are exact (they carry every true section start
            # with none straddling), and the roster says how long the section
            # should be. So look for the block start whose distance to the end
            # of the document matches that length, and require it to be UNIQUE —
            # two candidates mean the length does not identify the section and
            # guessing between them is not worth a wrong verdict.
            tail_start = (max(e for _s, e in merged.values()) + 1) if merged else 0
            last_page = blocks[-1][1]
            want_pages = next((n for k, n, _ in roster if k in missing), None)
            anchors = [f for f, _l in blocks
                       if f >= tail_start
                       and want_pages is not None
                       and abs((last_page - f + 1) - want_pages) <= _PAGE_SLOP]
            if len(missing) == 1 and merged and len(anchors) == 1:
                key = missing.pop()
                merged[key] = [anchors[0], last_page]
                order.append(key)
            else:
                # A SECTION WE CANNOT ANCHOR IS LEFT OUT, NOT A REASON TO THROW
                # THE SPLIT AWAY. This bailed outright at first, and making the
                # roster resolve one more section then destroyed a good split:
                # six correctly identified sections became zero because a
                # SEVENTH could not be placed. The fold is not what failed —
                # each identified section still matches its own stated page
                # count (checked below), and the unplaceable one simply falls
                # through to the locate stage, which is where it was before.
                #
                # Measured on the awarded package: Supplementary Documents is 9
                # pages by the table of contents, but the 19 unassigned pages
                # after the last identified section hold the biographical sketch
                # and current-and-pending support — sections NSF folds into a
                # slot this solicitation never names — AND the document ends
                # with reviewer lists that belong to no section at all. So the
                # run is neither contiguous nor at the end, three block starts
                # fit the length equally well, and the uniqueness guard above
                # correctly refuses to pick one. Reported, never guessed.
                report["unplaced"] = sorted(missing)
                print(f"[PDF-SECTIONS] left to the locate stage: {sorted(missing)}")

        toc_pages = {k: n for k, n, _ in roster if k}
        for key, (first, last) in merged.items():
            want = toc_pages.get(key)
            if want is not None and abs((last - first + 1) - want) > _PAGE_SLOP:
                report["reason"] = (f"{key} spans {last - first + 1} pages, "
                                    f"the table of contents says {want}")
                return {}, report

        if len(merged) < MIN_SECTIONS:
            report["reason"] = f"only {len(merged)} sections survived"
            return {}, report
        covered = sum(l - f + 1 for f, l in merged.values())
        if covered < MIN_COVERAGE * len(page_texts):
            report["reason"] = f"only {covered} of {len(page_texts)} pages assigned"
            return {}, report

        # Offsets into "\n".join(page_texts), computed the same way the caller
        # builds that string so the two cannot drift.
        starts = []
        pos = 0
        for t in page_texts:
            starts.append(pos)
            pos += len(t) + 1
        full = "\n".join(page_texts)
        spans = {}
        for key, (first, last) in merged.items():
            s = starts[first]
            e = starts[last] + len(page_texts[last])
            spans[key] = {"text": full[s:e], "start": s, "end": e,
                          "pages": last - first + 1,
                          "marker": (page_texts[first].strip().splitlines() or [key])[0][:120]}
        return spans, report
    except Exception as exc:                     # golden rule 3
        print(f"[PDF-SECTIONS] split failed: {exc}")
        report["reason"] = f"exception: {exc}"
        return {}, report
