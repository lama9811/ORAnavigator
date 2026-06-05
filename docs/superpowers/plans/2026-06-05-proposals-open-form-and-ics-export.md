# Proposals "Open form" buttons + Calendar (.ics) export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface a one-click "Open form" link on each proposal task and let a PI download/subscribe to their proposal deadlines as a calendar (.ics) feed.

**Architecture:** Both features reuse data that already exists. (A) Each `SubmissionTask` already stores `kb_doc_id`; the backend resolves it to a URL via the existing forms catalog and the React `TaskRow` renders a link. (B) Submissions already store `deadline`; a new pure `ics_export` module mints a scoped (email-less, replay-safe) JWT and builds an RFC 5545 calendar, served by two new endpoints.

**Tech Stack:** FastAPI + SQLAlchemy (backend), `python-jose` JWT (already used in `security.py`), pytest (service-level SQLite + full-app TestClient), Vite + React 19 (frontend), lucide-react icons.

**Design spec:** `docs/superpowers/specs/2026-06-05-proposals-open-form-and-ics-export-design.md`

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `backend/services/forms_catalog.py` | add `get_form(doc_id)` resolver | Modify |
| `backend/services/ics_export.py` | token mint/decode + RFC 5545 calendar builder (pure, no FastAPI) | **Create** |
| `backend/main.py` | enrich task dict with `kb_doc_url`/`kb_doc_title`; two new endpoints | Modify |
| `backend/tests/test_forms_catalog.py` | `get_form` unit tests | Modify |
| `backend/tests/test_ics_export.py` | token + calendar-builder unit tests (no `main` import) | **Create** |
| `backend/tests/test_proposals_api_e2e.py` | full-app TestClient tests for both endpoints + task enrichment | **Create** |
| `frontend/src/components/MyProposals.jsx` | "Open form" link in `TaskRow`; "Add to calendar" control | Modify |
| `frontend/src/components/MyProposals.css` | `.task-form-link`, `.calendar-export` styles | Modify |

Run all backend tests from `backend/`:
`cd backend && ../.venv/bin/python -m pytest -q`

---

## Task 1: `forms_catalog.get_form(doc_id)` resolver

**Files:**
- Modify: `backend/services/forms_catalog.py`
- Test: `backend/tests/test_forms_catalog.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_forms_catalog.py`:

```python
def test_get_form_returns_row_for_known_doc_id():
    from services.forms_catalog import list_forms, get_form
    forms = list_forms()
    assert forms, "catalog should be non-empty"
    known = forms[0]["doc_id"]
    row = get_form(known)
    assert row is not None
    assert row["doc_id"] == known
    assert "url" in row and "title" in row


def test_get_form_returns_none_for_unknown_doc_id():
    from services.forms_catalog import get_form
    assert get_form("definitely_not_a_real_doc_id_xyz") is None


def test_get_form_returns_none_for_none():
    from services.forms_catalog import get_form
    assert get_form(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_forms_catalog.py -q`
Expected: FAIL with `ImportError: cannot import name 'get_form'`.

- [ ] **Step 3: Write minimal implementation**

Append to `backend/services/forms_catalog.py` (after `list_forms`):

```python
@lru_cache(maxsize=1)
def _catalog_by_id() -> dict:
    """doc_id -> form row, built once from the cached catalog."""
    return {f["doc_id"]: f for f in _load_catalog()}


def get_form(doc_id: Optional[str]) -> Optional[dict]:
    """Return the catalog row for a single doc_id, or None if the id is
    falsy or not a form-like doc. Used to resolve a proposal task's
    kb_doc_id to an openable URL."""
    if not doc_id:
        return None
    return _catalog_by_id().get(doc_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_forms_catalog.py -q`
Expected: PASS (all, including the 3 new).

- [ ] **Step 5: Commit**

```bash
git add backend/services/forms_catalog.py backend/tests/test_forms_catalog.py
git commit -m "feat(forms): add get_form(doc_id) catalog resolver"
```

---

## Task 2: Enrich `_submission_task_to_dict` with `kb_doc_url` / `kb_doc_title`

This is verified end-to-end in Task 5 (the e2e suite), because `_submission_task_to_dict` lives in `main.py` and importing `main` runs network startup — unit-importing it in isolation is avoided per the existing test layout. Task 2 is the production edit; Task 5 Step asserts the enriched fields appear over the live route.

**Files:**
- Modify: `backend/main.py` (`_submission_task_to_dict`, ~line 2996)

- [ ] **Step 1: Write the implementation**

Replace `_submission_task_to_dict` in `backend/main.py`:

```python
def _submission_task_to_dict(t) -> dict:
    from services.forms_catalog import get_form
    form = get_form(t.kb_doc_id)
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "kb_doc_id": t.kb_doc_id,
        # Resolved form link (None when the task has no linked form, e.g.
        # biosketch / DMP / Specific Aims -- intentionally unlinked).
        "kb_doc_url": form["url"] if form else None,
        "kb_doc_title": form["title"] if form else None,
        "due_offset_days": t.due_offset_days,
        "status": t.status,
        "notes": t.notes,
        "sort_order": t.sort_order,
    }
```

- [ ] **Step 2: Smoke-import to catch syntax errors**

Run: `cd backend && ../.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat(proposals): resolve task kb_doc_id to kb_doc_url/kb_doc_title in API"
```

---

## Task 3: `ics_export` module — scoped token (mint/decode) + replay safety

**Files:**
- Create: `backend/services/ics_export.py`
- Test: `backend/tests/test_ics_export.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ics_export.py`:

```python
"""Unit tests for the .ics export module (no FastAPI / no DB).

The security contract: the calendar token carries `sub` + `scope` but NO
`email` claim, so it cannot be replayed as a normal auth bearer
(get_current_user authenticates on `email`, deps.py:48).
"""
import pytest
from jose import jwt

from services import ics_export
from security import JWT_SECRET, ALGORITHM


def test_mint_then_decode_roundtrips_user_id():
    tok = ics_export.mint_ics_token(42)
    assert ics_export.decode_ics_token(tok) == 42


def test_minted_token_has_no_email_claim():
    tok = ics_export.mint_ics_token(7)
    payload = jwt.decode(tok, JWT_SECRET, algorithms=[ALGORITHM])
    assert "email" not in payload          # replay-safety invariant
    assert payload["scope"] == "ics"
    assert payload["sub"] == 7


def test_decode_rejects_wrong_scope():
    bad = jwt.encode({"sub": 1, "scope": "auth"}, JWT_SECRET, algorithm=ALGORITHM)
    assert ics_export.decode_ics_token(bad) is None


def test_decode_rejects_garbage():
    assert ics_export.decode_ics_token("not.a.jwt") is None


def test_decode_rejects_empty():
    assert ics_export.decode_ics_token("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_ics_export.py -q`
Expected: FAIL with `ImportError` / `AttributeError: module 'services.ics_export' has no attribute 'mint_ics_token'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/services/ics_export.py`:

```python
"""Calendar (.ics) export for proposal deadlines.

Two concerns, no FastAPI/DB dependency so it unit-tests in isolation:

1. A scoped token (mint/decode). Calendar clients fetch the feed URL with
   no Authorization header, so we authenticate via a query-param JWT. The
   token carries `sub` (user_id) + `scope="ics"` and DELIBERATELY no
   `email` claim -- get_current_user authenticates on `email` (deps.py:48),
   so this token is useless as a normal bearer (replay-safe).
2. An RFC 5545 VCALENDAR builder over a list of (deadline-bearing) Submissions.
"""
from datetime import datetime, timezone
from typing import Optional

from jose import jwt, JWTError

from security import JWT_SECRET, ALGORITHM

_ICS_SCOPE = "ics"
_CAL_TZID = "America/New_York"   # matches the deadline-watcher cron tz
_PRODID = "-//ORA Navigator//Deadlines//EN"


def mint_ics_token(user_id: int) -> str:
    """Long-lived (no expiry) calendar token. No `email` claim -> not a
    valid auth bearer."""
    return jwt.encode(
        {"sub": user_id, "scope": _ICS_SCOPE}, JWT_SECRET, algorithm=ALGORITHM
    )


def decode_ics_token(token: str) -> Optional[int]:
    """Return the user_id for a valid ics-scoped token, else None."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("scope") != _ICS_SCOPE:
        return None
    sub = payload.get("sub")
    return int(sub) if sub is not None else None


def _escape(text: str) -> str:
    """RFC 5545 text escaping: backslash, comma, semicolon, newline."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def build_calendar(submissions: list) -> str:
    """Build a VCALENDAR string with one all-day VEVENT per submission that
    has a deadline. Submissions without a deadline are skipped. Always
    returns a valid (possibly event-less) calendar."""
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ORA Proposal Deadlines",
    ]
    for s in submissions:
        if not getattr(s, "deadline", None):
            continue
        day = s.deadline.strftime("%Y%m%d")           # all-day event
        sponsor = (getattr(s, "sponsor", "") or "").strip()
        title = (getattr(s, "title", "") or "Proposal").strip()
        summary = f"{sponsor}: {title}" if sponsor else title
        lines += [
            "BEGIN:VEVENT",
            f"UID:submission-{s.id}@ora.inavigator.ai",
            f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{day}",
            f"SUMMARY:{_escape(summary)} (deadline)",
            f"DESCRIPTION:{_escape('Proposal deadline tracked in ORA Navigator.')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    # RFC 5545 requires CRLF line endings.
    return "\r\n".join(lines) + "\r\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_ics_export.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/services/ics_export.py backend/tests/test_ics_export.py
git commit -m "feat(ics): scoped replay-safe token + RFC5545 calendar builder"
```

---

## Task 4: `build_calendar` content tests (escaping, deadline filter)

**Files:**
- Modify: `backend/tests/test_ics_export.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_ics_export.py`:

```python
from datetime import datetime as _dt
from types import SimpleNamespace


def _sub(id, title, sponsor, deadline):
    return SimpleNamespace(id=id, title=title, sponsor=sponsor, deadline=deadline)


def test_build_calendar_one_vevent_per_deadline():
    subs = [
        _sub(1, "NSF CAREER", "NSF", _dt(2026, 7, 1)),
        _sub(2, "No deadline", "NIH", None),   # skipped
    ]
    cal = ics_export.build_calendar(subs)
    assert cal.count("BEGIN:VEVENT") == 1
    assert "UID:submission-1@ora.inavigator.ai" in cal
    assert "DTSTART;VALUE=DATE:20260701" in cal
    assert "SUMMARY:NSF\\: NSF CAREER (deadline)" in cal
    assert cal.startswith("BEGIN:VCALENDAR")
    assert cal.rstrip().endswith("END:VCALENDAR")


def test_build_calendar_escapes_special_chars():
    subs = [_sub(3, "Title, with; chars", "", _dt(2026, 8, 9))]
    cal = ics_export.build_calendar(subs)
    assert "Title\\, with\\; chars" in cal


def test_build_calendar_empty_is_valid():
    cal = ics_export.build_calendar([])
    assert "BEGIN:VCALENDAR" in cal and "END:VCALENDAR" in cal
    assert "BEGIN:VEVENT" not in cal
```

- [ ] **Step 2: Run test to verify it passes** (implementation already exists from Task 3)

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_ics_export.py -q`
Expected: PASS (8 tests total).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_ics_export.py
git commit -m "test(ics): calendar builder content + escaping coverage"
```

---

## Task 5: Two endpoints + full-app e2e tests

**Files:**
- Modify: `backend/main.py` (add two routes near the other `/api/me/submissions` routes, ~line 3024)
- Create: `backend/tests/test_proposals_api_e2e.py`

- [ ] **Step 1: Write the failing e2e test**

Create `backend/tests/test_proposals_api_e2e.py`:

```python
"""Full-app TestClient tests for the proposals 'Open form' enrichment and
the .ics calendar export. Drives the real route -> dependency -> response
cycle (the layer that catches request-model / wiring bugs)."""
import os
os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import main
import deps
from db import Base
from models import User, Submission, SubmissionTask
from security import hash_password
from services import ics_export


@pytest.fixture
def ctx():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestingSession()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    seed.add(u)
    seed.commit()
    uid = u.id
    # A submission with a deadline + one task linked to a real form doc_id.
    from services.forms_catalog import list_forms
    real_doc_id = list_forms()[0]["doc_id"]
    sub = Submission(user_id=uid, title="NSF CAREER", sponsor="NSF",
                     deadline=datetime.now(timezone.utc) + timedelta(days=20),
                     status="active")
    seed.add(sub)
    seed.commit()
    seed.add(SubmissionTask(submission_id=sub.id, title="Internal routing form",
                            kb_doc_id=real_doc_id, status="pending", sort_order=0))
    seed.add(SubmissionTask(submission_id=sub.id, title="Biosketch",
                            kb_doc_id=None, status="pending", sort_order=1))
    seed.commit()
    sub_id = sub.id
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
    yield c, uid, sub_id, real_doc_id
    main.app.dependency_overrides.clear()


def test_task_dict_includes_resolved_form_url(ctx):
    c, uid, sub_id, real_doc_id = ctx
    r = c.get(f"/api/me/submissions/{sub_id}")
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    linked = next(t for t in tasks if t["kb_doc_id"] == real_doc_id)
    assert linked["kb_doc_url"]            # truthy resolved URL
    assert linked["kb_doc_title"]
    unlinked = next(t for t in tasks if t["title"] == "Biosketch")
    assert unlinked["kb_doc_url"] is None


def test_deadlines_token_requires_auth(ctx):
    c, *_ = ctx
    # Override removed for this call: hit the route with the auth dep active
    main.app.dependency_overrides.pop(main.get_current_user, None)
    r = c.get("/api/me/deadlines-token")           # no Bearer
    assert r.status_code in (401, 403)


def test_deadlines_token_returns_urls(ctx):
    c, *_ = ctx
    r = c.get("/api/me/deadlines-token")
    assert r.status_code == 200
    body = r.json()
    assert "/api/me/deadlines.ics?token=" in body["ics_url"]
    assert body["webcal_url"].startswith("webcal://")


def test_ics_feed_valid_token_returns_calendar(ctx):
    c, uid, sub_id, _ = ctx
    tok = ics_export.mint_ics_token(uid)
    r = c.get(f"/api/me/deadlines.ics?token={tok}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in r.text
    assert f"UID:submission-{sub_id}@ora.inavigator.ai" in r.text


def test_ics_feed_rejects_missing_token(ctx):
    c, *_ = ctx
    assert c.get("/api/me/deadlines.ics").status_code == 401


def test_ics_feed_rejects_bad_token(ctx):
    c, *_ = ctx
    assert c.get("/api/me/deadlines.ics?token=garbage").status_code == 401


def test_ics_token_cannot_be_replayed_as_bearer(ctx):
    c, uid, *_ = ctx
    # Drop the get_current_user override so the REAL guard runs.
    main.app.dependency_overrides.pop(main.get_current_user, None)
    tok = ics_export.mint_ics_token(uid)
    r = c.get("/api/me/submissions", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (401, 403)     # no email claim -> rejected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_proposals_api_e2e.py -q`
Expected: FAIL — `test_task_dict_includes_resolved_form_url` may pass (Task 2 done) but the `deadlines-token` / `deadlines.ics` tests 404 (routes not added yet).

- [ ] **Step 3: Add the two endpoints**

In `backend/main.py`, near the other `/api/me/submissions` routes (after the
`list_my_submissions` route, ~line 3037), add:

```python
@app.get("/api/me/deadlines-token")
async def my_deadlines_token(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Mint the per-user calendar URLs (download + webcal subscribe).
    The token is scoped to 'ics' and carries no email claim, so it can't be
    used as a normal auth bearer."""
    from services.ics_export import mint_ics_token
    tok = mint_ics_token(user["user_id"])
    base = str(request.base_url).rstrip("/")          # e.g. https://host
    ics_url = f"{base}/api/me/deadlines.ics?token={tok}"
    host = request.url.hostname or ""
    if request.url.port and request.url.port not in (80, 443):
        host = f"{host}:{request.url.port}"
    webcal_url = f"webcal://{host}/api/me/deadlines.ics?token={tok}"
    return {"ics_url": ics_url, "webcal_url": webcal_url}


@app.get("/api/me/deadlines.ics")
async def my_deadlines_ics(
    token: str = "",
    db: Session = Depends(get_db),
):
    """Token-authed (no Bearer) calendar feed of the user's proposal
    deadlines. Calendar apps fetch this URL directly."""
    from fastapi import Response
    from services.ics_export import decode_ics_token, build_calendar
    user_id = decode_ics_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid calendar token")
    subs = _proposals_service.list_submissions(db, user_id=user_id)
    body = build_calendar(subs)
    return Response(
        content=body,
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="ora-deadlines.ics"'},
    )
```

- [ ] **Step 4: Run the e2e suite to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_proposals_api_e2e.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_proposals_api_e2e.py
git commit -m "feat(api): /api/me/deadlines-token + /api/me/deadlines.ics calendar feed"
```

---

## Task 6: Frontend — "Open form" link in `TaskRow`

**Files:**
- Modify: `frontend/src/components/MyProposals.jsx` (import line 7; `TaskRow` ~line 383)
- Modify: `frontend/src/components/MyProposals.css`

- [ ] **Step 1: Add the icons to the import**

Change line 7 of `MyProposals.jsx`:

```jsx
import { ArrowLeft, Calendar, CalendarPlus, Check, CheckCircle, Circle, ClipboardCheck, ExternalLink, FileText, Plus, Trash2, X } from "lucide-react";
```

- [ ] **Step 2: Render the link in `TaskRow`**

In `TaskRow` (`MyProposals.jsx`), inside `<div className="task-body">`, after the
`due_offset_days` block (after line 409 `</div>`), add:

```jsx
        {task.kb_doc_url && (
          <a
            className="task-form-link"
            href={task.kb_doc_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            <ExternalLink size={12} />
            <span>Open {task.kb_doc_title || "form"}</span>
          </a>
        )}
```

- [ ] **Step 3: Add the CSS**

Append to `frontend/src/components/MyProposals.css`:

```css
.task-form-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--accent, #1d4ed8);
  text-decoration: none;
}
.task-form-link:hover {
  text-decoration: underline;
}
```

- [ ] **Step 4: Manual verify (dev server)**

Run the frontend (`cd frontend && npm run dev -- --port 3001`), open a proposal
that has a task linked to a form (an NSF/generic submission's "internal routing
form" task). Confirm "Open <form> ↗" appears on linked tasks and opens the PDF
in a new tab; unlinked tasks (Biosketch, DMP) show no link.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MyProposals.jsx frontend/src/components/MyProposals.css
git commit -m "feat(proposals-ui): Open form link on tasks with a resolved kb_doc_url"
```

---

## Task 7: Frontend — "Add to calendar" control

**Files:**
- Modify: `frontend/src/components/MyProposals.jsx` (list header ~line 221; the list component holds `submissions`)
- Modify: `frontend/src/components/MyProposals.css`

- [ ] **Step 1: Add a calendar-export handler + button in the list view**

In the list component that renders `submissions.map(...)` (~line 227), above the
list, add a button. First add the handler inside that component (it already has
`submissions` and `API_BASE`):

```jsx
  const addToCalendar = async () => {
    const token = localStorage.getItem("token");
    const r = await fetch(`${API_BASE}/api/me/deadlines-token`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!r.ok) return;
    const { ics_url } = await r.json();
    // Trigger a download of the .ics file.
    const a = document.createElement("a");
    a.href = ics_url;
    a.download = "ora-deadlines.ics";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
```

Then, only when at least one submission has a deadline, render the button near
the list header (after the intro text, ~line 221):

```jsx
      {submissions.some((s) => s.deadline) && (
        <button className="calendar-export" onClick={addToCalendar}>
          <CalendarPlus size={16} />
          <span>Add deadlines to calendar</span>
        </button>
      )}
```

- [ ] **Step 2: Add the CSS**

Append to `frontend/src/components/MyProposals.css`:

```css
.calendar-export {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 16px;
  padding: 8px 14px;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--accent, #1d4ed8);
  background: transparent;
  border: 1px solid var(--accent, #1d4ed8);
  border-radius: 8px;
  cursor: pointer;
}
.calendar-export:hover {
  background: var(--accent, #1d4ed8);
  color: #fff;
}
```

- [ ] **Step 3: Manual verify**

With the dev server running and at least one proposal that has a deadline,
click "Add deadlines to calendar" → an `ora-deadlines.ics` file downloads and
opens in the OS calendar app showing the deadline as an all-day event.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MyProposals.jsx frontend/src/components/MyProposals.css
git commit -m "feat(proposals-ui): Add deadlines to calendar (.ics download)"
```

---

## Task 8: Full verification (per ora-deploy-discipline)

- [ ] **Step 1: Run the FULL backend suite**

Run: `cd backend && ../.venv/bin/python -m pytest -q`
Expected: all green — was **319**, now **319 + 16 new** (3 forms + 8 ics + 7 e2e ≈ 18; confirm the exact final count and record it).

- [ ] **Step 2: Frontend build sanity**

Run: `cd frontend && npm run build`
Expected: build succeeds (no unresolved import / JSX error).

- [ ] **Step 3: Local UI smoke in incognito (PWA cache bypass)**

Per CLAUDE.md deploy discipline, verify in an incognito window:
1. Open a proposal with a form-linked task → "Open form" opens the right PDF.
2. Unlinked tasks show no button.
3. "Add deadlines to calendar" downloads a valid `.ics` that imports cleanly.

- [ ] **Step 4: Record the new test count**

Update CLAUDE.md "Backend test count" and Open-work items #3 and #7 to DONE.

```bash
git add CLAUDE.md
git commit -m "docs: mark open-work #3 (Open form) + #7 (.ics export) done; bump test count"
```

---

## Self-Review

**Spec coverage:**
- Feature A backend-resolve → Task 1 (`get_form`) + Task 2 (enrich dict) + Task 5 e2e. ✓
- Feature A frontend → Task 6. ✓
- Feature B token (email-less, replay-safe) → Task 3 + Task 5 replay test. ✓
- Feature B calendar builder + escaping → Task 3/4. ✓
- Feature B endpoints (mint + feed) → Task 5. ✓
- Feature B frontend control → Task 7. ✓
- TDD + verification + deploy-discipline smoke → throughout + Task 8. ✓
- Non-goals respected: deadlines-only, no per-task dates, America/New_York, no schema change. ✓

**Placeholder scan:** none — every code step has complete code.

**Type consistency:** `get_form(doc_id)` returns a row with `url`/`title` (Task 1) consumed as `form["url"]`/`form["title"]` (Task 2); `mint_ics_token`/`decode_ics_token`/`build_calendar` signatures match across Tasks 3/5; frontend reads `task.kb_doc_url`/`kb_doc_title` (Task 6) and `ics_url`/`webcal_url` (Task 7) exactly as the API returns them (Task 2/5). ✓

**Note for the implementer:** Task 5's `test_deadlines_token_requires_auth` and `test_ics_token_cannot_be_replayed_as_bearer` mutate `main.app.dependency_overrides` mid-test; the fixture's final `.clear()` resets state between tests, so order independence holds.
