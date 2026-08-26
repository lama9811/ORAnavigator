#!/usr/bin/env python3
"""Repair the mirrored-screenshot URLs already stored in the KB snapshot.

Companion to the public_url() fix in mirror_etraining_images.py. Those URLs were
written with the object name unescaped, so every screenshot whose original Rise
filename contained a space resolves to a name that does not exist and 404s. The
frontend renders a broken image under the chat answer; nothing logs an error,
because the backend only ever passes the string through
(forms_catalog.images_for_titles -> vertex_agent -> the browser).

Measured 2026-08-07 before the fix: 78 of 267 stored URLs dead, 189 fine. The
dead ones are exactly the genuine Banner captures ("... Screenshot 2024-11-11
133421.png"); the survivors are largely Rise's stock photos, whose plain
filenames need no escaping. So the feature looked like it worked.

Rewrites `images[].url` in:
  backend/kb_structured/_etraining_lessons.json
  backend/kb_structured/trainings/e_training/*.json

Usage:
  python3 scripts/fix_etraining_image_urls.py --dry-run   # report, write nothing
  python3 scripts/fix_etraining_image_urls.py --verify    # HTTP-check every URL
  python3 scripts/fix_etraining_image_urls.py             # apply
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(REPO, "backend", "kb_structured")
LESSONS_JSON = os.path.join(KB, "_etraining_lessons.json")
LESSON_DOCS = os.path.join(KB, "trainings", "e_training", "*.json")

# THE SERVED COPY, and the one this script did not touch until 2026-08-26.
# forms_catalog.images_for_titles() -- the whole chat-answer screenshot path --
# reads the manifest and nothing else, so repairing the two JSON copies above
# fixed every file except the one a PI actually sees. Measured that day: 73 of
# 262 manifest URLs still dead, across the same 25 lessons whose per-doc files
# had been correctly repaired a fortnight earlier.
MANIFEST = os.path.join(KB, "_all_documents.jsonl")
PREFIX = "https://storage.googleapis.com/"


_cache: dict[str, str] = {}


def repaired(url: str) -> str:
    """Return a URL that actually resolves, or the original if it already does.

    Deliberately EMPIRICAL rather than string-rewriting. A purely textual fix
    cannot be both correct and idempotent here: the broken form `%2520` is
    itself a syntactically valid escape sequence, so "escape any % that isn't
    already an escape" leaves every broken URL untouched (measured: 0 of 78
    fixed), while "unquote then re-quote" mangles the correct form `%252520`
    back down to the broken one. The bucket is the only authority on which
    spelling names a real object, so ask it.
    """
    if not url.startswith(PREFIX) or url in _cache:
        return _cache.get(url, url)
    candidate = url
    if not head_ok(url):
        base, name = url.rsplit("/", 1)
        alt = base + "/" + urllib.parse.quote(name, safe="")
        if alt != url and head_ok(alt):
            candidate = alt
    _cache[url] = candidate
    return candidate


def head_ok(url: str) -> bool:
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=25)
        return True
    except Exception:
        return False


def walk(obj, fn):
    """Apply fn to every images[].url found anywhere in a nested structure."""
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "images" and isinstance(v, list):
                for img in v:
                    if isinstance(img, dict) and isinstance(img.get("url"), str):
                        new = fn(img["url"])
                        if new != img["url"]:
                            img["url"] = new
                            n += 1
            else:
                n += walk(v, fn)
    elif isinstance(obj, list):
        for v in obj:
            n += walk(v, fn)
    return n


def collect(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "images" and isinstance(v, list):
                for img in v:
                    if isinstance(img, dict) and isinstance(img.get("url"), str):
                        out.append(img["url"])
            else:
                collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect(v, out)


def repair_manifest(dry_run: bool, before: list, after: list) -> int:
    """Rewrite images[].url in the JSONL manifest, IN PLACE and by string.

    Two properties this needs that the json.load/json.dump path above does not,
    both because the manifest is a different kind of file:

    It is JSONL, one document per line, and CLAUDE.md records that a whole-file
    manifest rewrite is how 54 rows from another generator were silently lost.
    So this never rebuilds the file from parsed objects -- it copies every line
    through untouched and substitutes only the URL strings it has proven need
    substituting. A row with no repair is byte-identical afterwards, which is
    also what keeps the diff readable enough to review.

    The substitution carries the surrounding quotes (`"<url>"`) so it cannot
    match a URL that merely has another as a prefix, and a mirrored URL contains
    no character JSON escapes, so a literal replace on the raw line is exact.
    """
    if not os.path.exists(MANIFEST):
        print(f"  SKIP {os.path.basename(MANIFEST)}: not found")
        return 0

    with open(MANIFEST, encoding="utf-8") as f:
        lines = f.readlines()

    changed, out = 0, []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        try:
            doc = json.loads(stripped)
        except json.JSONDecodeError:
            out.append(line)
            continue
        urls = [im.get("url") for im in (doc.get("images") or [])
                if isinstance(im, dict) and isinstance(im.get("url"), str)]
        collect(doc, before)
        for url in urls:
            new = repaired(url)
            if new != url:
                line = line.replace(f'"{url}"', f'"{new}"')
                changed += 1
        out.append(line)
        collect(json.loads(line.strip()), after)

    if changed:
        print(f"  {changed:3d} fixed  {os.path.relpath(MANIFEST, REPO)}")
        if not dry_run:
            with open(MANIFEST, "w", encoding="utf-8") as f:
                f.writelines(out)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="HTTP-check URLs before/after")
    args = ap.parse_args()

    targets = [LESSONS_JSON] + sorted(glob.glob(LESSON_DOCS))
    total_changed = 0
    before, after = [], []

    for path in targets:
        try:
            data = json.load(open(path))
        except Exception as e:
            print(f"  SKIP {os.path.basename(path)}: {e}")
            continue
        collect(data, before)
        changed = walk(data, repaired)
        collect(data, after)
        if changed:
            total_changed += changed
            print(f"  {changed:3d} fixed  {os.path.relpath(path, REPO)}")
            if not args.dry_run:
                with open(path, "w") as f:
                    json.dump(data, f, indent=1)

    total_changed += repair_manifest(args.dry_run, before, after)

    print(f"\nfiles scanned: {len(targets) + 1}   urls rewritten: {total_changed}")

    if args.verify:
        uniq_before = sorted(set(before))
        uniq_after = sorted(set(after))
        with ThreadPoolExecutor(max_workers=12) as ex:
            ok_b = sum(ex.map(head_ok, uniq_before))
            ok_a = sum(ex.map(head_ok, uniq_after))
        print(f"verify BEFORE: {ok_b}/{len(uniq_before)} resolve")
        print(f"verify AFTER : {ok_a}/{len(uniq_after)} resolve")
        if ok_a < len(uniq_after):
            print("WARNING: some URLs still do not resolve")
            return 1

    if args.dry_run:
        print("[dry-run] nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
