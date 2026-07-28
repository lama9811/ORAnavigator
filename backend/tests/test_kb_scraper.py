"""Tests for the KB scraper's pure logic — URL scoping, fingerprinting, the
readable/unreadable distinction, and the AI adjudicator's grounding rule.

The crawler and the Cloud Run Job are not covered here (they need a browser and
GCP); everything that decides whether a document gets OVERWRITTEN is.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRAPER = _ROOT / "kb_scraper"
sys.path.insert(0, str(_SCRAPER))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRAPER / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fp = _load("fingerprint")
adj = _load("adjudicator")


# ---------------------------------------------------------------------------
# URL normalization — wrong here means pages counted twice or missed entirely
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://www.morgan.edu/ora/", "https://www.morgan.edu/ora"),
    ("https://WWW.Morgan.EDU/ORA", "https://www.morgan.edu/ORA"),
    ("https://www.morgan.edu/ora#main", "https://www.morgan.edu/ora"),
    ("https://www.morgan.edu/ora?utm_source=x&utm_medium=y", "https://www.morgan.edu/ora"),
    ("https://www.morgan.edu/ora?page=2", "https://www.morgan.edu/ora?page=2"),
])
def test_url_normalization(raw, expected):
    assert fp.normalize_url(raw) == expected


def test_relative_urls_resolve_against_the_page():
    assert fp.normalize_url(
        "/office-of-research-administration/pre-award",
        "https://www.morgan.edu/office-of-research-administration",
    ) == "https://www.morgan.edu/office-of-research-administration/pre-award"


def test_trailing_slash_is_not_a_separate_page():
    """Otherwise every page reports its twin as new, forever."""
    a = fp.normalize_url("https://www.morgan.edu/office-of-research-administration/pre-award")
    b = fp.normalize_url("https://www.morgan.edu/office-of-research-administration/pre-award/")
    assert a == b


# ---------------------------------------------------------------------------
# Scope — ORA section only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.morgan.edu/office-of-research-administration",
    "https://www.morgan.edu/office-of-research-administration/pre-award/budget-development",
    "https://www.morgan.edu/ora",
])
def test_ora_pages_are_in_scope(url):
    assert fp.is_in_scope(url)


@pytest.mark.parametrize("url", [
    "https://www.morgan.edu/admissions",                      # different section
    "https://www.google.com/office-of-research-administration",  # different host
    "https://www.morgan.edu/office-of-research-administration/logo.png",
    "https://www.morgan.edu/office-of-research-administration/style.css",
    "mailto:ask.ora@morgan.edu",
    "",
])
def test_out_of_scope_urls_are_rejected(url):
    assert not fp.is_in_scope(url)


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------

def test_whitespace_differences_are_not_a_change():
    """A reflowed paragraph is the same page. Same principle as the evidence
    check in section_coach."""
    assert fp.fingerprint("The F&A rate\n\nis 54%.") == fp.fingerprint("The F&A rate is 54%.")


def test_a_changed_rate_is_a_change():
    assert fp.fingerprint("The F&A rate is 54%.") != fp.fingerprint("The F&A rate is 55%.")


def test_fingerprint_is_stable_across_calls():
    text = "Morgan State University Office of Research Administration"
    assert fp.fingerprint(text) == fp.fingerprint(text)


# ---------------------------------------------------------------------------
# Unreadable vs changed — the core safety distinction
# ---------------------------------------------------------------------------

def test_non_200_is_unreadable_not_changed():
    assert fp.looks_unreadable("some text " * 50, status=404)


def test_empty_render_is_unreadable():
    """A failed JS render returns almost nothing; treating that as 'the content
    was deleted' would wipe a good document."""
    assert fp.looks_unreadable("", status=200)
    assert fp.looks_unreadable("Loading...", status=200)


def test_404_body_served_with_200_is_still_unreadable():
    body = "Page Not Found. The page you requested could not be found. " * 10
    assert fp.looks_unreadable(body, status=200)


def test_a_real_page_is_readable():
    body = (
        "Pre-Award services support faculty in developing proposals. The facilities "
        "and administrative rate for on-campus research is 54 percent of modified "
        "total direct costs. Contact the Office of Research Administration for help "
        "preparing your budget and routing your internal form before the deadline."
    )
    assert not fp.looks_unreadable(body, status=200)


# ---------------------------------------------------------------------------
# Adjudicator grounding — an AI verdict must be quotable or it does not apply
# ---------------------------------------------------------------------------

PAGE = (
    "Facilities and Administrative Cost Rates. The off-campus rate is 27% of "
    "modified total direct costs, effective July 1 2026."
)


def _verdict(**kw):
    base = dict(material=True, what_changed="rate changed", new_content="new body",
                quote="", confidence="high", grounded=False)
    base.update(kw)
    return adj.Verdict(**base)


def test_quote_matching_collapses_whitespace():
    assert adj._quote_in("the off-campus rate is 27%", "the  off-campus\n\nrate is 27%")


def test_quote_matching_rejects_text_not_on_the_page():
    assert not adj._quote_in("the off-campus rate is 99%", PAGE)


def test_trivially_short_quotes_are_rejected():
    """A two-word 'quote' matches almost any page and proves nothing."""
    assert not adj._quote_in("rate", PAGE)


def test_grounded_material_high_confidence_applies():
    assert _verdict(grounded=True, confidence="high").applicable


def test_ungrounded_verdict_never_applies():
    assert not _verdict(grounded=False, confidence="high").applicable


def test_low_confidence_never_applies():
    assert not _verdict(grounded=True, confidence="low").applicable


def test_cosmetic_change_never_applies():
    assert not _verdict(material=False, grounded=True, confidence="high").applicable


def test_empty_draft_never_applies():
    """Nothing to write is not a reason to write nothing over a real document."""
    assert not _verdict(grounded=True, confidence="high", new_content="   ").applicable


def test_broken_ai_response_falls_back_to_review_not_to_writing(monkeypatch):
    """Golden rule 3: when the model is unavailable we report, we don't guess."""
    monkeypatch.setattr(adj, "_parse", lambda raw: None)
    verdict = adj.adjudicate(PAGE, "stored content", "F&A Cost Rates")
    assert not verdict.applicable
    assert verdict.confidence == "low"


def test_empty_page_text_is_never_adjudicated():
    verdict = adj.adjudicate("", "stored content", "F&A")
    assert not verdict.applicable


# ---------------------------------------------------------------------------
# JSON parsing tolerance
# ---------------------------------------------------------------------------

def test_parses_a_fenced_json_block():
    assert adj._parse('```json\n{"material": true}\n```') == {"material": True}


def test_parses_json_embedded_in_prose():
    assert adj._parse('Here you go: {"material": false} — hope that helps') == {"material": False}


def test_unparseable_response_returns_none():
    assert adj._parse("I could not complete that request.") is None


def test_diff_summary_reports_direction_of_change():
    summary = fp.summarize_diff("The rate is 54%.", "The rate is 55%.")
    assert "54" in summary and "55" in summary
