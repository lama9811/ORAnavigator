#!/usr/bin/env python3
"""
One-time backfill: give every live document a `kb_path` in its struct_data.

Placement currently lives only in the bundled _manifest.json, which is a frozen
2026-05-18 snapshot. After this runs, each document carries its own position in
the nav tree and the manifest is demoted to supplying shape + curated titles.
That means a placement set in the admin UI can never be silently overwritten by
regenerating the manifest.

The path is DERIVED, not invented — it is the directory of the manifest entry's
existing file_path:

    pre_award/budget_development/preaward_budget.json -> pre_award/budget_development
    pre_award/pre_award_overview.json                 -> pre_award

Safety:
  * --dry-run is the DEFAULT. Writing requires --apply.
  * Every derived path is checked against the real node set before any write.
  * Documents already carrying the correct kb_path are skipped, so this is
    idempotent and safe to re-run after a partial failure.
  * Fully recomputable — the manifest is committed to the repo.

Usage:
    python3 scripts/backfill_kb_path.py                 # preview
    python3 scripts/backfill_kb_path.py --apply         # write
    python3 scripts/backfill_kb_path.py --apply --only pre_award
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import kb_tree                      # noqa: E402
import datastore_manager as dm      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write (default is dry-run)")
    ap.add_argument("--only", default="", help="restrict to paths under this prefix, e.g. pre_award")
    args = ap.parse_args()

    # datastore_manager's default DATASTORE_ID still points at kb-v7 while prod
    # deploys set kb-v8. Backfilling 382 documents into the wrong datastore
    # would be quiet and confusing, so require the target to be explicit.
    if not os.getenv("VERTEX_AI_DATASTORE_ID"):
        print("ABORT: VERTEX_AI_DATASTORE_ID is not set, and the module default "
              "points at the old kb-v7 datastore.\n"
              "Set it explicitly, e.g.:\n"
              "  export VERTEX_AI_DATASTORE_ID=projects/infra-vertex-494621-v1/locations/us"
              "/collections/default_collection/dataStores/oranavigator-kb-v8")
        return 2
    print(f"Target datastore: {dm.DATASTORE_ID.split('/')[-1]}")

    placements = kb_tree.manifest_placements()
    valid = kb_tree.node_paths()

    # Refuse to touch anything if the manifest itself is inconsistent.
    unknown = {d: p for d, p in placements.items() if p and p not in valid}
    if unknown:
        print(f"ABORT: {len(unknown)} manifest entries point at nodes that do not exist:")
        for d, p in list(unknown.items())[:10]:
            print(f"  {d} -> {p}")
        return 2

    print(f"Manifest: {len(placements)} documents across {len(valid)} nodes")

    print("Reading live datastore...")
    live = {d["id"]: d for d in dm.list_datastore_documents()}
    print(f"Datastore: {len(live)} documents\n")

    to_write, already, missing_live, no_placement = [], [], [], []

    for doc_id, path in placements.items():
        if args.only and not path.startswith(args.only):
            continue
        if doc_id not in live:
            missing_live.append(doc_id)
            continue
        if not path:
            no_placement.append(doc_id)
            continue
        if (live[doc_id].get("kb_path") or "") == path:
            already.append(doc_id)
        else:
            to_write.append((doc_id, path))

    # Only meaningful on a full run; a --only pass sees most of the KB as "extra".
    in_live_not_manifest = [] if args.only else [d for d in live if d not in placements]

    print(f"  to write ............ {len(to_write)}")
    print(f"  already correct ..... {len(already)}")
    if missing_live:
        print(f"  in manifest, not in datastore ... {len(missing_live)}  {missing_live[:5]}")
    if no_placement:
        print(f"  no derivable path ............... {len(no_placement)}  {no_placement[:5]}")
    if in_live_not_manifest:
        print(f"  in datastore, not in manifest ... {len(in_live_not_manifest)}"
              f"  {in_live_not_manifest[:5]}   (these become 'Unfiled')")

    if not to_write:
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print("\nDRY RUN — no writes. Sample of what would change:")
        for doc_id, path in to_write[:15]:
            print(f"  {doc_id:<50} -> {path}")
        if len(to_write) > 15:
            print(f"  ... and {len(to_write) - 15} more")
        print("\nRe-run with --apply to write.")
        return 0

    print(f"\nWriting {len(to_write)} placements...")
    ok = failed = 0
    for i, (doc_id, path) in enumerate(to_write, 1):
        result = dm.update_placement(doc_id, path)
        if result["success"]:
            ok += 1
        else:
            failed += 1
            print(f"  FAILED {doc_id}: {result['message']}")
        if i % 50 == 0:
            print(f"  {i}/{len(to_write)}...")

    print(f"\nDone: {ok} written, {failed} failed.")
    if failed:
        print("Re-run to retry only the failures (already-correct documents are skipped).")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
