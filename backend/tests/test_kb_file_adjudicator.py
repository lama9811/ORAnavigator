"""Tests for drafting a KB entry from a file.

The grounding rule is the point: a draft that cannot quote the file verbatim is
refused, because an ungrounded draft is a confident invention that an admin
would approve on sight.
"""

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRAPER = _ROOT / "kb_scraper"
sys.path.insert(0, str(_SCRAPER))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRAPER / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("adjudicator")
fa = _load("file_adjudicator")

FILE_TEXT = (
    "Morgan State University PI Handbook 5\n"
    "The facilities and administrative rate for on-campus research is 54%.\n"
    "Questions to ask.ora@morgan.edu."
)


def _gen(payload):
    def _g(prompt, system):
        return json.dumps(payload)
    return _g


def test_a_grounded_draft_is_applicable():
    d = fa.draft_new(FILE_TEXT, "https://x/h5.pdf", generate=_gen({
        "title": "PI Handbook 5",
        "content": "The on-campus F&A rate is 54%.",
        "category": "pre_award",
        "subcategory": "handbooks",
        "quote": "The facilities and administrative rate for on-campus research is 54%.",
        "confidence": "high",
    }))
    assert d.grounded and d.applicable
    assert d.title == "PI Handbook 5"
    assert d.category == "pre_award"


def test_a_quote_absent_from_the_file_is_dropped_and_the_draft_refused():
    d = fa.draft_new(FILE_TEXT, "https://x/h5.pdf", generate=_gen({
        "title": "PI Handbook 5",
        "content": "The on-campus F&A rate is 99%.",
        "quote": "The rate is 99% as of 2027.",
        "confidence": "high",
    }))
    assert not d.grounded
    assert not d.applicable
    assert d.quote == ""


def test_a_quote_differing_only_in_whitespace_still_verifies():
    d = fa.draft_new(FILE_TEXT, "https://x/h5.pdf", generate=_gen({
        "title": "T", "content": "c",
        "quote": "The facilities and administrative rate\n   for on-campus research is 54%.",
        "confidence": "high",
    }))
    assert d.grounded


def test_low_confidence_is_never_applicable_even_when_grounded():
    d = fa.draft_new(FILE_TEXT, "https://x/h5.pdf", generate=_gen({
        "title": "T", "content": "c",
        "quote": "The facilities and administrative rate for on-campus research is 54%.",
        "confidence": "low",
    }))
    assert d.grounded and not d.applicable


def test_empty_file_text_never_produces_a_draft():
    d = fa.draft_new("", "https://x/h5.pdf", generate=_gen({"title": "x", "content": "y"}))
    assert not d.applicable and d.content == ""


def test_model_failure_degrades_to_an_unapplicable_draft():
    def boom(prompt, system):
        raise RuntimeError("503 backend unavailable")

    d = fa.draft_new(FILE_TEXT, "https://x/h5.pdf", generate=boom)
    assert not d.applicable
    assert d.what_changed


def test_unparseable_output_degrades_rather_than_raising():
    d = fa.draft_new(FILE_TEXT, "https://x/h5.pdf", generate=lambda p, s: "not json at all")
    assert not d.applicable


def test_update_preserves_detail_the_file_does_not_contradict():
    stored = "The on-campus F&A rate is 50%. The IRB chair is Benjamin Welsh, Ph.D."
    d = fa.draft_update(FILE_TEXT, stored, "F&A Rates", generate=_gen({
        "content": "The on-campus F&A rate is 54%. The IRB chair is Benjamin Welsh, Ph.D.",
        "what_changed": "On-campus F&A rate 50% -> 54%",
        "quote": "The facilities and administrative rate for on-campus research is 54%.",
        "confidence": "high",
    }))
    assert d.applicable
    assert "Benjamin Welsh" in d.content


def test_update_with_no_stored_content_is_not_applicable():
    d = fa.draft_update(FILE_TEXT, "", "T", generate=_gen({"content": "x", "quote": "y"}))
    assert not d.applicable


def test_update_with_an_ungrounded_quote_is_refused():
    d = fa.draft_update(FILE_TEXT, "stored text here", "T", generate=_gen({
        "content": "rewritten",
        "quote": "a sentence that is not in the file",
        "confidence": "high",
    }))
    assert not d.applicable and d.quote == ""
