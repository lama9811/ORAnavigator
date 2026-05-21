#!/usr/bin/env python3
"""
Mine the `failed_queries` DB table for real chatbot misses and emit them as
promptfoo case CANDIDATES for human curation.

Output: eval/cases/_mined_candidates.yaml  (a starting point — NOT auto-used;
the curator reviews it and moves good cases into cases/mined_failures.yaml).

If the DB is unreachable (Cloud SQL not authorized for this IP, etc.) the
script prints a warning and exits 0 — the harness still works without it.

Usage:
  python mine_failed_queries.py            # writes _mined_candidates.yaml
  python mine_failed_queries.py --limit 50
"""
import argparse
import re
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent.parent.parent / "backend"
OUT_PATH = EVAL_DIR / "cases" / "_mined_candidates.yaml"


def dedupe(queries):
    """Drop near-duplicate queries (case- and whitespace-insensitive)."""
    seen = set()
    result = []
    for q in queries:
        key = re.sub(r"\s+", " ", (q or "").strip().lower())
        if key and key not in seen:
            seen.add(key)
            result.append(q.strip())
    return result


def format_candidates(queries):
    """Render deduped queries as a YAML stub the curator fills in."""
    if not queries:
        return ("# No failed queries mined. The DB was empty or unreachable.\n"
                "# The harness does not depend on this file.\n")
    lines = ["# Mined from the failed_queries table. CANDIDATES ONLY —",
             "# review each, fill kb_context + assert, then move good ones",
             "# into cases/mined_failures.yaml.\n"]
    for q in queries:
        safe = q.replace('"', "'")
        lines.append(f'- description: "Mined: {safe[:60]}"')
        lines.append("  vars:")
        lines.append(f'    prompt: "{safe}"')
        lines.append('    kb_context: "TODO — fill from KB, or use the abstention sentinel"')
        lines.append("  assert:")
        lines.append('    - type: llm-rubric')
        lines.append('      value: "TODO — describe the correct, grounded answer"')
        lines.append("")
    return "\n".join(lines)


def fetch_failed_queries(limit):
    """Query the failed_queries table. Returns a list of query strings, or []."""
    sys.path.insert(0, str(BACKEND_DIR))
    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND_DIR / ".env")
        from db import SessionLocal  # backend/db.py
        from models import FailedQuery  # backend/models.py
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: cannot import backend DB modules: {e}")
        return []
    try:
        db = SessionLocal()
        try:
            rows = (db.query(FailedQuery.user_query)
                    .order_by(FailedQuery.created_at.desc())
                    .limit(limit).all())
            return [r[0] for r in rows]
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: failed_queries DB unreachable ({e}). Skipping mining.")
        return []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args(argv)

    queries = dedupe(fetch_failed_queries(args.limit))
    OUT_PATH.write_text(format_candidates(queries))
    print(f"Wrote {len(queries)} candidate(s) to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
