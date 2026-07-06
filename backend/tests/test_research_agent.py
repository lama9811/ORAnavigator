"""Tests for the Auto-Research Agent's failed-query filtering.

Only genuine knowledge-base questions the chatbot couldn't answer should become
failed queries. Personal questions about the user themselves ("what department
am I in?") and anything with personal data are answered from the profile / chat
history, not the KB, so they must NEVER be logged as failed queries.

Run from backend/:
    python -m pytest tests/test_research_agent.py -v
"""
from research_agent import _is_personal_question


def test_personal_recall_questions_are_not_kb_gaps():
    assert _is_personal_question("What department am I in?")
    assert _is_personal_question("what is my name?")
    assert _is_personal_question("what's my role?")
    assert _is_personal_question("who am I?")
    assert _is_personal_question("remind me what my sponsor is")


def test_personal_data_is_filtered():
    assert _is_personal_question("my salary is $145,000 and my SSN is 123-45-6789")
    assert _is_personal_question("email me at pi@morgan.edu")
    assert _is_personal_question("my social security number is 123456789")


def test_real_kb_questions_are_kept():
    # These are genuine ORA/knowledge-base questions -> NOT personal -> may be
    # logged as failed queries if the bot can't answer them.
    assert not _is_personal_question("What is Morgan State's F&A rate?")
    assert not _is_personal_question("How do I submit an IRB application?")
    assert not _is_personal_question("How do I close out my grant at the end?")   # 'my grant' is a KB topic
    assert not _is_personal_question("Where do I find the internal routing form?")
