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


# ===========================================================================
# _select_model -- the before_model_callback that injects the TF-IDF prefetch.
#
# Regression: a previous version skipped the prefetch as soon as ANY content
# in the session contained a function_response. In multi-turn ADK chats,
# turn 1's function_response stayed in `llm_request.contents` for all
# subsequent turns, so prefetch was permanently disabled after the first
# tool call. Symptom: follow-up turns like "give me another training video"
# returned "I'm sorry, I couldn't generate a response" because Gemini had
# no KB context and didn't always re-invoke the search tool.
#
# Fix: only treat the session as "mid-tool-loop" when the LAST content item
# is a function_response. Earlier ones are from prior turns and must not
# block prefetch on the new user message.
# ===========================================================================

from types import SimpleNamespace
from adk_agent.ora_navigator_unified import agent as agent_module


class _FakeLlmRequest:
    """Minimal ADK llm_request stand-in. Records append_instructions calls."""
    def __init__(self, contents):
        self.contents = contents
        self.model = ""
        self.injected: list[str] = []

    def append_instructions(self, text):
        self.injected.append(text)


def _user_content(text):
    """Build a fake ADK Content for a user message."""
    return SimpleNamespace(
        role="user",
        parts=[SimpleNamespace(text=text)],
    )


def _tool_call_content():
    """Build a fake ADK Content where the model invoked a tool."""
    return SimpleNamespace(
        role="model",
        parts=[SimpleNamespace(function_call={"name": "vertex_ai_search"})],
    )


def _tool_response_content():
    """Build a fake ADK Content carrying a tool's function_response."""
    return SimpleNamespace(
        role="tool",
        parts=[SimpleNamespace(function_response={"name": "vertex_ai_search", "response": {}})],
    )


def _model_text_content(text):
    """Build a fake ADK Content for a final model text reply."""
    return SimpleNamespace(
        role="model",
        parts=[SimpleNamespace(text=text)],
    )


def _ctx():
    """Minimal callback_context: only state.get is used by _select_model."""
    return SimpleNamespace(state={})


def _run(monkeypatch, contents, prefetch_returns="[PRE-FETCHED KB CONTEXT] ..."):
    """Invoke _select_model with the given contents and a stubbed
    prefetch_kb_context. Returns the _FakeLlmRequest so the test can
    inspect whether prefetch was injected."""
    # Stub prefetch_kb_context so the test never touches Discovery Engine.
    import adk_agent.ora_navigator_unified.kb_prefetch as kb_prefetch
    monkeypatch.setattr(kb_prefetch, "prefetch_kb_context",
                        lambda q, top_k=5: prefetch_returns)
    req = _FakeLlmRequest(contents)
    agent_module._select_model(_ctx(), req)
    return req


def test_prefetch_injects_on_fresh_user_turn(monkeypatch):
    """On a first-turn user message with no tool history, prefetch must run."""
    req = _run(monkeypatch, [_user_content("What's the F&A rate for NSF?")])
    assert req.injected, "prefetch should have been appended to instructions"


def test_prefetch_injects_on_follow_up_after_completed_tool_turn(monkeypatch):
    """The regression. Turn 1 ended with a successful tool call -> its
    function_response sits in history. Turn 2 is a fresh user message;
    prefetch must STILL inject so the model has KB context for the new
    question. The old broken version checked all contents for any
    function_response and skipped prefetch here -- which is the cause
    of 'I'm sorry, I couldn't generate a response' on follow-ups."""
    contents = [
        # Turn 1: user asked, model called the tool, tool replied, model wrote.
        _user_content("Give me the NSF training video"),
        _tool_call_content(),
        _tool_response_content(),
        _model_text_content("Here is the video link..."),
        # Turn 2: fresh user message.
        _user_content("Give me another training video, different topic"),
    ]
    req = _run(monkeypatch, contents)
    assert req.injected, (
        "prefetch must run on the second user turn -- the function_response "
        "from turn 1 is conversation history, not a sign we're mid-tool-loop"
    )


def test_prefetch_skipped_when_last_content_is_a_tool_response(monkeypatch):
    """Mid-tool-loop guard: when the LAST content item is the just-returned
    function_response (model is about to write its tool-grounded reply),
    re-injecting prefetch is redundant and would push fresh tool output
    further from the working window. Skip the inject in that case."""
    contents = [
        _user_content("What's the F&A rate for NSF?"),
        _tool_call_content(),
        _tool_response_content(),  # last item -- model about to reply
    ]
    req = _run(monkeypatch, contents)
    assert not req.injected, (
        "prefetch must be skipped when the last content is a "
        "function_response -- we're mid-tool-loop, not at a fresh user turn"
    )


def test_prefetch_skipped_when_user_text_is_too_short(monkeypatch):
    """Sanity: very short user queries (greetings, 'hi') don't get prefetched
    -- the cutoff is 10 chars in _select_model. Independent of the bug fix
    but guards the existing behavior."""
    req = _run(monkeypatch, [_user_content("hi")])
    assert not req.injected


def test_model_preference_state_overrides_model(monkeypatch):
    """Unrelated-but-cohabiting concern in _select_model: when
    state['model_preference'] is set to a known key, the llm_request.model
    is replaced. Guards the model-routing path next to the prefetch path."""
    import adk_agent.ora_navigator_unified.kb_prefetch as kb_prefetch
    monkeypatch.setattr(kb_prefetch, "prefetch_kb_context", lambda q, top_k=5: "")
    req = _FakeLlmRequest([_user_content("anything")])
    ctx = SimpleNamespace(state={"model_preference": "inav-1.1"})
    agent_module._select_model(ctx, req)
    assert req.model == agent_module.MODEL_MAP["inav-1.1"]
