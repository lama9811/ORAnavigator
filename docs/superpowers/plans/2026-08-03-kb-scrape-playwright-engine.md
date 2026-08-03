# KB Scrape: Playwright Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the KB scrape read every ORA page with a real browser instead of an LLM, so the 26 compliance pages the Gemini engine refuses are monitored and the fingerprint gate works again.

**Architecture:** Flip `kb_scraper/run.py`'s `--engine` default from `gemini` to `playwright`, which activates the existing `kb_scraper/crawler.py` (Playwright + Chromium, already in the job image) and lets the forced-`--audit` branch stop firing on its own. Because a fingerprint hashes whatever text the reader produced, and the two readers produce different text for the same page, add an `engine` column to `KbPageFingerprint` and compare only same-engine rows — so the first Playwright run baselines silently instead of reporting all 59 pages as changed. Adjudication stays on `gemini-3.6-flash`, untouched.

**Tech Stack:** Python 3.13, SQLAlchemy (Cloud SQL MySQL in prod, sqlite in tests), Playwright + Chromium, pytest, Google Cloud Run Jobs.

## Global Constraints

- **No new dependencies.** Playwright and Chromium are already in `kb_scraper/Dockerfile`; `google-genai==1.14.0` stays pinned.
- **The adjudicator does not change.** `kb_scraper/adjudicator.py` keeps `SCRAPE_MODEL=gemini-3.6-flash` and `SCRAPE_MODEL_LOCATION=global`. The location override is required — 3.6-flash 404s in `us-central1`.
- **The job never writes to a knowledge base document.** Every material change stays a `pending` row in `scrape_changes`; `approve_change()` in `backend/kb_scrape_service.py` remains the only path that writes to the datastore.
- **An unreadable page is not a change.** Do not weaken `looks_unreadable()` or the `result.unreadable` branch.
- **Schema changes use the self-healing migration convention** (golden rule 5): in `backend/main.py:init_db()`, try `SELECT <col>`; on `OperationalError`/`ProgrammingError`, `ALTER TABLE ADD COLUMN`. No Alembic.
- **Never `git push` and never deploy** without the user explicitly asking in that message (golden rule 7). Local commits are fine.
- **Backend test command:**
  ```bash
  cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
    python3 -m pytest -q --ignore=tests/test_agent_instruction.py
  ```
  Baseline before this plan: **779 passed, 1 skipped**.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/models.py` | Modify (`KbPageFingerprint`, ~line 305-325) | Add `engine` column — records which reader produced each hash |
| `backend/main.py` | Modify (`init_db()`, after the existing column migrations, ~line 168+) | Self-healing `ALTER TABLE` for the new column |
| `kb_scraper/run.py` | Modify | Flip engine default; extract four testable helpers; scope baseline reads and fingerprint writes by engine |
| `backend/tests/test_kb_scraper.py` | Modify (append) | Unit tests for the new helpers and the engine-scoping behaviour |
| `CLAUDE.md` | Modify (the "scrape engine" bullet under Build/test/deploy) | The canonical doc currently says the Gemini engine is the default and describes the 44% blackout as current state |

`kb_scraper/crawler.py`, `kb_scraper/adjudicator.py`, `kb_scraper/fingerprint.py`, `kb_scraper/gemini_crawler.py`, `kb_scraper/Dockerfile`, `cloudbuild.kb-scraper.yaml`, `backend/kb_scrape_service.py`, all `/api/admin/kb-scrape/*` endpoints and `frontend/src/components/KbScrapePanel.jsx` are **not modified**.

---

### Task 1: Record which reader produced each fingerprint

`KbPageFingerprint.fingerprint` is a SHA-256 of the extracted page text. Gemini's markdown extraction and Playwright's `inner_text()` of the same unchanged page hash differently, so every existing row is a stale baseline for the new engine. This column is what lets the comparison ignore rows another engine wrote.

**Files:**
- Modify: `backend/models.py:305-325` (the `KbPageFingerprint` class)
- Modify: `backend/main.py` (inside `init_db()`, after the existing column migrations)
- Test: `backend/tests/test_kb_scraper.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `KbPageFingerprint.engine` — a nullable `String(20)` holding `"playwright"` or `"gemini"`. `NULL` means the row predates this migration and must be treated as belonging to no engine.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_kb_scraper.py`:

```python
# ---------------------------------------------------------------------------
# Fingerprints are engine-specific — a hash of Gemini's markdown extraction and
# a hash of Playwright's inner_text() differ for the SAME unchanged page, so a
# row must record who wrote it or the first run after a switch reports every
# page as changed.
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    """In-memory SQLite carrying the real model definitions."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from models import Base

    eng = create_engine("sqlite://")
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng)()
    try:
        yield s
    finally:
        s.close()


def test_fingerprint_rows_record_their_engine(db_session):
    from models import KbPageFingerprint

    db_session.add(KbPageFingerprint(
        url="https://www.morgan.edu/ora", fingerprint="a" * 64, engine="playwright"
    ))
    db_session.commit()

    row = db_session.query(KbPageFingerprint).one()
    assert row.engine == "playwright"


def test_engine_is_nullable_for_rows_written_before_the_migration(db_session):
    from models import KbPageFingerprint

    db_session.add(KbPageFingerprint(url="https://www.morgan.edu/ora", fingerprint="b" * 64))
    db_session.commit()

    assert db_session.query(KbPageFingerprint).one().engine is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_kb_scraper.py -q -k "engine"
```

Expected: FAIL — `TypeError: 'engine' is an invalid keyword argument for KbPageFingerprint`.

- [ ] **Step 3: Add the column to the model**

In `backend/models.py`, inside `class KbPageFingerprint`, immediately after the `fingerprint` column:

```python
    # Which reader produced this hash: "playwright" | "gemini". A fingerprint is
    # a hash of the extracted TEXT, and the two engines extract the same
    # unchanged page differently — so a hash is only a valid baseline for the
    # engine that wrote it. NULL means the row predates this column; it belongs
    # to no engine and is therefore treated as a first sighting.
    engine = Column(String(20), nullable=True)
```

- [ ] **Step 4: Add the self-healing migration**

In `backend/main.py`, inside `init_db()`'s `with engine.connect() as conn:` block, after the existing column migrations:

```python
        # N. Add engine column to kb_page_fingerprints if missing.
        # Without it, the first run after a scrape-engine switch compares
        # browser hashes against LLM hashes and reports every page as changed.
        try:
            conn.execute(text("SELECT engine FROM kb_page_fingerprints LIMIT 1"))
        except (OperationalError, ProgrammingError):
            print("[WARN] 'engine' column missing on kb_page_fingerprints. Adding it now...")
            try:
                conn.execute(text("ALTER TABLE kb_page_fingerprints ADD COLUMN engine VARCHAR(20)"))
                conn.commit()
                print("[OK] Successfully added 'engine' column!")
            except Exception as e:
                print(f"[ERROR] Failed to add engine column: {e}")
```

Renumber the comment (`# N.`) to follow the last existing numbered migration block. This migration is not unit-tested, matching the convention for every other migration in `init_db()`; the model-level tests above cover fresh databases, where `create_all` produces the column directly.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_kb_scraper.py -q -k "engine"
```

Expected: PASS (2 tests).

- [ ] **Step 6: Run the full suite**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
```

Expected: 781 passed, 1 skipped (779 baseline + 2 new).

- [ ] **Step 7: Commit**

```bash
git add backend/models.py backend/main.py backend/tests/test_kb_scraper.py
git commit -m "feat(kb-scrape): record which engine produced each page fingerprint

A fingerprint hashes the extracted page TEXT, and the Gemini and Playwright
readers extract the same unchanged page differently. Without recording the
author, the first run after an engine switch compares browser hashes against
LLM hashes and reports all 59 pages as changed."
```

---

### Task 2: Make the engine choice and the audit rule testable, and flip the default

`main()` currently builds the argument parser inline and applies the forced-audit rule inline, so neither can be tested without running a crawl. Extract both, then change the default.

The forced-audit rule must be **preserved for Gemini**: that engine's extraction is not byte-stable (1444/1466/1478 chars across three reads of one unchanged page), so its fingerprint gate cannot work and `--audit` compensates. Playwright measured identical hashes across three reads, so it must **not** force audit.

**Files:**
- Modify: `kb_scraper/run.py:116-149` (the parser block and the forced-audit block inside `main()`)
- Test: `backend/tests/test_kb_scraper.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `build_parser() -> argparse.ArgumentParser` — module-level; reads `SCRAPE_ENGINE` at call time so the env override is testable.
  - `resolve_audit(engine: str, audit: bool) -> bool` — module-level; returns `True` for `"gemini"` regardless of `audit`, otherwise returns `audit` unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_kb_scraper.py`. Note `run` is loaded with the file-path loader already defined at the top of this file:

```python
run = _load("run")


# ---------------------------------------------------------------------------
# Engine selection. Playwright is the default because the Gemini engine cannot
# read 26 of 59 ORA URLs (RECITATION blocks the entire compliance core).
# ---------------------------------------------------------------------------

def test_playwright_is_the_default_engine(monkeypatch):
    monkeypatch.delenv("SCRAPE_ENGINE", raising=False)
    args = run.build_parser().parse_args([])
    assert args.engine == "playwright"


def test_scrape_engine_env_var_overrides_the_default(monkeypatch):
    monkeypatch.setenv("SCRAPE_ENGINE", "gemini")
    args = run.build_parser().parse_args([])
    assert args.engine == "gemini"


def test_engine_flag_beats_the_env_var(monkeypatch):
    monkeypatch.setenv("SCRAPE_ENGINE", "gemini")
    args = run.build_parser().parse_args(["--engine=playwright"])
    assert args.engine == "playwright"


# --- the forced-audit rule -------------------------------------------------

def test_gemini_forces_audit_because_its_text_is_not_byte_stable():
    assert run.resolve_audit("gemini", False) is True


def test_playwright_does_not_force_audit():
    assert run.resolve_audit("playwright", False) is False


def test_explicit_audit_flag_is_honoured_on_playwright():
    assert run.resolve_audit("playwright", True) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_kb_scraper.py -q -k "engine or audit"
```

Expected: FAIL — `AttributeError: module 'run' has no attribute 'build_parser'`.

- [ ] **Step 3: Extract `build_parser()` and `resolve_audit()`**

In `kb_scraper/run.py`, add both at module level (after `_now()`, before `_load_url_index()`). Move the parser body out of `main()` verbatim, changing only the `--engine` default:

```python
def build_parser() -> argparse.ArgumentParser:
    """The CLI surface. Separate from main() so the defaults are testable.

    SCRAPE_ENGINE is read here rather than at import time so the environment
    override can be exercised in tests.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, default=int(os.getenv("SCRAPE_RUN_ID") or 0))
    ap.add_argument("--dry-run", action="store_true", help="crawl and report; write nothing at all")
    ap.add_argument(
        "--audit", action="store_true",
        help="adjudicate pages on FIRST sighting too, instead of just recording a "
             "baseline. Surfaces drift that accumulated before fingerprinting "
             "existed — the KB was written in May and no page has been compared "
             "since. Costs one model call per mapped page, so this is a one-time "
             "sweep, not a routine run. Nothing is applied either way — it only "
             "changes how many proposals you get to review.",
    )
    ap.add_argument("--limit", type=int, default=0, help="stop after N pages (smoke test)")
    ap.add_argument("--seed", action="append", default=[], help="override seed URL(s)")
    ap.add_argument(
        "--engine", choices=("gemini", "playwright"),
        default=os.getenv("SCRAPE_ENGINE", "playwright"),
        help="who reads the pages. playwright (default): headless Chromium, "
             "walks the site, expands accordions, and returns the page verbatim "
             "so quotes can be verified against it. gemini: gemini-3.6-flash via "
             "the URL Context tool, no browser, works only on URLs the KB "
             "already knows, and is refused outright on 26 of 59 ORA pages.",
    )
    return ap


def resolve_audit(engine: str, audit: bool) -> bool:
    """Should this run adjudicate first sightings as well as changed pages?

    The Gemini engine's page text is a model rendering, not a transcript, so it
    is NOT byte-stable: the same unchanged page read three times at temperature
    0 measured 1444, 1466 and 1478 chars. A fingerprint that never matches
    cannot gate anything, and leaving the gate in would mean silently claiming
    "this page moved" on every page of every run — so that engine adjudicates
    everything and lets the model be the change signal.

    Playwright measured identical hashes across three reads of the same page
    (verified 2026-08-03), so its gate works and is left alone.
    """
    if engine == "gemini":
        return True
    return audit
```

- [ ] **Step 4: Use them in `main()`**

Replace the parser block and the forced-audit block at the top of `main()` with:

```python
def main() -> int:
    args = build_parser().parse_args()

    was_audit = args.audit
    args.audit = resolve_audit(args.engine, args.audit)
    if args.audit and not was_audit:
        log.info("Gemini engine: fingerprint gate disabled (extraction is not byte-stable)")
```

Everything from `from crawler import MAX_PAGES` onward stays exactly as it is.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_kb_scraper.py -q -k "engine or audit"
```

Expected: PASS (8 tests — 2 from Task 1, 6 new).

- [ ] **Step 6: Verify the CLI still works end to end**

```bash
cd kb_scraper && python3 run.py --help | head -5
```

Expected: usage text, no traceback.

- [ ] **Step 7: Commit**

```bash
git add kb_scraper/run.py backend/tests/test_kb_scraper.py
git commit -m "feat(kb-scrape): default to the Playwright engine

Measured against the live site: Playwright reads 21/21 pages including all 18
the Gemini engine refuses with RECITATION, returns identical hashes across
three reads, and expands the accordions that hold the IACUC SOPs (50 entries).

Extracts build_parser() and resolve_audit() so the default and the
Gemini-only forced-audit rule are testable without running a crawl. The Gemini
engine stays available via --engine=gemini or SCRAPE_ENGINE=gemini, with its
forced-audit behaviour intact."
```

---

### Task 3: Compare and write fingerprints per engine

Two call sites read or write `KbPageFingerprint` during a run, and both are wrong once two engines exist:

1. The baseline load (`run.py:176-182`) reads **every** row, so Gemini hashes become Playwright's baseline. It also feeds the removed-page sweep (`run.py:393-396`), which would report the whole Gemini-era URL set as deleted from the site.
2. The baseline-branch write (`run.py:270-280`) does a bare `session.add(KbPageFingerprint(url=...))`. `url` is `unique=True`, so once a Gemini row exists for that URL this raises an **IntegrityError** instead of updating. Both write sites must upsert.

**Files:**
- Modify: `kb_scraper/run.py` — add two helpers; use them at `run.py:176-182`, `run.py:270-280` and `run.py:375-390`
- Test: `backend/tests/test_kb_scraper.py`

**Interfaces:**
- Consumes: `KbPageFingerprint.engine` from Task 1.
- Produces:
  - `load_baseline(session, engine: str) -> dict[str, str]` — `{url: fingerprint}` for rows written by `engine` only.
  - `upsert_fingerprint(session, *, url: str, digest: str, engine: str, title: str, doc_ids: list[str], char_count: int, changed: bool) -> KbPageFingerprint` — updates the row for `url` if present, else inserts. Always sets `fingerprint`, `engine`, `title`, `doc_ids`, `char_count`, `last_seen_at`; sets `last_changed_at` only when `changed` is `True`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_kb_scraper.py`:

```python
# ---------------------------------------------------------------------------
# Baseline reads and fingerprint writes are scoped to the engine that ran, so
# switching engines re-baselines silently instead of reporting every page as
# changed — and instead of reporting every old URL as removed from the site.
# ---------------------------------------------------------------------------

def _fp(session, url, digest, engine):
    from models import KbPageFingerprint
    session.add(KbPageFingerprint(url=url, fingerprint=digest, engine=engine))
    session.commit()


def test_baseline_ignores_rows_written_by_another_engine(db_session):
    _fp(db_session, "https://www.morgan.edu/ora", "g" * 64, "gemini")

    assert run.load_baseline(db_session, "playwright") == {}


def test_baseline_returns_rows_written_by_this_engine(db_session):
    _fp(db_session, "https://www.morgan.edu/ora", "p" * 64, "playwright")

    assert run.load_baseline(db_session, "playwright") == {
        "https://www.morgan.edu/ora": "p" * 64
    }


def test_baseline_ignores_pre_migration_rows_with_no_engine(db_session):
    _fp(db_session, "https://www.morgan.edu/ora", "n" * 64, None)

    assert run.load_baseline(db_session, "playwright") == {}


def test_a_gemini_era_url_is_not_reported_as_removed_from_the_site(db_session):
    """The removed-page sweep is `prior` minus the URLs seen this run. Scoping
    `prior` by engine is what stops a switch from proposing that every page was
    deleted."""
    _fp(db_session, "https://www.morgan.edu/ora", "g" * 64, "gemini")

    prior = run.load_baseline(db_session, "playwright")
    seen = set()  # nothing crawled yet
    assert [u for u in prior if u not in seen] == []


def test_upsert_updates_a_row_written_by_the_other_engine(db_session):
    """url is unique=True, so a bare INSERT would raise IntegrityError here."""
    from models import KbPageFingerprint

    _fp(db_session, "https://www.morgan.edu/ora", "g" * 64, "gemini")

    run.upsert_fingerprint(
        db_session, url="https://www.morgan.edu/ora", digest="p" * 64,
        engine="playwright", title="ORA", doc_ids=["about_ora"],
        char_count=2128, changed=False,
    )
    db_session.commit()

    rows = db_session.query(KbPageFingerprint).all()
    assert len(rows) == 1
    assert rows[0].fingerprint == "p" * 64
    assert rows[0].engine == "playwright"
    assert rows[0].char_count == 2128


def test_upsert_inserts_when_the_page_is_new(db_session):
    from models import KbPageFingerprint

    run.upsert_fingerprint(
        db_session, url="https://www.morgan.edu/ora/new", digest="p" * 64,
        engine="playwright", title="New", doc_ids=[], char_count=10, changed=False,
    )
    db_session.commit()

    assert db_session.query(KbPageFingerprint).count() == 1


def test_upsert_only_stamps_last_changed_when_the_page_changed(db_session):
    from models import KbPageFingerprint

    run.upsert_fingerprint(
        db_session, url="https://www.morgan.edu/ora", digest="p" * 64,
        engine="playwright", title="ORA", doc_ids=[], char_count=1, changed=False,
    )
    db_session.commit()
    assert db_session.query(KbPageFingerprint).one().last_changed_at is None

    run.upsert_fingerprint(
        db_session, url="https://www.morgan.edu/ora", digest="q" * 64,
        engine="playwright", title="ORA", doc_ids=[], char_count=1, changed=True,
    )
    db_session.commit()
    assert db_session.query(KbPageFingerprint).one().last_changed_at is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_kb_scraper.py -q -k "baseline or upsert or removed"
```

Expected: FAIL — `AttributeError: module 'run' has no attribute 'load_baseline'`.

- [ ] **Step 3: Add the two helpers**

In `kb_scraper/run.py`, at module level after `resolve_audit()`:

```python
def load_baseline(session, engine: str) -> dict[str, str]:
    """{url: fingerprint} for pages THIS engine has read before.

    Scoped by engine on purpose. A fingerprint is a hash of the extracted text,
    and the two engines extract the same unchanged page differently — so a hash
    written by the other engine is not a baseline, it is noise that would make
    every page look changed. Rows with engine NULL predate the column and are
    treated the same way.

    This also scopes the removed-page sweep at the end of a run, which is
    `prior` minus the URLs seen: without the filter, switching engines would
    propose that every previously-known page had been deleted from the site.
    """
    from models import KbPageFingerprint

    rows = (
        session.query(KbPageFingerprint)
        .filter(KbPageFingerprint.engine == engine)
        .all()
    )
    return {r.url: r.fingerprint for r in rows}


def upsert_fingerprint(session, *, url: str, digest: str, engine: str, title: str,
                       doc_ids: list, char_count: int, changed: bool):
    """Record what this engine saw at this URL, updating the row if one exists.

    Must be an upsert rather than an insert: `url` is unique, so a page that
    already carries another engine's row would raise IntegrityError on a bare
    add. `last_changed_at` is only stamped when the page actually moved, so it
    keeps meaning "when did this page last change" rather than "when did we
    last look".
    """
    from models import KbPageFingerprint

    fp = session.query(KbPageFingerprint).filter(KbPageFingerprint.url == url).first()
    if fp is None:
        fp = KbPageFingerprint(url=url, created_at=_now())
        session.add(fp)
    fp.fingerprint = digest
    fp.engine = engine
    fp.title = (title or "")[:500]
    fp.doc_ids = json.dumps(doc_ids)
    fp.char_count = char_count
    fp.last_seen_at = _now()
    if changed:
        fp.last_changed_at = _now()
    return fp
```

- [ ] **Step 4: Use `load_baseline()` at the baseline load**

Replace `run.py:176-182`:

```python
    prior: dict[str, str] = {}
    if not args.dry_run:
        prior = load_baseline(session, args.engine)
    log.info("Baseline: %d known page fingerprints for engine=%s", len(prior), args.engine)
```

The now-unused `from models import KbPageFingerprint` import on that line is removed; `main()` still imports `KbPageFingerprint` in its `if not args.dry_run:` block near the top for the `unchanged` branch.

- [ ] **Step 5: Use `upsert_fingerprint()` at the first-sighting write**

Replace the body of the baseline branch at `run.py:270-280`:

```python
        if known is None and doc_ids and not args.audit:
            stats["baselined"] = stats.get("baselined", 0) + 1
            if not args.dry_run:
                upsert_fingerprint(
                    session, url=url, digest=digest, engine=args.engine,
                    title=result.title, doc_ids=doc_ids,
                    char_count=len(result.text), changed=False,
                )
                session.commit()
            continue
```

- [ ] **Step 6: Use `upsert_fingerprint()` at the changed-page write**

Replace `run.py:375-388` (the block starting `# Baseline advances for everything we successfully READ`):

```python
        # Baseline advances for everything we successfully READ, including
        # cosmetic changes — otherwise the same immaterial edit is re-judged,
        # and re-paid for, on every future run.
        upsert_fingerprint(
            session, url=url, digest=digest, engine=args.engine,
            title=result.title, doc_ids=doc_ids,
            char_count=len(result.text), changed=True,
        )
```

Leave the `run.changes_found = ...` and `session.commit()` lines that follow it unchanged.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest tests/test_kb_scraper.py -q
```

Expected: PASS — 26 existing + 15 new = 41 tests.

- [ ] **Step 8: Run the full suite**

```bash
cd backend && JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
  python3 -m pytest -q --ignore=tests/test_agent_instruction.py
```

Expected: 794 passed, 1 skipped.

- [ ] **Step 9: Commit**

```bash
git add kb_scraper/run.py backend/tests/test_kb_scraper.py
git commit -m "fix(kb-scrape): scope fingerprint reads and writes to the running engine

Two bugs surface the moment a second engine exists:

* The baseline load read every row, so Gemini hashes became Playwright's
  baseline and every page reported as changed. It also feeds the removed-page
  sweep, which would have proposed that all 59 known URLs were deleted.
* The first-sighting write was a bare INSERT, but url is unique -- so a page
  already carrying a Gemini row raised IntegrityError instead of updating.
  Both write sites now upsert."
```

---

### Task 4: Correct CLAUDE.md

`CLAUDE.md` is the canonical context file — "if anything elsewhere disagrees, trust this file." Its scrape-engine bullet currently states the Gemini engine is the default and presents the 44% blackout and the disabled fingerprint gate as current behaviour. After Tasks 1-3 that is false in a way that would actively mislead.

**Files:**
- Modify: `CLAUDE.md` — the bullet beginning "**The scrape engine is `gemini-3.6-flash` via the URL Context tool, and it can only see 56% of the site.**"

**Interfaces:**
- Consumes: the behaviour established in Tasks 1-3.
- Produces: nothing consumed by code.

- [ ] **Step 1: Rewrite the bullet**

Replace that bullet with:

```markdown
- **The scrape engine is Playwright + Chromium (`kb_scraper/crawler.py`), the default since 2026-08-03.** A headless browser reads each page, so there is no model in the read path: no refusals, byte-stable text, and the accordions get expanded. Measured against the live site on 2026-08-03: **21/21 pages readable, including all 18 the previous Gemini engine refused** (the whole of research-compliance, budget development, the PI handbooks, `/ora`); the same page read three times gave an identical hash (`fanda-cost-rates` 1540 chars, `iacuc-sops` 3102 chars, three times each); the IACUC SOPs page yields **50 `SOP n.n:` entries**, confirming `_expand_accordions()` reaches content absent from the served HTML; ~2.9s/page median, so a 59-page crawl is ~3 min against the 3600s task timeout. Because the text is byte-stable, **the fingerprint gate is live** — only pages that actually moved cost a model call. `gemini-3.6-flash` is still the **adjudicator** (see the next bullet); only page-reading changed.
- **The Gemini reader is still available as `--engine=gemini` / `SCRAPE_ENGINE=gemini`, and its limits are why it is no longer the default.** `kb_scraper/gemini_crawler.py` sends each URL to the model with `"tools":[{"urlContext":{}}]` and asks for a markdown extraction. Three measured properties: **(1) 26 of 59 ORA URLs (44%) come back EMPTY with `finishReason: RECITATION`** — the safety filter for reproducing source material. The blocked set is the compliance core: *all* of research-compliance (human subjects, animal research, COI, research security, NSPM-33, RCR, Maryland ethics), most of pre-award (budget development, internal routing form, proposal components, PI role), post-award reporting, the PI handbooks, and `/ora` itself. Prompt wording matters: "reproduce the full text verbatim" is blocked on nearly every page, "extract into markdown" on 44%. **(2) A RECITATION block is reported UNREADABLE, never as an empty or deleted page** — same invariant as `looks_unreadable()`, and the most destructive bug this job could have. **(3) The extraction is NOT byte-stable** — one unchanged page read three times at temperature 0 measured 1444/1466/1478 chars — so `resolve_audit()` forces `--audit` for this engine rather than leaving a gate in place that silently claims every page moved every run. Under this engine, quote grounding verifies against the model's own extraction rather than the page, which is weaker than golden rule 2 gives you on Playwright. **It calls REST, not the google-genai SDK** — this image pins `google-genai==1.14.0` to match `backend/requirements.txt` and 1.14.0 has no `url_context` field on `types.Tool`.
- **Fingerprints are engine-specific (`KbPageFingerprint.engine`).** A fingerprint hashes the extracted TEXT, and the two engines extract the same unchanged page differently, so a hash is only a valid baseline for the engine that wrote it. `load_baseline()` filters by engine and `upsert_fingerprint()` stamps it; a row from the other engine (or a pre-migration `NULL`) reads as a first sighting, which records the hash and proposes nothing. Two consequences worth knowing: this is what makes switching engines a silent re-baseline instead of 59 bogus "changed" proposals, and it scopes the removed-page sweep so a switch doesn't propose that every previously-known URL was deleted from the site. **`url` stays `unique=True`**, so there is one row per page and switching engines overwrites rather than preserving a per-engine baseline — which is why both write paths upsert (a bare INSERT raises `IntegrityError` on a page the other engine already saw).
```

- [ ] **Step 2: Verify no other part of CLAUDE.md contradicts this**

```bash
grep -n "SCRAPE_ENGINE\|56%\|44%\|gemini_crawler\|RECITATION" CLAUDE.md
```

Expected: every hit sits inside the bullets just rewritten. If the "KB web scrape" section further down still describes the fingerprint gate as disabled, update it to match.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): Playwright is the scrape engine; fingerprints are engine-scoped

CLAUDE.md is the canonical context file, and its scrape-engine bullet said the
Gemini reader was the default with the fingerprint gate disabled -- both false
after this change, and misleading in the direction that matters."
```

---

### Task 5: Deploy and verify (requires explicit user authorization)

**Do not run any command in this task until the user asks for it in that message.** Golden rule 7: nothing leaves the machine without an explicit request. This task is written so it is ready when they do.

**Files:** none — operational only.

**Interfaces:**
- Consumes: Tasks 1-4 committed and green.
- Produces: nothing consumed by code.

- [ ] **Step 1: Rebuild the scrape job image**

The job builds separately from the main deploy, and `SHORT_SHA` is **required** — it is populated only for repo-triggered builds, so a local directory submit leaves it empty and the image tag becomes the invalid `kb-scraper:`.

```bash
gcloud builds submit --config=cloudbuild.kb-scraper.yaml . \
  --substitutions=SHORT_SHA=$(git rev-parse --short HEAD)
```

- [ ] **Step 2: Deploy the backend for the `init_db()` migration**

```bash
gcloud builds submit --config=cloudbuild.yaml .
```

- [ ] **Step 3: Confirm the deploy actually ran**

A merge to `main` ships nothing — there is no Cloud Build trigger. Never infer a deploy from a merge.

```bash
gcloud run services describe oranavigator-backend --region=us-central1 \
  --format='value(status.latestReadyRevisionName)'
```

Expected: a revision newer than the one that was live before Step 2.

- [ ] **Step 4: Confirm the column exists in prod**

Check the backend logs for the migration line:

```bash
gcloud run services logs read oranavigator-backend --region=us-central1 --limit=200 \
  | grep -i "engine' column"
```

Expected: either `[OK] Successfully added 'engine' column!` on first boot, or no line at all on later boots (the `SELECT` succeeded, so no migration was needed).

- [ ] **Step 5: First run from the admin panel**

Click **Run scrape**. Expected, and worth checking against the change list:

- `pages_done` climbs past 59 — the crawl follows links, so it sees more than the KB's known URLs
- **zero** `modified` proposals — every page is a first sighting for this engine, so it baselines
- a batch of `new` rows — pages on the site with no KB document. This is expected and is a genuine content-gap inventory
- **zero** `removed` rows — the sweep is engine-scoped, so the Gemini-era URLs are not in `prior`
- few or no `skipped` (unreadable) rows

- [ ] **Step 6: Second run**

Click **Run scrape** again. Expected: near-zero changes, because the baseline now exists and the text is byte-stable. This is the run that proves the gate works — under the old engine every page was re-adjudicated every time.

- [ ] **Step 7 (optional): one deliberate audit sweep**

The KB was written in May and no document has ever been compared against its live page. `--audit` adjudicates every mapped page regardless of the gate. The admin button cannot pass it; it needs an args override on a manual execution, alongside the `SCRAPE_RUN_ID` the backend normally injects:

```bash
gcloud run jobs execute oranavigator-kb-scraper --region=us-central1 \
  --args="--run-id=<id>,--audit"
```

Create the `ScrapeRun` row first (or run it from the panel and note the id), since the job writes its progress to that row.

---

## Notes for the implementer

- **`SCRAPE_ENGINE` is set by hand on the live Job — the repo does not show it.** `cloudbuild.kb-scraper.yaml` never mentions it, but the deployed Job carried `SCRAPE_ENGINE=gemini` (observed 2026-08-03 on execution `…-dlccm`). An env var beats the code default, so flipping the default in `build_parser()` and shipping it would have changed nothing, silently. Line 107 uses **`--set-env-vars`**, which replaces the whole environment set, so deploying through that file clears the stale value — that, not the absence of the var from the yaml, is what makes the code default govern. **Verify with `gcloud run jobs executions describe <exec>` after deploying**, and never change line 107 to `--update-env-vars`.
- **The test file loads scraper modules by file path** (`_load("run")` at the top of `test_kb_scraper.py`), because importing the package pulls in `google-adk`, which is not always installed locally. Follow that pattern; do not add a normal import.
- **`kb_scraper/crawler.py` imports Playwright inside `crawl()`**, not at module scope, so `_load("run")` works in a test environment with no browser installed. Keep it that way.
- **Do not touch the `result.unreadable` branch.** A failed read is reported and the document is left alone; treating it as deleted content is the most destructive thing this job could do.
