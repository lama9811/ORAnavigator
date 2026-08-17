# Rulebook Baseline + Per-Section Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Draft Review the 14 rules NSF states on Research.gov's section pages, so a Project Summary with no Intellectual Merit statement stops coming back "Addressed" — and add a per-section endpoint so a PI can check one section while writing it.

**Architecture:** A new data module holds rules keyed by **rulebook name** (`"the PAPPG"`), never by funder — so it is not the funder branch the repo's grep gate forbids, and NIH slots in later as data. A second module holds the deterministic check callables. Rows are injected in `solicitation_profile.build_generic`, which `proposals_service.load_solicitation_profile` calls on every load, so every NSF proposal already in the database gains the rules with no migration and no re-extraction. The per-section check reuses `draft_review`'s existing primitives with the requirement universe filtered to one section — it does not get a parallel engine.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy backend, pytest, React 19 frontend. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-17-rulebook-baseline-section-check-design.md`

## Global Constraints

- **Golden rule 1** — these checks are code. No model decides a deterministic verdict.
- **Golden rule 2** — every rule row carries NSF's verbatim sentence in `source`. Never paraphrase into that field.
- **Golden rule 3** — nothing here may raise when Gemini is down; the deterministic rows must return identical findings offline.
- **Golden rule 6** — one feature, one focused change. Do not refactor `draft_review`'s existing stages.
- **Golden rule 7** — **never `git push`.** Commit locally only. A push to `main` fires `deploy-to-main` and ships to production.
- **The grep gate must stay clean:** `grep -ril "eir_solicitation\|23-598" backend/services/` returns nothing. Do not put a funder name in a module name, a branch, or a conditional.
- **Row shape is the existing one** (see `services/generic_checks.py:70` `contract_requirements`): keys are `id`, `label`, `section`, `kind`, `scored`, `check`, `check_args`, `source`, `why`, `keywords`, and optionally `flag_if_present`. The spec's illustrative shape used `quote`/`rulebook`; **the real field for the funder's sentence is `source`.** Add `rulebook` and `source_url` as extra keys — nothing else reads them, and the UI will.
- **Check signature contract:** `fn(ctx, req) -> (status, detail, evidence)` where `ctx = {"text", "spans", "title", "budget", "profile", "pages"}`.
- **Status vocabulary:** `clear`/`flagged` for prohibitions and limits (they carry `flag_if_present: True`); `addressed`/`partial`/`not_found` for presence; `could_not_locate`/`not_checked` when the input was not supplied. See the docstring at `services/generic_checks.py:1-31`.
- **Two imports MUST stay function-local, or the package stops importing.**
  `solicitation_profile.build_generic` imports `rulebook_baseline`, and
  `rulebook_baseline.baseline_rows` imports `solicitation_profile.section_key`.
  That is a cycle, and it is broken **only** because both are deferred inside
  their functions. Do not hoist either to module scope, and do not "tidy" them
  there in review. The rest of the graph is acyclic:
  `rulebook_checks → generic_checks → solicitation_profile`, and
  `rulebook_baseline → delegated_rules`, which imports nothing of ours.
- **Run the backend suite before every commit:**
  ```bash
  cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
    python3 -m pytest -q --ignore=tests/test_agent_instruction.py
  ```

## File Structure

| File | Responsibility |
|---|---|
| `backend/services/rulebook_baseline.py` — **create** | The rule table (data), rulebook detection, dedup, section list, skeletons. No logic that inspects a draft. |
| `backend/services/rulebook_checks.py` — **create** | The five deterministic check callables + `CHECKS`. No data. |
| `backend/services/solicitation_profile.py` — **modify** | `build_generic` gains a third row source. |
| `backend/services/proposals_service.py` — **modify** | `load_solicitation_profile` passes the profile URL through. |
| `backend/services/draft_review.py` — **modify** | `ctx["pages"]`; check resolution falls through to `rulebook_checks`; new `review_section`. |
| `backend/services/delegated_rules.py` — **modify** | `summarize` names what the baseline covered. |
| `backend/main.py` — **modify** | Two new endpoints. |
| `frontend/src/components/SectionCheckModal.jsx/.css` — **create** | The per-section UI. |
| `frontend/src/components/MyProposals.jsx` — **modify** | Toolbar button + `nextStep` writing step. |
| `backend/tests/test_rulebook_baseline.py` — **create** | Table integrity, detection, dedup. |
| `backend/tests/test_rulebook_checks.py` — **create** | Every check + every false-positive guard. |
| `backend/tests/test_section_check_api.py` — **create** | Endpoint e2e. |

---

### Task 1: The rule table

**Files:**
- Create: `backend/services/rulebook_baseline.py`
- Test: `backend/tests/test_rulebook_baseline.py`

**Interfaces:**
- Consumes: `services.delegated_rules.rulebooks_in` (exists, `services/delegated_rules.py:268`)
- Produces:
  - `RULES: dict[str, list[dict]]` — rulebook name → rows
  - `rules_for(rulebook: str, section: Optional[str] = None) -> list[dict]`
  - `rulebooks_cited_by(requirements: list[dict], url: str = "") -> list[str]`
  - `sections_offered(rulebook: str) -> list[dict]` — `[{key, label}]` for the UI picker

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_rulebook_baseline.py`:

```python
"""The rules NSF states on Research.gov, held as data keyed by RULEBOOK.

Captured 2026-08-17 from a live Morgan proposal (#329981, NSF 23-601). Those
pages are behind NSF login, so this table can never be scraped and is curated by
hand — the same shape as compliance_sentinel's rules and budget_helper's rates.
"""
import pytest

from services import rulebook_baseline as rb


def test_the_project_summary_heading_rule_exists_and_quotes_nsf():
    """The rule whose absence let a five-line summary come back 'Addressed'."""
    rows = rb.rules_for("the PAPPG", "project_summary")
    row = next(r for r in rows if r["check"] == "rb_headings")
    assert row["check_args"]["headings"] == [
        "Overview", "Intellectual Merit", "Broader Impacts"]
    assert "on its own line with no other text on that line" in row["source"]


def test_every_row_carries_a_verbatim_quote_and_a_source_url():
    """Golden rule 2 by construction: a row with no quote cannot be shown."""
    for name, rows in rb.RULES.items():
        for r in rows:
            assert r["source"].strip(), f"{r['id']} has no quote"
            assert r["source_url"].startswith("https://"), r["id"]
            assert r["rulebook"] == name


def test_a_semantic_row_names_no_check():
    """The deterministic rows' checks are verified in test_rulebook_checks.py,
    once the module they name exists."""
    for rows in rb.RULES.values():
        for r in rows:
            if r["kind"] != "deterministic":
                assert r.get("check") is None, r["id"]


def test_the_et_al_row_is_not_scored():
    """NSF's own sentence carries '(except for large consortia papers)'. A
    conditional ask is advisory and never counted against a compliant draft."""
    row = next(r for r in rb.rules_for("the PAPPG", "references_cited")
               if r["check"] == "rb_et_al")
    assert row["scored"] is False


def test_an_unknown_rulebook_yields_nothing():
    """Fails safe: a solicitation citing something we hold no rules for behaves
    exactly as it does today."""
    assert rb.rules_for("the Hitchhiker's Guide") == []


def test_a_solicitation_quoting_the_pappg_is_detected():
    reqs = [{"source": "Adhere to the requirements outlined in the PAPPG."}]
    assert rb.rulebooks_cited_by(reqs) == ["the PAPPG"]


def test_a_solicitation_naming_no_rulebook_is_detected_as_none():
    reqs = [{"source": "The Project Description is limited to 15 pages."}]
    assert rb.rulebooks_cited_by(reqs) == []


def test_the_sponsor_substring_bug_cannot_fire_here():
    """'Maryland Technology Transfer Fund' contains 'nsf'. This module never
    looks at a sponsor string — only at what the document CITES."""
    reqs = [{"source": "Funded by the Maryland Technology Transfer Fund."}]
    assert rb.rulebooks_cited_by(reqs) == []


def test_sections_offered_lists_the_four_covered_parts():
    keys = [s["key"] for s in rb.sections_offered("the PAPPG")]
    assert keys == ["project_summary", "project_description",
                    "references_cited", "facilities_equipment_other_resources"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_rulebook_baseline.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'services.rulebook_baseline'`

- [ ] **Step 3: Write the module**

Create `backend/services/rulebook_baseline.py`:

```python
"""The rules a named RULEBOOK states, held as data.

WHY THIS EXISTS
---------------
`delegated_rules` reports, honestly, that a requirement pointing at the PAPPG is
"Not ours to check" — and then the tool stops. NSF 23-598 states exactly one
Project Summary rule ("must include the LOI number in addition to all the
requirements outlined in the PAPPG"), so a five-line summary with no
Intellectual Merit or Broader Impacts statement was reported "Addressed". The
rules that would fail it live in the PAPPG.

Three attempts to read the PAPPG itself are on record as failures (see
CLAUDE.md): Chapter II whole is 598 requirements and 97 invented sections; a
scoped extraction prompt cut rows 36% without moving the noise and MERGED the
four usable Project Summary rows into two; a deterministic output filter removes
only 11%.

Research.gov publishes the answer already distilled: each section's upload page
carries a short "Content Instructions" list — the handful of rules that section
is actually checked against. Four sections, fourteen rules, half deterministic.

CURATED BY HAND, AND THAT IS FORCED, NOT LAZY
---------------------------------------------
Every source page carries `Welcome <name> | Sign Out`. They are behind NSF
login, so `kb_scraper/` can never reach them and no refresh path exists to
design for. This is the same shape as every other authoritative rule table here:
compliance_sentinel's five rules, budget_helper's F&A rates, forms_catalog.

KEYED ON THE RULEBOOK, NEVER THE FUNDER
---------------------------------------
`RULES["the PAPPG"]` is DATA. This is not the funder branch the repo forbids
(`grep -ril "eir_solicitation\\|23-598" backend/services/` must stay empty) —
what was forbidden was branching the ENGINE on one solicitation, the way
`isEirProposal()` hid the tool on 4 of 5 proposals. The engine here stays
funder-blind: it asks which rulebooks a solicitation cites and whether we hold
rules for any of them. `RULES["the NIH Grants Policy Statement"]` slots in with
no engine change.

The trigger is the CITATION, not the sponsor string — the funder's own sentence
saying "follow the PAPPG" is what earns it the PAPPG's rules. That also avoids
the bug compliance_sentinel was bitten by, where a bare substring test read
`nsf` out of "Maryland Technology Tra-nsf-er Fund".

Every row's `source` is NSF's VERBATIM sentence, so golden rule 2 holds by
construction — unlike generic_checks, which must fall back to a derived line
when the shared contract quote does not name the row.
"""

from __future__ import annotations

from typing import Optional

from services import delegated_rules

_PAPPG_URL = "https://www.nsf.gov/policies/pappg"

# Section keys must match what services/solicitation_profile.section_key()
# produces for the section's name, so a baseline row files under the same key as
# the solicitation's own rows for that part of the proposal.
PROJECT_SUMMARY = "project_summary"
PROJECT_DESCRIPTION = "project_description"
REFERENCES_CITED = "references_cited"
FACILITIES = "facilities_equipment_other_resources"

_SECTION_LABELS = {
    PROJECT_SUMMARY: "Project Summary",
    PROJECT_DESCRIPTION: "Project Description",
    REFERENCES_CITED: "References Cited",
    FACILITIES: "Facilities, Equipment and Other Resources",
}

# Order is the order Research.gov lists them, which is the order a PI meets them.
_SECTION_ORDER = [PROJECT_SUMMARY, PROJECT_DESCRIPTION, REFERENCES_CITED, FACILITIES]


def _row(id, section, label, source, why, *, kind="semantic", check=None,
         check_args=None, scored=True, flag_if_present=False,
         rulebook="the PAPPG", url=_PAPPG_URL, keywords=None) -> dict:
    row = {
        "id": id, "section": section, "label": label,
        "kind": kind, "scored": scored,
        "check": check, "check_args": check_args or {},
        "source": source, "why": why, "keywords": keywords or [],
        "rulebook": rulebook, "source_url": url,
    }
    if flag_if_present:
        row["flag_if_present"] = True
    return row


_PAPPG_RULES: list[dict] = [
    # ── Project Summary ─────────────────────────────────────────────────────
    # THE rule. Presence of the three headings is decided by CODE; whether what
    # sits under each is substantive is a separate SEMANTIC row below. Keeping
    # them apart is deliberate: this repo has shipped presence-rendered-as-
    # approval three times and unshipped it each time.
    _row("pappg_ps_headings", PROJECT_SUMMARY,
         "Overview, Intellectual Merit and Broader Impacts each on their own line",
         "Your file must include three separate section headers: Overview, "
         "Intellectual Merit, and Broader Impacts. To be valid, a heading must "
         "be on its own line with no other text on that line.",
         "NSF returns a Project Summary without these three headings without review.",
         kind="deterministic", check="rb_headings",
         check_args={"section": PROJECT_SUMMARY,
                     "headings": ["Overview", "Intellectual Merit", "Broader Impacts"]}),
    _row("pappg_ps_overview", PROJECT_SUMMARY,
         "The Overview describes the objectives and the methods",
         "Your file must include three separate section headers: Overview, "
         "Intellectual Merit, and Broader Impacts.",
         "A heading with nothing substantive under it reads to a reviewer as no answer at all."),
    _row("pappg_ps_merit", PROJECT_SUMMARY,
         "The Intellectual Merit statement addresses intellectual merit",
         "Your file must include three separate section headers: Overview, "
         "Intellectual Merit, and Broader Impacts.",
         "This is one of NSF's two review criteria; a reviewer looks for it here first."),
    _row("pappg_ps_impacts", PROJECT_SUMMARY,
         "The Broader Impacts statement addresses broader impacts",
         "Your file must include three separate section headers: Overview, "
         "Intellectual Merit, and Broader Impacts.",
         "This is NSF's second review criterion and the one most often left thin."),
    _row("pappg_ps_one_page", PROJECT_SUMMARY,
         "Project Summary fits on one page",
         "File cannot exceed one page.",
         "An over-length Project Summary is returned without review.",
         kind="deterministic", check="rb_page_limit", flag_if_present=True,
         check_args={"section": PROJECT_SUMMARY, "limit": 1}),

    # ── Project Description ─────────────────────────────────────────────────
    _row("pappg_pd_impacts_header", PROJECT_DESCRIPTION,
         "A separate Broader Impacts header on its own line",
         "Your file must include a separate section header for Broader Impacts. "
         "To be valid, a heading must be on its own line with no other text on "
         "that line.",
         "NSF requires Broader Impacts to be separately labeled, not woven into the narrative.",
         kind="deterministic", check="rb_headings",
         check_args={"section": PROJECT_DESCRIPTION,
                     "headings": ["Broader Impacts"]}),
    _row("pappg_pd_no_urls", PROJECT_DESCRIPTION,
         "No hyperlinks in the Project Description",
         "Hyperlinks (URLs) must not be used in the Project Description.",
         "A reviewer is not permitted to follow them, so anything behind one is unread.",
         kind="deterministic", check="rb_no_urls", flag_if_present=True,
         check_args={"section": PROJECT_DESCRIPTION}),
    _row("pappg_pd_page_limit", PROJECT_DESCRIPTION,
         "Project Description within its page limit",
         "Refer to your funding opportunity for page limit guidance. The system "
         "will enforce the page limit requirements listed in the funding "
         "opportunity. If the funding opportunity does not provide a specific "
         "limit, the 15-page limit stated in the PAPPG should be followed.",
         "Most funders return an over-length section without review.",
         kind="deterministic", check="rb_page_limit", flag_if_present=True,
         check_args={"section": PROJECT_DESCRIPTION, "limit": 15}),

    # ── References Cited ────────────────────────────────────────────────────
    _row("pappg_rc_scholarly", REFERENCES_CITED,
         "Citations follow accepted scholarly practice",
         "Follow accepted scholarly practices in providing citations for source materials.",
         "An incomplete reference list reads as carelessness to a reviewer in your field."),
    # scored=False: NSF's own sentence carries an exception, and a conditional
    # ask must never be counted against a compliant proposal.
    _row("pappg_rc_et_al", REFERENCES_CITED,
         "Avoid 'et al.' in the reference list",
         "References should avoid the use of et al. (except for large consortia papers).",
         "NSF asks for full author lists so reviewers can see who is involved.",
         kind="deterministic", check="rb_et_al", scored=False,
         flag_if_present=True, check_args={"section": REFERENCES_CITED}),

    # ── Facilities, Equipment and Other Resources ───────────────────────────
    _row("pappg_fe_no_financials", FACILITIES,
         "No dollar figures in Facilities, Equipment and Other Resources",
         "The section must not include any quantifiable financial information.",
         "Cost information belongs in the budget; NSF treats it as an error here.",
         kind="deterministic", check="rb_no_financials", flag_if_present=True,
         check_args={"section": FACILITIES}),
    _row("pappg_fe_narrative", FACILITIES,
         "Written as a narrative",
         "This section should be narrative in nature and include internal and "
         "external resources (both physical and personnel).",
         "A bare equipment list does not tell a reviewer the project is feasible."),
    _row("pappg_fe_coverage", FACILITIES,
         "Covers internal and external resources, physical and personnel",
         "This section should be narrative in nature and include internal and "
         "external resources (both physical and personnel).",
         "Reviewers assess feasibility from this section; omissions read as gaps."),
    _row("pappg_fe_unfunded", FACILITIES,
         "Names senior/key personnel and postdocs drawing no funds",
         "This section should include any senior/key personnel or postdoctoral "
         "scholars for whom no funds are being requested in the budget.",
         "It is the only place an unfunded contributor is visible to a reviewer."),
]

RULES: dict[str, list[dict]] = {"the PAPPG": _PAPPG_RULES}


# ── structural skeletons ────────────────────────────────────────────────────
# NOT a sample proposal and not AI-written prose about the PI's science. The
# failure this feature prevents is a SHAPE problem — a summary with no
# Intellectual Merit statement — so the shape is what to show. `sample_proposals`
# holds 19 link-only entries with no text and we never rehost, so there is
# nothing to quote from; a skeleton is the honest v1.
SKELETONS: dict[str, dict[str, dict]] = {
    "the PAPPG": {
        PROJECT_SUMMARY: {
            "title": "How a Project Summary is laid out",
            "note": "A structural example, not a real proposal. One page, three "
                    "headings, each on its own line.",
            "body": (
                "Overview\n"
                "What you will do and how. State the problem, the objectives, and "
                "the approach in a few sentences each.\n\n"
                "Intellectual Merit\n"
                "What this advances in your field, and why this team can do it. "
                "NSF reviewers score this criterion explicitly.\n\n"
                "Broader Impacts\n"
                "Who benefits beyond your field — students trained, curriculum "
                "changed, communities reached — and how you will know it happened."
            ),
        },
        PROJECT_DESCRIPTION: {
            "title": "Where Broader Impacts sits in a Project Description",
            "note": "A structural example, not a real proposal. Broader Impacts "
                    "must be its own labeled heading, not a paragraph inside the "
                    "narrative.",
            "body": (
                "Introduction and Motivation\n"
                "Results from Prior NSF Support   (if you have any)\n"
                "Research Plan\n"
                "Broader Impacts\n"
                "Timeline and Management Plan\n"
            ),
        },
    },
}


def rules_for(rulebook: str, section: Optional[str] = None) -> list[dict]:
    """Rows for `rulebook`, optionally narrowed to one section.

    An unknown rulebook returns [] rather than raising: a solicitation citing
    something we hold no rules for must behave exactly as it does today."""
    rows = RULES.get(rulebook or "", [])
    if section is None:
        return list(rows)
    return [r for r in rows if r["section"] == section]


def rulebooks_cited_by(requirements: list[dict], url: str = "") -> list[str]:
    """Which rulebooks we hold rules for does this solicitation actually cite?

    Reads the requirement rows' own `source` quotes rather than a sponsor
    string. Two reasons: the raw solicitation TEXT is not available here (it
    lives in `solicitation_sources`, keyed to the submission, not in the stored
    profile), and a citation is the grounded signal — the funder's own sentence
    saying "follow the PAPPG" is what earns it the PAPPG's rules."""
    blob = " ".join(
        str((r or {}).get("source") or "") + " " + str((r or {}).get("label") or "")
        for r in (requirements or [])
    )
    if url:
        blob = f"{blob} {url}"
    return [b["name"] for b in delegated_rules.rulebooks_in(blob) if b["name"] in RULES]


def sections_offered(rulebook: str) -> list[dict]:
    """The sections a PI can check one at a time, in Research.gov's own order."""
    have = {r["section"] for r in rules_for(rulebook)}
    return [{"key": k, "label": _SECTION_LABELS[k]}
            for k in _SECTION_ORDER if k in have]


def skeleton_for(rulebook: str, section: str) -> Optional[dict]:
    return (SKELETONS.get(rulebook or "") or {}).get(section or "")


def section_label(key: str) -> str:
    return _SECTION_LABELS.get(key, key.replace("_", " ").title())
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_rulebook_baseline.py -q
```
Expected: **PASS, all of them.** Task 1 commits green — the cross-module assertion that every deterministic rule names a real check lives in Task 2's file, because it needs both modules to exist.

- [ ] **Step 5: Commit**

```bash
git add backend/services/rulebook_baseline.py backend/tests/test_rulebook_baseline.py
git commit -m "feat(rulebook): hold the PAPPG rules NSF already distilled, as data

Research.gov's per-section upload pages carry the PAPPG's rules in the handful
of sentences each section is actually checked against. 14 rules across four
sections, against the 598 that reading Chapter II produced.

Keyed on the rulebook, not the funder, so this is data rather than the funder
branch the grep gate forbids -- and the NIH GPS slots in with no engine change.
Detection reads what the solicitation CITES, never a sponsor string, which is
the bug that read nsf out of Maryland Technology Transfer Fund.

Every row carries NSF's verbatim sentence, so golden rule 2 holds by
construction. The source pages are behind NSF login, so this table can never be
scraped -- curated by hand is forced, like compliance_sentinel's rules.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The deterministic checks

**Files:**
- Create: `backend/services/rulebook_checks.py`
- Test: `backend/tests/test_rulebook_checks.py`

**Interfaces:**
- Consumes: `services.solicitation_profile.heading_regex` (exists, `solicitation_profile.py:112` — matches a heading LINE with nothing else on it, which is literally NSF's criterion), `services.generic_checks.WORDS_PER_PAGE` (= 550)
- Produces: `CHECKS: dict[str, Callable]` with keys `rb_headings`, `rb_no_urls`, `rb_no_financials`, `rb_et_al`, `rb_page_limit`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_rulebook_checks.py`:

```python
"""The deterministic half of the rulebook baseline.

FALSE POSITIVES ARE THE RISK, and every guard here has its own test.
mechanical_checks shipped a case-insensitive \\bTO\\s?DO\\b that flagged
"would allow us to do much more work" as unfilled template text -- its own
docstring had warned about exactly that, and a USER found it, not a test.
"""
import pytest

from services import rulebook_checks as rc


def _ctx(section_text, *, key="project_summary", pages=None):
    return {"text": section_text, "spans": {key: {"text": section_text,
                                                  "marker": key, "start": 0}},
            "title": None, "budget": None, "profile": {}, "pages": pages}


def _req(check_args):
    return {"check_args": check_args}


# ── the bug this whole feature exists to remove ─────────────────────────────

FIVE_LINE_SUMMARY = """Project Summary

We propose to study trustworthy cardiac AI using multimodal physiological
sensing. The work will develop new models and validate them on clinical data.
We expect the results to be significant for the field.
"""

ARGS3 = {"section": "project_summary",
         "headings": ["Overview", "Intellectual Merit", "Broader Impacts"]}


def test_a_five_line_summary_with_no_headings_is_not_found():
    """The exact draft that came back 'Addressed' before this existed."""
    status, detail, evidence = rc.rb_headings(_ctx(FIVE_LINE_SUMMARY), _req(ARGS3))
    assert status == "not_found"
    assert "Overview" in detail and "Intellectual Merit" in detail


def test_all_three_headings_present_is_addressed():
    text = ("Overview\nWe will build X.\n\n"
            "Intellectual Merit\nThis advances Y.\n\n"
            "Broader Impacts\nStudents trained.\n")
    status, _, _ = rc.rb_headings(_ctx(text), _req(ARGS3))
    assert status == "addressed"


def test_two_of_three_headings_is_partial_and_names_the_missing_one():
    text = "Overview\nWe will build X.\n\nIntellectual Merit\nThis advances Y.\n"
    status, detail, _ = rc.rb_headings(_ctx(text), _req(ARGS3))
    assert status == "partial"
    assert "Broader Impacts" in detail


# ── heading-shape guards ────────────────────────────────────────────────────

@pytest.mark.parametrize("heading", [
    "Overview", "OVERVIEW", "overview", "**Overview**", "1. Overview",
    "Overview:", "  Overview  ", "- Overview", "II. Overview",
])
def test_real_heading_shapes_are_found(heading):
    text = f"{heading}\nWe will build X.\n\nIntellectual Merit\nY.\n\nBroader Impacts\nZ.\n"
    status, _, _ = rc.rb_headings(_ctx(text), _req(ARGS3))
    assert status == "addressed", heading


def test_a_heading_word_inside_a_sentence_is_not_a_heading():
    """NSF's own criterion is 'on its own line with no other text on that line'.
    Without this, a summary DISCUSSING intellectual merit passes the check for
    HAVING an Intellectual Merit statement -- which is the original bug wearing
    a different hat."""
    text = ("Overview\nWe will build X.\n\n"
            "The intellectual merit of this work is considerable, and the "
            "broader impacts are substantial.\n")
    status, detail, _ = rc.rb_headings(_ctx(text), _req(ARGS3))
    assert status == "partial"
    assert "Intellectual Merit" in detail


def test_the_evidence_quotes_the_headings_actually_found():
    text = "Overview\nX.\n\nIntellectual Merit\nY.\n\nBroader Impacts\nZ.\n"
    _, _, evidence = rc.rb_headings(_ctx(text), _req(ARGS3))
    assert "Overview" in evidence


def test_a_missing_section_is_could_not_locate_not_not_found():
    """could_not_locate != not_found. Telling a PI their summary has no
    Intellectual Merit statement when we simply never found the summary sends
    them rewriting something they already wrote."""
    ctx = {"text": "", "spans": {}, "title": None, "budget": None,
           "profile": {}, "pages": None}
    status, _, _ = rc.rb_headings(ctx, _req(ARGS3))
    assert status == "could_not_locate"


# ── URLs in the Project Description ─────────────────────────────────────────

PD = {"section": "project_description"}


@pytest.mark.parametrize("url", [
    "See https://example.edu/data for details.",
    "Available at http://example.edu.",
    "See www.example.edu for the code.",
    "Archived at doi.org/10.1234/abcd.",
])
def test_a_url_in_the_project_description_is_flagged(url):
    status, _, evidence = rc.rb_no_urls(
        _ctx(url, key="project_description"), _req(PD))
    assert status == "flagged"
    assert evidence


def test_an_email_address_is_not_a_hyperlink():
    """A collaborator's address is not a hyperlink and flagging it is noise."""
    status, _, _ = rc.rb_no_urls(
        _ctx("Contact pi@morgan.edu for details.", key="project_description"),
        _req(PD))
    assert status == "clear"


def test_ordinary_prose_is_clear():
    status, _, _ = rc.rb_no_urls(
        _ctx("We will study cardiac signals in year one.",
             key="project_description"), _req(PD))
    assert status == "clear"


# ── financial information in Facilities ─────────────────────────────────────

FE = {"section": "facilities_equipment_other_resources"}


def test_a_dollar_figure_in_facilities_is_flagged():
    status, _, evidence = rc.rb_no_financials(
        _ctx("The cluster was purchased for $240,000 in 2024.",
             key="facilities_equipment_other_resources"), _req(FE))
    assert status == "flagged"
    assert "$240,000" in evidence


def test_the_word_funds_is_not_financial_information():
    """NSF's OWN instruction for this section reads 'for whom no funds are being
    requested'. A word-level match would flag the section for complying with the
    rule it is being checked against."""
    status, _, _ = rc.rb_no_financials(
        _ctx("Dr. Smith contributes effort for whom no funds are being requested.",
             key="facilities_equipment_other_resources"), _req(FE))
    assert status == "clear"


def test_a_year_is_not_a_dollar_figure():
    status, _, _ = rc.rb_no_financials(
        _ctx("The laboratory was renovated in 2019 and holds 12 benches.",
             key="facilities_equipment_other_resources"), _req(FE))
    assert status == "clear"


# ── et al. ──────────────────────────────────────────────────────────────────

RC_ARGS = {"section": "references_cited"}


def test_et_al_is_flagged():
    status, _, evidence = rc.rb_et_al(
        _ctx("Smith et al. (2019). A paper.", key="references_cited"),
        _req(RC_ARGS))
    assert status == "flagged"
    assert "et al." in evidence


def test_et_alia_is_not_et_al():
    status, _, _ = rc.rb_et_al(
        _ctx("Smith, Jones et alia (2019).", key="references_cited"),
        _req(RC_ARGS))
    assert status == "clear"


def test_a_surname_etal_is_not_et_al():
    status, _, _ = rc.rb_et_al(
        _ctx("Etal, M. (2019). A paper.", key="references_cited"), _req(RC_ARGS))
    assert status == "clear"


# ── page limits: an estimate is never a verdict ─────────────────────────────

PAGE1 = {"section": "project_summary", "limit": 1}


def test_a_real_page_count_gives_a_real_verdict():
    ctx = _ctx("Short summary.", pages={"project_summary": 2})
    status, detail, _ = rc.rb_page_limit(ctx, _req(PAGE1))
    assert status == "flagged"
    assert "estimate" not in detail.lower()


def test_a_real_page_count_within_the_limit_is_clear():
    ctx = _ctx("Short summary.", pages={"project_summary": 1})
    status, _, _ = rc.rb_page_limit(ctx, _req(PAGE1))
    assert status == "clear"


def test_a_paste_never_returns_a_pass_or_fail_page_verdict():
    """Pages are a PDF property. Reporting a word-count estimate as a verdict is
    the same error class as the scraper calling an unreadable page a deleted
    one."""
    ctx = _ctx("word " * 4000)          # ~7 pages by estimate, well over 1
    status, detail, _ = rc.rb_page_limit(ctx, _req(PAGE1))
    assert status == "not_checked"
    assert "estimate" in detail.lower()
    assert "upload" in detail.lower()


def test_a_paste_estimate_is_excluded_from_the_score():
    """not_checked is outside draft_review._CREDIT, so an estimate can never
    move the number."""
    from services import draft_review
    assert "not_checked" not in draft_review._CREDIT


def test_every_check_is_registered():
    assert set(rc.CHECKS) == {"rb_headings", "rb_no_urls", "rb_no_financials",
                              "rb_et_al", "rb_page_limit"}


def test_every_deterministic_rule_names_a_check_that_exists():
    """Lives HERE, not in test_rulebook_baseline.py, so Task 1 could commit
    green — it needs both modules. A row whose check resolves to nothing is
    silently SKIPPED by run_deterministic, so a typo removes a rule with
    nothing going red. That is what this guards."""
    from services import rulebook_baseline as rb
    for rows in rb.RULES.values():
        for r in rows:
            if r["kind"] == "deterministic":
                assert r["check"] in rc.CHECKS, r["id"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_rulebook_checks.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'services.rulebook_checks'`

- [ ] **Step 3: Write the module**

Create `backend/services/rulebook_checks.py`:

```python
"""Deterministic checks for the rulebook baseline (golden rule 1).

SIGNATURE CONTRACT, identical to services/generic_checks.py:
  fn(ctx, req) -> (status, detail, evidence)
  ctx = {"text", "spans", "title", "budget", "profile", "pages"}
  req = the requirement row; parameters ride on req["check_args"]

FALSE POSITIVES ARE THE RISK HERE, not misses. A wrong "you left a URL in your
Project Description" costs the PI a hunt for something that is not there and
teaches them to ignore the tool. mechanical_checks shipped a case-insensitive
\\bTO\\s?DO\\b that matched "allow us to do much more work"; its own docstring
had warned about that exact class, and a user found it rather than a test. Every
guard below therefore has a named test.

PRESENCE IS NOT APPROVAL. `rb_headings` decides only that a heading LINE exists.
Whether what sits under it is substantive is a separate SEMANTIC row the model
judges against its own quote. Conflating the two is how an 85-word Project
Summary ended up under a green tick.
"""

from __future__ import annotations

import re
from typing import Optional

from services.generic_checks import WORDS_PER_PAGE
from services.solicitation_profile import heading_regex


def _span_text(ctx: dict, req: dict) -> Optional[str]:
    """The text of this row's section, or None if it was never located.

    None is NOT a failure — see `_unlocated`."""
    key = (req.get("check_args") or {}).get("section")
    span = (ctx.get("spans") or {}).get(key)
    return span["text"] if span else None


def _unlocated(what: str) -> tuple:
    """could_not_locate, and the wording matters.

    Excluded from draft_review._CREDIT, so it leaves the score's denominator.
    Saying "your Broader Impacts statement is missing" when we merely failed to
    find the section would send a PI rewriting something they already wrote."""
    return "could_not_locate", (
        f"{what} was not found in what you pasted, so this rule was not checked. "
        "If you did include it, give it a clear heading on its own line and re-run."
    ), ""


# ── headings on their own line ──────────────────────────────────────────────

def rb_headings(ctx: dict, req: dict) -> tuple:
    """Are the required headings present, each on a line of its own?

    Delegates the line test to solicitation_profile.heading_regex, which is
    already the shared primitive the locate stage segments a draft with — it
    allows numbering, bullets, markdown bold and a trailing colon, and requires
    nothing else on the line. That is LITERALLY NSF's criterion ("a heading must
    be on its own line with no other text on that line"), so this is not a
    stricter rule than the funder's."""
    text = _span_text(ctx, req)
    args = req.get("check_args") or {}
    wanted = list(args.get("headings") or [])
    if text is None:
        return _unlocated("That section")
    if not wanted:
        return "not_checked", "No headings were specified for this rule.", ""

    found, missing = [], []
    for h in wanted:
        # Markdown bold survives a paste from Word or a Markdown editor and is a
        # heading by every human standard; strip it before the line test rather
        # than widening the shared regex, which the locate stage also depends on.
        probe = re.sub(r"^[ \t]*(\*\*|__)|(\*\*|__)[ \t]*$", "",
                       text, flags=re.MULTILINE)
        (found if heading_regex(h).search(probe) else missing).append(h)

    if not missing:
        return "addressed", (
            "Found " + ", ".join(f"“{h}”" for h in found) +
            ", each on its own line."
        ), found[0]
    if found:
        return "partial", (
            "Found " + ", ".join(f"“{h}”" for h in found) + ". Missing " +
            ", ".join(f"“{h}”" for h in missing) +
            ". Each must be on a line of its own, with no other text on that line."
        ), found[0]
    return "not_found", (
        "None of " + ", ".join(f"“{h}”" for h in wanted) +
        " appears as a heading. Each must be on a line of its own, with no other "
        "text on that line — a sentence that mentions the words does not count."
    ), ""


# ── hyperlinks ──────────────────────────────────────────────────────────────

# An email address contains no scheme and no "www.", so the alternatives below
# cannot match one — but `\S+@\S+` is stripped first anyway, because a future
# edit widening this pattern would otherwise start flagging addresses silently.
_EMAIL_RE = re.compile(r"\S+@\S+")
_URL_RE = re.compile(
    r"(?:https?://\S+|www\.[A-Za-z0-9-]+\.\S+|\bdoi\.org/\S+)", re.IGNORECASE)


def rb_no_urls(ctx: dict, req: dict) -> tuple:
    text = _span_text(ctx, req)
    if text is None:
        return _unlocated("Your Project Description")
    hits = _URL_RE.findall(_EMAIL_RE.sub(" ", text))
    if not hits:
        return "clear", "No hyperlinks found in the Project Description.", ""
    shown = ", ".join(h.rstrip(".,;)") for h in hits[:3])
    more = f" and {len(hits) - 3} more" if len(hits) > 3 else ""
    return "flagged", (
        f"Found {len(hits)} hyperlink{'s' if len(hits) != 1 else ''}: {shown}{more}. "
        "NSF reviewers are not permitted to follow links, so anything behind one "
        "goes unread — put what matters in the text itself."
    ), shown


# ── quantifiable financial information ──────────────────────────────────────

# FIGURES, never the word "funds". NSF's own instruction for this section reads
# "for whom no funds are being requested", so a word-level match would flag the
# section for complying with the rule it is being checked against.
_MONEY_RE = re.compile(
    r"(?:\$\s?[\d,]+(?:\.\d{2})?(?:\s?[KkMm]\b)?"
    r"|\b[\d,]+(?:\.\d{2})?\s?(?:dollars|USD)\b)")


def rb_no_financials(ctx: dict, req: dict) -> tuple:
    text = _span_text(ctx, req)
    if text is None:
        return _unlocated("Your Facilities, Equipment and Other Resources section")
    hits = _MONEY_RE.findall(text)
    if not hits:
        return "clear", "No dollar figures found in this section.", ""
    shown = ", ".join(h.strip() for h in hits[:3])
    return "flagged", (
        f"Found {len(hits)} dollar figure{'s' if len(hits) != 1 else ''}: {shown}. "
        "NSF asks that this section carry no quantifiable financial information — "
        "describe the resource, and put its cost in the budget."
    ), shown


# ── et al. ──────────────────────────────────────────────────────────────────

# The literal token with its period. "et alia" and the surname "Etal" must not
# match, which is why the period is required and \b closes the front.
_ET_AL_RE = re.compile(r"\bet\s+al\.", re.IGNORECASE)


def rb_et_al(ctx: dict, req: dict) -> tuple:
    text = _span_text(ctx, req)
    if text is None:
        return _unlocated("Your References Cited section")
    hits = _ET_AL_RE.findall(text)
    if not hits:
        return "clear", "No use of 'et al.' found.", ""
    return "flagged", (
        f"'et al.' appears {len(hits)} time{'s' if len(hits) != 1 else ''}. NSF "
        "asks for full author lists except for large consortia papers, so this is "
        "advisory — it is not counted against your completeness score."
    ), "et al."


# ── page limits ─────────────────────────────────────────────────────────────

def rb_page_limit(ctx: dict, req: dict) -> tuple:
    """A real verdict from a real page count; an ESTIMATE otherwise, and an
    estimate is never a pass or a fail.

    Pages are a property of a formatted PDF. `ctx["pages"]` is populated only by
    the section-check upload path, where one file IS one section and pdfplumber's
    count is exact. From a paste there is no page count to have, and reporting a
    word-count estimate as a verdict is the same error class as the scraper
    treating an unreadable page as a deleted one."""
    args = req.get("check_args") or {}
    key, limit = args.get("section"), args.get("limit")
    text = _span_text(ctx, req)
    if text is None:
        return _unlocated("That section")

    real = (ctx.get("pages") or {}).get(key)
    if isinstance(real, int) and real > 0:
        if real <= limit:
            return "clear", f"{real} page{'s' if real != 1 else ''}, within the {limit}-page limit.", ""
        return "flagged", (
            f"{real} pages, over the {limit}-page limit. NSF returns an "
            "over-length section without review."
        ), ""

    words = len(text.split())
    pages = words / WORDS_PER_PAGE
    return "not_checked", (
        f"About {words:,} words ≈ {pages:.1f} pages — an estimate from word count, "
        f"against a {limit}-page limit. Page count depends on your formatting, so "
        "this is not a pass or a fail. Upload the PDF to have it checked properly."
    ), ""


CHECKS = {
    "rb_headings": rb_headings,
    "rb_no_urls": rb_no_urls,
    "rb_no_financials": rb_no_financials,
    "rb_et_al": rb_et_al,
    "rb_page_limit": rb_page_limit,
}
```

- [ ] **Step 4: Run both test files to verify they pass**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_rulebook_checks.py tests/test_rulebook_baseline.py -q
```
Expected: PASS, all. `test_every_deterministic_row_names_a_check_that_exists` from Task 1 now resolves.

- [ ] **Step 5: Run the full suite**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
```
Expected: PASS (682+ tests). Nothing is wired in yet, so no existing test may change.

- [ ] **Step 6: Commit**

```bash
git add backend/services/rulebook_checks.py backend/tests/test_rulebook_checks.py
git commit -m "feat(rulebook): the deterministic half, with a test per false positive

Five checks, none of them a model call. rb_headings reuses heading_regex -- the
shared primitive the locate stage already segments drafts with -- because its
'nothing else on the line' rule is LITERALLY NSF's criterion, so this is not
stricter than the funder.

Guards, each with a named test: an email is not a hyperlink; the word 'funds' is
not financial information (NSF's own sentence for that section reads 'for whom
no funds are being requested', so a word match would flag a section for
complying); 'et alia' and the surname 'Etal' are not 'et al.'; a heading word
inside a sentence is not a heading.

Page rules return not_checked from a paste, never a pass or a fail. Pages are a
PDF property; an estimate reported as a verdict is the same error class as the
scraper calling an unreadable page a deleted one.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Thread `pages` through the check context

**Files:**
- Modify: `backend/services/draft_review.py:218-245` (`run_deterministic`), `:570-582` (`review_draft` signature)
- Test: `backend/tests/test_rulebook_checks.py` (add)

**Interfaces:**
- Produces: `run_deterministic(text, spans, profile, *, title=None, budget=None, pages=None)`; `review_draft(..., pages: Optional[dict] = None)`; check resolution order becomes profile → `generic_checks.CHECKS` → `rulebook_checks.CHECKS`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_rulebook_checks.py`:

```python
# ── wiring into the engine ──────────────────────────────────────────────────

def test_run_deterministic_resolves_a_rulebook_check():
    """A row whose check resolves to nothing is SKIPPED, silently, with nothing
    going red. So the fall-through to rulebook_checks needs its own test."""
    from services import draft_review
    profile = {"requirements": [{
        "id": "pappg_ps_headings", "label": "headings", "section": "project_summary",
        "kind": "deterministic", "scored": True, "check": "rb_headings",
        "check_args": {"section": "project_summary",
                       "headings": ["Overview", "Intellectual Merit",
                                    "Broader Impacts"]},
        "source": "Your file must include three separate section headers.",
        "why": "", "keywords": [],
    }], "checks": {}}
    spans = {"project_summary": {"text": FIVE_LINE_SUMMARY, "marker": "Project Summary",
                                 "start": 0}}
    out = draft_review.run_deterministic(FIVE_LINE_SUMMARY, spans, profile)
    assert len(out) == 1
    assert out[0]["status"] == "not_found"


def test_run_deterministic_passes_pages_into_the_context():
    from services import draft_review
    profile = {"requirements": [{
        "id": "pappg_ps_one_page", "label": "one page", "section": "project_summary",
        "kind": "deterministic", "scored": True, "check": "rb_page_limit",
        "check_args": {"section": "project_summary", "limit": 1},
        "source": "File cannot exceed one page.", "why": "", "keywords": [],
    }], "checks": {}}
    spans = {"project_summary": {"text": "Short.", "marker": "PS", "start": 0}}
    out = draft_review.run_deterministic("Short.", spans, profile,
                                         pages={"project_summary": 3})
    assert out[0]["status"] == "flagged"
    assert "3 pages" in out[0]["note"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_rulebook_checks.py -q -k "run_deterministic"
```
Expected: FAIL — first test `assert len(out) == 1` gets `0` (check unresolved, row skipped); second gets `TypeError: run_deterministic() got an unexpected keyword argument 'pages'`.

- [ ] **Step 3: Modify `run_deterministic`**

In `backend/services/draft_review.py`, replace the signature and body of `run_deterministic` (currently at line 218):

```python
def run_deterministic(text: str, spans: dict, profile: dict, *,
                      title: Optional[str] = None,
                      budget: Optional[dict] = None,
                      pages: Optional[dict] = None) -> list[dict]:
    """Every code-decided requirement. No model involved, so these findings are
    identical whether or not Gemini is reachable (golden rule 1).

    A row names its check by string, resolved in three tiers: the profile's own
    callables, then the shared library in services/generic_checks.py, then the
    rulebook baseline's in services/rulebook_checks.py. A row whose check
    resolves to nothing is SKIPPED rather than guessed at — a fabricated verdict
    on a rule we cannot evaluate is worse than silence.

    `pages` maps a section key to its REAL page count, and is populated only by
    the section-check upload path where one file is one section. Absent it, a
    page rule reports an estimate and refuses to call it a pass or a fail."""
    rows = [r for r in profile.get("requirements", []) if r["kind"] == "deterministic"]
    if not rows:
        return []
    # Imported here, and only once a row actually needs it: both are separate
    # modules and this keeps the engine importable without them.
    from services import generic_checks
    from services import rulebook_checks
    ctx = {"text": text or "", "spans": spans or {}, "title": title,
           "budget": budget, "profile": profile, "pages": pages or {}}
    out = []
    for req in rows:
        name = req.get("check", "")
        fn = (profile.get("checks", {}).get(name)
              or generic_checks.CHECKS.get(name)
              or rulebook_checks.CHECKS.get(name))
        if fn is None:
            continue
        status, detail, evidence = fn(ctx, req)
        out.append(_finding(req, status, detail, evidence, source="check"))
    return out
```

- [ ] **Step 4: Thread `pages` through `review_draft`**

In `backend/services/draft_review.py`, change the `review_draft` signature (line 570) and its `run_deterministic` call (line 597):

```python
def review_draft(draft_text: str, *, profile: dict, title: Optional[str] = None,
                 budget: Optional[dict] = None, use_ai: bool = True,
                 pages: Optional[dict] = None) -> dict:
```

Add to the docstring, after the `use_ai` line:

```python
    #     pages      — section key -> REAL page count, from an upload. Absent it,
    #                  page rules report an estimate and never a verdict.
```

And the call:

```python
    findings = run_deterministic(text, spans, profile, title=title, budget=budget,
                                 pages=pages)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_rulebook_checks.py -q
```
Expected: PASS

- [ ] **Step 6: Run the full suite**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
```
Expected: PASS. `pages` defaults to `None` everywhere, so no existing behaviour moves.

- [ ] **Step 7: Commit**

```bash
git add backend/services/draft_review.py backend/tests/test_rulebook_checks.py
git commit -m "feat(draft-review): resolve rulebook checks, and carry a real page count

Check resolution gains a third tier (profile -> generic_checks ->
rulebook_checks). Worth its own test: a row whose check resolves to nothing is
SKIPPED silently, so a missing tier removes rules with nothing going red.

ctx gains 'pages' -- real per-section page counts, populated only by the
section-check upload path where one file is one section. Everywhere else it is
empty and a page rule reports an estimate rather than a verdict.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Inject baseline rows into the profile

**Files:**
- Modify: `backend/services/solicitation_profile.py:206-231` (`build_generic`)
- Modify: `backend/services/proposals_service.py:640-649` (pass the URL)
- Test: `backend/tests/test_rulebook_baseline.py` (add)

**Interfaces:**
- Consumes: `rulebook_baseline.rulebooks_cited_by`, `rulebook_baseline.rules_for`
- Produces: `rulebook_baseline.baseline_rows(requirements: list[dict], *, url: str = "", page_limits: Optional[dict] = None) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_rulebook_baseline.py`:

```python
# ── injection into the profile ──────────────────────────────────────────────

from services import solicitation_profile as sp

_PAPPG_ROW = {
    "id": "r1", "label": "Adhere to PAPPG guidelines", "section": "project_summary",
    "kind": "semantic", "scored": True,
    "source": "The Project Summary must include the LOI number in addition to "
              "all the requirements outlined in the PAPPG.",
    "why": "", "keywords": [],
}
_PLAIN_ROW = {
    "id": "r1", "label": "Limit the Project Description to 15 pages",
    "section": "project_description", "kind": "semantic", "scored": True,
    "source": "The Project Description is limited to 15 pages.",
    "why": "", "keywords": [],
}


def test_a_solicitation_citing_the_pappg_gains_the_baseline_rows():
    rows = rb.baseline_rows([_PAPPG_ROW])
    assert any(r["id"] == "pappg_ps_headings" for r in rows)


def test_a_solicitation_citing_nothing_gains_no_rows():
    """Fails safe: no rows added, no score moved, no finding lost."""
    assert rb.baseline_rows([_PLAIN_ROW]) == []


def test_build_generic_includes_the_baseline_for_a_pappg_solicitation():
    profile = sp.build_generic({}, [_PAPPG_ROW], id="NSF 23-598", title="T")
    ids = {r["id"] for r in profile["requirements"]}
    assert "pappg_ps_headings" in ids
    assert "pappg_pd_no_urls" in ids


def test_the_baseline_creates_the_sections_its_rows_need():
    """sections_from builds the universe from the rows, so a baseline row for a
    section the solicitation never named must still get a section to be located
    in -- otherwise it reports 'Not located' and drops out of the score."""
    profile = sp.build_generic({}, [_PAPPG_ROW], id="NSF 23-598", title="T")
    assert "facilities_equipment_other_resources" in profile["sections"]


def test_a_solicitation_stating_its_own_page_limit_suppresses_the_baseline_one():
    """The solicitation's number beats NSF's 15-page default -- which is what
    NSF's own instruction says: 'The system will enforce the page limit
    requirements listed in the funding opportunity.'"""
    contract = {"page_limits": {"Project Description": 12}}
    profile = sp.build_generic(contract, [_PAPPG_ROW], id="NSF 23-598", title="T")
    ids = [r["id"] for r in profile["requirements"]]
    assert "pappg_pd_page_limit" not in ids
    assert "page_limit_project_description" in ids


def test_a_solicitation_stating_a_summary_page_limit_suppresses_the_one_page_row():
    contract = {"page_limits": {"Project Summary": 1}}
    profile = sp.build_generic(contract, [_PAPPG_ROW], id="NSF 23-598", title="T")
    ids = [r["id"] for r in profile["requirements"]]
    assert "pappg_ps_one_page" not in ids


def test_only_page_rules_dedup():
    """Semantic rows deliberately do not dedup. Quote-based dedup is already
    known not to work here (rule 4 splits compound sentences, so several
    legitimate rows share one quote), and a visible duplicate is better than an
    invisible dropped rule."""
    contract = {"page_limits": {"Project Description": 12}}
    rows = rb.baseline_rows([_PAPPG_ROW], page_limits=contract["page_limits"])
    assert any(r["id"] == "pappg_pd_impacts_header" for r in rows)


def test_a_stored_profile_gains_the_rows_on_load_with_no_re_extraction():
    """load_solicitation_profile rebuilds on EVERY load, which is what makes
    this retroactive -- the same reason compliance_sentinel recomputes verdicts
    and canon_section re-canonicalises stored keys."""
    import json
    from services import proposals_service as ps

    class _Sub:
        solicitation_json = json.dumps({
            "id": "NSF 23-598", "title": "T", "contract": {},
            "requirements": [_PAPPG_ROW],
        })

    profile = ps.load_solicitation_profile(_Sub())
    assert any(r["id"] == "pappg_ps_headings" for r in profile["requirements"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_rulebook_baseline.py -q -k "baseline_rows or build_generic or stored_profile or dedup or suppress"
```
Expected: FAIL — `AttributeError: module 'services.rulebook_baseline' has no attribute 'baseline_rows'`

- [ ] **Step 3: Add `baseline_rows` to `rulebook_baseline.py`**

Append to `backend/services/rulebook_baseline.py`:

```python
# The only overlap between a baseline row and a contract-derived row is the page
# limit — generic_checks.contract_requirements derives one row per entry in
# contract["page_limits"], and the solicitation's number beats NSF's default.
# NSF says so itself: "The system will enforce the page limit requirements
# listed in the funding opportunity."
_PAGE_CHECKS = {"rb_page_limit"}


def baseline_rows(requirements: list[dict], *, url: str = "",
                  page_limits: Optional[dict] = None) -> list[dict]:
    """Rows to add to a profile whose solicitation cites a rulebook we hold.

    Returns [] when nothing is cited — no rows added, no score moved, no finding
    lost, which is the same fail-safe posture delegated_rules takes."""
    from services.solicitation_profile import section_key

    out: list[dict] = []
    suppressed = {section_key(str(s)) for s in (page_limits or {})}
    for book in rulebooks_cited_by(requirements, url=url):
        for row in rules_for(book):
            if row["check"] in _PAGE_CHECKS and row["section"] in suppressed:
                continue
            out.append(dict(row))
    return out
```

- [ ] **Step 4: Wire it into `build_generic`**

In `backend/services/solicitation_profile.py`, replace the body of `build_generic` (line 219 onward) with:

```python
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
    return make_profile(
        id=id, title=title, url=url,
        sections=sections_from(rows,
                               page_limits=contract.get("page_limits"),
                               attachments=contract.get("required_attachments")),
        requirements=rows,
        checks=generic_checks.CHECKS,
        merit_criteria=merit_criteria,
        eligibility_notes=eligibility_notes,
    )
```

Update `build_generic`'s docstring by appending a paragraph:

```python
    A third source: when the solicitation CITES a rulebook we hold rules for
    (the PAPPG today), that rulebook's rules are appended. Detection reads the
    requirement rows' own quotes, never a sponsor string. Because this runs on
    every profile load, adding a rule retroactively fixes every proposal already
    in the database.
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_rulebook_baseline.py -q
```
Expected: PASS

- [ ] **Step 6: Run the full suite and read every failure**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
```

Expected: some existing tests in `test_proposals_solicitation_profile.py`, `test_draft_review*.py` or `test_checklist_provenance.py` may now see extra requirement rows. **Each failure must be read, not silenced.**
- A count assertion (`len(requirements) == 3`) whose fixture quotes the PAPPG: correct to update, and add a comment saying the baseline supplied the extra rows.
- A fixture that does *not* mention the PAPPG but still gained rows: that is a **real bug** in `rulebooks_cited_by` — fix the detection, not the test.

- [ ] **Step 7: Commit**

```bash
git add backend/services/rulebook_baseline.py backend/services/solicitation_profile.py backend/tests/
git commit -m "feat(draft-review): give a PAPPG-citing solicitation the PAPPG's rules

build_generic gains a third row source. Because load_solicitation_profile
rebuilds the profile on EVERY load, this retroactively fixes every NSF proposal
already in the database -- no migration, no re-extraction -- the same property
that lets canon_section repair stored section keys and compliance_sentinel
repair stored verdicts.

Dedup is page-limits-only and narrow: a contract page_limit for a section
suppresses the baseline's row for it, because the solicitation's number beats
NSF's 15-page default -- which is what NSF's own instruction says. Semantic rows
deliberately do not dedup; quote-based dedup is known not to work here, and a
visible duplicate beats an invisible dropped rule.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Keep `delegated_rules` honest about what is now covered

**Files:**
- Modify: `backend/services/delegated_rules.py` (`summarize`, line 214)
- Test: `backend/tests/test_delegated_rules.py` (add)

**Interfaces:**
- Produces: `summarize(findings: list, *, covered: Optional[list[str]] = None) -> list[dict]`; each row gains `covered_sections: list[str]`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_delegated_rules.py`:

```python
# ── what the baseline now covers ────────────────────────────────────────────

def test_the_notice_names_the_sections_the_baseline_now_checks():
    """The caveat must shrink as coverage grows. Telling a PI 'the PAPPG is not
    checked here' when four of its sections now ARE is the same dishonesty as
    the badge that read 'attached' for a proposal with rules only."""
    findings = [{"id": "r1", "label": "Adhere to PAPPG guidelines",
                 "status": "delegated",
                 "source_text": "Adhere to PAPPG guidelines."}]
    rows = dr.summarize(findings, covered=["Project Summary", "Project Description"])
    assert rows and rows[0]["name"] == "the PAPPG"
    assert rows[0]["covered_sections"] == ["Project Summary", "Project Description"]


def test_with_nothing_covered_the_notice_is_unchanged():
    findings = [{"id": "r1", "label": "Adhere to PAPPG guidelines",
                 "status": "delegated",
                 "source_text": "Adhere to PAPPG guidelines."}]
    rows = dr.summarize(findings)
    assert rows[0]["covered_sections"] == []


def test_a_pointer_only_row_stays_delegated_even_when_covered():
    """We hold four sections' rules, not the whole PAPPG. 'Adhere to PAPPG
    guidelines' is still an ask we cannot assess in full."""
    from services import draft_review
    findings = [{"id": "r1", "label": "Adhere to PAPPG guidelines",
                 "status": "not_found", "note": "", "scored": True,
                 "source_text": "Adhere to PAPPG guidelines."}]
    out = draft_review.apply_delegation(findings)
    assert out[0]["status"] == "delegated"


def test_a_baseline_row_is_never_demoted_by_delegation():
    """A baseline row's own quote names the PAPPG. If apply_delegation treated
    it as pointer-only it would delete the very finding this feature adds."""
    from services import draft_review
    findings = [{
        "id": "pappg_ps_headings", "label": "Overview, Intellectual Merit and "
        "Broader Impacts each on their own line", "status": "not_found",
        "note": "None of those appears as a heading.", "scored": True,
        "source": "check", "rulebook": "the PAPPG",
        "source_text": "Your file must include three separate section headers.",
    }]
    out = draft_review.apply_delegation(findings)
    assert out[0]["status"] == "not_found"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_delegated_rules.py -q -k "covered or never_demoted"
```
Expected: FAIL — `summarize() got an unexpected keyword argument 'covered'`

- [ ] **Step 3: Read the two functions before editing them**

```bash
cd backend && sed -n '214,266p' services/delegated_rules.py
sed -n '535,570p' services/draft_review.py
```

- [ ] **Step 4: Modify `summarize` and guard `apply_delegation`**

In `backend/services/delegated_rules.py`, change `summarize`'s signature to take `covered` and set `covered_sections` on each returned row:

```python
def summarize(findings: list, *, covered: Optional[list[str]] = None) -> list[dict]:
    """One row per rulebook this solicitation points into.

    `covered` names the sections whose rules we now hold and DID check, so the
    notice can shrink as coverage grows. Telling a PI "the PAPPG is not checked
    here" when four of its sections now are is the same dishonesty as the badge
    that read "attached" for a proposal carrying rules only.
    """
```

Inside the function, add `"covered_sections": list(covered or [])` to each row dict it builds.

In `backend/services/draft_review.py`, `apply_delegation` must skip rows the baseline supplied — a baseline row's own quote names the PAPPG, so a pointer-only classifier would demote the finding this feature exists to add. At the top of its per-finding loop:

```python
        # A baseline row IS the rulebook's rule, quoted. Classifying it as a
        # pointer INTO the rulebook would demote the very finding this adds.
        if f.get("rulebook"):
            out.append(f)
            continue
```

And in `review_draft`'s return dict, pass the covered sections:

```python
        "delegated": delegated_rules.summarize(
            findings,
            covered=[rulebook_baseline.section_label(s) for s in sorted({
                r["section"] for r in profile.get("requirements", [])
                if r.get("rulebook") and r.get("section")})]),
```

Add `from services import rulebook_baseline` to `draft_review.py`'s imports.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_delegated_rules.py tests/test_rulebook_baseline.py -q
```
Expected: PASS

- [ ] **Step 6: Run the full suite, then commit**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
git add backend/services/delegated_rules.py backend/services/draft_review.py backend/tests/test_delegated_rules.py
git commit -m "fix(draft-review): the PAPPG caveat shrinks as coverage grows

summarize() takes the sections whose rules we now hold, so the notice stops
saying 'not checked here' about four sections that now are. Same dishonesty
class as the badge that read 'attached' for a proposal carrying rules only.

apply_delegation now skips any row the baseline supplied. A baseline row's own
quote names the PAPPG, so the pointer-only classifier would have demoted to
'Not ours to check' the exact finding this feature adds -- deleting it from the
score's denominator on the way past.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `review_section` — one section, same engine

**Files:**
- Modify: `backend/services/draft_review.py` (add at end, before `_solicitation_meta`)
- Test: `backend/tests/test_rulebook_baseline.py` (add)

**Interfaces:**
- Produces: `draft_review.review_section(text: str, *, section: str, rulebook: str, profile: Optional[dict] = None, pages: Optional[int] = None, budget: Optional[dict] = None, use_ai: bool = True) -> dict`
- Returns: `{section, label, rulebook, findings, mistakes, skeleton, score, ai, word_count, message}`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_rulebook_baseline.py`:

```python
# ── the per-section entry point ─────────────────────────────────────────────

from services import draft_review

FIVE_LINE = """We propose to study trustworthy cardiac AI using multimodal
physiological sensing. The work will develop new models and validate them on
clinical data. We expect the results to be significant.
"""


def test_a_section_check_needs_no_solicitation():
    """The rules are NSF's, not the solicitation's. Draft Review's 409 exists so
    a percentage is never computed against zero requirements; this returns no
    percentage, so the guard has nothing to protect."""
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    assert out["score"] is None
    assert any(f["id"] == "pappg_ps_headings" and f["status"] == "not_found"
               for f in out["findings"])


def test_a_section_check_returns_only_that_sections_rules():
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    assert {f["id"] for f in out["findings"]} <= {
        r["id"] for r in rb.rules_for("the PAPPG", "project_summary")}


def test_a_section_check_carries_the_skeleton():
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    assert "Intellectual Merit" in out["skeleton"]["body"]
    assert "not a real proposal" in out["skeleton"]["note"]


def test_a_real_page_count_reaches_the_page_rule():
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", pages=3, use_ai=False)
    row = next(f for f in out["findings"] if f["id"] == "pappg_ps_one_page")
    assert row["status"] == "flagged"


def test_a_paste_gets_an_estimate_not_a_verdict():
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    row = next(f for f in out["findings"] if f["id"] == "pappg_ps_one_page")
    assert row["status"] == "not_checked"


def test_mechanical_mistakes_run_on_the_section():
    out = draft_review.review_section(
        "Overview\nWe will study TBD in year one.\n", section="project_summary",
        rulebook="the PAPPG", use_ai=False)
    assert any("TBD" in (m.get("evidence") or "") for m in out["mistakes"])


def test_empty_text_returns_a_message_not_a_review():
    out = draft_review.review_section("", section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    assert out["findings"] == []
    assert out["message"]


def test_an_unknown_section_returns_no_findings():
    out = draft_review.review_section(FIVE_LINE, section="not_a_section",
                                      rulebook="the PAPPG", use_ai=False)
    assert out["findings"] == []


def test_the_section_check_agrees_with_the_whole_package_review():
    """One engine, two entry points. If these ever disagree for the same text,
    a second engine has grown."""
    profile = sp.build_generic({}, [_PAPPG_ROW], id="NSF 23-598", title="T")
    spans = {"project_summary": {"text": FIVE_LINE, "marker": "Project Summary",
                                 "start": 0}}
    whole = draft_review.run_deterministic(FIVE_LINE, spans, profile)
    whole_ps = {f["id"]: f["status"] for f in whole
                if f["id"] == "pappg_ps_headings"}
    out = draft_review.review_section(FIVE_LINE, section="project_summary",
                                      rulebook="the PAPPG", use_ai=False)
    section_ps = {f["id"]: f["status"] for f in out["findings"]
                  if f["id"] == "pappg_ps_headings"}
    assert whole_ps == section_ps
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_rulebook_baseline.py -q -k "section"
```
Expected: FAIL — `module 'services.draft_review' has no attribute 'review_section'`

- [ ] **Step 3: Write `review_section`**

Append to `backend/services/draft_review.py`, immediately before `_solicitation_meta`:

```python
def review_section(text: str, *, section: str, rulebook: str,
                   profile: Optional[dict] = None,
                   pages: Optional[int] = None,
                   budget: Optional[dict] = None,
                   use_ai: bool = True) -> dict:
    """Check ONE section against its rulebook's rules, while the PI writes it.

    The same primitives as review_draft, with the requirement universe filtered
    to one section — NOT a parallel engine. The reason is the one CLAUDE.md
    gives for routing Draft Review's "Use that document" through the single
    existing attach path: a second path drifts, and two engines that disagree
    about the same section is exactly the confusion this tool exists to remove.

    NO SOLICITATION REQUIRED, and no score returned. Draft Review's 409 exists
    so a completeness percentage is never computed against zero requirements;
    this returns no percentage, so that guard has nothing to protect. When a
    `profile` IS supplied its own rows for this section are checked too.

    `pages` is this section's REAL page count from an uploaded PDF — one file is
    one section here, so unlike the whole-package path the count is exact.
    """
    from services import rulebook_baseline

    label = rulebook_baseline.section_label(section)
    text = (text or "").strip()
    base = {
        "section": section, "label": label, "rulebook": rulebook,
        "skeleton": rulebook_baseline.skeleton_for(rulebook, section),
        "findings": [], "mistakes": [], "score": None, "ai": False,
        "word_count": len(text.split()),
    }
    if not text:
        return {**base, "message": f"Paste your {label} to have it checked."}

    rows = rulebook_baseline.rules_for(rulebook, section)
    if profile:
        rows = rows + [r for r in sp.requirements_for(profile, section)
                       if r["id"] not in {b["id"] for b in rows}]
    if not rows:
        return {**base, "message": (
            f"No rules are on file for {label}. Nothing was checked.")}

    # The whole paste IS the section, so the span is known without the locate
    # stage — that is the one thing this entry point genuinely skips.
    spans = {section: {"text": text, "marker": label, "start": 0}}
    mini = {"requirements": rows, "checks": sp_checks_for(profile),
            "sections": {section: {"label": label, "aliases": [label]}}}

    findings = run_deterministic(text, spans, mini, budget=budget,
                                 pages={section: pages} if pages else None)

    semantic = [r for r in rows if r["kind"] == "semantic"]
    if semantic and use_ai:
        # _review_section's last parameter is `solicitation_id`, which reaches
        # the model only through _review_system's prompt text ("requirements from
        # <id>"). Passing the RULEBOOK name is correct here and reads correctly:
        # these rules do come from the PAPPG, not from a solicitation.
        findings.extend(_review_section(section, spans[section], semantic,
                                        mini["sections"], rulebook))

    findings = apply_draft_scope(findings)
    order = {r["id"]: i for i, r in enumerate(rows)}
    findings.sort(key=lambda f: order.get(f["id"], 999))

    return {
        **base,
        "findings": findings,
        "ai": any(f["source"] == "ai" for f in findings),
        "mistakes": mechanical_checks.find_mistakes(text, budget=budget),
        # score stays None. A percentage here would read as "your Project
        # Summary is 60% done", which is not a thing this can measure — the
        # rules are NSF's floor, not a completeness universe.
        "message": None,
    }


def sp_checks_for(profile: Optional[dict]) -> dict:
    """The profile's own check callables, or none."""
    return (profile or {}).get("checks") or {}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_rulebook_baseline.py -q
```
Expected: PASS

- [ ] **Step 5: Run the full suite, then commit**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
git add backend/services/draft_review.py backend/tests/test_rulebook_baseline.py
git commit -m "feat(draft-review): review_section -- one section, the same engine

Same primitives, universe filtered to one section. Not a parallel engine, for
the reason CLAUDE.md gives for routing 'Use that document' through the one
existing attach path: a second path drifts, and two engines disagreeing about
the same Project Summary is the confusion this tool exists to remove. A test
asserts the two entry points agree on identical text.

No solicitation required and no score returned. Draft Review's 409 exists so a
percentage is never computed against zero requirements; there is no percentage
here, so the guard has nothing to protect.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The endpoints

**Files:**
- Modify: `backend/main.py` (after the `draft-review/upload` handler, ~line 4680)
- Test: `backend/tests/test_section_check_api.py` (create)

**Interfaces:**
- Consumes: `draft_review.review_section`, `rulebook_baseline.sections_offered`, `document_text.extract_upload`, `_proposals_service.get_submission`, `_require_profile_and_budget`
- Produces: `GET /api/me/section-check/sections`, `POST /api/me/submissions/{id}/section-check`, `POST /api/me/submissions/{id}/section-check/upload`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_section_check_api.py`:

```python
"""The per-section check endpoints.

Route/auth level only — the rules themselves are tested in
test_rulebook_checks.py. Mirrors the single-`ctx`-fixture harness of
tests/test_proposals_api_e2e.py: one in-memory SQLite engine per test, both
get_db dependencies overridden, get_current_user stubbed. Read that file
before changing anything here.
"""
import os
os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import main
import deps
from db import Base
from models import User, Submission
from security import hash_password

FIVE_LINE = ("We propose to study trustworthy cardiac AI using multimodal "
             "physiological sensing. The work will develop new models.")


@pytest.fixture
def ctx():
    """Yields (client, submission_id, other_users_submission_id, SessionMaker).

    The submission has NO solicitation attached — which is the point: this
    endpoint must work without one, unlike draft-review."""
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestingSession()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    other = User(email="other@morgan.edu", password_hash=hash_password("x"),
                 role="user", name="Someone Else")
    seed.add_all([u, other])
    seed.commit()
    uid = u.id

    sub = Submission(user_id=uid, title="REU Site: Cardiac AI",
                     sponsor="National Science Foundation", status="active")
    theirs = Submission(user_id=other.id, title="Someone else's",
                        sponsor="NSF", status="active")
    seed.add_all([sub, theirs])
    seed.commit()
    sub_id, theirs_id = sub.id, theirs.id
    seed.close()

    def _override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = _override_db
    main.app.dependency_overrides[deps.get_db] = _override_db
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "user_id": uid, "email": "pi@morgan.edu", "role": "user",
    }
    c = TestClient(main.app)
    yield c, sub_id, theirs_id, TestingSession
    main.app.dependency_overrides.clear()


def test_the_section_list_is_offered(ctx):
    c, _, _, _ = ctx
    r = c.get("/api/me/section-check/sections")
    assert r.status_code == 200
    keys = [s["key"] for s in r.json()["sections"]]
    assert "project_summary" in keys


def test_a_paste_is_checked_without_a_solicitation(ctx):
    """The rules are NSF's, so this must NOT 409 the way draft-review does."""
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check",
               json={"section": "project_summary", "text": FIVE_LINE,
                     "rulebook": "the PAPPG"})
    assert r.status_code == 200
    body = r.json()["result"]
    assert body["score"] is None
    assert any(f["id"] == "pappg_ps_headings" and f["status"] == "not_found"
               for f in body["findings"])


def test_another_users_submission_is_404(ctx):
    c, _, theirs_id, _ = ctx
    r = c.post(f"/api/me/submissions/{theirs_id}/section-check",
               json={"section": "project_summary", "text": FIVE_LINE,
                     "rulebook": "the PAPPG"})
    assert r.status_code == 404


def test_an_unknown_rulebook_is_400(ctx):
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check",
               json={"section": "project_summary", "text": FIVE_LINE,
                     "rulebook": "the Hitchhiker's Guide"})
    assert r.status_code == 400


def test_an_unknown_section_is_400(ctx):
    c, sub_id, _, _ = ctx
    r = c.post(f"/api/me/submissions/{sub_id}/section-check",
               json={"section": "cover_letter", "text": FIVE_LINE,
                     "rulebook": "the PAPPG"})
    assert r.status_code == 400


def test_the_paste_is_never_persisted(ctx):
    """It is the PI's unpublished manuscript. Same rule as Draft Review."""
    c, sub_id, _, TestingSession = ctx
    c.post(f"/api/me/submissions/{sub_id}/section-check",
           json={"section": "project_summary", "text": FIVE_LINE,
                 "rulebook": "the PAPPG"})
    db = TestingSession()
    sub = db.query(Submission).filter(Submission.id == sub_id).one()
    blob = " ".join(str(getattr(sub, f, "") or "") for f in
                    ("notes", "sections_json", "draft_review_json",
                     "solicitation_json"))
    db.close()
    assert FIVE_LINE not in blob
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_section_check_api.py -q
```
Expected: FAIL — 404 on every route.

- [ ] **Step 3: Add the request model and the three endpoints**

In `backend/main.py`, near the other proposal request models, add:

```python
class SectionCheckRequest(BaseModel):
    section: str
    text: str = ""
    rulebook: str = "the PAPPG"
```

After the `draft_review_upload` handler, add:

```python
@app.get("/api/me/section-check/sections")
async def section_check_sections(rulebook: str = "the PAPPG"):
    """Which sections a PI can check one at a time, in Research.gov's order.

    Auth-free: it is a static list of section names, and the picker needs it
    before the modal has anything to check."""
    from services import rulebook_baseline as _rb
    return {"rulebook": rulebook, "sections": _rb.sections_offered(rulebook)}


def _section_check_inputs(payload_section: str, rulebook: str):
    """Validate the pair, or 400 naming what is wrong."""
    from services import rulebook_baseline as _rb
    if not _rb.rules_for(rulebook):
        raise HTTPException(400, f"No rules are on file for {rulebook}.")
    if not _rb.rules_for(rulebook, payload_section):
        raise HTTPException(
            400, f"{rulebook} has no rules on file for that section.")


@app.post("/api/me/submissions/{submission_id}/section-check")
async def section_check_endpoint(
    submission_id: int,
    payload: SectionCheckRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Check ONE section against its rulebook, while the PI is still writing it.

    Stateless — the paste is NOT persisted. It is the PI's unpublished
    manuscript, the same rule Draft Review follows.

    Deliberately does NOT 409 without a solicitation, unlike draft-review: these
    rules are NSF's, not the solicitation's, and no completeness percentage is
    returned, so the guard that 409 exists to enforce has nothing to protect
    here. When a solicitation IS attached its own rows for this section are
    checked alongside."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(
        db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    _section_check_inputs(payload.section, payload.rulebook)

    from services import draft_review as _dr
    profile = _proposals_service.load_solicitation_profile(sub)
    budget = None
    raw_b = getattr(sub, "budget_json", None)
    if raw_b:
        try:
            from services.budget_helper import compute_budget
            budget = compute_budget(json.loads(raw_b))
        except (ValueError, TypeError):
            budget = None

    result = _dr.review_section(payload.text, section=payload.section,
                                rulebook=payload.rulebook, profile=profile,
                                budget=budget)
    return {"submission_id": submission_id, "result": result}


@app.post("/api/me/submissions/{submission_id}/section-check/upload")
async def section_check_upload(
    submission_id: int,
    section: str = Form(...),
    rulebook: str = Form("the PAPPG"),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The same check from an uploaded PDF.

    ONE file, because one file IS one section here — which is what makes the
    page count exact rather than a word-count estimate. That is the only thing
    this path can do that a paste cannot, and it is the whole reason it exists."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(
        db, submission_id=submission_id, user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")
    _section_check_inputs(section, rulebook)

    data = await file.read()
    if len(data) > _DRAFT_MAX_FILE_BYTES:
        raise HTTPException(
            400, f"That file is larger than {_DRAFT_MAX_FILE_BYTES // (1024 * 1024)} MB.")

    from services import document_text as _dt
    from services import draft_review as _dr
    read = _dt.extract_upload(file.filename or "file", data)
    if not (read.get("text") or "").strip():
        return {"submission_id": submission_id, "result": None,
                "extraction": {k: v for k, v in read.items() if k != "text"},
                "error": read.get("error") or "Couldn't read any text from that file."}

    profile = _proposals_service.load_solicitation_profile(sub)
    result = _dr.review_section(read["text"], section=section, rulebook=rulebook,
                                profile=profile, pages=read.get("pages") or None)
    return {
        "submission_id": submission_id,
        "result": result,
        # The extracted TEXT is deliberately not echoed back.
        "extraction": {k: v for k, v in read.items() if k != "text"},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_section_check_api.py -q
```
Expected: PASS

- [ ] **Step 5: Run the full suite, then commit**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
git add backend/main.py backend/tests/test_section_check_api.py
git commit -m "feat(api): check one section while you are still writing it

POST .../section-check and /upload, plus the section list. Stateless -- the
paste is never persisted, same rule as Draft Review, because it is the PI's
unpublished manuscript.

Deliberately does NOT 409 without a solicitation. That guard exists so a
completeness percentage is never computed against zero requirements; these rules
are NSF's rather than the solicitation's and no percentage is returned, so it
has nothing to protect. When a solicitation IS attached its own rows for the
section are checked alongside.

The upload path takes ONE file, because one file is one section -- which is what
makes the page count exact instead of a word-count estimate, and is the only
thing it can do that a paste cannot.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The frontend

**Files:**
- Create: `frontend/src/components/SectionCheckModal.jsx`, `frontend/src/components/SectionCheckModal.css`
- Modify: `frontend/src/components/MyProposals.jsx` (toolbar button; `nextStep` at line 61)
- Test: manual, in a fresh/incognito window (it is a PWA)

**Interfaces:**
- Consumes: `GET /api/me/section-check/sections`, `POST /api/me/submissions/{id}/section-check[/upload]`
- Produces: `<SectionCheckModal submission={} onClose={} />`

- [ ] **Step 1: Read the existing modal to match its patterns**

```bash
cd /Users/mingmalama/dev/ora-navigator/frontend/src/components
sed -n '1,80p' DraftReviewModal.jsx
grep -n "status\|badge\|chip" DraftReviewModal.css | head -30
```

Match its auth header construction, fetch error handling, status→class mapping and close-button markup exactly. Do not invent a second visual language.

- [ ] **Step 2: Build the modal**

The two calls, so the request shapes are not guessed at (match `DraftReviewModal`'s
own auth-header construction rather than copying this verbatim):

```jsx
// paste
const res = await fetch(`/api/me/submissions/${submission.id}/section-check`, {
  method: "POST",
  headers: { "Content-Type": "application/json", ...authHeaders() },
  body: JSON.stringify({ section, text, rulebook: "the PAPPG" }),
});
const { result } = await res.json();

// upload — ONE file, which is what makes the page count exact
const form = new FormData();
form.append("section", section);
form.append("rulebook", "the PAPPG");
form.append("file", file);
const res2 = await fetch(
  `/api/me/submissions/${submission.id}/section-check/upload`,
  { method: "POST", headers: authHeaders(), body: form });
```

`result` is `{section, label, rulebook, findings, mistakes, skeleton, score,
ai, word_count, message}`. `score` is **always `null`**. `skeleton` is `null`
for References Cited and Facilities. `findings[].status` is one of
`addressed | partial | not_found | clear | flagged | could_not_locate |
not_checked`.

`SectionCheckModal.jsx` renders, in order:

1. A section picker from `GET /api/me/section-check/sections` (default: Project Summary).
2. A paste box **and** a single-file upload, with the honest line: *"Upload the PDF if you want the page limit checked properly — from pasted text we can only estimate it."*
3. **The skeleton panel, collapsed by default**, headed by `skeleton.title`, with `skeleton.note` rendered above `skeleton.body` in a muted style. `note` says *"A structural example, not a real proposal"* and must never be hidden or truncated.
4. Findings, reusing Draft Review's row component and status classes.
5. `mistakes`, in their own block, under a heading that says these are errors and are **not** part of any score.

**Three UI rules from CLAUDE.md that apply directly here:**

- **No green ✓ on anything not actually assessed.** A located heading is not an approved section. `status: "not_checked"` (the page estimate) must render neutral — not as a pass, not as a failure.
- **A caveat belongs in exactly one place.** The delegation notice was on screen four times and a PI said it *"completely crowded the draft review"*. The skeleton note appears once, in the skeleton panel. Do not repeat it per finding.
- **No score anywhere in this modal.** `result.score` is always `null`; do not compute a percentage from the finding counts. The rules are NSF's floor, not a completeness universe, and a number here would read as *"your Project Summary is 60% done"*.

- [ ] **Step 3: Add the toolbar button and restore the writing step**

In `MyProposals.jsx`, add a **Check a section** button to the Build stage of the toolbar. Then in `nextStep(submission)` (line 61), insert a writing step ahead of Draft Review: when a solicitation is attached but no draft review has been saved, recommend **Check a section**.

CLAUDE.md records the gap this closes: *"`nextStep()` lost its writing step, so no tool now helps a PI write — only ones that check what they wrote."*

- [ ] **Step 4: Build the frontend**

```bash
cd frontend && npm run build
```
Expected: build succeeds with no new warnings.

- [ ] **Step 5: Verify in a browser**

Run the app, open a proposal in a **fresh/incognito window** (it is a PWA and a stale service worker will serve the old bundle), and confirm:
- the modal opens from the toolbar
- pasting the five-line summary reports the three missing headings
- the skeleton panel expands and shows its "not a real proposal" note
- the page-limit row renders neutral with the word "estimate"
- uploading a 3-page PDF flips that row to a real "3 pages, over the 1-page limit"
- **the app does not crash.** `DraftReviewModal` crashed the whole app for two days by dereferencing `result.solicitation.cycle` after `cycle` was removed. Every field this modal reads is optional — `skeleton` is `null` for References Cited and Facilities, which have no skeleton. Confirm those two sections render.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SectionCheckModal.jsx frontend/src/components/SectionCheckModal.css frontend/src/components/MyProposals.jsx
git commit -m "feat(ui): check one section while you write it

A per-section modal plus the Build-stage button, and nextStep gains back a
writing step -- since the Drafting Coach was removed no tool has helped a PI
write, only ones that check what they already wrote.

No score anywhere in this modal, deliberately: these rules are NSF's floor, not
a completeness universe, and a percentage would read as 'your Project Summary is
60% done'. The page-estimate row renders neutral rather than as a pass or a
fail, and the skeleton's 'a structural example, not a real proposal' note
appears exactly once -- the delegation caveat was on screen four times and a PI
said it crowded out the actual feedback.

Every field read is optional. DraftReviewModal took the whole app down for two
days dereferencing result.solicitation.cycle after cycle was removed; References
Cited and Facilities have no skeleton and must render.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Record it in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (the Draft Review section; the "PAPPG support is SHIPPED BUT INERT" bullet under Known limitations)

- [ ] **Step 1: Update the open item**

Under **Known limitations / open work**, the bullet beginning *"**PAPPG support is SHIPPED BUT INERT, on purpose.**"* is now wrong. Replace it with what is true: four sections' rules ship as a curated table sourced from Research.gov's login-gated section pages; Chapter II extraction remains unused and unsolved; the untried lever (slicing Chapter II's source text) is still untried and is the path to the other ten sections.

- [ ] **Step 2: Add the feature to the Draft Review section**

Record, in the house style — the failure first, then the fix, then what would break it:
- the rule and its verbatim quote
- why hand-curated is **forced** (the pages are behind NSF login; `kb_scraper` can never reach them)
- keyed on the rulebook, not the funder, and why that clears the grep gate
- the trigger is the citation, not the sponsor string, and the `Maryland Technology Tra-nsf-er Fund` bug it avoids
- retroactive via `load_solicitation_profile`, no migration
- **page rules are an estimate from a paste and never a verdict**
- **scores moved** on existing NSF proposals; saved reviews keep their stored numbers
- the false-positive guards and that each has a test
- the skeleton is not a sample proposal, and why (`sample_proposals` is link-only, we never rehost)

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): the PAPPG rules ship, and what would break them

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification before claiming done

- [ ] `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest -q --ignore=tests/test_agent_instruction.py` — green, and the count is **higher** than the **1033 passed / 3 skipped** measured on this branch before Task 1 (CLAUDE.md's "682" is stale; ignore it)
- [ ] `grep -ril "eir_solicitation\|23-598" backend/services/` — **empty**
- [ ] `cd frontend && npm run build` — succeeds
- [ ] The five-line Project Summary from the spec reports three missing headings, end to end in a browser
- [ ] A proposal whose solicitation cites **no** rulebook has an unchanged review — same findings, same score as before this work
- [ ] **Nothing pushed.** `git log origin/main..HEAD` shows the new commits sitting locally.
