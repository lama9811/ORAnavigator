# Caching

**In one line:** a three-tier cache that returns known answers instantly and cuts cost.

## What it does (plain English)
If a question (or one very similar) was answered before, return that answer immediately instead
of paying for another AI call.

## Where it lives
- `backend/cache.py`.

## How it works
- **L1** — in-memory `TTLCache` (always on).
- **L2** — Redis (`ora-redis-url`); local dev falls back to L1 only unless `REDIS_URL` is set.
- **Semantic** — embeds the query with `text-embedding-004` and matches at **0.95 cosine** so
  re-phrasings hit the cache too.

## Don't regress (load-bearing)
- **Cache keys include `user_id`** — answers aren't leaked across users.
- **Refusals/deflections and personal-recall queries are never cached** (shared
  `_is_personal_recall` detector). Caching a refusal would freeze a fixable gap.

## Status
✅ Built & deployed.
