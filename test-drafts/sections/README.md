# One test draft per Section Check section

Seven drafts, one per section the picker offers. Each is a realistic piece of the
same fictional proposal (zwitterionic polymer salinity sensors at Morgan State),
with **deliberate violations planted** so you can see the checker bite rather
than just return a wall of green.

Every number below was **MEASURED** against a running backend on **2026-08-20**,
proposal **#8** (`admin@example.com`, NSF 23-598). Nothing here is predicted.

## How to run these

Log in as **`admin@example.com`** — that account owns proposal #8. Other accounts
have no solicitation attached, so you get the PAPPG rules only and the
solicitation's own rows never appear.

**My Proposals → the HBCU-EiR proposal → Check a section →** pick the section,
paste the file, Check.

## What each file should produce

| # | File | Section to pick | Rules | Flagged | Time |
|---|---|---|---|---|---|
| 1 | `1-project-summary.txt` | Project Summary | 8 | **1** | 4s |
| 2 | `2-project-description.txt` | Project Description | 32 | **1** | 24s |
| 3 | `3-references-cited.txt` | References Cited | 7 | **3** | 5s |
| 4 | `4-budget-justification-CLEAN.txt` | Budget and Budget Justification | 51 | **0** | 23s |
| 5 | `5-facilities-equipment-other-resources.txt` | Facilities, Equipment and Other Resources | 7 | **1** | 4s |
| 6 | `6-senior-key-personnel.txt` | Senior/Key Personnel Documents | 34 | **1** | 12s |
| 7 | `7-special-information-supplementary.txt` | Special Information and Supplementary Documentation | 21 | **3** | 10s |

## The exact violations planted, and what came back

Each was reported **Flagged**, quoting the offending words:

**1. Project Summary** — opens *"This abstract summarizes..."*
- Do not frame Project Summary as a proposal abstract → quoted the opening line

**2. Project Description** — a URL buried in the background paragraph
- No hyperlinks in the Project Description → quoted `https://www.morgan.edu/chemistry/polymer-lab`

**3. References Cited** — three at once
- Avoid 'et al.' in the reference list → quoted `et al.`
- Restrict References Cited to bibliographic citations only → quoted the editorial paragraph
- Do not exceed page limits by using references for extra content → quoted the smuggled protocol sentence

**4. Budget and Budget Justification** — **the control. Nothing is planted.**
- **0 flagged** is the correct result. Compare against `../3-budget-justification-VIOLATIONS.txt`,
  which returns **6 flagged** on the same section. That pair is the prohibition
  path proved in both directions: violations caught, clean draft left alone.
- This is also the section that exercises the 2026-08-20 fix. **51 rules, not 45** —
  the extra 6 are NSF 23-598's own budget rules (30% equipment cap, cost sharing
  prohibited, non-HBCU subaward rules). Before the fix they were silently absent.

**5. Facilities, Equipment and Other Resources** — a price tag in a section that forbids them
- No dollar figures → quoted `$18,400`

**6. Senior/Key Personnel Documents** — date of birth and home address in a biosketch
- Omit personal information from biographical sketch → quoted both lines

**7. Special Information and Supplementary Documentation** — three at once
- Do Not Use Mentoring Plan to Circumvent Project Description Limit → quoted the smuggled protocol
- Restrict Letters of Collaboration Content → quoted *"is an outstanding scientist..."*
- Do Not Submit Unauthorized Letters of Support → quoted *"I strongly endorse this proposal..."*

## Two things that are NOT bugs

**Section 6 is intermittent.** Senior/Key Personnel has 19 semantic rules, which
splits into two model batches. Observed across three runs: twice it returned real
judgments, once a batch failed and 15 rules came back **unclear** with *"the AI
reviewer is offline"*. That is the designed fallback — a failed batch loses its
own rows, not the whole section — but it means this file does not always
reproduce the table above. Re-run it. The other six were stable.

**Many rules report `not checked here`.** Margins, fonts, cover-sheet fields and
SciENcv certifications are real NSF rules that pasted text cannot demonstrate.
They keep their place and their quote and stay out of the scoring. Counts:
15 of 34 in Senior/Key Personnel, 10 of 51 in Budget.

## To test the other direction

Delete the planted sentence and re-run — the flag should disappear. The cleanest
one to try is #5: delete `costing $18,400` from the Equipment paragraph and
Facilities should go from 1 flagged to 0.
