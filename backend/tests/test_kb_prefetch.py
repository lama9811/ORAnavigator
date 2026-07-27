"""Tests for the Layer 1 KB prefetch scorer (adk_agent/.../kb_prefetch.py).

Loaded by file path on purpose: `adk_agent.ora_navigator_unified.__init__` imports
`agent.py`, which needs `google-adk` (not always installed locally). kb_prefetch
itself has no ADK dependency, so importing the file directly keeps these tests in
the standard backend suite.
"""
import importlib.util
import pathlib
import time

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "adk_agent" / "ora_navigator_unified" / "kb_prefetch.py"
)
_spec = importlib.util.spec_from_file_location("kb_prefetch_under_test", _MODULE_PATH)
kb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb)


def _install_corpus(docs: dict) -> None:
    """Put docs in the prefetch cache and mark it fresh, so no network load runs."""
    kb._cache.clear()
    kb._cache.update(docs)
    kb._cache_ts = time.time()


@pytest.fixture(autouse=True)
def _clean_cache():
    yield
    kb._cache.clear()
    kb._cache_ts = 0


def _corpus_where_only_research_is_common() -> dict:
    """20 docs say 'research'; exactly one says 'zebrafish'.

    'common' repeats the everywhere-word twice in a 2-word doc  -> highest raw TF.
    'rare'   mentions the rare word once in a 4-word doc        -> lowest raw TF.
    So a TF-only scorer ranks 'common' first and 'rare' last.
    """
    docs = {
        f"filler_{i}": {"title": "", "content": "research program overview"}
        for i in range(20)
    }
    docs["common"] = {"title": "", "content": "research research"}
    docs["rare"] = {"title": "", "content": "zebrafish protocol overview data"}
    return docs


def test_rare_term_outranks_a_repeated_common_term():
    """IDF: one mention of a rare word beats two mentions of an everywhere-word.

    Without IDF the 'common' doc wins on raw term frequency (2/2 vs 1/4).
    """
    _install_corpus(_corpus_where_only_research_is_common())

    out = kb.prefetch_kb_context("zebrafish research")

    assert "zebrafish protocol overview data" in out, "rare doc missing from top-5"
    first_doc = out.split("--- Doc 2 ---")[0]
    assert "zebrafish protocol overview data" in first_doc, (
        "the doc holding the RARE query term should rank first; "
        f"got:\n{out[:400]}"
    )


def test_idf_comes_from_the_live_corpus_not_a_fixed_table():
    """When the rare word becomes just as common, its advantage disappears.

    Same two candidate docs as above, but now 20 docs also say 'zebrafish'.
    Both query terms are equally common, so raw term frequency decides again
    and the repeated-term doc wins.
    """
    docs = {
        f"r_filler_{i}": {"title": "", "content": "research program overview"}
        for i in range(20)
    }
    docs.update({
        f"z_filler_{i}": {"title": "", "content": "zebrafish program overview"}
        for i in range(20)
    })
    docs["common"] = {"title": "", "content": "research research"}
    docs["rare"] = {"title": "", "content": "zebrafish protocol overview data"}
    _install_corpus(docs)

    out = kb.prefetch_kb_context("zebrafish research", top_k=50)

    assert "research research" in out and "zebrafish protocol overview data" in out
    assert out.index("research research") < out.index("zebrafish protocol overview data"), (
        "with both terms equally common, IDF should not favour 'zebrafish'"
    )


def test_cold_cache_returns_empty_string_without_network():
    """Graceful fallback: no docs cached -> no context, never an exception."""
    kb._cache.clear()
    kb._cache_ts = time.time()  # 'fresh' but empty: skips the background load path

    assert kb.prefetch_kb_context("what is the F&A rate") == ""


def test_fact_check_only_framing_is_preserved():
    """The prefetch block must stay a hallucination guard, never a grounding source."""
    _install_corpus(_corpus_where_only_research_is_common())

    out = kb.prefetch_kb_context("zebrafish research")

    assert "[REFERENCE CONTEXT - FACT-CHECK ONLY]" in out
    assert "hallucination guard, NOT your retrieved answer" in out
    assert "do NOT cite them as your source" in out
