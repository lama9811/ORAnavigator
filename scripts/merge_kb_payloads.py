#!/usr/bin/env python3
"""Merge generated document payloads into the committed KB snapshot.

Several generators (site files, video transcripts, module attachments, image
transcriptions) each produce a payload of new documents. They must NOT write
`_all_documents.jsonl` themselves: the manifest is a single file and concurrent
writers silently clobber one another, losing whole batches with no error. This
is the one place it is rewritten.

A payload is `{"documents": [...]}` where each document carries at minimum
doc_id, title, category, subcategory, kb_path, source_url, procedure_url and
content.

Usage:
  python3 scripts/merge_kb_payloads.py --dry-run  a.json b.json
  python3 scripts/merge_kb_payloads.py            a.json b.json
  python3 scripts/merge_kb_payloads.py --push     a.json b.json   # + datastore
  python3 scripts/merge_kb_payloads.py --revert
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(REPO, "backend", "kb_structured")
MANIFEST = os.path.join(KB, "_all_documents.jsonl")
BACKUP = os.path.join(KB, "_merge_payloads_backup.json")

REQUIRED = ("doc_id", "title", "content")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("payloads", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    if args.revert:
        if not os.path.exists(BACKUP):
            print("no backup")
            return 1
        b = json.load(open(BACKUP))
        for rel in b["created_files"]:
            p = os.path.join(KB, rel)
            if os.path.exists(p):
                os.remove(p)
        with open(MANIFEST, "w") as f:
            for row in b["manifest"]:
                f.write(json.dumps(row) + "\n")
        print(f"reverted {len(b['created_files'])} files; manifest -> {len(b['manifest'])} rows")
        return 0

    if not args.payloads:
        print("no payloads given")
        return 1

    manifest = [json.loads(l) for l in open(MANIFEST)]
    known = {r["doc_id"] for r in manifest}

    incoming, rejected = [], []
    for path in args.payloads:
        if not os.path.exists(path):
            print(f"  MISSING payload: {path}")
            continue
        data = json.load(open(path))
        docs = data.get("documents") or []
        print(f"  {len(docs):4d} documents from {os.path.basename(path)}")
        for d in docs:
            missing = [k for k in REQUIRED if not (d.get(k) or "").strip()]
            if missing:
                rejected.append((d.get("doc_id", "?"), f"missing {','.join(missing)}"))
                continue
            if d["doc_id"] in known:
                # A duplicate doc_id would overwrite a real document with
                # whatever this payload happens to hold. Refuse; the generator
                # is wrong, not the KB.
                rejected.append((d["doc_id"], "doc_id already exists"))
                continue
            known.add(d["doc_id"])
            incoming.append(d)

    print(f"\naccepted: {len(incoming)}   rejected: {len(rejected)}")
    for did, why in rejected[:25]:
        print(f"  REJECT {did}: {why}")

    if args.dry_run:
        print("[dry-run] nothing written")
        return 0
    if not incoming:
        print("nothing to merge")
        return 0

    created, rows = [], []
    for d in incoming:
        kb_path = d.get("kb_path") or "unfiled"
        rel = os.path.join(kb_path.replace("/", os.sep), d["doc_id"] + ".json")
        created.append((rel, d))
        rows.append({
            "doc_id": d["doc_id"], "title": d["title"],
            "category": d.get("category") or "resources",
            "subcategory": d.get("subcategory") or "documents",
            "display_label": d.get("display_label") or d["title"],
            "source_url": d.get("source_url", ""),
            "procedure_url": d.get("procedure_url", ""),
            "playwright_verified": False,
            "file_path": rel.replace(os.sep, "/"),
        })

    json.dump({"manifest": manifest,
               "created_files": [r.replace(os.sep, "/") for r, _ in created]},
              open(BACKUP, "w"))

    for rel, d in created:
        p = os.path.join(KB, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump(d, open(p, "w"), indent=1)
    with open(MANIFEST, "w") as f:
        for row in manifest + rows:
            f.write(json.dumps(row) + "\n")
    print(f"wrote {len(created)} files; manifest {len(manifest)} -> {len(manifest) + len(rows)}")

    if args.push:
        sys.path.insert(0, os.path.join(REPO, "backend"))
        import datastore_manager as dm
        ok = exists = fail = 0
        for _, d in created:
            try:
                res = dm.create_kb_document(
                    doc_id=d["doc_id"], title=d["title"], content=d["content"],
                    kb_path=d.get("kb_path", ""), source_url=d.get("source_url", ""),
                    procedure_url=d.get("procedure_url", ""))
                if res.get("success"):
                    ok += 1
                elif "already exists" in (res.get("message") or ""):
                    exists += 1
                else:
                    fail += 1
                    print(f"  PUSH FAIL {d['doc_id']}: {res.get('message')}")
            except Exception as e:
                fail += 1
                print(f"  PUSH FAIL {d['doc_id']}: {str(e)[:140]}")
        print(f"datastore: created={ok} already_existed={exists} failed={fail}")
        print("REMEMBER: clear the chat answer cache (admin -> Sync All).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
