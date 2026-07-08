#!/usr/bin/env python3
"""
analyze_layer3_logs.py — is Layer 3 (the grounding verification pass) earning its keep?

Parses the backend's [LAYER3] / [LATENCY] stdout lines and answers the three
questions that decide whether the pipeline is over-built:

  1. How often does a Pass-1 answer grade "weak" (needs repair)?
  2. When weak, how often does SURGICAL re-grounding fix it vs. falling through
     to the expensive strict-regenerate? (the surgical step is the most fragile
     code in the pipeline — if it rarely pulls weight, retire it)
  3. How often do we refuse outright?

Plus latency stats, so you can see what the second-pass machinery costs.

USAGE
  # A) let the script pull logs itself (needs gcloud + auth):
  python3 scripts/analyze_layer3_logs.py --fetch --hours 168      # last 7 days

  # B) analyze logs you already have (file or stdin):
  gcloud logging read '...' --format='value(textPayload)' > logs.txt
  python3 scripts/analyze_layer3_logs.py --file logs.txt
  cat logs.txt | python3 scripts/analyze_layer3_logs.py

Local-only dev utility (like the other scripts/ one-offs). No deps beyond stdlib.
"""

import argparse
import re
import subprocess
import sys
from statistics import median

# --- defaults pulled from cloudbuild.yaml / deploy-cloudrun.sh -----------------
PROJECT = "infra-vertex-494621-v1"
SERVICE = "oranavigator-backend"

# --- exact log-line patterns (verbatim from vertex_agent.py) -------------------
# Non-streaming _run_verified emits these [LAYER3] markers:
RE_SURGICAL   = re.compile(r"\[LAYER3\] Surgically re-grounded (\d+) sentence")
RE_LAT_BAIL   = re.compile(r"\[LAYER3\] over latency budget")
RE_STRICT     = re.compile(r"\[LAYER3\] Grounding unverified \((\d+) chunks, (\d+)% coverage\) - regenerating")
RE_REFUSE     = re.compile(r"\[LAYER3\] Regeneration produced no usable answer")
# Every DELIVERED turn (both paths) emits a [LATENCY] line; refusals return before it.
RE_LATENCY    = re.compile(
    r"\[LATENCY\] chat turn( \(stream\))? (\d+)ms \(verdict=(\w+), chunks=(\d+)\)"
)


def fetch_logs(hours: int, project: str, service: str) -> str:
    """Pull the relevant stdout lines from Cloud Run via gcloud."""
    log_filter = (
        f'resource.type="cloud_run_revision" '
        f'resource.labels.service_name="{service}" '
        f'(textPayload:"[LAYER3]" OR textPayload:"[LATENCY]")'
    )
    cmd = [
        "gcloud", "logging", "read", log_filter,
        f"--project={project}",
        f"--freshness={hours}h",
        "--format=value(textPayload)",
        "--limit=100000",
    ]
    print(f"→ {' '.join(cmd[:3])} … (freshness={hours}h, project={project})",
          file=sys.stderr)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("ERROR: gcloud not found on PATH. Install it or use --file/stdin.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"ERROR: gcloud logging read failed:\n{e.stderr.strip()}")
    return out.stdout


def analyze(text: str) -> dict:
    surgical = lat_bail = strict = refuse = 0
    turns_stream = turns_nonstream = 0
    latencies_ns, latencies_st = [], []
    verdicts = {}

    for line in text.splitlines():
        if RE_SURGICAL.search(line):
            surgical += 1
        elif RE_LAT_BAIL.search(line):
            lat_bail += 1
        elif RE_STRICT.search(line):
            strict += 1
        elif RE_REFUSE.search(line):
            refuse += 1
        m = RE_LATENCY.search(line)
        if m:
            is_stream, ms, verdict, _chunks = m.groups()
            ms = int(ms)
            if is_stream:
                turns_stream += 1
                latencies_st.append(ms)
            else:
                turns_nonstream += 1
                latencies_ns.append(ms)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1

    delivered = turns_stream + turns_nonstream
    # "weak events" = Pass-1 answers that needed real repair (non-stream path only,
    # since only it logs [LAYER3]). Undercounts the "every sentence was actually
    # backed → flip to ok" case, which logs nothing. Treat as a lower bound.
    weak_events = surgical + lat_bail + strict
    all_latencies = latencies_ns + latencies_st

    return {
        "delivered": delivered,
        "turns_stream": turns_stream,
        "turns_nonstream": turns_nonstream,
        "surgical": surgical,
        "lat_bail": lat_bail,
        "strict": strict,
        "refuse": refuse,
        "weak_events": weak_events,
        "verdicts": verdicts,
        "lat_all": all_latencies,
        "lat_ns": latencies_ns,
        "lat_st": latencies_st,
    }


def pct(n, d):
    return f"{(100.0 * n / d):.1f}%" if d else "n/a"


def lat_summary(vals):
    if not vals:
        return "no data"
    s = sorted(vals)
    p50 = median(s)
    p90 = s[min(len(s) - 1, int(0.9 * len(s)))]
    return f"p50={p50:.0f}ms  p90={p90:.0f}ms  max={max(s)}ms  (n={len(s)})"


def report(a: dict) -> None:
    D = a["delivered"]
    bar = "─" * 62
    print(bar)
    print("  LAYER 3 — grounding verification: is it earning its keep?")
    print(bar)

    print("\n  VOLUME")
    print(f"    Delivered turns .............. {D}")
    print(f"      · non-streaming (/chat) .... {a['turns_nonstream']}")
    print(f"      · streaming (/chat/stream) . {a['turns_stream']}")
    print(f"    Refused (Layer 3 gave up) .... {a['refuse']}"
          f"   ({pct(a['refuse'], D + a['refuse'])} of all attempts)")

    print("\n  Q1 — HOW OFTEN IS AN ANSWER 'WEAK' (needs repair)?")
    print(f"    Weak events (lower bound*) ... {a['weak_events']}"
          f"   ≈ {pct(a['weak_events'], D)} of delivered turns")
    print("    * non-streaming only; the 'all sentences backed → ok' case isn't logged.")

    print("\n  Q2 — DOES SURGICAL RE-GROUNDING PULL ITS WEIGHT?")
    print(f"    Surgical fixes (step 1 win) .. {a['surgical']}")
    print(f"    Fell through to strict regen . {a['strict']}")
    print(f"    Latency bail (shipped + note)  {a['lat_bail']}")
    needed = a["surgical"] + a["strict"]
    if needed:
        print(f"    → of weaks needing a real fix, surgical handled "
              f"{pct(a['surgical'], needed)}")
    else:
        print("    → no weak-repair events recorded in this window.")

    print("\n  Q3 — LATENCY COST")
    print(f"    All turns .................... {lat_summary(a['lat_all'])}")
    print(f"      · non-streaming ............ {lat_summary(a['lat_ns'])}")
    print(f"      · streaming ................ {lat_summary(a['lat_st'])}")
    if a["verdicts"]:
        vs = "  ".join(f"{k}={v}" for k, v in sorted(a["verdicts"].items()))
        print(f"    Final verdict at delivery .... {vs}")

    # --- opinionated read --------------------------------------------------
    print("\n" + bar)
    print("  READ")
    print(bar)
    if D == 0:
        print("    No [LATENCY] lines found. Wrong service/time window, or logs")
        print("    rotated out. Widen --hours or check the filter.")
        return

    weak_rate = 100.0 * a["weak_events"] / D
    surg = a["surgical"]
    surg_share = (100.0 * surg / needed) if needed else 0.0

    if weak_rate < 5:
        print(f"    • Weak answers are rare (~{weak_rate:.1f}%). The whole repair")
        print("      cascade runs on a small tail — grounding is mostly healthy.")
    else:
        print(f"    • Weak answers are common (~{weak_rate:.1f}%). The repair path")
        print("      matters — before simplifying, look at KB coverage gaps.")

    if surg < 3 or surg_share < 25:
        print(f"    • Surgical re-grounding fired usefully {surg}× "
              f"({surg_share:.0f}% of real fixes).")
        print("      → LOW payoff for your most fragile code. Strong candidate to")
        print("        retire: collapse to grade → (bail?) → strict-regen → refuse.")
    else:
        print(f"    • Surgical re-grounding handled {surg_share:.0f}% of real fixes "
              f"({surg}×).")
        print("      → It's pulling weight; keep it.")

    if a["refuse"] == 0:
        print("    • Zero refusals: the KB answers what's asked; the refuse path is")
        print("      cheap insurance you're rarely hitting. Fine to keep as-is.")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--fetch", action="store_true",
                     help="pull logs from Cloud Run via gcloud")
    src.add_argument("--file", help="read log lines from a file instead")
    ap.add_argument("--hours", type=int, default=168,
                    help="with --fetch: look-back window in hours (default 168 = 7d)")
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--service", default=SERVICE)
    args = ap.parse_args()

    if args.fetch:
        text = fetch_logs(args.hours, args.project, args.service)
    elif args.file:
        with open(args.file, encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        if sys.stdin.isatty():
            ap.error("no input: use --fetch, --file PATH, or pipe logs on stdin")
        text = sys.stdin.read()

    report(analyze(text))


if __name__ == "__main__":
    main()
