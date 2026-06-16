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


# =====================================================================
# Personal-recall bypass -- session-specific recall answers must NEVER
# enter the cache. The cache key is (md5(query) + model), with NO
# user_id / session_id, so caching a recall answer leaks one user's
# personal context to every other user and serves stale answers across
# different conversations. Recall questions also produced the live
# regression where a model refusal was cached and re-served forever.
# =====================================================================

def test_personal_recall_question_is_not_cached():
    """Questions that ask the bot to recall something the user said about
    themselves (department, role, sponsor, deadline, IRB/IACUC protocol)
    must bypass the cache entirely. They are answered from the chat
    history, not from a globally-shared KB result, so they are not safe
    to share across users or sessions."""
    c = _fresh_cache()
    recall_questions = [
        "What department am I in?",
        "What sponsor did I tell you I work with?",
        "Remind me what department I'm in.",
        "What's my upcoming deadline?",
        "Did I mention my IRB protocol?",
        "What do you remember about me?",
        "Do you remember my department?",
        "What is the department I'm in?",
    ]
    for q in recall_questions:
        stored = c.set(q, "You're in the Biology department.", context_hash=CTX)
        assert stored is False, f"recall question must not be cached: {q!r}"
        # And reading it back must return nothing
        assert c.get(q, context_hash=CTX) is None, \
            f"recall question must not have a cached entry: {q!r}"


def test_refusal_responses_are_not_stored():
    """Model refusals ('I do not have information...') must not be stored.
    A cached refusal poisons the cache: once the bot is fixed to actually
    answer, the cache keeps serving the old refusal until TTL expiry."""
    c = _fresh_cache()
    # Slightly different phrasings of the same refusal pattern
    refusals = [
        "I do not have information about your specific department.",
        "I don't have that information available.",
        "I cannot provide details about your protocol.",
        "I can't access information about your role.",
    ]
    # Use a non-recall query so _should_cache() doesn't reject it for the
    # WRONG reason -- this test isolates the response-content filter.
    institutional_q = "What is the federal F&A rate for sponsored research at Morgan State"
    for refusal in refusals:
        stored = c.set(institutional_q, refusal, context_hash=CTX)
        assert stored is False, \
            f"refusal response must not be cached: {refusal!r}"


def test_transient_busy_responses_are_not_stored():
    """Transient 'try again' messages (rate-limit / busy / initializing) must not
    be cached. REGRESSION: 'The system is busy right now' slipped past the old
    guard, got cached in Redis (7-day TTL), and kept being served after the
    backend was fixed -- the cache answered before the fixed code ever ran."""
    c = _fresh_cache()
    transient = [
        "The system is busy right now. Please try again in a moment.",
        "I'm temporarily having trouble connecting to my knowledge base. "
        "This is a system issue, not a gap in my knowledge. Please try again in a minute.",
        "AI system is initializing. Please try again in a moment.",
    ]
    institutional_q = "What is the federal F&A rate for sponsored research at Morgan State"
    for msg in transient:
        stored = c.set(institutional_q, msg, context_hash=CTX)
        assert stored is False, f"transient message must not be cached: {msg!r}"


def test_institutional_questions_still_cache():
    """Regression guard: the bypass must not block legitimate cacheable
    queries. KB-grounded institutional questions still go through cache."""
    c = _fresh_cache()
    institutional = "What is the federal F&A rate for sponsored research at Morgan State"
    answer = "The on-campus organized research F&A rate is 54% for FY 2025-2026."
    stored = c.set(institutional, answer, context_hash=CTX)
    assert stored is True
    assert c.get(institutional, context_hash=CTX) == answer
