"""Unit tests for score.py — metric computation and the regression gate."""
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EVAL_DIR))

import score  # noqa: E402


def _results(rows):
    """Build a minimal promptfoo results.json structure from (success, faith) rows."""
    out = []
    for success, faith in rows:
        component = []
        if faith is not None:
            component.append({"assertion": {"metric": "faithfulness"}, "score": faith})
        out.append({"success": success, "gradingResult": {"componentResults": component}})
    return {"results": {"results": out}}


def test_compute_metrics_pass_rate_and_faithfulness():
    data = _results([(True, 1.0), (True, 0.8), (False, 0.2)])
    m = score.compute_metrics(data)
    assert m["total"] == 3
    assert m["passed"] == 2
    assert round(m["pass_rate"], 4) == round(2 / 3, 4)
    assert round(m["faithfulness"], 4) == round((1.0 + 0.8 + 0.2) / 3, 4)


def test_compute_metrics_handles_no_faithfulness_assertions():
    data = _results([(True, None), (True, None)])
    m = score.compute_metrics(data)
    assert m["faithfulness"] == 0.0
    assert m["faithfulness_count"] == 0


def test_gate_passes_when_at_or_above_baseline():
    current = {"pass_rate": 0.95, "faithfulness": 0.97}
    baseline = {"pass_rate": 0.95, "faithfulness": 0.97}
    assert score.gate(current, baseline, tolerance=0.02) is True


def test_gate_fails_on_faithfulness_regression():
    current = {"pass_rate": 0.95, "faithfulness": 0.90}
    baseline = {"pass_rate": 0.95, "faithfulness": 0.97}
    assert score.gate(current, baseline, tolerance=0.02) is False


def test_gate_allows_small_dip_within_tolerance():
    current = {"pass_rate": 0.94, "faithfulness": 0.955}
    baseline = {"pass_rate": 0.95, "faithfulness": 0.97}
    assert score.gate(current, baseline, tolerance=0.02) is True
