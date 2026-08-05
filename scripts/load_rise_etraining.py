#!/usr/bin/env python3
"""Load the extracted Articulate Rise eTraining content into the live KB.

Why this exists
---------------
ORA's eight eTraining modules teach Morgan's own grant-spending procedures and
that material exists nowhere else in the knowledge base. The KB carried only a
300-660 character description of each ("a module about travel exists"), so a PI
asking "can I claim per diem for a conference meal?" got a pointer, not an
answer.

Rise ships each whole course to the browser in one JSON payload -- the lesson
gating is a UI control, not a server-side one -- so the full text is reachable
in a single request per module. Two endpoints are needed because ORA's share
links come in two shapes:

    share.articulate.com/{id}            -> POST /api/instant-links/{id}/course
    rise.articulate.com/share/{id}       -> GET  /api/rise-runtime/boot/share/{id}

This script does NOT fetch. It writes content already extracted, reviewed and
committed to backend/kb_structured/_etraining_modules.json, so the network step
and the write step stay separate and each can be inspected on its own.

Safety
------
* Content-only. update_document preserves struct_data, so placement,
  procedure_url and titles are untouched.
* Every previous body is saved to _etraining_kb_backup.json first, and
  --revert restores from it.
* --dry-run prints what would change and writes nothing.

Usage
-----
    python3 scripts/load_rise_etraining.py --dry-run
    python3 scripts/load_rise_etraining.py
    python3 scripts/load_rise_etraining.py --revert

Requires VERTEX_AI_DATASTORE_ID and application-default credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Committed alongside the rest of the KB data so the load is reproducible and
# the extracted content is reviewable in a diff rather than living in /tmp.
STAGE = Path(__file__).resolve().parent.parent / "backend" / "kb_structured" / "_etraining_modules.json"
BACKUP = Path(__file__).resolve().parent.parent / "backend" / "kb_structured" / "_etraining_kb_backup.json"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


def _require_env() -> None:
    if not os.getenv("VERTEX_AI_DATASTORE_ID"):
        sys.exit(
            "VERTEX_AI_DATASTORE_ID is not set.\n"
            "  export VERTEX_AI_DATASTORE_ID="
            "projects/infra-vertex-494621-v1/locations/us/collections/"
            "default_collection/dataStores/oranavigator-kb-v8"
        )


def load_staged() -> list:
    if not STAGE.exists():
        sys.exit(f"No staged content at {STAGE}. Re-run the extraction first.")
    data = json.loads(STAGE.read_text())
    return [
        {"doc_id": m["doc_id"], "content": m["content"], "old_chars": m["previous_kb_chars"]}
        for m in data["modules"]
    ]


def do_revert() -> int:
    from datastore_manager import update_document

    if not BACKUP.exists():
        sys.exit(f"No backup at {BACKUP} — cannot revert.")
    backup = json.loads(BACKUP.read_text())
    failed = 0
    for doc_id, content in backup.items():
        result = update_document(doc_id, (content or "").encode("utf-8"))
        ok = result.get("success")
        failed += 0 if ok else 1
        print(f"  {'restored' if ok else 'FAILED  '} {len(content or ''):>6} chars  {doc_id}")
    print(f"\nReverted {len(backup) - failed}/{len(backup)} documents.")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    ap.add_argument("--revert", action="store_true", help="restore the pre-load content")
    args = ap.parse_args()

    _require_env()

    if args.revert:
        return do_revert()

    docs = load_staged()

    if args.dry_run:
        print(f"Would update {len(docs)} documents (content only):\n")
        for d in docs:
            print(f"  {d['old_chars']:>4} -> {len(d['content']):>6,} chars  {d['doc_id']}")
        print(f"\nTotal: {sum(len(d['content']) for d in docs):,} characters")
        print("Nothing was written. Drop --dry-run to apply.")
        return 0

    from datastore_manager import get_document_content, update_document

    # Re-read and back up NOW rather than trusting a file written earlier: a
    # document may have been edited by hand in the meantime, and that edit is
    # what a revert has to restore.
    backup = {d["doc_id"]: (get_document_content(d["doc_id"]) or "") for d in docs}
    BACKUP.write_text(json.dumps(backup, indent=2))
    print(f"Backed up {len(backup)} documents to {BACKUP}\n")

    failed = 0
    for d in docs:
        result = update_document(d["doc_id"], d["content"].encode("utf-8"))
        if result.get("success"):
            print(f"  wrote {len(d['content']):>6,} chars  {d['doc_id']}")
        else:
            failed += 1
            print(f"  FAILED {d['doc_id']}: {result.get('message')}")

    print(f"\n{len(docs) - failed} written, {failed} failed.")
    if failed:
        print("Some writes failed. `--revert` restores everything from the backup.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
