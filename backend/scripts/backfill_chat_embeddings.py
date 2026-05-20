"""Backfill embeddings for existing chat_history rows.

One-time job: per user, embeds the most recent N turns (default 500) that
don't yet have an embedding. Older rows keep their text but won't be
semantically searchable — that's intentional, we don't pay to embed
year-old conversations.

Usage:
    python -m scripts.backfill_chat_embeddings                # all users, last 500/user
    python -m scripts.backfill_chat_embeddings --user-id 42
    python -m scripts.backfill_chat_embeddings --per-user 200
    python -m scripts.backfill_chat_embeddings --dry-run

Throttled at EMBEDDING_MAX_RPM. Idempotent — only touches NULL embeddings.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import distinct  # noqa: E402

from db import SessionLocal  # noqa: E402
from models import ChatHistory  # noqa: E402
from services.embedding_util import embed_text_throttled  # noqa: E402
from services.memory_service import (  # noqa: E402
    EMBEDDING_MODEL_VERSION,
    _serialize_embedding,
)


def backfill_for_user(db, user_id: int, per_user: int, dry_run: bool, batch_size: int) -> tuple[int, int, int]:
    rows = (
        db.query(ChatHistory)
        .filter(ChatHistory.user_id == user_id, ChatHistory.embedding.is_(None))
        .order_by(ChatHistory.id.desc())
        .limit(per_user)
        .all()
    )
    if not rows:
        return 0, 0, 0

    processed = 0
    skipped = 0
    failed = 0
    for i, row in enumerate(rows, 1):
        uq = (row.user_query or "").strip()
        br = (row.bot_response or "").strip()
        if not uq and not br:
            skipped += 1
            continue
        if dry_run:
            processed += 1
            continue
        combined = f"User: {uq}\nAssistant: {br[:1500]}"
        vec = embed_text_throttled(combined)
        if not vec:
            failed += 1
            continue
        row.embedding = _serialize_embedding(vec)
        row.embedding_model = EMBEDDING_MODEL_VERSION
        processed += 1
        if i % batch_size == 0:
            db.commit()
        time.sleep(0.05)

    db.commit()
    return processed, skipped, failed


def backfill(user_id: int | None, per_user: int, dry_run: bool, batch_size: int) -> dict:
    db = SessionLocal()
    try:
        if user_id:
            user_ids = [user_id]
        else:
            user_ids = [uid for (uid,) in db.query(distinct(ChatHistory.user_id)).all() if uid is not None]
        print(f"[backfill] users to scan: {len(user_ids)} | per-user limit: {per_user}")

        total_processed = 0
        total_skipped = 0
        total_failed = 0
        for uid in user_ids:
            p, s, f = backfill_for_user(db, uid, per_user, dry_run, batch_size)
            total_processed += p
            total_skipped += s
            total_failed += f
            print(f"  user={uid}: processed={p} skipped={s} failed={f}")

        result = {
            "users": len(user_ids),
            "processed": total_processed,
            "skipped": total_skipped,
            "failed": total_failed,
        }
        print(f"[backfill] DONE {result}")
        return result
    finally:
        db.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--user-id", type=int, default=None, help="Limit to a single user")
    p.add_argument("--per-user", type=int, default=500, help="Max recent turns per user")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--batch-size", type=int, default=50)
    args = p.parse_args()
    backfill(
        user_id=args.user_id,
        per_user=args.per_user,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
