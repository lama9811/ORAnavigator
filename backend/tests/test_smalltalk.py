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


# Praise / affirmations ("very good", "awesome", "perfect") are small talk too,
# so Layer 3 delivers Gemini's varied warm reply as-is (never regenerated under
# the strict prompt) and the DELIVER step strips any stray KB Sources.
def test_praise_and_affirmations_are_smalltalk():
    for q in ["very good", "very good.", "VERY GOOD.", "awesome", "perfect",
              "excellent", "nice", "great", "cool", "good job", "well done",
              "makes sense", "sounds good", "got it", "good", "noted",
              "you good?", "later"]:
        assert _is_smalltalk(q), f"expected small talk: {q!r}"


# Bot-directed social / personal questions ("do you miss me", "are you real?",
# "who are you") are LANE 1 social chat: Gemini answers them warmly, so they must
# skip the KB and never be regenerated into a refusal by Layer 3.
def test_bot_directed_social_questions_are_smalltalk():
    for q in ["do you miss me", "do you miss me?", "are you real?", "are you human",
              "are you a bot", "who are you", "what is your name", "what's your name",
              "how old are you", "i missed you", "do you like me", "will you miss me",
              "are you okay?"]:
        assert _is_smalltalk(q), f"expected LANE 1 social chat: {q!r}"


# ...but the widened detector must NOT swallow real ORA questions phrased with
# "do you..." / "are you..." — those stay LANE 2 and keep their KB search.
def test_social_widening_does_not_catch_ora_questions():
    for q in ["do you know the UEI?", "do you have the IRB form?",
              "are you sure the deadline is Friday?", "what is my F&A rate?",
              "what are you able to help with for grants?", "who is the IRB contact?"]:
        assert not _is_smalltalk(q), f"should stay LANE 2 (ORA): {q!r}"
