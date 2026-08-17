#!/usr/bin/env python3
"""Read every PAPPG slice and write a REVIEW file. Offline; never on a request path.

Golden rule 4: this does not touch `rulebook_baseline.RULES`. It produces a file a
human reads, edits and then pastes from. Extraction is model output, and model
output does not reach a PI unreviewed.

Each section carries its own read report, so a run that stopped early says so
rather than letting a short list read as a complete one.

    python3 kb_pappg/ingest_all.py                     # all ten
    python3 kb_pappg/ingest_all.py --only project_summary budget_and_budget_justification
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

OUT = os.path.join(ROOT, "backend", "kb_structured", "_pappg_24_1_extracted_review.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--budget-s", type=float, default=150.0)
    args = ap.parse_args()

    from services import pappg_ingest as pi

    slices = pi.load_slices()
    keys = args.only or list(slices)
    results, t0 = [], time.time()

    print(f"{'section':<52}{'chars':>8}{'rows':>6}{'drop':>6}{'secs':>7}  caps")
    print("-" * 88)
    for key in keys:
        t = time.time()
        try:
            res = pi.read_section(key, slices=slices, budget_s=args.budget_s)
        except Exception as exc:                       # never lose the whole run
            print(f"{key:<52}{'':>8}{'':>6}{'':>6}{time.time()-t:>7.1f}  FAILED {exc}")
            results.append({"section_key": key, "error": str(exc), "rows": []})
            continue
        rr = res["read_report"]
        caps = ",".join(c for c, on in
                        (("round", rr["hit_round_cap"]), ("time", rr["hit_time_cap"])) if on)
        if res.get("suspicious_yield"):
            caps = (caps + " " if caps else "") + "LOW-YIELD"
        print(f"{key:<52}{res['chars']:>8,}{len(res['rows']):>6}"
              f"{len(res['dropped_out_of_scope']):>6}{time.time()-t:>7.1f}  {caps or '-'}")
        results.append(res)

    # A section whose model call failed to parse loses every row and says so only
    # in a log line. Surface it at the end, where it cannot be scrolled past.
    bad = [r["section_key"] for r in results if r.get("suspicious_yield")]
    failed = [r["section_key"] for r in results if r.get("error")]

    total_rows = sum(len(r.get("rows") or []) for r in results)
    print("-" * 88)
    print(f"{'TOTAL':<52}{'':>8}{total_rows:>6}{'':>6}{time.time()-t0:>7.1f}")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"pappg_version": "NSF 24-1",
                   "generated_by": "kb_pappg/ingest_all.py",
                   "review_status": "UNREVIEWED — do not paste into RULES until a human has read it",
                   "sections": results}, fh, indent=2, ensure_ascii=False)
    print(f"\nwrote {args.out}")
    if failed:
        print(f"\nFAILED OUTRIGHT: {', '.join(failed)}")
    if bad:
        print(f"\nLOW YIELD -- re-run these before trusting them: {', '.join(bad)}")
        print("A section whose JSON failed to parse loses every row in that response "
              "and reports only a log line. A thin section reads exactly like a "
              "section with few rules.")
    if failed or bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
