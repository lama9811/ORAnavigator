# Proposals tracker: "Open form" buttons + Calendar (.ics) export — Design

**Date:** 2026-06-05
**Status:** Approved (design), pending spec review
**Author:** brainstorming session
**Open-work items closed:** #3 (frontend "Open form" UI consuming `kb_doc_id`), #7 (`.ics` deadline export)

## Goal

Make the My Proposals tracker actionable by closing two half-built loops that
already have their backend data in place:

- **Feature A — "Open form" buttons.** Each proposal task already carries a
  `kb_doc_id` (backfilled 2026-06-02, returned by the API at
  `main.py:3001`). Surface a one-click link to the actual form/template PDF in
  the task list so the checklist becomes an action list.
- **Feature B — Calendar `.ics` export.** Submissions already have a `deadline`.
  Let a PI download a `.ics` file or subscribe via `webcal://` so every
  proposal deadline lands on their Outlook/Google calendar and stays current.

Both ship in one implementation pass.

## Non-goals (YAGNI)

- No individual task due-dates in the calendar — deadlines only (v1).
- No in-calendar reminders — Deadline Watcher already emails those.
- No timezone picker — use `America/New_York` (matches the existing crons).
- No revocable per-user calendar token / rotation endpoint — a scoped JWT is
  enough for v1; revisit only if a token leak becomes a real concern.
- No new DB columns or tables for either feature.

---

## Feature A — "Open form" buttons

### Approach (chosen): backend resolves `kb_doc_id` → URL

The frontend stays dumb: it renders a link only when the task dict carries a
resolved URL. Single source of truth, no extra network call, no duplicated
resolution logic in JS, and tasks with no real form (biosketch, DMP,
Current & Pending, Facilities, Specific Aims, RCR — intentionally left
unlinked) simply show no button.

Rejected alternative: frontend fetches `/api/forms` and builds a
`doc_id → url` map. More network, duplicate logic, and `/api/forms` only
contains *form-like* docs, so non-form links would break silently.

### Components

1. **`backend/services/forms_catalog.py` — new `get_form(doc_id)` helper.**
   - Returns the catalog row dict (`{doc_id, title, url, source_url, ...}`)
     for a given `doc_id`, or `None` if not found.
   - Implemented over the existing `_load_catalog()` (lru-cached), e.g. a
     module-level `@lru_cache` dict keyed by `doc_id` built from
     `_load_catalog()`. No new file reads.
   - Edge case: a `kb_doc_id` that exists in `_all_documents.jsonl` but is not
     "form-like" (so absent from the forms catalog) returns `None` → no button.
     All four current template links were verified present in the forms catalog
     on 2026-06-02, so this only guards future drift.

2. **`backend/main.py` — `_submission_task_to_dict` (line ~2996).**
   - When `t.kb_doc_id` is set, call `forms_catalog.get_form(t.kb_doc_id)` and
     add two fields to the returned dict:
     - `kb_doc_url`: the resolved `url` (DocuSign/PDF/Word link), or `None`.
     - `kb_doc_title`: the resolved `title`, or `None`.
   - `kb_doc_id` itself stays in the dict (already there) — unchanged.
   - When the doc can't be resolved, both new fields are `None`.

3. **`frontend/src/components/MyProposals.jsx` — `TaskRow` (line ~383).**
   - Below the existing `task-meta` block, render an "Open form ↗" link when
     `task.kb_doc_url` is truthy:
     `<a href={task.kb_doc_url} target="_blank" rel="noopener noreferrer" className="task-form-link">Open {task.kb_doc_title || "form"} ↗</a>`
   - Clicking must NOT toggle the task (the row's check toggle is on the
     checkbox, not the whole row in detail view — verify the link's click
     doesn't bubble into a toggle; add `onClick={e => e.stopPropagation()}` if
     needed).

4. **`frontend/src/components/MyProposals.css` — `.task-form-link`.**
   - Small inline link styled consistently with the existing design system
     (link color, small font, hover underline). Match existing tokens.

### Data flow

```
SubmissionTask.kb_doc_id  ──>  forms_catalog.get_form(doc_id)  ──>  {url, title}
        (DB)                         (static JSON, cached)
                                            │
        _submission_task_to_dict adds kb_doc_url / kb_doc_title
                                            │
                  GET /api/me/submissions/{id}  (JSON)
                                            │
                        TaskRow renders "Open form ↗"
```

---

## Feature B — Calendar (.ics) export

### Approach (chosen): scoped-JWT query-param token

Calendar clients fetch the URL with no Authorization header, so the normal
`HTTPBearer` guard can't protect it. Mint a long-lived JWT with a distinct
scope claim and pass it as a query param.

- Token: `jwt.encode({"sub": user_id, "scope": "ics"}, JWT_SECRET, ALGORITHM)`
  using the existing `backend/security.py` utilities. Long/no expiry (a
  calendar subscription is meant to persist).
- **Replay safety (verified against `deps.py:48`):** `get_current_user`
  authenticates by the **`email`** claim, not `sub`. The `.ics` token is minted
  with `sub` + `scope` and **deliberately NO `email` claim**, so replaying it as
  a normal `Authorization: Bearer` 403s (`"Invalid token"` — no email). The
  `.ics` endpoint therefore identifies the user by `sub` (user_id) and
  additionally requires `scope == "ics"`. This is the security boundary — a
  test MUST assert that the ics token is rejected by an authed endpoint.
- Rejected alternative: opaque per-user token in a new `users` column +
  rotation endpoint. Needs a migration; revocability isn't worth it for v1.

### Components

1. **`backend/main.py` — token mint endpoint (authed).**
   `GET /api/me/deadlines-token` (behind the normal `get_current_user` guard)
   returns `{ "ics_url": "<API_BASE>/api/me/deadlines.ics?token=<jwt>",
   "webcal_url": "webcal://<host>/api/me/deadlines.ics?token=<jwt>" }`.
   The frontend never has to construct the token itself.

2. **`backend/main.py` — the feed endpoint (token-authed, no Bearer).**
   `GET /api/me/deadlines.ics?token=<jwt>`:
   - Decode token; reject if invalid or `scope != "ics"` → 401.
   - Load the user's submissions with a non-null `deadline`
     (`proposals_service.list_submissions`).
   - Emit a valid `text/calendar` body:
     - `BEGIN:VCALENDAR` / `VERSION:2.0` / `PRODID:-//ORA Navigator//Deadlines//EN`
     - One `VEVENT` per submission: `UID` = stable per submission
       (`submission-{id}@ora.inavigator.ai`), `DTSTART;VALUE=DATE` or
       `DTSTART` with `America/New_York`, `SUMMARY` = sponsor + title,
       `DESCRIPTION` = task summary / link back to the app, `DTSTAMP`.
     - Deadlines are treated as all-day or end-of-day events in
       `America/New_York`.
   - `Content-Disposition: attachment; filename="ora-deadlines.ics"` for the
     download case (subscription clients ignore it).
   - Use a tiny hand-rolled ICS string builder (no new dependency) OR the
     `ics` library if already vendored — prefer hand-rolled to avoid adding a
     dep. Escape `,` `;` `\` and newlines per RFC 5545.

3. **`frontend/src/components/MyProposals.jsx` — "Add to calendar" control.**
   - In the list header (near the existing intro text, ~line 221) add a button
     that calls `GET /api/me/deadlines-token`, then offers:
     - **Download** — anchor to `ics_url` with `download`.
     - **Subscribe** — copy/open the `webcal_url`.
   - Only show it when the user has ≥1 submission with a deadline.

4. **`frontend/src/components/MyProposals.css`** — style the calendar button to
   match the existing action buttons.

### Data flow

```
[Add to calendar] ──> GET /api/me/deadlines-token (Bearer)
                            │ returns ics_url + webcal_url (token embedded)
                            ▼
   download .ics  OR  webcal:// subscribe
                            │
        GET /api/me/deadlines.ics?token=<jwt>   (no Bearer)
                            │ decode+scope-check
        submissions w/ deadline ──> VCALENDAR text/calendar
```

---

## Error handling

- **Feature A:** unresolved `kb_doc_id` → `kb_doc_url` is `None` → no button
  (no error surfaced). Forms catalog read failure already returns `[]`
  gracefully.
- **Feature B:** invalid/expired/wrong-scope token → `401`. No submissions
  with deadlines → a valid but empty `VCALENDAR` (clients accept it). Bad
  `deadline` data is already validated on write (`_parse_deadline`).

## Testing (TDD — write tests first)

Backend (`pytest`, target: keep all existing 319 green, add new):

- `forms_catalog.get_form`: returns row for a known form `doc_id`; `None` for
  unknown / non-form `doc_id`.
- `_submission_task_to_dict`: a task with a real linked `kb_doc_id` gets
  `kb_doc_url`/`kb_doc_title`; an unlinked task gets `None`/`None`; a task with
  a `kb_doc_id` absent from the catalog gets `None`.
- `/api/me/deadlines-token`: requires auth (401 without Bearer); returns both
  URLs with a token.
- `/api/me/deadlines.ics`: valid `ics`-scoped token → 200 `text/calendar` with
  one `VEVENT` per deadline-bearing submission, correct `UID`/`SUMMARY`;
  missing/invalid/wrong-scope token → 401; user with no deadlines → empty but
  valid calendar; one user cannot read another user's deadlines.
- **Replay safety:** an `ics`-scoped token presented as
  `Authorization: Bearer` to an authed endpoint (e.g. `/api/me/submissions`)
  is rejected (403) because it carries no `email` claim.
- ICS escaping: a submission whose title contains `,`/`;`/newline is escaped
  per RFC 5545.

Frontend: manual verification (no JS test harness in this repo) — see below.

## Verification (per ora-deploy-discipline)

1. Run the backend suite locally; confirm green (was 319, expect +N).
2. Local UI smoke (incognito, PWA cache bypass):
   - Open a proposal with linked tasks → "Open form ↗" appears and opens the
     correct PDF in a new tab; unlinked tasks show no button.
   - "Add to calendar" → download `.ics` opens in a calendar app with the
     right deadline; `webcal://` subscribe shows the event.
3. Only then claim "works."

## Files touched (summary)

| File | Change |
|---|---|
| `backend/services/forms_catalog.py` | add `get_form(doc_id)` |
| `backend/main.py` | `_submission_task_to_dict` adds `kb_doc_url`/`kb_doc_title`; new `/api/me/deadlines-token` + `/api/me/deadlines.ics` |
| `frontend/src/components/MyProposals.jsx` | "Open form" link in `TaskRow`; "Add to calendar" control |
| `frontend/src/components/MyProposals.css` | `.task-form-link`, calendar button |
| `backend/tests/...` | new tests for both features |
