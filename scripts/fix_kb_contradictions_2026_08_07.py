#!/usr/bin/env python3
"""Close the contradictions the 2026-08-07 site audit found.

A CONTRADICTION is worse than a gap: the assistant answers confidently and
wrongly, and nothing in the answer signals doubt. Each fix below is a literal
string replacement whose OLD text was verified present and whose NEW text was
verified against the live page or the transcribed form image.

Every edit is (doc_id, old, new). The replacement asserts the old string is
present exactly once -- a silent no-op is the failure mode that makes a fix
script look applied when it changed nothing.

Usage:
  python3 scripts/fix_kb_contradictions_2026_08_07.py --dry-run
  python3 scripts/fix_kb_contradictions_2026_08_07.py            # snapshot
  python3 scripts/fix_kb_contradictions_2026_08_07.py --push     # + datastore
  python3 scripts/fix_kb_contradictions_2026_08_07.py --revert
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB = os.path.join(REPO, "backend", "kb_structured")
MANIFEST = os.path.join(KB, "_all_documents.jsonl")
BACKUP = os.path.join(KB, "_contradiction_fix_backup.json")

# (doc_id, old, new, why)
FIXES = [
    # --- The Internal Routing Form. Verified against the transcribed form
    # images (MSU-IRF-Sept. 4, 2024), not against prose about the form.
    ("pre_award_internal_routing_form",
     "captures certifications regarding Responsible Conduct of Research standards,",
     "captures the PI's certifications (see Section 7 below),",
     "Section 7 has exactly four certifications and none mentions Responsible "
     "Conduct of Research. The claim was invented."),

    ("pre_award_internal_routing_form",
     "Dean or Center Director, ORA Research Budget Specialist, Grants Manager, and Director of Research Administration.",
     "Dean, Research Budget Specialist, Grants Manager, and Director, Research "
     "Administration (the form's own signature-block labels; there is no Center "
     "Director line).",
     "The signature block reads 'Dean'. 'or Center Director' is not on the form."),

    ("pre_award_internal_routing_form",
     "animal research, radioactive materials, and faculty release time.",
     "animal research, radioactive materials (the PI must be a permit holder or "
     "authorized under a current permit), use of an ionizing radiation device — "
     "whose listed examples include accelerators, x-ray machines, electron "
     "microscopes, and explicitly NON-ionizing devices (laser, ultraviolet, "
     "microwave, radio, ultrasonic frequency) — and faculty release time.",
     "The form asks TWO separate radiation questions. The KB carried only the "
     "radioactive-materials one, so a PI using a laser saw nothing telling them "
     "the IRF asks about it."),

    ("pre_award_internal_routing_form",
     "questions can be directed to Ms. Deshun Li (deshun.li@morgan.edu) or ask.ora@morgan.edu",
     "IRF questions go to ask.ora@morgan.edu; the budget (Excel) and budget "
     "justification (Word) go to Ms. Deshun Li (deshun.li@morgan.edu)",
     "The page routes IRF questions to ask.ora@morgan.edu. Deshun Li is named "
     "only as the recipient of the budget documents."),

    # --- Staff
    ("staff_keyshawn_moncrieffe",
     "Dr. Keyshawn Moncrieffe, PhD is the Director for Research Compliance",
     "Dr. Keyshawn Moncrieffe, PhD is the Acting Director for Research Compliance",
     "Their own profile page, the human-subjects page and the announcements page "
     "all say Acting Director; the announcement says it is until a permanent "
     "Director is hired."),
    ("staff_keyshawn_moncrieffe",
     "Dr. Keyshawn Moncrieffe serves as Director for Research Compliance",
     "Dr. Keyshawn Moncrieffe serves as Acting Director for Research Compliance",
     "Same; second occurrence."),

    ("staff_farin_kamangar",
     " He leads the Office of Research Administration and oversees the University's "
     "research strategy and externally sponsored research portfolio.",
     " He oversees the University's research strategy and externally sponsored "
     "research portfolio.",
     "'Leads the Office of Research Administration' appears on no page in the "
     "crawl, and the directory names Gillian Silver as ORA's Director. An "
     "unverifiable bridging sentence about who runs ORA."),

    # --- Trainings
    ("trainings_spark",
     "Seven modules, 3 hours 40 minutes in total:",
     "Seven modules, 4 hours (240 minutes) in total:",
     "The page gives per-module times only: 30+45+30+30+45+30+30 = 240. The KB "
     "listed all seven correctly and then totalled them wrong."),

    ("trainings_e_training",
     "https://rise.articulate.com/share/5gROiZqb81HR0jfGreHGt00baHxOl2m2",
     "https://share.articulate.com/Oj9sl7O574NyfS_WK9btG",
     "The old share id appears nowhere on the live page -- republishing a Rise "
     "course mints a new id. This link sent people to a superseded course."),

    ("trainings_monthly_d_red",
     "The schedule pattern is typically the second Wednesday of each month "
     "(recent dates fall on Sep 10, Oct 9, Nov 12, Dec 11). Each session is "
     "recorded via Zoom and slide decks (PDFs) are archived.",
     "The page publishes no meeting cadence, and its own dates do not follow one "
     "(Jun 15, 2026 is a Monday; Apr 22 and Mar 25, 2026 are fourth Wednesdays) "
     "-- do not state a recurring day. Slide decks (PDFs) are archived; some "
     "sessions are recorded, but several rows read 'No recording available'.",
     "Both claims are refuted by the page's own table."),

    # --- Post-award
    ("post_award_notification_and_setup",
     "Account setup typically takes up to three (3) business days.",
     "Account setup typically takes up to three (3) days.",
     "The page says 'three days'. 'Business' was added, stretching a 3-day "
     "expectation to as much as 5 calendar days."),

    ("post_award_changes_to_an_award",
     "Requests are routed through the Associate Vice President for Research, Dr. Farin Kamangar.",
     "Requests are routed through the Associate Vice President for ORA, Dr. Farin Kamangar.",
     "The page reads 'Associate Vice President for ORA'."),

    # --- Pre-award
    ("pre_award_overview",
     "at least two weeks",
     "preferably two weeks",
     "The page says 'Preferably, two weeks prior'. Hardening a preference into a "
     "requirement tells a PI at 10 days out that they are late."),

    ("pre_award_role_of_principal_investigator",
     "Full-time tenure track faculty members at any academic rank automatically qualify as PI/PD.",
     "The page frames these as suggestions: full-time tenure track faculty "
     "members at any academic rank are eligible to serve as PI/PD.",
     "The page opens the list with 'Following are suggestions:'."),
]

# Struct-field corrections. These live only in the snapshot -- the datastore
# carries seven seeder fields and none of these -- so they are inert today, but
# they are wrong and would become live the moment struct_data is ever pushed.
STRUCT_FIXES = [
    ("trainings_spark", "key_facts", "elearning_total_minutes", 220, 240,
     "Seven modules sum to 240."),
    ("post_award_forms_index", "key_facts", "stipend_advance_notice_days", 45, 30,
     "The page says the check issue date should be at least 30 days out. The "
     "document body was corrected to 30 and this field was not, so one document "
     "said both."),
]


def load(rows, doc_id):
    r = rows.get(doc_id)
    if not r:
        return None, None
    p = os.path.join(KB, r["file_path"])
    if not os.path.exists(p):
        return None, None
    return p, json.load(open(p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    rows = {json.loads(l)["doc_id"]: json.loads(l) for l in open(MANIFEST)}

    if args.revert:
        if not os.path.exists(BACKUP):
            print("no backup")
            return 1
        b = json.load(open(BACKUP))
        for doc_id, saved in b.items():
            p, d = load(rows, doc_id)
            if d is None:
                continue
            d["content"] = saved["content"]
            if "struct" in saved:
                for field, val in saved["struct"].items():
                    d[field] = val
            json.dump(d, open(p, "w"), indent=1)
        print(f"reverted {len(b)} documents (snapshot only — re-push to undo the datastore)")
        return 0

    backup, applied, failed, touched = {}, [], [], {}

    for doc_id, old, new, why in FIXES:
        p, d = load(rows, doc_id)
        if d is None:
            failed.append((doc_id, "document not found"))
            continue
        c = touched.get(doc_id, d["content"])
        n = c.count(old)
        if n != 1:
            failed.append((doc_id, f"old text found {n} times, expected 1: {old[:70]!r}"))
            continue
        backup.setdefault(doc_id, {"content": d["content"]})
        touched[doc_id] = c.replace(old, new)
        applied.append((doc_id, why))

    for doc_id, field, key, old_v, new_v, why in STRUCT_FIXES:
        p, d = load(rows, doc_id)
        if d is None or field not in d or key not in d[field]:
            failed.append((doc_id, f"{field}.{key} not present"))
            continue
        if d[field][key] != old_v:
            failed.append((doc_id, f"{field}.{key} is {d[field][key]!r}, expected {old_v!r}"))
            continue
        backup.setdefault(doc_id, {"content": d["content"]})
        backup[doc_id].setdefault("struct", {})[field] = json.loads(json.dumps(d[field]))
        d[field][key] = new_v
        applied.append((f"{doc_id}.{field}.{key}", why))
        if not args.dry_run:
            json.dump(d, open(p, "w"), indent=1)

    print(f"content edits applied: {len([a for a in applied])}   failed: {len(failed)}")
    for doc_id, why in applied:
        print(f"  OK   {doc_id}")
    for doc_id, why in failed:
        print(f"  FAIL {doc_id}: {why}")

    if args.dry_run:
        print("\n[dry-run] nothing written")
        return 0
    if failed:
        print("\nRefusing to write: fix the failures first, or the result is a "
              "partially-applied edit set that is harder to reason about than "
              "either state.")
        return 1

    json.dump(backup, open(BACKUP, "w"), indent=1)
    for doc_id, content in touched.items():
        p, d = load(rows, doc_id)
        d["content"] = content
        json.dump(d, open(p, "w"), indent=1)
    print(f"\nwrote {len(touched)} documents to the snapshot")

    if args.push:
        sys.path.insert(0, os.path.join(REPO, "backend"))
        import datastore_manager as dm
        ok = fail = 0
        for doc_id in sorted(set(list(touched) + [f[0] for f in STRUCT_FIXES])):
            p, d = load(rows, doc_id)
            if d is None:
                continue
            try:
                dm.update_document(doc_id, d["content"].encode())
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  PUSH FAIL {doc_id}: {str(e)[:160]}")
        print(f"datastore: updated={ok} failed={fail}")
        print("REMEMBER: clear the chat answer cache (admin -> Sync All). The cache "
              "is keyed on the question with no content hash, so it will keep "
              "replaying exactly the facts this just corrected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
