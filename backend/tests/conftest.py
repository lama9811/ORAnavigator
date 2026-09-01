import sys
from pathlib import Path

import pytest

# Make backend/ importable so tests can `import kb_browser` regardless of
# how pytest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Make the repo root importable so tests can `import adk_agent.*`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


@pytest.fixture(autouse=True)
def _no_live_gemini(monkeypatch):
    """Force the advisory AI layer OFFLINE for every test by default.

    The new Draft Critic / Deadline Watcher AI layers call
    services.gemini_client; on a dev machine WITH ADC that would make real,
    slow, non-deterministic Gemini calls during the unit suite. Pinning
    get_client() to None makes generate_json/generate_text return None fast,
    so every AI path falls back to its deterministic output (the behavior the
    existing tests assert). Tests that want to exercise the AI path override
    this by patching gemini_client.generate_json / the service composer, or by
    patching gemini_client._generate (which bypasses get_client)."""
    try:
        from services import gemini_client
        monkeypatch.setattr(gemini_client, "get_client", lambda: None)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _empty_review_cache():
    """A fresh review cache per test, and NO Redis.

    `services/review_cache` is keyed on (draft text, rules), so two tests
    reviewing the same fixture text would otherwise share an answer -- the second
    asserting against a reading it never made. L2 is pinned off so the suite can
    never reach a real Redis, and cleared both ways so an ordering change cannot
    make it green by accident."""
    try:
        from services import review_cache
    except Exception:
        yield
        return
    saved, review_cache._l2 = review_cache._l2, None
    review_cache.clear()
    yield
    review_cache.clear()
    review_cache._l2 = saved
