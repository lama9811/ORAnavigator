"""Tests for Layer 3 grounding verification: regenerate-then-deliver.

When an answer is not positively verified as KB-grounded, Layer 3 regenerates
it once under a strict KB-only prompt. The regenerated answer is then trusted
and delivered -- Gemini returns its groundingChunks metadata unreliably (it is
frequently empty even for a correct, grounded answer; verified live: the same
good answer came back with chunk counts 7, 0, 0, 0 on four identical runs), so
refusing on a low chunk count refused good answers far more often than it caught
a genuine miss. Only an empty or errored regeneration is refused.

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


def test_weak_after_regeneration_is_still_delivered(monkeypatch):
    """Regression test for the over-refusal bug. The strict regeneration's
    answer is trusted and delivered even when its grounding metadata is weak --
    Gemini reports groundingChunks unreliably, so a non-empty strict-regenerated
    answer must NOT be refused on a low chunk count."""
    events = _drive(
        monkeypatch,
        _result("Vague first answer.", chunks=0, coverage=0.0),
        _result("The off-campus F&A rate is 26%.", chunks=0, coverage=0.0),
    )
    final = _final(events)
    assert "off-campus F&A rate is 26%" in final
    assert final != vertex_agent._REFUSAL_MSG


def test_empty_regeneration_is_refused(monkeypatch):
    """If the strict regeneration genuinely produces no answer, the response is
    refused -- the safeguard for a real failure is preserved."""
    events = _drive(
        monkeypatch,
        _result("Vague first answer.", chunks=0, coverage=0.0),
        _result("", chunks=0, coverage=0.0),
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


# ===========================================================================
# Personal-recall short-circuit -- a question that asks the bot to recall
# something the user said about themselves in this conversation must NOT
# trigger Layer 3's KB-only regeneration. Those facts live in the chat
# history, not the KB, so regenerating under the strict KB-only prompt
# discards the correct answer and replies "I don't have that information."
# ===========================================================================

def test_personal_recall_question_skips_regeneration(monkeypatch):
    """Weak first answer to a personal-recall question is delivered, not
    regenerated. The user told the bot they're in Biology earlier in the chat;
    asking 'What department am I in?' must surface the recall answer, not the
    strict-prefix refusal."""
    monkeypatch.setattr(
        vertex_agent, "_do_agent_pass",
        _fake_passes(
            _result("You told me you're in the Biology department.",
                    chunks=0, coverage=0.0),
            _result("I do not have information about your specific department.",
                    chunks=0, coverage=0.0),
        ),
    )
    monkeypatch.setattr(vertex_agent, "_create_session",
                        lambda *a, **k: "regen-session")
    events = list(vertex_agent._run_verified(
        "What department am I in?", "user-1", "sess-1"))
    final = _final(events)
    assert "Biology" in final
    assert "do not have information" not in final


def test_non_recall_question_still_regenerates(monkeypatch):
    """Regression guard: a normal KB question whose first answer is weak
    must still be regenerated -- the personal-recall short-circuit must not
    let ungrounded KB-claims through."""
    monkeypatch.setattr(
        vertex_agent, "_do_agent_pass",
        _fake_passes(
            _result("Vague ungrounded guess.", chunks=0, coverage=0.0),
            _result("The on-campus F&A rate is in the rate agreement.",
                    chunks=4, coverage=0.8),
        ),
    )
    monkeypatch.setattr(vertex_agent, "_create_session",
                        lambda *a, **k: "regen-session")
    events = list(vertex_agent._run_verified(
        "What is the F&A rate?", "user-1", "sess-1"))
    final = _final(events)
    assert "rate agreement" in final
    assert "Vague ungrounded guess" not in final


def test_is_personal_recall_matches_self_reference_questions():
    """Unit test for the personal-recall detector. These phrasings all ask
    the bot to recall something the user said about themselves."""
    matches = [
        "What department am I in?",
        "What sponsor did I tell you I work with?",
        "Remind me what department I'm in.",
        "What's my upcoming deadline?",
        "What is my role on the NSF award?",
        "Did I mention my IRB protocol?",
        "What do you remember about me?",
        "Tell me about myself based on what I've said.",
        "Who am I working with on this grant?",
    ]
    for q in matches:
        assert vertex_agent._is_personal_recall(q), \
            f"should detect as personal-recall: {q!r}"


def test_is_personal_recall_rejects_kb_questions():
    """Regression guard: normal KB questions must NOT match the recall
    detector, or they will skip Layer 3 and let ungrounded KB-claims through."""
    non_matches = [
        "What is Morgan State's F&A rate?",
        "How long does IRB approval take?",
        "Where do I find IACUC SOPs?",
        "Who handles post-award setup?",
        "What's the deadline for the NSF CAREER award?",
        "Tell me about Research Security.",
    ]
    for q in non_matches:
        assert not vertex_agent._is_personal_recall(q), \
            f"should NOT detect as personal-recall: {q!r}"


# ===========================================================================
# Empty Pass 1 with KB chunks -- a vague / typo'd query like "also abou the
# preawards" makes the ADK call the KB search tool (finding real Pre-Award
# docs) but then emit no text. The old behavior gave up with the generic
# "couldn't generate" error even though usable KB grounding was already in
# hand. The fix retries via Pass 2's strict regeneration -- the strict
# prefix tells the model to answer fully from the KB context it already
# has, which is the exact recovery path that's needed.
# ===========================================================================

def test_empty_first_pass_with_chunks_triggers_regeneration(monkeypatch):
    """Pass 1 returns no text but found 5 KB chunks (typical of vague or
    typo'd queries where the model called the search tool but failed to
    synthesize an answer). Pass 2's strict regeneration should fire and
    its answer must be delivered."""
    events = _drive(
        monkeypatch,
        _result("", chunks=5, coverage=0.0,
                citations=[{"title": "Pre-Award — Overview", "url": "x"}]),
        _result("Pre-award covers proposal preparation, budgets, and F&A rates.",
                chunks=4, coverage=0.7),
    )
    final = _final(events)
    assert "Pre-award" in final
    assert "couldn't generate" not in final


def test_empty_first_pass_with_no_chunks_refuses_gracefully(monkeypatch):
    """Regression guard: empty text AND zero KB chunks (e.g. asking to confirm a
    non-existent SOP 37) must NOT surface the dead-end 'couldn't generate /
    rephrase' error. It degrades to the honest refusal (_REFUSAL_MSG) so the
    reply is always useful and routes to ORA -- without burning a Pass 2 call on
    a hopeless case."""
    monkeypatch.setattr(
        vertex_agent, "_do_agent_pass",
        _fake_passes(_result("", chunks=0, coverage=0.0)),
    )
    monkeypatch.setattr(vertex_agent, "_create_session",
                        lambda *a, **k: "regen-session")
    events = list(vertex_agent._run_verified(
        "garbled query", "user-1", "sess-1"))
    tail = [e for e in events if e["type"] in ("done", "error")]
    assert tail, "expected a done/error event"
    assert tail[-1]["type"] == "done"
    assert tail[-1]["content"] == vertex_agent._REFUSAL_MSG
    assert "couldn't generate" not in tail[-1]["content"]


# ===========================================================================
# _check_identifier_faithfulness -- soft guardrail that flags specific
# identifiers (SOP/FWA/EIN/UEI numbers, dates, dollar amounts, emails, phones,
# and F&A rates) that the bot stated but that don't appear verbatim in the
# retrieved KB chunks. The caller appends the _IDENTIFIER_DISCLAIMER footer;
# this function never blocks delivery.
# ===========================================================================

from vertex_agent import _check_identifier_faithfulness

# A long KB-context corpus used as the "grounded" backdrop for these tests.
# Identifiers stated in the answer must appear in this string verbatim (after
# whitespace/case normalization) or they get flagged.
_FAKE_KB_CORPUS = (
    "The Office of Research Administration handles proposal submissions. "
    "SOP 12 covers IACUC training requirements. Morgan State's FWA is "
    "FWA00003658. The federal F&A rate is 53.5% on modified total direct "
    "costs. The next proposal deadline is March 15, 2026. Budget cap is "
    "$500,000 across the project period. Contact rebecca.steiner@morgan.edu "
    "for budget questions. Direct line: 443-885-3000."
)


def test_identifier_check_short_corpus_returns_empty():
    """No KB corpus (or a too-short one) means we cannot verify anything --
    return empty rather than flag everything as a false positive."""
    assert _check_identifier_faithfulness("Anything goes.", "") == []
    assert _check_identifier_faithfulness("Anything goes.", "short") == []


def test_identifier_check_verified_date_passes():
    """A date that appears verbatim in the corpus is NOT flagged."""
    text = "The deadline is March 15, 2026 — submit by then."
    assert _check_identifier_faithfulness(text, _FAKE_KB_CORPUS) == []


def test_identifier_check_hallucinated_date_flagged():
    """A date not in the corpus IS flagged."""
    text = "The deadline is April 3, 2027."
    result = _check_identifier_faithfulness(text, _FAKE_KB_CORPUS)
    assert any("April 3, 2027" in r for r in result), result


def test_identifier_check_verified_dollar_passes():
    """A budget cap that matches the corpus exactly is NOT flagged."""
    text = "The award budget cap is $500,000."
    assert _check_identifier_faithfulness(text, _FAKE_KB_CORPUS) == []


def test_identifier_check_hallucinated_dollar_flagged():
    """A made-up budget cap IS flagged."""
    text = "The award budget cap is $750,000."
    result = _check_identifier_faithfulness(text, _FAKE_KB_CORPUS)
    assert any("$750,000" in r for r in result), result


def test_identifier_check_dollar_suffix_format():
    """Dollar amounts with K/M/B suffixes are recognized and checked."""
    text = "Budget cap is $2M for this program."
    result = _check_identifier_faithfulness(text, _FAKE_KB_CORPUS)
    assert any("$2M" in r.lower() or "$2m" in r.lower() for r in result), result


def test_identifier_check_verified_email_passes():
    """A KB-listed email is NOT flagged."""
    text = "Email rebecca.steiner@morgan.edu for help."
    assert _check_identifier_faithfulness(text, _FAKE_KB_CORPUS) == []


def test_identifier_check_hallucinated_email_flagged():
    """A made-up staff email IS flagged."""
    text = "Email john.doe@morgan.edu for help."
    result = _check_identifier_faithfulness(text, _FAKE_KB_CORPUS)
    assert any("john.doe@morgan.edu" in r for r in result), result


def test_identifier_check_whitelisted_ora_email_never_flagged():
    """ask.ora@morgan.edu is baked into the bot's canned refusal/outage
    messages -- it must never be flagged as a hallucination even when the
    KB corpus doesn't mention it."""
    text = "I don't have that info. Email ask.ora@morgan.edu for help."
    # KB corpus deliberately doesn't contain the ORA general email.
    corpus = "Some unrelated KB text that mentions other things but not the general inbox. " * 3
    assert _check_identifier_faithfulness(text, corpus) == []


def test_identifier_check_whitelisted_ora_phone_never_flagged():
    """ORA's main phone (443-885-4044) is part of the canned refusal message
    and must never trigger the disclaimer."""
    text = "I don't have that info. Please contact ORA at 443-885-4044."
    corpus = "Some unrelated KB text. " * 10
    assert _check_identifier_faithfulness(text, corpus) == []


def test_identifier_check_phone_alt_format_whitelisted():
    """The parenthesized form (443) 885-4044 is also whitelisted."""
    text = "Call (443) 885-4044 for assistance."
    corpus = "Some unrelated KB text. " * 10
    assert _check_identifier_faithfulness(text, corpus) == []


def test_identifier_check_hallucinated_phone_flagged():
    """A made-up phone number IS flagged."""
    text = "Call 555-123-9999 for budget help."
    result = _check_identifier_faithfulness(text, _FAKE_KB_CORPUS)
    assert any("555-123-9999" in r for r in result), result


def test_identifier_check_sop_existing_behavior_preserved():
    """Regression guard: the original SOP / FWA / EIN / UEI checks still work
    after the extension."""
    # SOP 12 is in the corpus (ok), SOP 99 is not (flagged).
    text = "See SOP 12 for training. See SOP 99 for biosecurity."
    result = _check_identifier_faithfulness(text, _FAKE_KB_CORPUS)
    assert any("SOP" in r and "99" in r for r in result), result
    assert not any("SOP" in r and "12" in r for r in result), result


def test_identifier_check_rate_existing_behavior_preserved():
    """Regression guard: the F&A rate check still flags hallucinated rates."""
    text = "Morgan State's F&A rate is 60% on direct costs."
    result = _check_identifier_faithfulness(text, _FAKE_KB_CORPUS)
    assert any("60%" in r for r in result), result


def test_identifier_check_dedupe_within_one_answer():
    """The same hallucinated identifier mentioned twice should appear in
    the unverified list at most once -- otherwise the disclaimer fills with
    repeats."""
    text = ("The deadline is April 3, 2027. As noted, April 3, 2027 is firm. "
            "The full window closes April 3, 2027.")
    result = _check_identifier_faithfulness(text, _FAKE_KB_CORPUS)
    date_mentions = [r for r in result if "April 3, 2027" in r]
    assert len(date_mentions) == 1, f"expected 1 date entry, got {date_mentions}"


def test_identifier_check_capped_at_six():
    """At most 6 unverified identifiers are reported so the disclaimer footer
    stays scannable."""
    # 8 distinct hallucinated dates
    text = " ".join(f"Deadline {m} 1, 2027." for m in
                    ["January", "February", "March", "April",
                     "May", "June", "July", "August"])
    result = _check_identifier_faithfulness(text, _FAKE_KB_CORPUS)
    assert len(result) <= 6, result
