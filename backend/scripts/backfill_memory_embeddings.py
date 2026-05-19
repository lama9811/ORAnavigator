"""Backfill embeddings for existing user_memories rows.

One-time job: iterates rows where embedding IS NULL, embeds the content via
the shared Vertex client, persists the result. Safe to re-run — only touches
NULL rows.

Usage:
    python -m scripts.backfill_memory_embeddings           # backfill all NULLs
    python -m scripts.backfill_memory_embeddings --limit 100
    python -m scripts.backfill_memory_embeddings --dry-run

Throttled at EMBEDDING_MAX_RPM (default 50). Set the env var to a lower
number if you're sharing quota with the live chat path.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Add backend/ to path so we can import sibling modules when run as a script.
HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from db import SessionLocal  # noqa: E402
from models import UserMemory  # noqa: E402
from services.embedding_util import embed_text_throttled  # noqa: E402
from services.memory_service import (  # noqa: E402
    EMBEDDING_MODEL_VERSION,
    _serialize_embedding,
)


def backfill(limit: int | None = None, dry_run: bool = False, batch_size: int = 50) -> dict:
    db = SessionLocal()
    processed = 0
    skipped = 0
    failed = 0
    try:
        q = db.query(UserMemory).filter(UserMemory.embedding.is_(None)).order_by(UserMemory.id.asc())
        if limit:
            q = q.limit(limit)
        rows = q.all()
        total = len(rows)
        print(f"[backfill] {total} user_memories rows need embedding")
        if not total:
            return {"processed": 0, "skipped": 0, "failed": 0, "total": 0}

        for i, row in enumerate(rows, 1):
            content = (row.content or "").strip()
            if not content:
                skipped += 1
                continue
            if dry_run:
                processed += 1
                continue
            vec = embed_text_throttled(content)
            if not vec:
                failed += 1
                if i % 10 == 0:
                    print(f"  [{i}/{total}] processed={processed} failed={failed}")
                continue
            row.embedding = _serialize_embedding(vec)
            row.embedding_model = EMBEDDING_MODEL_VERSION
            processed += 1
            if i % batch_size == 0:
                db.commit()
                print(f"  [{i}/{total}] committed batch — processed={processed} failed={failed}")
            # Light throttle in addition to RPM limiter.
            time.sleep(0.1)

        db.commit()
        print(f"[backfill] DONE total={total} processed={processed} skipped={skipped} failed={failed}")
        return {"processed": processed, "skipped": skipped, "failed": failed, "total": total}
    finally:
        db.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="Max rows to process")
    p.add_argument("--dry-run", action="store_true", help="Count only, no API calls")
    p.add_argument("--batch-size", type=int, default=50, help="Commit batch size")
    args = p.parse_args()
    result = backfill(limit=args.limit, dry_run=args.dry_run, batch_size=args.batch_size)
    print(result)


if __name__ == "__main__":
    main()
