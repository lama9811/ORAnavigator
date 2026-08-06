#!/usr/bin/env python3
"""Push the audit gap fixes from the committed snapshot to the LIVE datastore.

Why this is a separate step
---------------------------
There are two copies of the KB metadata and nothing syncs them. The live Vertex
AI Search datastore (`oranavigator-kb-v8`) is what the chatbot searches;
`backend/kb_structured/` is a static snapshot read at import time that powers the
Forms catalog, the citation URL map, the tree and the scraper's URL index.

`scripts/apply_kb_gap_fixes.py` writes the SNAPSHOT. Until this script runs, the
chatbot still answers from the old, wrong content — the SAM date still reads as
expired, the IRB calendar is still a year stale. Fixing the snapshot alone
changes nothing a PI would notice in chat.

CONTENT ONLY, and why
---------------------
This pushes `content` and leaves live `struct_data` exactly as it is.

Measured 2026-08-06: every live document carries the SAME seven struct fields
(`category`, `doc_id`, `file_path`, `playwright_verified`, `source_file`,
`subcategory`, `title`) — the seeder wrote those and stopped. The snapshot's rich
fields (`key_facts`, `staff_title`, the IRB meeting schedule, …) have NEVER been
in the datastore. Pushing them would give 30 of 469 documents a shape no other
document has, which is a schema change, not a content fix.

It is also unnecessary: Vertex indexes `content`, the chat path answers from
`content`, and every correction in this change set is present in `content` —
Moncrieffe's title, the 2026-2027 IRB dates, the SAM date, the MTDC rule. So the
minimal write is the correct one.

Consequence worth knowing: the snapshot and the datastore stay divergent on
struct_data. That divergence predates this change and is documented in CLAUDE.md
("Two copies of the KB metadata, and nothing syncs them").

The fail-closed rule from `datastore_manager.update_document()` is kept: a
NotFound is a legitimate create, any OTHER read failure refuses to write, because
a failed save is recoverable and a wiped document is not.

Usage
-----
    python3 scripts/push_kb_gap_fixes.py --dry-run
    python3 scripts/push_kb_gap_fixes.py
    python3 scripts/push_kb_gap_fixes.py --only trainings_spark,ora_history

Afterwards, clear the chat answer cache (admin -> Sync All, or
`POST /api/admin/knowledge-base/sync-all`). The cache is keyed on the question,
not the content, so a cached answer would keep replaying the OLD facts — which is
exactly the failure this whole change set exists to fix.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "backend" / "kb_structured"
MANIFEST = KB / "_all_documents.jsonl"
BACKUP = KB / "_gap_fix_backup.json"

sys.path.insert(0, str(ROOT / "backend"))

# struct_data carries metadata only — never the body. `content` is what Vertex
# indexes and is passed separately as raw_bytes.
_NOT_STRUCT = {"content", "file_path"}


def snapshot_docs() -> dict[str, dict]:
    rows = [json.loads(l) for l in MANIFEST.read_text().splitlines() if l.strip()]
    out = {}
    for r in rows:
        fp = r.get("file_path")
        if not fp:
            continue
        p = KB / fp
        if p.exists():
            out[r["doc_id"]] = json.loads(p.read_text())
    return out


def changed_doc_ids() -> list[str]:
    """Exactly what apply_kb_gap_fixes.py touched — read from its backup."""
    if not BACKUP.exists():
        sys.exit(
            f"No backup at {BACKUP.relative_to(ROOT)}.\n"
            "Run scripts/apply_kb_gap_fixes.py first — this script pushes what that wrote."
        )
    b = json.loads(BACKUP.read_text())
    return sorted(set(b.get("documents", {})) | set(b.get("manifest_added", [])))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated doc_ids")
    args = ap.parse_args()

    docs = snapshot_docs()
    targets = changed_doc_ids()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        targets = [t for t in targets if t in wanted]

    print(f"{len(targets)} documents to push\n")
    if args.dry_run:
        for doc_id in targets:
            d = docs.get(doc_id)
            if not d:
                print(f"  MISSING from snapshot: {doc_id}")
                continue
            print(f"  {doc_id:<48} {len(d.get('content','')):>6} chars, "
                  f"struct_data preserved")
        print("\nDRY RUN — nothing was written. Drop --dry-run to push.")
        return 0

    from google.api_core.exceptions import NotFound
    from google.cloud import discoveryengine_v1 as discoveryengine
    from google.protobuf.struct_pb2 import Struct
    from datastore_manager import BRANCH, _get_doc_client, invalidate_content_cache

    client = _get_doc_client()
    ok = failed = 0
    # Snapshot what the datastore holds RIGHT NOW, before touching it. The
    # snapshot backup covers the repo copy; this covers production, so a bad
    # push is one restore away rather than a re-derivation from git history.
    live_backup: dict[str, str] = {}

    for doc_id in targets:
        d = docs.get(doc_id)
        if not d:
            print(f"  MISSING  {doc_id}: not in the snapshot")
            failed += 1
            continue

        name = f"{BRANCH}/documents/{doc_id}"
        content = d.get("content", "")

        # Fail closed on anything that is not a clean "does not exist".
        created = False
        try:
            existing = client.get_document(name=name)
            live = dict(existing.struct_data) if existing.struct_data else {}
            if existing.content and existing.content.raw_bytes:
                live_backup[doc_id] = existing.content.raw_bytes.decode("utf-8", "replace")
        except NotFound:
            live, created = {}, True
        except Exception as e:
            print(f"  REFUSED  {doc_id}: could not read existing metadata: {e}")
            failed += 1
            continue

        # Preserve live struct_data verbatim — see the module docstring. The one
        # exception is a genuinely NEW document, which has no live metadata at
        # all and would otherwise land unplaced.
        if created:
            keep = {k: d[k] for k in (
                "doc_id", "title", "category", "subcategory",
                "playwright_verified", "file_path") if k in d}
            keep["source_file"] = d.get("file_path", "")
        else:
            keep = dict(live)
        keep.pop("content", None)

        struct = Struct()
        struct.update(keep)
        doc = discoveryengine.Document(
            name=name,
            struct_data=struct,
            content=discoveryengine.Document.Content(
                raw_bytes=content.encode("utf-8"), mime_type="text/plain"),
        )
        try:
            client.update_document(
                request=discoveryengine.UpdateDocumentRequest(document=doc, allow_missing=True))
            print(f"  {'CREATED' if created else 'updated'}  {doc_id}  ({len(content)} chars)")
            ok += 1
        except Exception as e:
            print(f"  FAILED   {doc_id}: {e}")
            failed += 1

    invalidate_content_cache()
    if live_backup:
        out = KB / "_gap_fix_live_backup.json"
        out.write_text(json.dumps(live_backup, ensure_ascii=False, indent=2) + "\n")
        print(f"\nprevious LIVE content saved to {out.relative_to(ROOT)} ({len(live_backup)} docs)")
    print(f"\npushed {ok}, failed {failed}")
    print("\nNow clear the chat answer cache (admin -> Sync All), or cached answers")
    print("will keep replaying the OLD facts this change set just corrected.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
