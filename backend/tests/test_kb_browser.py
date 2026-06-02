"""Regression tests for kb_browser.try_browse history-awareness.

Run from the backend/ directory:
    cd backend && ../.venv/bin/python -m pytest tests/test_kb_browser.py -v

Background: try_browse() is a deterministic pre-agent fast-path. It used to be
stateless, so a substantive follow-up like "can you give me what forms do I
need to fill?" (which trips the enumeration regex via "give me" / "what forms")
was misclassified as a KB-enumeration request and answered with a full KB-tree
dump instead of reaching the context-aware agent. These tests pin the
history-aware behavior that fixes that.
"""
import pytest

from kb_browser import try_browse, _detect_enumeration


# --- The reproduced bug -----------------------------------------------------

def test_followup_weak_trigger_defers_to_agent():
    """Turn 2 after 'explain post-awards' must NOT dump the KB tree."""
    q = "can you give me what forms do I need to fill?"
    assert try_browse(q, has_history=True) is None


def test_same_query_first_turn_still_browses():
    """Historyless first turn keeps the original deterministic behavior."""
    q = "can you give me what forms do I need to fill?"
    result = try_browse(q, has_history=False)
    assert result is not None
    assert "ORA Knowledge Base" in result


# --- Strong vs weak x has_history matrix ------------------------------------

@pytest.mark.parametrize("query", [
    "list IACUC SOPs",
    "what's in pre-award",
    "show me the templates",
])
def test_strong_trigger_with_topic_browses_even_with_history(query):
    """Genuine enumeration with a resolvable topic survives mid-conversation
    (the advertised multi-turn drill-down)."""
    assert try_browse(query, has_history=True) is not None


def test_strong_trigger_no_topic_with_history_defers():
    """Defense-in-depth: strong trigger but no topic -> no root dump mid-chat."""
    assert try_browse("what do you have", has_history=True) is None


def test_strong_trigger_no_topic_first_turn_shows_root():
    """First turn with no topic still shows the root index."""
    result = try_browse("what do you have", has_history=False)
    assert result is not None
    assert "ORA Knowledge Base" in result


def test_weak_trigger_no_history_browses():
    """Weak trigger on turn 1 still hits the deterministic path."""
    assert try_browse("tell me about post-award forms", has_history=False) is not None


def test_non_enumeration_query_returns_none():
    """A plain question is never intercepted, regardless of history."""
    assert try_browse("how do I submit an IRB protocol", has_history=False) is None
    assert try_browse("how do I submit an IRB protocol", has_history=True) is None


# --- _detect_enumeration classification -------------------------------------

def test_detect_classifies_strong_and_weak():
    """_detect_enumeration returns (matched_any, matched_strong)."""
    assert _detect_enumeration("list IACUC SOPs") == (True, True)
    assert _detect_enumeration("give me the forms") == (True, False)
    assert _detect_enumeration("what forms do you have") == (True, True)
    assert _detect_enumeration("how do I apply") == (False, False)


# --- Content questions must NOT be mistaken for enumeration ------------------
# Regression for the coverage finding: "what topics/types does X cover" is a
# content question about one doc, not a directory request. It used to dump a
# list of links (~50 failures in trainings + IACUC SOPs).

@pytest.mark.parametrize("query", [
    "What topics does SOP 41.2 cover regarding access to animal housing rooms?",
    "What topics are covered in the 'It Takes a Village' eTraining module?",
    "What types of items are covered in the annual review for the Animal Care program?",
    "What kinds of expenses are allowable on a sponsored project?",
])
def test_content_question_not_intercepted(query):
    """A 'what topics/types/kinds does X cover' content question must reach the
    agent (return None), not the deterministic directory dump -- on a fresh turn
    AND mid-conversation."""
    assert try_browse(query, has_history=False) is None
    assert try_browse(query, has_history=True) is None
    # and it is no longer classified as enumeration at all
    assert _detect_enumeration(query) == (False, False)


def test_genuine_list_requests_still_browse():
    """The real document-listing phrasings must still trigger enumeration."""
    assert _detect_enumeration("what forms do you have") == (True, True)
    assert _detect_enumeration("list IACUC SOPs") == (True, True)
    assert _detect_enumeration("show me the templates") == (True, True)
    assert _detect_enumeration("what templates does ORA provide") == (True, False)
    assert try_browse("list IACUC SOPs", has_history=True) is not None


def test_listserv_not_treated_as_list_command():
    """'list-serv' contains 'list' but is a content question, not enumeration."""
    q = "How can I subscribe to the ORA Announcements list-serv?"
    assert _detect_enumeration(q) == (False, False)
    assert try_browse(q, has_history=False) is None
    assert try_browse(q, has_history=True) is None
    # the bare verb 'list' must still work
    assert _detect_enumeration("list the IACUC SOPs") == (True, True)


# --- Filtered questions ("what templates SUPPORT AI") defer to the agent ------
# Regression for the directory dump answering "what forms or templates support
# AI?" with the entire templates list instead of honoring the "support AI" filter.

@pytest.mark.parametrize("query", [
    "What forms or templates support AI?",
    "Which templates support artificial intelligence research?",
    "Show me the templates related to animal research.",
    "What forms are used for IRB submissions that support clinical trials?",
])
def test_filtered_enumeration_defers_to_agent(query):
    """A filter cue (support / related to / used for) means the user wants a
    SUBSET the directory dump can't produce -> defer to the agent."""
    assert try_browse(query, has_history=False) is None
    assert try_browse(query, has_history=True) is None


def test_plain_enumeration_without_filter_still_browses():
    """No filter cue -> the genuine 'list the section' behavior is unchanged."""
    assert try_browse("what templates does ORA provide", has_history=False) is not None
    assert try_browse("list IACUC SOPs", has_history=False) is not None
    assert try_browse("tell me about post-award forms", has_history=False) is not None
