# Deadline Watcher (AI Agent)

**In one line:** emails PIs as their proposal deadlines approach so nothing is missed.

## What it does (plain English)
Watches active proposals and emails the owner when a deadline is 14 / 7 / 3 / 1 / 0 days away,
with their open tasks listed. Each (proposal, threshold) only emails once.

## Where it lives
- `backend/services/deadline_watcher.py`.
- Cron endpoint `POST /api/internal/deadlines/check` in `backend/main.py`.
- Cloud Scheduler job `ora-deadline-watcher` (daily 7am ET).

## How it works
- `find_due_reminders` (pure read) finds submissions sitting on a threshold.
- `compose_reminder_email` builds a deterministic subject + body; `compose_reminder_email_ai`
  (`use_ai=True`) personalizes the body and task ordering, with a **hard fallback** to the
  deterministic template — a reminder always sends even if Gemini is down.
- `deadline_reminder_log` gives per-(submission, threshold) idempotency so retries never double-email.

## API & data
- Endpoint: `POST /api/internal/deadlines/check` (X-Research-Secret).
- Tables: `submissions`, `submission_tasks`, `deadline_reminder_log`.

## Don't regress (load-bearing)
- Idempotency log must be written after each send.
- Subject stays deterministic; only the body is AI-personalized.

## Status
✅ Built & deployed. Cron live.

> **Reuse note:** this is the cleanest template to clone for a future **Training/Cert Tracker**
> (same threshold-email + idempotency-log shape).
