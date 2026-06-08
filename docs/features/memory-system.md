# Memory System

**In one line:** remembers durable facts about each user so answers stay personal across sessions.

## What it does (plain English)
Over time the assistant learns a user's research-admin context — their role, department, active
grant, IRB protocol, interests — and uses it to personalize answers. It distills these from chat
history; it does **not** store PII or financial details.

## Where it lives
- `backend/services/memory_service.py` — extraction, consolidation, recall, profile mirroring.
- `user_memories` table (`backend/models.py`) — facts + embeddings.
- Internal endpoints in `backend/main.py`.

## How it works
- **Rolling session summary** + **embedding-based semantic recall** on `user_memories` +
  **real-time extraction** every few turns.
- **Daily 3am consolidation cron** (`POST /api/internal/memory/consolidate`): reads `chat_history`
  only (never edits it), builds a transcript per active user, asks Gemini for durable one-line
  facts, writes them to `user_memories`. `_merge_memories` dedups and **caps at 5 per type**.
- **Idle-sweep** (every 5 min, `POST /api/internal/memory/idle-sweep`): catches mid-session facts
  for users idle 5–10 min before the nightly run.
- **Profile mirroring** (`mirror_profile_to_memories`): profile fields upsert matching
  `UserMemory` rows, and **profile values overwrite auto-extracted ones**.

## API & data
- Endpoints: `POST /api/internal/memory/{consolidate,idle-sweep,backfill-profiles}` (X-Research-Secret).
- Table: `user_memories`.

## Don't regress (load-bearing)
- Consolidation **distills + caps facts**; it never deletes raw chat transcripts.
- PII/financial exclusion is enforced by the extraction prompt.

## Status
✅ Built & deployed. 3am cron live (`ora-memory-consolidate`).
