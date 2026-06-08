# Suggested Questions

**In one line:** personalized starter prompts shown to each user.

## What it does (plain English)
Shows a user a few relevant questions they might want to ask, tailored to their role/department
and past activity — so they're not staring at a blank box.

## Where it lives
- `user_suggested_questions` table (`backend/models.py`).
- Regenerated in the post-commit hook after a chat turn.
- `GET /api/me/suggested-questions` — a pure DB read (fast).

## How it works
The heavy generation happens asynchronously (post-commit hook); the endpoint just reads the
latest stored set. Profile fields (department, title, role, interests) feed the personalization
via the memory system.

## API & data
- Endpoint: `GET /api/me/suggested-questions`.
- Table: `user_suggested_questions`.

## Status
✅ Built & deployed.
