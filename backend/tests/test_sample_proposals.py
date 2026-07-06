"""Tests for the Sample Proposals Library (services/sample_proposals.py + the
public GET /api/sample-proposals endpoint).

These pin the curation invariants (unique ids, https-only links, valid tags) and
the filter contract. The data is a static constant, so these are fast and
network-free.

Run from backend/:
    JWT_SECRET=test-secret TRUSTED_HOSTS=testserver,localhost,127.0.0.1 \
      python3 -m pytest tests/test_sample_proposals.py -q
"""
from services.sample_proposals import (
    SAMPLE_PROPOSALS, CATEGORIES, list_samples, categories,
    get_sample, pdf_path, rank_samples,
)

# Keys every entry carries, regardless of type.
_COMMON_KEYS = {"id", "type", "title", "source", "categories", "kind", "access", "why"}
_VALID_ACCESS = {"free", "partial"}
_VALID_TYPES = {"pdf", "link"}


# --- curation invariants ----------------------------------------------------

def test_every_entry_has_required_keys():
    for s in SAMPLE_PROPOSALS:
        missing = _COMMON_KEYS - set(s)
        assert not missing, f"{s.get('id')} missing keys: {missing}"


def test_types_are_known():
    for s in SAMPLE_PROPOSALS:
        assert s["type"] in _VALID_TYPES, f"{s['id']} has bad type {s['type']!r}"


def test_ids_are_unique():
    ids = [s["id"] for s in SAMPLE_PROPOSALS]
    assert len(ids) == len(set(ids)), "duplicate id in SAMPLE_PROPOSALS"


def test_link_entries_have_https_urls():
    links = [s for s in SAMPLE_PROPOSALS if s["type"] == "link"]
    assert links, "expected at least one link entry"
    for s in links:
        assert s["url"].startswith("https://"), f"{s['id']} url is not https"


def test_pdf_entries_have_an_existing_file():
    # Samples are now external links to real funded proposals; there may be no
    # locally-hosted PDFs. Any pdf-type entry that DOES exist must have its file.
    pdfs = [s for s in SAMPLE_PROPOSALS if s["type"] == "pdf"]
    for s in pdfs:
        assert s.get("pdf", "").endswith(".pdf"), f"{s['id']} missing .pdf filename"
        path = pdf_path(s["id"])
        assert path is not None, f"{s['id']} PDF file is missing on disk: {s.get('pdf')}"


def test_categories_are_valid_and_nonempty():
    valid = set(CATEGORIES)
    for s in SAMPLE_PROPOSALS:
        assert s["categories"], f"{s['id']} has no categories"
        assert set(s["categories"]) <= valid, f"{s['id']} has an unknown category"


def test_access_values_are_known():
    for s in SAMPLE_PROPOSALS:
        assert s["access"] in _VALID_ACCESS, f"{s['id']} has bad access value"


# --- filter contract --------------------------------------------------------

def test_list_samples_returns_everything_by_default():
    assert len(list_samples()) == len(SAMPLE_PROPOSALS)


def test_list_samples_filters_by_category():
    nsf = list_samples("NSF")
    assert nsf, "expected at least one NSF-tagged sample"
    assert all("NSF" in s["categories"] for s in nsf)
    # NSF is a strict subset of the full pool (some entries are NIH/Foundations only).
    assert len(nsf) < len(SAMPLE_PROPOSALS)


def test_unknown_category_returns_all_gracefully():
    assert len(list_samples("Nonexistent")) == len(SAMPLE_PROPOSALS)
    assert len(list_samples("")) == len(SAMPLE_PROPOSALS)


def test_list_samples_returns_copies_not_references():
    a = list_samples()[0]
    a["title"] = "MUTATED"
    assert SAMPLE_PROPOSALS[0]["title"] != "MUTATED"


def test_every_category_has_at_least_one_entry():
    for cat in categories():
        assert list_samples(cat), f"category {cat} has no entries"


# --- endpoint ---------------------------------------------------------------

def test_endpoint_returns_full_list_no_auth(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    # Stub the live ogrants source so this test stays offline + deterministic;
    # the static authored/vetted list is what we're pinning here.
    import services.ogrants_finder as og
    monkeypatch.setattr(og, "list_community_samples", lambda *a, **k: [])
    with TestClient(main.app) as client:
        r = client.get("/api/sample-proposals")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == len(body["proposals"]) == len(SAMPLE_PROPOSALS)
        assert body["categories"] == CATEGORIES


def test_endpoint_filters_by_category(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    import services.ogrants_finder as og
    monkeypatch.setattr(og, "list_community_samples", lambda *a, **k: [])
    with TestClient(main.app) as client:
        r = client.get("/api/sample-proposals", params={"category": "NIH"})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] > 0
        assert all("NIH" in p["categories"] for p in body["proposals"])


# --- download helper + endpoint ---------------------------------------------

def test_pdf_path_none_for_link_entry():
    link = next(s for s in SAMPLE_PROPOSALS if s["type"] == "link")
    assert pdf_path(link["id"]) is None


def test_pdf_path_none_for_unknown_id():
    assert pdf_path("does-not-exist") is None
    assert pdf_path(None) is None


def test_get_sample_returns_copy():
    sid = SAMPLE_PROPOSALS[0]["id"]
    s = get_sample(sid)
    s["title"] = "MUTATED"
    assert SAMPLE_PROPOSALS[0]["title"] != "MUTATED"
    assert get_sample("nope") is None


def test_download_endpoint_serves_pdf():
    from fastapi.testclient import TestClient
    import main
    import pytest
    pdf_entry = next((s for s in SAMPLE_PROPOSALS if s["type"] == "pdf"), None)
    if pdf_entry is None:
        pytest.skip("no pdf-type samples — all samples are external links")
    with TestClient(main.app) as client:
        r = client.get(f"/api/sample-proposals/{pdf_entry['id']}/download")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:4] == b"%PDF"


def test_download_endpoint_404_for_link_entry():
    from fastapi.testclient import TestClient
    import main
    link_entry = next(s for s in SAMPLE_PROPOSALS if s["type"] == "link")
    with TestClient(main.app) as client:
        r = client.get(f"/api/sample-proposals/{link_entry['id']}/download")
        assert r.status_code == 404


def test_download_endpoint_404_for_unknown_id():
    from fastapi.testclient import TestClient
    import main
    with TestClient(main.app) as client:
        r = client.get("/api/sample-proposals/nope/download")
        assert r.status_code == 404


# --- interest ranking (deterministic) ---------------------------------------

_RANK_ITEMS = [
    {"id": "a", "title": "Machine learning for robotics control", "source": "NSF",
     "kind": "CAREER", "why": "", "categories": ["NSF"]},
    {"id": "b", "title": "Coral reef restoration", "source": "NOAA",
     "kind": "", "why": "marine ecology", "categories": ["Foundations"]},
    {"id": "c", "title": "Soil microbiome", "source": "USDA",
     "kind": "", "why": "uses robotics sensors in the field", "categories": []},
]


def test_rank_samples_orders_by_relevance():
    out = rank_samples(_RANK_ITEMS, "AI and robotics")
    # 'a' (robotics in title, 3x) ranks above 'c' (robotics in body, 1x).
    ids = [s["id"] for s in out]
    assert ids.index("a") < ids.index("c")
    assert out[0]["id"] == "a"
    assert "robotics" in out[0]["match"]["terms"]


def test_rank_samples_unmatched_keep_order_and_have_no_match():
    out = rank_samples(_RANK_ITEMS, "robotics")
    unmatched = [s for s in out if "match" not in s]
    assert any(s["id"] == "b" for s in unmatched)        # coral reef: no match
    # matched entries sort ahead of unmatched
    assert out[-1]["id"] == "b"


def test_rank_samples_empty_query_unchanged():
    out = rank_samples(_RANK_ITEMS, "   ")
    assert [s["id"] for s in out] == ["a", "b", "c"]
    assert all("match" not in s for s in out)


def test_rank_samples_keeps_short_acronyms():
    items = [{"id": "x", "title": "AI for protein folding", "source": "", "kind": "", "why": "", "categories": []},
             {"id": "y", "title": "Coral reef ecology", "source": "", "kind": "", "why": "", "categories": []}]
    out = rank_samples(items, "AI methods")
    assert out[0]["id"] == "x" and "ai" in out[0]["match"]["terms"]


def test_rank_samples_whole_word_only():
    # "ai" must NOT match "training"/"available" (whole-word matching).
    items = [{"id": "x", "title": "Training available datasets", "source": "", "kind": "",
              "why": "", "categories": []}]
    out = rank_samples(items, "AI")
    assert "match" not in out[0]


def test_rank_samples_does_not_mutate_input():
    rank_samples(_RANK_ITEMS, "robotics")
    assert "match" not in _RANK_ITEMS[0]                 # operated on copies


def test_search_endpoint_ranks_and_requires_auth(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    import services.ogrants_finder as og
    monkeypatch.setattr(og, "list_community_samples", lambda *a, **k: [])
    # Auth + db are dependency-injected; override them for a focused route test.
    main.app.dependency_overrides[main.get_current_user] = lambda: {"user_id": 1}

    class _NoInterests:
        def query(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def order_by(self, *a, **k): return self
        def all(self): return []
    main.app.dependency_overrides[main.get_db] = lambda: (yield _NoInterests())
    try:
        with TestClient(main.app) as client:
            r = client.post("/api/sample-proposals/search", json={"query": "data management"})
            assert r.status_code == 200
            body = r.json()
            assert body["count"] == len(SAMPLE_PROPOSALS)   # ogrants stubbed empty
            assert body["matched"] is True
    finally:
        main.app.dependency_overrides.clear()
