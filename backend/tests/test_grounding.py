"""Tests for Layer 3 grounding verification: regenerate-then-refuse.

Layer 3 used to only append a soft disclaimer when an answer was poorly
grounded in the KB. It now *verifies*: a weak answer is regenerated once with a
strict prompt, and if it is still weak it is refused rather than shown.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_grounding.py -v
"""
import vertex_agent
from vertex_agent import _evaluate_grounding


# ===========================================================================
# _evaluate_grounding -- decides whether an answer is KB-grounded ("ok"/"weak")
# ===========================================================================

def test_no_sources_and_no_coverage_is_weak():
    """The core bad case: an answer with zero KB chunks and zero coverage."""
    assert _evaluate_grounding("Pre-award proposals are due Friday.", 0, 0.0, False) == "weak"


def test_two_chunks_is_ok():
    """Two or more cited KB chunks clears the grounding bar."""
    assert _evaluate_grounding("Pre-award proposals are due Friday.", 2, 0.0, False) == "ok"


def test_high_coverage_is_ok():
    """Enough of the answer backed by KB text clears the bar even with 1 chunk."""
    assert _evaluate_grounding("Pre-award proposals are due Friday.", 1, 0.45, False) == "ok"


def test_attached_context_is_ok():
    """An answer drawing on an uploaded file / profile is not a KB hallucination."""
    assert _evaluate_grounding("Your uploaded budget lists $50,000.", 0, 0.0, True) == "ok"


def test_honest_deflection_is_ok():
    """An honest 'I don't have this' must NOT be regenerated/refused -- a low
    grounding score on an honest non-answer is correct, not a hallucination."""
    text = ("Based on the information I have access to, I don't have specific "
            "details on that. For more, contact ORA at (443) 885-4044.")
    assert _evaluate_grounding(text, 0, 0.0, False) == "ok"


def test_greeting_is_ok():
    """Greetings / security / outage replies need no KB grounding."""
    assert _evaluate_grounding("Hey! I'm ORA Navigator, here to help.", 0, 0.0, False) == "ok"


def test_empty_text_is_weak():
    assert _evaluate_grounding("", 0, 0.0, False) == "weak"


def test_refusal_message_reads_as_ok():
    """The refusal text itself must evaluate as 'ok' so re-checking it never
    loops back into another regenerate/refuse cycle."""
    assert _evaluate_grounding(vertex_agent._REFUSAL_MSG, 0, 0.0, False) == "ok"


# ===========================================================================
# _run_verified -- orchestrates: deliver / regenerate / refuse
# ===========================================================================

def _result(text="", chunks=0, coverage=0.0, citations=None, grounded_corpus="",
            kb_fail=False, outage=False, error=None):
    """Build the result dict that _do_agent_pass yields for one agent round-trip."""
    return {"text": text, "chunks": chunks, "coverage": coverage,
            "citations": citations or [], "grounded_corpus": grounded_corpus,
            "kb_fail": kb_fail, "outage": outage, "error": error}


def _fake_passes(pass1, pass2=None):
    """A stand-in for _do_agent_pass (the real one needs the ADK over the network).

    Returns `pass1` for the normal call and `pass2` for the regeneration call,
    which is recognised by the strict-prompt prefix on its message.
    """
    def fake(message, user_id, session_id, context="", model="",
             memory_context="", retried=False):
        data = pass2 if message.startswith(vertex_agent._STRICT_PREFIX) else pass1
        yield {"type": "_result", "data": data}
    return fake


def _drive(monkeypatch, pass1, pass2=None):
    """Run _run_verified with a faked agent and return the events it yielded."""
    monkeypatch.setattr(vertex_agent, "_do_agent_pass", _fake_passes(pass1, pass2))
    monkeypatch.setattr(vertex_agent, "_create_session", lambda *a, **k: "regen-session")
    return list(vertex_agent._run_verified("What is the F&A rate?", "user-1", "sess-1"))


def _final(events):
    """The content of the last done/error event."""
    tail = [e for e in events if e["type"] in ("done", "error")]
    assert tail, f"no done/error event in {events}"
    return tail[-1]["content"]


def test_grounded_first_pass_is_delivered(monkeypatch):
    """A well-grounded first answer is delivered unchanged -- no regeneration."""
    events = _drive(monkeypatch, _result("Pre-award proposals route through ORA.",
                                         chunks=3, coverage=0.7))
    assert "Pre-award proposals route through ORA." in _final(events)


def test_weak_first_pass_regenerates_and_delivers_second(monkeypatch):
    """A weak first answer triggers a strict regeneration; the grounded second
    answer is delivered and the weak first answer is discarded."""
    events = _drive(
        monkeypatch,
        _result("Vague ungrounded guess.", chunks=0, coverage=0.0),
        _result("The on-campus F&A rate is in the rate agreement.", chunks=4, coverage=0.8),
    )
    final = _final(events)
    assert "rate agreement" in final
    assert "Vague ungrounded guess" not in final


def test_weak_after_regeneration_is_refused(monkeypatch):
    """When the regenerated answer is also weak, the answer is refused outright
    rather than shown."""
    events = _drive(
        monkeypatch,
        _result("Vague ungrounded guess.", chunks=0, coverage=0.0),
        _result("Another ungrounded guess.", chunks=0, coverage=0.0),
    )
    assert _final(events) == vertex_agent._REFUSAL_MSG


def test_honest_deflection_is_delivered_not_refused(monkeypatch):
    """An honest 'I don't have this' first answer is delivered as-is -- it must
    NOT be regenerated or refused even with zero KB chunks."""
    honest = ("Based on the information I have access to, I don't have that "
              "specific figure. Please contact ORA at 443-885-4044.")
    events = _drive(monkeypatch, _result(honest, chunks=0, coverage=0.0),
                    _result("SHOULD NOT BE USED", chunks=0, coverage=0.0))
    final = _final(events)
    assert "I don't have that specific figure" in final
    assert "SHOULD NOT BE USED" not in final


def test_outage_surfaces_an_error(monkeypatch):
    """An ADK outage surfaces an error event, not a refusal."""
    events = _drive(monkeypatch, _result(outage=True))
    tail = [e for e in events if e["type"] in ("done", "error")]
    assert tail[-1]["type"] == "error"
    assert tail[-1]["content"] == vertex_agent._OUTAGE_MSG
