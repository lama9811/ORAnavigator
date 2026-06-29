"""Tests for the Open Grants (ogrants.org) live sample feed.

All network-free: the parsing/normalization helpers are pure, and the fetch
path is exercised by monkeypatching the HTTP/parse layer. The golden rules under
test: link-only cards, graceful [] on failure, never fabricate, and caching.
"""
import services.ogrants_finder as og


_SAMPLE_MD = """---
layout: grant
title: 'Bridging Soil Microbiomes and Crop Resilience'
author: 'Jane Roe, John Doe'
year: '2021'
institution: 'Morgan State University'
link: 'https://example.org/proposal.pdf, https://example.org/budget.xlsx'
funder: U.S. National Science Foundation (NSF)
program: CAREER
discipline:
status: funded
---
"""


def setup_function(_):
    og.clear_cache()


# --- parsing ----------------------------------------------------------------

def test_parse_front_matter_reads_flat_keys():
    fm = og._parse_front_matter(_SAMPLE_MD)
    assert fm["title"] == "Bridging Soil Microbiomes and Crop Resilience"
    assert fm["funder"] == "U.S. National Science Foundation (NSF)"
    assert fm["program"] == "CAREER"
    assert fm["status"] == "funded"
    assert fm["discipline"] == ""        # empty value tolerated


def test_first_https_picks_first_secure_url():
    assert og._first_https("http://x.com, https://y.com/a") == "https://y.com/a"
    assert og._first_https("not a url") == ""


# --- category mapping -------------------------------------------------------

def test_categories_for_maps_funders():
    assert "NSF" in og._categories_for("National Science Foundation (NSF)", "")
    assert "NIH" in og._categories_for("National Institutes of Health", "")
    assert "Foundations" in og._categories_for("The Sloan Foundation", "")
    assert "Early-career" in og._categories_for("NSF", "CAREER")


def test_categories_for_unknown_funder_is_empty():
    assert og._categories_for("Department of Mystery", "Widgets") == []


# --- normalization ----------------------------------------------------------

def test_normalize_produces_link_card_shape():
    card = og._normalize(og._parse_front_matter(_SAMPLE_MD), "roe_jane_2021")
    assert card["type"] == "link"
    assert card["url"].startswith("https://")
    assert card["id"] == "ogrants-roe_jane_2021"
    assert card["community"] is True
    assert "NSF" in card["categories"] and "Early-career" in card["categories"]
    # required card keys present (mirrors the static SAMPLE_PROPOSALS shape)
    for key in ("id", "type", "title", "source", "url", "categories", "kind", "access", "why"):
        assert key in card


def test_normalize_skips_entry_without_https_link():
    fm = og._parse_front_matter(_SAMPLE_MD.replace(
        "link: 'https://example.org/proposal.pdf, https://example.org/budget.xlsx'",
        "link: ''"))
    assert og._normalize(fm, "x") is None


def test_normalize_skips_entry_without_title():
    fm = og._parse_front_matter(_SAMPLE_MD.replace(
        "title: 'Bridging Soil Microbiomes and Crop Resilience'", "title: ''"))
    assert og._normalize(fm, "x") is None


# --- fetch path (graceful + cached) -----------------------------------------

def test_fetch_index_returns_empty_on_error(monkeypatch):
    def boom():
        raise RuntimeError("network down")
    monkeypatch.setattr(og, "_download_and_parse", boom)
    assert og.fetch_index() == []          # never raises, no last-good cache


def test_fetch_index_is_cached(monkeypatch):
    calls = {"n": 0}
    sample = [{"id": "ogrants-x", "type": "link", "categories": ["NSF"]}]

    def fake():
        calls["n"] += 1
        return list(sample)
    monkeypatch.setattr(og, "_download_and_parse", fake)
    first = og.fetch_index()
    second = og.fetch_index()
    assert first == second == sample
    assert calls["n"] == 1                 # second call served from cache


def test_fetch_index_serves_last_good_after_failure(monkeypatch):
    good = [{"id": "ogrants-x", "type": "link", "categories": []}]
    monkeypatch.setattr(og, "_download_and_parse", lambda: list(good))
    assert og.fetch_index() == good        # populates last-good
    og._cache.clear()                      # simulate TTL expiry
    monkeypatch.setattr(og, "_download_and_parse",
                        lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert og.fetch_index() == good        # falls back to last good, not []


def test_list_community_samples_filters_by_category(monkeypatch):
    items = [
        {"id": "a", "categories": ["NSF"]},
        {"id": "b", "categories": ["NIH", "Early-career"]},
    ]
    monkeypatch.setattr(og, "fetch_index", lambda: list(items))
    assert {c["id"] for c in og.list_community_samples()} == {"a", "b"}
    assert [c["id"] for c in og.list_community_samples("NIH")] == ["b"]
