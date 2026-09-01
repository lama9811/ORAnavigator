"""One answer per (draft, rules) pair, shared across backend instances.

WHY. A PI uploaded one unchanged Project Summary PDF seventy times over seven
rounds and got 86%, 93% and 100%. Six of the seven rules were identical in every
run; the seventh -- "The Overview states the methods to be employed" -- is a
genuine 50/50 judgement, and with 7 rules half a rule is 7 percentage points.

EVERY CHEAPER FIX WAS TRIED AND MEASURED. None closed it:
  - `SEMANTIC_VOTES = 3` median-merged is already in place; this IS the smoothed
    number.
  - 3 vs 5 vs 7 votes: identical stability, 7 just costs 2 seconds.
  - a fixed sampling seed, RE-TESTED after finding that `generate_json` had been
    silently dropping it: still 86/93/100.
  - resolving a split downward: worse (4 distinct scores, 3 moving rules).
  - a >=2-of-5 proofreader threshold: zero noise but recall 8/10 -> 5/10,
    because the votes are correlated rather than independent.
  - filtering our own PDF artifacts: fixed the WORDING entirely, not the score.

So the answer is not to ask again. THE TWO OBJECTIONS ARE DESIGNED AGAINST:
  1. RETENTION -- the key is a one-way SHA-256, so the draft is never stored.
  2. STALE VERDICTS -- the key covers the RULES, so editing one busts it at once,
     which is what CLAUDE.md's "never cache the authoritative" rule protects.
     Only the model layer is cached; every deterministic check still runs.

AND THE THIRD, learned the hard way: an in-process cache CANNOT help ORA. The
backend runs up to 20 instances, so two reviewers hit different processes and
different notes. L2 is Redis, shared, reusing `cache.L2Cache` -- the same class
the chat answer cache uses, which already degrades to "no L2" when Redis is
down (the local-dev case, and its own test below).
"""
import json

import pytest

from services import draft_review as dr
from services import review_cache as rc


@pytest.fixture(autouse=True)
def _clear():
    rc.clear()
    yield
    rc.clear()


def _req(rid="r", source="S"):
    return {"id": rid, "label": "L", "source": source, "kind": "semantic",
            "scored": True, "section": "s"}


_SPAN = {"text": "Overview. We will study the thing.", "start": 0, "end": 34, "marker": "m"}
_SECTIONS = {"s": {"label": "S"}}


def _counting_model(monkeypatch, status="partial"):
    calls = {"n": 0}

    def fake(prompt, **kw):
        calls["n"] += 1
        return {"findings": [{"id": "r", "status": status, "note": "N",
                              "evidence": "We will study the thing",
                              "suggestion": "s"}]}
    monkeypatch.setattr(dr.gemini_client, "generate_json", fake)
    return calls


# ── the contract ────────────────────────────────────────────────────────────

def test_the_same_draft_and_rules_are_only_asked_once(monkeypatch):
    calls = _counting_model(monkeypatch)
    a = dr._voted_batch("s", _SPAN, [_req()], _SECTIONS, "SOL", 3)
    n = calls["n"]
    b = dr._voted_batch("s", _SPAN, [_req()], _SECTIONS, "SOL", 3)
    assert calls["n"] == n, "the second read must not call the model"
    assert [f["status"] for f in a] == [f["status"] for f in b]


def test_editing_a_rule_busts_the_cache(monkeypatch):
    """What CLAUDE.md's caching rule exists to protect: improving a rule must
    take effect immediately, not when a TTL expires."""
    calls = _counting_model(monkeypatch)
    dr._voted_batch("s", _SPAN, [_req(source="S")], _SECTIONS, "SOL", 3)
    n = calls["n"]
    dr._voted_batch("s", _SPAN, [_req(source="S, amended")], _SECTIONS, "SOL", 3)
    assert calls["n"] > n


def test_editing_the_draft_busts_the_cache(monkeypatch):
    calls = _counting_model(monkeypatch)
    dr._voted_batch("s", _SPAN, [_req()], _SECTIONS, "SOL", 3)
    n = calls["n"]
    dr._voted_batch("s", dict(_SPAN, text="Overview. Something else."),
                    [_req()], _SECTIONS, "SOL", 3)
    assert calls["n"] > n


def test_the_cache_never_holds_the_draft_text(monkeypatch):
    """Objection 1. A one-way hash goes in; the manuscript does not."""
    _counting_model(monkeypatch)
    secret = "Overview. My unpublished idea about zwitterionic membranes."
    dr._voted_batch("s", dict(_SPAN, text=secret), [_req()], _SECTIONS, "SOL", 3)
    blob = repr(rc._snapshot())
    assert "zwitterionic" not in blob and "unpublished" not in blob
    assert secret not in blob


def test_a_failed_read_is_not_cached(monkeypatch):
    """An outage must not freeze in as this draft's verdict."""
    monkeypatch.setattr(dr.gemini_client, "generate_json", lambda p, **k: None)
    dr._voted_batch("s", _SPAN, [_req()], _SECTIONS, "SOL", 3)
    calls = _counting_model(monkeypatch)
    out = dr._voted_batch("s", _SPAN, [_req()], _SECTIONS, "SOL", 3)
    assert calls["n"] > 0
    assert [f["status"] for f in out] == ["partial"]


# ── L2: the half that makes it useful in production ─────────────────────────

class _FakeRedis:
    """Stands in for cache.L2Cache: same three methods, same string values."""

    def __init__(self):
        self.store = {}
        self.sets = 0

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.sets += 1
        self.store[key] = value
        return True


def test_a_second_instance_reuses_the_first_ones_answer(monkeypatch):
    """THE REASON L2 EXISTS. An in-process cache cannot help ORA: the backend
    runs up to 20 instances, so two reviewers land on different processes. A
    fresh process is simulated by clearing L1 ONLY -- L2 must still answer."""
    shared = _FakeRedis()
    monkeypatch.setattr(rc, "_l2", shared)
    calls = _counting_model(monkeypatch)
    first = dr._voted_batch("s", _SPAN, [_req()], _SECTIONS, "SOL", 3)
    n = calls["n"]

    rc._l1.clear()                      # a different instance: empty memory
    second = dr._voted_batch("s", _SPAN, [_req()], _SECTIONS, "SOL", 3)
    assert calls["n"] == n, "the second instance must not call the model again"
    assert [f["status"] for f in second] == [f["status"] for f in first]


def test_everything_still_works_with_no_redis(monkeypatch):
    """Local dev, and any Redis outage. L2 absent must degrade to L1 only, never
    raise -- the same contract cache.L2Cache already keeps for the chat cache."""
    monkeypatch.setattr(rc, "_l2", None)
    calls = _counting_model(monkeypatch)
    dr._voted_batch("s", _SPAN, [_req()], _SECTIONS, "SOL", 3)
    n = calls["n"]
    dr._voted_batch("s", _SPAN, [_req()], _SECTIONS, "SOL", 3)
    assert calls["n"] == n, "L1 alone must still serve a repeat"


def test_a_broken_redis_never_breaks_a_review(monkeypatch):
    """Golden rule 3. A cache is an optimisation; it must never be the reason a
    review fails."""
    class Exploding:
        def get(self, key):
            raise RuntimeError("redis down")

        def set(self, key, value):
            raise RuntimeError("redis down")

    monkeypatch.setattr(rc, "_l2", Exploding())
    _counting_model(monkeypatch)
    out = dr._voted_batch("s", _SPAN, [_req()], _SECTIONS, "SOL", 3)
    assert [f["status"] for f in out] == ["partial"]


def test_what_goes_into_redis_is_json_and_carries_no_draft(monkeypatch):
    shared = _FakeRedis()
    monkeypatch.setattr(rc, "_l2", shared)
    _counting_model(monkeypatch)
    dr._voted_batch("s", dict(_SPAN, text="Overview. A zwitterionic idea."),
                    [_req()], _SECTIONS, "SOL", 3)
    assert shared.sets == 1
    (key, raw), = shared.store.items()
    json.loads(raw)                       # must be valid JSON
    assert "zwitterionic" not in raw and "zwitterionic" not in key


# ── A CODE FIX MUST NOT BE MASKED BY THE CACHE ──────────────────────────────
#
# Found 2026-09-01, immediately after the curly-apostrophe fix. The key covered
# the draft text and the rules -- not the ENGINE. So a fix that turned a real
# Project Description from 13/16 to 16/16 could not reach a cached result: the
# backend had to be restarted to see it. In production that is worse, because a
# deploy does not clear Redis: for the life of every entry, reviewers keep being
# served answers computed by the code that was just replaced.
#
# The fingerprint is derived from the SOURCE of the modules that decide a
# review, so it cannot be forgotten the way a hand-bumped version constant can.
# Within one deploy every instance computes the same value and shares the cache;
# across deploys it changes and stale answers become unreachable.

def test_the_key_changes_when_the_engine_changes(monkeypatch):
    reqs = [_req()]
    before = rc.key("some draft", "s", "SOL", 3, reqs=reqs)
    monkeypatch.setattr(rc, "ENGINE_FINGERPRINT", "pretend-the-code-changed")
    after = rc.key("some draft", "s", "SOL", 3, reqs=reqs)
    assert before != after, "a code change must make old entries unreachable"


def test_the_engine_fingerprint_is_stable_within_a_process():
    """It must not move between calls, or nothing would ever be a cache hit."""
    assert rc.ENGINE_FINGERPRINT == rc._engine_fingerprint()
    assert len(rc.ENGINE_FINGERPRINT) == 12


def test_the_fingerprint_survives_an_unreadable_source_file(monkeypatch):
    """Degrades, never raises: a cache is an optimisation and must never be the
    reason a review fails (golden rule 3)."""
    monkeypatch.setattr(rc, "_ENGINE_FILES", ("no_such_module_xyz.py",))
    assert isinstance(rc._engine_fingerprint(), str)
