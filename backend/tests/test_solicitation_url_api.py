"""TestClient coverage for POST /api/me/submissions/from-solicitation/url.

Drives the real route -> dependency -> response cycle. The network fetch and
the Gemini extraction are both mocked, so this is deterministic and offline."""
import os

os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ.setdefault("JWT_SECRET", "test-secret-for-url-endpoint")

import pytest
from fastapi.testclient import TestClient

import main
from services import url_fetcher


@pytest.fixture
def client():
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "user_id": 1, "email": "pi@morgan.edu", "role": "user",
    }
    c = TestClient(main.app)
    yield c
    main.app.dependency_overrides.clear()


_URL = "/api/me/submissions/from-solicitation/url"
_FAKE_EXTRACT = {
    "sponsor": "NSF", "program_id": "NSF 24-001", "program_name": "CAREER",
    "deadline": "2026-07-01", "budget_cap": 600000, "page_limits": {},
    "required_attachments": [], "eligibility": "US institutions",
    "submission_portal": "Research.gov", "source_quotes": {},
    "unverified_fields": [],
}


def test_url_extract_success(client, monkeypatch):
    monkeypatch.setattr(url_fetcher, "fetch_solicitation_text",
                        lambda url: "solicitation text from the page")
    from services import solicitation_extractor as sx
    monkeypatch.setattr(sx, "extract_from_text", lambda text: _FAKE_EXTRACT)

    r = client.post(_URL, json={"url": "https://nsf.gov/pubs/nsf24001/nsf24001.pdf"})
    assert r.status_code == 200, r.text
    assert r.json()["extracted"]["sponsor"] == "NSF"


def test_url_blocked_returns_fetcher_status(client, monkeypatch):
    def _raise(url):
        raise url_fetcher.FetchError("That URL points to a non-public address "
                                     "and can't be fetched.", 400)
    monkeypatch.setattr(url_fetcher, "fetch_solicitation_text", _raise)

    r = client.post(_URL, json={"url": "http://localhost/secret"})
    assert r.status_code == 400
    assert "non-public" in r.json()["detail"]


def test_url_not_a_solicitation_is_422(client, monkeypatch):
    monkeypatch.setattr(url_fetcher, "fetch_solicitation_text",
                        lambda url: "a blog post about cats")
    from services import solicitation_extractor as sx
    monkeypatch.setattr(sx, "extract_from_text", lambda text: None)

    r = client.post(_URL, json={"url": "https://example.com/cats"})
    assert r.status_code == 422


def test_url_missing_field_is_422(client):
    # Pydantic validation: no `url` in body.
    r = client.post(_URL, json={})
    assert r.status_code == 422
