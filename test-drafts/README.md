# Test drafts for the PAPPG rules

Paste these into a **Draft Review** or **Section Check** on any NSF proposal
whose solicitation cites the PAPPG. Every number below was measured against the
local backend on 2026-08-17, not predicted — if you get something materially
different, that is the bug.

Gemini varies run to run (the extractor measured 43 vs 47 requirements on
identical input), so treat ±1 or ±2 in a bucket as normal and a changed
**shape** — a flag that stops flagging, a clean draft that grows a fix-list — as
real.

---

## 1 · `1-project-summary-WEAK.txt` → Section Check → **Project Summary**

The original failure this whole thing exists for: a five-line summary with no
headings that used to come back **"Addressed"**.

| bucket | count |
|---|---|
| Needs work | **3** |
| Partly there | 3 |
| Addressed | 1 |
| Not checked here | 1 |

The three to fix:
- Overview, Intellectual Merit and Broader Impacts each on their own line
- The Intellectual Merit statement addresses intellectual merit
- Include LOI number in Project Summary

`Project Summary fits on one page` sits under **Not checked here** — pages are a
property of the PDF, and a word-count estimate must never be shown as a verdict.

## 2 · `2-project-summary-GOOD.txt` → Section Check → **Project Summary**

The same summary written properly. **Needs work: 0. Addressed: 7.**
If anything appears in the fix-list here, that is a false positive.

## 3 · `3-budget-justification-VIOLATIONS.txt` → Section Check → **Budget and Budget Justification**

The densest section (45 rules) and the one that proves prohibitions work. This
draft deliberately requests things NSF forbids.

| bucket | count |
|---|---|
| Needs work | **10** (incl. 6 flagged) |
| Partly there | 9 |
| If this applies to you | 9 *(folded)* |
| Addressed | 7 *(folded)* |
| Not checked here | 10 *(folded)* |

**Six prohibitions must come back FLAGGED, each quoting your own text back:**
- voluntary committed cost sharing → *"The university will contribute $25,000…"*
- alcoholic beverages → *"we request $600 for wine and beer…"*
- home office workspace rental → *"$3,600 per year to rent home office…"*
- meals / coffee breaks at internal meetings → *"$900 for catered lunches…"*
- per diem for local participants → *"Local participants attending…"*
- incentive payments under Participant Support → *"$1,200 in incentive payments…"*

A flag with **no quote** is a bug — a violation is a positive claim about your
draft and has to be evidenced.

Now delete those offending sentences and re-run: all six must move to
**Addressed** as `clear`. Before this work they came back "not found" and sat in
the fix-list **on a draft that complied**.

## 4 · `4-full-package.txt` → Draft Review (paste the whole thing)

A complete, mostly-compliant package.

- score **66%** of **79** assessed, **209** findings, ~24s
- **Fix these first: 20** — not 63, which is what it would be if advisory
  conditionals were still counted
- 6 sections located: Project Summary, Project Description, References Cited,
  Budget and Budget Justification, Facilities, Letter of Intent
- **4 sections open** (they have real work), **99 rows folded shut across 10**

The fold is the thing to look at: 209 findings must not arrive as one wall.
Check that closed headers still say what they hide ("6 to fix · 9 partial ·
7 addressed · 10 not checked") and that **Expand all / Collapse all** work.

---

## What to look for generally

1. **Nothing green that was not actually checked.** "Not checked here" and
   "If this applies to you" must never read as approval.
2. **Every flagged row quotes your text.** No quote → bug.
3. **The fix-list is short and real.** If it fills with "Justify entertainment
   costs" on a draft requesting no entertainment, the advisory filter regressed.
4. **Cover Sheet and Format of the Proposal are NOT in the Section Check
   picker** (7 sections offered, not 9) — every rule in them is unverifiable
   from text, so picking them would be a dead end. They still appear inside a
   full Draft Review.
