"""One answer per (draft, rules) pair, shared across backend instances.

WHY. A PI uploaded one unchanged Project Summary PDF seventy times over seven
rounds and got 86%, 93% and 100%. Six of seven rules were identical in EVERY
run; the seventh -- "The Overview states the methods to be employed" -- is a
genuine 50/50 judgement about a draft that states what the work addresses and
never names a method. With 7 rules, half a rule is 7 percentage points, so one
undecided row IS the whole 93->86 swing.

EVERY CHEAPER FIX WAS TRIED AND MEASURED, and none closed it:
  - `SEMANTIC_VOTES = 3`, median-merged, is already in place; this IS the
    smoothed number.
  - 3 vs 5 vs 7 votes: identical stability; 7 just costs 2 seconds.
  - a fixed sampling seed -- re-tested after discovering `generate_json` had
    been silently dropping it, so the first test had measured nothing: still
    86/93/100 with the seed genuinely reaching Vertex.
  - resolving a split downward: measurably worse (4 distinct scores, 3 moving
    rules, because one dissenting reader of three flips a rule).
  - a >=2-of-5 proofreader threshold: zero noise, but recall 8/10 -> 5/10. The
    votes are correlated, not independent.
  - filtering our own PDF artifacts: fixed the WORDING rows completely, and did
    not touch the score.

So the remedy is not to ask again.

WHAT IS CACHED, AND WHAT DELIBERATELY IS NOT. Only the MODEL layer -- the
per-requirement statuses `_voted_batch` returns and the proofreader's rows.
Every deterministic check (headings, page limits, attachment presence, the
mechanical and language rules) runs on every request, and the score is
recomputed in code from both halves. A fix to a deterministic check is live
immediately, and golden rule 1 holds: the authoritative half is never cached.

CLAUDE.md's rule is "cache the expensive (LLM answers), never the authoritative
(budgets, statuses, verdicts)". This obeys the first clause and protects what
the second is FOR -- verdicts are recomputed on every load so that improving a
rule retroactively fixes every proposal. The key covers the rules themselves
(ids, labels, source text, tier, rulebook, scored, prohibition, and ORDER), so
editing any rule makes the old entry unreachable immediately rather than at
expiry.

THE DRAFT IS NEVER STORED. The key is a SHA-256 of the text, so nothing here can
reconstruct a manuscript (its own test, in both tiers). The stored VALUE is the
findings -- the rows already on the PI's screen.

TWO TIERS, and L2 is the one that makes this worth having. An IN-PROCESS cache
CANNOT help ORA: the backend runs up to 20 instances, so two reviewers land on
different processes and different notes, and the feature would be useless for
exactly the case it was built for. L2 is Redis via `cache.L2Cache` -- the same
class the chat answer cache uses, which already degrades to "no L2" when Redis
is unreachable (local dev, and any outage). Every L2 call is wrapped: a cache is
an optimisation and must never be the reason a review fails (golden rule 3).

THE COST, STATED PLAINLY: this freezes whichever answer came first. A borderline
rule locks at that reading for the life of the entry, and the screen then looks
certain about something it is not -- which is why the `borderline` tag still
travels with the cached row, and why the TTL is short rather than long.
"""

from __future__ import annotations

import hashlib
import json
import os
import logging
import threading
from typing import Any, Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# Long enough to cover a review session -- the actual complaint was the same
# file read twice minutes apart -- and short enough that a coin-flip is not
# frozen for a working day.
TTL_SECONDS = 30 * 60
MAX_ENTRIES = 256

_l1: TTLCache = TTLCache(maxsize=MAX_ENTRIES, ttl=TTL_SECONDS)
_lock = threading.Lock()


def _make_l2():
    """Redis, or None. Never raises: no Redis is a supported configuration."""
    try:
        from cache import L2Cache
        l2 = L2Cache(ttl=TTL_SECONDS)
        return l2 if getattr(l2, "_connected", False) else None
    except Exception as exc:
        logger.warning("[REVIEW-CACHE] no L2: %s", exc)
        return None


_l2 = _make_l2()


# THE ENGINE ITSELF IS PART OF THE KEY.
#
# Found the day the cache shipped: the key covered the draft and the rules but
# NOT the code, so a fix to `text_match.quote_in` that took a real Project
# Description from 13/16 to 16/16 could not reach a cached result -- the backend
# had to be restarted to see it. In production that is worse, because a deploy
# does not clear Redis: for the life of every entry, reviewers keep being served
# answers computed by the code that was just replaced.
#
# Derived from the SOURCE of the modules that decide a review, rather than a
# version constant someone has to remember to bump. Within one deploy every
# instance computes the same value and shares the cache; across deploys it
# changes and stale answers become unreachable. Read once, at import.
_ENGINE_FILES = (
    "draft_review.py",        # the reviewer, the merge, the score
    "proofread.py",           # the wording rows
    "text_match.py",          # the quote gate -- where the apostrophe bug lived
    "rulebook_baseline.py",   # the rules themselves
    "rulebook_checks.py",     # the deterministic checks
    "generic_checks.py",
    "section_guidance.py",
)


def _engine_fingerprint() -> str:
    """A short digest of the review engine's source. Degrades, never raises."""
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in _ENGINE_FILES:
        try:
            with open(os.path.join(here, name), "rb") as fh:
                h.update(fh.read())
        except Exception:          # a missing file must not break a review
            h.update(name.encode("utf-8"))
    return h.hexdigest()[:12]


ENGINE_FINGERPRINT = _engine_fingerprint()


def _rule_fingerprint(reqs: list[dict]) -> str:
    """Everything about the rules that could change the right answer.

    ORDER included: the reviewer sees them as a numbered list. `scored` and
    `flag_if_present` are here because both change how a row is JUDGED, not just
    how it is displayed -- a prohibition is read with the opposite vocabulary.
    """
    parts = [[r.get("id"), r.get("label"), r.get("source"), r.get("kind"),
              r.get("tier"), r.get("rulebook"), bool(r.get("scored")),
              bool(r.get("flag_if_present"))] for r in reqs]
    return hashlib.sha256(
        json.dumps(parts, sort_keys=False, default=str).encode("utf-8")
    ).hexdigest()


def key(text: str, *parts: Any, reqs: Optional[list[dict]] = None) -> str:
    """A one-way key. The draft goes in as a hash and never comes back out."""
    h = hashlib.sha256(" ".join((text or "").split()).encode("utf-8")).hexdigest()
    tail = "|".join(str(p) for p in parts)
    return (f"rc:{ENGINE_FINGERPRINT}|{h}|{tail}|"
            f"{_rule_fingerprint(reqs or [])}")


def get(k: str):
    """L1, then L2 (promoting a hit into L1 so the next read is local)."""
    with _lock:
        hit = _l1.get(k)
    if hit is not None:
        return hit
    if _l2 is None:
        return None
    try:
        raw = _l2.get(k)
    except Exception as exc:                 # golden rule 3
        logger.warning("[REVIEW-CACHE] L2 get failed: %s", exc)
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except Exception:
        return None
    with _lock:
        _l1[k] = value
    return value


def put(k: str, value):
    """Store a real answer in both tiers. Callers must NOT store a fallback."""
    with _lock:
        _l1[k] = value
    if _l2 is not None:
        try:
            _l2.set(k, json.dumps(value, default=str))
        except Exception as exc:             # golden rule 3
            logger.warning("[REVIEW-CACHE] L2 set failed: %s", exc)
    return value


def clear() -> None:
    with _lock:
        _l1.clear()


def _snapshot() -> dict:
    """For tests: everything L1 holds, so a test can assert the draft is not."""
    with _lock:
        return dict(_l1)
