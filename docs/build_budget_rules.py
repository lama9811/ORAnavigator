#!/usr/bin/env python3
"""Build the Budget Rules Reference (HTML, then PDF via headless Chrome).

Reads every rate, threshold and rule from the RUNNING CODE rather than a
hand-written list, so the document can never drift from what the tool does.
Re-run it whenever Morgan renegotiates a rate or a rule is added.

    cd backend && ../.venv/bin/python ../docs/build_budget_rules.py
    # then, for the PDF:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
        --headless --disable-gpu --no-pdf-header-footer \\
        --print-to-pdf="$HOME/Desktop/Budget-Rules-Reference.pdf" \\
        "file://$(pwd)/../docs/Budget-Rules-Reference.html"

Sources: services/budget_helper.py, services/nsf_budget.py,
         services/nsf_budget_rules.py
"""
import datetime
import html
import os
import re
import sys

# Run from backend/ so `services` imports resolve.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from services import budget_helper as bh          # noqa: E402
from services import nsf_budget as nb              # noqa: E402
from services import nsf_budget_rules as nr        # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "Budget-Rules-Reference.html")

pct = lambda r: f"{r * 100:g}%"                    # noqa: E731
money = lambda n: f"${n:,.0f}"                     # noqa: E731


# ── markdown assembly ─────────────────────────────────────────────────────
def build_markdown():
    D = {
        "fa_year": bh.DEFAULT_FA_YEAR, "fa_key": bh.DEFAULT_FA_KEY,
        "subaward_cap": bh.SUBAWARD_MTDC_CAP,
        "capitalization": nb.DEFAULT_CAPITALIZATION,
        "itemise": nb.FORM_ITEMISE_THRESHOLD,
        "escalation": nb.DEFAULT_ESCALATION_PCT,
        "max_senior_months": nb.MAX_SENIOR_MONTHS,
        "basis": nb.MONTHS_PER_BASIS,
    }
    L = []
    w = L.append

    w("# Budget Rules Reference")
    w("")
    w("**ORA Navigator — NSF Form 1030 budget template**")
    w("")
    w(f"Generated {datetime.date.today().isoformat()} directly from the running code "
      f"(`services/budget_helper.py`, `services/nsf_budget.py`, "
      f"`services/nsf_budget_rules.py`). Every figure below is the value the tool "
      f"actually uses — this page cannot drift from the software.")
    w("")
    w("---")
    w("")
    w("## How to read this")
    w("")
    w("The tool separates two very different things.")
    w("")
    w("**Math** is what the tool computes for you. You supply the facts — a salary, a "
      "number of months, a dollar amount — and every total, the F&A calculation, and the "
      "year-by-year rollup are worked out by code. No AI touches a number.")
    w("")
    w("**Checks** are advice. The tool reads your finished budget and flags things NSF is "
      "likely to question. There are two levels, and **neither one blocks you** — you can "
      "always keep typing, save, and export:")
    w("")
    w("- **Warning** — a rule looks broken. Shown in red.")
    w("- **Notice** — a requirement you have to satisfy somewhere else, usually in the "
      "written justification or a separate document. Shown in grey.")
    w("")
    w("---")
    w("")
    w("## Part 1 — The numbers built in")
    w("")
    w("### Morgan State F&A (indirect cost) rates")
    w("")
    w("From the knowledge base file `pre_award_fanda_cost_rates`. "
      "**Organized Research (On-Campus) for FY 2025–2026 is the default.**")
    w("")
    years = sorted(bh.FA_RATES, reverse=True)
    w("| Rate type | " + " | ".join(y.replace("fy_", "FY ").replace("_", "–")
                                    for y in years) + " |")
    w("|---" * (len(years) + 1) + "|")
    for key in bh.FA_RATES[years[0]]:
        label = bh.FA_RATES[years[0]][key][0]
        star = " *(default)*" if key == D["fa_key"] else ""
        cells = []
        for i, y in enumerate(years):
            r = bh.FA_RATES[y].get(key)
            v = pct(r[1]) if r else "—"
            cells.append(f"**{v}**" if i == 0 else v)
        w(f"| {label}{star} | " + " | ".join(cells) + " |")
    w("")
    w("### Morgan State fringe benefit rates")
    w("")
    w("From `pre_award_fringe_benefit_rate`. The NSF form has only one fringe box, but "
      "these rates differ — so the tool applies the right rate to each person and adds "
      "them up.")
    w("")
    w("| Category | Rate |")
    w("|---|---|")
    for _key, (label, rate) in bh.FRINGE_RATES.items():
        w(f"| {label} | **{pct(rate)}** |")
    w("")
    w("### Thresholds and defaults")
    w("")
    w("| What | Value | Why |")
    w("|---|---|---|")
    w(f"| Subaward F&A limit | **{money(D['subaward_cap'])}** | Only the first "
      f"{money(D['subaward_cap'])} of *each* subaward carries F&A (2 CFR 200.1) |")
    w(f"| Equipment capitalization level | **{money(D['capitalization'])}** | Below this it "
      f"is supplies, not equipment — and supplies *do* carry F&A (PAPPG II.D.2.f(iii)) |")
    w(f"| Equipment itemisation | **{money(D['itemise'])}** | Form 1030 line D asks you to "
      f"list each item above this by name |")
    w(f"| Senior salary limit | **{D['max_senior_months']:g} months/year** | NSF's cap per "
      f"senior person (PAPPG II.D.2.f(i)(a)) |")
    w(f"| Salary escalation | **{D['escalation']:g}%** per year | Applied to salaries only "
      f"when you add a year; editable |")
    w(f"| 9-month appointment | salary ÷ **{D['basis']['academic_9']:g}** | Monthly rate for "
      f"academic-year faculty |")
    w(f"| 12-month appointment | salary ÷ **{D['basis']['calendar_12']:g}** | Monthly rate "
      f"for calendar-year staff |")
    w("")
    w("---")
    w("")
    w("## Part 2 — How the totals are worked out")
    w("")
    w("### Salary from person-months")
    w("")
    w("NSF budgets effort in months, not percentages.")
    w("")
    w("```")
    w(f"monthly rate = base salary / {D['basis']['academic_9']:g}"
      f"    ({D['basis']['academic_9']:g}-month academic appointment)")
    w(f"monthly rate = base salary / {D['basis']['calendar_12']:g}"
      f"   ({D['basis']['calendar_12']:g}-month calendar appointment)")
    w("")
    w("salary = (CAL months + ACAD months + SUMR months) x monthly rate")
    w("```")
    w("")
    w("*Example:* a $90,000 academic-year salary is $10,000/month. Two summer months = "
      "**$20,000**, which is 22% effort.")
    w("")
    w("### Fringe")
    w("")
    w("Each person's fringe is their own rate times their own salary. Line C is the sum. A "
      "faculty member at 42% and a graduate student at 9% never share a rate.")
    w("")
    w("### The line rollup")
    w("")
    w("```")
    w("H = A + B + C + D + E + F + G        total direct costs")
    w("I = MTDC x F&A rate                  indirect costs")
    w("J = H + I")
    w("K = fee                              (SBIR/STTR and Major Facilities only)")
    w("L = J - K                            the amount you are requesting")
    w("```")
    w("")
    w("### MTDC — the part most budgets get wrong")
    w("")
    w("F&A is **not** charged on your whole budget. It is charged on the *modified* total "
      "direct costs:")
    w("")
    w("```")
    w("MTDC = H")
    w("     - all equipment (line D)")
    w("     - all participant support (line F)")
    w(f"     - the part of EACH subaward over {money(D['subaward_cap'])}")
    w("     - any G-line item you tick as 'no F&A'")
    w("```")
    w("")
    w("That last line is the tuition case. NSF's form has no tuition box, and Morgan books "
      "graduate tuition remission in **G.6 Other**, sitting right next to items that *do* "
      "carry F&A. The tool cannot tell $40,000 of tuition from $40,000 of lab fees by "
      "looking at them — and guessing wrong moves your total by about $21,600 at the 54% "
      "rate. So **you tick a box, and the math follows.** The same box covers scholarships, "
      "fellowships, rent, and patient care.")
    w("")
    w("### Multiple years")
    w("")
    w(f"Each year is its own sheet. **Add year** copies the previous year and raises "
      f"**salaries only** by {D['escalation']:g}% — equipment, travel and subawards carry "
      f"over unchanged, because quietly inflating those is how a budget grows without "
      f"anyone noticing. The Cumulative sheet is added up fresh every time you look at it "
      f"and is never stored, so it cannot fall out of step with the years.")
    w("")
    w("---")
    w("")
    w("## Part 3 — The checks")
    w("")
    warns = [r for r in nr.RULES if r["severity"] == "warn"]
    infos = [r for r in nr.RULES if r["severity"] == "info"]
    w(f"{len(warns)} warnings and {len(infos)} notices. Each one names the NSF rule it "
      f"comes from, and the citation is shown next to the flag in the app so you can look "
      f"it up.")
    w("")
    for rules, heading in ((warns, "Warnings"), (infos, "Notices")):
        w(f"### {heading}")
        w("")
        w("| Line | Check | What it means | Source |")
        w("|---|---|---|---|")
        for r in rules:
            line = r["line"] if r["line"] != "-" else "whole budget"
            msg = r["message"].replace("|", "/")
            w(f"| {line} | **{r['title']}** | {msg} | {r['citation']} |")
        w("")
    w("---")
    w("")
    w("## Part 4 — What the tool does not know")
    w("")
    w("Two limits worth stating plainly, because a tool that hides them is worse than one "
      "that admits them.")
    w("")
    w("**1. The two-month cap counts across every NSF award you hold.** This tool only "
      "sees the proposal in front of it. It will catch you budgeting three summer months "
      "here, but it has no idea what you have committed on your other NSF grants. That "
      "total is yours to track.")
    w("")
    w(f"**2. Morgan's equipment capitalization level is unconfirmed.** The tool defaults to "
      f"{money(D['capitalization'])}, which is what PAPPG specifies when an organization's "
      f"own level is not lower. If ORA capitalizes at a different figure, changing that one "
      f"setting reclassifies equipment correctly everywhere. Note that Form 1030's line D "
      f"says {money(D['itemise'])} — that is only about which items you list by name on the "
      f"form, and is a different thing from the definition that decides whether F&A "
      f"applies.")
    w("")
    w("**Also worth confirming:** the F&A and fringe rates above are read from the "
      "knowledge base, not from ORA's live rate agreement. Worth a sanity check against "
      "ORA's current rate sheet before any of this goes on a real submission.")
    w("")
    return "\n".join(L)


# ── a small markdown subset -> HTML ───────────────────────────────────────
INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*([^*]+)\*\*")
EM = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")


def inline(text):
    out = html.escape(text)
    out = INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = EM.sub(lambda m: f"<em>{m.group(1)}</em>", out)
    return out


def render_table(rows):
    head, *body = [r for r in rows if not re.fullmatch(r"\s*\|[\s:|-]+\|\s*", r)]
    cells = lambda row: [c.strip() for c in row.strip().strip("|").split("|")]  # noqa: E731
    out = ["<table><thead><tr>"]
    out += [f"<th>{inline(c)}</th>" for c in cells(head)]
    out.append("</tr></thead><tbody>")
    for r in body:
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells(r)) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def convert(md):
    lines = md.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            continue
        if re.fullmatch(r"\s*---+\s*", line):
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if line.lstrip().startswith("|"):
            buf = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                buf.append(lines[i])
                i += 1
            out.append(render_table(buf))
            continue
        if re.match(r"^\s*[-*]\s+", line):
            buf = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                buf.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(b)}</li>" for b in buf) + "</ul>")
            continue
        if line.strip() == "":
            i += 1
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#{1,4}\s|```|\s*[-*]\s|\s*\|)", lines[i]
        ) and not re.fullmatch(r"\s*---+\s*", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>" + inline(" ".join(buf)) + "</p>")
    return "\n".join(out)


CSS = """
@page { size: Letter; margin: 0.7in 0.75in; }
* { box-sizing: border-box; }
body { font: 10.5pt/1.55 "Charter","Georgia",serif; color: #1b1b1f; margin: 0; }
h1 { font: 700 22pt/1.2 "Helvetica Neue",Arial,sans-serif; margin: 0 0 4pt;
     color: #0b2f5e; letter-spacing: -0.01em; }
h2 { font: 700 14pt/1.25 "Helvetica Neue",Arial,sans-serif; margin: 22pt 0 7pt;
     color: #0b2f5e; padding-bottom: 4pt; border-bottom: 1.5px solid #d6dee8;
     page-break-after: avoid; }
h3 { font: 700 11.5pt/1.3 "Helvetica Neue",Arial,sans-serif; margin: 15pt 0 5pt;
     color: #24405f; page-break-after: avoid; }
p { margin: 0 0 8pt; }
ul { margin: 0 0 9pt; padding-left: 20pt; }
li { margin-bottom: 3.5pt; }
hr { border: 0; border-top: 1px solid #e2e7ee; margin: 16pt 0; }
code { font: 9pt "SF Mono",Menlo,Consolas,monospace; background: #eef2f7;
       padding: 1px 4px; border-radius: 3px; color: #10375f; }
pre { background: #f6f8fb; border: 1px solid #dfe6ef; border-left: 3px solid #0b2f5e;
      border-radius: 4px; padding: 9pt 11pt; margin: 0 0 10pt;
      page-break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.6pt; line-height: 1.5; color: #24303f; }
table { border-collapse: collapse; width: 100%; margin: 0 0 11pt; font-size: 9pt;
        page-break-inside: avoid; }
th { background: #0b2f5e; color: #fff; text-align: left; padding: 5pt 7pt;
     font: 700 8.8pt "Helvetica Neue",Arial,sans-serif; }
td { padding: 4.5pt 7pt; border-bottom: 1px solid #e4e9f0; vertical-align: top; }
tbody tr:nth-child(even) { background: #f7f9fc; }
strong { color: #10375f; }
"""


def main():
    body = convert(build_markdown())
    doc = ("<!doctype html><html><head><meta charset='utf-8'>"
           "<title>Budget Rules Reference</title>"
           f"<style>{CSS}</style></head><body>{body}</body></html>")
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"wrote {OUT}")
    print(f"{len(nr.RULES)} rules "
          f"({sum(1 for r in nr.RULES if r['severity'] == 'warn')} warn / "
          f"{sum(1 for r in nr.RULES if r['severity'] == 'info')} info)")


if __name__ == "__main__":
    main()
