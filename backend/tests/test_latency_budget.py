"""Tests for the chat latency budget.

When a turn has already spent the budget, Layer 3 skips the expensive Pass-2
regeneration (a full second agent round-trip) and delivers the Pass-1 answer
with a caution note instead -- so a warm turn stays under the target even in
the worst case, rather than stacking a second model pass.
"""
import importlib
import vertex_agent


def test_over_budget_true_past_threshold(monkeypatch):
    monkeypatch.setattr(vertex_agent, "_LATENCY_BUDGET_S", 7.0)
    assert vertex_agent._over_latency_budget(7.0) is True
    assert vertex_agent._over_latency_budget(9.5) is True


def test_under_budget_is_false(monkeypatch):
    monkeypatch.setattr(vertex_agent, "_LATENCY_BUDGET_S", 7.0)
    assert vertex_agent._over_latency_budget(3.0) is False
    assert vertex_agent._over_latency_budget(6.99) is False


def test_budget_is_env_configurable(monkeypatch):
    # The budget is read from CHAT_LATENCY_BUDGET_S at import; default is sane.
    assert vertex_agent._LATENCY_BUDGET_S > 0
    monkeypatch.setattr(vertex_agent, "_LATENCY_BUDGET_S", 2.0)
    assert vertex_agent._over_latency_budget(2.5) is True
    assert vertex_agent._over_latency_budget(1.0) is False
