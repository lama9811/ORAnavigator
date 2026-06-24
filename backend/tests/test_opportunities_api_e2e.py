"""Full-app TestClient tests for POST /api/opportunities/search -- drives the
real route -> auth -> request-model -> response cycle (the layer that catches
wiring bugs the service unit tests can't). The live Grants.gov call is mocked
so the test is hermetic."""
import os
os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import main
import deps
from db import Base
from models import User, UserMemory
from security import hash_password
from services import opportunity_finder


@pytest.fixture
def ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestingSession()
    u = User(email="pi@morgan.edu", password_hash=hash_password("password123"),
             role="user", name="Pat Investigator")
    seed.add(u)
    seed.commit()
    uid = u.id
    seed.add(UserMemory(user_id=uid, memory_type="interest", content="cybersecurity"))
    seed.commit()
    seed.close()

    def _override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = _override_db
    main.app.dependency_overrides[deps.get_db] = _override_db
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "user_id": uid, "email": "pi@morgan.edu", "role": "user",
    }
    c = TestClient(main.app)
    yield c, uid
    main.app.dependency_overrides.clear()


def test_search_returns_ranked_opportunities(ctx, monkeypatch):
    captured = {}

    def fake_find(description, profile=None, rows=12):
        captured["description"] = description
        captured["profile"] = profile
        return [{
            "id": "1", "title": "AI Cyber Scholarships", "agency": "NSF",
            "close_date": "07/21/2026", "internal_deadline": "2026-07-14",
            "institution_eligibility": "eligible", "fit_explanation": "Strong match.",
            "fit_quote": "AI and cybersecurity education", "mechanism_note": "",
            "solicitation_url": "https://example.gov/opp/1", "contact": {},
        }]
    monkeypatch.setattr(opportunity_finder, "find_opportunities", fake_find)

    r = c_post(ctx, {"description": "machine learning for cyber defense"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["opportunities"][0]["institution_eligibility"] == "eligible"
    # the saved interest enriched the profile passed to the finder
    assert "cybersecurity" in (captured["profile"] or {}).get("interests", "")


def test_blank_description_is_422(ctx, monkeypatch):
    monkeypatch.setattr(opportunity_finder, "find_opportunities",
                        lambda *a, **k: [])
    r = c_post(ctx, {"description": "   "})
    assert r.status_code == 422


def test_search_requires_auth(ctx, monkeypatch):
    c, _ = ctx
    monkeypatch.setattr(opportunity_finder, "find_opportunities", lambda *a, **k: [])
    main.app.dependency_overrides.pop(main.get_current_user, None)
    r = c.post("/api/opportunities/search", json={"description": "anything"})
    assert r.status_code in (401, 403)


def c_post(ctx, payload):
    c, _ = ctx
    return c.post("/api/opportunities/search", json=payload)
