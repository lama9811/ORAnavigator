# Per-category Sponsor Caps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a PI pick their funding category in the Budget Helper when a solicitation defines multiple category caps (e.g. NSF IDSS Category I/II/III), and have that choice fill the Sponsor cap field.

**Architecture:** Purely additive, mirroring the existing `deadline_details` pattern. The solicitation extractor gains a new `budget_cap_details` list `[{category, cap}]` alongside the unchanged single `budget_cap`. That list is persisted as a parseable `Category caps:` line in the Submission `notes` and round-tripped back out. The Budget Helper modal parses it into a "Funding category" dropdown that fills the cap. The deterministic budget math is untouched.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy (backend), pytest (backend tests), React 19 / Vite (frontend, build + lint verified — no JS test runner in this repo).

## Global Constraints

- **AI is advisory; deterministic core is authoritative.** No change to `budget_helper.py` math. The dropdown only chooses which number is sent.
- **Additive only.** The existing single `budget_cap` (smallest / "most restrictive wins") stays unchanged — Draft Critic and `reconstruct_solicitation_context` depend on it. Single-cap solicitations must behave exactly as today.
- **Graceful fallback.** When Gemini returns no per-category data (or is unavailable), `budget_cap_details` is `[]`, no `Category caps:` line is written, and no dropdown renders.
- **Structured data lives in `notes` as text** (no new DB column), consistent with `deadline_details`, page limits, and required attachments.
- **Backend test command (keep green):**
  ```bash
  cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
    python3 -m pytest -q --ignore=tests/test_agent_instruction.py
  ```
- **Frontend verify:** `cd frontend && npm run build && npm run lint` (run `npm install` first if `node_modules` is absent).
- **Notes-line format (exact, shared contract between Task 2 writer and Task 3 parser):**
  `Category caps: Category I — $30,000,000; Category II — $9,000,000; Category III — $500,000`
  Per entry: `<category> — $<comma-grouped integer>`, entries joined by `; `. The separator between category and amount is a spaced **em dash** (` — `, U+2014).

---

## Task 1: Extractor — add `budget_cap_details`

**Files:**
- Modify: `backend/services/solicitation_extractor.py` (`_CONTRACT_KEYS` ~line 75; `_EXTRACT_SYSTEM` prompt ~line 81–114; add `_coerce_cap_details`; call it in `_coerce_extracted` ~line 281–285)
- Test: `backend/tests/test_solicitation_extractor.py`

**Interfaces:**
- Produces: contract dict now has key `"budget_cap_details"` → `list[{"category": str, "cap": int}]` (empty list when absent/single). `budget_cap` (int|None) is unchanged.
- New helper `_coerce_cap_details(raw) -> list[dict]`: accepts whatever Gemini returned, returns a clean list of `{"category": str, "cap": int}`, dropping entries with no usable category or no positive integer cap.

> **Scoping note (deliberate):** `budget_cap_details` is NOT added to `_VERIFIABLE_FIELDS`. That harness cross-checks one scalar quote per field name and would not fit a list value. The canonical `budget_cap` remains quote-verified, and prompt rules 1–4 still forbid inventing values. The per-category list is additive, user-selectable, and editable context — keeping it out of the scalar harness avoids breaking verification while preserving grounding of the authoritative cap.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_solicitation_extractor.py`:

```python
# ---------- budget_cap_details (per-category caps) --------------------------

def test_coerce_cap_details_normalizes_strings_and_floats():
    """Category caps arrive as strings/floats with symbols; coerce to int."""
    raw = {
        "sponsor": "NSF", "deadline": None, "page_limits": {},
        "required_attachments": [], "eligibility": None, "budget_cap": 500000,
        "submission_portal": None, "program_id": None, "program_name": None,
        "source_quotes": {},
        "budget_cap_details": [
            {"category": "Category I", "cap": "$30,000,000"},
            {"category": "Category II", "cap": 9000000.0},
            {"category": "Category III", "cap": 500000},
        ],
    }
    out = sx._coerce_extracted(raw)
    assert out["budget_cap_details"] == [
        {"category": "Category I", "cap": 30000000},
        {"category": "Category II", "cap": 9000000},
        {"category": "Category III", "cap": 500000},
    ]


def test_coerce_cap_details_drops_unusable_entries():
    """Entries with no category or no parseable cap are dropped, not kept as junk."""
    raw = {
        "sponsor": "NSF", "deadline": None, "page_limits": {},
        "required_attachments": [], "eligibility": None, "budget_cap": None,
        "submission_portal": None, "program_id": None, "program_name": None,
        "source_quotes": {},
        "budget_cap_details": [
            {"category": "Category I", "cap": "30000000"},
            {"category": "", "cap": "9000000"},        # no category -> drop
            {"category": "Category III", "cap": "n/a"}, # no number -> drop
            {"category": "Category IV"},                # no cap key -> drop
        ],
    }
    out = sx._coerce_extracted(raw)
    assert out["budget_cap_details"] == [{"category": "Category I", "cap": 30000000}]


def test_coerce_cap_details_absent_or_single_is_empty_list():
    """Missing or non-list budget_cap_details normalizes to []."""
    raw = {
        "sponsor": "NSF", "deadline": None, "page_limits": {},
        "required_attachments": [], "eligibility": None, "budget_cap": 500000,
        "submission_portal": None, "program_id": None, "program_name": None,
        "source_quotes": {},
        # budget_cap_details intentionally absent
    }
    out = sx._coerce_extracted(raw)
    assert out["budget_cap_details"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_solicitation_extractor.py -k cap_details -v
```
Expected: FAIL — `KeyError: 'budget_cap_details'` (key not in contract / not coerced).

- [ ] **Step 3: Add the key to `_CONTRACT_KEYS`**

In `backend/services/solicitation_extractor.py`, change the tuple (~line 75):

```python
_CONTRACT_KEYS = (
    "sponsor", "program_id", "program_name", "deadline", "deadline_details",
    "page_limits", "required_attachments", "eligibility",
    "budget_cap", "budget_cap_details", "submission_portal", "source_quotes",
)
```

- [ ] **Step 4: Add the `_coerce_cap_details` helper**

Add directly below `_coerce_budget` (after ~line 204):

```python
def _coerce_cap_details(raw) -> list:
    """Normalize Gemini's per-category cap list into clean
    [{"category": str, "cap": int}] entries. Drops any entry without a
    non-empty category or without a parseable positive integer cap."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cat = item.get("category")
        cat = cat.strip() if isinstance(cat, str) else ""
        cap = _coerce_budget(item.get("cap"))
        if cat and cap and cap > 0:
            out.append({"category": cat, "cap": cap})
    return out
```

- [ ] **Step 5: Call it in `_coerce_extracted`**

In `_coerce_extracted`, right after the `out["budget_cap"] = _coerce_budget(out["budget_cap"])` line (~line 284), add:

```python
    # Per-category caps (additive; budget_cap above stays the single
    # most-restrictive value). Empty list when the solicitation has one cap.
    out["budget_cap_details"] = _coerce_cap_details(out.get("budget_cap_details"))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_solicitation_extractor.py -k cap_details -v
```
Expected: PASS (3 passed).

- [ ] **Step 7: Add the prompt rule so Gemini actually emits the field**

In `_EXTRACT_SYSTEM`, add a new line to the JSON field list, immediately after the `"budget_cap": ...` line (~line 107):

```python
  "budget_cap_details": when the solicitation defines MULTIPLE proposal categories/tracks with DIFFERENT award maxima, an array of {"category": <name as written, e.g. "Category I">, "cap": <integer dollar maximum for that category, no commas/symbols>}; for a stated RANGE (e.g. "$10 million to $30 million") use the MAXIMUM as cap; [] if there is only one category/cap,
```

This is prompt text only (no test asserts on the live model). The coercion tests above pin the contract behavior.

- [ ] **Step 8: Run the full extractor test file to confirm nothing regressed**

Run:
```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_solicitation_extractor.py -q
```
Expected: PASS (all green).

- [ ] **Step 9: Commit**

```bash
git add backend/services/solicitation_extractor.py backend/tests/test_solicitation_extractor.py
git commit -m "feat(solicitation): extract per-category budget_cap_details"
```

---

## Task 2: Storage — write & reconstruct the `Category caps:` notes line

**Files:**
- Modify: `backend/services/proposals_service.py` (notes assembly in `create_submission_from_solicitation` ~line 124–162; regex constants ~line 389–392; `reconstruct_solicitation_context` ~line 416–424)
- Test: `backend/tests/test_proposals.py`

**Interfaces:**
- Consumes: `extracted["budget_cap_details"]` → `list[{"category": str, "cap": int}]` from Task 1.
- Produces: a `Category caps: ...` line in `Submission.notes` (format per Global Constraints) when 2+ entries exist; `reconstruct_solicitation_context(sub)["budget_cap_details"]` → `list[{"category": str, "cap": int}]` (empty list when the line is absent).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_proposals.py`:

```python
def test_create_from_solicitation_surfaces_category_caps_in_notes(db):
    """A multi-category solicitation writes a parseable 'Category caps:' line;
    the single budget_cap (smallest) is still written separately."""
    extracted = {
        "sponsor": "NSF", "program_id": "NSF 26-509",
        "program_name": "Integrated Data Systems & Services",
        "deadline": "2026-07-28", "deadline_details": None,
        "page_limits": {}, "required_attachments": [],
        "eligibility": "US institutions", "budget_cap": 500000,
        "budget_cap_details": [
            {"category": "Category I", "cap": 30000000},
            {"category": "Category II", "cap": 9000000},
            {"category": "Category III", "cap": 500000},
        ],
        "submission_portal": "Research.gov", "source_quotes": {},
    }
    sub = ps.create_submission_from_solicitation(
        db, user_id=db.user_id, extracted=extracted,
    )
    assert sub.notes is not None
    assert "Budget cap: $500,000" in sub.notes          # single cap unchanged
    assert "Category caps:" in sub.notes
    assert "Category I — $30,000,000" in sub.notes
    assert "Category II — $9,000,000" in sub.notes
    assert "Category III — $500,000" in sub.notes


def test_create_from_solicitation_omits_category_caps_when_single(db):
    """A solicitation with 0/1 category caps gets no 'Category caps:' line."""
    extracted = {
        "sponsor": "NSF", "program_id": "NSF 23-1", "program_name": "X",
        "deadline": "2026-06-12", "deadline_details": None,
        "page_limits": {}, "required_attachments": [], "eligibility": None,
        "budget_cap": 500000, "budget_cap_details": [],
        "submission_portal": None, "source_quotes": {},
    }
    sub = ps.create_submission_from_solicitation(
        db, user_id=db.user_id, extracted=extracted,
    )
    assert "Category caps:" not in (sub.notes or "")


def test_reconstruct_round_trips_category_caps(db):
    """reconstruct_solicitation_context parses the caps back out of notes."""
    extracted = {
        "sponsor": "NSF", "program_id": "NSF 26-509", "program_name": "IDSS",
        "deadline": "2026-07-28", "deadline_details": None,
        "page_limits": {}, "required_attachments": [], "eligibility": None,
        "budget_cap": 500000,
        "budget_cap_details": [
            {"category": "Category I", "cap": 30000000},
            {"category": "Category III", "cap": 500000},
        ],
        "submission_portal": None, "source_quotes": {},
    }
    sub = ps.create_submission_from_solicitation(
        db, user_id=db.user_id, extracted=extracted,
    )
    ctx = ps.reconstruct_solicitation_context(sub)
    assert ctx["budget_cap_details"] == [
        {"category": "Category I", "cap": 30000000},
        {"category": "Category III", "cap": 500000},
    ]


def test_reconstruct_category_caps_empty_for_manual_submission(db):
    """A submission with no 'Category caps:' line yields an empty list."""
    sub = ps.create_submission(db, user_id=db.user_id, title="Manual", sponsor="NSF")
    ctx = ps.reconstruct_solicitation_context(sub)
    assert ctx["budget_cap_details"] == []
```

> Note: `test_reconstruct_category_caps_empty_for_manual_submission` uses `ps.create_submission`. If that helper's signature differs in this repo, mirror the construction used by the existing `test_solicitation_required_attachments_survive_template_dedup` test (which already builds a submission and calls `reconstruct_solicitation_context`). The assertion (`ctx["budget_cap_details"] == []`) is the load-bearing part.

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_proposals.py -k category_caps -v
```
Expected: FAIL — `Category caps:` not in notes, and `KeyError: 'budget_cap_details'` from `reconstruct_solicitation_context`.

- [ ] **Step 3: Write the `Category caps:` notes line**

In `create_submission_from_solicitation`, immediately after the `budget_cap` notes block (the `if extracted.get("budget_cap"):` ... line ~135–136), add:

```python
    # Multi-category solicitations (NSF/NIH Category I/II/III, tracks) carry a
    # different award max per category; `budget_cap` above is only the smallest.
    # Surface every category cap as a parseable line so the Budget Helper can
    # offer the PI a "Funding category" picker. Em-dash separated; "; " between
    # entries. Only written when there are 2+ categories.
    cap_details = [
        c for c in (extracted.get("budget_cap_details") or [])
        if isinstance(c, dict) and c.get("category") and c.get("cap")
    ]
    if len(cap_details) >= 2:
        cap_parts = [f"{c['category']} — ${int(c['cap']):,}" for c in cap_details]
        notes_lines.append(f"Category caps: {'; '.join(cap_parts)}")
```

- [ ] **Step 4: Add the reconstruct regex constant**

Next to `_BUDGET_NOTE_RE` (~line 389), add:

```python
_CATEGORY_CAPS_NOTE_RE = _re.compile(r"^Category caps:\s*(.+)", _re.MULTILINE)
```

- [ ] **Step 5: Initialize the new key and parse it in `reconstruct_solicitation_context`**

In `reconstruct_solicitation_context`, add `"budget_cap_details": []` to the initial `out` dict:

```python
    out: dict = {
        "budget_cap": None,
        "budget_cap_details": [],
        "page_limits": {},
        "required_attachments": [],
    }
```

Then, inside the `if notes:` block (right after the `_BUDGET_NOTE_RE` parse, before the page-limits parse), add:

```python
        cc = _CATEGORY_CAPS_NOTE_RE.search(notes)
        if cc:
            # "Category I — $30,000,000; Category III — $500,000"
            caps = []
            for part in cc.group(1).split(";"):
                seg = part.split("—", 1)          # split on the em dash
                if len(seg) != 2:
                    continue
                cat = seg[0].strip()
                amt = _re.sub(r"[^\d]", "", seg[1])
                if cat and amt:
                    caps.append({"category": cat, "cap": int(amt)})
            out["budget_cap_details"] = caps
```

- [ ] **Step 6: Run the tests to verify they pass**

Run:
```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_proposals.py -k category_caps -v
```
Expected: PASS (4 passed).

- [ ] **Step 7: Run the whole backend suite to confirm no regression**

Run:
```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
```
Expected: PASS (all green).

- [ ] **Step 8: Commit**

```bash
git add backend/services/proposals_service.py backend/tests/test_proposals.py
git commit -m "feat(proposals): persist & reconstruct per-category caps in notes"
```

---

## Task 3: Frontend — "Funding category" dropdown in Budget Helper

**Files:**
- Modify: `frontend/src/components/BudgetHelperModal.jsx` (add `categoryCapsFromNotes` ~after line 28; fresh-load branch ~line 61–64; F&A grid render ~line 224–225)

**Interfaces:**
- Consumes: `submission.notes` containing the `Category caps:` line written in Task 2.
- Produces: a rendered `<select>` (only when 2+ categories parse) that writes the chosen cap into `inputs.cap`.

No JS test runner exists in this repo (verify via `npm run build` + `npm run lint` + manual smoke).

- [ ] **Step 1: Add the `categoryCapsFromNotes` parser**

In `frontend/src/components/BudgetHelperModal.jsx`, directly below the existing `capFromNotes` function (~line 28), add:

```javascript
// Pull the per-category caps out of a "Category caps: Category I — $30,000,000; …"
// notes line, if present. Returns [{category, cap}] with cap as a numeric string
// (to match the <input> value type), or [] when there's no such line.
function categoryCapsFromNotes(notes) {
  if (!notes) return [];
  const line = String(notes).match(/^Category caps:\s*(.+)$/m);
  if (!line) return [];
  return line[1]
    .split(";")
    .map((part) => {
      const m = part.match(/^\s*(.+?)\s*—\s*\$?([\d,]+)/);
      if (!m) return null;
      return { category: m[1].trim(), cap: m[2].replace(/,/g, "") };
    })
    .filter(Boolean);
}
```

- [ ] **Step 2: Don't auto-prefill the cap when categories exist (force a choice)**

In the load `useEffect`, change the fresh (`else`) branch (~line 61–64) from:

```javascript
        } else {
          // fresh — prefill the cap from the solicitation if we can find one
          setInputs((p) => ({ ...p, cap: capFromNotes(submission.notes) }));
        }
```

to:

```javascript
        } else {
          // fresh — if the solicitation has multiple category caps, leave the
          // cap blank so the PI must pick a category; otherwise prefill the
          // single cap as before.
          const cats = categoryCapsFromNotes(submission.notes);
          setInputs((p) => ({
            ...p,
            cap: cats.length >= 2 ? "" : capFromNotes(submission.notes),
          }));
        }
```

- [ ] **Step 3: Compute the parsed caps once per render**

Next to the existing `const faOptions = ...` line (~line 95), add:

```javascript
  const categoryCaps = categoryCapsFromNotes(submission.notes);
```

- [ ] **Step 4: Render the "Funding category" dropdown before the Sponsor cap field**

In the F&A grid, change the line (~line 225):

```javascript
                {numField("Sponsor cap", "cap", "(optional)")}
```

to:

```javascript
                {categoryCaps.length >= 2 && (
                  <label className="bh-field"><span>Funding category</span>
                    <select
                      value={categoryCaps.find((c) => c.cap === String(inputs.cap))?.category || ""}
                      onChange={(e) => {
                        const picked = categoryCaps.find((c) => c.category === e.target.value);
                        set({ cap: picked ? picked.cap : "" });
                      }}>
                      <option value="">Select your category…</option>
                      {categoryCaps.map((c) => (
                        <option key={c.category} value={c.category}>
                          {c.category} — {fmt(c.cap)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {numField("Sponsor cap", "cap", "(optional)")}
```

- [ ] **Step 5: Build and lint**

Run:
```bash
cd frontend && npm run build && npm run lint
```
Expected: build succeeds, lint clean (no new errors in `BudgetHelperModal.jsx`).

- [ ] **Step 6: Manual smoke (per CLAUDE.md — PWA, use a fresh/incognito window)**

In a fresh window:
1. Open a proposal created from a multi-category solicitation (or temporarily set its notes to include `Category caps: Category I — $30,000,000; Category II — $9,000,000; Category III — $500,000`).
2. Open Budget Helper → confirm a **"Funding category"** dropdown appears above **"Sponsor cap"**, showing `Select your category…` and the three tiers; the Sponsor cap starts **blank**.
3. Pick **Category II** → Sponsor cap fills with `9000000`; the summary cap pill updates.
4. Open a single-cap proposal → confirm **no** dropdown and the cap still prefills as before.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/BudgetHelperModal.jsx
git commit -m "feat(budget): funding-category picker fills sponsor cap"
```

---

## Self-Review

**Spec coverage:**
- Extraction `budget_cap_details` + prompt rule + range→max + coercion → Task 1. ✓
- `budget_cap` unchanged / additive → Task 1 Step 5 + Task 2 assertion (`Budget cap: $500,000` still present). ✓
- Storage `Category caps:` notes line + reconstruct round-trip, no new column → Task 2. ✓
- Frontend dropdown, 2+ only, force-a-choice blank default, single-cap unchanged, saved-budget no-clobber → Task 3 (Step 4 `value` derives from `inputs.cap` so a saved cap that matches a tier auto-selects, and never overwrites on load). ✓
- Backend math untouched → no task touches `budget_helper.py`. ✓
- Edge cases (range, custom typed value resets dropdown to placeholder via the `find` miss, Gemini-unavailable → empty list) → covered across Tasks 1–3. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases" — every step has concrete code or an exact command. The one conditional note (Task 2 Step 1, `create_submission` signature) gives a concrete fallback. ✓

**Type consistency:**
- `budget_cap_details` is `list[{"category": str, "cap": int}]` everywhere in backend (Task 1 produces, Task 2 consumes/reconstructs). ✓
- Frontend `categoryCapsFromNotes` returns `cap` as a **string** (matches `<input>`/`inputs.cap`), and the select compares `c.cap === String(inputs.cap)` — consistent. ✓
- Notes-line format is identical between Task 2 writer (`f"{c['category']} — ${int(c['cap']):,}"`) and Task 3 parser (em-dash split) and the Global Constraints contract. ✓
