#!/usr/bin/env python3
"""Build the NSF rules reference PDF from the LIVE rule table.

WHY THIS GENERATES RATHER THAN TRANSCRIBES
------------------------------------------
A hand-written list of "the rules the app checks" is a second copy of the truth,
and second copies drift. This reads `services.rulebook_baseline.RULES` directly,
so the PDF cannot claim a rule the engine does not enforce, and cannot miss one
it does. Add a rule to the table, re-run this, and the document follows.

It also reads each row's `kind` to say whether a rule is decided by CODE or by
the model, and its `scored` flag to mark the advisory ones -- both facts a reader
needs and neither of which a prose summary would keep in step.

USAGE
    cd backend && python3 ../scripts/make_rules_pdf.py
    # writes docs/ORA_Navigator_NSF_Rules.html, then prints it to PDF with
    # headless Chrome (same pipeline as docs/build_guide.py).
"""
from __future__ import annotations

import html
import os
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import rulebook_baseline as rb  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT_HTML = os.path.join(ROOT, "docs", "ORA_Navigator_NSF_Rules.html")
OUT_PDF = os.path.join(ROOT, "docs", "ORA_Navigator_NSF_Rules.pdf")

CSS = """
@page { size: Letter; margin: 0.75in 0.7in; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
       color: #1f2a37; font-size: 10.5pt; line-height: 1.5; margin: 0; }
h1 { font-size: 22pt; margin: 0 0 4px; color: #0f2747; letter-spacing: -.3px; }
.sub { color: #5b6777; font-size: 10pt; margin: 0 0 18px; }
h2 { font-size: 13pt; margin: 26px 0 2px; color: #0f2747;
     border-bottom: 2px solid #c9d6ea; padding-bottom: 4px; page-break-after: avoid; }
h2 .cnt { float: right; font-size: 9pt; color: #96a0b0; font-weight: 400; }
.lede { background: #f3f6fb; border-left: 3px solid #c9d6ea; padding: 10px 14px;
        margin: 0 0 18px; font-size: 10pt; }
.note { background: #fff7ec; border-left: 3px solid #f7941e; padding: 10px 14px;
        margin: 16px 0; font-size: 9.5pt; color: #5c3d05; }
.rule { padding: 9px 0 9px 0; border-bottom: 1px solid #e6eaf0; page-break-inside: avoid; }
.rule:last-child { border-bottom: none; }
.hdr { display: flex; gap: 8px; align-items: baseline; }
.lbl { font-weight: 600; color: #16324f; flex: 1; }
.tag { font-size: 7.5pt; text-transform: uppercase; letter-spacing: .5px;
       padding: 2px 6px; border-radius: 3px; white-space: nowrap; }
.code { background: #e7f0e9; color: #24603a; }
.ai   { background: #eceaf6; color: #453a75; }
.adv  { background: #fff7ec; color: #9a5b00; }
blockquote { margin: 6px 0 0; padding: 6px 0 6px 12px; border-left: 3px solid #d9dee6;
             color: #3d4b5c; font-size: 9.5pt; font-style: italic; }
blockquote cite { display: block; font-style: normal; font-size: 8.5pt;
                  color: #96a0b0; margin-top: 3px; }
.derived { color: #5b6777; font-size: 9.5pt; margin: 6px 0 0; }
.why { color: #5b6777; font-size: 9pt; margin: 4px 0 0; }
footer { margin-top: 28px; padding-top: 10px; border-top: 1px solid #d9dee6;
         color: #96a0b0; font-size: 8.5pt; }
"""


def _is_quote(row: dict) -> bool:
    """A row's `source` is either NSF's own sentence or an honest derived line.

    Derived lines are written by us and must NOT be presented as a quotation --
    that is the grounding rule this whole product rests on."""
    return not row["source"].lstrip().lower().startswith("derived")


def build_html() -> str:
    parts = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>NSF rules and amendments — ORA Navigator</title>",
        f"<style>{CSS}</style>",
        "<h1>NSF rules &amp; amendments</h1>",
        f"<p class='sub'>Generated from the live rule table on {date.today():%d %B %Y} "
        "&mdash; <code>backend/services/rulebook_baseline.py</code></p>",
        "<div class='lede'><strong>These are NSF's rules, not ours.</strong> Each one is "
        "taken from the Content Instructions NSF publishes on the Research.gov page where "
        "that document is uploaded &mdash; the PAPPG already distilled by NSF into the "
        "handful of rules each section is actually checked against. Every rule below shows "
        "NSF's own sentence so it can be verified against the source.</div>",
        "<div class='note'><strong>How to read the tags.</strong> "
        "<span class='tag code'>code</span> means the app decides it with no AI involved &mdash; "
        "the check is a yes/no fact about the text. "
        "<span class='tag ai'>reviewer</span> means a model judges it and must quote your draft "
        "to support what it says. "
        "<span class='tag adv'>advisory</span> means the rule is conditional in NSF's own wording, "
        "so it is reported but never counted against you.</div>",
    ]

    for book, rows in rb.RULES.items():
        by_section: dict = {}
        for r in rows:
            by_section.setdefault(r["section"], []).append(r)

        parts.append(f"<h2>{html.escape(book)}<span class='cnt'>{len(rows)} rules</span></h2>")
        for section, srows in by_section.items():
            parts.append(f"<h2 style='font-size:11.5pt;border-bottom:1px solid #e6eaf0;"
                         f"margin-top:18px'>{html.escape(rb.section_label(section))}</h2>")
            for r in srows:
                tag = ("<span class='tag code'>code</span>" if r["kind"] == "deterministic"
                       else "<span class='tag ai'>reviewer</span>")
                if not r["scored"]:
                    tag += " <span class='tag adv'>advisory</span>"
                parts.append("<div class='rule'>")
                parts.append(f"<div class='hdr'><span class='lbl'>{html.escape(r['label'])}"
                             f"</span>{tag}</div>")
                if _is_quote(r):
                    parts.append(
                        f"<blockquote>&ldquo;{html.escape(r['source'])}&rdquo;"
                        f"<cite>NSF &mdash; {html.escape(r['source_url'])}</cite></blockquote>")
                else:
                    parts.append(f"<p class='derived'>{html.escape(r['source'])}</p>")
                if r.get("why"):
                    parts.append(f"<p class='why'>{html.escape(r['why'])}</p>")
                parts.append("</div>")

    # ── amendments ─────────────────────────────────────────────────────────
    # Rendered in their OWN part, clearly separated from the rules, because
    # nothing here is enforced. Presenting an amendment beside a rule the app
    # checks would imply the app checks it.
    stale = rb.amendments_affecting_rules()
    parts.append("<h2 style='page-break-before:always'>Changes since PAPPG 24-1"
                 f"<span class='cnt'>{len(rb.AMENDMENTS)} amendments</span></h2>")
    parts.append(
        "<div class='lede'><strong>None of these are checked by the app.</strong> NSF patches "
        "the PAPPG between editions with supplements, each naming the exact section it amends. "
        "They are recorded here so a reader knows which document a rule comes from, and what has "
        "moved since the base version was published. Anyone reading only PAPPG 24-1 is reading "
        "rules that are partly out of date.</div>")
    parts.append(
        "<div class='note'><strong>Do the 14 rules above need review?</strong> "
        + ("<strong>No.</strong> No amendment touches the Project Summary, Project Description, "
           "References Cited, Facilities sections or the formatting requirements, so the rules "
           "above are current." if not stale else
           f"<strong>Yes &mdash; {len(stale)} amendment(s) affect a rule we enforce.</strong>")
        + "</div>")

    by_doc: dict = {}
    for a in rb.AMENDMENTS:
        by_doc.setdefault(a["doc"], []).append(a)
    for doc, items in by_doc.items():
        parts.append(f"<h2 style='font-size:11.5pt;border-bottom:1px solid #e6eaf0;"
                     f"margin-top:18px'>{html.escape(doc)}</h2>")
        for a in sorted(items, key=lambda x: x["effective"]):
            parts.append("<div class='rule'>")
            parts.append(f"<div class='hdr'><span class='lbl'>{html.escape(a['title'])}</span>"
                         f"<span class='tag adv'>from {html.escape(a['effective'])}</span></div>")
            parts.append(f"<p class='derived'>{html.escape(a['detail'])}</p>")
            parts.append(f"<p class='why'>Amends PAPPG section {html.escape(a['amends'])}</p>")
            parts.append("</div>")

    parts.append("<h2>Read the source yourself</h2>")
    parts.append(
        "<div class='lede'>Everything above is public and free &mdash; no login. "
        "The Research.gov Content Instructions the rules come from are NOT public; they sit "
        "behind an NSF sign-in on each document's upload page.</div>")
    for label, url in [
        ("PAPPG 24-1, the whole rulebook (PDF)", "https://nsf-gov-resources.nsf.gov/files/nsf24_1.pdf"),
        ("Chapter II &mdash; Proposal Preparation (the one that matters)",
         "https://www.nsf.gov/policies/pappg/24-1/ch-2-proposal-preparation"),
        ("Supplement 1", "https://nsf.gov/policies/document/pappg24-1-supplement-1"),
        ("Supplement 2", "https://nsf.gov/policies/document/pappg24-1-supplement-2"),
    ]:
        parts.append(f"<div class='rule'><div class='hdr'><span class='lbl'>{label}</span></div>"
                     f"<p class='why'>{html.escape(url)}</p></div>")

    covered = ", ".join(rb.section_label(s["key"]) for s in rb.sections_offered("the PAPPG"))
    parts.append(
        "<div class='note'><strong>What this does NOT cover.</strong> Only these sections are "
        f"checked against the PAPPG: {html.escape(covered)}. The PAPPG governs much more &mdash; "
        "biographical sketches, current and pending support, the budget justification, data "
        "management plans. Those are reported as <em>not checked here</em> rather than passed. "
        "Rules that belong to your specific solicitation are read from that document separately "
        "and checked alongside these.</div>")
    parts.append("<footer>ORA Navigator &mdash; Morgan State University Office of Research "
                 "Administration. Generated from code; if a rule changes in the app it changes "
                 "here on the next build.</footer>")
    return "\n".join(parts)


def main() -> None:
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(build_html())
    print(f"wrote {OUT_HTML}")

    if not os.path.exists(CHROME):
        print("Chrome not found; HTML written, skipping PDF.")
        return
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={OUT_PDF}", f"file://{OUT_HTML}"],
                   check=True, capture_output=True)
    print(f"wrote {OUT_PDF} ({os.path.getsize(OUT_PDF):,} bytes)")


if __name__ == "__main__":
    main()
