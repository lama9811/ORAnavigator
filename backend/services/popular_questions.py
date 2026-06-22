"""Global "Top 10 most-asked questions" for the landing page.

Every user (logged-in or guest) sees the SAME 10 suggestion chips: the curated
ORA questions that the most DISTINCT users have actually asked about.

How it works (see the per-function docs):
  1. The candidates are the curated pool (main.DEFAULT_QUESTION_POOL) -- only
     those clean, pre-approved questions can ever appear, so no user's personal
     phrasing ("my IDSS award") can leak.
  2. Every real question in ChatHistory is matched to the nearest curated
     question by keyword overlap (deterministic, no AI/embedding cost).
  3. Each curated question is ranked by the number of DISTINCT users whose
     questions matched it. Top `limit` win; ties break by curated order.
  4. If fewer than `limit` curated questions have any demand yet, the remaining
     slots are filled from the curated pool in order -- so the list is always
     exactly `limit`, identical for everyone.

The ranking is cached (in-process + Redis) and refreshed by a daily cron; the
serving endpoints never run the scan inline once warm. Everything degrades
gracefully: on any error or empty history, callers get the curated pool.
"""
from __future__ import annotations

import json
import re
import time
from typing import Iterable, Optional

# Common question words that carry no topic signal -- dropped before matching so
# "How do I prepare a budget..." matches on {prepare, budget, federal, grant}.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "at", "is",
    "are", "do", "does", "did", "i", "my", "me", "how", "what", "whats", "where",
    "wheres", "who", "whos", "when", "which", "can", "could", "need", "needed",
    "get", "find", "with", "about", "this", "that", "there", "theres", "their",
    "from", "you", "your", "we", "our", "it", "its", "be", "as", "by", "not",
    "use", "should", "would", "will", "if", "so", "any", "some", "make", "want",
    "know", "tell", "help", "please", "am", "was", "were", "have", "has", "had",
}

# Multi-word / punctuated domain terms collapsed to a single token BEFORE
# tokenizing, applied to BOTH the curated question and the user question, so
# synonyms and split-prone terms still overlap. Keep this list short and
# editable -- it is the main lever for matching quality.
_ALIASES = [
    (r"f\s*&\s*a", "facost"),
    (r"indirect costs?", "facost"),
    (r"no[\s-]*cost extensions?", "ncext"),
    (r"\bnce\b", "ncext"),
    (r"pre[\s-]*award", "preaward"),
    (r"post[\s-]*award", "postaward"),
    (r"principal investigators?", "pi"),
    (r"conflict of interest", "coi"),
    (r"human subjects?", "irb"),
    (r"animal (?:research|study|studies|subjects?)", "iacuc"),
    (r"sub[\s-]*awards?", "subaward"),
    (r"sub[\s-]*contracts?", "subaward"),
    (r"nspm[\s-]*33", "nspm33"),
    (r"research security", "nspm33"),
    (r"d[\s-]*red", "dred"),
    (r"no[\s-]*cost", "ncext"),
]

# A user question matches a curated question when their shared-token SCORE meets
# this bar. A token unique to one curated question (e.g. "subaward", "irb",
# "nspm33") is distinctive and scores 2; a token shared across several curated
# questions (e.g. "federal", "award") is ambiguous and scores 1. So one
# distinctive token OR two ambiguous ones clears the bar -- a single ambiguous
# token does not. Lower this if too many real questions go uncounted.
_MIN_SCORE = 2

# Cache freshness (seconds). The daily cron refreshes well within this; the
# in-process copy avoids a Redis round-trip on every landing-page load.
_TTL_SECONDS = 6 * 60 * 60
_REDIS_KEY = "popular_questions:v1"

_mem: dict = {"questions": None, "at": 0.0}


def _normalize(text: str) -> str:
    t = (text or "").lower().replace("'", "")
    for pattern, repl in _ALIASES:
        t = re.sub(pattern, repl, t)
    return t


def _topic_tokens(text: str) -> set[str]:
    """Meaningful tokens of a question: alphanumeric runs, minus stopwords and
    single chars. Short domain acronyms (pi, irb, coi, nsf, nih) survive."""
    toks = re.findall(r"[a-z0-9]+", _normalize(text))
    return {w for w in toks if len(w) >= 2 and w not in _STOPWORDS}


def _build_curated_tokens(curated: list[str]) -> list[set[str]]:
    return [_topic_tokens(q) for q in curated]


def _token_df(curated_tokens: list[set[str]]) -> dict:
    """Document frequency: how many curated questions contain each token.
    df == 1 means the token is distinctive (identifies a single topic)."""
    df: dict = {}
    for c_tokens in curated_tokens:
        for t in c_tokens:
            df[t] = df.get(t, 0) + 1
    return df


def match_question(q_tokens: set[str], curated_tokens: list[set[str]],
                   df: Optional[dict] = None, min_score: int = _MIN_SCORE) -> Optional[int]:
    """Return the index of the best-matching curated question, or None.

    Score = sum over shared tokens of (2 if distinctive else 1). A curated
    question wins if its score is highest and >= min_score. Ties go to the
    lowest curated index (stable). `df` is precomputed once by the ranker; when
    omitted it is derived from curated_tokens."""
    if df is None:
        df = _token_df(curated_tokens)
    best_idx, best_score = None, 0
    for idx, c_tokens in enumerate(curated_tokens):
        shared = q_tokens & c_tokens
        score = sum(2 if df.get(t, 0) == 1 else 1 for t in shared)
        if score > best_score:
            best_idx, best_score = idx, score
    return best_idx if best_score >= min_score else None


def rank_questions(rows: Iterable[tuple], curated: list[str],
                   limit: int = 10) -> list[str]:
    """Rank curated questions by DISTINCT users who asked something matching them.

    `rows` is an iterable of (user_id, user_query). Returns exactly `limit`
    curated question strings (or all of them if the pool is smaller): the
    demand-ranked ones first, then the rest of the pool in order to fill out
    `limit`. The same user asking a topic many times counts once.
    """
    if not curated:
        return []
    curated_tokens = _build_curated_tokens(curated)
    df = _token_df(curated_tokens)
    buckets: dict[int, set] = {}  # curated index -> set of user_ids

    for user_id, user_query in rows:
        if not user_query:
            continue
        idx = match_question(_topic_tokens(user_query), curated_tokens, df)
        if idx is None:
            continue
        buckets.setdefault(idx, set()).add(user_id)

    # Demand-ranked indices: most distinct users first, ties by curated order.
    ranked = sorted(buckets.keys(), key=lambda i: (-len(buckets[i]), i))

    ordered: list[int] = list(ranked)
    seen = set(ranked)
    for i in range(len(curated)):  # fill remaining slots in curated order
        if i not in seen:
            ordered.append(i)
    return [curated[i] for i in ordered[:limit]]


# ---------------------------------------------------------------------------
# DB + cache wrappers
# ---------------------------------------------------------------------------
def compute_from_db(db, curated: list[str], limit: int = 10,
                    days: Optional[int] = None) -> list[str]:
    """Scan ChatHistory and rank. `days` limits to recent questions (None =
    all-time). Never raises -- on any error returns the curated pool order."""
    from models import ChatHistory
    try:
        q = db.query(ChatHistory.user_id, ChatHistory.user_query)
        if days:
            from datetime import datetime, timedelta, timezone
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            q = q.filter(ChatHistory.timestamp >= cutoff)
        rows = q.all()
        return rank_questions(rows, curated, limit)
    except Exception as e:  # pragma: no cover - defensive
        print(f"[POPULAR] compute_from_db failed: {e}")
        return list(curated[:limit])


def _redis_get() -> Optional[list[str]]:
    try:
        from cache import query_cache
        raw = query_cache.l2.get(_REDIS_KEY)
        if raw:
            val = json.loads(raw)
            if isinstance(val, list) and val:
                return val
    except Exception:
        pass
    return None


def _redis_set(questions: list[str]) -> None:
    try:
        from cache import query_cache
        query_cache.l2.set(_REDIS_KEY, json.dumps(questions))
    except Exception:
        pass


def recompute(db, curated: list[str], limit: int = 10,
              days: Optional[int] = None) -> list[str]:
    """Force a fresh scan, update both cache tiers, return the new list.
    Used by the daily cron endpoint."""
    questions = compute_from_db(db, curated, limit, days)
    _mem["questions"], _mem["at"] = questions, time.time()
    _redis_set(questions)
    return questions


def get_top_questions(db, curated: list[str], limit: int = 10) -> list[str]:
    """Serve the cached Top-N (in-process -> Redis -> compute). Always returns
    `limit` questions; degrades to the curated pool if the scan yields nothing."""
    if _mem["questions"] and (time.time() - _mem["at"]) < _TTL_SECONDS:
        return _mem["questions"]

    cached = _redis_get()
    if cached:
        _mem["questions"], _mem["at"] = cached, time.time()
        return cached

    return recompute(db, curated, limit)
