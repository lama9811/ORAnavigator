# Rulebook baseline + per-section check

**Date:** 2026-08-17
**Status:** approved design, not yet implemented

## The problem, stated exactly

`services/delegated_rules.py` opens with it:

> NSF 23-598 states exactly one rule about the Project Summary: *"The Project
> Summary must include the LOI number in addition to all the requirements
> outlined in the PAPPG."* So the whole of what Draft Review could check about a
> Project Summary was (a) does a section by that name exist and (b) does it
> contain the LOI number. A five-line summary with no Intellectual Merit or
> Broader Impacts statement came back "Addressed".

Today those rows render as **"Not ours to check"** and drop out of the score's
denominator. That is honest, and it is where the tool stops. Nothing in the app
holds the rules the PAPPG actually states, so no NSF proposal is checked against
the rules that most often get one returned without review.

CLAUDE.md records the attempts to close this and why each failed:

- **Ingest PAPPG Chapter II wholesale** — 598 requirements, 8 rounds, 245s, hit
  the round cap, 105 invented sections. Unusable: a review would go from 44
  findings to ~640 and bury the four that matter.
- **`_RULEBOOK_SYSTEM`, a scoped extraction prompt** — 380 rows (-36%), but the
  submission-mechanics noise it was written to cut did not move (25% → 26%), and
  it made the target case *worse*: the Project Summary went from four usable rows
  to two, one of them the merged and useless "Include required distinct
  components in Project Summary".
- **`draft_scope.is_draft_checkable` as an output filter** — removes the right
  rows but only 11-12%, leaving 527 requirements and 97 sections against a
  solicitation's 38 and ~10.

CLAUDE.md names the untried lever as slicing the PAPPG's *source text* to the
standard-proposal subsections. This spec proposes a different one.

## The finding that changes the answer

Research.gov's proposal-preparation UI publishes, on each section's upload page,
a short **Content Instructions** list — the PAPPG already distilled by NSF into
the handful of rules that section is actually checked against. Captured
2026-08-17 from a live Morgan proposal (#329981, NSF 23-601). Verbatim, from the
Project Summary page:

> Your file must include three separate section headers: Overview, Intellectual
> Merit, and Broader Impacts. To be valid, a heading must be on its own line with
> no other text on that line.
>
> File cannot exceed one page.

That is the missing rule, stated by NSF, in two sentences. Across the four
sections that matter it is **14 rules, not 527** — and half are deterministic.

This is a curated rule table, not an extraction. It therefore sidesteps every
failure above rather than trying to beat them.

### Why it must be hand-curated

Every Research.gov screenshot carries `Welcome <name> | Sign Out`. **These pages
are behind NSF login.** `kb_scraper/` can never reach them, so an automated
refresh path does not exist and should not be designed for. Hand-curated is
forced, and it matches how every other authoritative rule table in this repo
already works: `compliance_sentinel`'s five rules, `budget_helper`'s F&A rates,
`forms_catalog`.

## Scope

**In:** Project Summary, Project Description, References Cited, Facilities /
Equipment / Other Resources. 14 rules — 7 deterministic, 7 semantic.

**Out, deliberately:** the other ten Research.gov sections (Budget Justification,
Data Management & Sharing Plan, Mentoring Plan, the four Senior/Key Personnel
documents, Suggested Reviewers, Reviewers Not to Include, Cover Sheet, Other
Personnel Bio). Several are forms rather than documents. Adding a section later
is a data edit, not an engine change — that is the point of the design.

**Out:** any AI that writes the PI's science. Feedback is *NSF's rule, quoted* +
*a structural skeleton*. Golden rule 1 holds: the model explains, it never
drafts their content. This is deliberately narrower than the Drafting Coach that
was removed 2026-08-10 and the Draft Critic removed 2026-08-11; neither is being
re-added.

## Architecture

### `services/rulebook_baseline.py` — new

Keyed on **rulebook**, never on funder. At a glance an NSF rule table looks like
a violation of the rule CLAUDE.md enforces with a grep gate (*"Do not re-add a
funder branch"*; `grep -ril "eir_solicitation\|23-598" backend/services/` must
come back empty). It is not one, and the module name should make that obvious
rather than require the argument:

- What was forbidden was branching **the engine** on one solicitation —
  `isEirProposal()` hiding the tool on 4 of 5 proposals.
- This is **data** keyed on the rulebook names `delegated_rules` already knows.
  `_RULES["the PAPPG"]` today; `_RULES["the NIH Grants Policy Statement"]` slots
  in later with zero engine change.

The engine stays funder-blind: *"which rulebooks does this solicitation cite, and
do we hold rules for any of them?"*

Row shape, matching what `solicitation_profile` already carries so no consumer
changes:

```python
{
    "id": "pappg_ps_headings",
    "section": "project_summary",
    "label": "Include Overview, Intellectual Merit and Broader Impacts headings",
    "quote": "Your file must include three separate section headers: Overview, "
             "Intellectual Merit, and Broader Impacts. To be valid, a heading "
             "must be on its own line with no other text on that line.",
    "source": "rulebook",
    "rulebook": "the PAPPG",
    "source_url": "https://www.nsf.gov/policies/pappg",
    "kind": "deterministic",       # or "semantic"
    "check": "ps_headings",        # names a callable in CHECKS; None if semantic
    "scored": True,
}
```

`quote` is NSF's verbatim sentence, so golden rule 2 holds **by construction** —
every finding can show the funder's own words. Contrast `generic_checks`, which
must fall back to a derived line when `_quote_if_it_names` cannot find the row's
name in the shared contract quote.

### Trigger: the cited rulebook, not the sponsor string

`delegated_rules.rulebooks_in(text)` is shipped and tested and already detects a
PAPPG citation by name or URL. That is the grounded trigger — the funder's own
sentence says *"follow the PAPPG"*, so we supply the PAPPG's rules for exactly
the rows we currently stamp "Not ours to check".

It also sidesteps a bug this repo has already been bitten by: sponsor-string
matching read `nsf` out of `Maryland Technology **Tra-nsf-er** Fund` and applied
NSF's RCR mandate to it (`compliance_sentinel`, fixed 2026-08-12).

**Fails safe.** A solicitation citing no rulebook we hold rules for behaves
exactly as it does today: no rows added, no score moved, no finding lost.

### Consumer 1 — the existing Draft Review

Wired in `proposals_service.load_solicitation_profile`, which rebuilds the
profile on **every** load. So this retroactively upgrades every NSF proposal
already in the database with no re-extraction and no migration — the same
pattern that lets `canon_section` repair stored section keys and
`compliance_sentinel` repair stored verdicts.

Composition happens in `solicitation_profile.build_generic`, which already merges
two row sources (extracted semantic rows + derived deterministic rows). This adds
a third. Order and dedup:

- Baseline rows are appended **after** the solicitation's own rows.
- A baseline row is **dropped** when the solicitation states the same rule
  itself. The solicitation is more specific and its quote is the one the PI must
  satisfy.

Dedup is deterministic-only and narrow, because there are exactly two overlaps
and both are page limits. `generic_checks.contract_requirements` already derives
a `page_limit` row per entry in `contract["page_limits"]`. So:

- a `page_limits` entry for `project_description` suppresses
  `pappg_pd_page_limit` (the solicitation's number beats NSF's 15-page default —
  which is what NSF's own instruction says: *"The system will enforce the page
  limit requirements listed in the funding opportunity"*)
- a `page_limits` entry for `project_summary` suppresses `pappg_ps_one_page`

Suppression keys on `(section, "page_limit")` after `resolve_section_key`, so the
Budget-Justification-style section-name merge applies here too. Semantic rows do
not dedup at all (see Risks).

### Consumer 2 — the per-section check

`review_draft` already owns locate → deterministic → semantic → score. The
section check calls the **same primitives** with the requirement universe
filtered to one section. It does not get a parallel engine — CLAUDE.md's reason
for routing Draft Review's "Use that document" through the one existing attach
path applies verbatim: *a second path would drift*.

- `POST /api/me/submissions/{id}/section-check` — `{section, text, rulebook?}`
- `POST /api/me/submissions/{id}/section-check/upload` — one PDF
- **Stateless.** The paste is never persisted. It is the PI's unpublished
  manuscript, same rule as Draft Review.
- **No solicitation required.** The PI picks the funder; the rulebook's rules
  run; **no score is returned** (there is nothing to score completeness
  against). If a solicitation *is* attached, its rows for that section stack on
  top and the score appears.
- `mechanical_checks.find_mistakes` runs on the section text — already
  deterministic, already outside the score.

Draft Review's 409-on-no-solicitation stays exactly as it is. That guard exists
so a completeness percentage is never computed against zero requirements; this
endpoint returns no percentage, so the guard has nothing to protect.

## The rules

Every quote below is verbatim from Research.gov, captured 2026-08-17.

"upload only" in the `scored` column means the row is scored when the section
arrives as a PDF and a real page count exists, and is reported as an unscored
estimate when it arrives as a paste. See **Page counts**.

### Project Summary

| id | rule | kind | scored |
|---|---|---|---|
| `pappg_ps_headings` | three headings — Overview, Intellectual Merit, Broader Impacts — each on its own line | deterministic | yes |
| `pappg_ps_overview` | Overview describes the objectives and methods | semantic | yes |
| `pappg_ps_merit` | Intellectual Merit statement is substantive | semantic | yes |
| `pappg_ps_impacts` | Broader Impacts statement is substantive | semantic | yes |
| `pappg_ps_one_page` | file cannot exceed one page | deterministic | upload only |

### Project Description

| id | rule | kind | scored |
|---|---|---|---|
| `pappg_pd_impacts_header` | separate Broader Impacts header on its own line | deterministic | yes |
| `pappg_pd_no_urls` | "Hyperlinks (URLs) must not be used in the Project Description" | deterministic | yes |
| `pappg_pd_page_limit` | the solicitation's limit; absent one, NSF's 15-page default | deterministic | upload only |

### References Cited

| id | rule | kind | scored |
|---|---|---|---|
| `pappg_rc_scholarly` | accepted scholarly citation practice | semantic | yes |
| `pappg_rc_et_al` | "References should avoid the use of et al." | deterministic | **no** |

`pappg_rc_et_al` is `scored: False`. NSF's own sentence carries the exception
"(except for large consortia papers)", and CLAUDE.md's standing rule is that a
conditional ask is advisory and never counted against a compliant proposal.

### Facilities, Equipment and Other Resources

| id | rule | kind | scored |
|---|---|---|---|
| `pappg_fe_no_financials` | "The section must not include any quantifiable financial information" | deterministic | yes |
| `pappg_fe_narrative` | narrative in nature | semantic | yes |
| `pappg_fe_coverage` | internal **and** external, physical **and** personnel | semantic | yes |
| `pappg_fe_unfunded` | lists senior/key personnel drawing no funds | semantic | yes |

## Presence is not approval

Heading **present** is deterministic. Heading **content** is semantic. They stay
separate rows.

This repo has shipped the conflated version three times and had to unship it each
time: the section map's green ✓ that made an 85-word Project Summary read as
approved; `could_not_locate` rendered as `not_found`; `delegated` rows styled
like assessed ones. A found `Intellectual Merit` heading with two vapid sentences
under it must not render like a real statement.

## Page counts

`pappg_ps_one_page` and `pappg_pd_page_limit` are page rules, and pages are a
PDF property.

- **From an upload:** real. pdfplumber is already in the stack and
  `solicitation_extractor.read_pdf()` already reports page counts.
- **From a paste:** a word-count **estimate**, labeled as an estimate, never
  pass/fail, never scored.

Reporting an estimate as a verdict is the same error class as the scraper
treating an unreadable page as a deleted one. The UI copy must say the PI can get
a real answer by uploading the PDF.

## False-positive guards

`mechanical_checks` shipped a case-insensitive `\bTO\s?DO\b` that flagged
*"would allow us **to do** much more work"* as unfilled template text, and a user
found it, not a test. Its own docstring had warned about exactly that. Each guard
below gets its own test.

**Heading detection** accepts `Intellectual Merit`, `INTELLECTUAL MERIT`,
`**Intellectual Merit**`, `2. Intellectual Merit`, `Intellectual Merit:` — and
rejects *"the intellectual merit of this work is…"* mid-sentence. NSF's own
"must be on its own line with no other text on that line" criterion is what does
that work, so this is not a stricter rule than the funder's.

**URL detection** matches `http://`, `https://`, `www.`, bare `doi.org/`; ignores
email addresses; and is scoped to the Project Description span only, so DOIs in
References Cited never trip it.

**Financial-information detection** fires on **figures** (`$12,000`,
`12,000 dollars`), never on the word "funds". NSF's own instruction for this
section reads *"for whom no funds are being requested"* — a word-level match
would flag the section for complying with it.

**`et al.` detection** requires the literal token with its period, so
*"et alia"* and a surname *"Etal"* do not match.

## Interaction with `delegated_rules`

`apply_delegation` must **not** demote a row the baseline can now check. Rules:

- A pointer-only row (`"Adhere to PAPPG guidelines"`) stays `delegated`. We hold
  four sections' rules, not the whole PAPPG.
- The `delegated` notice narrows and becomes honest: *"We check NSF's rules for
  Project Summary, Project Description, References Cited and Facilities. The rest
  of the PAPPG is not checked here."*
- A rider row keeps the status it earned, unchanged.

## Scores will move

Baseline rows enter `_CREDIT`'s denominator, so re-running a review on an
existing NSF proposal will not reproduce its previous percentage. That is
correct — the old number was computed with these rules missing — but it will
surprise someone.

A **saved** review (`Submission.draft_review_json`) keeps its stored numbers and
is not rewritten. Only a fresh run differs.

## The worked example

The second half of the ask is *how to write it*, and the honest constraint is
that we have nothing to quote from: `sample_proposals.py` holds **19 link-only
entries**, URLs to funded PDFs with no text, and the standing product decision is
that we never rehost.

**v1:** an authored **structural skeleton** per section — the three headings on
their own lines and the question each must answer, with a word budget — labeled
in the UI as a structural example and not a real proposal. Plus a link to the
funded samples already curated.

A skeleton is genuinely what a first-time PI needs here: the failure this feature
exists to prevent is a summary with no Intellectual Merit statement, which is a
*shape* problem.

**Later, separately:** extract real Project Summaries from Morgan ORA's own six
awarded proposals hosted on morgan.edu. Those are ORA's, publicly posted, and
HBCU-relevant. Out of scope here.

## Guided Pathway

`nextStep()` lost its writing step when the Drafting Coach was removed, and
CLAUDE.md flags the consequence: *no tool now helps a PI write — only ones that
check what they wrote*. The Build stage gains "Check a section as you write it",
which partially restores it without restoring the Coach.

## Testing

TDD. Each bullet is a test written before its implementation.

**The bug that motivates the feature**
- a five-line Project Summary with no Intellectual Merit or Broader Impacts
  heading → `not_found`, **not** `addressed`

**Heading detection**
- markdown-bolded, numbered, colon-suffixed and all-caps headings → found
- a heading inline in a sentence → not found (the "own line" rule)
- lower-case `overview` on its own line → found

**Guards**
- a URL in the Project Description → flagged; an email address → not flagged
- a DOI in References Cited → not flagged
- `$40,000` in Facilities → flagged; *"no funds are being requested"* → not
  flagged
- *"et alia"* and the surname *"Etal"* → not flagged

**Wiring**
- a solicitation citing no rulebook → zero baseline rows added (fails safe)
- a solicitation citing the PAPPG → baseline rows present in the loaded profile
- a solicitation stating the one-page summary rule itself → baseline row deduped
- a stored profile extracted before this existed → gains the rows on load, with
  no re-extraction

**Honesty**
- a pasted section never returns a pass/fail page verdict
- `pappg_rc_et_al` never enters the score
- a pointer-only PAPPG row stays `delegated`
- the section-check endpoint returns no score when no solicitation is attached

**Engine**
- `grep -ril "eir_solicitation\|23-598" backend/services/` stays empty
- the section check and Draft Review produce identical findings for the same
  section text

## Risks

**Over-curation.** Fourteen hand-typed rules can drift from the PAPPG. Mitigated
by every row carrying its verbatim quote and source URL, so a reviewer can check
it; and by the rules being NSF's own distillation rather than our reading of the
PAPPG.

**Semantic rows do not dedup.** A solicitation that states its own Intellectual
Merit expectation will produce two similar semantic rows. Chosen deliberately:
quote-based dedup is already known not to work here (`solicitation_requirements`
splits compound sentences, so several legitimate rows share one quote), and a
duplicate row is visible and harmless where a wrongly-dropped row is invisible
and not.

**Login-gated source.** If NSF rewrites these instructions we will not be
notified. Accepted: the rules are stable policy, and the alternative is no rules
at all.

## Out of scope

- The other ten Research.gov sections
- NIH, DOE or any second rulebook's rules (the shape supports them; the data is
  not being written)
- AI that drafts the PI's science
- Extracting real section text from funded proposals
- Slicing PAPPG Chapter II's source text — the lever CLAUDE.md names. If the
  curated table proves too narrow, that remains the next thing to try.
