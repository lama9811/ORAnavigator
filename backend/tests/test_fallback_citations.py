"""Tests for Part C: deterministic citation fallback.

When a real ORA content answer comes back with no Sources (Gemini's grounding
metadata is empty even though the answer is correct), the backend runs a live
KB search and attaches the matching docs as Sources -- so a curated "top 10"
question never renders sourceless.

Two pure units:
  * _wants_fallback_citations(question, result) -> bool   (the guards)
  * _fallback_citations(query) -> [{title, url}]           (retrieve + resolve)

Run from the backend/ directory:
    cd backend && python -m pytest tests/test_fallback_citations.py -v
"""
import vertex_agent
from vertex_agent import _fallback_citations, _wants_fallback_citations


def _result(**over):
    """A baseline raw-pass result dict, overridable per test."""
    base = {
        "text": "For IRB approval, allow at least 30 days for review.",
        "citations": [],
        "kb_fail": False,
        "outage": False,
        "error": None,
    }
    base.update(over)
    return base


# ===========================================================================
# _fallback_citations -- live KB search resolved to {title, url}
# ===========================================================================

def test_resolves_searched_titles_to_urls(monkeypatch):
    """Searched doc titles are resolved to clickable URLs via the KB URL map."""
    monkeypatch.setattr(
        vertex_agent, "_search_kb_titles",
        lambda q, top_k=5: ["IRB Approval Process", "Human Subjects Research"],
    )
    monkeypatch.setattr(
        vertex_agent, "_get_kb_url_map",
        lambda: {
            "irb approval process": "https://morgan.edu/irb",
            "human subjects research": "https://morgan.edu/hsr",
        },
    )
    assert _fallback_citations("how long does IRB approval take") == [
        {"title": "IRB Approval Process", "url": "https://morgan.edu/irb"},
        {"title": "Human Subjects Research", "url": "https://morgan.edu/hsr"},
    ]


def test_empty_when_search_finds_nothing(monkeypatch):
    """No match -> no guess. Returns an empty list, never a blank source."""
    monkeypatch.setattr(vertex_agent, "_search_kb_titles", lambda q, top_k=5: [])
    monkeypatch.setattr(vertex_agent, "_get_kb_url_map", lambda: {"x": "y"})
    assert _fallback_citations("how long does IRB approval take") == []


def test_skips_titles_with_no_resolvable_url(monkeypatch):
    """A searched title that isn't in the URL map is dropped (not clickable)."""
    monkeypatch.setattr(
        vertex_agent, "_search_kb_titles",
        lambda q, top_k=5: ["Known Doc", "Mystery Doc"],
    )
    monkeypatch.setattr(
        vertex_agent, "_get_kb_url_map",
        lambda: {"known doc": "https://morgan.edu/known"},
    )
    assert _fallback_citations("anything") == [
        {"title": "Known Doc", "url": "https://morgan.edu/known"},
    ]


def test_dedupes_and_caps_at_five(monkeypatch):
    """Duplicate titles collapse; the list never exceeds the 5-source UI cap."""
    monkeypatch.setattr(
        vertex_agent, "_search_kb_titles",
        lambda q, top_k=5: ["A", "A", "B", "C", "D", "E", "F"],
    )
    monkeypatch.setattr(
        vertex_agent, "_get_kb_url_map",
        lambda: {k.lower(): f"https://morgan.edu/{k.lower()}" for k in "ABCDEF"},
    )
    out = _fallback_citations("anything")
    assert len(out) == 5
    assert [c["title"] for c in out] == ["A", "B", "C", "D", "E"]


def test_returns_empty_on_search_exception(monkeypatch):
    """A retrieval failure degrades gracefully -- the answer still delivers."""
    def _boom(q, top_k=5):
        raise RuntimeError("discovery engine down")
    monkeypatch.setattr(vertex_agent, "_search_kb_titles", _boom)
    monkeypatch.setattr(vertex_agent, "_get_kb_url_map", lambda: {"a": "b"})
    assert _fallback_citations("anything") == []


# ===========================================================================
# _wants_fallback_citations -- the guards at the wire-in site
# ===========================================================================

def test_wants_fallback_for_uncited_ora_answer():
    assert _wants_fallback_citations("How long does IRB approval take?", _result()) is True


def test_skips_when_citations_already_present():
    r = _result(citations=[{"title": "X", "url": "https://x"}])
    assert _wants_fallback_citations("How long does IRB approval take?", r) is False


def test_skips_small_talk():
    assert _wants_fallback_citations("hi", _result()) is False
    assert _wants_fallback_citations("thanks!", _result()) is False


def test_skips_personal_recall():
    # Personal answers come from the profile/chat history, not the KB — no Sources.
    assert _wants_fallback_citations("what department am I in?", _result()) is False
    assert _wants_fallback_citations("what's my role?", _result()) is False
    assert _wants_fallback_citations("who am I?", _result()) is False
    # A real ORA question is unaffected.
    assert _wants_fallback_citations("what is the F&A rate?", _result()) is True


def test_skips_on_kb_fail():
    assert _wants_fallback_citations("How long does IRB take?", _result(kb_fail=True)) is False


def test_skips_on_outage():
    assert _wants_fallback_citations("How long does IRB take?", _result(outage=True)) is False


def test_skips_on_error():
    assert _wants_fallback_citations("How long does IRB take?", _result(error="boom")) is False
