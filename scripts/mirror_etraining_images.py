#!/usr/bin/env python3
"""Mirror the eTraining screenshots from Articulate into our own GCS bucket.

Why mirror rather than hot-link
-------------------------------
The Articulate URL embeds the Rise course id, so republishing a module changes
it. That has already happened once: three modules were republished and the KB
kept pointing at the superseded copies. Hot-linked images would have gone dead
in answers with no warning. Mirrored images survive it; only NEW screenshots
need a re-run.

Public-read is deliberate
-------------------------
These images are already served with no authentication from Articulate's CDN,
so a public bucket adds no exposure -- and it keeps image traffic off a backend
that runs a single uvicorn worker with CPU throttled outside requests. Do not
"harden" this to private without re-reading that reasoning; the alternative is
proxying every screenshot through the chat path.

Usage
-----
    python3 scripts/mirror_etraining_images.py --dry-run
    python3 scripts/mirror_etraining_images.py
    python3 scripts/mirror_etraining_images.py --bucket my-other-bucket

Idempotent: an object already in the bucket is skipped, so re-running after a
module update uploads only what is new.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "backend" / "kb_structured" / "_etraining_images.json"
BUCKET = "oranavigator-kb-assets"
CDN = "https://articulateusercontent.com/"
UA = "Mozilla/5.0 (compatible; ORANavigatorKB/1.0; +https://ora.inavigator.ai)"


def public_url(bucket: str, object_name: str) -> str:
    """Browser-fetchable URL for a mirrored object.

    The object NAME legitimately contains percent signs: Rise's own keys arrive
    already URL-encoded ("...Screenshot%25202024-11-11..."), and object_name()
    stores that last segment verbatim, so the blob really is called `%2520`.
    Emitting it raw produces a URL the server decodes back to `%20`, which names
    no object -- a 404 on every screenshot whose original filename had a space,
    i.e. most of the genuine Banner captures, while the stock photos (plain
    names) keep working. Quote with safe="/" so the path separators survive and
    only the `%` is escaped. Measured 2026-08-07: 78 of 267 stored URLs were
    dead this way, and re-encoding fixed all 78.
    """
    return f"https://storage.googleapis.com/{bucket}/{urllib.parse.quote(object_name, safe='/')}"


def object_name(module: str, key: str) -> str:
    """etraining/{module}/{filename} — the Rise key's last segment is unique."""
    return f"etraining/{module}/{key.rsplit('/', 1)[-1]}"


def load_inventory() -> dict:
    if not INVENTORY.exists():
        sys.exit(f"No inventory at {INVENTORY}. Run the extraction first.")
    return json.loads(INVENTORY.read_text())


def fetch(key: str) -> bytes:
    req = urllib.request.Request(CDN + key, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--bucket", default=BUCKET)
    args = ap.parse_args()

    inv = load_inventory()
    todo = []
    for module, m in inv["modules"].items():
        for img in m["images"]:
            todo.append((module, img["key"]))
    unique = {object_name(mod, k): (mod, k) for mod, k in todo}

    print(f"{len(todo)} image references, {len(unique)} unique objects")

    if args.dry_run:
        for name in list(unique)[:6]:
            print(f"  would upload  {name}")
        print(f"  … {max(0, len(unique) - 6)} more")
        print(f"\nDestination: gs://{args.bucket}/etraining/…")
        print("Nothing was uploaded. Drop --dry-run to apply.")
        return 0

    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(args.bucket)
    if not bucket.exists():
        sys.exit(f"Bucket {args.bucket} does not exist. Create it first:\n"
                 f"  gcloud storage buckets create gs://{args.bucket} "
                 f"--location=us-central1 --uniform-bucket-level-access")

    existing = {b.name for b in client.list_blobs(bucket, prefix="etraining/")}
    print(f"already in the bucket: {len(existing)}")

    def upload(item):
        name, (module, key) = item
        if name in existing:
            return ("skipped", name)
        try:
            data = fetch(key)
            blob = bucket.blob(name)
            ctype = "image/png" if key.lower().endswith(".png") else "image/jpeg"
            blob.upload_from_string(data, content_type=ctype)
            return ("uploaded", name)
        except Exception as e:
            return (f"FAILED {str(e)[:60]}", name)

    counts = {"uploaded": 0, "skipped": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for status, name in pool.map(upload, unique.items()):
            if status == "uploaded":
                counts["uploaded"] += 1
            elif status == "skipped":
                counts["skipped"] += 1
            else:
                counts["failed"] += 1
                print(f"  {status}  {name}")

    print(f"\nuploaded {counts['uploaded']}, skipped {counts['skipped']}, "
          f"failed {counts['failed']}")
    print(f"public base: {public_url(args.bucket, 'etraining/…')}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
