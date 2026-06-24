"""Tests for the global Top-10 most-asked ranking (services/popular_questions.py).

The ranking buckets real user questions into the curated pool by keyword
overlap, then ranks the pool by DISTINCT users per bucket. These pin the
load-bearing behavior: distinct-user counting, tie-break, fill-to-limit, and
that unmatched (gibberish) questions are ignored.

Run from backend/:
    JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
      python3 -m pytest tests/test_popular_questions.py -q
"""
from services.popular_questions import (
    match_question, rank_questions, _topic_tokens, _build_curated_tokens,
)

# A small curated pool standing in for main.DEFAULT_QUESTION_POOL.
CURATED = [
    "How do I prepare a budget for a federal grant?",          # 0
    "What is Morgan State's federal F&A rate?",                # 1
    "How do I submit an IRB application for human subjects?",  # 2
    "How do I request a no-cost extension on an award?",       # 3
    "Who handles subaward and subcontract questions?",         # 4
]
_CT = _build_curated_tokens(CURATED)


# --- matching ---------------------------------------------------------------

def test_match_budget_question():
    idx = match_question(_topic_tokens("how do i build a budget for my federal grant"), _CT)
    assert idx == 0


def test_match_fa_rate_via_alias():
    # "indirect cost" and "F&A" both normalize to the same token, so they match
    # the F&A curated question even with no literal shared words.
    assert match_question(_topic_tokens("what is the indirect cost rate"), _CT) == 1
    assert match_question(_topic_tokens("morgan f&a rate"), _CT) == 1


def test_match_subaward_via_alias():
    assert match_question(_topic_tokens("who do i contact about a sub-contract"), _CT) == 4


def test_gibberish_matches_nothing():
    assert match_question(_topic_tokens("xyzzy plugh nonsense"), _CT) is None
    assert match_question(_topic_tokens(""), _CT) is None


def test_single_distinctive_token_matches():
    # "subaward" is unique to one curated question -> scores 2 -> matches on its own.
    assert match_question(_topic_tokens("subaward"), _CT) == 4


def test_single_ambiguous_token_is_none():
    # "federal" appears in both the budget and F&A questions -> ambiguous, scores
    # 1 -> below the bar, so it matches nothing on its own.
    assert match_question(_topic_tokens("federal"), _CT) is None


# --- ranking ----------------------------------------------------------------

def test_ranks_by_distinct_users_not_raw_count():
    """One user asking budgets 3x must rank BELOW a topic two different users
    asked once each -- proves we count distinct users, not raw volume."""
    rows = [
        (1, "prepare a budget for a federal grant"),
        (1, "budget for my federal grant again"),
        (1, "more budget federal grant please"),   # user 1, three times -> distinct 1
        (3, "what is the f&a rate"),
        (4, "morgan f&a rate"),                     # users 3 & 4 -> distinct 2
    ]
    out = rank_questions(rows, CURATED, limit=5)
    assert out[0] == CURATED[1]   # F&A (2 distinct users) ranks first
    assert out[1] == CURATED[0]   # budget (1 distinct user) second


def test_tie_breaks_by_curated_order():
    rows = [
        (1, "prepare a budget for a federal grant"),
        (2, "budget help federal grant"),           # budget -> 2 distinct
        (3, "what is the f&a rate"),
        (4, "morgan f&a rate federal"),             # F&A -> 2 distinct (tie)
    ]
    out = rank_questions(rows, CURATED, limit=5)
    # Tie at 2 users each -> lower curated index (budget=0) wins.
    assert out[0] == CURATED[0]
    assert out[1] == CURATED[1]


def test_fills_to_limit_with_curated_order_when_demand_thin():
    rows = [(1, "submit an irb application for human subjects")]  # only IRB has demand
    out = rank_questions(rows, CURATED, limit=5)
    assert out[0] == CURATED[2]            # demand-ranked first
    assert len(out) == 5                   # padded out to limit
    assert set(out) == set(CURATED)        # the rest fill from the pool


def test_returns_exactly_limit():
    rows = [(1, "submit an irb application for human subjects")]
    assert len(rank_questions(rows, CURATED, limit=3)) == 3


def test_cold_start_empty_history_returns_curated_order():
    out = rank_questions([], CURATED, limit=3)
    assert out == CURATED[:3]


def test_gibberish_rows_do_not_appear_or_crash():
    rows = [(1, "xyzzy nonsense"), (2, ""), (3, None)]
    out = rank_questions(rows, CURATED, limit=5)
    assert out == CURATED[:5]   # nothing matched -> pure curated order


def test_empty_curated_returns_empty():
    assert rank_questions([(1, "budget grant")], [], limit=5) == []
