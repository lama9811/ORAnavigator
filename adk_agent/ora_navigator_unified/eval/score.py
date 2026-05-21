#!/usr/bin/env python3
"""
Score a promptfoo results.json: compute the headline faithfulness % and pass
rate, compare against baseline.json, and act as the pre-deploy regression gate.

Usage:
  python score.py results.json                  # report + gate vs baseline.json
  python score.py results.json --update-baseline# write current metrics as baseline
  python score.py results.json --no-gate        # report only, never exit non-zero

Exit code: 0 if no regression (or no baseline / --no-gate); 1 on regression.
"""
import argparse
import json
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"
TOLERANCE = 0.02  # allow a 2-point dip before the gate fails


def compute_metrics(data):
    """Reduce a promptfoo results.json dict to headline metrics."""
    rows = data.get("results", {}).get("results", [])
    total = len(rows)
    passed = sum(1 for r in rows if r.get("success"))
    faith_scores = []
    for r in rows:
        for comp in r.get("gradingResult", {}).get("componentResults", []) or []:
            if (comp.get("assertion") or {}).get("metric") == "faithfulness":
                score_val = comp.get("score")
                if score_val is not None:
                    faith_scores.append(float(score_val))
    faithfulness = sum(faith_scores) / len(faith_scores) if faith_scores else 0.0
    return {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0.0,
        "faithfulness": faithfulness,
        "faithfulness_count": len(faith_scores),
    }


def gate(current, baseline, tolerance=TOLERANCE):
    """Return True if current metrics are within tolerance of baseline."""
    if current["faithfulness"] < baseline["faithfulness"] - tolerance:
        return False
    if current["pass_rate"] < baseline["pass_rate"] - tolerance:
        return False
    return True


def _print_report(m, baseline):
    print("=" * 56)
    print("  ORA Navigator — Faithfulness Exam")
    print("=" * 56)
    print(f"  Cases run            : {m['total']}")
    print(f"  Passed               : {m['passed']}/{m['total']}")
    print(f"  Pass rate            : {m['pass_rate'] * 100:.1f}%")
    print(f"  FAITHFULNESS (headline): {m['faithfulness'] * 100:.1f}%"
          f"  (over {m['faithfulness_count']} cases)")
    if baseline:
        print("-" * 56)
        print(f"  Baseline pass rate   : {baseline['pass_rate'] * 100:.1f}%")
        print(f"  Baseline faithfulness: {baseline['faithfulness'] * 100:.1f}%")
    print("=" * 56)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("results", help="path to promptfoo results.json")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--no-gate", action="store_true")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.results).read_text())
    m = compute_metrics(data)

    if args.update_baseline:
        BASELINE_PATH.write_text(json.dumps(
            {"pass_rate": m["pass_rate"], "faithfulness": m["faithfulness"],
             "total": m["total"]}, indent=2) + "\n")
        print(f"Baseline written to {BASELINE_PATH}")
        _print_report(m, None)
        return 0

    baseline = None
    if BASELINE_PATH.exists():
        baseline = json.loads(BASELINE_PATH.read_text())
    _print_report(m, baseline)

    if args.no_gate or baseline is None:
        if baseline is None:
            print("No baseline.json — run with --update-baseline to record one.")
        return 0

    if gate(m, baseline):
        print("GATE: PASS — no regression vs baseline.")
        return 0
    print("GATE: FAIL — faithfulness or pass rate regressed beyond tolerance.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
