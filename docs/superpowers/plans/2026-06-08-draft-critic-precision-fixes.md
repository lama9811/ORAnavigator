# Draft Critic Precision Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three precision weak spots a real-proposal benchmark exposed in the Draft Critic — over-flagging page limits on full packages, false "missing" on near-identical section names, and a fragile budget heuristic — without weakening its anti-false-positive guarantees or regressing the existing suite.

**Architecture:** All changes are surgical edits to the deterministic core in `backend/services/draft_critic.py`. Each fix reuses machinery already in the file (`_estimate_section_pages`, `_norm`/`_header_match`, `_largest_dollar_amount`, `_MAGNITUDE`). Changes are additive and backward-compatible (new params default to current behavior), so existing callers and tests keep working; the AI layer is untouched.

**Tech Stack:** Python 3.13, pytest, pdfplumber (already vendored). No new dependencies.

**Why:** A benchmark on 4 real funded NIH applications (2026-06-08) found the critic's *factual integrity* ~100% but *practical accuracy* ~70%, with three repeatable failure modes:
1. Page-limit check compares the **whole document** to a **section** limit → every full package "fails."
2. Section detection is **exact-string** → false "missing" on "Bibliography and References Cited" vs "References Cited", "Budget Justification**s**" (plural), etc.
3. Budget check uses the **single largest `$`** in the doc → false *fail* on a stray "$295,000,000", false *pass* when NIH budget forms expose only "$0".

**Pre-work (once, before Task 1):**
```bash
cd "/Users/mingmalama/Desktop/WORKING PROJECTS/ora-navigator"
git checkout -b fix/draft-critic-precision
```
**Test command used throughout** (the CLAUDE.md SQLite gotcha avoids a live-DB hang only if a test imports `main`; the draft_critic suites don't, but the prefix is harmless):
```bash
cd backend && DATABASE_URL="sqlite:///:memory:" TRUSTED_HOSTS="testserver,localhost,127.0.0.1" \
  ../.venv/bin/python -m pytest tests/test_draft_critic.py tests/test_draft_critic_ai.py tests/test_draft_critic_hardening.py -v
```

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/services/draft_critic.py` | Deterministic checks (the only product file touched) | Modify `_NAME_ALIASES`, `_header_match`, `check_budget_cap` (+ new `_budget_total_amount`), `check_page_count` (+ its call site in `critique_pdf`) |
| `backend/tests/test_draft_critic_precision.py` | New unit tests for the three fixes | Create |
| `backend/tests/test_draft_critic*.py` (existing) | Regression guard | Re-run; reconcile only tests that pinned old behavior |

Each fix is independent except Task 3, which adds an optional arg threaded from `critique_pdf`.

---

### Task 1: Section matching — accept curated aliases + plurals (fixes false "missing")

**Files:**
- Modify: `backend/services/draft_critic.py` — `_NAME_ALIASES` (~line 181), `_header_match` (~line 192)
- Test: `backend/tests/test_draft_critic_precision.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_draft_critic_precision.py
from services.draft_critic import _section_present


def test_plural_header_matches_singular_required():
    text = "2. Budget Justifications\nPersonnel costs are described here."
    assert _section_present(text, "Budget Justification")


def test_bibliography_alias_matches_references_cited():
    text = "Bibliography and References Cited\n[1] Smith J, 2024."
    assert _section_present(text, "References Cited")


def test_data_mgmt_and_sharing_alias_matches_dmp():
    text = "Data Management and Sharing Plan\nData will be archived publicly."
    assert _section_present(text, "Data Management Plan")


def test_strict_matching_still_rejects_prefix_regression():
    # The anti-false-positive guarantee must survive: "Budget" is NOT
    # "Budget Justification".
    text = "Budget\n$100,000 total"
    assert not _section_present(text, "Budget Justification")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_draft_critic_precision.py -v`
Expected: the first three FAIL (false "missing"), the regression test PASSES.

- [ ] **Step 3: Implement — extend aliases and allow a trailing plural**

Replace `_NAME_ALIASES` (~line 181):
```python
_NAME_ALIASES = (
    ("biosketch", "biographical sketch"),
    ("bibliography and references cited", "references cited"),
    ("data management and sharing plan", "data management plan"),
)
```

In `_header_match` (~line 192), change the exact-equality line so a trailing
plural "s" on the candidate also matches (keeps the strict prefix guard intact):
```python
def _header_match(line: str, target_norm: str) -> bool:
    cand = _norm(_LEADING_RE.sub("", line.strip()))
    if cand == target_norm or cand == target_norm + "s":   # allow simple plural
        return True
    if target_norm and cand.startswith(target_norm):
        rest = cand[len(target_norm):].lstrip()
        return rest == "" or not rest[0].isalnum()
    return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_draft_critic_precision.py -v`
Expected: all four PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/draft_critic.py backend/tests/test_draft_critic_precision.py
git commit -m "fix(draft-critic): match section aliases + plurals to cut false 'missing'"
```

---

### Task 2: Budget check — anchor on labeled totals, sanity-bound the fallback (fixes false pass/fail)

**Files:**
- Modify: `backend/services/draft_critic.py` — add `_budget_total_amount` after `_largest_dollar_amount` (~line 421); rewrite `check_budget_cap` (~line 424)
- Test: `backend/tests/test_draft_critic_precision.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_draft_critic_precision.py
from services.draft_critic import check_budget_cap


def test_budget_prefers_labeled_total_over_stray_large_number():
    text = "Our target market is worth $295,000,000.\nTotal Direct Costs: $275,000\n"
    r = check_budget_cap(text, 300_000)
    assert r["status"] == "ok"          # uses the $275k labeled total, not the $295M stray


def test_budget_warns_instead_of_false_fail_on_stray_huge_number():
    text = "The global antibiotics market is $295,000,000 annually.\n"  # no budget total
    r = check_budget_cap(text, 300_000)
    assert r["status"] == "warn"        # don't hard-fail on a number that isn't the budget


def test_budget_warns_instead_of_false_pass_on_only_zero():
    text = "Budget Period Anticipated Amount ($) $0\n"
    r = check_budget_cap(text, 500_000)
    assert r["status"] == "warn"        # $0 is not a real 'under cap' pass


def test_budget_still_fails_on_genuine_overage():
    text = "Total Costs: $650,000 requested.\n"
    r = check_budget_cap(text, 500_000)
    assert r["status"] == "fail"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_draft_critic_precision.py -k budget -v`
Expected: the first three FAIL (stray-number / $0 mishandling), the overage test may already pass.

- [ ] **Step 3: Implement — labeled-total extractor + safer comparison**

Add after `_largest_dollar_amount` (~line 421):
```python
_BUDGET_TOTAL_RE = re.compile(
    r"(?:total\s+(?:direct\s+)?(?:costs?|project\s+costs?|budget)"
    r"|total\s+amount(?:\s+requested)?"
    r"|amount\s+requested"
    r"|budget\s+total)"
    r"[^\$\d]{0,40}\$?\s*([\d,]+(?:\.\d+)?)\s*"
    r"(million|billion|thousand|mm|m|b|k)?",
    re.IGNORECASE,
)


def _budget_total_amount(text: str) -> Optional[int]:
    """Largest amount attached to a 'Total ... costs/budget/amount requested'
    label -- a far better proxy for the requested budget than the single
    largest dollar figure in the document (which is often a stray market /
    population / id number)."""
    if not text:
        return None
    best = None
    for m in _BUDGET_TOTAL_RE.finditer(text):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        val *= _MAGNITUDE.get((m.group(2) or "").upper(), 1)
        if not math.isfinite(val) or val <= 0 or val > 1e12:
            continue
        if best is None or val > best:
            best = val
    return int(best) if best is not None else None
```

Rewrite the body of `check_budget_cap` (keep the two early `skipped` guards
at the top unchanged) so the figure-selection and edge cases are:
```python
    labeled = _budget_total_amount(text)
    largest = _largest_dollar_amount(text)
    figure = labeled if labeled is not None else largest

    if figure is None:
        return {
            "name": "Budget vs cap", "status": "warn",
            "value": f"cap ${budget_cap:,}",
            "detail": ("No dollar amounts found in the draft. The budget "
                       "section may be missing, or the PDF is image-only."),
        }
    # Fallback only: a figure wildly above the cap is almost certainly NOT the
    # budget (market size, genome length, an id). Warn, don't false-fail.
    if labeled is None and figure > budget_cap * 50:
        return {
            "name": "Budget vs cap", "status": "warn",
            "value": f"${figure:,}? / ${budget_cap:,}",
            "detail": (f"Largest $ figure is ${figure:,} -- far above the "
                       f"${budget_cap:,} cap, so it's probably a stray number, "
                       f"not the budget total. Confirm the budget section."),
        }
    if figure == 0:
        return {
            "name": "Budget vs cap", "status": "warn",
            "value": f"cap ${budget_cap:,}",
            "detail": ("Couldn't read a budget total (found only $0). The "
                       "budget may be in a form field the PDF text doesn't "
                       "expose -- verify manually."),
        }
    status = "ok" if figure <= budget_cap else "fail"
    if status == "fail":
        over = figure - budget_cap
        detail = (f"Budget figure in the draft is ${figure:,} -- "
                  f"${over:,} over the ${budget_cap:,} per-award cap. "
                  f"Trim before submitting.")
    elif figure == budget_cap:
        detail = (f"Budget figure (${figure:,}) is exactly at the cap. "
                  f"Double-check the budget justification.")
    else:
        headroom = budget_cap - figure
        detail = (f"Budget figure in the draft is ${figure:,}; "
                  f"${headroom:,} under the ${budget_cap:,} cap.")
    return {
        "name": "Budget vs cap", "status": status,
        "value": f"${figure:,} / ${budget_cap:,}", "detail": detail,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_draft_critic_precision.py -k budget -v`
Expected: all four PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/draft_critic.py backend/tests/test_draft_critic_precision.py
git commit -m "fix(draft-critic): anchor budget on labeled totals; warn on stray/zero figures"
```

---

### Task 3: Page-limit check — measure the named section, not the whole document (fixes over-flagging)

**Files:**
- Modify: `backend/services/draft_critic.py` — `check_page_count` signature + body (~line 227); its call site in `critique_pdf` (~line 910)
- Test: `backend/tests/test_draft_critic_precision.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_draft_critic_precision.py
from services.draft_critic import check_page_count


def test_page_count_scopes_to_named_section_when_pages_given():
    # Research Strategy is ~2 pages inside a 10-page packet; limit 12 -> OK.
    pages = [
        "Cover page",
        "Project Summary/Abstract\n...",
        "Research Strategy\nA. Significance ...",
        "...continued aim 1 and 2 detail...",
        "Biographical Sketch\nDr X ...",
        "Budget Justification\n...",
        "f", "g", "h",
        "References Cited\n[1] ...",
    ]
    r = check_page_count(len(pages), {"research_strategy": 12}, pages_text=pages)
    assert r["status"] == "ok"


def test_page_count_falls_back_to_total_when_section_not_found():
    r = check_page_count(20, {"project_description": 15}, pages_text=["x", "y"])
    assert r["status"] == "fail"        # header absent -> total-doc fallback


def test_page_count_backward_compatible_without_pages_text():
    r = check_page_count(10, {"project_description": 15})
    assert r["status"] == "ok"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_draft_critic_precision.py -k page_count -v`
Expected: `test_page_count_scopes_to_named_section_when_pages_given` FAILS (today it compares 10 pages... actually the doc-wide count is 10 ≤ 12 so it would pass — adjust: use a packet larger than the limit). **Before running, set the packet to 14 dummy pages** so total (14) > limit (12) but the section is ~2 pages:

```python
    pages = (["Cover", "Project Summary/Abstract\n..."]
             + ["Research Strategy\nA. Significance ..."]
             + ["...aim detail..."]
             + ["Biographical Sketch\n..."]
             + ["pad"] * 9
             + ["References Cited\n[1] ..."])      # 15 pages total, RS ~2
    r = check_page_count(len(pages), {"research_strategy": 12}, pages_text=pages)
    assert r["status"] == "ok"
```
Expected now: FAILS today (15 > 12 -> "fail"), passes after the fix (RS section ~2 pages).

- [ ] **Step 3: Implement — optional `pages_text` scoping via existing `_estimate_section_pages`**

Change the signature and add scoping right before the `status = ...` line in
`check_page_count` (~line 290). Full replacement of the function's tail after
`limit_int` is resolved and validated:
```python
def check_page_count(
    actual_pages: int,
    page_limits: Optional[dict],
    pages_text: Optional[list[str]] = None,
) -> dict:
    # ... unchanged: resolve `limit`, `label`, validate `limit_int` ...
    # (everything up to the `status = ...` computation stays the same)

    # NEW: when we have per-page text and the cap names a narrative section,
    # measure THAT section's span (reusing _estimate_section_pages) instead of
    # the whole document -- a full assembled package shouldn't fail a section cap.
    measured = actual_pages
    scope_note = ""
    if pages_text:
        est = _estimate_section_pages(pages_text, label.replace("_", " "))
        if est is not None:
            measured = est
            scope_note = f" (measured the {label.replace('_', ' ')} section span)"

    status = "ok" if measured <= limit_int else "fail"
    over_by = measured - limit_int
    if status == "ok":
        detail = (f"{label.replace('_', ' ').capitalize()} is "
                  f"{_plural(measured, 'page')}; cap is {limit_int}.{scope_note}")
    else:
        detail = (f"{label.replace('_', ' ').capitalize()} is "
                  f"{_plural(measured, 'page')} -- {over_by} over the "
                  f"{limit_int}-page cap.{scope_note} Trim before submitting.")
    return {
        "name": "Page count",
        "status": status,
        "value": f"{measured} / {limit_int}",
        "detail": detail,
    }
```

Update the call site in `critique_pdf` (~line 910):
```python
    checks.append(check_page_count(page_count, sol.get("page_limits"),
                                   pages_text=pages_text))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_draft_critic_precision.py -k page_count -v`
Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/draft_critic.py backend/tests/test_draft_critic_precision.py
git commit -m "fix(draft-critic): scope page-limit check to the narrative section"
```

---

### Task 4: Full regression run + reconcile any pinned-old-behavior tests

**Files:**
- Possibly modify: `backend/tests/test_draft_critic.py`, `test_draft_critic_hardening.py` (only tests that asserted the OLD behavior)

- [ ] **Step 1: Run the entire draft-critic suite**

Run:
```bash
cd backend && DATABASE_URL="sqlite:///:memory:" TRUSTED_HOSTS="testserver,localhost,127.0.0.1" \
  ../.venv/bin/python -m pytest tests/test_draft_critic.py tests/test_draft_critic_ai.py \
  tests/test_draft_critic_hardening.py tests/test_draft_critic_precision.py -v
```
Expected: all green. The new behavior is backward-compatible (new params default off; aliases/plurals only *add* matches; budget changes only alter stray/zero cases).

- [ ] **Step 2: If any pre-existing test fails, classify it**

For each failure, decide:
- **Pinned the OLD buggy behavior** (e.g. asserted a stray-number budget "fail", or a full-package page "fail" via a constructed multi-page text)? → update that one test to the corrected expectation and add a one-line comment `# updated 2026-06-08: section-scoped page check`. Note it in the commit body.
- **A genuine regression** (a false positive reappeared, e.g. "Budget" now matches "Budget Justification")? → STOP and fix the code, not the test.

- [ ] **Step 3: Run the full backend suite to confirm no collateral damage**

Run:
```bash
cd backend && DATABASE_URL="sqlite:///:memory:" TRUSTED_HOSTS="testserver,localhost,127.0.0.1" \
  ../.venv/bin/python -m pytest -q
```
Expected: the full suite (≈337+ new) passes.

- [ ] **Step 4: Commit any reconciled tests**

```bash
git add backend/tests/
git commit -m "test(draft-critic): reconcile tests that pinned pre-fix behavior"
```

---

## Out of scope (deliberately not fixed)

- **NIH continuation headers that carry a trailing page number** ("Research Strategy 80" repeated atop each page) are still not matched, because safely accepting a trailing number would re-open the table-of-contents false-positive the strict matcher exists to prevent. Fixing it safely needs multi-page corroboration (same "name N" header on ≥2 pages) — a separate, larger change. Impact is low: it only affects the advisory "Standard sections" *warn* row, not required-attachment *fail* rows.
- The **AI review layer** is untouched.

## End-to-end verification (after all tasks)

Re-run the 4-real-proposal benchmark from 2026-06-08 (NIAID sample applications + realistic NIH rules) and confirm: the stray-`$295M` case no longer false-fails, the `$0` case warns instead of passing, full-package page checks no longer hard-fail on the assembled packet, and "References Cited"/"Budget Justifications" are found. Expected practical accuracy moves from ~70% toward ~90%+. (Vertex quota permitting, also confirm the advisory `ai_review` still populates and never overrides the verdict.)
```
