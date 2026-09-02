#!/usr/bin/env python3
"""Build "What each section is checked against" for ONE proposal, from the LIVE engine.

WHY THIS ASKS THE ENGINE RATHER THAN READING THE RULE TABLE
-----------------------------------------------------------
`make_rules_pdf.py` documents the whole NSF rulebook. This documents what ONE
proposal is actually checked against, which is a different and smaller thing:
the rulebook's BASIC rows plus that solicitation's own requirements, filtered by
the same `sections_offered_for` the picker uses.

It gets that list by calling `draft_review.review_section(..., use_ai=False)` --
the real entry point, on a placeholder draft, with the model switched off. So
the document cannot list a rule the engine does not evaluate, and cannot miss one
it does. Reading `rulebook_baseline.RULES` directly would be a second copy of the
truth, and the previous edition of this PDF drifted for exactly that reason:
generated 2026-08-28, it showed 6 rules for Project Summary and 4 for Facilities
where the engine had 7 and 6, because two rules had since been SPLIT
("objectives and methods" into one rule each; "internal and external resources,
physical and personnel" into two). That split is not cosmetic -- combined, a
draft stating objectives but not methods scored a single `partial`; split, it
scores full marks on one row and zero on the other.

USAGE
    cd backend && python3 ../scripts/make_section_rules_pdf.py --submission 8
    # writes docs/ORA_Section_Rules_<id>.html and .pdf
"""
from __future__ import annotations

import argparse
import html
import os
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(os.path.join(ROOT, "backend"))

from db import SessionLocal                                    # noqa: E402
import models                                                  # noqa: E402
from services import draft_review as dr                        # noqa: E402
from services import proposals_service as ps                   # noqa: E402
from services import rulebook_baseline as rb                   # noqa: E402
from services import solicitation_profile as sp                # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Reused verbatim from make_rules_pdf.py so the two documents look like one set.
from importlib.machinery import SourceFileLoader               # noqa: E402
CSS = SourceFileLoader("_mrp", os.path.join(HERE, "make_rules_pdf.py")).load_module().CSS

RULEBOOK = "the PAPPG"
PLACEHOLDER = "Overview\nplaceholder\n\nIntellectual Merit\nplaceholder\n\nBroader Impacts\nplaceholder\n"


def _is_quote(text: str) -> bool:
    """NSF's own sentence, or an honest derived line we wrote.

    A derived line must never be dressed as a quotation -- that is the grounding
    rule the whole product rests on."""
    return not (text or "").lstrip().lower().startswith("derived")


def build_html(sub, profile) -> str:
    sol_id = (profile or {}).get("id") or "this solicitation"
    offered = sp.sections_offered_for(profile, RULEBOOK)

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>What each section is checked against</title>",
        f"<style>{CSS}</style>",
        "<h1>What each section is checked against</h1>",
        f"<p class='sub'>ORA Navigator &mdash; Check a Section. Every rule below carries the "
        f"sentence it came from.<br>Proposal #{sub.id} &middot; {html.escape(str(sol_id))} "
        f"&middot; generated {date.today():%d %B %Y}</p>",
        "<div class='lede'><strong>Two sources, and only two.</strong> Rules marked "
        f"<em>{html.escape(str(sol_id))}</em> were read out of your solicitation &mdash; each one "
        "had to be quotable from that document or it was dropped. Rules marked "
        "<em>NSF baseline</em> are NSF's standing requirements, taken from the Content "
        "Instructions screens Research.gov shows when you upload each file; they apply only "
        "because this solicitation cites the PAPPG. "
        "<span class='tag code'>code</span> means a program decides it &mdash; no judgement, no "
        "variation. <span class='tag ai'>reviewer</span> means the model reads your draft and "
        "must quote it back. These rules check that something is <em>present</em>, not that it "
        "is good.</div>",
    ]

    total = 0
    for entry in offered:
        key = entry.get("key")
        label = entry.get("label") or key
        result = dr.review_section(PLACEHOLDER, section=key, rulebook=RULEBOOK,
                                   profile=profile, use_ai=False)
        rows = result.get("findings", [])
        total += len(rows)
        n_sol = sum(1 for r in rows if not r.get("rulebook"))
        n_base = len(rows) - n_sol
        parts.append(f"<h2>{html.escape(label)}<span class='cnt'>{len(rows)} rules</span></h2>")
        parts.append(f"<p class='why'>{n_sol} from {html.escape(str(sol_id))}, "
                     f"{n_base} from the NSF baseline</p>")
        for r in rows:
            tag = ("<span class='tag code'>code</span>" if r.get("source") == "check"
                   else "<span class='tag ai'>reviewer</span>")
            if not r.get("scored"):
                tag += " <span class='tag adv'>advisory</span>"
            src = "NSF baseline" if r.get("rulebook") else str(sol_id)
            parts.append("<div class='rule'>")
            parts.append(f"<div class='hdr'><span class='lbl'>{html.escape(r['label'])}</span>"
                         f"{tag}</div>")
            says = r.get("solicitation_says") or ""
            if says and _is_quote(says):
                parts.append(f"<blockquote>&ldquo;{html.escape(says)}&rdquo;"
                             f"<cite>{html.escape(src)}</cite></blockquote>")
            elif says:
                parts.append(f"<p class='derived'>{html.escape(says)} "
                             f"&mdash; <em>{html.escape(src)}</em></p>")
            if r.get("why"):
                parts.append(f"<p class='why'>{html.escape(r['why'])}</p>")
            parts.append("</div>")

    parts.append("<div class='note'><strong>Sections not offered.</strong> A part of the "
                 "proposal appears in Check a Section only when it has rules to check. Anything "
                 "missing from this document either has no rule in the baseline or none in your "
                 "solicitation &mdash; it is not being checked silently.</div>")
    parts.append(f"<footer>ORA Navigator &mdash; Morgan State University Office of Research "
                 f"Administration. {total} rules across {len(offered)} sections, read from the "
                 f"live engine (<code>draft_review.review_section</code>), so this document "
                 f"cannot claim a rule the app does not check.</footer>")
    return "\n".join(parts)


def _label_for(key, profile) -> str:
    """The section's own name: the profile's label first, then the rulebook's."""
    if key in (None, "", "None"):
        return "Whole proposal"
    secs = (profile or {}).get("sections") or {}
    entry = secs.get(key)
    if isinstance(entry, dict) and entry.get("label"):
        return entry["label"]
    return rb.section_label(key)


def build_whole_draft_html(sub, profile) -> str:
    """Every rule a WHOLE Draft Review checks, read from the live engine.

    `dr._basics_and_solicitation` is the exact narrowing `review_draft` performs
    at line 1261 -- the solicitation's own rows plus the rulebook's BASIC tier --
    so this cannot list a rule the review does not assemble, nor miss one it does.
    No model is called: the rule SET is deterministic even though the verdicts
    are not.
    """
    sol_id = (profile or {}).get("id") or "this solicitation"
    narrowed = dr._basics_and_solicitation(profile)
    reqs = narrowed["requirements"]

    scored = [r for r in reqs if r.get("scored")]
    advisory = [r for r in reqs if not r.get("scored")]
    prohib = [r for r in reqs if r.get("flag_if_present")]
    by_code = [r for r in scored if r["kind"] == "deterministic"]

    groups = {}
    for r in reqs:
        groups.setdefault(r.get("section"), []).append(r)
    # Biggest first: a reader wants to know where the weight is.
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>What the whole draft is checked against</title>",
        f"<style>{CSS}</style>",
        "<h1>What the whole draft is checked against</h1>",
        f"<p class='sub'>ORA Navigator &mdash; Draft Review (the whole package, not one "
        f"section).<br>Proposal #{sub.id} &middot; {html.escape(str(sol_id))} &middot; "
        f"generated {date.today():%d %B %Y}</p>",

        "<div class='lede'><strong>Two sources, and only two.</strong> Rules marked "
        f"<em>{html.escape(str(sol_id))}</em> were read out of your solicitation &mdash; each "
        "had to be quotable from that document or it was dropped. Rules marked "
        "<em>NSF baseline</em> are NSF's standing requirements from the PAPPG, and apply only "
        "because this solicitation cites it. <span class='tag code'>code</span> means a "
        "program decides it &mdash; no judgement, no variation. "
        "<span class='tag ai'>reviewer</span> means the model reads your draft and must quote "
        "it back. <span class='tag adv'>advisory</span> rules are shown but never counted.</div>",

        "<h2>The numbers<span class='cnt'>%d rules</span></h2>" % len(reqs),
        "<div class='rule'><div class='hdr'><span class='lbl'>Scored &mdash; these produce the "
        "percentage</span><span class='tag code'>%d</span></div>"
        "<p class='why'>%d from %s, %d from the NSF baseline. %d decided by code, %d judged by "
        "the model.</p></div>" % (
            len(scored),
            sum(1 for r in scored if not r.get("rulebook")), html.escape(str(sol_id)),
            sum(1 for r in scored if r.get("rulebook")),
            len(by_code), len(scored) - len(by_code)),
        "<div class='rule'><div class='hdr'><span class='lbl'>Advisory &mdash; shown, never "
        "counted</span><span class='tag adv'>%d</span></div>"
        "<p class='why'>Conditional in NSF's or the funder's own wording "
        "(&ldquo;if applicable&rdquo;), so a proposal they do not apply to is not "
        "penalised.</p></div>" % len(advisory),
        "<div class='rule'><div class='hdr'><span class='lbl'>Prohibitions &mdash; absence is a "
        "PASS</span><span class='tag ai'>%d</span></div>"
        "<p class='why'>For these the draft must NOT do the thing. A draft that never mentions "
        "it is compliant, not incomplete.</p></div>" % len(prohib),

        "<div class='note'><strong>%d is a ceiling, not the divisor.</strong> A rule leaves the "
        "score when it cannot be judged &mdash; the section was not found in what you uploaded, "
        "the rule is decided at submission rather than in the document, it is handed to a "
        "rulebook we do not read, or the reviewer returned nothing for it. So the denominator is "
        "decided per run and is normally lower than %d. A percentage here measures completeness "
        "against these rules; it is not a judgement of the science and not a prediction of "
        "funding.</div>" % (len(scored), len(scored)),
    ]

    # The two problems the numbers themselves reveal. Better stated than tidied away.
    stray = [k for k, rows in groups.items()
             if k and k not in {r["section"] for r in rb.RULES.get(RULEBOOK, [])}]
    if stray:
        names = ", ".join(html.escape(_label_for(k, profile)) for k in stray)
        parts.append(
            "<div class='note'><strong>Known gaps in this list, stated rather than hidden.</strong> "
            "These sections carry rules from your solicitation and <em>none</em> from NSF, because "
            "their keys do not match the rulebook's own: <em>%s</em>. NSF does state rules for "
            "some of them &mdash; they are simply filed under a different name and never merge. "
            "Separately, a Letter of Intent is a distinct submission with an earlier deadline, so "
            "its rules are counted here in a package score they do not really belong to.</div>"
            % names)

    for key, rows in ordered:
        label = _label_for(key, profile)
        n_sol = sum(1 for r in rows if not r.get("rulebook"))
        parts.append(f"<h2>{html.escape(label)}<span class='cnt'>{len(rows)} rules</span></h2>")
        parts.append(f"<p class='why'>{n_sol} from {html.escape(str(sol_id))}, "
                     f"{len(rows) - n_sol} from the NSF baseline</p>")
        for r in rows:
            tag = ("<span class='tag code'>code</span>" if r["kind"] == "deterministic"
                   else "<span class='tag ai'>reviewer</span>")
            if not r.get("scored"):
                tag += " <span class='tag adv'>advisory</span>"
            if r.get("flag_if_present"):
                tag += " <span class='tag adv'>prohibition</span>"
            src = "NSF baseline" if r.get("rulebook") else str(sol_id)
            parts.append("<div class='rule'>")
            parts.append(f"<div class='hdr'><span class='lbl'>{html.escape(r['label'])}</span>"
                         f"{tag}</div>")
            says = r.get("source") or ""
            if says and _is_quote(says):
                parts.append(f"<blockquote>&ldquo;{html.escape(says)}&rdquo;"
                             f"<cite>{html.escape(src)}</cite></blockquote>")
            elif says:
                parts.append(f"<p class='derived'>{html.escape(says)} "
                             f"&mdash; <em>{html.escape(src)}</em></p>")
            if r.get("why"):
                parts.append(f"<p class='why'>{html.escape(r['why'])}</p>")
            parts.append("</div>")

    parts.append(f"<footer>ORA Navigator &mdash; Morgan State University Office of Research "
                 f"Administration. {len(reqs)} rules across {len(groups)} sections, read from "
                 f"the live engine (<code>draft_review._basics_and_solicitation</code>), so this "
                 f"document cannot claim a rule the app does not check.</footer>")
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission", type=int, required=True)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "docs"))
    ap.add_argument("--whole-draft", action="store_true",
                    help="list what a WHOLE Draft Review checks, not the picker's sections")
    args = ap.parse_args()

    db = SessionLocal()
    sub = db.get(models.Submission, args.submission)
    if sub is None:
        raise SystemExit(f"no submission {args.submission}")
    profile = ps.load_solicitation_profile(sub)
    if not profile:
        raise SystemExit(f"submission {args.submission} has no solicitation attached")
    doc = (build_whole_draft_html(sub, profile) if args.whole_draft
           else build_html(sub, profile))
    db.close()

    os.makedirs(args.out_dir, exist_ok=True)
    stem = ("ORA_Draft_Review_Rules" if args.whole_draft else "ORA_Section_Rules")
    out_html = os.path.join(args.out_dir, f"{stem}_{args.submission}.html")
    out_pdf = os.path.join(args.out_dir, f"{stem}_{args.submission}.pdf")
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"wrote {out_html}")
    if not os.path.exists(CHROME):
        print("Chrome not found; HTML written, skipping PDF.")
        return
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={out_pdf}", f"file://{out_html}"],
                   check=True, capture_output=True)
    print(f"wrote {out_pdf} ({os.path.getsize(out_pdf):,} bytes)")


if __name__ == "__main__":
    main()
