# Auth & Roles

**In one line:** Morgan-only signup, email-verified login, and user/admin roles.

## What it does (plain English)
Only `@morgan.edu` addresses can sign up. New users verify their email before they can log in
(admins are exempt). There are two roles: regular `user` and `admin`.

## Where it lives
- `backend/routers/auth.py` — signup, login, verification, password reset.
- `backend/security.py` — password hashing (bcrypt, rounds = 12) + JWT.
- `frontend/src/Login.jsx`, `frontend/src/App.jsx` (`RequireAuth` route guard).

## How it works
- Signup restricted to `@morgan.edu`; `ALLOW_TEST_EMAILS=false` in prod.
- Email verification required (skipped for admins).
- Login returns a JWT; the frontend stores it and guards routes client-side (`RequireAuth` reads
  the token from `localStorage` — it does **not** call `/api/me`, so login isn't gated on a slow fetch).
- Roles: `user` (default) / `admin`. `milam5@morgan.edu` is admin (id 6); `admin@local.dev` is backup.

## API & data
- Endpoints: under `/api/...` in `routers/auth.py` (login, signup, verify, reset).
- Table: `users` (profile columns `department`, `title`, `primary_role`).
- E2E: `backend/tests/test_profile_api_e2e.py`.

## Don't regress (load-bearing)
- **Login itself is fast** (~10-300ms). If login feels slow it's infrastructure (cold start / DB
  reachability), not this code — see `design-system/architecture.md` §3.
- Single source of truth for request models: `ProfileUpdateRequest` lives in `deps.py`
  (a local stub once shadowed it and silently dropped fields — don't redefine request models locally).

## Status
✅ Built & deployed.
