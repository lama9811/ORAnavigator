# Calendar (.ics) Export

**In one line:** add your proposal deadlines to Google/Apple/Outlook calendar in one click.

## What it does (plain English)
Generates a calendar file (and a `webcal://` subscribe link) containing an all-day event for each
proposal deadline, so deadlines show up in the user's own calendar app.

## Where it lives
- `backend/services/ics_export.py`.
- Frontend: "Add deadlines to calendar" button in `frontend/src/MyProposals.jsx`.

## How it works
- Mints a **scoped JWT** (`sub` + `scope="ics"`, **no `email` claim**) so the token can't be
  replayed as an auth bearer (`get_current_user` authenticates on `email`).
- Builds an RFC 5545 VCALENDAR — one all-day VEVENT per deadline-bearing submission,
  `America/New_York`. (Per spec 3.3.11 the colon is **not** escaped.)
- Endpoints: `GET /api/me/deadlines-token` (Bearer-authed; returns `ics_url` + `webcal_url`) and
  `GET /api/me/deadlines.ics?token=…` (token-authed, no Bearer).

## API & data
- Endpoints: `GET /api/me/deadlines-token`, `GET /api/me/deadlines.ics`.
- Reads `submissions`.
- Tests: `backend/tests/test_ics_export.py` (8 cases).

## Don't regress (load-bearing)
- The scoped token must **never** carry an `email` claim (replay-safety).

## Status
✅ Built & deployed.
