#!/usr/bin/env python3
"""Mark the correct answer on the eTraining quiz items that carry no marker.

CLAUDE.md states this as a correctness rule, not a formatting one: these modules
teach through worked problems whose distractors are plausible dollar figures, so
an unmarked option lets the assistant quote a deliberately wrong number as
Morgan policy.

Scope, measured 2026-08-07 against the live Rise payloads:
  108 quiz items carry an answer key. 105 are already marked correctly -- the
  extractor handled MULTIPLE_CHOICE properly, and a claim that 83% were inverted
  did not survive checking (387 options compared, 0 mismarked). The gap is the
  3 MULTIPLE_RESPONSE "select all that apply" items, which were flattened to
  bare prose: four assertions in a row with nothing saying which are true.

The answer key is a PER-ANSWER `correct` boolean on each answer object, not a
block-level `correct`/`corrects` id field. Rise OMITS the key entirely when
false, so absent means incorrect.

TWO of the three items are internally inconsistent IN ORA'S OWN COURSE DATA:
the key marks one option correct while `feedbackIncorrect` says "One of your
selections is not a requirement", implying three are. Marking them flatly would
have the assistant assert, for instance, that tax charges need not be removed
from an invoice to a tax-exempt state university. So each rendered block carries
an explicit caution naming the disagreement, and the feedback is reproduced
alongside. Flag this to ORA as a course-authoring fix.

Usage:
  python3 scripts/fix_etraining_quiz_markers.py --payloads <dir> --dry-run
  python3 scripts/fix_etraining_quiz_markers.py --payloads <dir>
  python3 scripts/fix_etraining_quiz_markers.py --payloads <dir> --push
  python3 scripts/fix_etraining_quiz_markers.py --revert
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(REPO, "backend", "kb_structured")
LESSONS = os.path.join(KB, "trainings", "e_training", "*.json")
BACKUP = os.path.join(KB, "_quiz_marker_backup.json")


def strip(h: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", h or "")).split())


def unmarked_items(payload_dir: str) -> list[dict]:
    out = []
    for f in sorted(glob.glob(os.path.join(payload_dir, "*.json"))):
        data = json.load(open(f))

        def walk(o):
            if isinstance(o, dict):
                a = o.get("answers")
                if (isinstance(a, list) and a and isinstance(a[0], dict)
                        and "title" in a[0] and o.get("type") != "MULTIPLE_CHOICE"
                        and any("correct" in x for x in a)):
                    out.append({
                        "type": o.get("type"),
                        "question": strip(o.get("title", "")),
                        "answers": [(strip(x["title"]), bool(x.get("correct"))) for x in a],
                        "feedback": strip(o.get("feedback", "")),
                        "feedback_correct": strip(o.get("feedbackCorrect", "")),
                        "feedback_incorrect": strip(o.get("feedbackIncorrect", "")),
                    })
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(data)
    return out


def render(item: dict) -> str:
    n_correct = sum(1 for _, c in item["answers"] if c)
    lines = []
    fb_i = item["feedback_incorrect"]
    # "One of your selections is not a requirement" implies all-but-one are
    # correct; a key marking exactly one correct says the opposite.
    inconsistent = (n_correct == 1 and len(item["answers"]) > 2
                    and re.search(r"\bone of your selections\b", fb_i, re.I))
    if inconsistent:
        lines.append(
            "  NOTE: this module's own answer key and its feedback disagree about "
            "this question — the key marks one option correct while the feedback "
            "implies all but one are. Treat the options below as quiz material, "
            "NOT as statements of Morgan policy, and check the module itself "
            "before relying on any single option.")
    for text, correct in item["answers"]:
        lines.append(f"  - {text}  [{'CORRECT ANSWER' if correct else 'incorrect'}]")
    for label, key in (("If correct", "feedback_correct"),
                       ("If incorrect", "feedback_incorrect"),
                       ("Explanation", "feedback")):
        if item[key]:
            lines.append(f"  {label}: {item[key]}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payloads", help="directory of live Rise payload json files")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    files = sorted(glob.glob(LESSONS))

    if args.revert:
        if not os.path.exists(BACKUP):
            print("no backup")
            return 1
        b = json.load(open(BACKUP))
        for rel, content in b.items():
            p = os.path.join(KB, rel)
            if os.path.exists(p):
                d = json.load(open(p))
                d["content"] = content
                json.dump(d, open(p, "w"), indent=1)
        print(f"reverted {len(b)} documents (snapshot only — re-push to undo the datastore)")
        return 0

    if not args.payloads:
        print("--payloads is required")
        return 1

    items = unmarked_items(args.payloads)
    print(f"unmarked quiz items in the live payloads: {len(items)}")

    # Load each lesson document ONCE and accumulate edits in it. Two of the
    # three items live in the same file; re-reading it from disk per item means
    # the second edit is computed against the unedited content and the final
    # write silently discards the first.
    cache: dict[str, dict] = {}

    def doc_for(path: str) -> dict:
        if path not in cache:
            cache[path] = json.load(open(path))
        return cache[path]

    backup, edits = {}, []
    for item in items:
        q = item["question"]
        target = None
        for p in files:
            d = doc_for(p)
            c = d.get("content", "") or ""
            if q and q in c:
                target = (p, d, c)
                break
        if not target:
            print(f"  SKIP question not found in any lesson doc: {q[:70]}")
            continue
        p, d, c = target
        i = c.find(q) + len(q)
        # The options follow the question as bare lines, in payload order, and
        # end at the first feedback line. Locate them by matching the exact
        # answer texts rather than by counting lines: a blank line or a stray
        # wrap would silently shift a line-count approach onto the wrong text.
        tail = c[i:i + 4000]
        block_end = i
        for text, _ in item["answers"]:
            pos = c.find(text, block_end)
            if pos == -1:
                block_end = -1
                break
            block_end = pos + len(text)
        if block_end == -1:
            print(f"  SKIP options not found verbatim: {q[:70]}")
            continue
        # Swallow any feedback lines that already follow, so they are not
        # duplicated by render().
        rest = c[block_end:]
        for fb in (item["feedback_correct"], item["feedback_incorrect"], item["feedback"]):
            if fb and rest.lstrip().startswith(fb):
                consumed = len(rest) - len(rest.lstrip())
                block_end += consumed + len(fb)
                rest = c[block_end:]
        new_c = c[:i] + "\n" + render(item) + c[block_end:]
        rel = os.path.relpath(p, KB)
        backup.setdefault(rel, d["content"])
        d["content"] = new_c
        edits.append((rel, d, p, q))
        print(f"  OK   {os.path.basename(p)}: {q[:60]}")

    print(f"\nitems marked: {len(edits)}")
    if args.dry_run:
        if edits:
            print("\n--- sample rendering ---")
            print(render(items[0]))
        print("[dry-run] nothing written")
        return 0
    if not edits:
        print("nothing to do")
        return 0

    json.dump(backup, open(BACKUP, "w"), indent=1)
    for rel, d, p, _ in edits:
        json.dump(d, open(p, "w"), indent=1)
    print(f"wrote {len({r for r, _, _, _ in edits})} documents")

    if args.push:
        sys.path.insert(0, os.path.join(REPO, "backend"))
        import datastore_manager as dm
        ok = fail = 0
        for rel, d, p, _ in edits:
            try:
                dm.update_document(d["doc_id"], d["content"].encode())
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  PUSH FAIL {d['doc_id']}: {str(e)[:150]}")
        print(f"datastore: updated={ok} failed={fail}")
        print("REMEMBER: clear the chat answer cache (admin -> Sync All).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
