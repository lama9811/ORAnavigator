#!/usr/bin/env python3
"""Create KB documents for the ORA files that had none.

Until the crawler learned to resolve morgan.edu's bare-relative document links,
most of ORA's PDFs were invisible to every audit -- the KB recorded that a page
existed but nothing about the documents it hands people. This script closes that
for the files the 2026-08-07 crawl found with no matching KB document.

Measured: 277 distinct files linked from the site, 250 already covered, 27 not.
Three of the 27 are Google Forms (no document body to read -- their URLs are
recorded on the owning page's document instead); one is a duplicate carrying a
tracking query string. The remaining 23 extract to 430,195 characters, and they
are not trivia: MSU's Nondiscrimination Policy, the Sexual Harassment and
Prohibited Conduct Policy and Procedures, the Biological Safety Manual, Title
VI, and the three most recent D-RED decks.

LARGE FILES ARE SPLIT. `chunkingConfig` is create-time only on this datastore
and does not support text/plain, so every document is retrieved WHOLE. Loading
Prohibited Conduct Procedures as one 104k document would do to compliance
questions exactly what loading whole eTraining modules did to procurement ones:
a single question drags in the entire policy. Sections are ~4k characters, cut
on heading boundaries.

Usage:
  python3 scripts/load_ora_site_files.py --dry-run     # report, write nothing
  python3 scripts/load_ora_site_files.py               # write the snapshot
  python3 scripts/load_ora_site_files.py --push        # ...and the live datastore
  python3 scripts/load_ora_site_files.py --revert      # undo the snapshot write
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(REPO, "backend", "kb_structured")
MANIFEST = os.path.join(KB, "_all_documents.jsonl")
BACKUP = os.path.join(KB, "_site_files_backup.json")

TARGET_CHARS = 4000      # ~ the median size of an existing KB document
MIN_SPLIT = 6000         # below this, keep the file whole

# Which part of the tree a file belongs to, keyed by a fragment of its URL.
# Derived from the page that links it; kept explicit so placement is reviewable
# in a diff rather than guessed at load time.
PLACEMENT = [
    ("/Diversity and EEO/", "research_compliance", "diversity_and_eeo",
     "research_compliance/diversity_and_eeo"),
    ("/Animal Research/", "research_compliance", "animal_research",
     "research_compliance/animal_research"),
    ("/Title VI/", "research_compliance", "diversity_and_eeo",
     "research_compliance/diversity_and_eeo"),
    ("ResearchMisconduct", "research_compliance", "research_misconduct",
     "research_compliance/research_misconduct"),
    ("IRB-CITI", "research_compliance", "human_subjects_research",
     "research_compliance/human_subjects_research"),
    ("D-RED Seminars/", "trainings", "d_red_seminars", "trainings/monthly_d_red_seminars"),
    ("Faculty Development Seminars/", "trainings", "faculty_development",
     "trainings/new_faculty_development_seminars"),
    # NOT "trainings/training_presentations" -- that is not a node in the tree.
    # create_kb_document validates kb_path against kb_tree.node_paths() and
    # DROPS an unknown one rather than rejecting the write, so an invented path
    # produces a document that saves fine, answers questions fine, and lands in
    # the Unfiled bucket with no error anywhere. These two files are a subaward
    # deck and a funding-database guide; file them where they belong.
    ("ORA6 Subaward Process", "post_award", "post_award_subawards",
     "post_award/post_award_subawards"),
    ("Pivot-RP", "funding_sources", "external_databases",
     "funding_sources/external_databases"),
]
DEFAULT_PLACEMENT = ("resources", "documents", "resources")


def assert_placements_are_real_nodes() -> None:
    """Fail loudly at start-up rather than silently unfiling documents later.

    The cost of getting this wrong is invisible: the document is created, it is
    searchable, and only the admin tree shows anything is amiss -- as an Unfiled
    count nobody is watching.
    """
    try:
        sys.path.insert(0, os.path.join(REPO, "backend"))
        from kb_tree import node_paths
    except Exception as e:                      # tree unavailable -> skip, don't block
        print(f"  (could not validate kb_paths: {e})")
        return
    valid = set(node_paths())
    bad = [p for *_, p in PLACEMENT if p not in valid]
    if DEFAULT_PLACEMENT[2] not in valid:
        bad.append(DEFAULT_PLACEMENT[2])
    if bad:
        raise SystemExit(
            "PLACEMENT names paths that are not tree nodes: " + ", ".join(sorted(set(bad)))
            + "\nValid nodes come from kb_tree.node_paths(); an unknown path is "
              "dropped on write and the document lands in Unfiled.")

_HEADING = re.compile(
    r"^\s*(?:"
    r"(?:ARTICLE|SECTION|APPENDIX|ATTACHMENT|CHAPTER|PART)\b.*"
    r"|[IVXLC]+\.\s+\S.*"
    r"|\d+(?:\.\d+)*\.?\s+[A-Z].*"
    r"|[A-Z][A-Z \t&,'\-/()]{8,}"
    r")\s*$"
)


def slug(s: str) -> str:
    s = urllib.parse.unquote(s)
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", s, flags=re.I)
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return re.sub(r"_+", "_", s)[:70]


def title_of(url: str) -> str:
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    name = re.sub(r"\.[a-z0-9]{2,5}$", "", name, flags=re.I)
    name = re.sub(r"^Attachment[A-G]\.\s*", "", name)
    name = name.replace("_", " ").replace("%20", " ")
    name = re.sub(r"\s+", " ", name).strip(" -")
    if name.isupper():
        name = name.title()
    return name


def placement_for(url: str):
    for frag, cat, sub, path in PLACEMENT:
        if frag.lower() in urllib.parse.unquote(url).lower():
            return cat, sub, path
    return DEFAULT_PLACEMENT[0], DEFAULT_PLACEMENT[1], DEFAULT_PLACEMENT[2]


def split_sections(text: str) -> list[tuple[str, str]]:
    """[(section_label, body)] -- one entry when the file is small enough.

    Cuts only at lines that look like headings, so a section never begins
    mid-sentence. Falls back to accumulating paragraphs when a document has no
    recognisable headings at all (some scanned policies do not).
    """
    if len(text) < MIN_SPLIT:
        return [("", text)]

    lines = text.split("\n")
    chunks, cur, label, cur_label = [], [], "", ""
    size = 0
    for line in lines:
        is_head = bool(_HEADING.match(line)) and len(line.strip()) < 90
        if is_head and size >= TARGET_CHARS:
            chunks.append((cur_label, "\n".join(cur).strip()))
            cur, size, cur_label = [], 0, line.strip()
        elif is_head and not cur_label:
            cur_label = line.strip()
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append((cur_label, "\n".join(cur).strip()))

    # No headings found -> hard-wrap on paragraph boundaries rather than
    # emitting one enormous document.
    if len(chunks) == 1 and len(text) > MIN_SPLIT * 2:
        paras, cur, size, chunks = text.split("\n\n"), [], 0, []
        for p in paras:
            cur.append(p)
            size += len(p) + 2
            if size >= TARGET_CHARS:
                chunks.append(("", "\n\n".join(cur).strip()))
                cur, size = [], 0
        if cur:
            chunks.append(("", "\n\n".join(cur).strip()))
    return [(l, b) for l, b in chunks if b.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true", help="also write the live datastore")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--input", default=None, help="missing_files_text.json")
    args = ap.parse_args()

    if args.revert:
        if not os.path.exists(BACKUP):
            print("no backup to revert")
            return 1
        b = json.load(open(BACKUP))
        for p in b["created_files"]:
            fp = os.path.join(KB, p)
            if os.path.exists(fp):
                os.remove(fp)
        # Remove only THIS script's rows, by doc_id. Restoring the whole
        # manifest from a snapshot taken at write time silently discards every
        # row any OTHER generator added since -- which is not hypothetical: it
        # deleted 54 video-transcript rows here on 2026-08-07 and orphaned their
        # files on disk, with no error and no output saying so.
        mine = set(b.get("doc_ids") or [])
        if not mine:  # backups written before doc_ids was recorded
            mine = {os.path.basename(p)[:-5] for p in b["created_files"]}
        rows = [json.loads(l) for l in open(MANIFEST)]
        kept = [r for r in rows if r["doc_id"] not in mine]
        with open(MANIFEST, "w") as f:
            for row in kept:
                f.write(json.dumps(row) + "\n")
        print(f"reverted: removed {len(b['created_files'])} files, "
              f"manifest {len(rows)} -> {len(kept)} rows")
        return 0

    src = args.input or os.path.join(
        os.environ.get("SCRATCH", "."), "missing_files_text.json")
    if not os.path.exists(src):
        print(f"input not found: {src}\nPass --input <missing_files_text.json>")
        return 1
    assert_placements_are_real_nodes()
    items = [i for i in json.load(open(src)) if i.get("chars", 0) > 0]
    print(f"files with extracted text: {len(items)}")

    manifest = [json.loads(l) for l in open(MANIFEST)]
    known = {r["doc_id"] for r in manifest}
    today = date.today().isoformat()

    created, new_rows, skipped = [], [], []
    for it in items:
        url = it["url"]
        cat, sub, kb_path = placement_for(url)
        base_title = title_of(url)
        base_id = "file_" + slug(url.rsplit("/", 1)[-1])
        sections = split_sections(it["text"])

        for n, (label, body) in enumerate(sections, 1):
            if len(sections) == 1:
                doc_id, title = base_id, base_title
            else:
                doc_id = f"{base_id}_{n:02d}"
                title = f"{base_title} — part {n} of {len(sections)}"
                if label:
                    clean = re.sub(r"\s+", " ", label).strip()[:60]
                    title = f"{base_title} — {clean}"
            if doc_id in known:
                skipped.append(doc_id)
                continue
            known.add(doc_id)

            header = (f"{title}\n\nSource document: {urllib.parse.unquote(url)}\n"
                      f"Linked from morgan.edu/ora. Extracted text of the file itself.\n\n")
            doc = {
                "doc_id": doc_id,
                "title": title,
                "category": cat,
                "subcategory": sub,
                "display_label": title,
                "source_url": url,
                "procedure_url": url,
                "content": header + body,
                "last_scraped": today,
                "kb_path": kb_path,
                "legacy_category": "form",
                "extracted_from_file": True,
            }
            rel = os.path.join(kb_path.replace("/", os.sep), doc_id + ".json")
            created.append((rel, doc))
            new_rows.append({
                "doc_id": doc_id, "title": title, "category": cat,
                "subcategory": sub, "display_label": title,
                "source_url": url, "procedure_url": url,
                "playwright_verified": False, "file_path": rel.replace(os.sep, "/"),
            })

    print(f"documents to create: {len(created)}  (skipped existing: {len(skipped)})")
    by_file: dict[str, int] = {}
    for it in items:
        base = urllib.parse.unquote(it["url"].rsplit("/", 1)[-1])
        by_file[base] = sum(1 for r, _ in created if slug(base) in r)
    for name, n in sorted(by_file.items(), key=lambda kv: -kv[1]):
        print(f"   {n:3d} docs  {name[:78]}")

    if args.dry_run:
        print("\n[dry-run] nothing written")
        return 0

    json.dump({"manifest": manifest,
               "created_files": [r.replace(os.sep, "/") for r, _ in created],
               "doc_ids": [d["doc_id"] for _, d in created]},
              open(BACKUP, "w"))

    for rel, doc in created:
        fp = os.path.join(KB, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        json.dump(doc, open(fp, "w"), indent=1)
    with open(MANIFEST, "w") as f:
        for row in manifest + new_rows:
            f.write(json.dumps(row) + "\n")
    print(f"\nwrote {len(created)} documents; manifest {len(manifest)} -> "
          f"{len(manifest) + len(new_rows)} rows")

    if args.push:
        sys.path.insert(0, os.path.join(REPO, "backend"))
        import datastore_manager as dm
        # create_kb_document, NOT update_document. These doc_ids are new, and
        # update_document's create path (allow_missing) writes content with no
        # metadata -- the document would answer questions but carry no title,
        # category or kb_path, so it would land in the tree's Unfiled bucket and
        # be cited with no URL. create_kb_document writes the full metadata set a
        # seeded document carries. It refuses to overwrite, which is the correct
        # behaviour here: a collision means the doc_id is wrong, not that we
        # should clobber someone's document.
        ok = fail = exists = 0
        for _, doc in created:
            try:
                res = dm.create_kb_document(
                    doc_id=doc["doc_id"], title=doc["title"], content=doc["content"],
                    kb_path=doc["kb_path"], source_url=doc["source_url"],
                    procedure_url=doc["procedure_url"],
                )
                if res.get("success"):
                    ok += 1
                elif "already exists" in (res.get("message") or ""):
                    exists += 1
                else:
                    fail += 1
                    print(f"  PUSH FAIL {doc['doc_id']}: {res.get('message')}")
            except Exception as e:
                fail += 1
                print(f"  PUSH FAIL {doc['doc_id']}: {str(e)[:160]}")
        print(f"pushed to datastore: created={ok} already_existed={exists} failed={fail}")
        print("REMEMBER: clear the chat answer cache (admin -> Sync All).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
