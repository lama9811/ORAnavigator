#!/usr/bin/env python3
"""Point each staff document at the person's PROFILE page, not the directory.

Every staff_* document carried source_url = procedure_url = the staff DIRECTORY
page. The directory prints only name, title, office, phone and email -- but the
documents are built almost entirely from the profile pages, which is where the
bios, degrees, certifications and publication lists live. Measured: 22,708
characters of profile-page content watched by nothing.

The consequence was not theoretical. The scraper fingerprints the pages a
document names, so a rewritten bio or a new credential produced no hash change
and no proposal, forever. And a citation could never deep-link to a person.

This repoints both URLs at the profile page and keeps the directory as
`directory_url`, so the directory-level fields still have a recorded source.

Matching is by NAME against the crawled profile pages, because the URLs in the
documents are precisely what is wrong -- matching on them would find nothing.

Usage:
  python3 scripts/repoint_staff_profile_urls.py --index <crawl _index.json> --dry-run
  python3 scripts/repoint_staff_profile_urls.py --index <crawl _index.json>
  python3 scripts/repoint_staff_profile_urls.py --index <...> --push
  python3 scripts/repoint_staff_profile_urls.py --revert
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(REPO, "backend", "kb_structured")
MANIFEST = os.path.join(KB, "_all_documents.jsonl")
BACKUP = os.path.join(KB, "_staff_url_backup.json")
DIRECTORY = "https://www.morgan.edu/office-of-research-administration/about/staff-directory"


def name_tokens(s: str) -> set:
    s = re.sub(r"\b(dr|mr|ms|mrs|phd|md|mba|ccep|cra|jr|sr|ii|iii)\b", " ", s.lower())
    return {t for t in re.split(r"[^a-z]+", s) if len(t) > 2}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=False, help="crawl _index.json with profile pages")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(MANIFEST)]
    staff = [r for r in rows if r["doc_id"].startswith("staff_")]

    if args.revert:
        if not os.path.exists(BACKUP):
            print("no backup")
            return 1
        b = json.load(open(BACKUP))
        for doc_id, saved in b["docs"].items():
            p = os.path.join(KB, saved["file_path"])
            if os.path.exists(p):
                d = json.load(open(p))
                d["source_url"] = saved["source_url"]
                d["procedure_url"] = saved["procedure_url"]
                d.pop("directory_url", None)
                json.dump(d, open(p, "w"), indent=1)
        with open(MANIFEST, "w") as f:
            for row in b["manifest"]:
                f.write(json.dumps(row) + "\n")
        print(f"reverted {len(b['docs'])} staff documents")
        return 0

    if not args.index:
        print("--index is required (the crawl's _index.json)")
        return 1
    pages = json.load(open(args.index))["pages"]
    profiles = [p for p in pages if "/staff-directory/" in p["url"]]
    print(f"staff documents: {len(staff)}   crawled profile pages: {len(profiles)}")

    backup = {"manifest": rows, "docs": {}}
    matched, unmatched, dupes = [], [], []
    used: dict[str, str] = {}

    for r in staff:
        want = name_tokens(r["title"] + " " + r.get("display_label", ""))
        scored = []
        for p in profiles:
            slug_tokens = set(re.split(r"[^a-z]+", p["url"].rsplit("/", 1)[-1].lower()))
            overlap = len(want & slug_tokens)
            if overlap:
                scored.append((overlap, p))
        scored.sort(key=lambda t: -t[0])
        best, score = (scored[0][1], scored[0][0]) if scored else (None, 0)
        # Two matching tokens is the safe bar, but a SHORT surname is dropped by
        # the >2-char token filter, so "Deshun Li" can only ever score 1. Accept a
        # single-token match when exactly one profile matches at all -- that is
        # unambiguous, whereas two candidates at score 1 is a coin flip.
        unique_single = score == 1 and sum(1 for s, _ in scored if s == score) == 1
        if not best or (score < 2 and not unique_single):
            unmatched.append(r["doc_id"])
            continue
        # /farin-kamangar and /farin-kamangar-md-phd are byte-identical: two URLs,
        # one page. Keep the shorter (canonical) one rather than recording both.
        url = best["url"]
        if url in used.values():
            dupes.append((r["doc_id"], url))
        used[r["doc_id"]] = url
        matched.append((r, url))

    for doc_id, url in sorted(used.items()):
        print(f"  {doc_id:32s} -> {url.replace('https://www.morgan.edu', '')}")
    if unmatched:
        print(f"\nUNMATCHED ({len(unmatched)}): {unmatched}")
    if dupes:
        print(f"duplicate targets: {dupes}")

    if args.dry_run:
        print("\n[dry-run] nothing written")
        return 0

    for r, url in matched:
        p = os.path.join(KB, r["file_path"])
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        backup["docs"][r["doc_id"]] = {
            "file_path": r["file_path"],
            "source_url": d.get("source_url", ""),
            "procedure_url": d.get("procedure_url", ""),
        }
        d["source_url"] = url
        d["procedure_url"] = url
        d["directory_url"] = DIRECTORY
        json.dump(d, open(p, "w"), indent=1)
        r["source_url"] = url
        r["procedure_url"] = url

    json.dump(backup, open(BACKUP, "w"))
    with open(MANIFEST, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"\nrepointed {len(matched)} staff documents")

    if args.push:
        # Metadata-only change. update_document rewrites content and merges
        # struct_data; source_url is not one of the seven fields the seeder
        # wrote, so the datastore has never carried it -- the citation URL map
        # reads the snapshot with a live overlay. Nothing to push.
        print("no datastore write needed: source_url lives in the snapshot, "
              "not in the datastore's struct_data (the seeder wrote seven "
              "fields and source_url is not among them).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
