"""Structural guards for the ADK agent's BASE_INSTRUCTION.

The system prompt controls the model's Pass 1 behavior. A previous bug:
the prompt's "Only state facts returned by KB search" rule made the model
refuse to recall facts the user had volunteered earlier in the chat
("What department am I in?" -> "I do not have information about your
specific department"). The fix added a recall carve-out distinguishing
KB facts (must be KB-grounded) from user-shared self-facts (recalled
from conversation history).

These tests guard against accidental removal of that carve-out.
"""

from adk_agent.ora_navigator_unified.agent import BASE_INSTRUCTION


def test_instruction_permits_recall_of_user_self_facts():
    """BASE_INSTRUCTION must explicitly tell the model it can recall facts
    the user shared about themselves earlier in the conversation. Without
    this, the grounding rules trigger refusals on questions like 'What
    department am I in?' that should be answered from chat history."""
    text = BASE_INSTRUCTION.lower()
    # The carve-out must mention the conversation as a permitted source
    # AND explicitly distinguish user-shared self-facts from KB facts.
    assert "conversation" in text, \
        "BASE_INSTRUCTION must permit recall from the conversation"
    assert any(token in text for token in (
        "user has shared", "user has stated", "user has told you",
        "user told you", "what the user said", "user-shared",
    )), "BASE_INSTRUCTION must reference user-shared facts as recallable"


def test_instruction_distinguishes_kb_facts_from_user_facts():
    """The carve-out must not weaken KB grounding: institutional facts
    (rates, policies, IDs) must still require KB search. The carve-out
    is specifically for the user's own self-facts."""
    text = BASE_INSTRUCTION.lower()
    # KB grounding rule must still be present
    assert "knowledge base" in text
    assert "training data" in text
    # And the carve-out must scope itself to USER self-facts -- not
    # blanket-permit recall of arbitrary "facts the conversation mentioned"
    # which would undermine KB grounding.
    assert any(token in text for token in (
        "their department", "their role", "their deadlines",
        "about themselves", "user's department", "user's role",
    )), ("the recall carve-out must scope to the user's own self-facts, "
         "not arbitrary conversational content")
