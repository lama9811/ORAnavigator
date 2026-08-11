# Solicitation-driven Draft Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draft Review works on every proposal, judging the pasted draft against whatever solicitation that proposal has — NSF, NIH, DoE, anything — instead of only NSF 23-598.

**Architecture:** The existing four-stage engine (`eir_review.py`) is already generic in shape; it is hardcoded only because it imports `eir_solicitation` directly. We introduce a **solicitation profile** — sections + requirement rows + deterministic check callables — and parameterize the engine on it. A new extractor reads any solicitation PDF *completely* (chunked, sweep-until-dry, every row quote-verified) and produces that profile, which is stored on the submission in a new `solicitation_json` column and reviewed by the PI before it is saved.

**NSF 23-598 is removed from the product.** There is ONE code path — attach a solicitation, get reviewed against it — and no branch, entry point, endpoint, or UI check anywhere that knows which solicitation it is. `services/eir_review.py` and the eight NSF-specific check functions are **deleted**, not shimmed. `eir_solicitation.py` moves to `tests/fixtures/nsf_23_598.py`, where it is test data and nothing else: it is the only human-verified requirement list in the repo, so Task 8 measures the generic extractor against it. If it stayed under `services/`, the next reader would reasonably assume the product still special-cases NSF.

**Tech Stack:** FastAPI + SQLAlchemy (single-worker uvicorn), `services/gemini_client` → `gemini-3.6-flash` on `location="global"`, pdfplumber, React 19 + Vite.

## Global Constraints

- **Golden rule 1** — figures, statuses and the score are computed by code. The model assigns coverage and drafts prose; it never computes the number.
- **Golden rule 2** — every positive claim carries a verbatim quote, checked with `services.text_match.quote_in` (whitespace-collapsing). Unquotable → dropped/demoted.
- **Golden rule 3** — every AI path returns a deterministic result when Gemini is unavailable. `generate_json` returns `None`; never raise because the model is down.
- **Golden rule 4** — extracted requirements are returned for review and saved only on explicit confirm.
- **Golden rule 5** — new column is TEXT + `json.dumps/loads`, added with a self-healing migration in `main.py:init_db()`. No Alembic.
- **Golden rule 6** — one feature, one focused change. Do not refactor unrelated code.
- **Golden rule 7** — **never `git push`.** Commit locally only. A push to `main` fires `deploy-to-main` and ships to production.
- **`could_not_locate` / `not_checked` / `unclear` are NOT failures** and stay out of the score denominator. Reporting an unassessed requirement as missing is the single worst thing this feature can do.
- **Model pinning:** `gemini-3.6-flash` **404s in `us-central1`** — model and location move together. Reuse `MODEL` / `MODEL_LOCATION` env pairs; never pass the model without the location.
- **Test command (must stay green before any commit):**
  ```bash
  cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
    python3 -m pytest -q --ignore=tests/test_agent_instruction.py
  ```
- `tests/conftest.py` pins `gemini_client.get_client` to `None` for every test, so the AI layer is OFF by default. Tests that need the AI path patch `gemini_client.generate_json` directly.

## File Structure

| File | Responsibility |
|---|---|
| `backend/services/solicitation_profile.py` (new) | The shape the engine consumes: `make_profile`, `build_generic`, `sections_from`, `aliases_for`, `requirements_for`. No LLM, no DB. |
| `backend/services/generic_checks.py` (new) | Deterministic checks that work for any solicitation: page limit, attachment present, budget vs cap. |
| `backend/services/draft_review.py` (new, from `eir_review.py`) | The four-stage engine, parameterized on a profile. |
| `backend/services/eir_review.py` (**deleted**) | Was the NSF 23-598 entry point. Nothing replaces it — the generic path is the only path. |
| `backend/services/eir_solicitation.py` → `backend/tests/fixtures/nsf_23_598.py` | Leaves `services/` entirely. Read only by tests: the recall measurement (Task 8) and as realistic profile data for engine tests. |
| `backend/services/solicitation_requirements.py` (new) | Reads a whole solicitation and returns quote-verified requirement rows + a read report. |
| `backend/services/solicitation_extractor.py` (modify) | Add a read report; stop truncating silently. |
| `backend/models.py` (modify) | `Submission.solicitation_json`. |
| `backend/main.py` (modify) | Migration, serializer flag, four endpoints. |
| `backend/services/proposals_service.py` (modify) | `get_solicitation_profile` / `save_solicitation_profile`. |
| `frontend/src/components/DraftReviewModal.jsx` (from `EirReviewModal.jsx`) | Attach step + requirement list + read report + existing findings UI. |
| `frontend/src/components/MyProposals.jsx` (modify) | Delete `isEirProposal`; show Draft Review on every proposal. |

---

### Task 1: The solicitation profile

**Files:**
- Create: `backend/services/solicitation_profile.py`
- Test: `backend/tests/test_solicitation_profile.py`

**Interfaces:**
- Consumes: nothing. This module is data-shaping only — no LLM, no DB, no imports from other services.
- Produces:
  - `make_profile(*, id, title, url=None, sections, requirements, checks=None, merit_criteria=None, eligibility_notes=None) -> dict`
  - `aliases_for(label: str) -> list[str]`
  - `sections_from(requirements: list[dict], page_limits: dict | None = None, attachments: list[str] | None = None) -> dict`
  - `requirements_for(profile: dict, section: str | None) -> list[dict]`
  - Profile shape: `{"id","title","url","sections","requirements","checks","merit_criteria","eligibility_notes"}` where `sections` is `{key: {"label","aliases"}}` and `checks` is `{check_name: callable}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_solicitation_profile.py
from services import solicitation_profile as sp


def test_aliases_include_the_label_and_its_denumbered_form():
    aliases = sp.aliases_for("II. Project Description")
    assert "ii. project description" in aliases
    assert "project description" in aliases


def test_sections_are_built_from_the_requirement_rows_own_section_values():
    reqs = [
        {"id": "a", "label": "Research goals", "section": "project_description",
         "kind": "semantic", "scored": True, "source": "x", "why": "", "keywords": []},
        {"id": "b", "label": "Summary asks", "section": "project_summary",
         "kind": "semantic", "scored": True, "source": "y", "why": "", "keywords": []},
    ]
    sections = sp.sections_from(reqs)
    assert set(sections) == {"project_description", "project_summary"}
    assert sections["project_description"]["label"] == "Project Description"
    assert "project description" in sections["project_description"]["aliases"]


def test_sections_also_come_from_page_limits_and_attachments():
    sections = sp.sections_from([], page_limits={"data_management_plan": 2},
                                attachments=["Biosketch"])
    assert "data_management_plan" in sections
    assert "biosketch" in sections


def test_nothing_in_the_profile_module_knows_about_any_named_solicitation():
    # The point of the whole change: one path, no funder-specific branch. A
    # regression here would be someone re-adding a from_nsf()/from_nih() helper.
    import inspect
    src = inspect.getsource(sp).lower()
    for token in ("23-598", "eir", "hbcu", "excellence in research"):
        assert token not in src


def test_requirements_for_none_returns_the_whole_document_rows():
    reqs = [
        {"id": "a", "label": "A", "section": None, "kind": "semantic",
         "scored": True, "source": "x", "why": "", "keywords": []},
        {"id": "b", "label": "B", "section": "project_summary", "kind": "semantic",
         "scored": True, "source": "y", "why": "", "keywords": []},
    ]
    profile = sp.make_profile(id="X", title="X", sections={}, requirements=reqs)
    assert [r["id"] for r in sp.requirements_for(profile, None)] == ["a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_solicitation_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.solicitation_profile'`

- [ ] **Step 3: Write the implementation**

```python
# backend/services/solicitation_profile.py
"""The shape services/draft_review.py reviews against.

WHY THIS EXISTS
---------------
The reviewer used to import services/eir_solicitation directly, which is why it
only worked for NSF 23-598. A PROFILE is that same information as data, so the
engine can be handed NSF 23-598, an NIH FOA, or anything a PI attaches, and
behave identically.

A profile is deliberately a plain dict (no dataclass): it round-trips through
json.dumps into Submission.solicitation_json, and `checks` — the only
non-serializable member — is re-attached on load from code, never persisted.

DATA ONLY: no LLM, no network, no DB (mirrors forms_catalog.py).
"""

from __future__ import annotations

import re
from typing import Optional

_NUMBERING_RE = re.compile(r"^\s*(?:[\dIVXivx]+[.)]\s*)+")


def aliases_for(label: str) -> list[str]:
    """Headings a PI plausibly types for a section called `label`.

    The locate stage matches these against whole heading LINES, so they are
    lowercased variants, never substrings of prose."""
    label = " ".join((label or "").split())
    if not label:
        return []
    out = [label.lower()]
    bare = _NUMBERING_RE.sub("", label).strip().lower()
    if bare and bare not in out:
        out.append(bare)
    # "Letters of Collaboration" -> also match the singular a PI may write.
    if bare.endswith("s") and bare[:-1] not in out:
        out.append(bare[:-1])
    return out


def _section_key(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return key or "other"


def _section_label(key: str) -> str:
    return " ".join(w.capitalize() for w in key.split("_"))


def sections_from(requirements: list[dict], page_limits: Optional[dict] = None,
                  attachments: Optional[list] = None) -> dict:
    """Assemble the section universe the locate stage segments the draft into.

    Three sources, all already produced by extraction: the `section` each
    requirement row names, the sections the solicitation gives a page limit for,
    and the attachments it requires by name. A requirement whose section is not
    otherwise known still gets a section here, so it can never be dropped."""
    sections: dict = {}

    def add(raw_key: str, label: Optional[str] = None) -> None:
        key = _section_key(raw_key)
        if not key or key in sections:
            return
        lbl = label or _section_label(key)
        sections[key] = {"label": lbl, "aliases": aliases_for(lbl)}

    for req in requirements or []:
        if req.get("section"):
            add(str(req["section"]))
    for name in (page_limits or {}):
        add(str(name))
    for name in (attachments or []):
        add(str(name), label=str(name))
    return sections


def make_profile(*, id: str, title: str, url: Optional[str] = None,
                 sections: dict, requirements: list[dict],
                 checks: Optional[dict] = None,
                 merit_criteria: Optional[list] = None,
                 eligibility_notes: Optional[list] = None) -> dict:
    return {
        "id": id,
        "title": title,
        "url": url,
        "sections": sections or {},
        "requirements": list(requirements or []),
        "checks": checks or {},
        "merit_criteria": merit_criteria or [],
        "eligibility_notes": eligibility_notes or [],
    }


def requirements_for(profile: dict, section: Optional[str]) -> list[dict]:
    """Rows belonging to `section`; None -> the whole-document rows."""
    return [r for r in profile.get("requirements", []) if r.get("section") == section]


def scored_requirements(profile: dict) -> list[dict]:
    return [r for r in profile.get("requirements", []) if r.get("scored")]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python3 -m pytest tests/test_solicitation_profile.py -v`
Expected: PASS (all five). This task has no dependency on any later one.

- [ ] **Step 5: Commit**

```bash
git add backend/services/solicitation_profile.py backend/tests/test_solicitation_profile.py
git commit -m "feat(review): a solicitation profile the reviewer can be handed"
```

---

### Task 2: Delete the NSF 23-598 path, parameterize the engine

The engine keeps every behavior it has; what goes is the funder-specific half.
**This task DELETES code and DELETES tests** — read the coverage note in Step 1
before starting, because some of what is removed is genuinely useful and its
removal is a decision, not an accident.

**Files:**
- Create: `backend/services/draft_review.py` (git mv from `backend/services/eir_review.py`)
- Create: `backend/tests/fixtures/__init__.py`, `backend/tests/fixtures/nsf_23_598.py` (git mv from `backend/services/eir_solicitation.py`)
- Create: `backend/tests/test_draft_review.py` (engine behavior, ported)
- Delete: `backend/services/eir_review.py` (the shim is NOT kept), `backend/tests/test_eir_review.py`
- Test: `backend/tests/test_draft_review.py`, `backend/tests/test_draft_review_generic.py` (new)

**Interfaces:**
- Consumes: `solicitation_profile.make_profile`, `.requirements_for`, `.sections_from` from Task 1.
- Produces:
  - `draft_review.review_draft(draft_text, *, profile, title=None, budget=None, use_ai=True) -> dict`
  - `draft_review.locate_sections(text, sections, *, use_ai=True) -> tuple[dict, bool]`
  - `draft_review.run_deterministic(text, spans, profile, *, title=None, budget=None) -> list[dict]`
  - `draft_review.score(findings, *, solicitation_id) -> dict | None`
  - `draft_review.MAX_DRAFT_CHARS: int` (unchanged, 120_000)
  - No `review_eir_draft` anywhere. No module under `services/` mentions NSF 23-598.

- [ ] **Step 1: Delete the NSF-specific code, and know what goes with it**

```bash
cd backend
git mv services/eir_review.py services/draft_review.py
mkdir -p tests/fixtures && touch tests/fixtures/__init__.py
git mv services/eir_solicitation.py tests/fixtures/nsf_23_598.py
```

Delete from `draft_review.py` the eight `_check_*` functions, the
`_DETERMINISTIC_CHECKS` table, and the regexes and constants only they used:
`_LOI_RE`, `_SUPPORT_LETTER_RE`, `_COST_SHARE_RE`, `_NEGATION_WINDOW`,
`_NEGATIONS`, `_COLLAB_SPINE`, `_DC_TRAVEL_RE`, `_EVERY_YEAR_RE`,
`_negated_nearby`, `WORDS_PER_PAGE`.

**COVERAGE THAT GOES AWAY, deliberately.** `tests/test_eir_review.py` holds 50
tests. Roughly 18 of them test the eight deleted checks — the NSF title prefix,
the LOI number in the Project Summary, the 30% equipment cap arithmetic, the
prohibited letters of support, the cost-sharing negation window ("no cost
sharing is included" must not be flagged as offering it), the mandated
collaboration sentence, and the DC grantee-meeting travel rule. Those tests are
deleted with the code they test. **This is real capability leaving the product**,
accepted as the price of one uniform path: for an EiR proposal those rules now
reach a PI only if the extractor pulls them out of the PDF as quoted semantic
rows. The remaining ~30 tests cover the ENGINE and are ported in Step 3 — do not
delete those.

`tests/fixtures/nsf_23_598.py` needs one edit after the move: it is test data, so
replace its module docstring's "This module is DATA ONLY" preamble with a note
saying it is no longer imported by any service, that it is the only
human-verified requirement list in the repo, and that Task 8's recall test is
what it is for. Nothing under `backend/services/` may import it — a grep for
`eir_solicitation` outside `tests/` must come back empty.

- [ ] **Step 2: Replace every `sol.` reference in `draft_review.py` with the profile**

Mechanical, six places. `sol.SECTIONS` → the `sections` argument;
`sol.EIR_REQUIREMENTS` → `profile["requirements"]`; `sol.requirements_for(k)` →
`solicitation_profile.requirements_for(profile, k)`; `sol.MERIT_CRITERIA` →
`profile["merit_criteria"]`; `sol.ELIGIBILITY_NOTES` →
`profile["eligibility_notes"]`; `_solicitation_meta()` → returns
`{"id","title","url"}` off the profile. The `cycle` key it used to carry
(`loi_deadline` / `full_proposal_deadline`) is **dropped entirely** — those dates
are computed from NSF 23-598's recurrence rules and have no generic equivalent;
the proposal's own `deadline` field already carries what a PI needs.
`run_deterministic` resolves each row through `profile["checks"]`, falling back
to `generic_checks.CHECKS`:

```python
def run_deterministic(text: str, spans: dict, profile: dict, *,
                      title: Optional[str] = None,
                      budget: Optional[dict] = None) -> list[dict]:
    """Every code-decided requirement. No model involved, so these findings are
    identical whether or not Gemini is reachable (golden rule 1)."""
    from services import generic_checks
    ctx = {"text": text or "", "spans": spans or {}, "title": title,
           "budget": budget, "profile": profile}
    out = []
    for req in profile.get("requirements", []):
        if req["kind"] != "deterministic":
            continue
        fn = profile.get("checks", {}).get(req.get("check", "")) \
            or generic_checks.CHECKS.get(req.get("check", ""))
        if fn is None:
            continue
        status, detail, evidence = fn(ctx, req)
        out.append(_finding(req, status, detail, evidence, source="check"))
    return out
```

Three strings in the prompts name NSF 23-598 and must become the profile's id:
`_REVIEW_SYSTEM`'s "from solicitation NSF 23-598", `_review_section`'s
`f"SECTION: ... of an NSF HBCU-EiR proposal (solicitation {sol.SOLICITATION_ID})"`,
and `score()`'s `basis` string. Make `_REVIEW_SYSTEM` a function
`_review_system(solicitation_id)`; pass the id into `score()`.

Also generalize the two hardcoded EiR facts in the assembly step:
`_project_description_span` keys off `"project_description"` / `"broader_impacts"`
— guard both with `if key in profile["sections"]` so a solicitation without a
Broader Impacts section is unaffected.

- [ ] **Step 3: Port the ~30 engine tests, delete the rest**

```bash
cd backend && git mv tests/test_eir_review.py tests/test_draft_review.py
```

`test_draft_review.py` keeps the tests that exercise the ENGINE and drops the
ones that exercised the deleted checks. Keep and re-point:

- **Locate** — `test_heading_fallback_locates_sections_without_ai`,
  `test_located_spans_do_not_overlap_and_follow_document_order`,
  `test_a_mention_in_a_paragraph_is_not_a_heading`,
  `test_missing_section_is_reported_as_unlocated_not_missing`,
  `test_unlocated_requirements_are_excluded_from_the_score`
- **Span assembly** — the three `project_description_span` tests
- **Grounding** — `test_addressed_without_a_verifiable_quote_is_demoted`,
  `test_addressed_with_a_real_quote_survives`,
  `test_hard_wrapped_draft_still_matches_the_quote`,
  `test_model_cannot_invent_a_requirement`,
  `test_a_requirement_the_model_skipped_is_unclear_not_missing`,
  `test_an_unrecognised_status_falls_back_to_not_found`
- **Score** — `test_score_is_arithmetic_over_coverage`,
  `test_unscored_conditional_requirements_do_not_count_against_the_draft`,
  `test_score_bands`, `test_score_is_none_when_nothing_was_assessed`,
  `test_a_flagged_prohibition_costs_points`
- **Assembly / offline** — `test_offline_suppresses_the_score_but_keeps_the_rule_checks`,
  `test_offline_semantic_rows_are_unclear_never_a_hard_absence_claim`,
  `test_empty_paste_returns_a_prompt_not_a_zero_score`,
  `test_every_requirement_gets_exactly_one_finding`,
  `test_findings_follow_solicitation_order`,
  `test_every_finding_carries_its_solicitation_source_text`,
  `test_result_reports_which_sections_were_found_and_which_were_not`

Delete: the three `*_deadline*` tests, the eighteen tests of the deleted checks
(title prefix, LOI number, support letters, cost sharing, equipment cap, DC
travel, collaboration letters, institutional letter length),
`test_solicitation_metadata_points_at_the_next_open_cycle`,
`test_conditional_requirements_are_marked_unscored_in_the_solicitation`, and
`test_every_deterministic_requirement_has_a_real_check_function`.

Each kept test changes only in how it gets its subject: instead of calling
`review_eir_draft(...)`, it builds a profile from the fixture's semantic rows —
realistic data, no NSF code path — via one shared helper at the top of the file:

```python
from tests.fixtures import nsf_23_598 as fx
from services import draft_review, solicitation_profile as sp


def fixture_profile():
    """A realistic profile from human-verified NSF 23-598 rows.

    Test DATA only: nothing under services/ imports this, and the engine cannot
    tell these rows from ones the extractor produced. Using real requirement
    text keeps these tests honest — synthetic one-line requirements would not
    exercise the locate stage the way a real heading does."""
    rows = [r for r in fx.EIR_REQUIREMENTS if r["kind"] == "semantic"]
    return sp.make_profile(
        id=fx.SOLICITATION_ID, title=fx.SOLICITATION_TITLE, url=fx.SOLICITATION_URL,
        sections=dict(fx.SECTIONS), requirements=rows,
        merit_criteria=list(fx.MERIT_CRITERIA))
```

- [ ] **Step 4: Write the generic-path test**

```python
# backend/tests/test_draft_review_generic.py
"""The engine, driven by a profile that is NOT NSF 23-598."""
from services import draft_review, solicitation_profile as sp

DRAFT = """Project Summary
We will study salt-tolerant polymers for coastal sensing.

Research Strategy
Our specific aims are to synthesize and characterize three polymer families.
Four undergraduates per year will be trained in materials characterization.
"""

REQS = [
    {"id": "aims", "label": "Specific aims", "section": "research_strategy",
     "kind": "semantic", "scored": True, "source": "State the specific aims.",
     "why": "", "keywords": ["specific aim", "aims"]},
    {"id": "training", "label": "Student training plan", "section": "research_strategy",
     "kind": "semantic", "scored": True, "source": "Describe student training.",
     "why": "", "keywords": ["undergraduate", "train"]},
]


def _profile():
    return sp.make_profile(
        id="PAR-24-118", title="NIH Research Project Grant", url=None,
        sections=sp.sections_from(REQS + [{"section": "project_summary"}]),
        requirements=REQS)


def test_a_non_eir_profile_locates_its_own_sections_offline():
    result = draft_review.review_draft(DRAFT, profile=_profile(), use_ai=False)
    located = {s["key"] for s in result["sections_located"]}
    assert "research_strategy" in located
    assert result["solicitation"]["id"] == "PAR-24-118"


def test_the_score_is_withheld_when_the_ai_layer_is_offline():
    # Golden rule 3 + the EiR rule: a percentage computed without the semantic
    # half reads as a verdict on the draft, not on our availability.
    result = draft_review.review_draft(DRAFT, profile=_profile(), use_ai=False)
    assert result["score"] is None
    assert all(f["status"] == "unclear" for f in result["findings"])


def test_no_finding_is_ever_reported_against_a_requirement_the_profile_lacks():
    result = draft_review.review_draft(DRAFT, profile=_profile(), use_ai=False)
    assert {f["id"] for f in result["findings"]} <= {"aims", "training"}
```

- [ ] **Step 5: Run the ported suites and prove nothing under services/ names NSF**

Run:
```bash
cd backend && python3 -m pytest tests/test_draft_review.py \
  tests/test_draft_review_generic.py tests/test_solicitation_profile.py -v
grep -ril "eir_solicitation\|23-598\|hbcu" services/ ; echo "exit=$?"
```
Expected: tests PASS, and the grep prints **nothing** (`exit=1`). A hit means a
funder-specific branch survived, which is the one thing this task exists to
remove.

- [ ] **Step 6: Delete the old API tests, run the full suite, commit**

`tests/test_eir_review_api_e2e.py` targets the `/eir-review` routes, which Task 6
deletes. Leave it in place for now — it still passes, because the routes are
still there until Task 6 — and delete it there, so this task's commit stays a
clean engine change.

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
git add -A backend/services backend/tests
git commit -m "refactor(review): drop the NSF 23-598 path; the reviewer takes a profile"
```

---

### Task 3: Deterministic checks that work for any solicitation

**Files:**
- Create: `backend/services/generic_checks.py`
- Modify: `backend/services/solicitation_profile.py` (add `build_generic`)
- Test: `backend/tests/test_generic_checks.py`

**Interfaces:**
- Consumes: the `fn(ctx, req)` check signature from Task 2; `ctx` keys `text`, `spans`, `title`, `budget`, `profile`.
- Produces:
  - `generic_checks.CHECKS: dict[str, callable]` with keys `page_limit`, `attachment_present`, `budget_cap`.
  - `generic_checks.contract_requirements(contract: dict) -> list[dict]` — deterministic rows built from `page_limits`, `required_attachments`, `budget_cap`, each carrying `check` and `check_args`.
  - `solicitation_profile.build_generic(contract: dict, requirements: list[dict], *, id: str, title: str, url: str | None = None) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_generic_checks.py
from services import generic_checks as gc


def _ctx(**kw):
    base = {"text": "", "spans": {}, "title": None, "budget": None, "profile": {}}
    base.update(kw)
    return base


def test_contract_rows_are_built_for_limits_attachments_and_cap():
    rows = gc.contract_requirements({
        "page_limits": {"project_description": 12},
        "required_attachments": ["Data Management Plan"],
        "budget_cap": 400000,
    })
    checks = {r["check"] for r in rows}
    assert checks == {"page_limit", "attachment_present", "budget_cap"}
    assert all(r["kind"] == "deterministic" for r in rows)
    # Every row must quote the contract fact it came from (golden rule 2).
    assert all(r["source"] for r in rows)


def test_a_page_limit_over_run_is_reported_with_the_estimate_flagged():
    req = {"id": "pl", "label": "x", "check": "page_limit",
           "check_args": {"section": "project_description", "limit": 1}}
    ctx = _ctx(spans={"project_description": {"text": "word " * 1200}})
    status, detail, _ = gc.CHECKS["page_limit"](ctx, req)
    assert status == "not_found"
    assert "estimate" in detail.lower()


def test_a_missing_section_is_not_located_never_over_the_limit():
    # The distinction the whole feature rests on: we did not find it is NOT
    # the same claim as it is too long.
    req = {"id": "pl", "label": "x", "check": "page_limit",
           "check_args": {"section": "project_description", "limit": 1}}
    status, _, _ = gc.CHECKS["page_limit"](_ctx(), req)
    assert status == "could_not_locate"


def test_an_attachment_named_in_the_draft_is_addressed():
    req = {"id": "at", "label": "Data Management Plan", "check": "attachment_present",
           "check_args": {"name": "Data Management Plan"}}
    ctx = _ctx(text="DATA MANAGEMENT PLAN\nAll data will be deposited...")
    status, _, evidence = gc.CHECKS["attachment_present"](ctx, req)
    assert status == "addressed"
    assert evidence


def test_budget_over_cap_is_reported_and_no_budget_is_not_checked():
    req = {"id": "bc", "label": "cap", "check": "budget_cap",
           "check_args": {"cap": 100000}}
    over, _, _ = gc.CHECKS["budget_cap"](_ctx(budget={"total": 150000}), req)
    assert over == "not_found"
    none, detail, _ = gc.CHECKS["budget_cap"](_ctx(), req)
    assert none == "not_checked"
    assert "Budget Helper" in detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_generic_checks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.generic_checks'`

- [ ] **Step 3: Write the implementation**

```python
# backend/services/generic_checks.py
"""Deterministic checks that hold for ANY solicitation.

These are the rules whose input is a NUMBER the contract already carries — page
limits, required attachment names, the budget cap — so they are decided by code
for every funder (golden rule 1). This module is the WHOLE deterministic layer:
there is no per-funder check table. A solicitation-specific rule nobody extracted
a number for (a 30% equipment ceiling, a "no cost sharing" prohibition) reaches
the PI as a quoted semantic row instead, judged by the model rather than by
arithmetic. That is the accepted cost of one uniform path — see the design doc.

Signature contract: fn(ctx, req) -> (status, detail, evidence). `req` carries
`check_args`, so one table serves every check without per-funder wiring.
"""

from __future__ import annotations

import re

# ~550 words/page at typical federal formatting (11pt, single-spaced, 1" margins).
# Used ONLY to estimate a pasted section's length; every message that uses it says
# it is an estimate, because a formatted PDF is the authority.
WORDS_PER_PAGE = 550


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split())


def contract_requirements(contract: dict) -> list[dict]:
    """Deterministic rows from the extracted contract's hard numbers."""
    rows: list[dict] = []
    for section, limit in (contract.get("page_limits") or {}).items():
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            continue
        label = " ".join(w.capitalize() for w in str(section).split("_"))
        rows.append({
            "id": f"page_limit_{section}",
            "label": f"{label} within its {limit}-page limit",
            "section": str(section),
            "kind": "deterministic", "scored": True,
            "check": "page_limit",
            "check_args": {"section": str(section), "limit": limit},
            "source": f"{label}: {limit} page limit, as extracted from the solicitation.",
            "why": "Over-length sections are returned without review by most funders.",
            "keywords": [],
        })
    for name in (contract.get("required_attachments") or []):
        name = str(name).strip()
        if not name:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        rows.append({
            "id": f"attachment_{key}",
            "label": f"{name} included",
            "section": None,
            "kind": "deterministic", "scored": True,
            "check": "attachment_present",
            "check_args": {"name": name},
            "source": f"The solicitation lists {name} as a required attachment.",
            "why": "A missing required attachment is a compliance rejection, not a weakness.",
            "keywords": [],
        })
    cap = contract.get("budget_cap")
    if cap:
        rows.append({
            "id": "budget_within_cap",
            "label": f"Budget within the ${int(cap):,} cap",
            "section": None,
            "kind": "deterministic", "scored": True,
            "check": "budget_cap",
            "check_args": {"cap": int(cap)},
            "source": f"The solicitation caps an award at ${int(cap):,}.",
            "why": "Over-cap budgets are returned without review.",
            "keywords": [],
        })
    return rows


def _check_page_limit(ctx: dict, req: dict) -> tuple:
    args = req.get("check_args") or {}
    span = ctx["spans"].get(args.get("section"))
    if not span:
        # NOT a failure. Saying "over the limit" about a section we never found
        # would be a confident, wrong claim.
        return "could_not_locate", (
            "That section was not found in what you pasted, so its page limit "
            "was not checked."), ""
    limit = args.get("limit")
    words = len(span["text"].split())
    pages = words / WORDS_PER_PAGE
    if pages <= limit:
        return "addressed", (
            f"About {words:,} words ≈ {pages:.1f} pages (estimated), within the "
            f"{limit}-page limit."), ""
    return "not_found", (
        f"About {words:,} words ≈ {pages:.1f} pages (estimated), over the "
        f"{limit}-page limit. This is an estimate from word count — check the "
        "formatted PDF."), ""


def _check_attachment_present(ctx: dict, req: dict) -> tuple:
    name = ((req.get("check_args") or {}).get("name") or "").strip()
    if not name:
        return "not_checked", "No attachment name to look for.", ""
    text = ctx.get("text") or ""
    pattern = r"\s+".join(re.escape(tok) for tok in name.split())
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return "addressed", f"Found {name} in what you pasted.", \
            _norm_ws(text[max(0, m.start() - 40):m.end() + 60])[:160]
    return "not_found", (
        f"No {name} found in what you pasted. If you have it as a separate file, "
        "upload it with the rest of the package so it can be checked."), ""


def _check_budget_cap(ctx: dict, req: dict) -> tuple:
    cap = (req.get("check_args") or {}).get("cap")
    budget = ctx.get("budget")
    if not budget:
        return "not_checked", (
            "No budget saved for this proposal yet. Build one in the Budget Helper "
            "and this check runs against real numbers."), ""
    total = float(budget.get("total") or 0.0)
    if total <= 0:
        return "not_checked", "The saved budget has no total to measure against.", ""
    if total <= cap:
        return "addressed", f"${total:,.0f} requested, within the ${cap:,} cap.", ""
    return "not_found", (
        f"${total:,.0f} requested, ${total - cap:,.0f} over the ${cap:,} cap."), ""


CHECKS = {
    "page_limit": _check_page_limit,
    "attachment_present": _check_attachment_present,
    "budget_cap": _check_budget_cap,
}
```

Append to `services/solicitation_profile.py`:

```python
def build_generic(contract: dict, requirements: list[dict], *, id: str,
                  title: str, url: Optional[str] = None) -> dict:
    """A profile for an arbitrary solicitation: extracted narrative requirements
    plus the deterministic rows its contract's hard numbers support."""
    from services import generic_checks
    rows = list(requirements or []) + generic_checks.contract_requirements(contract or {})
    return make_profile(
        id=id, title=title, url=url,
        sections=sections_from(rows,
                               page_limits=(contract or {}).get("page_limits"),
                               attachments=(contract or {}).get("required_attachments")),
        requirements=rows,
        checks=generic_checks.CHECKS,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python3 -m pytest tests/test_generic_checks.py tests/test_solicitation_profile.py tests/test_draft_review_generic.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/generic_checks.py backend/services/solicitation_profile.py backend/tests/test_generic_checks.py
git commit -m "feat(review): deterministic page-limit, attachment and cap checks for any solicitation"
```

---

### Task 4: Read the whole solicitation — chunked, swept, quote-verified

This is the task the user's "fully read the PDF without missing anything" turns on.

**Files:**
- Create: `backend/services/solicitation_requirements.py`
- Modify: `backend/services/solicitation_extractor.py` (read report + no silent truncation)
- Test: `backend/tests/test_solicitation_requirements.py`

**Interfaces:**
- Consumes: `gemini_client.generate_json(prompt, temperature, max_output_tokens, timeout_s, system_instruction, model, location)`; `services.text_match.quote_in(haystack, needle) -> bool`.
- Produces:
  - `solicitation_requirements.extract_requirements(text: str, *, use_ai: bool = True, max_rounds: int = 3) -> dict` returning `{"requirements": [...], "read": {...}, "rounds": int, "dropped_unverified": int, "hit_round_cap": bool}`
  - `solicitation_requirements.chunk_text(text: str, size: int = 60_000, overlap: int = 4_000) -> list[str]`
  - `solicitation_extractor.read_pdf(pdf_bytes: bytes) -> dict` returning `{"text","pages","pages_without_text","chars","engine"}`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_solicitation_requirements.py
from services import solicitation_requirements as sr

SOLICITATION = (
    "The Project Description must include a sustainability plan describing how the "
    "work leads to a future core-program submission. " * 40
    + "\nProposals must include a Data Management Plan of no more than two pages.\n"
)


def test_chunking_covers_every_character_of_the_document():
    text = "".join(f"line {i}\n" for i in range(20_000))
    chunks = sr.chunk_text(text, size=10_000, overlap=500)
    assert len(chunks) > 1
    # Nothing may be lost between windows — the failure this whole task exists
    # to prevent.
    assert text[:200] in chunks[0]
    assert text[-200:] in chunks[-1]
    joined = "".join(chunks)
    assert len(joined) >= len(text)


def test_a_row_whose_quote_is_not_in_the_document_is_dropped(monkeypatch):
    from services import gemini_client

    def fake(prompt, **kw):
        return {"requirements": [
            {"label": "Sustainability plan", "section": "project_description",
             "source": "must include a sustainability plan", "why": "", "keywords": [],
             "scored": True},
            {"label": "Invented ask", "section": "project_description",
             "source": "the proposal must include a haiku about polymers",
             "why": "", "keywords": [], "scored": True},
        ]}

    monkeypatch.setattr(gemini_client, "generate_json", fake)
    out = sr.extract_requirements(SOLICITATION, max_rounds=1)
    labels = {r["label"] for r in out["requirements"]}
    assert "Sustainability plan" in labels
    assert "Invented ask" not in labels        # golden rule 2
    assert out["dropped_unverified"] == 1


def test_the_sweep_keeps_going_until_a_round_adds_nothing(monkeypatch):
    from services import gemini_client
    calls = {"n": 0}

    def fake(prompt, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"requirements": [
                {"label": "Sustainability plan", "section": "project_description",
                 "source": "must include a sustainability plan", "why": "",
                 "keywords": [], "scored": True}]}
        if calls["n"] == 2:
            return {"requirements": [
                {"label": "Data Management Plan", "section": "data_management_plan",
                 "source": "Data Management Plan of no more than two pages",
                 "why": "", "keywords": [], "scored": True}]}
        return {"requirements": []}

    monkeypatch.setattr(gemini_client, "generate_json", fake)
    out = sr.extract_requirements(SOLICITATION, max_rounds=5)
    assert len(out["requirements"]) == 2
    assert out["hit_round_cap"] is False


def test_ids_are_stable_and_unique():
    rows = [{"label": "Sustainability plan", "section": "project_description"},
            {"label": "Sustainability  plan", "section": "project_description"}]
    ids = [sr.make_id(r) for r in rows]
    assert ids[0] == ids[1]        # normalized, so the dedup below can fire


def test_offline_returns_no_requirements_and_says_so():
    # Golden rule 3: never raise, never fabricate. An empty list plus ai=False is
    # the honest answer.
    out = sr.extract_requirements(SOLICITATION, use_ai=False)
    assert out["requirements"] == []
    assert out["ai"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_solicitation_requirements.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.solicitation_requirements'`

- [ ] **Step 3: Write the implementation**

```python
# backend/services/solicitation_requirements.py
"""Turn ANY solicitation's text into quote-verified requirement rows.

WHY THIS IS NOT ONE GEMINI CALL
-------------------------------
Two measured failure modes, both silent:

1. INPUT. solicitation_extractor.extract_from_text does text[:250_000] with no
   flag. A long FOA loses its tail, and the extractor's own prompt notes that
   the load-bearing facts appear late. Here the document is CHUNKED instead, so
   nothing is dropped, and what could not be read is reported.

2. EXTRACTION. One pass drops requirements even with the whole text in the
   prompt: on NSF 23-598, gemini-3.6-flash returned 3 required attachments on
   one run and 5 on the next, identical input, temperature 0 (CLAUDE.md). So we
   chunk, union, then SWEEP — hand the model what we have and ask what is
   missing — until a round adds nothing.

Every row must quote the solicitation verbatim (golden rule 2); unquotable rows
are dropped, and the count is reported rather than hidden. The model can widen
the requirement list but can never invent an ask that is not in the document.

Cost: several calls per solicitation. Affordable because this runs ONCE at
attach time and is stored — never per review.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from services import gemini_client
from services.text_match import quote_in

MODEL = os.getenv("SOLICITATION_REQUIREMENTS_MODEL", "gemini-3.6-flash")
MODEL_LOCATION = os.getenv("SOLICITATION_REQUIREMENTS_LOCATION", "global")

# Well inside the model's window, so a chunk is never the binding constraint.
CHUNK_CHARS = 60_000
CHUNK_OVERLAP = 4_000

_SYSTEM = (
    "You extract, for a university research office, EVERY requirement a grant "
    "solicitation places on an applicant's proposal. You output DATA ONLY from the "
    "text given to you.\n\n"

    "WHAT COUNTS AS A REQUIREMENT\n"
    "A requirement is anything the applicant must INCLUDE in, DO for, or ADDRESS in "
    "their proposal: content, structure, format, limits, prohibitions, and conditions "
    "the proposal itself must demonstrate.\n"
    "Signal words, none of which may be skimmed past: must, shall, should, is/are "
    "required, will, is expected to, needs to, include, provide, describe, address, "
    "submit, attach, specify, identify, demonstrate, no more than, at least, may not, "
    "is prohibited, is not allowed, limited to.\n"
    "Requirements ALSO appear with none of those words — as bare imperative headings, "
    "and as the items of a list introduced by something like 'The Project Description "
    "must contain the following:'. Every item of such a list is its own requirement.\n\n"

    "COMPLETENESS IS THE POINT OF THIS TASK\n"
    "1. Read the text you are given from its FIRST line to its LAST. Do not stop "
    "early, do not sample, do not summarise. A partial answer here tells a faculty "
    "member their proposal is complete when it is not.\n"
    "2. There is no maximum. If the text holds forty requirements, return forty. Never "
    "trim the list for brevity, or because the items feel repetitive.\n"
    "3. Read the parts that are easy to skip: bulleted and numbered lists, tables, "
    "figure and table captions, footnotes, endnotes, appendices, boxed notes, "
    "parenthetical asides, and text under headings that look administrative.\n"
    "4. SPLIT, never merge. 'Include a timeline and a description of experimental "
    "methods' is TWO requirements. One row per distinct ask, even when several share "
    "one sentence.\n"
    "5. If the same ask is restated later with MORE specificity, return the specific "
    "statement as its own row and quote that statement.\n"
    "6. When the text imposes a requirement by pointing elsewhere ('prepared in "
    "accordance with PAPPG Chapter II.D.2'), return the pointer itself as a "
    "requirement, quoted. Never invent what the referenced document says.\n"
    "7. If a passage genuinely contains no requirement, return an empty array. Never "
    "manufacture one to appear thorough.\n\n"

    "QUOTING — ENFORCED IN CODE, NOT A STYLE NOTE\n"
    "8. 'source' MUST be a VERBATIM quote from the text given, copied character for "
    "character, <=300 characters. Do not paraphrase, do not tidy it, do not correct "
    "typos, do not join distant sentences. Every row is checked against the document "
    "and a row whose quote is not found there is DISCARDED — so an imperfect copy "
    "destroys a real requirement.\n\n"

    "FIELDS\n"
    "9. 'section' — the part of the proposal the requirement belongs to, snake_case, "
    "named as THIS solicitation names it (project_description, research_strategy, "
    "project_summary, budget_justification, data_management_plan, ...). Use null when "
    "it applies to the proposal as a whole. Never translate one funder's vocabulary "
    "into another's.\n"
    "10. 'scored' — false ONLY where the text marks the ask conditional or optional "
    "('if applicable', 'if available', 'where appropriate', 'optional', 'may'). "
    "Otherwise true. Return conditional rows; never drop them.\n"
    "11. 'label' — a short imperative name (<=80 chars) for what the applicant must "
    "do. 'why' — one sentence on why a reviewer cares. 'keywords' — lowercase words or "
    "phrases a real draft would use for this.\n\n"

    "OUT OF SCOPE\n"
    "12. Do NOT extract administrative metadata: submission deadlines, submission "
    "portals, award sizes and durations, contact names, agency background, review "
    "timelines. Those are captured separately. A LIMIT the proposal must respect (a "
    "page count, a budget ceiling the narrative must fit) IS in scope."
)

# The sweep runs with a DIFFERENT job description. Asking the same prompt again
# returns the same list; asking "what did the first reader miss?" is what finds
# the tail. The measured failure — 3 attachments on one pass, 5 on the next from
# identical input — is a recall problem, so the second reader is told that
# recall, not tidiness, is what it is for.
_SWEEP_SYSTEM = (
    "You audit a requirement list against the solicitation text it was extracted "
    "from, and report ONLY what is missing from it.\n\n"
    "You are the second reader. The first pass over this text missed requirements — "
    "that is why you exist. 'Nothing is missing' is the right answer only when it is "
    "true.\n"
    "1. Work through the text from its first line to its last. For each requirement "
    "you find, check the ALREADY EXTRACTED list: if it is there, ignore it; if it is "
    "not, return it.\n"
    "2. Match by MEANING, not wording. A different phrasing of an ask already on the "
    "list is not missing.\n"
    "3. Look hardest where a first pass fails: items after the third or fourth bullet "
    "of a long list, requirements inside tables and footnotes, asks buried mid-"
    "paragraph after a long descriptive passage, the second and third clauses of "
    "compound sentences, and anything in the last third of the text.\n"
    "4. An empty array is a valid and common answer. Never pad it with rephrasings of "
    "rows already on the list, and never invent.\n"
    "5. Same quoting rule, enforced the same way: 'source' is a VERBATIM quote from "
    "this text, <=300 characters. Unquotable rows are discarded."
)

_SCHEMA_HINT = (
    'Return JSON: {"requirements": [{"label": "<short imperative name, <=80 chars>", '
    '"section": "<snake_case section or null>", "source": "<verbatim quote>", '
    '"why": "<one sentence: why a reviewer cares>", '
    '"keywords": ["<lowercase word or phrase a draft would use>", ...], '
    '"scored": true}]}'
)


def chunk_text(text: str, size: int = CHUNK_CHARS,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Overlapping windows covering the WHOLE document.

    Overlap exists because a requirement sentence split across a boundary would
    be quotable in neither half and therefore dropped by the verifier — a silent
    loss, which is the failure class this module exists to remove."""
    text = text or ""
    if len(text) <= size:
        return [text] if text else []
    step = max(1, size - overlap)
    return [text[i:i + size] for i in range(0, len(text), step)]


def make_id(row: dict) -> str:
    """Deterministic id from section + label, so re-extraction of the same
    solicitation produces the same ids and dedup works across chunks."""
    section = re.sub(r"[^a-z0-9]+", "_", str(row.get("section") or "general").lower()).strip("_")
    label = re.sub(r"[^a-z0-9]+", "_", str(row.get("label") or "").lower()).strip("_")
    return f"{section}_{label}"[:80] or "requirement"


def _coerce(row: dict) -> Optional[dict]:
    if not isinstance(row, dict):
        return None
    label = " ".join(str(row.get("label") or "").split())[:120]
    source = " ".join(str(row.get("source") or "").split())[:300]
    if not label or not source:
        return None
    section = row.get("section")
    section = str(section).strip().lower() or None if section else None
    keywords = [str(k).strip().lower() for k in (row.get("keywords") or [])
                if str(k).strip()][:8]
    return {
        "id": make_id({"label": label, "section": section}),
        "label": label,
        "section": section,
        "kind": "semantic",
        "scored": bool(row.get("scored", True)),
        "source": source,
        "why": " ".join(str(row.get("why") or "").split())[:300],
        "keywords": keywords,
    }


def _ask(prompt: str, system: str = _SYSTEM) -> list:
    ai = gemini_client.generate_json(
        prompt, temperature=0.0, max_output_tokens=8192, timeout_s=120,
        system_instruction=system, model=MODEL, location=MODEL_LOCATION)
    if not ai or not isinstance(ai.get("requirements"), list):
        return []
    return ai["requirements"]


def extract_requirements(text: str, *, use_ai: bool = True,
                         max_rounds: int = 3) -> dict:
    """Every requirement in `text`, each backed by a verbatim quote."""
    text = text or ""
    out: dict = {"requirements": [], "ai": False, "rounds": 0,
                 "dropped_unverified": 0, "hit_round_cap": False,
                 "chunks": 0, "chars": len(text)}
    if not text.strip() or not use_ai:
        return out

    chunks = chunk_text(text)
    out["chunks"] = len(chunks)
    by_id: dict = {}
    dropped = 0

    def absorb(rows: list) -> int:
        nonlocal dropped
        added = 0
        for raw in rows:
            row = _coerce(raw)
            if row is None:
                continue
            # GOLDEN RULE 2 — verified against the WHOLE document, not the chunk,
            # so a quote the model pulled from an overlap still verifies.
            if not quote_in(text, row["source"]):
                dropped += 1
                continue
            if row["id"] in by_id:
                continue
            by_id[row["id"]] = row
            added += 1
        return added

    for chunk in chunks:
        absorb(_ask(
            f"SOLICITATION TEXT (part {chunks.index(chunk) + 1} of {len(chunks)}):\n"
            f'"""\n{chunk}\n"""\n\n{_SCHEMA_HINT}'))

    # SWEEP UNTIL DRY: one pass misses requirements; ask what is still missing.
    for round_no in range(1, max_rounds + 1):
        out["rounds"] = round_no
        known = "; ".join(sorted(r["label"] for r in by_id.values()))[:6000]
        added = 0
        for chunk in chunks:
            added += absorb(_ask(
                f"SOLICITATION TEXT:\n\"\"\"\n{chunk}\n\"\"\"\n\n"
                f"ALREADY EXTRACTED (do NOT repeat these): {known}\n\n"
                "List ONLY requirements present in the text above that are missing from "
                f"that list. Return an empty array if there are none.\n{_SCHEMA_HINT}",
                _SWEEP_SYSTEM))
        if added == 0:
            break
    else:
        # Bounded rather than unbounded, and SAID so — a silent cap would read as
        # "we found everything" when it means "we stopped looking".
        out["hit_round_cap"] = True

    out["requirements"] = list(by_id.values())
    out["dropped_unverified"] = dropped
    out["ai"] = True
    return out
```

Then modify `services/solicitation_extractor.py`:

```python
def read_pdf(pdf_bytes: bytes) -> dict:
    """PDF -> text WITH an account of what was readable.

    pdfplumber yields nothing for scanned/image pages and there is no OCR here,
    so a scan reads as a short, clean, complete-looking document. The caller must
    be able to tell "this solicitation asks for little" from "we could not read
    it" — the same invariant as kb_scraper's looks_unreadable()."""
    if not pdf_bytes:
        return {"text": "", "pages": 0, "pages_without_text": 0, "chars": 0,
                "engine": "pdfplumber"}
    try:
        pdfp = _get_pdfplumber()
    except ImportError:
        print("   [SOLICITATION] pdfplumber not installed")
        return {"text": "", "pages": 0, "pages_without_text": 0, "chars": 0,
                "engine": "unavailable"}
    pages_text, blank = [], 0
    try:
        with pdfp.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    pages_text.append(t)
                else:
                    blank += 1
    except Exception as e:
        print(f"   [SOLICITATION] PDF parse failed: {e}")
        return {"text": "", "pages": 0, "pages_without_text": 0, "chars": 0,
                "engine": "pdfplumber", "error": str(e)}
    joined = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
                    "", "\n\n".join(pages_text))
    return {"text": joined, "pages": len(pages_text) + blank,
            "pages_without_text": blank, "chars": len(joined),
            "engine": "pdfplumber"}


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Text only. Kept so existing callers are untouched; prefer read_pdf()."""
    return read_pdf(pdf_bytes)["text"]
```

and in `extract_from_text`, replace the silent truncation:

```python
    snippet = text[:_MAX_PROMPT_CHARS]
    ...
    out["unverified_fields"] = _verify_source_quotes(out, snippet)
    # Was silent. A dropped tail is exactly where deadlines and caps live.
    out["input_chars"] = len(text)
    out["truncated"] = len(text) > _MAX_PROMPT_CHARS
    return out
```

- [ ] **Step 4: Add the extractor test, then run both**

```python
# append to backend/tests/test_solicitation_extractor.py
def test_truncation_is_reported_rather_than_silent(monkeypatch):
    from services import solicitation_extractor as se
    monkeypatch.setattr(se, "_call_gemini", lambda *a, **k: '{"sponsor": "NSF"}')
    out = se.extract_from_text("x" * (se._MAX_PROMPT_CHARS + 10))
    assert out["truncated"] is True
    assert out["input_chars"] == se._MAX_PROMPT_CHARS + 10


def test_read_pdf_reports_pages_that_yielded_no_text():
    from services import solicitation_extractor as se
    out = se.read_pdf(b"")
    assert out["pages"] == 0 and out["chars"] == 0
```

Run: `cd backend && python3 -m pytest tests/test_solicitation_requirements.py tests/test_solicitation_extractor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/solicitation_requirements.py backend/services/solicitation_extractor.py backend/tests/test_solicitation_requirements.py backend/tests/test_solicitation_extractor.py
git commit -m "feat(solicitation): extract requirements from the whole document, quote-verified"
```

---

### Task 5: Store the profile on the submission

**Files:**
- Modify: `backend/models.py:200` (after `sections_json`)
- Modify: `backend/main.py:232` (migration, after the `sections_json` block), `backend/main.py:3620` (serializer)
- Modify: `backend/services/proposals_service.py`
- Test: `backend/tests/test_proposals_solicitation_profile.py`

**Interfaces:**
- Consumes: `solicitation_profile.build_generic` (Task 3), `solicitation_requirements.extract_requirements` (Task 4).
- Produces:
  - `Submission.solicitation_json` (TEXT, nullable)
  - `proposals_service.save_solicitation_profile(db, sub, payload: dict) -> None`
  - `proposals_service.load_solicitation_profile(sub) -> dict | None` — re-attaches the `checks` callables, which are never persisted
  - Serializer key `"has_solicitation_requirements": bool`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_proposals_solicitation_profile.py
import json

from services import proposals_service as ps


class _Sub:
    def __init__(self, payload=None):
        self.solicitation_json = json.dumps(payload) if payload else None


def test_a_saved_profile_round_trips_with_its_checks_reattached():
    payload = {
        "id": "PAR-24-118", "title": "NIH R01",
        "contract": {"budget_cap": 500000, "page_limits": {}, "required_attachments": []},
        "requirements": [{"id": "a", "label": "Specific aims", "section": "research_strategy",
                          "kind": "semantic", "scored": True, "source": "State the aims.",
                          "why": "", "keywords": []}],
    }
    profile = ps.load_solicitation_profile(_Sub(payload))
    assert profile["id"] == "PAR-24-118"
    # `checks` holds callables and is NOT serializable — it must come back from
    # code on load, or every deterministic row silently no-ops.
    assert callable(profile["checks"]["budget_cap"])
    assert any(r["check"] == "budget_cap" for r in profile["requirements"])


def test_no_stored_solicitation_returns_none_not_an_empty_profile():
    # An empty profile would review a draft against zero requirements and score
    # it — reviewing against nothing must be impossible.
    assert ps.load_solicitation_profile(_Sub()) is None


def test_malformed_json_is_treated_as_absent_never_raised():
    sub = _Sub()
    sub.solicitation_json = "{not json"
    assert ps.load_solicitation_profile(sub) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python3 -m pytest tests/test_proposals_solicitation_profile.py -v`
Expected: FAIL — `AttributeError: module 'services.proposals_service' has no attribute 'load_solicitation_profile'`

- [ ] **Step 3: Write the implementation**

In `backend/models.py`, immediately after the `sections_json` column:

```python
    # Solicitation-driven Draft Review: JSON string of the extracted profile for
    # THIS proposal's solicitation — {contract, requirements, sections,
    # read_report, extracted_at, model}. The `checks` callables are re-attached
    # from code on load and never stored. Nullable: a proposal has none until a
    # solicitation is attached.
    solicitation_json = Column(Text, nullable=True)
```

In `backend/main.py:init_db()`, directly after the `sections_json` block:

```python
        # 5e. Add submissions.solicitation_json if missing (Draft Review profile).
        try:
            conn.execute(text("SELECT solicitation_json FROM submissions LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'solicitation_json' column missing. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE submissions ADD COLUMN solicitation_json MEDIUMTEXT NULL"))
                conn.commit()
                print("[OK] Successfully added 'solicitation_json' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add solicitation_json column: {e}")
```

In `_submission_to_dict`, beside `has_sections`:

```python
        # Draft Review: whether this proposal has a solicitation to be reviewed
        # against. Drives the modal's attach step and the tool's badge.
        "has_solicitation_requirements": bool(getattr(s, "solicitation_json", None)),
```

In `backend/services/proposals_service.py`:

```python
def save_solicitation_profile(db: Session, sub: Submission, payload: dict) -> None:
    """Persist the reviewed profile. `checks` is dropped — callables do not
    serialize, and they are re-attached from code on load."""
    stored = {k: v for k, v in (payload or {}).items() if k != "checks"}
    sub.solicitation_json = _json.dumps(stored)
    db.commit()


def load_solicitation_profile(sub: Submission) -> Optional[dict]:
    """The stored profile, ready for draft_review.review_draft.

    Returns None — never an empty profile — when nothing is stored: reviewing a
    draft against zero requirements would produce a confident score based on
    nothing."""
    raw = getattr(sub, "solicitation_json", None)
    if not raw:
        return None
    try:
        stored = _json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(stored, dict) or not stored.get("requirements"):
        return None
    from services import solicitation_profile as sp
    return sp.build_generic(
        stored.get("contract") or {},
        [r for r in stored["requirements"] if r.get("kind") != "deterministic"],
        id=stored.get("id") or "this solicitation",
        title=stored.get("title") or "",
        url=stored.get("url"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_proposals_solicitation_profile.py tests/test_proposals_api_e2e.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/main.py backend/services/proposals_service.py backend/tests/test_proposals_solicitation_profile.py
git commit -m "feat(proposals): store each proposal's solicitation profile"
```

---

### Task 6: Endpoints

**Files:**
- Modify: `backend/main.py:4304-4420` (the two `eir-review` handlers)
- Test: `backend/tests/test_draft_review_api_e2e.py`

**Interfaces:**
- Consumes: everything from Tasks 3–5.
- Produces:
  - `POST /api/me/submissions/{id}/solicitation-requirements` — multipart `file`, or JSON `{"url": "..."}`. Extracts, returns `{profile, read_report, warnings}` **without saving** (golden rule 4).
  - `PUT /api/me/submissions/{id}/solicitation-requirements` — JSON `{profile}`; saves the confirmed set.
  - `POST /api/me/submissions/{id}/draft-review` — JSON `{draft_text}`.
  - `POST /api/me/submissions/{id}/draft-review/upload` — multipart `files`.
  - `/eir-review` and `/eir-review/upload` are **deleted**, along with `tests/test_eir_review_api_e2e.py`. Nothing outside this repo calls them: the only caller is `EirReviewModal.jsx`, which Task 7 replaces. Leaving an alias would leave a second path into the reviewer that no test covers.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_draft_review_api_e2e.py
"""Route + auth + the one behavior that must never regress: a proposal with no
solicitation is told to attach one, never given a review of nothing."""


def test_draft_review_without_a_solicitation_asks_for_one(client, auth_headers, submission):
    r = client.post(f"/api/me/submissions/{submission.id}/draft-review",
                    json={"draft_text": "Project Summary\nWe will study polymers."},
                    headers=auth_headers)
    assert r.status_code == 409
    body = r.json()
    assert "solicitation" in body["detail"].lower()


def test_draft_review_uses_the_stored_profile(client, auth_headers, submission_with_solicitation):
    r = client.post(f"/api/me/submissions/{submission_with_solicitation.id}/draft-review",
                    json={"draft_text": "Research Strategy\nOur specific aims are three."},
                    headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["solicitation"]["id"] == "PAR-24-118"
    assert {f["id"] for f in body["result"]["findings"]} <= {"aims"}


def test_extract_does_not_save_until_confirmed(client, auth_headers, submission, monkeypatch):
    from services import solicitation_requirements as sr
    monkeypatch.setattr(sr, "extract_requirements", lambda *a, **k: {
        "requirements": [{"id": "aims", "label": "Specific aims", "section": "research_strategy",
                          "kind": "semantic", "scored": True, "source": "State the aims.",
                          "why": "", "keywords": []}],
        "ai": True, "rounds": 1, "dropped_unverified": 0, "hit_round_cap": False,
        "chunks": 1, "chars": 100})
    r = client.post(f"/api/me/submissions/{submission.id}/solicitation-requirements",
                    json={"url": "https://example.org/foa"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["profile"]["requirements"]
    # Nothing persisted yet — golden rule 4.
    listing = client.get(f"/api/me/submissions/{submission.id}", headers=auth_headers)
    assert listing.json()["has_solicitation_requirements"] is False


def test_the_eir_routes_are_gone(client, auth_headers, submission):
    # One path into the reviewer. A surviving alias would be an untested second.
    r = client.post(f"/api/me/submissions/{submission.id}/eir-review",
                    json={"draft_text": "Project Summary\nWe will study polymers."},
                    headers=auth_headers)
    assert r.status_code == 404
```

Reuse the fixture style in `tests/test_eir_review_api_e2e.py` (FastAPI `TestClient` with `dependency_overrides` for `get_db` / `get_current_user`). Add a `submission_with_solicitation` fixture whose `solicitation_json` holds the `PAR-24-118` payload from Task 5's test.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 python3 -m pytest tests/test_draft_review_api_e2e.py -v`
Expected: FAIL — 404 on `/draft-review`

- [ ] **Step 3: Write the implementation**

Add to `backend/main.py` beside the existing EiR handlers. The review handler:

```python
@app.post("/api/me/submissions/{submission_id}/draft-review")
async def draft_review_endpoint(
    submission_id: int,
    payload: EirReviewRequest,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Completeness review of a pasted draft against THIS proposal's solicitation.

    Stateless — the paste is the PI's unpublished manuscript and is never stored.
    409 (not an empty review) when no solicitation is attached: a score computed
    against zero requirements would be a confident number meaning nothing."""
    if not user:
        raise HTTPException(401, "Unauthorized")
    sub = _proposals_service.get_submission(db, submission_id=submission_id,
                                            user_id=user["user_id"])
    if sub is None:
        raise HTTPException(404, "Submission not found")

    from services import draft_review as _dr
    from services.budget_helper import compute_budget

    profile = _proposals_service.load_solicitation_profile(sub)
    if profile is None:
        raise HTTPException(409, "Attach this proposal's solicitation first — the "
                                 "review is run against its requirements.")

    budget = None
    raw_b = getattr(sub, "budget_json", None)
    if raw_b:
        try:
            budget = compute_budget(json.loads(raw_b))
        except (ValueError, TypeError):
            budget = None

    result = _dr.review_draft(payload.draft_text, profile=profile,
                              title=sub.title, budget=budget)
    return {"submission_id": submission_id, "sponsor": sub.sponsor, "result": result}
```

The extract handler accepts either a PDF upload or a URL, reusing the existing
plumbing (`solicitation_extractor.read_pdf`, `url_fetcher` — SSRF-guarded, with
the Firecrawl chain — and `extract_from_text` for the contract):

```python
@app.post("/api/me/submissions/{submission_id}/solicitation-requirements")
async def extract_solicitation_requirements(
    submission_id: int,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read the solicitation and return its requirements FOR REVIEW.

    Saves nothing (golden rule 4). `read_report` rides along so a scanned PDF is
    visibly a failed read rather than a short requirement list."""
    ...
    # 1. text = read_pdf(bytes) | url_fetcher.fetch(url)
    # 2. contract = solicitation_extractor.extract_from_text(text)
    # 3. rows = solicitation_requirements.extract_requirements(text)
    # 4. profile = solicitation_profile.build_generic(contract, rows["requirements"],
    #       id=contract.get("program_id") or "this solicitation",
    #       title=contract.get("program_name") or sub.title, url=url)
    # 5. return {"profile": {k: v for k, v in profile.items() if k != "checks"},
    #            "contract": contract, "read_report": {...}, "warnings": [...]}
```

`warnings` is a list of plain sentences built in code, one per condition, and
the UI shows them verbatim: `read_report["pages_without_text"] > 0` →
*"N of M pages had no extractable text — this looks like a scan, so requirements
on those pages were not read."*; `contract.get("truncated")` → *"The solicitation
is longer than the extractor reads in one pass."*; `rows["hit_round_cap"]` →
*"Extraction stopped at the round limit and may be incomplete."*;
`rows["dropped_unverified"] > 0` → *"N proposed requirements were dropped because
they could not be quoted from the document."*; `rows["ai"] is False` → *"The AI
extractor is unavailable — no requirements could be read."*

The `PUT` validates that every incoming row carries a non-empty `source` and a
`label`, then calls `save_solicitation_profile`. Finally **delete** the two
`/eir-review` handlers, the `_EIR_MAX_*` upload ceilings' EiR naming (rename to
`_DRAFT_MAX_*`), and `tests/test_eir_review_api_e2e.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_draft_review_api_e2e.py -v
```
Expected: PASS, including `test_the_eir_routes_are_gone`

- [ ] **Step 5: Run the full suite and commit**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
git add backend/main.py backend/tests/test_draft_review_api_e2e.py
git commit -m "feat(api): draft-review and solicitation-requirements endpoints"
```

---

### Task 7: The modal — attach, review the requirements, then review the draft

**Files:**
- Create: `frontend/src/components/DraftReviewModal.jsx` (git mv from `EirReviewModal.jsx`), `frontend/src/components/DraftReviewModal.css` (git mv)
- Modify: `frontend/src/components/MyProposals.jsx:64` (delete `isEirProposal`), `:567`, and the tool button block

**Interfaces:**
- Consumes: the four endpoints from Task 6; `submission.has_solicitation_requirements` from Task 5.
- Produces: `<DraftReviewModal submission={...} onClose={...} onSolicitationSaved={...} />`

- [ ] **Step 1: Rename and re-point**

```bash
cd frontend/src/components && git mv EirReviewModal.jsx DraftReviewModal.jsx && git mv EirReviewModal.css DraftReviewModal.css
```

Rename the component and the CSS import. Change the two fetch URLs from
`/eir-review` to `/draft-review`. **Do not touch `STATUS_META`** — the status
vocabulary and its `neutral: true` flags are unchanged, and the three neutral
states must keep rendering as "not assessed".

- [ ] **Step 2: Add the attach step**

When `submission.has_solicitation_requirements` is false, the modal opens on an
attach panel instead of the draft box: a file input and a URL field posting to
`POST .../solicitation-requirements`, then a review list showing every extracted
requirement with its `source` quote and a **Save these requirements** button
posting the `PUT`. `warnings` render above the list in the existing warning
style, and `read_report` renders as one line (`"34 pages read, 0 with no text"`).
Nothing is saved until the button is pressed.

- [ ] **Step 3: Show what the review is judged against**

The results header reads `Reviewed against {result.solicitation.id}` with the
title beneath, and a collapsible listing every requirement row and its
solicitation quote. The score caption already comes from the backend's
`score.basis` — leave it exactly as-is.

- [ ] **Step 4: Ungate the tool in `MyProposals.jsx`**

Delete `EIR_RES` and `isEirProposal` (lines 63-68) and the `const isEir = ...`
at line 567. The tool button moves into the Review & submit stage for every
proposal, labeled **Draft Review**, with `status={submission.has_solicitation_requirements ? null : "needs solicitation"}`.
Leave `nextStep()` alone — it already routes to `critique`, and adding a second
recommended action would undercut the single-next-step design.

- [ ] **Step 5: Build and drive it**

```bash
cd frontend && npm run build
```
Expected: build succeeds. Then, with the backend running on 5002 and the dev
server on 3001, log in as `milam5@morgan.edu`, open a proposal, and confirm:
Draft Review appears; with no solicitation it opens on the attach panel; after
saving requirements a pasted draft returns findings whose header names that
solicitation. **It is a PWA — verify in a fresh/incognito window.**

- [ ] **Step 6: Commit**

```bash
git add -A frontend/src/components
git commit -m "feat(ui): Draft Review on every proposal, against its own solicitation"
```

---

### Task 8: The recall fixture — is the generic extractor good enough?

**Files:**
- Create: `backend/tests/test_solicitation_requirements_recall.py`

**Interfaces:**
- Consumes: `solicitation_requirements.extract_requirements`, `tests.fixtures.nsf_23_598.EIR_REQUIREMENTS`.
- Produces: an opt-in integration test, skipped unless `EIR_SOLICITATION_PDF` points at a real NSF 23-598 PDF and ADC is present.

- [ ] **Step 1: Write the test**

```python
# backend/tests/test_solicitation_requirements_recall.py
"""Does the GENERIC extractor recover the hand-curated NSF 23-598 rows?

tests/fixtures/nsf_23_598.py is the only human-verified requirement list in this
repo — no service imports it — so it is the yardstick for the extractor that now
does this job for every solicitation. OPT-IN: needs a real PDF and live Gemini,
so it never runs in the normal suite.

    EIR_SOLICITATION_PDF=/path/nsf23-598.pdf python3 -m pytest \\
      tests/test_solicitation_requirements_recall.py -v -s
"""
import os

import pytest

PDF = os.getenv("EIR_SOLICITATION_PDF")
pytestmark = pytest.mark.skipif(not PDF, reason="set EIR_SOLICITATION_PDF to run")


@pytest.fixture(autouse=True)
def _live_gemini(monkeypatch):
    # conftest pins get_client to None for every test; this one needs it live.
    monkeypatch.undo()


def test_generic_extraction_recovers_the_curated_semantic_requirements():
    from tests.fixtures import nsf_23_598 as fx
    from services import solicitation_extractor as se
    from services import solicitation_requirements as sr

    with open(PDF, "rb") as fh:
        read = se.read_pdf(fh.read())
    assert read["chars"] > 20_000, f"PDF read looks truncated: {read}"

    out = sr.extract_requirements(read["text"])
    found = " ".join(r["label"].lower() + " " + r["source"].lower()
                     for r in out["requirements"])

    curated = [r for r in fx.EIR_REQUIREMENTS
               if r["kind"] == "semantic" and r.get("scored")]
    missed = [r["label"] for r in curated
              if not any(kw in found for kw in (r.get("keywords") or [r["label"].lower()]))]

    print(f"\nextracted {len(out['requirements'])} rows in {out['rounds']} rounds; "
          f"dropped {out['dropped_unverified']} unquotable; missed {len(missed)}/"
          f"{len(curated)}: {missed}")
    # A floor, not a target. Below this the derived path is not fit to judge a
    # PI's draft and the extraction prompt needs work.
    assert len(missed) <= len(curated) * 0.2, f"missed too many: {missed}"
```

- [ ] **Step 2: Run it once against the real PDF**

Download NSF 23-598 from `tests/fixtures/nsf_23_598.py`'s `SOLICITATION_URL`, then:
```bash
cd backend && EIR_SOLICITATION_PDF=/tmp/nsf23-598.pdf python3 -m pytest \
  tests/test_solicitation_requirements_recall.py -v -s
```
Expected: PASS, and **record the printed numbers in the commit message** — they are the only evidence the generic path works.

- [ ] **Step 3: Confirm it skips cleanly in the normal suite, then commit**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
git add backend/tests/test_solicitation_requirements_recall.py
git commit -m "test(solicitation): measure generic extraction against the curated NSF 23-598 rows"
```

---

### Task 9: Document it

**Files:**
- Modify: `CLAUDE.md` (the "Draft Critic vs Draft Review" and "EiR Review" bullets)

- [ ] **Step 1: Rewrite the two bullets**

**Delete** the EiR Review bullet — the feature it describes no longer exists —
and replace it with a Draft Review one recording: the profile indirection; that
requirements are extracted per solicitation, quote-verified, and PI-confirmed
before saving; that `solicitation_json` holds them and `checks` is never
persisted; that the chunk + sweep-until-dry exists because one pass measurably
drops rows (3 vs 5 attachments on identical input); and that
`extract_from_text`'s 250k truncation is now reported rather than silent. Keep
every existing note about `could_not_locate`, the score's meaning, and the
region-locked model — unchanged and still load-bearing.

Three deletions matter as much as the additions, because each is a trap for the
next reader: **(1)** NSF 23-598 is no longer special-cased anywhere — no
`isEirProposal`, no `/eir-review`, no `eir_*` module under `services/`; **(2)**
`tests/fixtures/nsf_23_598.py` is **test data that looks like dead code** and
must not be deleted — it is the only human-verified requirement list in the repo
and the sole yardstick for the extractor; **(3)** the eight NSF-specific
deterministic checks are **gone**, so the 30% equipment cap, the cost-sharing
prohibition and the mandated collaboration sentence are now only as good as what
the extractor pulls out of the PDF. Someone will eventually notice EiR reviews
got weaker; the file should already say why.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): Draft Review is solicitation-driven, not EiR-only"
```

---

## Self-Review

**Spec coverage.** §1 requirements-as-data → Task 5. §2 the extractor, chunking,
sweep, quote-verify, read report → Task 4. §3 engine parameterization → Tasks 2
and 3. §4 fixture → Tasks 2 and 8. §5 UI → Task 7. §6 endpoints → Task 6. Docs →
Task 9.

**Deviation from the spec, on the user's instruction (2026-08-11):** the spec
kept NSF 23-598 as a curated runtime profile alongside the derived path. It does
not — *"I don't need the NSF EiR to be hardcoded."* There is one path. The
curated rows survive only as `tests/fixtures/nsf_23_598.py`, and the eight
NSF-specific deterministic checks plus their ~18 tests are deleted outright
(Task 2, Step 1).

**Ordering.** Every task consumes only earlier ones; there are no forward
dependencies left.

**Type consistency.** Check signature is `fn(ctx, req) -> (status, detail, evidence)`
in Tasks 2 and 3. Profile keys `{id,title,url,sections,requirements,checks,merit_criteria,eligibility_notes}`
are identical in Tasks 1, 3 and 5. `extract_requirements` returns the same key
set in Tasks 4 and 6. `has_solicitation_requirements` is spelled the same in
Tasks 5, 6 and 7.

**Not covered, deliberately** (from the spec's out-of-scope): OCR for scanned
solicitations, backfilling the proposals already in the DB, and hand-editing
individual requirement rows after saving.
