"""Unit tests for mine_failed_queries.py — pure formatting logic."""
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EVAL_DIR))

import mine_failed_queries as mfq  # noqa: E402


def test_dedupe_normalizes_whitespace_and_case():
    rows = ["How do I get an NCE?", "how do i get an nce?  ", "What is F&A?"]
    assert mfq.dedupe(rows) == ["How do I get an NCE?", "What is F&A?"]


def test_format_candidates_yaml_emits_one_block_per_query():
    text = mfq.format_candidates(["What is the F&A rate?", "Who is the director?"])
    assert text.count("- description:") == 2
    assert "What is the F&A rate?" in text
    assert "kb_context:" in text  # each stub carries the field to fill in


def test_format_candidates_yaml_empty():
    assert mfq.format_candidates([]).strip().startswith("#")
