#!/usr/bin/env python3
"""Split the ORA eTraining modules into one knowledge base document per lesson.

Why
---
Loading each module as a single document put 17k-84k characters into places
where the rest of the KB averages a few thousand, and this datastore cannot be
configured for chunking (`chunkingConfig` is create-time only and does not
support text/plain), so each document is retrieved WHOLE. Asking "do I need
three quotes for a $10,000 purchase?" pulled the entire procurement course.

One document per lesson gives ~2.5k-character units that match the rest of the
KB, and each one carries a **deep link to its own lesson**
(`{share}#/lessons/{id}`) so the assistant can open the learner at the exact
lesson the answer came from rather than at the top of a 23-lesson course.

What it does
------------
* CREATES one document per lesson (83), placed at trainings/e_training.
* REWRITES the 8 module documents into overviews -- description, lesson index,
  attachment list -- rather than leaving the full text duplicated across both
  the parent and its lessons, which would have the parent competing with its
  own children for every query.

Safety
------
* Parent bodies are backed up before rewriting; --revert restores them and
  deletes the lesson documents.
* Refuses to create over an existing doc_id (create_kb_document 409s), so a
  half-finished run can be re-run.
* --dry-run prints the plan and writes nothing.

Usage
-----
    python3 scripts/split_etraining_lessons.py --dry-run
    python3 scripts/split_etraining_lessons.py
    python3 scripts/split_etraining_lessons.py --revert

Requires VERTEX_AI_DATASTORE_ID and application-default credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "backend" / "kb_structured" / "_etraining_lessons.json"
BACKUP = ROOT / "backend" / "kb_structured" / "_etraining_parent_backup.json"
KB_PATH = "trainings/e_training"

sys.path.insert(0, str(ROOT / "backend"))


def _require_env() -> None:
    if not os.getenv("VERTEX_AI_DATASTORE_ID"):
        sys.exit(
            "VERTEX_AI_DATASTORE_ID is not set.\n"
            "  export VERTEX_AI_DATASTORE_ID="
            "projects/infra-vertex-494621-v1/locations/us/collections/"
            "default_collection/dataStores/oranavigator-kb-v8"
        )


def load_plan() -> dict:
    if not PLAN.exists():
        sys.exit(f"No plan at {PLAN}.")
    return json.loads(PLAN.read_text())


def overview(mod: dict) -> str:
    """The parent document: what this module is, and what is in it.

    Deliberately NOT the full text. Leaving the whole course here as well as in
    the lessons would make every lesson compete with its own parent, which is
    the retrieval problem this split exists to fix.
    """
    lines = [mod["module_title"], ""]
    if mod.get("description"):
        lines += [mod["description"], ""]
    lines += [f"Self-paced ORA eTraining module. Source: {mod['share_url']}", ""]
    lines.append(f"This module has {len(mod['lessons'])} lessons, each a separate "
                 f"knowledge base entry:")
    for i, L in enumerate(mod["lessons"], 1):
        lines.append(f"{i}. {L['title'].split(' — ')[0]}")
    if mod.get("attachments"):
        lines += ["", "Files provided in this module:"]
        for a in mod["attachments"]:
            lines.append(f"- {a['name']}: {a['url']}")
    return "\n".join(lines).strip()


def do_revert() -> int:
    from datastore_manager import delete_document, update_document

    if not BACKUP.exists():
        sys.exit(f"No backup at {BACKUP} — cannot revert.")
    plan = load_plan()
    backup = json.loads(BACKUP.read_text())

    failed = 0
    for mod in plan["modules"]:
        for L in mod["lessons"]:
            r = delete_document(L["doc_id"])
            if not r.get("success"):
                failed += 1
                print(f"  FAILED delete {L['doc_id']}: {r.get('message')}")
    print(f"  deleted lesson documents")

    for doc_id, content in backup.items():
        r = update_document(doc_id, (content or "").encode("utf-8"))
        if not r.get("success"):
            failed += 1
            print(f"  FAILED restore {doc_id}: {r.get('message')}")
    print(f"  restored {len(backup)} module documents")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    _require_env()
    if args.revert:
        return do_revert()

    plan = load_plan()
    lessons = [(m, L) for m in plan["modules"] for L in m["lessons"]]

    if args.dry_run:
        print(f"Would CREATE {len(lessons)} lesson documents at {KB_PATH}:\n")
        for mod in plan["modules"]:
            print(f"  {mod['parent_doc_id']}  ->  {len(mod['lessons'])} lessons")
            for L in mod["lessons"][:2]:
                print(f"      {L['doc_id']}  ({L['chars']:,} chars)")
            if len(mod["lessons"]) > 2:
                print(f"      … {len(mod['lessons']) - 2} more")
        print(f"\nWould REWRITE {len(plan['modules'])} module documents as overviews.")
        print("Nothing was written. Drop --dry-run to apply.")
        return 0

    from datastore_manager import (create_kb_document, get_document_content,
                                   update_document)

    backup = {m["parent_doc_id"]: (get_document_content(m["parent_doc_id"]) or "")
              for m in plan["modules"]}
    BACKUP.write_text(json.dumps(backup, indent=2))
    print(f"Backed up {len(backup)} module documents to {BACKUP}\n")

    created = failed = 0
    for mod, L in lessons:
        r = create_kb_document(
            doc_id=L["doc_id"],
            title=L["title"],
            content=L["content"],
            kb_path=KB_PATH,
            procedure_url=L["procedure_url"],
        )
        if r.get("success"):
            created += 1
        else:
            failed += 1
            print(f"  FAILED {L['doc_id']}: {r.get('message')}")
    print(f"  created {created} lesson documents ({failed} failed)")

    rewritten = 0
    for mod in plan["modules"]:
        r = update_document(mod["parent_doc_id"], overview(mod).encode("utf-8"))
        if r.get("success"):
            rewritten += 1
        else:
            failed += 1
            print(f"  FAILED overview {mod['parent_doc_id']}: {r.get('message')}")
    print(f"  rewrote {rewritten} module documents as overviews")

    print(f"\n{created} created, {rewritten} rewritten, {failed} failed.")
    if failed:
        print("`--revert` deletes the lesson documents and restores the modules.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
