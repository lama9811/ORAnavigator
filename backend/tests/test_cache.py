"""Regression tests for the L3 (semantic) cache gating bug.

Bug: MultiTierCache.get()/set() only consulted the semantic layer
`if not context_hash`. The frontend always sends a model, so context_hash
was always non-empty -> the semantic cache was never read or written in
production. These tests pin that L3 runs regardless of context_hash.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_cache.py -v
"""
from cache import MultiTierCache


class _FakeSemantic:
    """Stand-in for SemanticCache (the real one needs the embedding API).

    Exact-text storage is enough to verify that MultiTierCache *routes* to
    the L3 layer -- the routing/gating logic is what the bug was in.
    """

    def __init__(self):
        self.store = {}
        self.get_calls = 0
        self.set_calls = 0

    def get(self, query):
        self.get_calls += 1
        return self.store.get(query)

    def set(self, query, response):
        self.set_calls += 1
        self.store[query] = response
        return True


# Non-empty context_hash -- what get_context_hash() returns once a model is set.
CTX = "abc12345"
Q = "what is the indirect cost rate at morgan state university"


def _fresh_cache():
    c = MultiTierCache()
    c.semantic = _FakeSemantic()  # L2 (Redis) is naturally absent in tests
    return c


def test_set_populates_semantic_even_with_context_hash():
    """set() must write to L3 even when a context_hash is present."""
    c = _fresh_cache()
    c.set(Q, "AGENT-ANSWER", context_hash=CTX)
    assert c.semantic.set_calls == 1
    assert c.semantic.store.get(Q) == "AGENT-ANSWER"


def test_get_falls_through_to_semantic_with_context_hash():
    """get() must consult L3 (and return its hit) even with a context_hash."""
    c = _fresh_cache()
    c.set(Q, "AGENT-ANSWER", context_hash=CTX)
    c.l1.clear()  # force an L1 miss so only L3 can answer
    assert c.get(Q, context_hash=CTX) == "AGENT-ANSWER"
    assert c.semantic.get_calls >= 1
