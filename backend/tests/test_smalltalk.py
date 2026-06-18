"""Greetings / small talk must get a warm KB-free reply, never the
'I can only help with ORA questions' refusal and never the strict-prompt
regeneration that turns them into a refusal. See vertex_agent._is_smalltalk."""
import os

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")

from vertex_agent import _is_smalltalk


GREETINGS = [
    "hi", "hello", "hey!", "hey there", "yo", "howdy",
    "how are you", "how are you?", "How are you?", "hey how are you?",
    "how are you doing", "how's it going", "whats up", "what's up",
    "good morning", "good afternoon", "  hi  ",
    "thanks", "thank you", "thx", "ty", "cheers", "appreciate it",
    "ok cool", "great, thanks", "got it", "bye", "see ya", "take care",
]

# Real ORA questions that happen to start with a greeting word or "how" must
# NOT be treated as small talk -- they still need full KB grounding.
ORA_QUESTIONS = [
    "how do I submit a proposal",
    "what is the fringe rate",
    "how are the indirect costs calculated",
    "hi, what is the IRB deadline",
    "hello can you list all forms",
    "thanks for the F&A rate, what about fringe?",
    "what is up with my grant account",
    "how are F&A rates applied to equipment",
    "who do I contact about IACUC",
]


def test_greetings_are_smalltalk():
    for q in GREETINGS:
        assert _is_smalltalk(q), f"expected small talk: {q!r}"


def test_ora_questions_are_not_smalltalk():
    for q in ORA_QUESTIONS:
        assert not _is_smalltalk(q), f"should NOT be small talk: {q!r}"


def test_empty_is_not_smalltalk():
    assert not _is_smalltalk("")
    assert not _is_smalltalk(None)
