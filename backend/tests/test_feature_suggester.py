"""Unit tests for the deterministic in-app feature suggester.

The chat path surfaces a "we have a tool for this -- use it" callout based on
the user's QUESTION (not the answer). These pin the routing: funding-discovery
questions -> Find Funding, proposal-building -> Proposals, examples -> Samples,
forms -> Forms; unrelated ORA questions -> no card.
"""
from services.feature_suggester import suggest_feature


def test_find_funding_question_maps_to_opportunity_finder():
    for q in [
        "How do I find funding opportunities for my research?",
        "Where can I find grants for my lab?",
        "I'm looking for funding to apply to.",
        "how to find funding",
    ]:
        f = suggest_feature(q)
        assert f and f["id"] == "find_funding", f"{q!r} -> {f}"
        assert f["route"] == "/opportunities"
        assert "triggers" not in f  # internal field must not leak to the client


def test_proposal_building_maps_to_proposals():
    for q in [
        "How do I start a proposal?",
        "Help me build my budget for my proposal.",
        "I want to write a proposal for NSF.",
    ]:
        f = suggest_feature(q)
        assert f and f["id"] == "build_proposal", f"{q!r} -> {f}"
        assert f["route"] == "/my-proposals"


def test_examples_map_to_samples_even_with_proposal_word():
    # "sample proposal" contains "proposal" but must resolve to Samples (more
    # specific feature is checked first).
    f = suggest_feature("Can I see a sample proposal?")
    assert f and f["id"] == "samples"
    assert f["route"] == "/sample-proposals"


def test_forms_question_maps_to_forms():
    f = suggest_feature("Which form do I need for a no-cost extension?")
    assert f and f["id"] == "forms"
    assert f["route"] == "/forms"


def test_unrelated_question_gets_no_card():
    for q in [
        "What is Morgan State's F&A rate?",
        "How long does IRB approval take?",
        "Who is the pre-award contact?",
        "",
        None,
    ]:
        assert suggest_feature(q) is None, f"{q!r} should not produce a card"
